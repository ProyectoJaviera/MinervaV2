"""Informe de la memoria de lecciones (texto). SOLO se llama con los resultados visibles
(`app.memory.gate.results_visible`): lleva tasas de acierto y retornos. `scripts/lessons_report.py`
comprueba la puerta ANTES de abrir la base para escribir; sin ella solo cuenta operaciones."""

from __future__ import annotations

from datetime import UTC, datetime

from app.config import Settings
from app.memory.calibration import calibration_table
from app.memory.entries import refresh_entries
from app.memory.lessons import ACTIVE, rebuild_lessons
from app.memory.postmortem import maybe_postmortem
from app.persistence.database import Database
from app.persistence.repositories import memory_repo


def _pct(value: float | None) -> str:
    return "n/d" if value is None else f"{value:.1%}"


async def build_report_text(
    db: Database, settings: Settings, *, now: datetime | None = None,
) -> str:
    now = now or datetime.now(UTC)
    added = await refresh_entries(db, now=now)
    build_id, lessons = await rebuild_lessons(db, min_sample=settings.lessons_min_sample, now=now)
    await maybe_postmortem(db, n_losses=settings.consecutive_losses_circuit_breaker, now=now)
    entries = await memory_repo.count_entries(db)
    by_status: dict[str, int] = {}
    for lesson in lessons:
        by_status[lesson.status] = by_status.get(lesson.status, 0) + 1
    base = lessons[0].base_winrate if lessons else None

    out = [
        "=== Memoria de lecciones (Fase 4) ===",
        f"Entradas de memoria (operaciones cerradas fiables): {entries} (nuevas ahora: {added})",
        f"Tasa de acierto base: {_pct(base)} | reconstruccion: {build_id}",
        "Lecciones por estado: " + (", ".join(f"{k} {v}" for k, v in sorted(by_status.items()))
                                     or "ninguna"),
        f"Una leccion solo es ACTIVE con n >= {settings.lessons_min_sample}, intervalo de Wilson "
        "del 99 % que EXCLUYE la tasa base y el mismo signo en las dos mitades del tiempo.",
        "",
        "## Lecciones ACTIVE",
    ]
    active = [x for x in lessons if x.status == ACTIVE]
    if not active:
        out.append("- ninguna (todavia no hay patron que supere esas tres condiciones)")
    for x in active:
        out.append(
            f"- {x.dimension} = {x.pattern}: n {x.n}, acierto {_pct(x.winrate)} "
            f"(IC99 [{_pct(x.ci_low)}, {_pct(x.ci_high)}]) vs base {_pct(x.base_winrate)}, "
            f"retorno medio {x.mean_return_pct:+.1f} % del margen, "
            f"mitades {x.half1_delta:+.1%} / {x.half2_delta:+.1%}"
        )

    reports = await memory_repo.get_reports(db, "POSTMORTEM", limit=1)
    out += ["", "## Ultimo post-mortem de racha perdedora"]
    if reports:
        body = reports[0]["body"]
        out.append(
            f"- {body['losses']} perdidas seguidas ({body['first_closed_at']} a "
            f"{body['last_closed_at']}), PnL neto {body['total_pnl_net_usdt']:+.4f} USDT; "
            f"en comun: {body['in_common'] or 'nada que supere el 75 %'}"
        )
    else:
        out.append("- ninguno (no hubo una racha de perdidas consecutivas)")

    calibration = await calibration_table(db)
    out += ["", "## Calibracion de la confianza del LLM (informativa)"]
    for decision, rows in calibration["table"].items():
        out.append(f"- {decision}:")
        for row in rows:
            if row["n"]:
                out.append(
                    f"    {row['bucket']}: n {row['n']}, acierto {_pct(row['winrate'])}, "
                    f"retorno medio {row['mean_return_pct']:+.1f} %"
                )
    out += [
        "",
        "Estadistica descriptiva de operaciones pasadas: no garantiza resultados. Se prueban "
        "muchas dimensiones a la vez, asi que una leccion ACTIVE es una pista a vigilar, no una "
        "verdad.",
    ]
    return "\n".join(out) + "\n"
