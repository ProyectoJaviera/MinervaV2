"""`PaperBackend`: unica implementacion ACTIVA de `ExecutionBackend` en v1.

Usa el precio de mercado real de Bitunix (`markPrice` de `/market/tickers`)
para abrir/cerrar cuando no hay un precio observado del monitor, y aplica la
comision taker configurada. Nunca envia ninguna orden real -- solo lee datos
publicos y escribe en la base de datos local.

Motor de riesgo (subfase 3.2): `open_position` pasa por
`app.trading.risk_engine.check_new_entry`, que puede BLOQUEAR una entrada nueva
(incluido feed obsoleto, subfase 3.4) o ajustar el margen hacia abajo. Los
cierres nunca pasan por ahi.

Subfase 3.4: cada posicion nace con sus niveles planeados (SL, TP, trailing,
liquidacion) calculados al precio real de llenado (`app.trading.levels`). El
monitor de posiciones cierra con `close_if_open` a un precio explicito (SL/TP/
liquidacion/trailing), y el funding acumulado entra en el PnL neto.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.config import Settings
from app.core.logging import get_logger
from app.execution.backend_base import ExecutionBackend
from app.execution.pnl import compute_close_result, compute_open_fill
from app.market.bitunix_rest import BitunixRestClient
from app.persistence.database import Database
from app.persistence.models import Side, Trade, TradeStatus
from app.persistence.repositories import specs_repo, trades_repo
from app.trading import risk_engine
from app.trading.fills import settle_close
from app.trading.funding_accrual import accrue_funding
from app.trading.levels import StrategyLevels, resolve_trade_levels
from app.trading.slippage import entry_slippage_usdt, exit_slippage_usdt

logger = get_logger(__name__)


class InsufficientRiskBudgetError(RuntimeError):
    """La operacion no cumple el minimo operable del contrato de Bitunix
    (`spec.min_trade_volume`) una vez aplicado el margen que aprobo el
    motor de riesgo."""


class PaperBackend(ExecutionBackend):
    def __init__(self, db: Database, rest_client: BitunixRestClient, settings: Settings) -> None:
        self.db = db
        self.rest_client = rest_client
        self.settings = settings
        # Serializa comprobacion+escritura de aperturas Y cierres (`close_if_open`):
        # el motor de riesgo cuenta posiciones abiertas, y esa cuenta no puede
        # cambiar entre que se aprueba una entrada y que se inserta su fila; y dos
        # cierres concurrentes de la misma posicion (monitor + reconciliacion) no
        # deben liquidarla dos veces.
        self._state_lock = asyncio.Lock()

    async def _get_mark_price(self, symbol: str) -> float:
        tickers = await self.rest_client.get_tickers(symbol)
        ticker = next((t for t in tickers if t.get("symbol") == symbol), None)
        if ticker is None:
            raise ValueError(f"Sin ticker para {symbol}")
        return float(ticker.get("markPrice") or ticker["lastPrice"])

    async def get_equity(self, marks: dict[str, float] | None = None) -> float:
        """Capital realizado MAS PnL flotante neto de las posiciones abiertas
        (menos el funding ya acumulado). `marks` (precio por simbolo) evita pedir
        cada precio por REST cuando el llamador ya lo tiene."""
        balance = await self.get_balance()
        positions = await trades_repo.get_open_positions(self.db)
        if not positions:
            return balance
        price_cache: dict[str, float] = dict(marks or {})
        unrealized = 0.0
        for p in positions:
            if p.symbol not in price_cache:
                price_cache[p.symbol] = await self._get_mark_price(p.symbol)
            result = compute_close_result(
                p.side, p.qty, p.entry_price, price_cache[p.symbol],
                p.fee_entry_usdt, self.settings.taker_fee_pct,
            )
            unrealized += (
                result.pnl_net_usdt
                - p.funding_paid_usdt
                - p.slippage_entry_usdt
                - exit_slippage_usdt(
                    p.qty, price_cache[p.symbol], self.settings.backtest_slippage_bps
                )
            )
        return balance + unrealized

    async def open_position(
        self,
        symbol: str,
        side: Side,
        margin_usdt: float,
        leverage: int,
        strategy: str | None = None,
        sl_margin_loss_pct: float | None = None,
        is_manual: bool = False,
        decision_source: str | None = None,
        levels: StrategyLevels | None = None,
    ) -> Trade:
        async with self._state_lock:
            equity = await self.get_equity()
            decision = await risk_engine.check_new_entry(
                self.db, self.settings, symbol=symbol, side=side, strategy=strategy,
                margin_usdt=margin_usdt, current_equity=equity,
                sl_margin_loss_pct=sl_margin_loss_pct, is_manual=is_manual,
            )
            if not decision.allowed:
                logger.info(
                    "Entrada rechazada por el motor de riesgo: %s %s %s -- %s %s",
                    symbol, side.value, strategy, decision.reason, decision.details,
                )
                raise risk_engine.RiskRejectedError(decision.reason, decision.details)
            assert decision.approved_margin_usdt is not None
            if decision.approved_margin_usdt < margin_usdt:
                logger.info(
                    "Margen solicitado %.4f excede el maximo permitido %.4f para %s; se ajusta.",
                    margin_usdt, decision.approved_margin_usdt, symbol,
                )
            margin_usdt = decision.approved_margin_usdt

            spec = await specs_repo.get_spec(self.db, symbol)
            price = await self._get_mark_price(symbol)
            fill = compute_open_fill(margin_usdt, leverage, price, self.settings.taker_fee_pct)

            if spec and spec.min_trade_volume and fill.qty < spec.min_trade_volume:
                raise InsufficientRiskBudgetError(
                    f"qty {fill.qty} por debajo del minimo operable de {symbol} "
                    f"({spec.min_trade_volume})"
                )

            trade_levels = resolve_trade_levels(
                side, price, leverage, fill.notional_usdt,
                spec.margin_tiers_json if spec else None,
                levels, self.settings,
            )
            trade = Trade(
                symbol=symbol,
                side=side,
                strategy=strategy,
                status=TradeStatus.OPEN,
                leverage=leverage,
                margin_usdt=margin_usdt,
                notional_usdt=fill.notional_usdt,
                qty=fill.qty,
                entry_price=price,
                fee_entry_usdt=fill.fee_entry_usdt,
                opened_at=datetime.now(UTC),
                sl_margin_loss_pct=sl_margin_loss_pct,
                decision_source=decision_source or ("MANUAL" if is_manual else "SIN_LLM"),
                sl_price=trade_levels.sl_price,
                tp_price=trade_levels.tp_price,
                trailing_distance=trade_levels.trailing_distance,
                effective_stop=trade_levels.sl_price,
                best_price=price,
                liq_price=trade_levels.liq_price,
                slippage_entry_usdt=entry_slippage_usdt(
                    fill.notional_usdt, self.settings.backtest_slippage_bps
                ),
            )
            trade = await trades_repo.create_trade(self.db, trade)
            logger.info(
                "Posicion abierta (paper): %s %s margen=%.2f qty=%.6f @ %.4f sl=%s tp=%s liq=%.4f",
                symbol, side.value, margin_usdt, fill.qty, price,
                trade_levels.sl_price, trade_levels.tp_price, trade_levels.liq_price,
            )
            return trade

    async def close_position(self, trade_id: int, reason: str = "MANUAL") -> Trade:
        trade = await trades_repo.get_trade(self.db, trade_id)
        if trade is None:
            raise ValueError(f"Trade {trade_id} no existe")
        if trade.status != TradeStatus.OPEN:
            raise ValueError(f"Trade {trade_id} ya esta cerrado")

        exit_price = await self._get_mark_price(trade.symbol)
        closed = await self.close_if_open(trade_id, exit_price, reason, fill_source="REST_MARK")
        if closed is None:
            raise ValueError(f"Trade {trade_id} ya esta cerrado")
        return closed

    async def close_if_open(
        self,
        trade_id: int,
        exit_price: float,
        reason: str,
        closed_at: datetime | None = None,
        fill_source: str = "TICK",
    ) -> Trade | None:
        """Cierra la posicion a `exit_price` (precio explicito: nominal del SL/TP o
        el observado por el monitor). Devuelve `None` si ya estaba cerrada -- dos
        disparadores concurrentes no pueden liquidar la misma posicion dos veces.
        Aplica antes el funding pendiente hasta el instante del cierre. Nunca
        pasa por el motor de riesgo: los cierres no se bloquean."""
        async with self._state_lock:
            trade = await trades_repo.get_trade(self.db, trade_id)
            if trade is None or trade.status != TradeStatus.OPEN:
                return None

            closed_at = closed_at or datetime.now(UTC)
            trade = await accrue_funding(
                self.db, trade, int(closed_at.timestamp() * 1000)
            )
            settlement = settle_close(
                trade.side, trade.qty, trade.entry_price, exit_price,
                trade.fee_entry_usdt, trade.slippage_entry_usdt, trade.funding_paid_usdt,
                self.settings.taker_fee_pct, self.settings.backtest_slippage_bps,
            )
            pnl_net = settlement.pnl_net_usdt
            await trades_repo.close_trade(
                self.db, trade_id, exit_price, settlement.fee_exit_usdt,
                settlement.pnl_gross_usdt, pnl_net, reason, closed_at=closed_at,
                slippage_exit_usdt=settlement.slippage_exit_usdt, fill_source=fill_source,
            )
            logger.info(
                "Posicion cerrada (paper): trade=%d %s precio=%.4f pnl_neto=%.4f "
                "funding=%.4f slippage=%.4f motivo=%s fuente=%s",
                trade_id, trade.symbol, exit_price, pnl_net,
                trade.funding_paid_usdt,
                trade.slippage_entry_usdt + settlement.slippage_exit_usdt,
                reason, fill_source,
            )
            # Actualiza racha de perdidas/circuit breaker y trackers de equity
            # DESPUES de cerrar -- nunca bloquea el cierre en si (ya ocurrio).
            equity_after_close = await self.get_equity()
            await risk_engine.record_trade_closed(
                self.db, self.settings, pnl_net_usdt=pnl_net,
                current_equity=equity_after_close,
            )
            return await trades_repo.get_trade(self.db, trade_id)

    async def get_balance(self) -> float:
        closed_trades = [t for t in await trades_repo.get_trades(self.db, limit=100000)
                          if t.status == TradeStatus.CLOSED]
        realized_pnl = sum(t.pnl_net_usdt or 0.0 for t in closed_trades)
        return self.settings.initial_capital_usdt + realized_pnl

    async def get_positions(self) -> list[Trade]:
        return await trades_repo.get_open_positions(self.db)
