"""Fase 4, colector de noticias: ciclo completo contra una "internet" falsa (sin red), intervalos
minimos, GET condicional, 429 con backoff, fallo parcial, una fuente caida sin afectar a las demas,
XML hostil, deduplicacion, poda y que solo se le pida a las fuentes verificadas."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx

from app.config import Settings
from app.news import assets
from app.news import collector as collector_module
from app.news.collector import NewsCollector, backoff_seconds
from app.news.fetch import HttpFetcher
from app.persistence.repositories import health_repo, news_repo

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
UA = "MinervaTest/1.0"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "TRXUSDT", "NOEXISTEUSDT"]


def gdelt_article(url, title, seen="20261101T113000Z", domain="example.com"):
    return {
        "url": url, "title": title, "seendate": seen, "domain": domain, "language": "English",
        "sourcecountry": "United States",
    }


SHARED = gdelt_article("https://shared.test/etf?utm_source=a", "SEC approval lifts crypto market")
BTC_A = gdelt_article("https://news.test/btc-record", "Bitcoin surges to a new record high today")
BTC_NEAR = gdelt_article(
    "https://other.test/btc-record-2", "Bitcoin surges to a new record high today again"
)
ETH_A = gdelt_article("https://news.test/eth", "Ethereum upgrade ships; ETH rallies")
HACK = gdelt_article("https://news.test/hack", "Exchange hacked, millions stolen")

FED_XML = (
    chr(0xFEFF) + '<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel>'
    "<item><title>Federal Reserve issues FOMC statement</title>"
    "<link>https://www.federalreserve.gov/a.htm</link>"
    "<pubDate>Tue, 13 Oct 2026 18:00:00 GMT</pubDate></item>"
    "<item><title>Minutes of the Federal Open Market Committee</title>"
    "<link>https://www.federalreserve.gov/b.htm</link>"
    "<pubDate>Tue, 13 Oct 2026 19:00:00 GMT</pubDate></item></channel></rss>"
)
FNG = json.dumps({"data": [
    {"value": "55", "value_classification": "Greed", "timestamp": "1792195200"},
    {"value": "63", "value_classification": "Greed", "timestamp": "1792108800"},
]})


class FakeInternet:
    """Responde por host. Cada fuente se puede cambiar o romper desde el test."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.gdelt = self.default_gdelt
        self.fed = lambda request: httpx.Response(
            200, content=FED_XML.encode("utf-8"),
            headers={"ETag": 'W/"fed1"', "Last-Modified": "Tue, 13 Oct 2026 18:00:13 GMT"},
        )
        self.fng = lambda request: httpx.Response(200, text=FNG)

    @staticmethod
    def default_gdelt(request: httpx.Request) -> httpx.Response:
        query = request.url.params["query"]
        if '"bitcoin"' in query:
            articles = [BTC_A, BTC_NEAR, SHARED]
        elif '"ethereum"' in query:
            articles = [ETH_A, SHARED]
        elif "crypto" in query and "tron" not in query:
            articles = [HACK]
        else:
            return httpx.Response(200, text="{}")  # sin resultados
        return httpx.Response(200, text=json.dumps({"articles": articles}))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        if host == "api.gdeltproject.org":
            return self.gdelt(request)
        if host == "www.federalreserve.gov":
            return self.fed(request)
        if host == "api.alternative.me":
            return self.fng(request)
        raise AssertionError(f"host NO verificado pedido por el colector: {host}")

    def hosts(self) -> set[str]:
        return {r.url.host for r in self.requests}

    def by_host(self, host: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == host]


class Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def make_collector(db, internet, *, clock=None, **settings_overrides):
    settings = Settings(
        _env_file=None, NEWS_COLLECTION_ENABLED=True, NEWS_USER_AGENT=UA, **settings_overrides,
    )  # type: ignore[call-arg]
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    async def symbols():
        return SYMBOLS

    clock = clock or Clock()
    fetcher = HttpFetcher(
        user_agent=UA, transport=httpx.MockTransport(internet), sleep=fake_sleep,
        jitter=lambda: 0.0,
    )
    coll = NewsCollector(
        db, settings, fetcher, clock=clock, sleep=fake_sleep, symbols_provider=symbols,
    )
    return coll, clock, sleeps


async def titles(db):
    return {r.title: r for r in await news_repo.get_recent(db, limit=100)}


# --- ciclo completo ------------------------------------------------------------------------------


