"""Repara huecos puntuales en `ohlcv_cache` para un simbolo/intervalo/
price_type (subfase 3.6, fase iv -- incidente de estabilidad 2026-10-10).
Ver el docstring de `app/market/ohlcv_history.py` para el hallazgo que
motiva esto: una respuesta "ancha" de Bitunix (`limit=200` sin `start_time`)
puede omitir una vela real que SI aparece con un pedido estrecho.

Usa `find_gaps`/`fill_gaps`: detecta los huecos en lo YA cacheado entre el
minimo y el maximo de cada serie, y los repara con pedidos estrechos.

Ejecutalo sobre una COPIA de la base (regla de CLAUDE.md, nunca la de
trabajo):

    python scripts/repair_ohlcv_gaps.py --db data/backups/copia.db \\
        --symbols DOGEUSDT,TRXUSDT,XRPUSDT --interval 1m
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, ".")

from app.config import settings  # noqa: E402
from app.market.bitunix_rest import BitunixRestClient  # noqa: E402
from app.market.ohlcv_history import fill_gaps, find_gaps, interval_to_ms  # noqa: E402
from app.persistence.database import Database  # noqa: E402
from app.persistence.repositories import ohlcv_repo  # noqa: E402


async def repair_one(
    client: BitunixRestClient, db: Database, symbol: str, interval: str, price_type: str,
) -> tuple[int, int]:
    step_ms = interval_to_ms(interval)
    if step_ms is None:
        print(f"{symbol} {interval} {price_type}: intervalo sin grilla conocida, se omite")
        return 0, 0
    covered = await ohlcv_repo.get_covered_range(db, symbol, interval, price_type)
    if covered is None:
        print(f"{symbol} {interval} {price_type}: sin datos cacheados, nada que reparar")
        return 0, 0
    min_cached, max_cached = covered
    bars = await ohlcv_repo.get_bars(db, symbol, interval, price_type, min_cached, max_cached)
    gaps = find_gaps([b.open_time for b in bars], step_ms, min_cached, max_cached)
    if not gaps:
        print(f"{symbol} {interval} {price_type}: 0 huecos")
        return 0, 0
    recovered = await fill_gaps(client, db, symbol, interval, price_type, gaps, step_ms)
    still_missing = set(gaps) - set(recovered)
    extra = f" -> {sorted(still_missing)}" if still_missing else ""
    print(
        f"{symbol} {interval} {price_type}: {len(gaps)} huecos, {len(recovered)} reparados, "
        f"{len(still_missing)} sin reparar{extra}"
    )
    return len(gaps), len(recovered)


async def main(db_path: str, symbols: list[str], interval: str, price_types: list[str]) -> None:
    db = Database(db_path)
    await db.connect()
    client = BitunixRestClient(
        base_url=settings.bitunix_rest_base_url,
        rate_limit_per_sec=settings.bitunix_rate_limit_per_sec,
    )
    try:
        total_gaps = total_fixed = 0
        for symbol in symbols:
            for price_type in price_types:
                gaps, fixed = await repair_one(client, db, symbol, interval, price_type)
                total_gaps += gaps
                total_fixed += fixed
        print(
            f"\nTotal: {total_gaps} huecos encontrados, {total_fixed} reparados, "
            f"{total_gaps - total_fixed} sin reparar"
        )
    finally:
        await client.aclose()
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", required=True, help="copia de la base (nunca la de trabajo)")
    parser.add_argument("--symbols", required=True, help="separados por coma (BTCUSDT,ETHUSDT)")
    parser.add_argument("--interval", default="1m")
    parser.add_argument("--price-types", default="LAST_PRICE,MARK_PRICE")
    args = parser.parse_args()

    if not Path(args.db).exists():
        raise SystemExit(f"No existe: {args.db}")
    assert os.path.abspath(args.db) != os.path.abspath(settings.database_path), (
        "la ruta de --db no puede ser la base de trabajo (settings.database_path)"
    )

    asyncio.run(main(
        args.db, [s.strip() for s in args.symbols.split(",")], args.interval,
        [p.strip() for p in args.price_types.split(",")],
    ))
