// Indicador de banda: posiciona la tasa de aprobacion (0-100%) sobre una
// escala, con la franja aceptable (15-85%) sombreada. El color de estado
// (dentro/fuera de banda) va SIEMPRE junto al texto "dentro"/"fuera", nunca
// solo el color del marcador (dataviz: status color nunca solo).
const BAND_LOW = 15;
const BAND_HIGH = 85;

export function BandIndicator({
  rate,
  withinBand,
}: {
  rate: number | null;
  withinBand: boolean | null;
}) {
  if (rate === null) {
    return <p className="text-sm text-gray-500 dark:text-gray-400">Sin decisiones OK todavia.</p>;
  }
  const pct = Math.min(100, Math.max(0, rate * 100));
  const statusText = withinBand ? "dentro de la banda" : "fuera de la banda";
  const statusColor = withinBand
    ? "text-green-700 dark:text-green-400"
    : "text-red-700 dark:text-red-400";
  const markerColor = withinBand ? "bg-green-600" : "bg-red-600";

  return (
    <div>
      <div className="mb-1 flex justify-between text-sm text-gray-700 dark:text-gray-300">
        <span>Tasa de APROBAR</span>
        <span className={`tabular-nums font-medium ${statusColor}`}>
          {pct.toFixed(1)}% ({statusText})
        </span>
      </div>
      <div className="relative h-3 w-full rounded-full bg-gray-200 dark:bg-gray-700">
        <div
          className="absolute h-full rounded-full bg-gray-300 dark:bg-gray-600"
          style={{ left: `${BAND_LOW}%`, width: `${BAND_HIGH - BAND_LOW}%` }}
        />
        <div
          className={`absolute top-1/2 h-4 w-1.5 -translate-y-1/2 rounded-full ${markerColor}`}
          style={{ left: `calc(${pct}% - 3px)` }}
        />
      </div>
      <div className="mt-0.5 flex justify-between text-xs text-gray-400 dark:text-gray-500">
        <span>0%</span>
        <span>{BAND_LOW}%-{BAND_HIGH}% aceptable</span>
        <span>100%</span>
      </div>
    </div>
  );
}
