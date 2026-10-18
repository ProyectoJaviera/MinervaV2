import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RiskTab } from "./RiskTab";
import { calledPaths, mockFetch, withQueryClient } from "./testUtils";

const STOPS = "DETIENE LA MEDICION en curso: sin senales, sin sombras y sin etiquetado del LLM mientras dure.";

const RISK = {
  reveal_allowed: false,
  message: "Racha de perdidas y cruces de drawdown ocultos hasta el registro oficial del veredicto (regla del 2026-10-17).",
  paused: false, kill_switch_active: false, measurement_stopped: false, entries_blocked_by: [],
  circuit_breaker: { active: false, until: null, consecutive_losses_threshold: 4, cooldown_hours: 8 },
  drawdown_stop: { active: false, mode: "duro", max_drawdown_fraction: 0.2 },
  daily_loss: { locked: false, max_daily_loss_fraction: 0.05 },
  positions: { open: 1, max: 3, long: 1, short: 0, max_same_direction: 2 },
  scheduler: { last_cycle_at: new Date().toISOString(), stale: false, stale_after_minutes: 90 },
  numbers: null,
  rejections: [],
  rejections_today_by_reason: {},
};

const INFO = {
  enabled: true,
  state: { paused: false, kill_switch: false },
  actions: {
    pause: {
      label: "Pausar medicion", confirm_phrase: "PAUSAR", stops_measurement: true,
      warning: `ATENCION: pausar ${STOPS}`,
    },
    resume: {
      label: "Reanudar", confirm_phrase: "REANUDAR", stops_measurement: false,
      warning: "Reanudar levanta la pausa del scheduler Y el kill switch.",
    },
    kill: {
      label: "Kill switch", confirm_phrase: "KILL", stops_measurement: true,
      warning: `ATENCION: el kill switch bloquea entradas nuevas y pausa el scheduler: ${STOPS}`,
    },
  },
};

const AUDIT = {
  entries: [
    {
      id: 2, created_at: "2026-10-18T12:00:00+00:00", channel: "TELEGRAM", actor: "telegram",
      action: "pause", paused_before: true, kill_before: false, paused_after: true,
      kill_after: false, changed: false,
    },
    {
      id: 1, created_at: "2026-10-18T11:00:00+00:00", channel: "DASHBOARD", actor: "renzo",
      action: "kill", paused_before: false, kill_before: false, paused_after: true,
      kill_after: true, changed: true,
    },
  ],
};

const RESULT = {
  ok: true, action: "pause", changed: true, before: { paused: false, kill_switch: false },
  after: { paused: true, kill_switch: false }, actor: "renzo", audit_id: 3,
  at: "2026-10-18T12:30:00+00:00", drawdown_stop_active: false,
  message: "Scheduler PAUSADO. La medicion esta DETENIDA (sin senales, sombras ni etiquetado).",
};

type Overrides = Record<string, Parameters<typeof mockFetch>[0][string]>;

function setup(overrides: Overrides = {}) {
  const fetchMock = mockFetch({
    "/api/risk": { status: 200, body: RISK },
    "/api/control/info": { status: 200, body: INFO },
    "/api/control/audit": { status: 200, body: AUDIT },
    ...overrides,
  });
  vi.stubGlobal("fetch", fetchMock);
  render(withQueryClient(<RiskTab />));
  return fetchMock;
}

const value = (label: string) => screen.getByText(label).nextSibling as HTMLElement;

