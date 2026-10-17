"""Parsers de las fuentes de noticias verificadas (docs/FASE4_PLAN.md). Puros: reciben texto,
devuelven datos; nunca tocan la red ni la base. Todo lo que viene de fuera es NO CONFIABLE, asi que
son defensivos: un campo ausente o con otro tipo se omite, un elemento malo no tumba al resto.

- GDELT DOC 2.0 (`mode=artlist&format=json`): `{"articles": [{url, title, seendate, domain,
  language, sourcecountry, ...}]}`. Una consulta sin resultados responde `{}`; un 429 responde
  TEXTO ("Please limit requests to one every 5 seconds ..."). **El formato real de los articulos
  no se pudo verificar desde este PC** (la API respondio 429): se sigue la documentacion.
- RSS 2.0 / Atom (Federal Reserve): XML. Se rechaza cualquier DTD o entidad (ataques de expansion
  de entidades) antes de parsear, y se tolera el BOM que antepone la Fed.
- Alternative.me Fear & Greed: `{"data": [{value, value_classification, timestamp}]}`."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from app.news.text import domain_of

BOM = chr(0xFEFF)  # la Fed antepone un BOM a sus RSS


class UnsafeXmlError(ValueError):
    """El XML trae una DTD o una entidad: no se parsea."""


class RateLimitedError(RuntimeError):
    """La fuente respondio (aunque con 200) que se la esta consultando demasiado."""


@dataclass(frozen=True)
class ParsedItem:
    title: str
    url: str
    published_at: datetime | None = None
    domain: str | None = None
    language: str | None = None
    source_country: str | None = None


@dataclass(frozen=True)
class FearGreedPoint:
    ts: datetime
    value: int
    classification: str | None


_RATE_LIMIT_RE = re.compile(r"limit requests|rate limit|too many requests", re.IGNORECASE)


def parse_gdelt_datetime(value: object) -> datetime | None:
    """`seendate` de GDELT: `20261017T130000Z` (UTC). Tambien acepta ISO 8601."""
    if not isinstance(value, str) or not value:
        return None
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_gdelt(text: str) -> list[ParsedItem]:
    """Articulos de una respuesta `artlist`. `{}` o vacio = sin resultados (lista vacia). Texto
    de limite de peticiones -> `RateLimitedError`. Cualquier otra cosa que no sea JSON ->
    `ValueError`."""
    body = (text or "").strip().lstrip(BOM)
    if not body:
        return []
    if _RATE_LIMIT_RE.search(body[:400]) and not body.startswith(("{", "[")):
        raise RateLimitedError(body[:160])
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"respuesta de GDELT que no es JSON: {body[:80]!r}") from exc
    articles = payload.get("articles") if isinstance(payload, dict) else None
    if not isinstance(articles, list):
        return []
    items: list[ParsedItem] = []
    for raw in articles:
        if not isinstance(raw, dict):
            continue
        title, url = raw.get("title"), raw.get("url")
        if not isinstance(title, str) or not isinstance(url, str) or not title.strip():
            continue
        domain = raw.get("domain") if isinstance(raw.get("domain"), str) else domain_of(url)
        items.append(ParsedItem(
            title=title, url=url, published_at=parse_gdelt_datetime(raw.get("seendate")),
            domain=domain,
            language=raw.get("language") if isinstance(raw.get("language"), str) else None,
            source_country=(
                raw.get("sourcecountry") if isinstance(raw.get("sourcecountry"), str) else None
            ),
        ))
    return items


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(element: ET.Element, name: str) -> str | None:
    for child in element:
        if _local(child.tag) == name and child.text and child.text.strip():
            return child.text.strip()
    return None


def _parse_rss_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_rss(data: bytes | str) -> list[ParsedItem]:
    """Items de un RSS 2.0 (`channel/item`) o Atom (`entry`). Sin DTD ni entidades; tolera BOM."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    text = raw.decode("utf-8-sig", errors="replace")
    upper = text.upper()
    if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
        raise UnsafeXmlError("el XML trae DTD o entidades")
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as exc:
        raise ValueError(f"XML mal formado: {exc}") from exc
    items: list[ParsedItem] = []
    for element in root.iter():
        kind = _local(element.tag)
        if kind not in {"item", "entry"}:
            continue
        title = _child_text(element, "title")
        link = _child_text(element, "link")
        if link is None:  # Atom: <link href="..."/>
            for child in element:
                if _local(child.tag) == "link" and child.get("href"):
                    link = child.get("href")
                    break
        if not title or not link:
            continue
        published = _parse_rss_date(
            _child_text(element, "pubDate") or _child_text(element, "published")
            or _child_text(element, "updated") or _child_text(element, "date")
        )
        items.append(ParsedItem(
            title=title, url=link, published_at=published, domain=domain_of(link),
        ))
    return items


def parse_fear_greed(text: str) -> list[FearGreedPoint]:
    """Puntos del Fear & Greed Index (el mas reciente primero). Fuera de 0-100 se descartan."""
    try:
        payload = json.loads((text or "").strip().lstrip(BOM))
    except json.JSONDecodeError as exc:
        raise ValueError("respuesta de Fear & Greed que no es JSON") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    points: list[FearGreedPoint] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        try:
            value = int(raw["value"])
            ts = datetime.fromtimestamp(int(raw["timestamp"]), tz=UTC)
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            continue
        if not 0 <= value <= 100:
            continue
        classification = raw.get("value_classification")
        points.append(FearGreedPoint(
            ts=ts, value=value,
            classification=classification if isinstance(classification, str) else None,
        ))
    return points
