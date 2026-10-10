"""Libro de operaciones sombra (subfase 3.5, `docs/FASE3_PLAN.md` punto 2).

Cada senal agrupada (`SignalCandidate`) abre UNA operacion sombra: margen
ilimitado, sin cupos de posicion ni margen disponible de la cuenta real, para que
la comparacion de calidad no dependa de los limites de riesgo. Usa exactamente las
mismas funciones que `PaperBackend` para llenado, niveles, slippage, funding y
cierre (`compute_open_fill`, `resolve_trade_levels`, `settle_close`), de modo que
una senal tenga el mismo PnL en ambas cuentas.

Dos diferencias deliberadas con la cuenta real, documentadas en
`app/trading/shadow_report.py`:
- **Solapes permitidos**: una sombra nueva se abre aunque otra del mismo simbolo y
  direccion siga abierta. El backtest no lo permite por celda (`FASE2_CRITERIOS.md`,
  punto 6); la sombra si, para que una RECHAZADA no bloquee a la siguiente y sesgue la
  comparacion APROBADA vs RECHAZADA. El valor estadistico se corrige con N efectivo
  por conglomerados (`app/trading/ai_value.py`).
- **Entrada al precio de cierre de la vela evaluada**, sin demora ni slippage de
  llenado: la cuenta real entra al precio de mercado con slippage. Para comparar
  APROBADA con RECHAZADA dentro de la sombra no afecta, porque todas entran igual.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from app.config import Settings
from app.core.logging import get_logger
from app.execution.pnl import compute_open_fill
from app.persistence.database import Database
from app.persistence.models import ShadowTrade, TradeStatus
from app.persistence.repositories import shadow_repo, specs_repo
from app.trading.fills import settle_close
from app.trading.funding_accrual import compute_funding_accrual
from app.trading.levels import resolve_trade_levels
from app.trading.signal_generator import SignalCandidate, pick_representative_strategy
from app.trading.slippage import entry_slippage_usdt

logger = get_logger(__name__)


def signal_group_key(symbol: str, side_value: str, candle_close_time: datetime) -> str:
    return f"{symbol}|{side_value}|{candle_close_time.isoformat()}"


class ShadowBook:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self._lock = asyncio.Lock()

    async def open_candidate(self, candidate: SignalCandidate) -> ShadowTrade | None:
        """Abre la operacion sombra de una senal agrupada, una sola vez por
        (simbolo, direccion, vela). Devuelve None si ya existia o si los niveles de la
        estrategia representante no son validos para ese precio."""
        representative = pick_representative_strategy(
            candidate.contributing_strategies, self.settings
        )
        price = candidate.price_by_strategy.get(representative)
        if price is None:
            logger.warning("Senal sin precio de evaluacion para %s; no se abre sombra",
                           representative)
            return None

        leverage = self.settings.leverage
        fill = compute_open_fill(
            self.settings.default_margin_usdt, leverage, price, self.settings.taker_fee_pct
        )
        spec = await specs_repo.get_spec(self.db, candidate.symbol)
        try:
            levels = resolve_trade_levels(
                candidate.side, price, leverage, fill.notional_usdt,
                spec.margin_tiers_json if spec else None,
                candidate.levels_by_strategy.get(representative), self.settings,
            )
        except ValueError as exc:
            logger.info("Senal sombra descartada por niveles invalidos: %s %s -- %s",
                        candidate.symbol, candidate.side.value, exc)
            return None

        trade = ShadowTrade(
            symbol=candidate.symbol,
            side=candidate.side,
            strategy=representative,
            status=TradeStatus.OPEN,
            leverage=leverage,
            margin_usdt=self.settings.default_margin_usdt,
            notional_usdt=fill.notional_usdt,
            qty=fill.qty,
            entry_price=price,
            fee_entry_usdt=fill.fee_entry_usdt,
            opened_at=candidate.candle_close_time,
            sl_margin_loss_pct=candidate.sl_margin_loss_pct,
            sl_price=levels.sl_price,
            tp_price=levels.tp_price,
            trailing_distance=levels.trailing_distance,
            effective_stop=levels.sl_price,
            best_price=price,
            liq_price=levels.liq_price,
            slippage_entry_usdt=entry_slippage_usdt(
                fill.notional_usdt, self.settings.backtest_slippage_bps
            ),
            contributing_strategies=sorted(candidate.contributing_strategies),
            signal_group_key=signal_group_key(
                candidate.symbol, candidate.side.value, candidate.candle_close_time
            ),
            candle_close_time=candidate.candle_close_time,
        )
        async with self._lock:
            new_id = await shadow_repo.insert_if_new(self.db, trade)
        if new_id is None:
            return None
        trade.id = new_id
        logger.info("Operacion sombra abierta: %s %s estrategias=%s sl=%s tp=%s",
                    candidate.symbol, candidate.side.value,
                    json.dumps(trade.contributing_strategies), levels.sl_price, levels.tp_price)
        return trade

    async def load_open(self) -> list[ShadowTrade]:
        return await shadow_repo.get_open(self.db)

    async def accrue_funding(self, trade: ShadowTrade, until_ms: int) -> ShadowTrade:
        accrual = await compute_funding_accrual(self.db, trade, until_ms)
        if accrual is None:
            return trade
        paid, last_ms = accrual
        await shadow_repo.update_funding(self.db, trade.id, paid, last_ms)
        return trade.model_copy(
            update={"funding_paid_usdt": paid, "funding_last_applied_ms": last_ms}
        )

    async def update_risk(self, trade: ShadowTrade) -> None:
        await shadow_repo.update_risk_state(
            self.db, trade.id, trade.effective_stop, trade.best_price
        )

    async def close(
        self,
        trade: ShadowTrade,
        exit_price: float,
        reason: str,
        closed_at: datetime | None = None,
        fill_source: str = "TICK",
    ) -> ShadowTrade | None:
        closed_at = closed_at or datetime.now(UTC)
        async with self._lock:
            current = await shadow_repo.get(self.db, trade.id)
            if current is None or current.status != TradeStatus.OPEN:
                return None
            current = await self.accrue_funding(current, int(closed_at.timestamp() * 1000))
            settlement = settle_close(
                current.side, current.qty, current.entry_price, exit_price,
                current.fee_entry_usdt, current.slippage_entry_usdt, current.funding_paid_usdt,
                self.settings.taker_fee_pct, self.settings.backtest_slippage_bps,
            )
            await shadow_repo.close(
                self.db, trade.id, exit_price, settlement.fee_exit_usdt,
                settlement.slippage_exit_usdt, settlement.pnl_gross_usdt,
                settlement.pnl_net_usdt, reason, fill_source, closed_at=closed_at,
            )
        return await shadow_repo.get(self.db, trade.id)

    async def close_unreliable(
        self, trade: ShadowTrade, closed_at: datetime | None = None
    ) -> ShadowTrade | None:
        """Cierre administrativo SIN PnL (incidente de estabilidad 2026-10-10, Etapa
        2c): la reconciliacion de esta sombra fallo `MAX_SHADOW_RECONCILE_ATTEMPTS`
        veces seguidas (`app/trading/position_monitor.py`) -- no hubo velas 1m
        suficientes para reconstruir el periodo caido, asi que no se le atribuye
        ganancia ni perdida. Se marca `close_reason='RECONCILE_FAILED'` y queda
        excluida de `ai_value_verdict` (`shadow_repo.get_unreliable_signal_group_keys`),
        a diferencia de `close()` que SIEMPRE liquida PnL real via `settle_close`."""
        closed_at = closed_at or datetime.now(UTC)
        async with self._lock:
            current = await shadow_repo.get(self.db, trade.id)
            if current is None or current.status != TradeStatus.OPEN:
                return None
            await shadow_repo.close(
                self.db, trade.id, current.entry_price, 0.0, 0.0, 0.0, 0.0,
                "RECONCILE_FAILED", "RECONCILE_FAILED", closed_at=closed_at,
            )
        return await shadow_repo.get(self.db, trade.id)
