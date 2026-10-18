import { useState } from "react";
import {
  ApiError,
  type ControlAction,
  type ControlResult,
  useControlAction,
  useControlAudit,
  useControlInfo,
  useRisk,
} from "./api";
import { Card } from "./components/Card";
import { ConfirmDialog } from "./components/ConfirmDialog";
import { StatTile } from "./components/StatTile";
import { Unavailable } from "./components/Unavailable";
import { fmtAgo, fmtDate, fmtPct } from "./format";

// Fase 5: estado de riesgo y controles. El estado (pausa, kill switch, breaker, stop por drawdown,
// perdida diaria, cupos) siempre; la racha de perdidas y los cruces del drawdown (derivados de
// RESULTADOS) solo si el servidor manda `numbers`. Los controles DETIENEN LA MEDICION: piden
// confirmacion explicita y la advertencia sale del servidor, la misma que Telegram.
const HIDDEN_NUMBERS =
  "Racha de perdidas y cruces de drawdown ocultos hasta el registro oficial del veredicto (regla del 2026-10-17).";

const BLOCK_LABELS: Record<string, string> = {
  SCHEDULER_PAUSED: "scheduler pausado",
  KILL_SWITCH_ACTIVE: "kill switch",
  DRAWDOWN_STOP_ACTIVE: "stop por drawdown",
  CIRCUIT_BREAKER_ACTIVE: "circuit breaker",
  DAILY_LOSS_LIMIT: "limite de perdida diaria",
};

const ACTION_ORDER: ControlAction[] = ["pause", "resume", "kill"];
const BUTTON_CLASSES: Record<ControlAction, string> = {
  pause: "bg-amber-600 hover:bg-amber-700",
  resume: "bg-green-700 hover:bg-green-800",
  kill: "bg-red-700 hover:bg-red-800",
};

const stateText = (paused: boolean, kill: boolean) =>
  `pausa ${paused ? "si" : "no"}, kill switch ${kill ? "si" : "no"}`;