async function openDialog(label: string) {
  fireEvent.click(await screen.findByRole("button", { name: label }));
  return screen.findByRole("dialog");
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("RiskTab: estado de riesgo", () => {
  it("un sistema tranquilo muestra todo en marcha y nada bloqueado", async () => {
    setup();

    await screen.findByText("Scheduler (medicion)");
    expect(value("Scheduler (medicion)")).toHaveTextContent("en marcha");
    expect(value("Kill switch")).toHaveTextContent("inactivo");
    expect(value("Circuit breaker")).toHaveTextContent("inactivo");
    expect(value("Stop por drawdown (modo duro)")).toHaveTextContent("inactivo");
    expect(value("Perdida diaria")).toHaveTextContent("dentro del limite");
    expect(value("Posiciones abiertas")).toHaveTextContent("1/3 (1 LONG, 0 SHORT)");
    expect(value("Limites")).toHaveTextContent("breaker 4 perdidas, drawdown 20%, dia 5%");
    expect(screen.getByText("ninguna")).toBeInTheDocument();
    expect(screen.getByText("Ningun rechazo registrado.")).toBeInTheDocument();
  });

  it("muestra cada estado que bloquea entradas, con texto y no solo color", async () => {
    setup({
      "/api/risk": {
        status: 200,
        body: {
          ...RISK, paused: true, kill_switch_active: true, measurement_stopped: true,
          entries_blocked_by: [
            "SCHEDULER_PAUSED", "KILL_SWITCH_ACTIVE", "DRAWDOWN_STOP_ACTIVE",
            "CIRCUIT_BREAKER_ACTIVE", "DAILY_LOSS_LIMIT",
          ],
          circuit_breaker: { ...RISK.circuit_breaker, active: true, until: "2026-10-18T20:00:00+00:00" },
          drawdown_stop: { ...RISK.drawdown_stop, active: true },
          daily_loss: { ...RISK.daily_loss, locked: true },
          scheduler: { ...RISK.scheduler, last_cycle_at: null, stale: true },
        },
      },
    });

    await screen.findByText("Scheduler (medicion)");
    expect(value("Scheduler (medicion)")).toHaveTextContent("PAUSADO: medicion detenida");
    expect(value("Kill switch")).toHaveTextContent("ACTIVO");
    expect(value("Circuit breaker")).toHaveTextContent(/^ACTIVO hasta /);
    expect(value("Stop por drawdown (modo duro)")).toHaveTextContent("ACTIVO");
    expect(value("Perdida diaria")).toHaveTextContent("BLOQUEADA hasta el cambio de dia");
    expect(value("Ultimo ciclo de senales")).toHaveTextContent("(STALE)");
    for (const chip of [
      "scheduler pausado", "kill switch", "stop por drawdown", "circuit breaker",
      "limite de perdida diaria",
    ]) {
      expect(screen.getAllByText(chip).length).toBeGreaterThanOrEqual(1);
    }
    expect(screen.queryByText("ninguna")).not.toBeInTheDocument();
  });

  it("la racha de perdidas y los cruces de drawdown solo aparecen con la puerta abierta", async () => {
    setup({
      "/api/risk": {
        status: 200,
        body: { ...RISK, reveal_allowed: true, message: null,
                numbers: { consecutive_losses: 3, drawdown_trigger_count: 2 } },
      },
    });

    await screen.findByText("Racha de perdidas");
    expect(value("Racha de perdidas")).toHaveTextContent("3");
    expect(value("Cruces de drawdown")).toHaveTextContent("2");
  });

  it("falla cerrado: sin reveal_allowed no se ven aunque el backend los mande", async () => {
    setup({
      "/api/risk": {
        status: 200,
        body: { ...RISK, numbers: { consecutive_losses: 777, drawdown_trigger_count: 27182 } },
      },
    });

    await screen.findByText("Scheduler (medicion)");
    expect(screen.queryByText("Racha de perdidas")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/777|27182/);
    expect(screen.getByRole("note")).toHaveTextContent(/ocultos hasta el registro oficial/);
  });

  it("lista los rechazos recientes y sus conteos de hoy", async () => {
    setup({
      "/api/risk": {
        status: 200,
        body: {
          ...RISK,
          rejections: [
            {
              id: 5, created_at: "2026-10-18T11:00:00+00:00", symbol: "ETHUSDT", side: "SHORT",
              strategy: "donchian_breakout_20", reason: "STRATEGY_NOT_ELIGIBLE",
            },
          ],
          rejections_today_by_reason: { STRATEGY_NOT_ELIGIBLE: 2 },
        },
      },
    });

    expect(await screen.findByText("ETHUSDT SHORT")).toBeInTheDocument();
    expect(screen.getByText("hoy STRATEGY_NOT_ELIGIBLE: 2")).toBeInTheDocument();
    expect(screen.getByText("donchian_breakout_20")).toBeInTheDocument();
  });

  it("si el estado de riesgo aun no existe en el bot (404) lo dice y los controles siguen", async () => {
    setup({ "/api/risk": { status: 404, body: {} } });

    expect(await screen.findByText(/Estado de riesgo: no disponible todavia/)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Pausar medicion" })).toBeInTheDocument();
  });
});

describe("RiskTab: controles (pausar, reanudar, kill switch)", () => {
  it("muestra los tres botones con la advertencia del servidor debajo de cada uno", async () => {
    setup();

    expect(await screen.findByRole("button", { name: "Pausar medicion" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Reanudar" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Kill switch" })).toBeEnabled();
    expect(screen.getAllByText(/DETIENE LA MEDICION/)).toHaveLength(2); // pausar y kill
    expect(screen.getByText(/no abren ni cierran posiciones/)).toBeInTheDocument();
    expect(screen.getByText(/Estado actual: pausa no, kill switch no/)).toBeInTheDocument();
  });

  it("pedir una accion abre el dialogo con la advertencia y NO ejecuta nada", async () => {
    const fetchMock = setup();

    const dialog = await openDialog("Pausar medicion");

    expect(within(dialog).getByText(/ATENCION: pausar DETIENE LA MEDICION/)).toBeInTheDocument();
    expect(within(dialog).getByLabelText(/Para confirmar escribe/)).toHaveFocus();
    expect(within(dialog).getByRole("button", { name: "Pausar medicion" })).toBeDisabled();
    expect(calledPaths(fetchMock, "POST")).toEqual([]);
  });

  it("solo se habilita al escribir la palabra exacta y envia esa palabra", async () => {
    const posts: unknown[] = [];
    const fetchMock = setup({
      "POST /api/control/pause": (init) => {
        posts.push(JSON.parse(String(init?.body)));
        return { status: 200, body: RESULT };
      },
    });
    const dialog = await openDialog("Pausar medicion");
    const input = within(dialog).getByLabelText(/Para confirmar escribe/);
    const confirm = within(dialog).getByRole("button", { name: "Pausar medicion" });

    fireEvent.change(input, { target: { value: "pausa" } });
    expect(confirm).toBeDisabled();
    fireEvent.change(input, { target: { value: "pausar" } }); // sin importar mayusculas
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);

    await waitFor(() => expect(posts).toEqual([{ confirm: "PAUSAR" }]));
    const call = fetchMock.mock.calls.find(([, init]) => (init as RequestInit)?.method === "POST");
    expect(String(call?.[0])).toBe("/api/control/pause");
    expect((call?.[1] as RequestInit).credentials).toBe("include");
    expect((call?.[1] as RequestInit).headers).toEqual({ "Content-Type": "application/json" });
  });

  it("tras ejecutar muestra quien y cuando, cierra el dialogo y refresca el estado", async () => {
    const fetchMock = setup({ "POST /api/control/pause": { status: 200, body: RESULT } });
    const dialog = await openDialog("Pausar medicion");
    fireEvent.change(within(dialog).getByLabelText(/Para confirmar escribe/), {
      target: { value: "PAUSAR" },
    });
    const risksBefore = calledPaths(fetchMock).filter((p) => p === "/api/risk").length;

    fireEvent.click(within(dialog).getByRole("button", { name: "Pausar medicion" }));

    const status = await screen.findByText(/Scheduler PAUSADO\. La medicion esta DETENIDA/);
    expect(status).toHaveTextContent(/\(por renzo, /);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(calledPaths(fetchMock).filter((p) => p === "/api/risk").length).toBeGreaterThan(
        risksBefore
      )
    );
    expect(calledPaths(fetchMock)).toContain("/api/control/audit");
  });

  it("cancelar o pulsar Escape cierra el dialogo sin ejecutar nada", async () => {
    const fetchMock = setup();
    let dialog = await openDialog("Kill switch");
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancelar" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    dialog = await openDialog("Reanudar");
    fireEvent.keyDown(dialog, { key: "Escape" });

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(calledPaths(fetchMock, "POST")).toEqual([]);
  });

  it("cada accion pide su propia palabra", async () => {
    setup();

    for (const [label, phrase] of [["Kill switch", "KILL"], ["Reanudar", "REANUDAR"]]) {
      const dialog = await openDialog(label);
      expect(within(dialog).getByText(phrase)).toBeInTheDocument();
      fireEvent.change(within(dialog).getByLabelText(/Para confirmar escribe/), {
        target: { value: "PAUSAR" },
      });
      expect(within(dialog).getByRole("button", { name: label })).toBeDisabled();
      fireEvent.click(within(dialog).getByRole("button", { name: "Cancelar" }));
    }
  });

  it("si el servidor rechaza la accion muestra su motivo y el dialogo sigue abierto", async () => {
    setup({
      "POST /api/control/kill": {
        status: 422,
        body: { detail: "Para confirmar escribe exactamente KILL. No se hizo ningun cambio." },
      },
    });
    const dialog = await openDialog("Kill switch");
    fireEvent.change(within(dialog).getByLabelText(/Para confirmar escribe/), {
      target: { value: "kill" },
    });

    fireEvent.click(within(dialog).getByRole("button", { name: "Kill switch" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Para confirmar escribe exactamente KILL. No se hizo ningun cambio."
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.queryByText(/\(por /)).not.toBeInTheDocument();
  });

  it("ante un error sin detalle dice que no se hizo ningun cambio", async () => {
    setup({ "POST /api/control/pause": { status: 500, body: {} } });
    const dialog = await openDialog("Pausar medicion");
    fireEvent.change(within(dialog).getByLabelText(/Para confirmar escribe/), {
      target: { value: "PAUSAR" },
    });

    fireEvent.click(within(dialog).getByRole("button", { name: "Pausar medicion" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      /No se pudo ejecutar la accion\. No se hizo ningun cambio\./
    );
  });

  it("mientras se ejecuta bloquea el dialogo para no repetir la accion", async () => {
    let release: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const fetchMock = setup();
    const original = fetchMock.getMockImplementation();
    fetchMock.mockImplementation(async (input, init) => {
      if ((init as RequestInit | undefined)?.method === "POST") {
        await gate;
        return new Response(JSON.stringify(RESULT), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
      }
      return original!(input, init);
    });
    const dialog = await openDialog("Pausar medicion");
    fireEvent.change(within(dialog).getByLabelText(/Para confirmar escribe/), {
      target: { value: "PAUSAR" },
    });

    fireEvent.click(within(dialog).getByRole("button", { name: "Pausar medicion" }));

    const busy = await within(dialog).findByRole("button", { name: "Ejecutando..." });
    expect(busy).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Cancelar" })).toBeDisabled();
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.getByRole("dialog")).toBeInTheDocument(); // Escape no cancela a medias
    release?.();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(calledPaths(fetchMock, "POST")).toEqual(["/api/control/pause"]);
  });

  it("con los controles desactivados los botones no se pueden usar", async () => {
    setup({ "/api/control/info": { status: 200, body: { ...INFO, enabled: false } } });

    const pause = await screen.findByRole("button", { name: "Pausar medicion" });
    expect(pause).toBeDisabled();
    expect(screen.getByRole("button", { name: "Kill switch" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reanudar" })).toBeDisabled();
    expect(screen.getByText(/DASHBOARD_CONTROLS_ENABLED=false/)).toHaveAttribute("role", "note");
    fireEvent.click(pause);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("el registro de acciones dice quien, por que canal y el efecto", async () => {
    setup();

    await screen.findByText("Registro de acciones de control");
    const rows = (await screen.findAllByRole("row")).filter((r) =>
      within(r).queryByText("renzo") || within(r).queryByText("telegram")
    );
    const dashboard = rows.find((r) => within(r).queryByText("renzo")) as HTMLElement;
    expect(within(dashboard).getByText("dashboard")).toBeInTheDocument();
    expect(within(dashboard).getByText("Kill switch")).toBeInTheDocument();
    expect(within(dashboard).getByText("pausa no, kill switch no")).toBeInTheDocument();
    expect(within(dashboard).getByText("pausa si, kill switch si")).toBeInTheDocument();
    const telegram = rows.find((r) => within(r).queryByText("telegram")) as HTMLElement;
    expect(within(telegram).getByText("Telegram")).toBeInTheDocument();
    expect(within(telegram).getByText(/Pausar medicion \(sin cambios\)/)).toBeInTheDocument();
  });

  it("sin acciones todavia lo dice", async () => {
    setup({ "/api/control/audit": { status: 200, body: { entries: [] } } });

    expect(await screen.findByText("Ninguna accion de control todavia.")).toBeInTheDocument();
  });

  it("si los controles aun no existen en el bot (404) lo dice sin romper el estado", async () => {
    setup({ "/api/control/info": { status: 404, body: {} } });

    expect(await screen.findByText(/Controles: no disponible todavia/)).toBeInTheDocument();
    expect(screen.getByText("Scheduler (medicion)")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pausar medicion" })).not.toBeInTheDocument();
  });
});
