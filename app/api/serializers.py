"""Serializadores de respuesta para los endpoints de solo lectura bajo
`/api/` (Subfase C del plan pre-Fase 5 aprobado 2026-10-14).

**El unico punto de paso para revelar un resultado de sombra etiquetada**:
`shadow_trade_to_public_dict`. La regla del usuario es estricta -- ninguna
respuesta puede mostrar PnL ni resultado de una sombra etiquetada hasta que
la medicion junte `MIN_EFFECTIVE_N` conglomerados efectivos por lado Y exista
el registro oficial del veredicto
(`app.trading.measurement_status.is_outcome_reveal_allowed`: gate abierto +
`docs/VEREDICTO_3_6_REGISTRO*.txt` con un veredicto valido), para no sesgar la
lectura de una medicion todavia en curso ni el procedimiento del placebo.

Disenado para fallar CERRADO, no abierto:

- Lista BLANCA explicita, nunca negra. Si `ShadowTrade` gana un campo nuevo
  en el futuro, NO aparece en la respuesta hasta que alguien lo agregue a
  mano aqui -- nunca `trade.model_dump()` ni ningun atajo que copie "todo
  menos lo prohibido".
- El router nunca declara `response_model=list[ShadowTrade]` (ni, desde la subfase
  3.9, `positions.py`/`trades.py` con `Trade`): como `ShadowTrade`/`Trade` SI declaran
  `pnl_net_usdt` como campo real, el filtrado de `response_model` de FastAPI no
  protegeria nada -- el valor real se serializaria igual. Las rutas que usan esto
  devuelven `list[dict]` simple.
- Deliberadamente fuera del whitelist en CUALQUIER caso (no sirven para esta
  vista y son senales blandas de resultado, no solo PnL explicito):
  `fee_entry_usdt`/`fee_exit_usdt`/`funding_paid_usdt`/
  `slippage_entry_usdt`/`slippage_exit_usdt` (costos/cargos) y
  `best_price`/`effective_stop` (el trailing revela que tan favorable fue el
  precio durante una posicion ABIERTA)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from app.persistence.models import ShadowTrade, Trade

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


# --- cuenta paper (`trades`): subfase 3.9 ---------------------------------------------------
#
# Con el brazo ejecutado activo, `trades` contiene operaciones de senales APROBADA: su PnL, sus
# costos y su motivo de cierre son la mitad APROBADA del veredicto, asi que siguen la MISMA
# regla que las sombras (`is_outcome_reveal_allowed`). `GET /trades` y `GET /positions` (sin
# login) ya NO usan `response_model=list[Trade]`: pasan por esta lista BLANCA. `decision_json`,
# las columnas de reconciliacion y cualquier campo futuro quedan fuera hasta agregarlos a mano.
_TRADE_ALWAYS_VISIBLE_FIELDS = (
    "id", "symbol", "side", "strategy", "status", "leverage", "margin_usdt", "notional_usdt",
    "qty", "entry_price", "opened_at", "sl_price", "tp_price", "trailing_distance", "liq_price",
    "sl_margin_loss_pct", "decision_source",
)
_TRADE_OUTCOME_FIELDS = (
    "pnl_gross_usdt", "pnl_net_usdt", "exit_price", "close_reason", "closed_at", "fee_entry_usdt",
    "fee_exit_usdt", "funding_paid_usdt", "slippage_entry_usdt", "slippage_exit_usdt",
    "best_price", "effective_stop", "fill_source",
)


def trade_to_public_dict(trade: Trade, *, reveal_outcome: bool) -> dict:
    data = {
        field: _serialize_value(getattr(trade, field)) for field in _TRADE_ALWAYS_VISIBLE_FIELDS
    }
    if reveal_outcome:
        data.update(
            {field: _serialize_value(getattr(trade, field)) for field in _TRADE_OUTCOME_FIELDS}
        )
    return data


# --- Fase 5: vista de la cuenta paper del dashboard (`/api/account`) ---------------------------
#
# Misma regla que `trade_to_public_dict`, con UNA diferencia pedida para el dashboard: la hora de
# cierre (`closed_at`) se ve siempre ("posiciones abiertas y cerradas con simbolo, lado, estrategia,
# niveles y horas"). Sugiere cuanto duro la operacion pero no su resultado. El precio de salida, el
# motivo de cierre, el PnL, los costos y el resto de lo que revela un resultado NO viajan en el
# JSON mientras `is_outcome_reveal_allowed` sea falso.
_ACCOUNT_TRADE_VISIBLE_FIELDS = (*_TRADE_ALWAYS_VISIBLE_FIELDS, "closed_at")
_ACCOUNT_TRADE_OUTCOME_FIELDS = tuple(f for f in _TRADE_OUTCOME_FIELDS if f != "closed_at")


def account_trade_to_dict(trade: Trade, *, reveal_outcome: bool) -> dict:
    data = {
        field: _serialize_value(getattr(trade, field)) for field in _ACCOUNT_TRADE_VISIBLE_FIELDS
    }
    if reveal_outcome:
        data.update({
            field: _serialize_value(getattr(trade, field))
            for field in _ACCOUNT_TRADE_OUTCOME_FIELDS
        })
    return data


# --- Fase 5: registro del brazo ejecutado y rechazos de riesgo ------------------------------------
#
# `paper_execution_log.details_json` y `risk_rejections.details_json` guardan los valores exactos
# que motivaron la decision. Casi todos son estructura (cupos, tamanos minimos, retrasos), pero
# algunos son numeros derivados de RESULTADOS: `MARGIN_UNAVAILABLE` guarda el `equity`. Por eso
# los detalles pasan por una lista BLANCA de claves: una clave nueva no sale hasta agregarla aqui.
ARM_DETAIL_KEYS = frozenset({
    "decision_delay_s", "limit_s", "symbol", "price", "qty", "qty_floored", "min_trade_volume",
    "base_precision", "message", "error_type", "entry_delay_s", "shadow_entry_price",
    "executed_entry_price", "adverse_gap_bps", "margin_usdt", "open", "limit",
    "same_direction_open", "sl_margin_loss_pct", "cap_pct", "eligible", "until",
    "max_drawdown_pct", "max_daily_loss_pct", "ws_stale_after_seconds", "committed_total",
    "resolved_at_startup",
})
_MAX_DETAIL_TEXT = 200


def _safe_detail_value(value):
    if isinstance(value, str):
        return value[:_MAX_DETAIL_TEXT]
    if isinstance(value, bool | int | float) or value is None:
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return [item[:_MAX_DETAIL_TEXT] for item in value[:20]]
    return None


def safe_arm_details(details: dict | None) -> dict:
    """Solo las claves de la lista blanca, con valores simples (nunca estructuras anidadas)."""
    if not isinstance(details, dict):
        return {}
    return {
        key: _safe_detail_value(value) for key, value in details.items() if key in ARM_DETAIL_KEYS
    }


def arm_entry_to_dict(entry) -> dict:
    """Una fila de `paper_execution_log` (`PaperExecutionEntry`) para el dashboard."""
    return {
        "id": entry.id,
        "created_at": _serialize_value(entry.created_at),
        "resolved_at": _serialize_value(entry.resolved_at),
        "symbol": entry.symbol,
        "side": entry.side,
        "strategy": entry.strategy,
        "decision_delay_s": entry.decision_delay_s,
        "outcome": entry.outcome,
        "reason": entry.reason,
        "trade_id": entry.trade_id,
        "details": safe_arm_details(entry.details),
    }


def rejection_to_dict(rejection) -> dict:
    """Un rechazo del motor de riesgo SIN sus detalles (pueden traer el equity)."""
    return {
        "id": rejection.id,
        "created_at": _serialize_value(rejection.created_at),
        "symbol": rejection.symbol,
        "side": _serialize_value(rejection.side),
        "strategy": rejection.strategy,
        "reason": rejection.reason,
    }


def _serialize_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value
