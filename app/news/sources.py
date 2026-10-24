"""Registro de las fuentes de noticias INTEGRADAS, cada una verificada en su documentacion oficial
(docs/FASE4_PLAN.md, "Fuentes de noticias: verificacion", 2026-10-17). Una fuente no listada aqui
no existe para el colector, aunque aparezca en `NEWS_SOURCES`.

Descartadas a proposito (terminos o robots.txt que chocan con alimentar un LLM): Cointelegraph
(prohibe el scraping automatizado y el uso para IA/LLM sin consentimiento), CoinDesk (su robots.txt
bloquea a ClaudeBot/anthropic-ai; terminos no accesibles) y Decrypt (prohibe robots que minen el
contenido; solo uso no comercial)."""

from __future__ import annotations

from dataclasses import dataclass

GDELT = "gdelt"
FED_MONETARY = "fed_monetary"
FEAR_GREED = "fear_greed"


@dataclass(frozen=True)
class NewsSource:
    """Una fuente de noticias integrada y verificada en su documentacion oficial.

    Attributes:
        id: identificador (`gdelt`, `fed_monetary`, `fear_greed`).
        label: nombre legible.
        kind: `headlines` o `market`.
        url: direccion de consulta.
        min_interval_s: intervalo MINIMO entre consultas completas.
        attribution: texto de atribucion que debe verse junto a cualquier muestra de sus datos.
        request_gap_s: espera entre dos peticiones de un mismo ciclo (GDELT: una cada 5 s).
    """
    id: str
    label: str
    kind: str  # "headlines" | "market"
    url: str
    # Intervalo MINIMO entre consultas completas a la fuente.
    min_interval_s: float
    # Texto de atribucion que debe verse junto a cualquier muestra de sus datos.
    attribution: str
    # Cuanto esperar entre dos peticiones dentro de un mismo ciclo (GDELT: una cada 5 s).
    request_gap_s: float = 0.0

    @property
    def health_key(self) -> str:
        """La clave de esta fuente en `data_source_health`: `news:<id>`."""
        return f"news:{self.id}"


SOURCES: dict[str, NewsSource] = {
    GDELT: NewsSource(
        id=GDELT, label="GDELT Project", kind="headlines",
        url="https://api.gdeltproject.org/api/v2/doc/doc",
        min_interval_s=3600.0, request_gap_s=10.0,
        attribution="Titulares: GDELT Project (https://www.gdeltproject.org/)",
    ),
    FED_MONETARY: NewsSource(
        id=FED_MONETARY, label="Federal Reserve (politica monetaria)", kind="headlines",
        url="https://www.federalreserve.gov/feeds/press_monetary.xml",
        min_interval_s=3600.0,
        attribution=(
            "Comunicados: Board of Governors of the Federal Reserve System "
            "(https://www.federalreserve.gov/)"
        ),
    ),
    FEAR_GREED: NewsSource(
        id=FEAR_GREED, label="Fear & Greed Index (alternative.me)", kind="market",
        url="https://api.alternative.me/fng/",
        min_interval_s=6 * 3600.0,
        attribution=(
            "Fear & Greed Index: alternative.me "
            "(https://alternative.me/crypto/fear-and-greed-index/)"
        ),
    ),
}


def enabled_sources(ids: list[str]) -> list[NewsSource]:
    """Las fuentes conocidas entre las pedidas, en orden de registro. Las desconocidas se ignoran.

    Args:
        ids: ids de fuente (`NEWS_SOURCES`).

    Returns:
        Lista de `NewsSource`.
    """
    wanted = set(ids)
    return [s for sid, s in SOURCES.items() if sid in wanted]


def attribution_lines(ids: list[str] | None = None) -> list[str]:
    """Los textos de atribucion de las fuentes pedidas (o de todas).

    Args:
        ids: ids de fuente, o `None` para todas.

    Returns:
        Lista de textos de atribucion.
    """
    sources = list(SOURCES.values()) if ids is None else enabled_sources(ids)
    return [s.attribution for s in sources]
