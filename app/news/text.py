"""Saneado y canonizacion de titulares y URLs, hashes de deduplicacion y similitud.

Un titular es TEXTO NO CONFIABLE de un tercero: aqui se limpia (sin caracteres de control, sin
etiquetas HTML, sin espacios raros, longitud acotada) para que nunca arrastre formato ni
instrucciones disfrazadas de estructura. Limpiar NO lo vuelve confiable: cualquier prompt que lo
use debe tratarlo como dato a evaluar, jamas como instruccion (ver `app/llm/prompts_v5.py`)."""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MAX_TITLE_CHARS = 200

_TAG_RE = re.compile(r"<[^>]{0,200}>")
_SPACE_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "ref", "ref_src", "ref_url",
    "ocid", "cmpid", "cmp", "igshid", "yclid", "_ga", "_gl", "spm",
}
_STOPWORDS = frozenset(
    "the a an and or of to in on for at by with as is are was were be been it its this that "
    "from after before over amid new says say said will".split()
)


def sanitize_headline(raw: str | None, max_chars: int = MAX_TITLE_CHARS) -> str:
    """Titular limpio: sin etiquetas HTML ni caracteres de control o de formato.

    Resuelve entidades HTML, quita los caracteres de ancho cero y los controles
    bidireccionales, colapsa espacios y acota la longitud. Limpiar NO lo vuelve confiable.

    Args:
        raw: titular crudo, o `None`.
        max_chars: longitud maxima (se recorta con `...`).

    Returns:
        El titular limpio, o `""` si no queda nada.
    """
    if not raw:
        return ""
    text = html.unescape(str(raw))
    text = _TAG_RE.sub(" ", text)
    cleaned = []
    for ch in text:
        category = unicodedata.category(ch)
        if category.startswith("C") and ch not in "\t\n\r":  # Cc, Cf, Cs, Co, Cn
            continue
        cleaned.append(" " if ch in "\t\n\r" or category.startswith("Z") else ch)
    text = _SPACE_RE.sub(" ", "".join(cleaned)).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 3].rstrip() + "..."
    return text


def canonical_url(url: str | None) -> str | None:
    """URL canonica para deduplicar.

    Solo http o https, host en minusculas, sin fragmento, sin puerto por defecto, sin
    parametros de seguimiento (`utm_*`, `fbclid`, ...), parametros restantes ordenados y
    sin barra final.

    Args:
        url: URL cruda.

    Returns:
        La URL canonica, o `None` si no es una URL http(s) valida.
    """
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return None
    host = parts.hostname.lower()
    port = parts.port
    default = {"http": 80, "https": 443}[parts.scheme.lower()]
    netloc = host if port in (None, default) else f"{host}:{port}"
    query = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), netloc, path, urlencode(query), ""))


def domain_of(url: str | None) -> str | None:
    """El dominio de una URL, sin `www.`.

    Args:
        url: URL cruda.

    Returns:
        El dominio, o `None` si la URL no es valida.
    """
    canonical = canonical_url(url)
    if canonical is None:
        return None
    host = urlsplit(canonical).hostname or ""
    return host.removeprefix("www.") or None


def url_hash(canonical: str) -> str:
    """SHA-256 de una URL canonica (la clave unica de `news_items.url_hash`).

    Args:
        canonical: URL canonica.

    Returns:
        El hash en hexadecimal.
    """
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def title_tokens(title: str) -> frozenset[str]:
    """Tokens en minusculas, sin acentos ni palabras vacias, para comparar titulares.

    Args:
        title: titular.

    Returns:
        Conjunto de tokens de mas de 2 caracteres.
    """
    folded = unicodedata.normalize("NFKD", title.lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return frozenset(t for t in _TOKEN_RE.findall(folded) if len(t) > 2 and t not in _STOPWORDS)


def title_hash(title: str) -> str:
    """Hash estable de un titular normalizado: mayusculas, signos y espacios no cuentan.

    Args:
        title: titular.

    Returns:
        El SHA-256 en hexadecimal.
    """
    normalized = " ".join(sorted(title_tokens(title))) or title.lower().strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Similitud de Jaccard entre dos conjuntos de tokens.

    Args:
        a: primer conjunto.
        b: segundo conjunto.

    Returns:
        Un valor entre 0 y 1 (0 si alguno esta vacio).
    """
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def find_near_duplicate(
    tokens: frozenset[str], candidates: list[tuple[int, frozenset[str]]], threshold: float = 0.8,
) -> int | None:
    """Id del candidato mas parecido con Jaccard >= `threshold`.

    Con menos de 4 tokens no se declara casi duplicado: un titular de 2 o 3 palabras
    coincide por azar.

    Args:
        tokens: tokens del titular nuevo.
        candidates: pares `(id, tokens)` de titulares recientes.
        threshold: umbral de similitud.

    Returns:
        El id del original, o `None`.
    """
    if len(tokens) < 4:
        return None
    best_id, best = None, threshold
    for candidate_id, other in candidates:
        score = jaccard(tokens, other)
        if score >= best:
            best_id, best = candidate_id, score
    return best_id
