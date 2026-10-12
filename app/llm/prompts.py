"""Plantilla del prompt para la decision del LLM (subfase 3.6, fases iii-A y iv;
revisado tras los pilotos v1/v2/v3 -> signal_review_v4, docs/FASE3_6_LLM.md
seccion k -- ULTIMA iteracion del prompt antes de congelar, por pedido
explicito).

Ver docs/FASE3_6_LLM.md, secciones (a) y (b). El mensaje de usuario lleva solo
datos de la señal y el mercado YA DISPONIBLES en el cierre de la vela evaluada
(`ShadowTrade.candle_close_time`): identidad del grupo, indicadores y
timeframe de cada estrategia contribuyente (`signals.indicators_json`, ya
calculados por `precompute` -- no se recalculan aqui, pero sus unidades de
funding SI se normalizan a porcentaje aqui, ver `_normalize_funding_units`),
niveles y riesgo planeados (incluido el precio de entrada y sus distancias
derivadas, `_compute_entry_risk_features`), funding (incluido si ESTA
posicion cobra o paga, `_compute_funding_effect` -- calculado, no inferido
por el modelo), volatilidad, correlacion con BTC, contexto de tendencia en 1h
(ver `app/llm/market_features.py`), y posiciones REALES abiertas (nunca
sombras). Un dato que falte queda en `null` EXPLICITO: no se inventa ni se
infiere. Nunca se incluye texto externo no confiable (noticias, redes
sociales, etc.): esa fuente no esta implementada en este proyecto.
"""

from __future__ import annotations

import hashlib
import json

from app.config import Settings
from app.llm import market_features
from app.persistence.database import Database
from app.persistence.models import ShadowTrade, Side
from app.persistence.repositories import signals_repo

PROMPT_VERSION = "signal_review_v4"

