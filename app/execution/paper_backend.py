"""`PaperBackend`: unica implementacion ACTIVA de `ExecutionBackend` en v1.

Limitaciones conocidas de esta fase (documentadas tambien en PROGRESS.md):
sin SL/TP/trailing, sin liquidacion por tiers, sin funding, sin slippage,
sin reconciliacion tras downtime. El cierre ocurre solo por senal contraria
o llamada manual. Todo esto llega en Fase 3 (simulador realista).

Usa el precio de mercado real de Bitunix (`markPrice` de `/market/tickers`)
para abrir/cerrar, y aplica la comision taker configurada (ordenes a
mercado). Nunca envia ninguna orden real -- solo lee datos publicos y
escribe en la base de datos local.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.config import Settings
from app.core.logging import get_logger
from app.execution.backend_base import ExecutionBackend
from app.execution.pnl import compute_close_result, compute_open_fill
from app.market.bitunix_rest import BitunixRestClient
from app.persistence.database import Database
from app.persistence.models import Side, Trade, TradeStatus
from app.persistence.repositories import specs_repo, trades_repo

logger = get_logger(__name__)


class InsufficientRiskBudgetError(RuntimeError):
    """La operacion no cumple el minimo operable del contrato o los limites
    de riesgo configurados (ver `Settings.max_margin_for_new_trade`)."""


class PaperBackend(ExecutionBackend):
    def __init__(self, db: Database, rest_client: BitunixRestClient, settings: Settings) -> None:
        self.db = db
        self.rest_client = rest_client
        self.settings = settings

    async def _get_mark_price(self, symbol: str) -> float:
        tickers = await self.rest_client.get_tickers(symbol)
        ticker = next((t for t in tickers if t.get("symbol") == symbol), None)
        if ticker is None:
            raise ValueError(f"Sin ticker para {symbol}")
        return float(ticker.get("markPrice") or ticker["lastPrice"])

    async def open_position(
        self,
        symbol: str,
        side: Side,
        margin_usdt: float,
        leverage: int,
        strategy: str | None = None,
    ) -> Trade:
        committed_symbol = await trades_repo.committed_margin(self.db, symbol)
        committed_total = await trades_repo.committed_margin(self.db)
        max_allowed = self.settings.max_margin_for_new_trade(
            current_capital=await self.get_balance(),
            margin_committed_on_symbol=committed_symbol,
            margin_committed_total=committed_total,
        )
        if margin_usdt > max_allowed + 1e-9:
            logger.info(
                "Margen solicitado %.4f excede el maximo permitido %.4f para %s; se ajusta.",
                margin_usdt, max_allowed, symbol,
            )
            margin_usdt = max_allowed

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
