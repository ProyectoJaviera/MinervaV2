"""Piloto por repeticion sobre señales ya guardadas (subfase 3.6, fase iii-C).

**Por repeticion, no en vivo.** Corre en lote sobre grupos de señal que YA
estan guardados en una copia de la base (sombras de `shadow_trades`); no esta
conectado a `app/core/scheduler.py` ni al ciclo real del bot. Correrlo en vivo
necesitaria wirear el scheduler a esto -- una decision aparte, no tomada
todavia (`AUTO_OPEN_WITHOUT_LLM` sigue en `false`). Ver docs/FASE3_6_LLM.md,
seccion (k).

Mismas protecciones que `scripts/llm_smoke.py`: **--dry-run por defecto**
(cliente falso, sin red ni coste); una llamada real solo con **--real Y
--confirm-real juntos**; **copia obligatoria de la base** (`--db`, ruta
distinta de `settings.database_path`, verificada con assert). El presupuesto
diario se reserva dentro de `LlmDecisionService`, antes de cada llamada.

`--max-calls` (por defecto 30, **tope duro 50**): hasta esa cantidad de grupos
sin fila en `llm_logs`. Si hay menos disponibles en la copia, procesa los que
haya y lo avisa -- no es un error del script, es el estado real de los datos.

Reporta: validez del JSON (%), tokens de entrada/salida (media y p95), latencia
(media y p95, en ms), tasa de APROBAR y coste real total; y si la tasa de
aprobacion cae en la banda 15%-85% -- fuera de ella, el prompt se ajusta antes
de empezar la medicion y el piloto se repite con una `prompt_version` nueva
(seccion k).

Ejecutalo asi:

    python scripts/llm_pilot.py --db data/backups/minerva_llm_copia.db
    python scripts/llm_pilot.py --db data/backups/minerva_llm_copia.db --max-calls 30 \\
        --real --confirm-real
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
from app.llm.decision_service import STATUS_OK, LlmDecisionService  # noqa: E402
from app.llm.prompts import (  # noqa: E402
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_features_from_shadow_trade,
    build_user_message,
    estimate_input_tokens,
)
from app.persistence.database import Database  # noqa: E402
from app.persistence.repositories import llm_logs_repo, shadow_repo  # noqa: E402

MAX_CALLS_DEFAULT = 30
MAX_CALLS_HARD_CAP = 50
APPROVAL_BAND = (0.15, 0.85)


def _fake_response(i: int) -> LlmRawResponse:
    decision = "APROBAR" if i % 2 == 0 else "RECHAZAR"
    text = (f'{{"decision": "{decision}", "confianza": 0.6, '
            f'"razonamiento": "piloto en seco #{i}, sin red"}}')
    return LlmRawResponse(
        text=text, input_tokens=max(1, len(text) // 3), output_tokens=1, latency_ms=0
    )


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


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


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
        if len(groups) < args.max_calls:
            print(
                f"Aviso: se pidieron {args.max_calls} grupos; solo hay {len(groups)} "
                "disponibles en esta copia. Se procesan esos."
            )

        fake_client = (
            FakeLlmClient([_fake_response(i) for i in range(len(groups))]) if dry_run else None
        )
        client_factory = (lambda s: fake_client) if dry_run else build_anthropic_client
        service = LlmDecisionService(db, settings, client_factory=client_factory, fase="PILOTO")

        print(f"Modo: {'DRY-RUN (sin red, sin coste)' if dry_run else 'REAL (llama a la API)'}")
        print(f"Modelo: {settings.anthropic_sonnet_model}   prompt_version: {PROMPT_VERSION}")
        print(f"Procesando {len(groups)} grupos...")
        print()

        statuses: list[str] = []
        input_tokens: list[float] = []
        output_tokens: list[float] = []
        latencies: list[float] = []
        decisions: list[str] = []
        total_cost = 0.0
        null_counts: dict[str, int] = {}
        indicator_slots_total = 0
        indicator_slots_null = 0

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

            statuses.append(result.status)
            if row is not None:
                total_cost += row["cost_usd"] or 0.0
                if row["input_tokens"] is not None:
                    input_tokens.append(row["input_tokens"])
                if row["output_tokens"] is not None:
                    output_tokens.append(row["output_tokens"])
                if row["latency_ms"] is not None:
                    latencies.append(row["latency_ms"])
                if row["decision"] is not None:
                    decisions.append(row["decision"])

            # Cuantos grupos deciden con cada feature en null (ajuste 5, fase
            # iv): asi se sabe con que informacion real se esta decidiendo.
            for key, value in features.items():
                if key in ("indicators_by_strategy", "timeframe_by_strategy"):
                    continue
                if value is None:
                    null_counts[key] = null_counts.get(key, 0) + 1
            for indicator_value in features["indicators_by_strategy"].values():
                indicator_slots_total += 1
                if indicator_value is None:
                    indicator_slots_null += 1

        n = len(groups)
        valid = sum(1 for s in statuses if s == STATUS_OK)
        aprobar = sum(1 for d in decisions if d == "APROBAR")

        print(f"Grupos procesados: {n}")
        print(f"Validez del JSON (status OK): {valid}/{n} ({valid / n:.1%})")
        for status, etiqueta in (
            ("TIMEOUT", "timeouts"), ("ERROR_HTTP", "errores HTTP"),
            ("INVALID", "JSON invalido"), ("BUDGET_EXCEEDED", "sin presupuesto"),
        ):
            count = sum(1 for s in statuses if s == status)
            if count:
                print(f"  {etiqueta}: {count}")
        if input_tokens:
            print(f"Tokens de entrada: media {sum(input_tokens) / len(input_tokens):.1f}, "
                  f"p95 {_percentile(input_tokens, 0.95):.1f}")
        if output_tokens:
            print(f"Tokens de salida: media {sum(output_tokens) / len(output_tokens):.1f}, "
                  f"p95 {_percentile(output_tokens, 0.95):.1f}")
        if latencies:
            print(f"Latencia: media {sum(latencies) / len(latencies):.0f} ms, "
                  f"p95 {_percentile(latencies, 0.95):.0f} ms")
        if decisions:
            tasa = aprobar / len(decisions)
            dentro_de_banda = APPROVAL_BAND[0] <= tasa <= APPROVAL_BAND[1]
            print(f"Tasa de APROBAR: {aprobar}/{len(decisions)} ({tasa:.1%})")
            print(f"Dentro de la banda 15%-85%: {'SI' if dentro_de_banda else 'NO'}")
            if not dentro_de_banda:
                print(
                    "Fuera de banda: ajusta el prompt antes de empezar la medicion "
                    "(docs/FASE3_6_LLM.md, seccion k) y repite el piloto con una "
                    "prompt_version nueva."
                )
        else:
            print("Ninguna decision valida: no se puede evaluar la banda de aprobacion.")
        print(f"Coste real total: {total_cost:.6f} USD")

        print()
        print(f"Campos en null, de {n} grupos procesados (con que informacion real se decide):")
        if null_counts:
            for key in sorted(null_counts):
                print(f"  {key}: {null_counts[key]}/{n}")
        else:
            print("  ninguno")
        if indicator_slots_total:
            print(
                f"  indicators_by_strategy sin fila en 'signals' (por estrategia "
                f"contribuyente): {indicator_slots_null}/{indicator_slots_total}"
            )

        if not dry_run:
            print()
            print(
                "Llamadas reales hechas. Compara el coste total impreso arriba con tu "
                "consola de Anthropic (platform.claude.com) antes de confiar en el numero."
            )
    finally:
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", required=True, help="copia de la base (nunca la de trabajo)")
    parser.add_argument("--real", action="store_true", help="llama de verdad a la API")
    parser.add_argument("--confirm-real", action="store_true",
                         help="confirmacion obligatoria junto con --real")
    parser.add_argument("--max-calls", type=int, default=MAX_CALLS_DEFAULT)
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
