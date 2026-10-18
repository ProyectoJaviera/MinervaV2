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

// --- Fase 5 ----------------------------------------------------------------------------------

// Numero sin separador de miles y sin ceros de relleno: 64000.5, 0.0007, 148.9.
export function fmtNum(value: number | null | undefined, maxDigits = 8): string {
  if (value === null || value === undefined) return "-";
  return value.toLocaleString("en-US", { useGrouping: false, maximumFractionDigits: maxDigits });
}

export function fmtUsdt(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "-";
  return `${value.toFixed(digits)} USDT`;
}

// Con signo explicito (+1.2345 / -0.5000): para PnL.
export function fmtSigned(value: number | null | undefined, digits = 4): string {
  if (value === null || value === undefined) return "-";
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}

// Capitalizacion de mercado compacta: 1.65T, 295.1B, 96.3M.
export function fmtCompact(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  const units: [number, string][] = [[1e12, "T"], [1e9, "B"], [1e6, "M"]];
  for (const [size, suffix] of units) {
    if (Math.abs(value) >= size) return `${(value / size).toFixed(value / size >= 100 ? 0 : 1)}${suffix}`;
  }
  return value.toFixed(0);
}

// Un valor que YA viene en porcentaje (70.37 -> "70.4%"), a diferencia de `fmtPct` (fraccion 0-1).
export function fmtPercentValue(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "-";
  return `${value.toFixed(digits)}%`;
}

export function fmtAgo(iso: string | null, nowMs: number = Date.now()): string {
  if (!iso) return "-";
  const minutes = Math.max(0, Math.round((nowMs - new Date(iso).getTime()) / 60_000));
  if (minutes < 60) return `hace ${minutes} min`;
  const hours = minutes / 60;
  if (hours < 48) return `hace ${hours.toFixed(1)} h`;
  return `hace ${(hours / 24).toFixed(1)} dias`;
}