# v2 (docs/FASE3_6_LLM.md seccion k): revision tras el piloto v1 (21 grupos,
# 90% APROBAR, fuera de la banda 15-85%). Cambios de texto: convenciones
# explicitas de funding y de liquidacion/SL que el modelo erro, contexto de
# tendencia y riesgo/beneficio en la lista de que sopesar, instruccion contra
# la complacencia (sin tasa objetivo), y el limite de `razonamiento` bajado a
# 250 caracteres (con margen bajo el tope duro de 300 del esquema, que ahora
# trunca en vez de invalidar -- ver app/llm/schemas.py).
#
# v3 (revision del piloto v2, misma seccion k): el TEXTO de este prompt no
# cambia, pero la congelacion debe cubrir tambien las FEATURES del mensaje
# (`FEATURES_VERSION` abajo) -- el piloto v2 encontro un bug de unidades
# (`volatility_atr14_pct` era una fraccion cruda, no un porcentaje) que no
# habria cambiado este hash si solo hasheara el texto. `PROMPT_SHA256` ahora
# cubre las dos cosas, que es lo que de verdad evita mezclar versiones en
# `ai_value_verdict(measurement_keys=...)` -- `PROMPT_VERSION`/
# `FEATURES_VERSION` son solo las etiquetas legibles, independientes entre si
# a proposito (una es del texto, la otra del mensaje).
#
# v4 (revision del piloto v3, misma seccion k -- ULTIMA iteracion antes de
# congelar, pedido explicito: no agregar nada mas despues de esto). El
# piloto v3 (32 grupos, 100% JSON valido, ATR ya correcto) seguia fallando
# el signo del funding en 2/4 casos SHORT con funding negativo, A PESAR de
# la convencion explicita de texto (parrafo de abajo) -- el modelo la sigue
# infiriendo mal con cierta frecuencia. Se resuelve de forma DETERMINISTA en
# vez de con mas texto: `funding_effect_on_position` (calculado, no inferido
# por el modelo) dice directamente si la posicion COBRA o PAGA funding.
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
    "Dos convenciones del mercado de futuros perpetuos, para que no las infieras mal: "
    "funding POSITIVO significa que los LONG pagan a los SHORT; funding NEGATIVO "
    "significa que los SHORT pagan a los LONG. En un LONG la liquidación está por "
    "DEBAJO del precio de entrada, con el SL entre ambos; en un SHORT la "
    "liquidación está por ENCIMA, con el SL entre la entrada y ella -- que la "
    "liquidación quede más allá del SL en esa dirección es siempre así por diseño, "
    "NUNCA una inconsistencia de los datos. "
    "El campo `funding_effect_on_position` ya indica si ESTA posición concreta "
    "COBRA o PAGA funding (\"RECIBE\", \"PAGA\", \"NEUTRO\" o null si no hay dato): "
    "úsalo directamente, no lo recalcules a partir de `funding_rate_pct` y la "
    "dirección. "
    "Al decidir, conviene sopesar: la tendencia reciente en 1h (retorno de 24h y "
    "72h, y la distancia porcentual a la media móvil de 50 velas) y la volatilidad "
    "reciente, si el funding va en contra de la dirección de la señal, la "
    "correlación con BTC (una señal muy correlacionada con BTC no es tan "
    "independiente como parece), las posiciones reales ya abiertas (concentración "
    "de riesgo), y la relación entre el riesgo (distancia al SL) y el beneficio "
    "(distancia al TP) frente al tope del 50 % del margen. "
    "Antes de decidir, nombra el PRINCIPAL riesgo concreto de esta operación con "
    "los datos del mensaje, y evalúa si la señal lo compensa. Que la señal exista "
    "(una estrategia la generó) no es, por sí mismo, un argumento a favor. Un "
    "chequeo que SIEMPRE se cumple antes de que la señal llegue a ti (p.ej. que el "
    "SL planeado no supere el tope del 50 % del margen, o que no haya ya una "
    "posición abierta en el mismo símbolo y dirección) tampoco cuenta como "
    "argumento: ya está garantizado para toda señal que recibes, la apruebes o no. "
    "Decides solo con los datos del mensaje; no tienes acceso a nada más, y no "
    "predices precios. Un campo en null significa que ese dato no está disponible "
    "todavía en este proyecto -- NUNCA lo infieras ni asumas un valor: decide con "
    "lo que sí tienes. No tienes una tasa de aprobación objetivo: aprueba o "
    "rechaza según el mérito de cada señal, sin buscar un equilibrio entre aprobar "
    "y rechazar. Responde SOLO con un objeto JSON válido que siga exactamente el "
    "esquema indicado, sin texto adicional antes ni después, y sin bloques de "
    "código ni comillas invertidas de ningún tipo (nunca uses ```json ni ```, nunca "
    "envuelvas la respuesta). "
    'Esquema: {"decision": "APROBAR" | "RECHAZAR", "confianza": número entre 0 y 1, '
    '"razonamiento": texto de máximo 250 caracteres}.'
)

# Version del MENSAJE (campos y unidades), separada de PROMPT_VERSION (texto
# del sistema) -- revision del piloto v2, docs/FASE3_6_LLM.md seccion k.
# Bumpear esto (y por lo tanto PROMPT_SHA256, ver abajo) en cualquier cambio
# de campos o de unidades del mensaje, aunque SYSTEM_PROMPT no cambie ni una
# letra: antes de esto, un cambio asi (como el bug de ATR en fraccion cruda
# del piloto v2) no se reflejaba en ningun hash y measurement_keys podia
# mezclar dos mensajes distintos bajo el mismo prompt_sha256.
# v2 (revision del piloto v3): se agrega `funding_effect_on_position`.
FEATURES_VERSION = "features_v2"

# Hash de la plantilla de sistema Y de la version de features
# (docs/FASE3_6_LLM.md seccion c, columna `prompt_sha256` de `llm_logs`):
# cualquier cambio de CUALQUIERA de los dos cambia este hash, y es el hash --
# no `PROMPT_VERSION` ni `FEATURES_VERSION` -- el que usa `ai_value_verdict
# (measurement_keys=...)` para no mezclar versiones distintas en el mismo
# veredicto (seccion k).
PROMPT_SHA256 = hashlib.sha256(
    f"{SYSTEM_PROMPT}\n---FEATURES_VERSION---\n{FEATURES_VERSION}".encode()
).hexdigest()


