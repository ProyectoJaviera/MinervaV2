"""Serializadores de respuesta para los endpoints de solo lectura bajo
`/api/` (Subfase C del plan pre-Fase 5 aprobado 2026-10-14).

**El unico punto de paso para revelar un resultado de sombra etiquetada**:
`shadow_trade_to_public_dict`. La regla del usuario es estricta -- ninguna
respuesta puede mostrar PnL ni resultado de una sombra etiquetada hasta que
la medicion junte `MIN_EFFECTIVE_N` conglomerados efectivos por lado
(`app.trading.measurement_status.is_measurement_gate_open`), para no sesgar
la lectura de una medicion todavia en curso.

Disenado para fallar CERRADO, no abierto:

- Lista BLANCA explicita, nunca negra. Si `ShadowTrade` gana un campo nuevo
  en el futuro, NO aparece en la respuesta hasta que alguien lo agregue a
  mano aqui -- nunca `trade.model_dump()` ni ningun atajo que copie "todo
  menos lo prohibido".
- El router nunca declara `response_model=list[ShadowTrade]` (a diferencia
  de `positions.py`/`trades.py`, donde no hay nada que esconder): como
  `ShadowTrade` SI declara `pnl_net_usdt` como campo real, el filtrado de
  `response_model` de FastAPI no protegeria nada -- el valor real se
  serializaria igual. Las rutas que usan esto devuelven `list[dict]` simple.
- Deliberadamente fuera del whitelist en CUALQUIER caso (no sirven para esta
  vista y son senales blandas de resultado, no solo PnL explicito):
  `fee_entry_usdt`/`fee_exit_usdt`/`funding_paid_usdt`/
  `slippage_entry_usdt`/`slippage_exit_usdt` (costos/cargos) y
  `best_price`/`effective_stop` (el trailing revela que tan favorable fue el
  precio durante una posicion ABIERTA)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from app.persistence.models import ShadowTrade

# Campos que NUNCA revelan un resultado (plan/senal al abrir, no el cierre).
_ALWAYS_VISIBLE_FIELDS = (
    "id", "symbol", "side", "strategy", "status", "leverage", "margin_usdt",
    "notional_usdt", "qty", "entry_price", "opened_at", "sl_price", "tp_price",
    "liq_price", "sl_margin_loss_pct", "contributing_strategies", "signal_group_key",
    "candle_close_time", "llm_decision", "executed_in_real_account",
)
# Solo se agregan si `reveal_outcome=True` -- gateados juntos a proposito
# (ver docstring del modulo): un cierre sin PnL/motivo es un revelado parcial
# raro, y `closed_at` por si solo ya sugiere cuanto duro la operacion.
_OUTCOME_FIELDS = ("pnl_gross_usdt", "pnl_net_usdt", "exit_price", "close_reason", "closed_at")


def shadow_trade_to_public_dict(trade: ShadowTrade, *, reveal_outcome: bool) -> dict:
    data = {field: _serialize_value(getattr(trade, field)) for field in _ALWAYS_VISIBLE_FIELDS}
    if reveal_outcome:
        data.update(
            {field: _serialize_value(getattr(trade, field)) for field in _OUTCOME_FIELDS}
        )
    return data


def _serialize_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value
