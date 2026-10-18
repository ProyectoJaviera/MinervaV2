"""Caracteristicas DISCRETAS de una operacion, conocidas al momento de decidir (nunca el resultado).
Los cortes son fijos y documentados: asi los patrones son comparables en el tiempo."""

from __future__ import annotations

from dataclasses import dataclass

from app.persistence.repositories.memory_repo import MemoryEntry

UNKNOWN = "UNK"

# Dimensiones sobre las que se agregan lecciones (clave -> funcion de patron de una entrada).
DIMENSIONS = (
    "strategy_set", "side", "strategy_set+side", "symbol", "timeframe", "hour_bin", "atr_bucket",
    "rr_bucket", "risk_bucket", "llm_label", "sentiment_bucket", "fear_greed_bucket",
)


def hour_bin(hour_utc: int | None) -> str:
    if hour_utc is None:
        return UNKNOWN
    start = (hour_utc // 4) * 4
    return f"{start:02d}-{start + 3:02d}"


def atr_bucket(atr_pct: float | None) -> str:
    if atr_pct is None:
        return UNKNOWN
    if atr_pct < 0.8:
        return "ATR<0.8"
    if atr_pct < 1.6:
        return "ATR0.8-1.6"
    return "ATR>=1.6"


def rr_bucket(risk_reward: float | None) -> str:
    if risk_reward is None:
        return UNKNOWN
    if risk_reward < 1.0:
        return "RR<1"
    if risk_reward < 2.0:
        return "RR1-2"
    return "RR>=2"


def risk_bucket(sl_margin_loss_pct: float | None) -> str:
    if sl_margin_loss_pct is None:
        return UNKNOWN
    if sl_margin_loss_pct < 20.0:
        return "SL<20"
    if sl_margin_loss_pct < 35.0:
        return "SL20-35"
    return "SL>=35"


def sentiment_bucket(sentiment: float | None) -> str:
    if sentiment is None:
        return UNKNOWN
    if sentiment <= -0.15:
        return "NEG"
    if sentiment >= 0.15:
        return "POS"
    return "NEU"


def fear_greed_bucket(value: int | None) -> str:
    if value is None:
        return UNKNOWN
    if value <= 40:
        return "FEAR"
    if value >= 60:
        return "GREED"
    return "NEUTRAL"


def risk_reward(entry: float, sl: float | None, tp: float | None) -> float | None:
    """Razon beneficio/riesgo planeada (distancias al TP y al SL sobre el precio de entrada)."""
    if not entry or sl is None or tp is None:
        return None
    risk = abs(entry - sl) / entry
    return abs(tp - entry) / entry / risk if risk else None


@dataclass(frozen=True)
class QueryFeatures:
    """Lo que se sabe de una operacion NUEVA al decidir (el equivalente de una `MemoryEntry` sin
    resultado ni etiqueta)."""

    symbol: str
    side: str
    strategies: frozenset[str]
    timeframe: str | None = None
    hour_utc: int | None = None
    atr_pct: float | None = None
    risk_reward: float | None = None
    sl_margin_loss_pct: float | None = None
    news_sentiment: float | None = None
    fear_greed: int | None = None


def patterns_of(entry: MemoryEntry) -> dict[str, str]:
    """Patron de una entrada en cada dimension (`UNK` si falta el dato)."""
    return {
        "strategy_set": entry.strategy_set,
        "side": entry.side,
        "strategy_set+side": f"{entry.strategy_set}|{entry.side}",
        "symbol": entry.symbol,
        "timeframe": entry.timeframe or UNKNOWN,
        "hour_bin": hour_bin(entry.hour_utc),
        "atr_bucket": atr_bucket(entry.atr_pct),
        "rr_bucket": rr_bucket(entry.risk_reward),
        "risk_bucket": risk_bucket(entry.sl_margin_loss_pct),
        "llm_label": entry.llm_label,
        "sentiment_bucket": sentiment_bucket(entry.news_sentiment),
        "fear_greed_bucket": fear_greed_bucket(entry.fear_greed),
    }


def patterns_of_query(q: QueryFeatures) -> dict[str, str]:
    """Patron de una operacion NUEVA en las dimensiones que se conocen antes de decidir (sin
    `llm_label`: es justo lo que se esta por decidir)."""
    strategy_set = "+".join(sorted(q.strategies))
    return {
        "strategy_set": strategy_set,
        "side": q.side,
        "strategy_set+side": f"{strategy_set}|{q.side}",
        "symbol": q.symbol,
        "timeframe": q.timeframe or UNKNOWN,
        "hour_bin": hour_bin(q.hour_utc),
        "atr_bucket": atr_bucket(q.atr_pct),
        "rr_bucket": rr_bucket(q.risk_reward),
        "risk_bucket": risk_bucket(q.sl_margin_loss_pct),
        "sentiment_bucket": sentiment_bucket(q.news_sentiment),
        "fear_greed_bucket": fear_greed_bucket(q.fear_greed),
    }
