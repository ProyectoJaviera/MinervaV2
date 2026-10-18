import { BandIndicator } from "./components/BandIndicator";
import { Card } from "./components/Card";
import { ProgressBar } from "./components/ProgressBar";
import { StatTile } from "./components/StatTile";
import { fmtDate, fmtDays, fmtPct, fmtUsd } from "./format";
import {
  type HealthResponse,
  useHealth,
  useMeasurementStatus,
  useRecentDecisions,
  useRecentShadows,
} from "./api";
import { NewsMemoryCard } from "./NewsMemoryCard";

// Pestana "Resumen": lo que antes era todo el dashboard (salud, costo del LLM, avance de la
// medicion, noticias y memoria, decisiones del LLM y sombras). Sin cambios de comportamiento,
// salvo el indicador de las notificaciones de Telegram en la tarjeta de salud (Fase 5).
const NON_OK_LABELS: Record<string, string> = {
  TIMEOUT: "Timeout",
  ERROR_HTTP: "Error HTTP",
  INVALID: "JSON invalido",
  BUDGET_EXCEEDED: "Presupuesto agotado",
};

function pushText(push: HealthResponse["telegram_push"]): string {
  if (!push) return "n/d";
  if (!push.enabled) return "apagadas";
  if (!push.active) return "sin armar";
  return `activas (${push.sent_total} enviadas${push.failed_total ? `, ${push.failed_total} fallos` : ""})`;
}

