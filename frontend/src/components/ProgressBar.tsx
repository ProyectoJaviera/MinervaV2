// Barra de progreso hacia un objetivo (p.ej. N efectivo hacia
// MIN_EFFECTIVE_N=300). Extremos redondeados, nunca mas del 100% de ancho
// aunque el valor supere el objetivo -- el numero exacto va en el texto, no
// solo en el ancho (dataviz: nunca color/geometria sola sin una etiqueta).
export function ProgressBar({
  label,
  value,
  target,
  statusColor = "bg-blue-500",
}: {
  label: string;
  value: number;
  target: number;
  statusColor?: string;
}) {
  const pct = target > 0 ? Math.min(100, (value / target) * 100) : 0;
  return (
    <div>
      <div className="mb-1 flex justify-between text-sm text-gray-700 dark:text-gray-300">
        <span>{label}</span>
        <span className="tabular-nums">
          {value}/{target}
        </span>
      </div>
      <div className="h-3 w-full overflow-hidden rounded-full bg-gray-200 dark:bg-gray-700">
        <div
          className={`h-full rounded-full ${statusColor} transition-[width]`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