# Revision del piloto v1 (docs/FASE3_6_LLM.md seccion k): las columnas de
# funding que calculan las estrategias `funding_contrarian*` (precompute del
# backtest/generador de senales) guardan la FRACCION cruda de Bitunix (0,0001
# = 0,01 %) -- distinta de `funding_rate_pct` (features de mercado, ya en
# PORCENTAJE). El modelo confundio las dos unidades en el piloto. En vez de
# explicarle la diferencia (texto que puede ignorar u olvidar), se normaliza
# aqui: se convierten a porcentaje y se renombran con el sufijo `_pct`, para
# que la unidad sea evidente por el propio nombre del campo.
_FUNDING_FRACTION_TO_PCT_KEYS = {
    "funding_rate": "funding_rate_pct",
    "funding_recent_avg": "funding_recent_avg_pct",
    "funding_pctl_upper": "funding_pctl_upper_pct",
    "funding_pctl_lower": "funding_pctl_lower_pct",
}


def _normalize_funding_units(indicators: dict | None) -> dict | None:
    if indicators is None:
        return None
    out = dict(indicators)
    for raw_key, pct_key in _FUNDING_FRACTION_TO_PCT_KEYS.items():
        if raw_key in out and out[raw_key] is not None:
            out[pct_key] = out.pop(raw_key) * 100
    return out


RECIBE = "RECIBE"
PAGA = "PAGA"
NEUTRO = "NEUTRO"


def _compute_funding_effect(side: Side, funding_rate_pct: float | None) -> str | None:
    """Revision del piloto v3 (docs/FASE3_6_LLM.md seccion k): el modelo siguio
    equivocando el signo del funding en SHORT con funding negativo (2/4 casos
    en el piloto v3) A PESAR de la convencion explicita de texto en
    SYSTEM_PROMPT -- la sigue infiriendo mal con cierta frecuencia. En vez de
    mas texto, se resuelve de forma DETERMINISTA: esta funcion calcula
    directamente si LA POSICION (no el mercado en general) cobra o paga
    funding, y el prompt le pide al modelo usar este campo tal cual, sin
    recalcularlo.

    Convencion (ya verificada, ver SYSTEM_PROMPT): funding POSITIVO = LONG
    paga a SHORT; funding NEGATIVO = SHORT paga a LONG. Por lo tanto, esta
    posicion COBRA (`RECIBE`) cuando un LONG tiene funding < 0 o un SHORT
    tiene funding > 0; PAGA en el caso contrario; NEUTRO si es exactamente 0.
    `None` si no hay dato de funding (nunca se infiere ni se asume)."""
    if funding_rate_pct is None:
        return None
    if funding_rate_pct == 0:
        return NEUTRO
    is_long = side == Side.LONG
    pays = (is_long and funding_rate_pct > 0) or (not is_long and funding_rate_pct < 0)
    return PAGA if pays else RECIBE


def _compute_entry_risk_features(
    entry_price: float, sl_price: float | None, tp_price: float | None,
) -> dict:
    """Derivadas puras del precio de entrada PLANEADO (`trade.entry_price`,
    fijado al abrir, nunca el precio en vivo): distancia porcentual al SL y
    al TP, y la razon riesgo/beneficio. El modelo se quejo en el piloto v1 de
    no poder verificar el precio de entrada -- antes ni siquiera se enviaba.
    `None` cuando falta el nivel correspondiente (p.ej. sin TP)."""
    sl_distance_pct = (
        abs(entry_price - sl_price) / entry_price * 100
        if sl_price is not None and entry_price else None
    )
    tp_distance_pct = (
        abs(tp_price - entry_price) / entry_price * 100
        if tp_price is not None and entry_price else None
    )
    risk_reward = (
        tp_distance_pct / sl_distance_pct
        if sl_distance_pct and tp_distance_pct is not None else None
    )
    return {
        "entry_price_planned": entry_price,
        "sl_distance_pct": sl_distance_pct,
        "tp_distance_pct": tp_distance_pct,
        "risk_reward": risk_reward,
    }


