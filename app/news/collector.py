"""Colector de noticias: una tarea propia que, fuente por fuente, respeta su intervalo minimo, pide
con GET condicional donde se puede, guarda titulares limpios/deduplicados/etiquetados (activo y
sentimiento deterministas) y registra la salud de cada fuente.

**Aislado y sin costo.** No importa nada del nucleo de la medicion (scheduler, labeler, brazo
ejecutado, monitor, prompts) ni `anthropic`: solo la base y HTTP gratuito. Cada fuente corre dentro
de su propio `try/except` y su propio timeout duro; un fallo de una no afecta a las demas ni al
resto del bot. Degradacion controlada: tras un fallo, la fuente espera un backoff exponencial
(5 min, 10, 20 ... hasta 6 h), o lo que pida `Retry-After`; mientras tanto el contexto de noticias
simplemente envejece (`app/news/context.py` lo marca) y nada se bloquea.

GDELT limita a una peticion cada 5 s (lo dice la propia API en su 429): las consultas de un ciclo
se separan `request_gap_s` y un 429 corta el ciclo."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.core.logging import get_logger
from app.news import parsers, sentiment
from app.news.assets import GENERAL_QUERY, base_ticker, extract_assets, query_for
from app.news.fetch import FetchError, FetchResult, HttpFetcher
from app.news.sources import FEAR_GREED, FED_MONETARY, GDELT, NewsSource, enabled_sources
from app.news.text import (
    canonical_url,
    domain_of,
    find_near_duplicate,
    sanitize_headline,
    title_hash,
    title_tokens,
    url_hash,
)
from app.persistence.database import Database
from app.persistence.repositories import health_repo, news_repo, universe_repo
from app.persistence.repositories.news_repo import FetchState, NewsRecord

logger = get_logger(__name__)

SOURCE_TIMEOUT_S = 600.0
BACKOFF_BASE_S = 300.0
BACKOFF_CAP_S = 6 * 3600.0
PARTIAL_RETRY_S = 1800.0
DEDUP_WINDOW = timedelta(hours=48)
PRUNE_EVERY = timedelta(hours=24)
FUTURE_TOLERANCE = timedelta(minutes=5)
GDELT_MAXRECORDS = "50"
# Cubre mas que el intervalo entre consultas; la deduplicacion evita repetidos.
GDELT_TIMESPAN = "3h"


class SourceRateLimited(RuntimeError):
    """La fuente respondio 429 (demasiadas peticiones).

    Attributes:
        retry_after_s: espera pedida por la fuente (`Retry-After`), si la indico.
    """
    def __init__(self, message: str, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


def backoff_seconds(consecutive_failures: int, retry_after_s: float | None = None) -> float:
    """Espera tras un fallo: 5 minutos por 2^(k-1), con tope de 6 horas.

    Si la fuente pide esperar mas (`Retry-After`), esa espera (tambien con tope de 6 h).

    Args:
        consecutive_failures: fallos seguidos de la fuente (minimo 1).
        retry_after_s: espera pedida por la fuente, o `None`.

    Returns:
        Los segundos a esperar.
    """
    k = max(1, consecutive_failures)
    seconds = min(BACKOFF_BASE_S * 2 ** (k - 1), BACKOFF_CAP_S)
    return max(seconds, min(retry_after_s or 0.0, BACKOFF_CAP_S))


class NewsCollector:
    """El colector de noticias: una tarea propia, fuente por fuente, aislada y sin costo.

    Para cada fuente habilitada respeta su intervalo minimo, pide con GET condicional donde
    se puede, guarda titulares limpios, deduplicados y etiquetados (activo y sentimiento
    deterministas) y registra su salud. Cada fuente corre en su propio `try/except` y con
    un timeout duro de 600 s: una fuente caida no afecta a las demas ni al bot. Tras un
    fallo la fuente espera un backoff exponencial; mientras tanto el contexto de noticias
    simplemente envejece. No importa nada del nucleo de la medicion ni la API de Anthropic.
    """
    def __init__(
        self, db: Database, settings: Settings, fetcher: HttpFetcher,
        *, clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        symbols_provider: Callable[[], Awaitable[list[str]]] | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.fetcher = fetcher
        self._clock = clock
        self._sleep = sleep
        self._symbols = symbols_provider or (lambda: universe_repo.get_included_symbols(db))
        self._stop = asyncio.Event()
        self._last_prune: datetime | None = None
        # (id, tokens del titular, instante de publicacion o, si falta, de descarga)
        self._recent: list[tuple[int, frozenset[str], datetime]] = []

    def stop(self) -> None:
        """Pide que `run_forever` termine despues de su espera actual."""
        self._stop.set()

    async def aclose(self) -> None:
        """Cierra el cliente HTTP del colector.

        Efectos:
            Cierra las conexiones de red.
        """
        await self.fetcher.aclose()

    # --- ciclo ------------------------------------------------

    async def run_once(self) -> dict[str, str]:
        """Un ciclo sobre las fuentes habilitadas. Nunca lanza (salvo cancelacion).

        Las fuentes con `next_allowed_at` en el futuro se aplazan. Al final poda los titulares
        mas viejos que `NEWS_RETENTION_DAYS` (como mucho una vez al dia).

        Returns:
            El resultado por fuente: un resumen, `aplazada` o `error: ...`.

        Efectos:
            Lee y escribe `news_fetch_state`, `data_source_health`, `news_items` (y `fear_greed`);
            lee `asset_universe`. Red: GDELT, Federal Reserve y alternative.me.
        """
        now = self._clock()
        results: dict[str, str] = {}
        await self._load_recent(now)
        for source in enabled_sources(self.settings.news_sources_list):
            state = await news_repo.get_state(self.db, source.id)
            if state.next_allowed_at is not None and now < state.next_allowed_at:
                results[source.id] = "aplazada"
                continue
            try:
                results[source.id] = await asyncio.wait_for(
                    self._collect(source, state, now), timeout=SOURCE_TIMEOUT_S
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - una fuente caida no afecta a las demas
                results[source.id] = await self._record_failure(source, state, now, exc)
        await self._maybe_prune(now)
        return results

    async def run_forever(self, tick_seconds: float = 60.0, startup_delay_s: float = 60.0) -> None:
        """Espera `startup_delay_s` y despues revisa cada `tick_seconds` que fuentes estan vencidas.

        La espera inicial evita competir con el arranque del bot. Un error inesperado se
        registra y el bucle sigue.

        Args:
            tick_seconds: segundos entre revisiones.
            startup_delay_s: espera antes del primer ciclo.

        Efectos:
            Ver `run_once`.
        """
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=startup_delay_s)
            return
        except TimeoutError:
            pass
        while not self._stop.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - el bucle sobrevive a cualquier fallo
                logger.exception("Error inesperado en el colector de noticias")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=tick_seconds)
            except TimeoutError:
                pass

    # --- por fuente ------------------------------------------------

    async def _collect(self, source: NewsSource, state: FetchState, now: datetime) -> str:
        if source.id == GDELT:
            summary = await self._collect_gdelt(source, state, now)
        elif source.id == FED_MONETARY:
            summary = await self._collect_rss(source, state, now)
        elif source.id == FEAR_GREED:
            summary = await self._collect_fear_greed(source, state, now)
        else:  # pragma: no cover - `enabled_sources` solo entrega fuentes registradas
            raise ValueError(f"fuente sin colector: {source.id}")
        return summary

    async def _finish_ok(
        self, source: NewsSource, now: datetime, *, items: int, status: int,
        etag: str | None = None, last_modified: str | None = None,
        next_in_s: float | None = None,
    ) -> None:
        await health_repo.record_success(self.db, source.health_key, self._clock())
        await news_repo.save_state(self.db, FetchState(
            source=source.id, etag=etag, last_modified=last_modified, last_attempt_at=now,
            next_allowed_at=now + timedelta(seconds=next_in_s or source.min_interval_s),
            last_status=status, last_items=items,
        ))

    async def _record_failure(
        self, source: NewsSource, state: FetchState, now: datetime, exc: Exception,
    ) -> str:
        message = f"{type(exc).__name__}: {str(exc)[:200]}"
        try:
            await health_repo.record_failure(self.db, source.health_key, message)
            failures = await health_repo.get_consecutive_failures(self.db, source.health_key)
            retry_after = exc.retry_after_s if isinstance(exc, SourceRateLimited) else None
            wait = backoff_seconds(failures, retry_after)
            await news_repo.save_state(self.db, FetchState(
                source=source.id, etag=state.etag, last_modified=state.last_modified,
                last_attempt_at=now, next_allowed_at=now + timedelta(seconds=wait),
                last_status=429 if isinstance(exc, SourceRateLimited) else state.last_status,
                last_items=state.last_items,
            ))
        except Exception:  # noqa: BLE001 - el registro del fallo tampoco debe propagarse
            logger.exception("No se pudo registrar el fallo de %s", source.id)
        logger.warning("Noticias: %s fallo (%s)", source.id, message)
        return f"error: {message}"

    # GDELT --------------------------------------------------

    async def _collect_gdelt(self, source: NewsSource, state: FetchState, now: datetime) -> str:
        queries: list[tuple[str, str]] = [("MARKET", GENERAL_QUERY)]
        seen_tags = {"MARKET"}
        for symbol in await self._symbols():
            query = query_for(symbol)
            tag = base_ticker(symbol)
            if query is not None and tag not in seen_tags:
                queries.append((tag, query))
                seen_tags.add(tag)
        stored = ok_queries = 0
        rate_limited: SourceRateLimited | None = None
        last_error: Exception | None = None
        for index, (tag, query) in enumerate(queries):
            if index:
                await self._sleep(source.request_gap_s)
            params = {
                "query": query, "mode": "artlist", "format": "json", "sort": "DateDesc",
                "maxrecords": GDELT_MAXRECORDS, "timespan": GDELT_TIMESPAN,
            }
            try:
                result = await self.fetcher.get(source.url, params=params)
                if result.rate_limited:
                    raise SourceRateLimited("HTTP 429 de GDELT", result.retry_after_s)
                if not result.ok:
                    raise FetchError(f"HTTP {result.status}")
                items = parsers.parse_gdelt(result.text)
            except parsers.RateLimitedError as exc:
                rate_limited = SourceRateLimited(str(exc))
                break
            except SourceRateLimited as exc:
                rate_limited = exc
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - se sigue con la siguiente consulta
                last_error = exc
                continue
            ok_queries += 1
            stored += await self._store_headlines(source, items, now, query_tag=tag)
        if ok_queries == 0:
            raise rate_limited or last_error or FetchError("GDELT sin consultas")
        if rate_limited is not None:  # parcial: lo logrado cuenta, pero se reintenta antes
            await self._finish_ok(
                source, now, items=stored, status=429, next_in_s=PARTIAL_RETRY_S,
            )
            return f"parcial: {stored} titulares de {ok_queries}/{len(queries)} consultas (429)"
        await self._finish_ok(source, now, items=stored, status=200)
        return f"{stored} titulares de {ok_queries}/{len(queries)} consultas"

    # RSS (Fed) --------------------------------------------------

    async def _collect_rss(self, source: NewsSource, state: FetchState, now: datetime) -> str:
        result = await self.fetcher.get(
            source.url, etag=state.etag, last_modified=state.last_modified
        )
        if result.rate_limited:
            raise SourceRateLimited(f"HTTP 429 de {source.id}", result.retry_after_s)
        if result.not_modified:
            await self._finish_ok(
                source, now, items=0, status=304, etag=state.etag,
                last_modified=state.last_modified,
            )
            return "sin cambios (304)"
        if not result.ok:
            raise FetchError(f"HTTP {result.status}")
        stored = await self._store_headlines(
            source, parsers.parse_rss(result.content), now, query_tag=None
        )
        await self._finish_ok(
            source, now, items=stored, status=200, etag=result.etag,
            last_modified=result.last_modified,
        )
        return f"{stored} titulares"

    # Fear & Greed --------------------------------------------------

    async def _collect_fear_greed(
        self, source: NewsSource, state: FetchState, now: datetime,
    ) -> str:
        result: FetchResult = await self.fetcher.get(
            source.url, params={"limit": "2", "format": "json"}
        )
        if result.rate_limited:
            raise SourceRateLimited("HTTP 429 de alternative.me", result.retry_after_s)
        if not result.ok:
            raise FetchError(f"HTTP {result.status}")
        points = parsers.parse_fear_greed(result.text)
        if not points:
            raise ValueError("Fear & Greed sin datos validos")
        fetched = self._clock()
        new = 0
        for point in points:
            if await news_repo.upsert_fear_greed(
                self.db, ts=point.ts, value=point.value, classification=point.classification,
                fetched_at=fetched,
            ):
                new += 1
        await self._finish_ok(source, now, items=new, status=200)
        return f"{new} puntos nuevos"

    # almacenamiento --------------------------------------------------

    async def _load_recent(self, now: datetime) -> None:
        rows = await news_repo.get_recent_originals(self.db, now - DEDUP_WINDOW)
        self._recent = [
            (r.id, title_tokens(r.title), r.published_at or r.fetched_at)
            for r in rows if r.id is not None
        ]

    async def _store_headlines(
        self, source: NewsSource, items: list[parsers.ParsedItem], now: datetime,
        *, query_tag: str | None,
    ) -> int:
        stored = 0
        for item in items:
            title = sanitize_headline(item.title)
            canonical = canonical_url(item.url)
            if not title or canonical is None:
                continue
            tokens = title_tokens(title)
            published = item.published_at
            if published is not None and published > now + FUTURE_TOLERANCE:
                published = now  # fecha en el futuro: no es creible, se trata como vista ahora
            if published is None and source.id == GDELT:
                published = now  # GDELT la vio ahora
            # El activo sale SOLO del titular: que la consulta lo mencione no basta (GDELT busca en
            # el cuerpo del articulo). `query_tag` se guarda aparte, como dato de procedencia.
            assets = extract_assets(title)
            score = sentiment.score_headline(title)
            # Casi duplicado = mismo titular publicado con pocas horas de diferencia (una noticia
            # replicada por varios medios). Dos comunicados con el mismo titulo pero publicados
            # con semanas de diferencia ("Federal Reserve issues FOMC statement") son hechos
            # distintos, aunque se descarguen juntos en la primera carga.
            when = published or now
            candidates = [
                (candidate_id, candidate_tokens)
                for candidate_id, candidate_tokens, candidate_when in self._recent
                if abs(when - candidate_when) <= DEDUP_WINDOW
            ]
            duplicate_of = find_near_duplicate(tokens, candidates)
            record = NewsRecord(
                source=source.id, fetched_at=now, published_at=published,
                domain=item.domain or domain_of(canonical), title=title, url=canonical,
                url_hash=url_hash(canonical), title_hash=title_hash(title),
                language=item.language, source_country=item.source_country, assets=assets,
                query_tag=query_tag, sentiment=score, sentiment_method=sentiment.METHOD,
                dup_of=duplicate_of,
            )
            new_id = await news_repo.insert_item(self.db, record)
            if new_id is None:
                continue
            stored += 1
            if duplicate_of is None:
                self._recent.append((new_id, tokens, when))
        return stored

    async def _maybe_prune(self, now: datetime) -> None:
        if self._last_prune is not None and now - self._last_prune < PRUNE_EVERY:
            return
        self._last_prune = now
        try:
            removed = await news_repo.prune_older_than(
                self.db, now - timedelta(days=self.settings.news_retention_days)
            )
            if removed:
                logger.info("Noticias: %d titulares viejos eliminados", removed)
        except Exception:  # noqa: BLE001
            logger.exception("Noticias: fallo la poda")
