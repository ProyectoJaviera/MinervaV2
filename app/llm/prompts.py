"""Plantilla del prompt para la decision del LLM (subfase 3.6, fases iii-A y iv).

Ver docs/FASE3_6_LLM.md, secciones (a) y (b). El mensaje de usuario lleva solo
datos de la señal y el mercado YA DISPONIBLES en el cierre de la vela evaluada
(`ShadowTrade.candle_close_time`): identidad del grupo, indicadores y
timeframe de cada estrategia contribuyente (`signals.indicators_json`, ya
calculados por `precompute` -- no se recalculan aqui), niveles y riesgo
planeados, funding, volatilidad, correlacion con BTC (ver
`app/llm/market_features.py`), y posiciones REALES abiertas (nunca sombras).
Un dato que falte queda en `null` EXPLICITO: no se inventa ni se infiere.
Nunca se incluye texto externo no confiable (noticias, redes sociales, etc.):
esa fuente no esta implementada en este proyecto.
"""

from __future__ import annotations

import hashlib
import json

from app.config import Settings
from app.llm import market_features
from app.persistence.database import Database
from app.persistence.models import ShadowTrade
from app.persistence.repositories import signals_repo

PROMPT_VERSION = "signal_review_v1"

# El texto crecio en la fase iv (mas contexto y mas features), pero se queda
# con el mismo prompt_version: todavia no se uso en ninguna medicion real
# (fase='MEDICION'), solo en copias de prueba, y lo que de verdad evita
# mezclar dos versiones es el hash (`PROMPT_SHA256`, via `measurement_keys` en
# `ai_value_verdict`), no esta etiqueta legible -- ver docs/FASE3_6_LLM.md
# seccion (k).
SYSTEM_PROMPT = (
    "Eres un revisor de señales de trading de criptomonedas en una cuenta de "
    "PAPER TRADING (dinero simulado, nunca real). Cada posición usa apalancamiento "
    "10x en margen AISLADO: el stop loss (SL) planeado puede perder hasta el 50 % "
    "del margen de esa posición si se ejecuta. Recibes una señal ya generada por "
    "reglas técnicas (una o más estrategias) y debes decidir si se APRUEBA o se "
    "RECHAZA. Las estrategias que pueden contribuir a una señal, y qué mide cada una:\n"
    "- ema_cross_9_21: cruce de medias móviles exponenciales (sigue tendencia).\n"
    "- donchian_breakout_20: ruptura del canal de las últimas 20 velas (sigue "
    "tendencia en rupturas de rango).\n"
    "- mean_reversion_rsi14_bb20: RSI(14) y bandas de Bollinger(20) (apuesta a que "
    "el precio vuelve a la media tras un extremo).\n"
    "- trend_atr_stop_9_21_50: cruce de medias con un stop basado en ATR (sigue "
    "tendencia con un stop que se adapta a la volatilidad).\n"
    "- funding_contrarian_experimental / funding_contrarian_percentile_experimental: "
    "apuestan contra una tasa de funding extrema (estrategias experimentales).\n"
    "Al decidir, conviene sopesar: la tendencia y la volatilidad reciente, si el "
    "funding va en contra de la dirección de la señal, la correlación con BTC "
    "(una señal muy correlacionada con BTC no es tan independiente como parece), "
    "las posiciones reales ya abiertas (concentración de riesgo), y el riesgo del "
    "SL planeado frente al tope del 50 % del margen. Decides solo con los datos "
    "del mensaje; no tienes acceso a nada más, y no predices precios. Un campo en "
    "null significa que ese dato no está disponible todavía en este proyecto -- "
    "NUNCA lo infieras ni asumas un valor: decide con lo que sí tienes. No tienes "
    "una tasa de aprobación objetivo: aprueba o rechaza según el mérito de cada "
    "señal, sin buscar un equilibrio entre aprobar y rechazar. Responde SOLO con "
    "un objeto JSON válido que siga exactamente el esquema indicado, sin texto "
    "adicional antes ni después, y sin bloques de código ni comillas invertidas "
    "de ningún tipo (nunca uses ```json ni ```, nunca envuelvas la respuesta). "
    'Esquema: {"decision": "APROBAR" | "RECHAZAR", "confianza": número entre 0 y 1, '
    '"razonamiento": texto de máximo 300 caracteres}.'
)

# Hash de la plantilla de sistema (docs/FASE3_6_LLM.md seccion c, columna
# `prompt_sha256` de `llm_logs`): cualquier cambio de texto cambia este hash, y
# es el hash -- no `PROMPT_VERSION` -- el que usa `ai_value_verdict
# (measurement_keys=...)` para no mezclar dos versiones del prompt en el mismo
# veredicto (seccion k).
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


async def build_features_from_shadow_trade(
    db: Database, trade: ShadowTrade, settings: Settings
) -> dict:
    """Junta TODO lo que pide la seccion (a), usando solo datos con cierre o
    instante <= `trade.candle_close_time` (la vela evaluada):

    - identidad, indicadores y timeframe por estrategia contribuyente
      (`signals.indicators_json`/`signals.timeframe`, ya calculados por
      `precompute`);
    - niveles y riesgo planeados (SL, TP, trailing, liquidacion estimada,
      `sl_margin_loss_pct`) -- de la propia sombra, fijados al abrir, nunca los
      que `PositionMonitor` actualiza despues (`effective_stop`/`best_price`
      seguirian el precio POSTERIOR a la decision: eso si seria fuga);
    - funding, volatilidad, correlacion con BTC y posiciones REALES abiertas
      (`app/llm/market_features.py`, que ya respeta el mismo limite de
      instante).

    Un dato que falte (velas insuficientes, sin funding cacheado, sin fila en
    `signals`) queda en `null` explicito -- nunca se inventa ni se infiere."""
    indicators_by_strategy: dict[str, dict | None] = {}
    timeframe_by_strategy: dict[str, str | None] = {}
    for strategy in trade.contributing_strategies:
        signal = await signals_repo.get_by_symbol_strategy_candle(
            db, trade.symbol, strategy, trade.candle_close_time
        )
        indicators_by_strategy[strategy] = (
            json.loads(signal.indicators_json)
            if signal is not None and signal.indicators_json
            else None
        )
        timeframe_by_strategy[strategy] = signal.timeframe if signal is not None else None

    market = await market_features.build_market_features(
        db, trade.symbol, trade.candle_close_time, settings.funding_stale_margin_hours
    )

    return {
        "symbol": trade.symbol,
        "side": trade.side.value,
        "candle_close_time": trade.candle_close_time.isoformat(),
        "contributing_strategies": sorted(trade.contributing_strategies),
        "indicators_by_strategy": indicators_by_strategy,
        "timeframe_by_strategy": timeframe_by_strategy,
        "leverage": trade.leverage,
        "sl_margin_loss_pct": trade.sl_margin_loss_pct,
        "sl_price": trade.sl_price,
        "tp_price": trade.tp_price,
        "trailing_distance": trade.trailing_distance,
        "liq_price_estimated": trade.liq_price,
        **market,
    }