async def test_a_full_cycle_stores_clean_tagged_and_deduplicated_headlines(db):
    internet = FakeInternet()
    coll, _, sleeps = make_collector(db, internet)

    results = await coll.run_once()

    assert results["gdelt"].startswith("5 titulares de 4/4 consultas")
    assert results["fed_monetary"] == "2 titulares"
    assert results["fear_greed"] == "2 puntos nuevos"
    originals = await titles(db)
    assert set(originals) == {
        "Bitcoin surges to a new record high today", "SEC approval lifts crypto market",
        "Ethereum upgrade ships; ETH rallies", "Exchange hacked, millions stolen",
        "Federal Reserve issues FOMC statement", "Minutes of the Federal Open Market Committee",
    }
    btc = originals["Bitcoin surges to a new record high today"]
    assert btc.assets == ["BTC"] and btc.sentiment > 0 and btc.sentiment_method == "LEXICON_V1"
    assert btc.domain == "example.com" and btc.query_tag == "BTC"
    assert btc.published_at == datetime(2026, 11, 1, 11, 30, tzinfo=UTC)
    assert originals["Exchange hacked, millions stolen"].sentiment < 0
    # activo SOLO del titular: 'Ether...' y 'ETH' -> ETH; el titular de la SEC no nombra ninguno
    assert originals["Ethereum upgrade ships; ETH rallies"].assets == ["ETH"]
    assert originals["SEC approval lifts crypto market"].assets == []
    # la misma URL en dos consultas se guarda una vez; el titular casi igual queda marcado
    all_rows = await db.fetch_all("SELECT url_hash, dup_of, title FROM news_items")
    assert len({r["url_hash"] for r in all_rows}) == len(all_rows) == 7
    near = next(r for r in all_rows if r["title"].endswith("high today again"))
    assert near["dup_of"] is not None
    assert sum(1 for r in all_rows if r["dup_of"] is not None) == 1
    # Fed: fecha del comunicado conservada; Fear & Greed guardado
    fed = originals["Federal Reserve issues FOMC statement"]
    assert fed.source == "fed_monetary"
    assert fed.published_at == datetime(2026, 10, 13, 18, tzinfo=UTC)
    assert (await news_repo.latest_fear_greed(db, NOW))["value"] == 55
    # GDELT: una peticion cada 10 s (limite oficial: una cada 5 s), 4 consultas -> 3 pausas
    assert sleeps == [10.0, 10.0, 10.0]


async def test_requests_go_only_to_the_verified_hosts_with_our_user_agent(db):
    internet = FakeInternet()
    coll, _, _ = make_collector(db, internet)

    await coll.run_once()

    assert internet.hosts() == {
        "api.gdeltproject.org", "www.federalreserve.gov", "api.alternative.me",
    }
    assert all(r.headers["user-agent"] == UA for r in internet.requests)
    queries = [r.url.params["query"] for r in internet.by_host("api.gdeltproject.org")]
    assert queries[0] == assets.GENERAL_QUERY and len(queries) == 4  # NOEXISTEUSDT no tiene alias
    assert all("sourcelang:english" in q for q in queries)
    params = internet.by_host("api.gdeltproject.org")[1].url.params
    assert params["mode"] == "artlist" and params["format"] == "json"
    assert params["sort"] == "DateDesc" and params["maxrecords"] == "50"


async def test_health_and_fetch_state_are_recorded_per_source(db):
    internet = FakeInternet()
    coll, _, _ = make_collector(db, internet)

    await coll.run_once()

    rows = {r["source"]: r for r in await health_repo.get_health_rows(db, "news:")}
    assert set(rows) == {"news:gdelt", "news:fed_monetary", "news:fear_greed"}
    assert all(r["consecutive_failures"] == 0 and r["last_success_at"] for r in rows.values())
    gdelt = await news_repo.get_state(db, "gdelt")
    fed = await news_repo.get_state(db, "fed_monetary")
    fng = await news_repo.get_state(db, "fear_greed")
    assert gdelt.next_allowed_at == NOW + timedelta(hours=1) and gdelt.last_status == 200
    assert fed.etag == 'W/"fed1"' and fed.last_modified == "Tue, 13 Oct 2026 18:00:13 GMT"
    assert fng.next_allowed_at == NOW + timedelta(hours=6)


async def test_a_second_cycle_before_the_interval_makes_no_requests(db):
    internet = FakeInternet()
    coll, clock, _ = make_collector(db, internet)
    await coll.run_once()
    made = len(internet.requests)

    clock.now = NOW + timedelta(minutes=30)
    results = await coll.run_once()

    assert results == {"gdelt": "aplazada", "fed_monetary": "aplazada", "fear_greed": "aplazada"}
    assert len(internet.requests) == made