export function SummaryTab() {
  const health = useHealth();
  const measurement = useMeasurementStatus();
  const decisions = useRecentDecisions();
  const shadows = useRecentShadows();

  const gateOpen = measurement.data?.gate_open === true;
  // Falla CERRADO: sin el campo (backend viejo) o en false, nunca se muestran
  // resultados, ni siquiera si el backend los mandara. El frontend nunca los calcula.
  const revealAllowed = measurement.data?.outcome_reveal_allowed === true;
  const showOutcomeColumns =
    revealAllowed && (shadows.data ?? []).some((s) => "pnl_net_usdt" in s);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Card title="Salud del bot">
          {health.data ? (
            <div className="grid grid-cols-2 gap-3">
              <StatTile label="Estado" value={health.data.status} />
              <StatTile
                label="Etiquetado en vivo"
                value={health.data.llm_labeling.enabled ? "activo" : "apagado"}
              />
              <StatTile
                label="Hash congelado OK"
                value={health.data.llm_labeling.frozen_hash_ok ? "si" : "NO"}
              />
              <StatTile label="Ultimo ciclo" value={fmtDate(health.data.last_cycle_at)} />
              <StatTile label="Etiquetadas hoy" value={String(health.data.llm_labeling.labeled_today)} />
              <StatTile label="Fallidas hoy" value={String(health.data.llm_labeling.failed_today)} />
              <StatTile
                label="Notificaciones Telegram"
                value={pushText(health.data.telegram_push)}
              />
            </div>
          ) : (
            <p className="text-sm text-gray-500">Cargando...</p>
          )}
        </Card>

        <Card title="Costo del LLM">
          <div className="grid grid-cols-2 gap-3">
            <StatTile label="Hoy" value={fmtUsd(health.data?.llm_labeling.cost_today_usd ?? null, 6)} />
            <StatTile
              label="Acumulado medicion"
              value={fmtUsd(measurement.data?.total_cost_usd ?? null, 6)}
            />
            <StatTile
              label="Promedio por fila"
              value={fmtUsd(measurement.data?.cost_per_row_usd ?? null, 6)}
            />
            <StatTile
              label="Presupuesto agotado hoy"
              value={String(health.data?.llm_labeling.budget_exceeded_today ?? "-")}
            />
          </div>
        </Card>
      </div>

      <Card title="Avance de la medicion">
        {measurement.data ? (
          <div className="space-y-4">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <ProgressBar
                label="APROBADA (efectivas)"
                value={measurement.data.n_eff_approved}
                target={300}
                statusColor="bg-green-600"
              />
              <ProgressBar
                label="RECHAZADA (efectivas)"
                value={measurement.data.n_eff_rejected}
                target={300}
                statusColor="bg-amber-600"
              />
            </div>

            {gateOpen && !revealAllowed && (
              <p
                role="status"
                className="rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-900/30 dark:text-amber-200"
              >
                Gate abierto: seguir docs/VEREDICTO_3_6.md (copia, placebo oficial y, solo si sale
                CALIBRADO, el veredicto). No se muestran resultados hasta que exista el registro
                oficial del veredicto.
              </p>
            )}

            <BandIndicator rate={measurement.data.approval_rate} withinBand={measurement.data.within_band} />

            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <StatTile label="Filas MEDICION" value={String(measurement.data.total_medicion_rows)} />
              <StatTile label="JSON reparado" value={String(measurement.data.json_repaired_count)} />
              <StatTile label="Omitidas sin fila" value={String(measurement.data.omitted_without_row)} />
              <StatTile
                label="Gate de medicion"
                value={measurement.data.gate_open ? "ABIERTO" : "cerrado"}
              />
            </div>

            {Object.entries(measurement.data.non_ok_counts).some(([, n]) => n > 0) && (
              <div>
                <p className="mb-1 text-sm text-gray-700 dark:text-gray-300">No-OK por causa</p>
                <div className="flex flex-wrap gap-2">
                  {Object.entries(measurement.data.non_ok_counts)
                    .filter(([, n]) => n > 0)
                    .map(([cause, n]) => (
                      <span
                        key={cause}
                        className="rounded-full bg-red-100 px-2 py-0.5 text-xs text-red-800 dark:bg-red-900/40 dark:text-red-300"
                      >
                        {NON_OK_LABELS[cause] ?? cause}: {n}
                      </span>
                    ))}
                </div>
              </div>
            )}

            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <caption className="mb-1 text-left text-sm text-gray-700 dark:text-gray-300">
                  Dias estimados para llegar a 300 conglomerados efectivos
                </caption>
                <thead>
                  <tr className="border-b border-gray-200 text-gray-500 dark:border-gray-700 dark:text-gray-400">
                    <th className="py-1 pr-2 font-normal">Ritmo</th>
                    <th className="py-1 pr-2 font-normal">APROBADA</th>
                    <th className="py-1 pr-2 font-normal">RECHAZADA</th>
                  </tr>
                </thead>
                <tbody className="tabular-nums">
                  <tr className="border-b border-gray-100 dark:border-gray-800">
                    <th scope="row" className="py-1 pr-2 text-left font-medium">
                      Por calendario
                      <span className="block text-xs font-normal text-gray-500 dark:text-gray-400">
                        incluye el tiempo con el bot apagado
                      </span>
                    </th>
                    <td className="py-1 pr-2">
                      {fmtDays(measurement.data.days_needed_approved, "dias de calendario")}
                    </td>
                    <td className="py-1 pr-2">
                      {fmtDays(measurement.data.days_needed_rejected, "dias de calendario")}
                    </td>
                  </tr>
                  <tr>
                    <th scope="row" className="py-1 pr-2 text-left font-medium">
                      En marcha
                      <span className="block text-xs font-normal text-gray-500 dark:text-gray-400">
                        solo el tiempo evaluando senales (un hueco de mas de{" "}
                        {measurement.data.offline_gap_hours ?? 2} h sin ciclos cuenta como apagado)
                      </span>
                    </th>
                    <td className="py-1 pr-2">
                      {fmtDays(measurement.data.days_needed_running_approved, "dias EN MARCHA")}
                    </td>
                    <td className="py-1 pr-2">
                      {fmtDays(measurement.data.days_needed_running_rejected, "dias EN MARCHA")}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <StatTile
                label="Tiempo en marcha (ventana del ritmo)"
                value={
                  measurement.data.running_window_days == null
                    ? "sin datos"
                    : `${fmtPct(measurement.data.uptime_pct_window, 0)} (${measurement.data.running_window_days.toFixed(1)} de ${measurement.data.window_days.toFixed(1)} dias)`
                }
              />
              <StatTile
                label="Tiempo en marcha (desde el inicio de la medicion)"
                value={fmtPct(measurement.data.uptime_pct_total, 0)}
              />
            </div>
            <p className="text-xs text-gray-400 dark:text-gray-500">
              Ritmo sobre {measurement.data.window_days.toFixed(1)} dias de historia reciente. Los
              dias EN MARCHA equivalen a dias de calendario solo si el bot no vuelve a apagarse.
              {measurement.data.running_window_days != null &&
                measurement.data.running_window_days < 1 &&
                " Menos de 1 dia en marcha en la ventana: la estimacion en marcha es volatil."}{" "}
              Tasa de aprobacion: {fmtPct(measurement.data.approval_rate)}.
            </p>
          </div>
        ) : (
          <p className="text-sm text-gray-500">Cargando...</p>
        )}
      </Card>

      <NewsMemoryCard />

      <Card title="Decisiones recientes del LLM">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-gray-200 text-gray-500 dark:border-gray-700 dark:text-gray-400">
                <th className="py-1 pr-2">Hora</th>
                <th className="py-1 pr-2">Simbolo</th>
                <th className="py-1 pr-2">Decision</th>
                <th className="py-1 pr-2">Confianza</th>
                <th className="py-1 pr-2">Razonamiento</th>
                <th className="py-1 pr-2">Costo</th>
              </tr>
            </thead>
            <tbody>
              {(decisions.data ?? []).map((d) => (
                <tr key={d.signal_group_key} className="border-b border-gray-100 dark:border-gray-800">
                  <td className="py-1 pr-2 whitespace-nowrap">{fmtDate(d.created_at)}</td>
                  <td className="py-1 pr-2">
                    {d.symbol ?? "-"} {d.side ?? ""}
                  </td>
                  <td className="py-1 pr-2">
                    {d.decision ?? (
                      <span className="text-red-600 dark:text-red-400">{d.status}</span>
                    )}
                  </td>
                  <td className="py-1 pr-2">{d.confianza !== null ? d.confianza.toFixed(2) : "-"}</td>
                  <td className="py-1 pr-2 max-w-xs truncate" title={d.razonamiento ?? d.error ?? ""}>
                    {d.razonamiento ?? d.error ?? "-"}
                  </td>
                  <td className="py-1 pr-2">{fmtUsd(d.cost_usd, 6)}</td>
                </tr>
              ))}
              {(decisions.data ?? []).length === 0 && (
                <tr>
                  <td colSpan={6} className="py-2 text-gray-400">
                    Sin datos todavia.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title="Sombras recientes">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-gray-200 text-gray-500 dark:border-gray-700 dark:text-gray-400">
                <th className="py-1 pr-2">Abierta</th>
                <th className="py-1 pr-2">Simbolo</th>
                <th className="py-1 pr-2">Estrategia</th>
                <th className="py-1 pr-2">Estado</th>
                <th className="py-1 pr-2">Decision LLM</th>
                {/* Estas dos columnas solo aparecen con el gate abierto Y el registro
                    oficial del veredicto (outcome_reveal_allowed), y solo si el backend
                    manda los campos -- nunca se calculan aca. */}
                {showOutcomeColumns && (
                  <>
                    <th className="py-1 pr-2">PnL neto</th>
                    <th className="py-1 pr-2">Motivo de cierre</th>
                  </>
                )}
              </tr>
            </thead>
            <tbody>
              {(shadows.data ?? []).map((s) => (
                <tr key={s.id} className="border-b border-gray-100 dark:border-gray-800">
                  <td className="py-1 pr-2 whitespace-nowrap">{fmtDate(s.opened_at)}</td>
                  <td className="py-1 pr-2">
                    {s.symbol} {s.side}
                  </td>
                  <td className="py-1 pr-2">{s.strategy ?? "-"}</td>
                  <td className="py-1 pr-2">{s.status}</td>
                  <td className="py-1 pr-2">{s.llm_decision}</td>
                  {showOutcomeColumns && (
                    <>
                      <td className="py-1 pr-2">
                        {s.pnl_net_usdt !== null && s.pnl_net_usdt !== undefined
                          ? s.pnl_net_usdt.toFixed(4)
                          : "-"}
                      </td>
                      <td className="py-1 pr-2">{s.close_reason ?? "-"}</td>
                    </>
                  )}
                </tr>
              ))}
              {(shadows.data ?? []).length === 0 && (
                <tr>
                  <td colSpan={showOutcomeColumns ? 7 : 5} className="py-2 text-gray-400">
                    Sin datos todavia.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {!revealAllowed && (
          <p className="mt-2 text-xs text-gray-400 dark:text-gray-500">
            {gateOpen
              ? "El PnL de las sombras etiquetadas queda oculto hasta que exista el registro oficial del veredicto (ver el aviso de la tarjeta de avance)."
              : "El PnL de las sombras etiquetadas queda oculto hasta que la medicion junte 300 conglomerados efectivos por lado -- ver la tarjeta de avance de arriba."}
          </p>
        )}
      </Card>
    </div>
  );
}