def build_user_message(features: dict) -> str:
    """JSON de las features, con claves ordenadas y nulos explicitos cuando
    falte un dato. No se mezcla texto libre con datos (seccion b)."""
    return json.dumps(features, sort_keys=True, ensure_ascii=False)


def estimate_input_tokens(system: str, user: str) -> int:
    """Estimacion conservadora de tokens de entrada: caracteres / 2.

    Medido con el primer smoke real (2026-10-10, docs/FASE3_6_LLM.md seccion e):
    1.393 tokens de entrada reales para este prompt, unos 2,3 caracteres por
    token -- caracteres/3 (lo usado hasta ahora) daba 1.048, un 25 % por debajo
    de lo real, y ya no era conservador. Con /2 el mismo mensaje da ~1.570, por
    encima de lo medido, asi que vuelve a sobrestimar en vez de subestimar
    `coste_max` frente al presupuesto diario (seccion e). El piloto
    (`scripts/llm_pilot.py`) mide los tokens reales que reporta la API y, si
    difieren mucho de esto, se recalibra la estimacion otra vez."""
    return (len(system) + len(user)) // 2


async def build_features_from_shadow_trade(
    db: Database, trade: ShadowTrade, settings: Settings
) -> dict:
    """Junta TODO lo que pide la seccion (a), usando solo datos con cierre o
    instante <= `trade.candle_close_time` (la vela evaluada):

    - identidad, indicadores (con las unidades de funding normalizadas a
      porcentaje, ver `_normalize_funding_units`) y timeframe por estrategia
      contribuyente (`signals.indicators_json`/`signals.timeframe`, ya
      calculados por `precompute`);
    - niveles y riesgo planeados (SL, TP, liquidacion estimada,
      `sl_margin_loss_pct`) -- de la propia sombra, fijados al abrir, nunca los
      que `PositionMonitor` actualiza despues (`effective_stop`/`best_price`
      seguirian el precio POSTERIOR a la decision: eso si seria fuga); mas el
      precio de entrada planeado y sus distancias derivadas
      (`_compute_entry_risk_features`, revision del piloto v1: antes no se
      enviaba `entry_price` y el modelo no podia verificarlo). `trailing_
      distance` YA NO se envia: en el piloto v1 salio `null` en las 21
      respuestas (ninguna estrategia activa lo usa hoy);
    - funding, volatilidad, correlacion con BTC, contexto de tendencia en 1h
      y posiciones REALES abiertas (`app/llm/market_features.py`, que ya
      respeta el mismo limite de instante); mas `funding_effect_on_position`
      (`_compute_funding_effect`, revision del piloto v3): si ESTA posicion
      cobra o paga funding, calculado deterministicamente en vez de dejar
      que el modelo infiera el signo (lo seguia equivocando, ver seccion k).

    Un dato que falte (velas insuficientes, sin funding cacheado, sin fila en
    `signals`) queda en `null` explicito -- nunca se inventa ni se infiere."""
    indicators_by_strategy: dict[str, dict | None] = {}
    timeframe_by_strategy: dict[str, str | None] = {}
    for strategy in trade.contributing_strategies:
        signal = await signals_repo.get_by_symbol_strategy_candle(
            db, trade.symbol, strategy, trade.candle_close_time
        )
        raw_indicators = (
            json.loads(signal.indicators_json)
            if signal is not None and signal.indicators_json
            else None
        )
        indicators_by_strategy[strategy] = _normalize_funding_units(raw_indicators)
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
        "liq_price_estimated": trade.liq_price,
        "funding_effect_on_position": _compute_funding_effect(
            trade.side, market["funding_rate_pct"]
        ),
        **_compute_entry_risk_features(trade.entry_price, trade.sl_price, trade.tp_price),
        **market,
    }