async def test_after_the_interval_the_fed_is_asked_conditionally_and_nothing_is_duplicated(db):
    internet = FakeInternet()
    coll, clock, _ = make_collector(db, internet)
    await coll.run_once()
    before = len(await news_repo.get_recent(db, limit=100))
    internet.fed = lambda request: httpx.Response(304)

    clock.now = NOW + timedelta(hours=1, minutes=1)
    results = await coll.run_once()

    fed_request = internet.by_host("www.federalreserve.gov")[-1]
    assert fed_request.headers["if-none-match"] == 'W/"fed1"'
    assert fed_request.headers["if-modified-since"] == "Tue, 13 Oct 2026 18:00:13 GMT"
    assert results["fed_monetary"] == "sin cambios (304)"
    assert results["fear_greed"] == "aplazada"  # su intervalo es de 6 h
    assert results["gdelt"] == "0 titulares de 4/4 consultas"  # mismas URLs: nada nuevo
    assert len(await news_repo.get_recent(db, limit=100)) == before
    state = await news_repo.get_state(db, "fed_monetary")
    assert state.etag == 'W/"fed1"' and state.last_status == 304


# --- fallos y degradacion -----------------------------------------------------------------------


def rate_limited(request):
    return httpx.Response(
        429, text="Please limit requests to one every 5 seconds or contact us.",
    )


async def test_a_429_stops_the_gdelt_cycle_records_the_failure_and_backs_off_exponentially(db):
    internet = FakeInternet()
    internet.gdelt = rate_limited
    coll, clock, sleeps = make_collector(db, internet, NEWS_SOURCES="gdelt")

    first = await coll.run_once()

    assert first["gdelt"].startswith("error: SourceRateLimited")
    assert len(internet.by_host("api.gdeltproject.org")) == 1 and sleeps == []  # corto el ciclo
    state = await news_repo.get_state(db, "gdelt")
    assert state.next_allowed_at == NOW + timedelta(minutes=5) and state.last_status == 429
    row = (await health_repo.get_health_rows(db, "news:gdelt"))[0]
    assert row["consecutive_failures"] == 1 and "SourceRateLimited" in row["last_error"]

    clock.now = NOW + timedelta(minutes=6)  # vencio el primer enfriamiento
    await coll.run_once()
    state = await news_repo.get_state(db, "gdelt")
    assert state.next_allowed_at == clock.now + timedelta(minutes=10)  # se duplica
    assert (await health_repo.get_health_rows(db, "news:gdelt"))[0]["consecutive_failures"] == 2


async def test_retry_after_longer_than_the_backoff_is_respected_and_capped():
    assert backoff_seconds(1) == 300.0 and backoff_seconds(2) == 600.0
    assert backoff_seconds(1, retry_after_s=3600.0) == 3600.0
    assert backoff_seconds(30) == 6 * 3600.0  # tope de 6 h
    assert backoff_seconds(1, retry_after_s=10**9) == 6 * 3600.0


async def test_the_gdelt_rate_limit_text_with_http_200_is_also_a_rate_limit(db):
    internet = FakeInternet()
    internet.gdelt = lambda request: httpx.Response(
        200, text="Please limit requests to one every 5 seconds or contact the author.",
    )
    coll, _, _ = make_collector(db, internet, NEWS_SOURCES="gdelt")

    results = await coll.run_once()

    assert results["gdelt"].startswith("error: SourceRateLimited")
    assert (await health_repo.get_health_rows(db, "news:gdelt"))[0]["consecutive_failures"] == 1


async def test_a_partial_gdelt_cycle_keeps_what_it_got_and_retries_sooner(db):
    internet = FakeInternet()
    calls = {"n": 0}

    def flaky(request):
        calls["n"] += 1
        if calls["n"] >= 3:
            return rate_limited(request)
        return FakeInternet.default_gdelt(request)

    internet.gdelt = flaky
    coll, _, _ = make_collector(db, internet, NEWS_SOURCES="gdelt")

    results = await coll.run_once()

    assert results["gdelt"].startswith("parcial:") and "(429)" in results["gdelt"]
    assert len(await news_repo.get_recent(db, limit=100)) >= 3  # lo logrado se conserva
    state = await news_repo.get_state(db, "gdelt")
    assert state.next_allowed_at == NOW + timedelta(minutes=30)
    row = (await health_repo.get_health_rows(db, "news:gdelt"))[0]
    assert row["consecutive_failures"] == 0 and row["last_success_at"]


async def test_one_failing_source_never_affects_the_others(db):
    internet = FakeInternet()
    internet.gdelt = lambda request: httpx.Response(500)
    coll, _, _ = make_collector(db, internet)

    results = await coll.run_once()

    assert results["gdelt"].startswith("error: FetchError")
    assert results["fed_monetary"] == "2 titulares" and results["fear_greed"] == "2 puntos nuevos"
    rows = {r["source"]: r for r in await health_repo.get_health_rows(db, "news:")}
    assert rows["news:gdelt"]["consecutive_failures"] == 1
    assert rows["news:fed_monetary"]["consecutive_failures"] == 0


