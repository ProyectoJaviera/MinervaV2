// Mismos textos de casos especiales que app/trading/measurement_status.py::_fmt_pace_days
// -- no reinventar la semantica del lado de Python. `undefined` = un backend viejo que
// todavia no manda el campo (el dashboard sigue funcionando).
export function fmtDays(value: number | null | undefined, unit = "dias"): string {
  if (value === null || value === undefined) return "sin datos suficientes para estimar";
  if (value === 0) return "ya alcanzado";
  return `~${value.toFixed(1)} ${unit}`;
}

export function fmtDate(iso: string | null): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString();
}

export function fmtPct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "-";
  return `${(value * 100).toFixed(digits)}%`;
}

export function fmtUsd(value: number | null, digits = 4): string {
  if (value === null) return "-";
  return `$${value.toFixed(digits)}`;
}
