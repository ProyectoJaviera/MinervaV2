"""`PaperBackend`: unica implementacion ACTIVA de `ExecutionBackend` en v1.

Limitaciones conocidas de esta fase (documentadas tambien en PROGRESS.md):
sin SL/TP/trailing, sin liquidacion por tiers, sin funding, sin slippage,
sin reconciliacion tras downtime. El cierre ocurre solo por senal contraria
o llamada manual. Todo esto llega en Fase 3 (simulador realista).

Usa el precio de mercado real de Bitunix (`markPrice` de `/market/tickers`)
para abrir/cerrar, y aplica la comision taker configurada (ordenes a
mercado). Nunca envia ninguna orden real -- solo lee datos publicos y
escribe en la base de datos local.

Desde la subfase 3.2 de Fase 3, `open_position` pasa primero por
`app.trading.risk_engine.check_new_entry` -- el motor de riesgo en vivo
que puede BLOQUEAR una entrada nueva (posiciones, misma direccion,
estrategia no elegible, tope de SL, perdida diaria, drawdown, circuit
breaker, kill switch) o ajustar el margen hacia abajo. `close_position`
nunca pasa por ahi -- el motor de riesgo solo bloquea entradas, nunca
cierres (ver el docstring de `risk_engine.py`). La comprobacion y la
escritura son atomicas (`asyncio.Lock` en `__init__`): necesario desde que
el generador de senales de la subfase 3.3 puede evaluar varios simbolos
con llamadas concurrentes. `is_manual=True` (nuevo) es para una entrada
tecleada por un humano -- salta la lista de elegibilidad por estrategia y
permite omitir `sl_margin_loss_pct`; toda entrada generada por una
estrategia automatica DEBE declarar ambos (ver `risk_engine.py`).
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
        # Serializa comprobacion+apertura (ver `open_position`): sin esto,
        # dos llamadas concurrentes (p.ej. el generador de senales de la
        # subfase 3.3 evaluando varios simbolos con asyncio.gather) podian
        # leer ambas "0/3 posiciones abiertas" ANTES de que ninguna hubiera
        # insertado su fila, y las dos pasaban el motor de riesgo -- la
        # comprobacion de cupo y la escritura en `trades` deben ser una
        # sola operacion atomica a nivel de proceso.
        self._open_lock = asyncio.Lock()

    async def _get_mark_price(self, symbol: str) -> float:
        tickers = await self.rest_client.get_tickers(symbol)
        ticker = next((t for t in tickers if t.get("symbol") == symbol), None)
        if ticker is None:
            raise ValueError(f"Sin ticker para {symbol}")
        return float(ticker.get("markPrice") or ticker["lastPrice"])

    async def get_equity(self) -> float:
        """Capital realizado MAS PnL flotante de las posiciones abiertas
        (a precio de mercado actual) -- la base correcta para el motor de
        riesgo (perdida diaria, drawdown), nunca solo lo realizado."""
        balance = await self.get_balance()
        positions = await trades_repo.get_open_positions(self.db)
        if not positions:
            return balance
        price_cache: dict[str, float] = {}
        unrealized = 0.0
        for p in positions:
            if p.symbol not in price_cache:
                price_cache[p.symbol] = await self._get_mark_price(p.symbol)
            result = compute_close_result(
                p.side, p.qty, p.entry_price, price_cache[p.symbol],
                p.fee_entry_usdt, self.settings.taker_fee_pct,
            )
            unrealized += result.pnl_net_usdt
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
    ) -> Trade:
        # Todo el ciclo comprobacion-de-cupo + escritura es atomico a nivel
        # de proceso (ver el comentario en __init__) -- el motor de riesgo
        # cuenta posiciones abiertas, y esa cuenta no puede cambiar entre
        # que se aprueba una entrada y que se inserta su fila.
        async with self._open_lock:
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
            )
            trade = await trades_repo.create_trade(self.db, trade)
            logger.info("Posicion abierta (paper): %s %s margen=%.2f qty=%.6f @ %.4f",
                        symbol, side.value, margin_usdt, fill.qty, price)
            return trade

    async def close_position(self, trade_id: int, reason: str = "MANUAL") -> Trade:
        trade = await trades_repo.get_trade(self.db, trade_id)
        if trade is None:
            raise ValueError(f"Trade {trade_id} no existe")
        if trade.status != TradeStatus.OPEN:
            raise ValueError(f"Trade {trade_id} ya esta cerrado")

        exit_price = await self._get_mark_price(trade.symbol)
        result = compute_close_result(
            trade.side, trade.qty, trade.entry_price, exit_price,
            trade.fee_entry_usdt, self.settings.taker_fee_pct,
        )

        await trades_repo.close_trade(
            self.db, trade_id, exit_price, result.fee_exit_usdt,
            result.pnl_gross_usdt, result.pnl_net_usdt, reason,
        )
        logger.info(
            "Posicion cerrada (paper): trade=%d %s pnl_neto=%.4f motivo=%s",
            trade_id, trade.symbol, result.pnl_net_usdt, reason,
        )
        # Actualiza racha de perdidas/circuit breaker y trackers de equity
        # DESPUES de cerrar -- nunca bloquea el cierre en si (ya ocurrio).
        equity_after_close = await self.get_equity()
        await risk_engine.record_trade_closed(
            self.db, self.settings, pnl_net_usdt=result.pnl_net_usdt,
            current_equity=equity_after_close,
        )
        updated = await trades_repo.get_trade(self.db, trade_id)
        assert updated is not None
        return updated

    async def get_balance(self) -> float:
        closed_trades = [t for t in await trades_repo.get_trades(self.db, limit=100000)
                          if t.status == TradeStatus.CLOSED]
        realized_pnl = sum(t.pnl_net_usdt or 0.0 for t in closed_trades)
        return self.settings.initial_capital_usdt + realized_pnl

    async def get_positions(self) -> list[Trade]:
        return await trades_repo.get_open_positions(self.db)