async def test_hostile_xml_from_the_fed_is_refused_and_recorded_without_storing_anything(db):
    internet = FakeInternet()
    internet.fed = lambda request: httpx.Response(
        200,
        content=b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss><channel/></rss>',
    )
    coll, _, _ = make_collector(db, internet, NEWS_SOURCES="fed_monetary")

    results = await coll.run_once()

    assert results["fed_monetary"].startswith("error: UnsafeXmlError")
    assert await news_repo.get_recent(db) == []


async def test_a_garbage_fear_greed_answer_is_a_failure_and_a_repeat_adds_nothing(db):
    internet = FakeInternet()
    internet.fng = lambda request: httpx.Response(200, text='{"data": []}')
    coll, clock, _ = make_collector(db, internet, NEWS_SOURCES="fear_greed")
    assert (await coll.run_once())["fear_greed"].startswith("error: ValueError")

    internet.fng = lambda request: httpx.Response(200, text=FNG)
    clock.now = NOW + timedelta(hours=1)
    assert (await coll.run_once())["fear_greed"] == "2 puntos nuevos"
    clock.now = NOW + timedelta(hours=8)
    assert (await coll.run_once())["fear_greed"] == "0 puntos nuevos"  # idempotente


async def test_a_database_failure_while_storing_is_contained(db, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("disco lleno")

    monkeypatch.setattr(collector_module.news_repo, "insert_item", boom)
    coll, _, _ = make_collector(db, FakeInternet(), NEWS_SOURCES="fed_monetary")

    results = await coll.run_once()  # no lanza

    assert results["fed_monetary"].startswith("error: RuntimeError")


async def test_only_the_configured_sources_are_requested_and_excluded_ones_are_ignored(db):
    internet = FakeInternet()
    coll, _, _ = make_collector(
        db, internet, NEWS_SOURCES="fear_greed,cointelegraph,coindesk,decrypt",
    )

    results = await coll.run_once()

    assert set(results) == {"fear_greed"}
    assert internet.hosts() == {"api.alternative.me"}


# --- fechas y poda ------------------------------------------------------------------------------


async def test_future_dates_are_clamped_missing_gdelt_dates_mean_seen_now(db):
    internet = FakeInternet()
    future = gdelt_article(
        "https://f.test/1", "Bitcoin gains on future news", seen="20270101T000000Z"
    )
    undated = gdelt_article("https://f.test/2", "Ethereum rises without a date")
    undated.pop("seendate")
    internet.gdelt = lambda request: httpx.Response(
        200, text=json.dumps({"articles": [future, undated]})
    )
    coll, _, _ = make_collector(db, internet, NEWS_SOURCES="gdelt")

    await coll.run_once()

    rows = await titles(db)
    assert rows["Bitcoin gains on future news"].published_at == NOW
    assert rows["Ethereum rises without a date"].published_at == NOW


async def test_the_retention_prune_runs_at_most_once_a_day(db):
    internet = FakeInternet()
    coll, clock, _ = make_collector(
        db, internet, NEWS_SOURCES="fed_monetary", NEWS_RETENTION_DAYS=30
    )
    await coll.run_once()
    await db.execute(
        "UPDATE news_items SET fetched_at = ? WHERE url LIKE '%/a.htm'",
        ((NOW - timedelta(days=60)).isoformat(),),
    )

    clock.now = NOW + timedelta(hours=2)  # menos de 24 h desde la ultima poda
    await coll.run_once()
    assert len(await news_repo.get_recent(db, limit=100)) == 2

    clock.now = NOW + timedelta(hours=25)
    await coll.run_once()
    assert [r.title for r in await news_repo.get_recent(db, limit=100)] == [
        "Minutes of the Federal Open Market Committee",
    ]


# --- bucle --------------------------------------------------------------------------------------


async def test_run_forever_collects_and_stops_cleanly(db):
    internet = FakeInternet()
    coll, _, _ = make_collector(db, internet, NEWS_SOURCES="fear_greed")

    task = asyncio.create_task(coll.run_forever(tick_seconds=0.01, startup_delay_s=0.0))
    for _ in range(100):
        await asyncio.sleep(0.01)
        if internet.requests:
            break
    coll.stop()
    await asyncio.wait_for(task, timeout=2)

    assert internet.hosts() == {"api.alternative.me"}
    assert (await news_repo.latest_fear_greed(db, NOW))["value"] == 55


async def test_stopping_during_the_startup_delay_never_makes_a_request(db):
    internet = FakeInternet()
    coll, _, _ = make_collector(db, internet)
    coll.stop()

    await asyncio.wait_for(coll.run_forever(startup_delay_s=30.0), timeout=2)

    assert internet.requests == []
