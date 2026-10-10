"""Una sola llamada minima al LLM, para verificar clave/modelo/precio con un
gasto de centimos (subfase 3.6, fase iii-B). Ver docs/FASE3_6_LLM.md, seccion (h).

**Por defecto corre en --dry-run**, con un cliente falso: SIN RED y SIN COSTE.
Una llamada real a la API necesita --real Y --confirm-real A LA VEZ; nunca se
hace sola con solo una de las dos. `--max-calls` es 1 por defecto y no puede
superar 3 (tope duro: esto es un chequeo, no el piloto).

El script SIEMPRE trabaja sobre una COPIA de la base, nunca sobre la base de
trabajo (regla de CLAUDE.md): `--db` tiene que apuntar a una ruta distinta de
`settings.database_path`, verificado con un assert explicito antes de conectar.

Reutiliza `LlmDecisionService` tal como quedo en la fase (i): la reserva de
presupuesto diario (seccion e) corre DENTRO de `decide_group`, antes de llamar
al cliente -- si no hay presupuesto, el resultado impreso dice
`BUDGET_EXCEEDED` y no hubo ninguna llamada real; no hace falta duplicar esa
comprobacion aqui.

Elige hasta `--max-calls` grupos de señal ya guardados en la copia (sombras
abiertas o cerradas que todavia no tengan fila en `llm_logs`), les construye el
prompt con datos YA DISPONIBLES (`app.llm.prompts`), y corre la decision con
`fase='PILOTO'` (es un chequeo que vive en una copia descartable, no cuenta
para la medicion real de todos modos).

Nunca imprime `ANTHROPIC_API_KEY` ni ningun otro secreto.

Ejecutalo asi:

    python scripts/llm_smoke.py --db data/backups/minerva_llm_copia.db
    python scripts/llm_smoke.py --db data/backups/minerva_llm_copia.db --real --confirm-real

Al terminar una llamada real, compara el coste impreso con tu consola de
Anthropic (platform.claude.com) antes de confiar en el numero.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, ".")

from app.config import settings  # noqa: E402
from app.llm.client import FakeLlmClient, LlmRawResponse, build_anthropic_client  # noqa: E402
from app.llm.decision_service import LlmDecisionService  # noqa: E402
from app.llm.prompts import (  # noqa: E402
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_features_from_shadow_trade,
    build_user_message,
    estimate_input_tokens,
)
from app.persistence.database import Database  # noqa: E402
from app.persistence.repositories import llm_logs_repo, shadow_repo  # noqa: E402

MAX_CALLS_HARD_CAP = 3


def _fake_response() -> LlmRawResponse:
    text = '{"decision": "APROBAR", "confianza": 0.6, "razonamiento": "chequeo en seco, sin red"}'
    return LlmRawResponse(text=text, input_tokens=max(1, len(text) // 3), output_tokens=1,
                           latency_ms=0)


async def _pick_groups(db: Database, n: int) -> list:
    """Hasta `n` grupos (sombras abiertas o cerradas) que todavia no tengan fila
    en `llm_logs`, los mas recientes primero."""
    open_trades = await shadow_repo.get_open(db)
    closed_trades = await shadow_repo.get_closed(db)
    candidates = sorted(
        open_trades + closed_trades, key=lambda t: t.candle_close_time, reverse=True
    )
    picked = []
    for t in candidates:
        if len(picked) >= n:
            break
        if await llm_logs_repo.get_by_signal_group_key(db, t.signal_group_key) is not None:
            continue
        picked.append(t)
    return picked


async def main(args: argparse.Namespace) -> None:
    dry_run = not args.real
    db = Database(args.db)
    await db.connect()
    try:
        groups = await _pick_groups(db, args.max_calls)
        if not groups:
            print(
                "No hay grupos de señal disponibles sin fila en llm_logs en esta copia "
                "(¿esta vacia la tabla shadow_trades, o ya se usaron todos?)."
            )
            return

        fake_client = FakeLlmClient([_fake_response() for _ in groups]) if dry_run else None
        client_factory = (lambda s: fake_client) if dry_run else build_anthropic_client
        service = LlmDecisionService(db, settings, client_factory=client_factory, fase="PILOTO")

        print(f"Modo: {'DRY-RUN (sin red, sin coste)' if dry_run else 'REAL (llama a la API)'}")
        print(f"Modelo configurado: {settings.anthropic_sonnet_model}")
        print(f"prompt_version: {PROMPT_VERSION}")
        print(f"Grupos a procesar: {len(groups)}")
        print()

        for trade in groups:
            features = await build_features_from_shadow_trade(db, trade, settings)
            user_message = build_user_message(features)
            estimated_tokens = estimate_input_tokens(SYSTEM_PROMPT, user_message)

            result = await service.decide_group(
                signal_group_key=trade.signal_group_key, shadow_trade_id=trade.id,
                candle_close_time=trade.candle_close_time, system_prompt=SYSTEM_PROMPT,
                prompt_version=PROMPT_VERSION, user_message=user_message,
                estimated_input_tokens=estimated_tokens,
            )
            row = await llm_logs_repo.get_by_signal_group_key(db, trade.signal_group_key)

            print(f"-- grupo {trade.signal_group_key} ({trade.symbol} {trade.side.value}) --")
            print(f"  mensaje de usuario (sin secretos): {user_message}")
            print(f"  status: {result.status}   etiqueta: {result.label}")
            if row is not None:
                print(f"  tokens entrada/salida: {row['input_tokens']}/{row['output_tokens']} "
                      f"(estimados antes de llamar: {estimated_tokens})")
                print(f"  coste: {row['cost_usd']:.6f} USD   latencia: {row['latency_ms']} ms")
                print(f"  respuesta cruda: {row['response_raw']!r}")
                print(f"  paso la validacion JSON: {row['status'] == 'OK'}")
                if row["error"]:
                    print(f"  error: {row['error']}")
            print()

        if not dry_run:
            print(
                "Llamada real hecha. Compara el coste impreso arriba con tu consola de "
                "Anthropic (platform.claude.com) antes de confiar en el numero."
            )
    finally:
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", required=True, help="copia de la base (nunca la de trabajo)")
    parser.add_argument("--real", action="store_true", help="llama de verdad a la API")
    parser.add_argument("--confirm-real", action="store_true",
                         help="confirmacion obligatoria junto con --real")
    parser.add_argument("--max-calls", type=int, default=1)
    args = parser.parse_args()

    if not Path(args.db).exists():
        raise SystemExit(f"No existe: {args.db}")
    assert os.path.abspath(args.db) != os.path.abspath(settings.database_path), (
        "la ruta de --db no puede ser la base de trabajo (settings.database_path)"
    )
    if args.max_calls < 1 or args.max_calls > MAX_CALLS_HARD_CAP:
        raise SystemExit(f"--max-calls debe estar entre 1 y {MAX_CALLS_HARD_CAP}")
    if args.real and not args.confirm_real:
        raise SystemExit(
            "Una llamada real necesita --real Y --confirm-real juntos (por seguridad)."
        )
    if args.confirm_real and not args.real:
        raise SystemExit("--confirm-real no hace nada sin --real.")

    asyncio.run(main(args))
