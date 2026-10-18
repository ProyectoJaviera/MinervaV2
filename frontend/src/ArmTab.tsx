import { type ArmEntry, useArm } from "./api";
import { Card } from "./components/Card";
import { StatTile } from "./components/StatTile";
import { Unavailable } from "./components/Unavailable";
import { fmtDate } from "./format";

// Fase 5: registro del brazo ejecutado de la cuenta paper (subfase 3.9). Cada decision APROBADA que
// el brazo considero, abierta u omitida, con su motivo consultable. Nunca trae resultados: los
// detalles ya pasan por una lista blanca en el servidor.
const OUTCOME_LABELS: Record<ArmEntry["outcome"], string> = {
  OPENED: "ABIERTA",
  SKIPPED: "OMITIDA",
  PENDING: "EN CURSO",
};

export function ArmTab() {
  const arm = useArm(50);

  if (arm.isLoading) {
    return <p className="text-sm text-gray-500">Cargando...</p>;
  }
  if (arm.isError || !arm.data) {
    return (
      <Card title="Brazo ejecutado">
        <Unavailable error={arm.error} what="Brazo ejecutado" />
      </Card>
    );
  }

  const data = arm.data;
  const legend = data.reason_legend;
  const reasons = Object.entries(data.skipped_by_reason);

  return (
    <div className="space-y-4">
      <Card title="Brazo ejecutado (cuenta paper)">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatTile
            label="Estado"
            value={data.status.active ? "activo" : data.status.enabled ? "sin armar" : "apagado"}
          />
          <StatTile label="Armado desde" value={fmtDate(data.status.armed_since)} />
          <StatTile label="Abiertas hoy" value={String(data.status.opened_today)} />
          <StatTile label="Omitidas hoy" value={String(data.status.skipped_today)} />
          <StatTile label="Abiertas (total)" value={String(data.totals.opened)} />
          <StatTile label="Omitidas (total)" value={String(data.totals.skipped)} />
          <StatTile label="Margen por operacion" value={`${data.config.margin_usdt} USDT x${data.config.leverage}`} />
          <StatTile
            label="Retraso maximo de la decision"
            value={`${data.config.max_decision_delay_s} s`}
          />
        </div>
        <p className="mt-3 text-sm text-gray-700 dark:text-gray-300">
          Estrategias elegibles para la cuenta:{" "}
          {data.config.eligible_strategies.length > 0
            ? data.config.eligible_strategies.join(", ")
            : "todas"}
        </p>
      </Card>

      <Card title="Omitidas por motivo">
        {reasons.length === 0 ? (
          <p className="text-sm text-gray-400">Ninguna omitida todavia.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {reasons.map(([reason, count]) => (
              <li key={reason}>
                <span className="font-medium">{reason}</span>: {count}
                {legend[reason] && (
                  <span className="block text-xs text-gray-500 dark:text-gray-400">
                    {legend[reason]}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Decisiones recientes del brazo">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-gray-200 text-gray-500 dark:border-gray-700 dark:text-gray-400">
                <th className="py-1 pr-2 font-normal">Hora</th>
                <th className="py-1 pr-2 font-normal">Simbolo</th>
                <th className="py-1 pr-2 font-normal">Estrategia</th>
                <th className="py-1 pr-2 font-normal">Retraso</th>
                <th className="py-1 pr-2 font-normal">Resultado</th>
                <th className="py-1 pr-2 font-normal">Motivo</th>
                <th className="py-1 pr-2 font-normal">Posicion</th>
              </tr>
            </thead>
            <tbody className="tabular-nums">
              {data.entries.map((e) => (
                <tr key={e.id} className="border-b border-gray-100 dark:border-gray-800">
                  <td className="whitespace-nowrap py-1 pr-2">{fmtDate(e.created_at)}</td>
                  <td className="py-1 pr-2">
                    {e.symbol} {e.side}
                  </td>
                  <td className="py-1 pr-2">{e.strategy ?? "-"}</td>
                  <td className="py-1 pr-2">{e.decision_delay_s.toFixed(1)} s</td>
                  <td className="py-1 pr-2 font-medium">{OUTCOME_LABELS[e.outcome]}</td>
                  <td className="py-1 pr-2" title={e.reason ? (legend[e.reason] ?? "") : ""}>
                    {e.reason ?? "-"}
                  </td>
                  <td className="py-1 pr-2">{e.trade_id !== null ? `#${e.trade_id}` : "-"}</td>
                </tr>
              ))}
              {data.entries.length === 0 && (
                <tr>
                  <td colSpan={7} className="py-2 text-gray-400">
                    Sin decisiones todavia.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-xs text-gray-400 dark:text-gray-500">
          Solo las decisiones APROBADA llegan al brazo; las RECHAZADA no abren nada. Sin PnL.
        </p>
      </Card>
    </div>
  );
}
