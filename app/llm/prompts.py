"""Plantilla del prompt para la decision del LLM (subfase 3.6, fase iii-A).

Ver docs/FASE3_6_LLM.md, secciones (a) y (b). El mensaje de usuario lleva solo
datos de la señal y el mercado YA DISPONIBLES en el cierre de la vela evaluada:
identidad del grupo, indicadores de cada estrategia contribuyente
(`signals.indicators_json`, ya calculados por `precompute` -- no se recalculan
aqui), y el riesgo planeado (`sl_margin_loss_pct`). Los campos que esta fase
todavia no calcula (funding, volatilidad, correlacion con BTC, posiciones
reales abiertas -- ver seccion (a), puntos 4-7) quedan en `null` EXPLICITO: no
se inventan. Se completan cuando el LLM se conecte al ciclo real del bot, fuera
del alcance de la 3.6. Nunca se incluye texto externo no confiable (noticias,
redes sociales, etc.): esa fuente no esta implementada en este proyecto.
"""

from __future__ import annotations

import hashlib
import json

from app.persistence.database import Database
from app.persistence.models import ShadowTrade
from app.persistence.repositories import signals_repo

PROMPT_VERSION = "signal_review_v1"

SYSTEM_PROMPT = (
    "Eres un revisor de señales de trading de criptomonedas en paper trading. "
    "Recibes una señal ya generada por reglas técnicas y debes decidir si se "
    "APRUEBA o se RECHAZA. Decides solo con los datos del mensaje; no tienes "
    "acceso a nada más. No predices precios. Responde SOLO con un objeto JSON "
    "válido que siga exactamente el esquema indicado, sin texto adicional antes "
    "ni después, y sin bloques de código ni comillas invertidas de ningún tipo "
    "(nunca uses ```json ni ```, nunca envuelvas la respuesta). "
    'Esquema: {"decision": "APROBAR" | "RECHAZAR", "confianza": número entre 0 y 1, '
    '"razonamiento": texto de máximo 300 caracteres}.'
)

# Hash de la plantilla de sistema (docs/FASE3_6_LLM.md seccion c, columna
# `prompt_sha256` de `llm_logs`): cualquier cambio de texto cambia este hash y,
# con el, `prompt_version` (seccion k) -- asi dos versiones del prompt nunca se
# mezclan en el mismo veredicto (`ai_value_verdict(measurement_keys=...)`).
PROMPT_SHA256 = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()


def build_user_message(features: dict) -> str:
    """JSON de las features, con claves ordenadas y nulos explicitos cuando
    falte un dato. No se mezcla texto libre con datos (seccion b)."""
    return json.dumps(features, sort_keys=True, ensure_ascii=False)


def estimate_input_tokens(system: str, user: str) -> int:
    """Estimacion conservadora de tokens de entrada: caracteres / 3. Es mas alta
    que la aproximacion habitual de ~4 caracteres por token, a proposito: para
    no subestimar `coste_max` frente al presupuesto diario (seccion e). El
    piloto (`scripts/llm_pilot.py`) mide los tokens reales que reporta la API
    y, si difieren mucho de esto, se recalibra la estimacion."""
    return (len(system) + len(user)) // 3


async def build_features_from_shadow_trade(db: Database, trade: ShadowTrade) -> dict:
    """Junta lo que YA esta guardado para este grupo de señal: identidad,
    indicadores por estrategia contribuyente (de `signals.indicators_json`, si
    existe la fila) y el riesgo planeado. Los campos que esta fase no calcula
    quedan en `null` explicito -- ver el docstring del modulo."""
    indicators_by_strategy: dict[str, dict | None] = {}
    for strategy in trade.contributing_strategies:
        signal = await signals_repo.get_by_symbol_strategy_candle(
            db, trade.symbol, strategy, trade.candle_close_time
        )
        indicators_by_strategy[strategy] = (
            json.loads(signal.indicators_json)
            if signal is not None and signal.indicators_json
            else None
        )
    return {
        "symbol": trade.symbol,
        "side": trade.side.value,
        "candle_close_time": trade.candle_close_time.isoformat(),
        "contributing_strategies": sorted(trade.contributing_strategies),
        "indicators_by_strategy": indicators_by_strategy,
        "sl_margin_loss_pct": trade.sl_margin_loss_pct,
        "leverage": trade.leverage,
        # No calculado todavia por este proyecto (seccion a, puntos 4-7):
        "funding_rate_pct": None,
        "funding_is_approximated": None,
        "volatility_atr_pct": None,
        "returns_std_30": None,
        "btc_correlation_30": None,
        "btc_correlation_100": None,
        "open_real_positions": None,
        "open_real_positions_same_symbol": None,
    }
