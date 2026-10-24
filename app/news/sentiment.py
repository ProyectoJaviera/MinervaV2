"""Sentimiento de un titular, DETERMINISTA y sin costo (`LEXICON_V1`).

Un lexico de palabras de titulares financieros/cripto con pesos, una ventana de negacion de tres
tokens ("not approved" invierte "approved") y una saturacion suave. Es deliberadamente simple y
limitado: no entiende contexto, ironia ni el tamano real de la noticia. Cada fila guarda el metodo
para convivir con un clasificador mejor mas adelante (un LLM barato, de pago, que NO se activa en
esta fase). Puntaje en [-1, 1]; 0 = sin senal."""

from __future__ import annotations

import re

METHOD = "LEXICON_V1"
SATURATION = 4.0  # suma de pesos que satura el puntaje en +-1
NEUTRAL_BAND = 0.15  # |puntaje| menor a esto se considera neutro

_TOKEN_RE = re.compile(r"[a-z]+(?:-[a-z]+)*")
_NEGATORS = frozenset({"not", "no", "never", "without", "fails", "failed", "denies", "denied"})
_NEGATION_WINDOW = 3

# Frases (se buscan primero, en el titular en minusculas) y palabras sueltas.
_PHRASES: dict[str, float] = {
    "all-time high": 2.0, "all time high": 2.0, "record high": 2.0, "rate cut": 1.5,
    "rate cuts": 1.5, "cuts rates": 1.5, "etf inflow": 1.5, "etf inflows": 1.5,
    "rug pull": -3.0, "rate hike": -1.5, "rate hikes": -1.5, "raises rates": -1.5,
    "etf outflow": -1.5, "etf outflows": -1.5, "sell-off": -2.0, "sell off": -2.0,
}
_WORDS: dict[str, float] = {
    # positivo
    "surge": 2.0, "surges": 2.0, "surged": 2.0, "soar": 2.0, "soars": 2.0, "rally": 2.0,
    "rallies": 2.0, "jump": 1.5, "jumps": 1.5, "gain": 1.0, "gains": 1.0, "rise": 1.0,
    "rises": 1.0, "climb": 1.0, "climbs": 1.0, "record": 1.0, "adoption": 1.0, "approve": 1.5,
    "approves": 1.5, "approved": 1.5, "approval": 1.5, "inflow": 1.0, "inflows": 1.0,
    "bullish": 2.0, "breakout": 1.5, "upgrade": 1.0, "partnership": 1.0, "rebound": 1.5,
    "rebounds": 1.5, "recovery": 1.0, "recovers": 1.0, "optimism": 1.0, "boost": 1.0,
    "boosts": 1.0, "milestone": 1.0, "buyback": 1.0, "dovish": 1.5, "easing": 1.0,
    "accumulate": 1.0, "accumulates": 1.0, "wins": 1.0,
    # negativo
    "crash": -2.5, "crashes": -2.5, "plunge": -2.5, "plunges": -2.5, "tumble": -2.0,
    "tumbles": -2.0, "slump": -2.0, "slumps": -2.0, "drop": -1.5, "drops": -1.5, "fall": -1.5,
    "falls": -1.5, "decline": -1.0, "declines": -1.0, "selloff": -2.0, "liquidation": -2.0,
    "liquidations": -2.0, "hack": -3.0, "hacked": -3.0, "hacks": -3.0, "exploit": -3.0,
    "exploited": -3.0, "stolen": -2.5, "theft": -2.5, "breach": -2.0, "lawsuit": -2.0,
    "sues": -2.0, "sued": -2.0, "probe": -1.5, "investigation": -1.5, "ban": -2.0, "bans": -2.0,
    "banned": -2.0, "crackdown": -2.0, "delist": -2.0, "delists": -2.0, "delisting": -2.0,
    "outflow": -1.5, "outflows": -1.5, "bearish": -2.0, "fraud": -3.0, "scam": -3.0,
    "bankruptcy": -3.0, "insolvency": -3.0, "insolvent": -3.0, "collapse": -3.0, "fears": -1.0,
    "warning": -1.0, "warns": -1.0, "halt": -1.5, "halted": -1.5, "suspends": -1.5,
    "suspension": -1.5, "fines": -1.5, "penalty": -1.5, "hawkish": -1.5,
    "uncertainty": -1.0, "rejects": -1.5, "rejected": -1.5, "rejection": -1.5, "denied": -1.0,
}


def score_headline(title: str) -> float:
    """Puntaje de sentimiento de un titular, en [-1, 1], con el lexico `LEXICON_V1`.

    Determinista y sin costo: suma los pesos de frases y palabras, invierte el signo si hay
    una negacion en las tres palabras previas y satura suavemente. No entiende contexto ni
    ironia.

    Args:
        title: titular.

    Returns:
        El puntaje (0.0 si no hay ninguna palabra del lexico).
    """
    text = title.lower()
    total = 0.0
    for phrase, weight in _PHRASES.items():
        count = text.count(phrase)
        if count:
            total += weight * count
            text = text.replace(phrase, " ")
    tokens = _TOKEN_RE.findall(text)
    for i, token in enumerate(tokens):
        weight = _WORDS.get(token)
        if weight is None:
            continue
        window = tokens[max(0, i - _NEGATION_WINDOW):i]
        if any(w in _NEGATORS for w in window):
            weight = -weight
        total += weight
    return max(-1.0, min(1.0, total / SATURATION))


def label(score: float | None) -> str | None:
    """Etiqueta de un puntaje: `POSITIVE`, `NEGATIVE` o `NEUTRAL` (banda de +-0.15).

    Args:
        score: puntaje, o `None`.

    Returns:
        La etiqueta, o `None` si no hay puntaje.
    """
    if score is None:
        return None
    if score >= NEUTRAL_BAND:
        return "POSITIVE"
    if score <= -NEUTRAL_BAND:
        return "NEGATIVE"
    return "NEUTRAL"
