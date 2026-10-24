"""Activo afectado por un titular, de forma DETERMINISTA (sin LLM, sin costo).

Cada activo se reconoce por su nombre (sin distinguir mayusculas, palabra completa) y, si el ticker
no es una palabra comun, tambien por el ticker en MAYUSCULAS exactas. Los nombres compuestos van
primero para que "Bitcoin Cash" no cuente como BTC. Es una heuristica de titulares en ingles:
puede fallar (un titular que no nombra el activo queda sin etiqueta), nunca inventa uno."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class AssetAlias:
    """Los nombres y el ticker con los que se reconoce un activo en un titular.

    Attributes:
        ticker: ticker base (`BTC`).
        names: nombres en ingles que lo identifican (`bitcoin`).
        ticker_matches: si False, el ticker no cuenta (es una palabra comun, como `LINK`).
        ambiguous: si True, el nombre exige ademas contexto cripto en el titular.
    """
    ticker: str
    names: tuple[str, ...]
    # Tickers que tambien son palabras comunes ("LINK", "NEAR"): solo cuentan en mayusculas exactas.
    ticker_matches: bool = True
    # Nombres ambiguos en lenguaje comun ("tron", "near", "sui"): la consulta a la fuente exige
    # ademas contexto cripto (ver `query_for`).
    ambiguous: bool = False


ALIASES: tuple[AssetAlias, ...] = (
    AssetAlias("BCH", ("bitcoin cash",)),
    AssetAlias("BTC", ("bitcoin",)),
    AssetAlias("ETH", ("ethereum", "ether")),
    AssetAlias("SOL", ("solana",)),
    AssetAlias("XRP", ("xrp", "ripple")),
    AssetAlias("BNB", ("bnb chain", "binance coin", "bnb")),
    AssetAlias("DOGE", ("dogecoin",)),
    AssetAlias("TRX", ("tron",), ambiguous=True),
    AssetAlias("LINK", ("chainlink",)),
    AssetAlias("ZEC", ("zcash",)),
    AssetAlias("HYPE", ("hyperliquid",), ticker_matches=False),
    AssetAlias("ADA", ("cardano",)),
    AssetAlias("AVAX", ("avalanche",), ambiguous=True),
    AssetAlias("DOT", ("polkadot",), ticker_matches=False),
    AssetAlias("LTC", ("litecoin",)),
    AssetAlias("TON", ("toncoin",), ticker_matches=False),
    AssetAlias("SHIB", ("shiba inu",)),
    AssetAlias("SUI", ("sui network",), ticker_matches=False, ambiguous=True),
    AssetAlias("NEAR", ("near protocol",), ticker_matches=False),
    AssetAlias("XLM", ("stellar",), ambiguous=True),
    AssetAlias("XMR", ("monero",)),
    AssetAlias("PEPE", ("pepe coin",), ticker_matches=False),
    AssetAlias("UNI", ("uniswap",), ticker_matches=False),
    AssetAlias("AAVE", ("aave",)),
    AssetAlias("ARB", ("arbitrum",), ticker_matches=False),
    AssetAlias("OP", ("optimism",), ticker_matches=False, ambiguous=True),
    AssetAlias("ATOM", ("cosmos",), ticker_matches=False, ambiguous=True),
    AssetAlias("ETC", ("ethereum classic",)),
)
_BY_TICKER = {a.ticker: a for a in ALIASES}
# (ticker, patron, exige_contexto_cripto)
_PATTERNS: list[tuple[str, re.Pattern, bool]] = []
for _alias in ALIASES:
    for _name in _alias.names:
        _PATTERNS.append(
            (_alias.ticker, re.compile(rf"\b{re.escape(_name)}\b", re.IGNORECASE), _alias.ambiguous)
        )
    if _alias.ticker_matches:
        _PATTERNS.append((_alias.ticker, re.compile(rf"\b{re.escape(_alias.ticker)}\b"), False))
# Los patrones largos primero: "bitcoin cash" se consume antes de que "bitcoin" cuente.
_PATTERNS.sort(key=lambda item: -len(item[1].pattern))
# Un nombre comun ("Avalanche", "Stellar", "Cosmos") solo cuenta si el titular habla de cripto.
_CRYPTO_CONTEXT = re.compile(
    r"\b(crypto\w*|token\w*|coins?|blockchain|defi|web3|stablecoins?|altcoins?|memecoins?)\b",
    re.IGNORECASE,
)


def base_ticker(symbol: str) -> str:
    """El ticker base de un simbolo de futuros: `BTCUSDT` -> `BTC`.

    Args:
        symbol: simbolo.

    Returns:
        El simbolo sin el sufijo `USDT` (igual si no lo tiene).
    """
    return symbol[:-4] if symbol.endswith("USDT") else symbol


def extract_assets(title: str) -> list[str]:
    """Tickers base mencionados en un titular, de forma determinista (sin LLM, sin costo).

    Reconoce por nombre (palabra completa, sin distinguir mayusculas) y, si el ticker no es
    una palabra comun, por el ticker en MAYUSCULAS exactas. Los nombres compuestos van
    primero para que "Bitcoin Cash" no cuente como BTC; un nombre ambiguo solo cuenta si el
    titular habla de cripto. Es una heuristica para titulares en ingles: puede no etiquetar
    un titular, pero nunca inventa un activo.

    Args:
        title: titular.

    Returns:
        Tickers ordenados y sin repetir.
    """
    remaining = title
    found: set[str] = set()
    has_crypto_context = bool(_CRYPTO_CONTEXT.search(title))
    for ticker, pattern, needs_context in _PATTERNS:
        if needs_context and not has_crypto_context:
            continue
        if pattern.search(remaining):
            found.add(ticker)
            remaining = pattern.sub(" ", remaining)
    return sorted(found)


def query_for(symbol: str) -> str | None:
    """La consulta de GDELT para un simbolo del universo, o `None` si no hay alias.

    Une los nombres entre comillas con OR; si el nombre es ambiguo en lenguaje comun exige
    tambien contexto cripto. Siempre solo noticias en ingles.

    Args:
        symbol: simbolo del universo, p. ej. `BTCUSDT`.

    Returns:
        El texto de la consulta, o `None`.
    """
    alias = _BY_TICKER.get(base_ticker(symbol))
    if alias is None:
        return None
    names = " OR ".join(f'"{n}"' for n in alias.names)
    core = f"({names})" if len(alias.names) > 1 else names
    if alias.ambiguous:
        core = f"{core} (crypto OR cryptocurrency OR token OR blockchain)"
    return f"{core} sourcelang:english"


GENERAL_QUERY = (
    '(crypto OR cryptocurrency OR "digital assets" OR stablecoin) sourcelang:english'
)
