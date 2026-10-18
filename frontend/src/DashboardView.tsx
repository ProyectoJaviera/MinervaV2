import { useState } from "react";
import { AccountTab } from "./AccountTab";
import { ArmTab } from "./ArmTab";
import { RiskTab } from "./RiskTab";
import { SummaryTab } from "./SummaryTab";
import { UniverseTab } from "./UniverseTab";
import { useHealth, useLogout } from "./api";
import { CoinGeckoAttribution } from "./components/Attribution";
import { type TabDef, Tabs, tabId, tabPanelId } from "./components/Tabs";

// Fase 5: el dashboard pasa a tener pestanas. "Resumen" es todo lo que ya existia; las demas son
// la cuenta paper, el brazo ejecutado, el estado de riesgo con los controles y el universo con los
// veredictos de backtest. Cada pestana pide sus datos solo cuando se abre.
const TABS: TabDef[] = [
  { id: "summary", label: "Resumen" },
  { id: "account", label: "Cuenta paper" },
  { id: "arm", label: "Brazo ejecutado" },
  { id: "risk", label: "Riesgo y controles" },
  { id: "universe", label: "Universo y backtest" },
];

function ActiveTab({ id }: { id: string }) {
  switch (id) {
    case "account":
      return <AccountTab />;
    case "arm":
      return <ArmTab />;
    case "risk":
      return <RiskTab />;
    case "universe":
      return <UniverseTab />;
    default:
      return <SummaryTab />;
  }
}

export function DashboardView({ username }: { username: string }) {
  const logout = useLogout();
  const health = useHealth();
  const [active, setActive] = useState("summary");

  // Pausar el scheduler DETIENE LA MEDICION: el aviso se ve en todas las pestanas.
  const paused = health.data?.control?.paused === true;
  const kill = health.data?.control?.kill_switch_active === true;

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4">
      <div className="flex items-center justify-between">
        <span className="text-sm text-gray-500 dark:text-gray-400">Sesion: {username}</span>
        <button
          onClick={() => logout.mutate()}
          className="rounded border border-gray-300 px-3 py-1 text-sm text-gray-700 hover:bg-gray-100 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-700"
        >
          Cerrar sesion
        </button>
      </div>

      {(paused || kill) && (
        <div
          role="alert"
          className="rounded border border-red-300 bg-red-50 p-3 text-sm font-medium text-red-900 dark:border-red-700 dark:bg-red-900/30 dark:text-red-200"
        >
          {paused
            ? "LA MEDICION ESTA DETENIDA: el scheduler esta en pausa (sin senales, sombras ni etiquetado del LLM)."
            : null}
          {paused && kill ? " " : null}
          {kill ? "KILL SWITCH ACTIVO: no se abren entradas nuevas." : null}
        </div>
      )}

      <Tabs tabs={TABS} active={active} onChange={setActive} />
      <div role="tabpanel" id={tabPanelId(active)} aria-labelledby={tabId(active)}>
        <ActiveTab id={active} />
      </div>

      <footer className="border-t border-gray-200 pt-2 dark:border-gray-700">
        <CoinGeckoAttribution />
      </footer>
    </div>
  );
}
