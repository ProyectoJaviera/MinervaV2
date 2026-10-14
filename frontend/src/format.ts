// Mismo texto de casos especiales que scripts/measurement_status.py::_fmt_days
// -- no reinventar la semantica del lado de Python.
export function fmtDays(value: number | null): string {
  if (value === null) return "sin datos suficientes para estimar";
  if (value === 0) return "ya alcanzado";
  return `~${value.toFixed(1)} dias`;
}

export function fmtDate(iso: string | null): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString();
}

export function fmtPct(value: number | null, digits = 1): string {
  if (value === null) return "-";
  return `${(value * 100).toFixed(digits)}%`;
}

export function fmtUsd(value: number | null, digits = 4): string {
  if (value === null) return "-";
  return `$${value.toFixed(digits)}`;
}
