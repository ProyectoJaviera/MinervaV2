r"""Resumen diario por Telegram del bot real (puerto 8000) y de la medicion en vivo
(subfase 3.6). Hermano de `scripts/telegram_alert.py` (que avisa de caidas en un
CAMBIO de estado); este manda UN mensaje al dia, siempre, para ver de un vistazo
que todo sigue andando. Proceso COMPLETAMENTE separado del bot: si el bot no
responde, el resumen lo dice (y sigue informando lo que hay en la base).

Contenido: estado de `GET /health` (ok/stale, `frozen_hash_ok`, etiquetado en vivo),
etiquetadas y fallidas en las ultimas 24 h, costo del LLM en 24 h y acumulado de la
medicion, N efectivo por lado (x/300), ritmo y dias estimados POR CALENDARIO y EN MARCHA
(con el porcentaje de tiempo en marcha) y si el gate esta abierto. **Nunca PnL ni
resultados de sombras**: las consultas solo tocan `llm_logs` y las marcas de
tiempo/lado/simbolo de `shadow_trades` que ya usa `app.trading.measurement_status`
(el mismo modulo sin PnL que protege la API y el dashboard).

**De donde salen los datos.** `/health` solo da contadores "de hoy" (desde la
medianoche local) y `/api/measurement/status` exige sesion (el `.env` guarda solo el
HASH de la contrasena, no la contrasena), asi que los numeros de 24 h y de la
medicion salen de una COPIA de solo lectura de la base, hecha con la API de backup de
sqlite (la fuente se abre `mode=ro`) en un directorio temporal del sistema -- nunca
dentro de `data/` -- y borrada al terminar. `/health` se consulta en vivo para saber
si el bot responde ahora.

**Base por defecto: `data/minerva_estabilidad.db`**, la misma que fija
`scripts/start_bot.ps1` en el proceso del bot. NO se usa `settings.database_path`: el
`.env` puede apuntar a otra base (el default de `app/config.py` es `data/minerva.db`,
que ya no se escribe) y el resumen informaria una medicion que no es la real.

Token y chat_id vienen de `settings.telegram_bot_token`/`telegram_chat_id` y nunca
se imprimen ni se loguean. Probalo primero a mano, sin enviar nada:

    python scripts/telegram_daily.py --dry-run

Tarea de Windows "MinervaDailySummary" a las 09:15 (hora de Chile del PC): comando
exacto (Register-ScheduledTask, con la hora fija en LOCAL) en docs/MIGRACION_PC.md.
El script fija su propio directorio de trabajo, asi que funciona igual desde donde lo
dispare el Programador de tareas.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Igual que `scripts/telegram_alert.py`: el Programador de tareas arranca en
# System32, no en la raiz del repo -- se fija el cwd ANTES de importar nada local.
os.chdir(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ".")

from app.config import settings  # noqa: E402
from app.trading.ai_value import MIN_EFFECTIVE_N  # noqa: E402
from app.trading.measurement_status import (  # noqa: E402
    FASE_MEDICION,
    MeasurementStatus,
    compute_status,
    is_measurement_gate_open,
    pace_lines,
)
from app.trading.shadow_live_labeler import FROZEN_PROMPT_SHA256  # noqa: E402
from scripts.check_health import fetch_health  # noqa: E402
from scripts.telegram_alert import send_telegram_message  # noqa: E402

DEFAULT_DB_PATH = "data/minerva_estabilidad.db"
SUMMARY_WINDOW_HOURS = 24
FAILED_CAUSES = ("TIMEOUT", "ERROR_HTTP", "INVALID")


@dataclass(frozen=True)
class DbSnapshot:
    status: MeasurementStatus
    approved_24h: int
    rejected_24h: int
    failed_by_cause_24h: dict[str, int]
    budget_exceeded_24h: int
    cost_24h_usd: float


def snapshot_database(source: str, dest_dir: Path) -> Path:
    """Copia consistente de `source` en `dest_dir` con la API de backup de sqlite.
    La fuente se abre en solo lectura; el destino NUNCA puede estar dentro de `data/`."""
    source_path = Path(source).resolve()
    dest = (dest_dir / "daily_summary_copy.db").resolve()
    assert dest != source_path, "la copia no puede ser la base de origen"
    assert Path("data").resolve() not in dest.parents, "la copia no puede vivir en data/"
    src = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return dest


def read_snapshot(
    conn: sqlite3.Connection, now: datetime, frozen_hash: str = FROZEN_PROMPT_SHA256,
) -> DbSnapshot:
    """Solo `llm_logs` y `compute_status` (sin PnL): nunca resultados de sombras."""
    since = (now.astimezone(UTC) - timedelta(hours=SUMMARY_WINDOW_HOURS)).isoformat()
    approved = rejected = budget_exceeded = 0
    failed: dict[str, int] = {}
    rows = conn.execute(
        "SELECT status, decision, COUNT(*) AS n FROM llm_logs "
        "WHERE fase = ? AND prompt_sha256 = ? AND created_at >= ? GROUP BY status, decision",
        (FASE_MEDICION, frozen_hash, since),
    ).fetchall()
    for row in rows:
        if row["status"] == "OK":
            if row["decision"] == "APROBAR":
                approved += row["n"]
            elif row["decision"] == "RECHAZAR":
                rejected += row["n"]
        elif row["status"] in FAILED_CAUSES:
            failed[row["status"]] = failed.get(row["status"], 0) + row["n"]
        elif row["status"] == "BUDGET_EXCEEDED":
            budget_exceeded += row["n"]
    cost = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM llm_logs WHERE created_at >= ?",
        (since,),
    ).fetchone()["total"]
    return DbSnapshot(
        status=compute_status(conn, frozen_hash=frozen_hash), approved_24h=approved,
        rejected_24h=rejected, failed_by_cause_24h=failed, budget_exceeded_24h=budget_exceeded,
        cost_24h_usd=float(cost),
    )


def collect_snapshot(
    source: str, now: datetime, frozen_hash: str = FROZEN_PROMPT_SHA256,
) -> DbSnapshot:
    """Copia a un directorio temporal del sistema, lee y borra la copia."""
    with tempfile.TemporaryDirectory(prefix="minerva_daily_", ignore_cleanup_errors=True) as tmp:
        copy_path = snapshot_database(source, Path(tmp))
        conn = sqlite3.connect(f"file:{copy_path.as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            return read_snapshot(conn, now, frozen_hash)
        finally:
            conn.close()


def _local(dt: datetime | None, tz: ZoneInfo) -> str:
    return "ninguna" if dt is None else dt.astimezone(tz).strftime("%Y-%m-%d %H:%M")


def _health_lines(
    health: dict | None, health_error: str | None, port: int, now: datetime,
) -> list[str]:
    if health is None:
        return [
            f"BOT SIN RESPUESTA (puerto {port}): no contesta /health. {(health_error or '')[:160]}",
            "Lo que sigue sale de la base (copia de solo lectura), no del bot en vivo.",
        ]
    labeling = health.get("llm_labeling", {})
    status = str(health.get("status", "?")).upper()
    lines = [
        f"Bot (puerto {port}): {status} | hash congelado OK: "
        f"{'si' if labeling.get('frozen_hash_ok') else 'NO'} | etiquetado en vivo: "
        f"{'activo' if labeling.get('enabled') else 'DESACTIVADO'}",
    ]
    last_cycle = health.get("last_cycle_at")
    if last_cycle:
        age_min = (now - datetime.fromisoformat(last_cycle)).total_seconds() / 60
        lines.append(f"Ultimo ciclo de senales: hace {age_min:.0f} min")
    else:
        lines.append("Ultimo ciclo de senales: ninguno todavia")
    if status != "OK":
        lines.append("ATENCION: el bot esta STALE (el ciclo de senales no avanza).")
    if not labeling.get("frozen_hash_ok"):
        lines.append("ATENCION: el hash del prompt NO coincide con el congelado: el etiquetado "
                     "en vivo esta detenido.")
    if not labeling.get("enabled"):
        lines.append("ATENCION: el etiquetado en vivo esta desactivado: la medicion no avanza.")
    control = health.get("control", {})
    if control.get("paused"):
        lines.append(
            "ATENCION: el scheduler esta PAUSADO a proposito (comando de Telegram): la medicion "
            "esta DETENIDA. Reanuda con /resume."
        )
    if control.get("kill_switch_active"):
        lines.append("ATENCION: KILL SWITCH ACTIVO (no se abren entradas reales).")
    return lines


def _db_lines(snap: DbSnapshot | None, db_error: str | None, tz: ZoneInfo) -> list[str]:
    if snap is None:
        return [f"Base: no se pudo leer ({(db_error or 'error desconocido')[:160]})."]
    s = snap.status
    failed_total = sum(snap.failed_by_cause_24h.values())
    causes = ", ".join(f"{k} {v}" for k, v in sorted(snap.failed_by_cause_24h.items()))
    lines = [
        f"Ultimas {SUMMARY_WINDOW_HOURS} h (medicion):",
        f"- Etiquetadas: {snap.approved_24h + snap.rejected_24h} "
        f"(APROBADA {snap.approved_24h} / RECHAZADA {snap.rejected_24h})",
        f"- Fallidas: {failed_total}" + (f" ({causes})" if causes else ""),
        f"- Sin presupuesto (BUDGET_EXCEEDED): {snap.budget_exceeded_24h}",
        f"- Costo del LLM en {SUMMARY_WINDOW_HOURS} h: {snap.cost_24h_usd:.4f} USD",
        f"- Costo acumulado de la medicion: {s.total_cost_usd:.4f} USD en "
        f"{s.total_medicion_rows} filas",
        f"- Ultima fila de la medicion: {_local(s.data_now, tz)}",
        "",
        f"Avance de la medicion (punto de analisis: {MIN_EFFECTIVE_N} por lado):",
        f"- N efectivo APROBADA: {s.n_eff_approved}/{MIN_EFFECTIVE_N}",
        f"- N efectivo RECHAZADA: {s.n_eff_rejected}/{MIN_EFFECTIVE_N}",
        *(f"- {line}" for line in pace_lines(s)),
    ]
    if is_measurement_gate_open(s):
        lines.append(
            "- Gate: ABIERTO. Seguir el procedimiento de docs/VEREDICTO_3_6.md (copia, "
            "placebo oficial, y solo si sale CALIBRADO, el veredicto)."
        )
    else:
        lines.append(
            f"- Gate: CERRADO (faltan {s.shortfall('APROBADA')} aprobadas y "
            f"{s.shortfall('RECHAZADA')} rechazadas)"
        )
    return lines


def build_message(
    *, health: dict | None, health_error: str | None, snapshot: DbSnapshot | None,
    db_error: str | None, now: datetime, port: int = 8000,
    tz_name: str = "America/Santiago",
) -> str:
    tz = ZoneInfo(tz_name)
    lines = [
        f"Minerva - resumen diario {now.astimezone(tz).strftime('%Y-%m-%d %H:%M')} ({tz_name})",
        "MODO PAPER TRADING",
        "",
        *_health_lines(health, health_error, port, now),
        "",
        *_db_lines(snapshot, db_error, tz),
        "",
        "Este resumen no incluye resultados (PnL) de las sombras.",
    ]
    return "\n".join(lines)


def run(
    *,
    host: str,
    port: int,
    timeout: float,
    db_path: str,
    token: str,
    chat_id: str,
    dry_run: bool = False,
    now: datetime | None = None,
    frozen_hash: str = FROZEN_PROMPT_SHA256,
    fetch_health_fn: Callable[[str, int, float], tuple[dict | None, str | None]] = fetch_health,
    snapshot_fn: Callable[..., DbSnapshot] = collect_snapshot,
    send_fn: Callable[..., bool] = send_telegram_message,
) -> bool:
    """Devuelve si el resumen se entrego (o se imprimio en `dry_run`). Un bot caido
    o una base ilegible NO son un fallo del resumen: se informan dentro del mensaje.
    Solo un envio fallido a Telegram es `False`, para que el Programador de tareas
    marque la corrida como fallida."""
    now = now or datetime.now(UTC)
    health, health_error = fetch_health_fn(host, port, timeout)
    try:
        snapshot, db_error = snapshot_fn(db_path, now, frozen_hash), None
    except Exception as exc:  # noqa: BLE001 - la base ilegible se informa en el mensaje
        snapshot, db_error = None, f"{type(exc).__name__}: {exc}"
    message = build_message(
        health=health, health_error=health_error, snapshot=snapshot, db_error=db_error,
        now=now, port=port, tz_name=settings.report_timezone,
    )
    print(message)
    if dry_run:
        return True
    return send_fn(token, chat_id, message, timeout=timeout)


if __name__ == "__main__":
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--db", default=DEFAULT_DB_PATH,
        help="base viva de la que se hace la copia de solo lectura (default: la del bot real)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="imprime el resumen sin enviarlo a Telegram",
    )
    args = parser.parse_args()

    if not args.dry_run and (not settings.telegram_bot_token or not settings.telegram_chat_id):
        raise SystemExit(
            "Completa TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en .env antes de usar este script "
            "(ver el docstring de scripts/telegram_alert.py), o usa --dry-run."
        )

    ok = run(
        host=args.host, port=args.port, timeout=args.timeout, db_path=args.db,
        token=settings.telegram_bot_token, chat_id=settings.telegram_chat_id,
        dry_run=args.dry_run,
    )
    sys.exit(0 if ok else 1)
