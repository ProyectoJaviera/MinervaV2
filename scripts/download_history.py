"""Descarga TODO lo que el backtest de Fase 2 necesita (universo, specs de
contrato, velas LAST_PRICE/MARK_PRICE y funding) y lo deja en SQLite.

Correccion post-Fase-2 (ver docs/FASE2_BLOQUEO_RED.md): antes, la descarga
por red y el calculo del backtest estaban entrelazados dentro de
`scripts/run_backtest.py`, lo que hacia imposible distinguir "esta
calculando" de "esta esperando una respuesta de red" en los logs. Ahora la
descarga es un paso PREVIO y SEPARADO: corre este script primero (puede
tardar bastante en la primera corrida completa; es idempotente y
reanudable, interrumpirlo con Ctrl+C y volver a correrlo no pierde
progreso ni vuelve a pedir lo que ya esta guardado) y recien despues
corre `scripts/run_backtest.py`, que ya NO toca la red -- si falta algo,
lanza un error claro en vez de descargar nada.

Uso:
    python scripts/download_history.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import UTC, datetime

sys.path.insert(0, ".")

from app.config import settings  # noqa: E402
from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.market.bitunix_rest import BitunixRestClient  # noqa: E402
from app.market.coingecko_client import CoinGeckoClient  # noqa: E402
from app.market.contract_specs import refresh_spec  # noqa: E402
from app.market.funding_history import download_missing_funding  # noqa: E402
from app.market.ohlcv_history import download_missing, interval_to_ms  # noqa: E402
from app.market.universe import refresh_universe  # noqa: E402
from app.persistence.database import Database  # noqa: E402
from app.persistence.repositories import ohlcv_repo  # noqa: E402
from app.strategies.registry import STRATEGY_TIMEFRAMES  # noqa: E402

logger = get_logger("download_history")

# Ancla de inicio anterior al piso real verificado de Bitunix (~2022-04-17):
# la paginacion hacia atras se detiene sola al agotar el historial.
START_MS = int(datetime(2022, 1, 1, tzinfo=UTC).timestamp() * 1000)


async def _check_internal_gaps(
    db: Database, symbol: str, interval: str, price_type: str, start_ms: int, end_ms: int
) -> None:
    """Compara velas esperadas vs. realmente guardadas dentro del rango
    que REALMENTE se descargo -- detecta huecos INTERNOS (p.ej. una
    interrupcion real del exchange), no la falta de historial anterior al
    piso real (que no es un hueco, es el limite real de los datos) NI la
    vela mas reciente que todavia no cierra.

    Correccion de un off-by-one real: antes se comparaba contra `end_ms`
    (el instante en que corre el script, "ahora"), no contra la ultima
    vela que realmente se alcanzo a descargar (`max_cached`) -- como la
    vela en curso nunca esta cerrada, "ahora" casi nunca coincide con un
    borde de vela real, y eso se contaba como "1 vela faltante" en la
    enorme mayoria de las series (46 de 60 en la corrida real), escondiendo
    los huecos genuinos entre el ruido."""
    step_ms = interval_to_ms(interval)
    if step_ms is None:
        return
    covered = await ohlcv_repo.get_covered_range(db, symbol, interval, price_type)
    if covered is None:
        return
    min_cached, max_cached = covered
    floor = await ohlcv_repo.get_floor(db, symbol, interval, price_type)
    effective_start = max(start_ms, floor) if floor is not None else max(start_ms, min_cached)
    effective_end = min(end_ms, max_cached)  # nunca exigir mas alla de lo realmente descargado
    if effective_start > effective_end:
        return
    expected = int((effective_end - effective_start) // step_ms) + 1
    actual = await ohlcv_repo.get_bar_count(
        db, symbol, interval, price_type, effective_start, effective_end
    )
    if actual < expected:
        logger.warning(
            "%s %s %s: %d velas faltantes dentro del rango cubierto (hueco interno) -- "
            "esperadas %d, guardadas %d",
            symbol, interval, price_type, expected - actual, expected, actual,
        )


async def main() -> None:
    setup_logging()
    t0 = time.time()
    db = Database(settings.database_path)
    await db.connect()
    rest_client = BitunixRestClient(
        base_url=settings.bitunix_rest_base_url,
        rate_limit_per_sec=settings.bitunix_rate_limit_per_sec,
    )
    coingecko_client = CoinGeckoClient(settings.coingecko_base_url, settings.coingecko_api_key)

    try:
        logger.info("Construyendo universo dinamico...")
        universe_entries = await refresh_universe(coingecko_client, rest_client, db, settings)
        universe_symbols = [e.symbol for e in universe_entries if e.included]
        logger.info("Universo incluido: %s", universe_symbols)

        all_symbols = sorted(set(universe_symbols) | set(settings.backtest_control_symbols_list))
        logger.info("Refrescando specs de contrato para %d simbolos...", len(all_symbols))
        for symbol in all_symbols:
            try:
                await refresh_spec(rest_client, db, symbol)
            except Exception:
                logger.exception("No se pudo refrescar specs de %s", symbol)

        timeframes = sorted({tf for tfs in STRATEGY_TIMEFRAMES.values() for tf in tfs})
        end_ms = int(time.time() * 1000)

        # Serie = una combinacion (simbolo, timeframe, price_type) de velas,
        # o un simbolo de funding. Se cuenta el total de antemano para el
        # log "serie X/Y" pedido.
        kline_series = [
            (symbol, tf, price_type)
            for symbol in all_symbols
            for tf in timeframes
            for price_type in ("LAST_PRICE", "MARK_PRICE")
        ]
        total_series = len(kline_series) + len(all_symbols)
        series_num = 0

        for symbol, tf, price_type in kline_series:
            series_num += 1
            logger.info(
                "serie %d/%d: velas %s %s %s (%s a %s)",
                series_num, total_series, symbol, tf, price_type,
                datetime.fromtimestamp(START_MS / 1000, tz=UTC).date(),
                datetime.fromtimestamp(end_ms / 1000, tz=UTC).date(),
            )
            t_series = time.time()
            await download_missing(rest_client, db, symbol, tf, START_MS, end_ms, price_type)
            await _check_internal_gaps(db, symbol, tf, price_type, START_MS, end_ms)
            # Solo se llega aqui si `download_missing` no lanzo ninguna
            # excepcion -- marca afirmativa de "serie completa" (tarea 1b,
            # corrige que `ohlcv_floor` pueda faltar aunque la cabeza SI
            # este completa, p.ej. tras una descarga interrumpida).
            await ohlcv_repo.mark_series_complete(db, symbol, tf, price_type, START_MS, end_ms)
            logger.info(
                "serie %d/%d: terminada (%.1fs)", series_num, total_series, time.time() - t_series
            )

        for symbol in all_symbols:
            series_num += 1
            logger.info("serie %d/%d: funding %s", series_num, total_series, symbol)
            t_series = time.time()
            await download_missing_funding(rest_client, db, symbol, START_MS, end_ms)
            logger.info(
                "serie %d/%d: terminada (%.1fs)", series_num, total_series, time.time() - t_series
            )

        logger.info(
            "Descarga completa en %.1fs. Ya puedes correr scripts/run_backtest.py.",
            time.time() - t0,
        )
    finally:
        await rest_client.aclose()
        await coingecko_client.aclose()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