function ControlPanel() {
  const info = useControlInfo();
  const audit = useControlAudit(20);
  const mutation = useControlAction();
  const [pending, setPending] = useState<ControlAction | null>(null);
  const [result, setResult] = useState<ControlResult | null>(null);

  if (info.isLoading) {
    return <p className="text-sm text-gray-500">Cargando controles...</p>;
  }
  if (info.isError || !info.data) {
    return (
      <Card title="Controles">
        <Unavailable error={info.error} what="Controles" />
      </Card>
    );
  }

  const controls = info.data;
  const errorText = mutation.error
    ? mutation.error instanceof ApiError && mutation.error.detail
      ? mutation.error.detail
      : "No se pudo ejecutar la accion. No se hizo ningun cambio."
    : null;

  function open(action: ControlAction) {
    mutation.reset();
    setResult(null);
    setPending(action);
  }

  function close() {
    mutation.reset();
    setPending(null);
  }

  return (
    <>
      <Card title="Controles (pausar, reanudar, kill switch)">
        <p className="mb-3 text-sm text-gray-700 dark:text-gray-300">
          Estado actual: {stateText(controls.state.paused, controls.state.kill_switch)}. Estos
          controles no abren ni cierran posiciones.
        </p>
        {!controls.enabled && (
          <p
            role="note"
            className="mb-3 rounded border border-gray-300 bg-gray-50 p-3 text-sm text-gray-700 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-300"
          >
            Controles desactivados (DASHBOARD_CONTROLS_ENABLED=false).
          </p>
        )}
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          {ACTION_ORDER.map((action) => {
            const entry = controls.actions[action];
            return (
              <div key={action} className="space-y-2">
                <button
                  type="button"
                  disabled={!controls.enabled}
                  onClick={() => open(action)}
                  className={`w-full rounded px-3 py-2 text-sm font-medium text-white disabled:opacity-40 ${BUTTON_CLASSES[action]}`}
                >
                  {entry.label}
                </button>
                <p className="text-xs text-gray-600 dark:text-gray-400">{entry.warning}</p>
              </div>
            );
          })}
        </div>
        {result && (
          <p
            role="status"
            className="mt-3 rounded border border-green-300 bg-green-50 p-3 text-sm text-green-900 dark:border-green-700 dark:bg-green-900/30 dark:text-green-200"
          >
            {result.message} (por {result.actor}, {fmtDate(result.at)})
          </p>
        )}
      </Card>

      <Card title="Registro de acciones de control">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-gray-200 text-gray-500 dark:border-gray-700 dark:text-gray-400">
                <th className="py-1 pr-2 font-normal">Cuando</th>
                <th className="py-1 pr-2 font-normal">Quien</th>
                <th className="py-1 pr-2 font-normal">Canal</th>
                <th className="py-1 pr-2 font-normal">Accion</th>
                <th className="py-1 pr-2 font-normal">Antes</th>
                <th className="py-1 pr-2 font-normal">Despues</th>
              </tr>
            </thead>
            <tbody>
              {(audit.data?.entries ?? []).map((e) => (
                <tr key={e.id} className="border-b border-gray-100 dark:border-gray-800">
                  <td className="whitespace-nowrap py-1 pr-2">{fmtDate(e.created_at)}</td>
                  <td className="py-1 pr-2">{e.actor}</td>
                  <td className="py-1 pr-2">{e.channel === "DASHBOARD" ? "dashboard" : "Telegram"}</td>
                  <td className="py-1 pr-2">
                    {controls.actions[e.action]?.label ?? e.action}
                    {!e.changed && " (sin cambios)"}
                  </td>
                  <td className="py-1 pr-2">{stateText(e.paused_before, e.kill_before)}</td>
                  <td className="py-1 pr-2">{stateText(e.paused_after, e.kill_after)}</td>
                </tr>
              ))}
              {(audit.data?.entries ?? []).length === 0 && (
                <tr>
                  <td colSpan={6} className="py-2 text-gray-400">
                    Ninguna accion de control todavia.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>

      {pending && (
        <ConfirmDialog
          title={controls.actions[pending].label}
          warning={controls.actions[pending].warning}
          phrase={controls.actions[pending].confirm_phrase}
          confirmLabel={controls.actions[pending].label}
          busy={mutation.isPending}
          error={errorText}
          onCancel={close}
          onConfirm={(typed) =>
            mutation.mutate(
              { action: pending, confirm: typed },
              {
                onSuccess: (response) => {
                  setResult(response);
                  setPending(null);
                },
              }
            )
          }
        />
      )}
    </>
  );
}

export function RiskTab() {
  const risk = useRisk();

  if (risk.isLoading) {
    return <p className="text-sm text-gray-500">Cargando...</p>;
  }
  if (risk.isError || !risk.data) {
    return (
      <div className="space-y-4">
        <Card title="Estado de riesgo">
          <Unavailable error={risk.error} what="Estado de riesgo" />
        </Card>
        <ControlPanel />
      </div>
    );
  }

  const r = risk.data;
  const numbers = r.reveal_allowed === true ? r.numbers : null;

  return (
    <div className="space-y-4">
      <Card title="Estado de riesgo">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatTile
            label="Scheduler (medicion)"
            value={r.paused ? "PAUSADO: medicion detenida" : "en marcha"}
          />
          <StatTile label="Kill switch" value={r.kill_switch_active ? "ACTIVO" : "inactivo"} />
          <StatTile
            label="Circuit breaker"
            value={
              r.circuit_breaker.active
                ? `ACTIVO hasta ${fmtDate(r.circuit_breaker.until)}`
                : "inactivo"
            }
          />
          <StatTile
            label={`Stop por drawdown (modo ${r.drawdown_stop.mode})`}
            value={r.drawdown_stop.active ? "ACTIVO" : "inactivo"}
          />
          <StatTile
            label="Perdida diaria"
            value={r.daily_loss.locked ? "BLOQUEADA hasta el cambio de dia" : "dentro del limite"}
          />
          <StatTile
            label="Posiciones abiertas"
            value={`${r.positions.open}/${r.positions.max} (${r.positions.long} LONG, ${r.positions.short} SHORT)`}
          />
          <StatTile
            label="Ultimo ciclo de senales"
            value={`${fmtAgo(r.scheduler.last_cycle_at)}${r.scheduler.stale ? " (STALE)" : ""}`}
          />
          <StatTile
            label="Limites"
            value={`breaker ${r.circuit_breaker.consecutive_losses_threshold} perdidas, drawdown ${fmtPct(r.drawdown_stop.max_drawdown_fraction, 0)}, dia ${fmtPct(r.daily_loss.max_daily_loss_fraction, 0)}`}
          />
        </div>
        <div className="mt-3">
          <p className="mb-1 text-sm text-gray-700 dark:text-gray-300">
            Entradas nuevas bloqueadas por
          </p>
          {r.entries_blocked_by.length === 0 ? (
            <p className="text-sm text-green-700 dark:text-green-400">ninguna</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {r.entries_blocked_by.map((code) => (
                <span
                  key={code}
                  className="rounded-full bg-red-100 px-2 py-0.5 text-xs text-red-800 dark:bg-red-900/40 dark:text-red-300"
                >
                  {BLOCK_LABELS[code] ?? code}
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="mt-3">
          {numbers ? (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <StatTile label="Racha de perdidas" value={String(numbers.consecutive_losses)} />
              <StatTile
                label="Cruces de drawdown"
                value={String(numbers.drawdown_trigger_count)}
              />
            </div>
          ) : (
            <p role="note" className="text-xs text-gray-500 dark:text-gray-400">
              {r.message ?? HIDDEN_NUMBERS}
            </p>
          )}
        </div>
      </Card>

      <Card title="Rechazos recientes del motor de riesgo">
        {Object.keys(r.rejections_today_by_reason).length > 0 && (
          <div className="mb-3 flex flex-wrap gap-2">
            {Object.entries(r.rejections_today_by_reason).map(([reason, count]) => (
              <span
                key={reason}
                className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-700 dark:bg-gray-700 dark:text-gray-200"
              >
                hoy {reason}: {count}
              </span>
            ))}
          </div>
        )}
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-gray-200 text-gray-500 dark:border-gray-700 dark:text-gray-400">
                <th className="py-1 pr-2 font-normal">Hora</th>
                <th className="py-1 pr-2 font-normal">Simbolo</th>
                <th className="py-1 pr-2 font-normal">Estrategia</th>
                <th className="py-1 pr-2 font-normal">Motivo</th>
              </tr>
            </thead>
            <tbody>
              {r.rejections.map((x) => (
                <tr key={x.id} className="border-b border-gray-100 dark:border-gray-800">
                  <td className="whitespace-nowrap py-1 pr-2">{fmtDate(x.created_at)}</td>
                  <td className="py-1 pr-2">
                    {x.symbol} {x.side}
                  </td>
                  <td className="py-1 pr-2">{x.strategy ?? "-"}</td>
                  <td className="py-1 pr-2">{x.reason}</td>
                </tr>
              ))}
              {r.rejections.length === 0 && (
                <tr>
                  <td colSpan={4} className="py-2 text-gray-400">
                    Ningun rechazo registrado.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>

      <ControlPanel />
    </div>
  );
}
