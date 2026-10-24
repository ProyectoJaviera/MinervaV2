"""Registro oficial del veredicto de valor de la IA (docs/VEREDICTO_3_6.md).

El registro es el archivo que escribe `scripts/ai_value_report.py --out`:
`docs/VEREDICTO_3_6_REGISTRO*.txt` (con fecha, p.ej. `..._REGISTRO_2026-12-05.txt`).
Su EXISTENCIA con un veredicto valido es lo que, junto con el gate abierto, permite
mostrar resultados (PnL, salida, motivo de cierre) de las sombras en la API y el
dashboard (`app.trading.measurement_status.is_outcome_reveal_allowed`): abrir el
gate NO basta -- primero van el placebo oficial y el veredicto (regla del
2026-10-17: si el placebo sale NO INFORMATIVO se esperan 7 dias sin ver
resultados).

Falla CERRADO: un archivo ausente, vacio, ilegible, enorme, sin la linea
`Veredicto: <X>` del informe, o con otro texto, NO cuenta. El placebo
(`docs/PLACEBO_<fecha>.txt`) tiene un nombre que no cumple el patron, a proposito."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

RECORD_GLOB = "VEREDICTO_3_6_REGISTRO*.txt"
VALID_VERDICTS = ("APORTA_VALOR", "NO_APORTA_VALOR", "INCONCLUSO")
MAX_RECORD_BYTES = 1_000_000
DOCS_DIR = Path(__file__).resolve().parent.parent.parent / "docs"

# La linea del informe: "- **Veredicto: APORTA_VALOR** (...)". Se exige el prefijo
# "Veredicto:" -- la palabra suelta (p.ej. en el titulo del desglose "solo con
# APORTA_VALOR") no cuenta.
_VERDICT_RE = re.compile(r"Veredicto:\s*\**\s*(NO_APORTA_VALOR|APORTA_VALOR|INCONCLUSO)\b")


def _read_record_text(path: Path) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size > MAX_RECORD_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _verdict_in(path: Path) -> str | None:
    text = _read_record_text(path)
    if text is None:
        return None
    match = _VERDICT_RE.search(text)
    return match.group(1) if match else None


def find_verdict_record(docs_dir: Path | None = None) -> Path | None:
    """El primer registro oficial con un veredicto valido.

    Busca `VEREDICTO_3_6_REGISTRO*.txt` en `docs/`. FALLA CERRADO: un archivo ausente, vacio,
    ilegible, de mas de 1 MB, sin la linea `Veredicto: <X>` o con otro texto no cuenta.

    Args:
        docs_dir: carpeta donde buscar; por defecto, `docs/` del repositorio.

    Returns:
        La ruta del registro, o `None`.

    Efectos:
        Lee el directorio `docs/` y los archivos candidatos.
    """
    directory = docs_dir if docs_dir is not None else DOCS_DIR
    try:
        candidates = sorted(directory.glob(RECORD_GLOB))
    except OSError:
        return None
    for path in candidates:
        if _verdict_in(path) is not None:
            return path
    return None


@dataclass(frozen=True)
class VerdictRecordInfo:
    """Lo que el reporte de paso a dinero real toma del registro oficial: el
    veredicto, la marca MAGNITUD_BAJA y las lineas del bloque del veredicto,
    CITADAS tal cual (nunca se recalcula nada)."""

    path: Path
    verdict: str
    magnitud_baja: bool
    quoted_lines: tuple[str, ...]


def parse_verdict_record(path: Path) -> VerdictRecordInfo | None:
    """Lee un registro oficial y extrae el veredicto, `MAGNITUD_BAJA` y el bloque citado.

    Args:
        path: archivo del registro.

    Returns:
        El `VerdictRecordInfo`, o `None` si el archivo no es un registro valido (mismas
        reglas que `find_verdict_record`).

    Efectos:
        Lee el archivo.
    """
    text = _read_record_text(path)
    if text is None:
        return None
    match = _VERDICT_RE.search(text)
    if match is None:
        return None
    lines = text.splitlines()
    verdict_line = next((ln for ln in lines if _VERDICT_RE.search(ln)), "")
    quoted: list[str] = []
    in_block = False
    for line in lines:
        if line.startswith("## Veredicto"):
            in_block = True
            continue
        if in_block and line.startswith("## "):
            break
        if in_block and line.strip():
            quoted.append(line.rstrip())
    if not quoted:
        quoted = [verdict_line.rstrip()]
    return VerdictRecordInfo(
        path=path, verdict=match.group(1), magnitud_baja="MAGNITUD_BAJA" in verdict_line,
        quoted_lines=tuple(quoted),
    )


def load_official_record(docs_dir: Path | None = None) -> VerdictRecordInfo | None:
    """El registro oficial vigente, ya parseado.

    Args:
        docs_dir: carpeta donde buscar; por defecto, `docs/` del repositorio.

    Returns:
        El `VerdictRecordInfo` del primer registro valido, o `None`.

    Efectos:
        Lee el directorio `docs/` y el registro.
    """
    path = find_verdict_record(docs_dir)
    return parse_verdict_record(path) if path is not None else None
