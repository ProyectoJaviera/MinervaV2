import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DashboardView } from "./DashboardView";
import { calledPaths, jsonResponse, mockFetch, withQueryClient } from "./testUtils";

const HEALTH_BODY = {
  status: "ok",
  mode: "PAPER_TRADING",
  last_beat_at: null,
  last_cycle_at: null,
  log_dropped_count: 0,
  llm_labeling: {
    enabled: false, frozen_hash_ok: true, labeled_today: 0, failed_today: 0,
    budget_exceeded_today: 0, budget_exhausted_at: null, cost_today_usd: 0,
  },
};

const MEASUREMENT_BODY = {
  frozen_hash: "abc", total_medicion_rows: 0, ok_by_label: { APROBADA: 0, RECHAZADA: 0 },
  total_ok: 0, approval_rate: null, within_band: null, non_ok_counts: {},
  json_repaired_count: 0, total_cost_usd: 0, cost_per_row_usd: null, omitted_without_row: 0,
  first_medicion_row_at: null, data_now: null, n_eff_approved: 0, n_eff_rejected: 0,
  shortfall_approved: 300, shortfall_rejected: 300, window_days: 0,
  rate_approved_per_day: null, rate_rejected_per_day: null, days_needed_approved: null,
  days_needed_rejected: null, gate_open: false, outcome_reveal_allowed: false,
};

const SHADOW_WITHOUT_OUTCOME = {
  id: 1, symbol: "BTCUSDT", side: "LONG", strategy: "ema_cross_9_21", status: "CLOSED",
  leverage: 10, margin_usdt: 5, notional_usdt: 50, qty: 1, entry_price: 100,
  opened_at: "2026-01-01T00:00:00+00:00", sl_price: 95, tp_price: 110, liq_price: 90,
  sl_margin_loss_pct: 20, contributing_strategies: ["ema_cross_9_21"], signal_group_key: "k1",
  candle_close_time: "2026-01-01T00:00:00+00:00", llm_decision: "APROBADA",
  executed_in_real_account: false,
  // Deliberadamente SIN pnl_net_usdt/exit_price/close_reason/closed_at --
  // asi responde el backend real con el gate cerrado.
};

const SHADOW_WITH_OUTCOME = {
  ...SHADOW_WITHOUT_OUTCOME,
  id: 2,
  pnl_gross_usdt: 5, pnl_net_usdt: 4.5, exit_price: 105, close_reason: "TP",
  closed_at: "2026-01-01T01:00:00+00:00",
};

function mockFetchRoutedByPath(routes: Record<string, { status: number; body: unknown }>) {
  return vi.fn((input: string | URL | Request) => {
    const url = typeof input === "string" ? input : input.toString();
    const path = url.split("?")[0];
    const route = routes[path];
    if (!route) throw new Error(`ruta no mockeada en el test: ${path}`);
    return Promise.resolve(jsonResponse(route.body, route.status));
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("DashboardView: columnas de PnL de las sombras", () => {
  it("NO muestra columnas de PnL/cierre cuando el backend no las manda (gate cerrado)", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetchRoutedByPath({
        "/health": { status: 200, body: HEALTH_BODY },
        "/api/measurement/status": { status: 200, body: MEASUREMENT_BODY },
        "/api/llm/decisions/recent": { status: 200, body: [] },
        "/api/shadow-trades/recent": { status: 200, body: [SHADOW_WITHOUT_OUTCOME] },
      })
    );

    render(withQueryClient(<DashboardView username="admin" />));

    await screen.findByText("BTCUSDT LONG");
    expect(screen.queryByText("PnL neto")).not.toBeInTheDocument();
    expect(screen.queryByText("Motivo de cierre")).not.toBeInTheDocument();
  });

  it("SI muestra columnas de PnL/cierre con el gate abierto Y el registro oficial", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetchRoutedByPath({
        "/health": { status: 200, body: HEALTH_BODY },
        "/api/measurement/status": {
          status: 200,
          body: { ...MEASUREMENT_BODY, gate_open: true, outcome_reveal_allowed: true },
        },
        "/api/llm/decisions/recent": { status: 200, body: [] },
        "/api/shadow-trades/recent": { status: 200, body: [SHADOW_WITH_OUTCOME] },
      })
    );

    render(withQueryClient(<DashboardView username="admin" />));

    await screen.findByText("BTCUSDT LONG");
    expect(screen.getByText("PnL neto")).toBeInTheDocument();
    expect(screen.getByText("Motivo de cierre")).toBeInTheDocument();
    expect(screen.getByText("TP")).toBeInTheDocument();
    expect(screen.queryByText(/seguir docs\/VEREDICTO_3_6\.md/i)).not.toBeInTheDocument();
  });

  it("gate abierto SIN registro: avisa 'seguir docs/VEREDICTO_3_6.md' y no muestra PnL", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetchRoutedByPath({
        "/health": { status: 200, body: HEALTH_BODY },
        "/api/measurement/status": {
          status: 200,
          body: { ...MEASUREMENT_BODY, gate_open: true, outcome_reveal_allowed: false },
        },
        "/api/llm/decisions/recent": { status: 200, body: [] },
        // el backend real no manda los campos de resultado en este estado
        "/api/shadow-trades/recent": { status: 200, body: [SHADOW_WITHOUT_OUTCOME] },
      })
    );

    render(withQueryClient(<DashboardView username="admin" />));

    await screen.findByText("BTCUSDT LONG");
    expect(screen.getByRole("status")).toHaveTextContent(/gate abierto: seguir docs\/VEREDICTO_3_6\.md/i);
    expect(screen.queryByText("PnL neto")).not.toBeInTheDocument();
    expect(screen.getByText(/queda oculto hasta que exista el registro oficial/i)).toBeInTheDocument();
  });

  it("falla cerrado: aunque el backend mandara resultados, sin outcome_reveal_allowed no se ven", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetchRoutedByPath({
        "/health": { status: 200, body: HEALTH_BODY },
        "/api/measurement/status": {
          status: 200,
          body: { ...MEASUREMENT_BODY, gate_open: true, outcome_reveal_allowed: false },
        },
        "/api/llm/decisions/recent": { status: 200, body: [] },
        "/api/shadow-trades/recent": { status: 200, body: [SHADOW_WITH_OUTCOME] },
      })
    );

    render(withQueryClient(<DashboardView username="admin" />));

    await screen.findByText("BTCUSDT LONG");
    expect(screen.queryByText("PnL neto")).not.toBeInTheDocument();
    expect(screen.queryByText("4.5000")).not.toBeInTheDocument();
    expect(screen.queryByText("TP")).not.toBeInTheDocument();
  });

  it("un backend viejo sin el campo outcome_reveal_allowed tampoco muestra resultados", async () => {
    const { outcome_reveal_allowed: _omit, ...legacy } = MEASUREMENT_BODY;
    vi.stubGlobal(
      "fetch",
      mockFetchRoutedByPath({
        "/health": { status: 200, body: HEALTH_BODY },
        "/api/measurement/status": { status: 200, body: { ...legacy, gate_open: true } },
        "/api/llm/decisions/recent": { status: 200, body: [] },
        "/api/shadow-trades/recent": { status: 200, body: [SHADOW_WITH_OUTCOME] },
      })
    );

    render(withQueryClient(<DashboardView username="admin" />));

    await screen.findByText("BTCUSDT LONG");
    expect(screen.queryByText("PnL neto")).not.toBeInTheDocument();
  });

  it("muestra el ritmo por calendario Y el ritmo en marcha, rotulados, con el porcentaje de tiempo en marcha", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetchRoutedByPath({
        "/health": { status: 200, body: HEALTH_BODY },
        "/api/measurement/status": {
          status: 200,
          body: {
            ...MEASUREMENT_BODY,
            window_days: 3.2,
            days_needed_approved: 317.7,
            days_needed_rejected: 237.5,
            offline_gap_hours: 2,
            running_window_days: 0.8,
            uptime_pct_window: 0.25,
            uptime_pct_total: 0.62,
            rate_approved_running_per_day: 3.7,
            rate_rejected_running_per_day: 5,
            days_needed_running_approved: 80.2,
            days_needed_running_rejected: 59.1,
          },
        },
        "/api/llm/decisions/recent": { status: 200, body: [] },
        "/api/shadow-trades/recent": { status: 200, body: [] },
      })
    );

    render(withQueryClient(<DashboardView username="admin" />));

    await screen.findByText(/Dias estimados para llegar a 300/);
    expect(screen.getByText("~317.7 dias de calendario")).toBeInTheDocument();
    expect(screen.getByText("~237.5 dias de calendario")).toBeInTheDocument();
    expect(screen.getByText("~80.2 dias EN MARCHA")).toBeInTheDocument();
    expect(screen.getByText("~59.1 dias EN MARCHA")).toBeInTheDocument();
    expect(screen.getByText(/incluye el tiempo con el bot apagado/)).toBeInTheDocument();
    expect(screen.getByText(/un hueco de mas de 2 h sin ciclos cuenta como apagado/)).toBeInTheDocument();
    expect(screen.getByText("25% (0.8 de 3.2 dias)")).toBeInTheDocument();
    expect(screen.getByText("62%")).toBeInTheDocument();
    expect(screen.getByText(/Menos de 1 dia en marcha en la ventana/)).toBeInTheDocument();
  });

  it("un backend viejo sin los campos de ritmo en marcha sigue funcionando y dice 'sin datos'", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetchRoutedByPath({
        "/health": { status: 200, body: HEALTH_BODY },
        "/api/measurement/status": {
          status: 200,
          body: { ...MEASUREMENT_BODY, days_needed_approved: 12.5 },
        },
        "/api/llm/decisions/recent": { status: 200, body: [] },
        "/api/shadow-trades/recent": { status: 200, body: [] },
      })
    );

    render(withQueryClient(<DashboardView username="admin" />));

    await screen.findByText("~12.5 dias de calendario");
    expect(screen.getAllByText("sin datos suficientes para estimar").length).toBeGreaterThanOrEqual(3);
    expect(screen.getByText("sin datos")).toBeInTheDocument();
    expect(screen.queryByText(/EN MARCHA$/)).not.toBeInTheDocument();
  });

  it("monta la tarjeta de noticias y memoria, y una falla de sus rutas no afecta al resto del panel", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetchRoutedByPath({
        "/health": { status: 200, body: HEALTH_BODY },
        "/api/measurement/status": { status: 200, body: MEASUREMENT_BODY },
        "/api/llm/decisions/recent": { status: 200, body: [] },
        "/api/shadow-trades/recent": { status: 200, body: [SHADOW_WITHOUT_OUTCOME] },
        "/api/news/status": { status: 500, body: {} },
        "/api/news/recent": { status: 500, body: {} },
        "/api/memory/status": { status: 500, body: {} },
      })
    );

    render(withQueryClient(<DashboardView username="admin" />));

    expect(await screen.findByText("Noticias y memoria de lecciones (Fase 4)")).toBeInTheDocument();
    expect(await screen.findByText("Noticias no disponibles.")).toBeInTheDocument();
    expect(await screen.findByText("Memoria no disponible.")).toBeInTheDocument();
    await screen.findByText("BTCUSDT LONG");
    expect(screen.getByText("Avance de la medicion")).toBeInTheDocument();
  });

  it("con el gate cerrado explica que faltan los 300 conglomerados y no muestra el aviso del procedimiento", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetchRoutedByPath({
        "/health": { status: 200, body: HEALTH_BODY },
        "/api/measurement/status": { status: 200, body: MEASUREMENT_BODY },
        "/api/llm/decisions/recent": { status: 200, body: [] },
        "/api/shadow-trades/recent": { status: 200, body: [SHADOW_WITHOUT_OUTCOME] },
      })
    );

    render(withQueryClient(<DashboardView username="admin" />));

    await screen.findByText("BTCUSDT LONG");
    expect(screen.getByText(/junte 300 conglomerados efectivos por lado/i)).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});

describe("DashboardView: pestanas (Fase 5)", () => {
  const SUMMARY_ROUTES = {
    "/health": { status: 200, body: HEALTH_BODY },
    "/api/measurement/status": { status: 200, body: MEASUREMENT_BODY },
    "/api/llm/decisions/recent": { status: 200, body: [] },
    "/api/shadow-trades/recent": { status: 200, body: [] },
  };
  const CONTROL = (paused: boolean, kill: boolean) => ({
    ...HEALTH_BODY,
    control: { paused, kill_switch_active: kill },
  });
  const NEW_TAB_PATHS = [
    "/api/account", "/api/arm", "/api/risk", "/api/universe", "/api/backtest/verdicts",
    "/api/control/info", "/api/control/audit",
  ];
  const UNIVERSE_MIN = {
    refreshed_at: "2026-10-18T11:12:13+00:00", age_hours: 1, stale: false,
    staleness_limit_hours: 48, refresh_hours: 24, universe_size: 10, last_refresh_error: null,
    included: ["BTCUSDT"], changes: [], rule: "regla",
    candidates: [
      {
        rank: 1, coingecko_id: "bitcoin", symbol: "BTCUSDT", market_cap_usd: 1648722303443,
        included: true, excluded_category: null, excluded_manual: false, has_bitunix_perp: true,
        price_sanity_ok: true, exclusion_reason: null, mark_price: 64000.5,
      },
    ],
    attribution: { text: "Data provided by CoinGecko", url: "https://www.coingecko.com" },
  };
  const PUSH = {
    enabled: true, active: true, sent_total: 4, failed_total: 1, dropped_total: 0,
    consecutive_failures: 0, last_sent_at: null, last_error: null,
  };

  function open(routes: Record<string, { status: number; body: unknown }> = {}) {
    const fetchMock = mockFetch({ ...SUMMARY_ROUTES, ...routes });
    vi.stubGlobal("fetch", fetchMock);
    render(withQueryClient(<DashboardView username="admin" />));
    return fetchMock;
  }

  const tab = (name: string) => screen.getByRole("tab", { name });

  it("ofrece las cinco secciones y abre en Resumen", async () => {
    open();

    await screen.findByText("Avance de la medicion");
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "Resumen", "Cuenta paper", "Brazo ejecutado", "Riesgo y controles", "Universo y backtest",
    ]);
    expect(tab("Resumen")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveAccessibleName("Resumen");
  });

  it("las pestanas nuevas no piden nada hasta que se abren", async () => {
    const fetchMock = open();

    await screen.findByText("Avance de la medicion");
    await waitFor(() => expect(calledPaths(fetchMock)).toContain("/api/shadow-trades/recent"));

    for (const path of NEW_TAB_PATHS) expect(calledPaths(fetchMock)).not.toContain(path);
  });

  it("cada pestana pide sus datos solo al abrirse", async () => {
    const fetchMock = open({
      "/api/account": { status: 404, body: {} },
      "/api/arm": { status: 404, body: {} },
    });
    await screen.findByText("Avance de la medicion");

    fireEvent.click(tab("Cuenta paper"));
    expect(await screen.findByText(/Cuenta paper: no disponible todavia/)).toBeInTheDocument();
    expect(screen.getByRole("tabpanel")).toHaveAccessibleName("Cuenta paper");
    expect(calledPaths(fetchMock)).toContain("/api/account");
    expect(calledPaths(fetchMock)).not.toContain("/api/arm");

    fireEvent.click(tab("Brazo ejecutado"));
    expect(await screen.findByText(/Brazo ejecutado: no disponible todavia/)).toBeInTheDocument();
    expect(calledPaths(fetchMock)).toContain("/api/arm");
    expect(screen.queryByText(/Cuenta paper: no disponible/)).not.toBeInTheDocument();
  });

  it("volver al Resumen muestra otra vez la medicion", async () => {
    open({ "/api/account": { status: 404, body: {} } });
    await screen.findByText("Avance de la medicion");

    fireEvent.click(tab("Cuenta paper"));
    await screen.findByText(/Cuenta paper: no disponible todavia/);
    expect(screen.queryByText("Avance de la medicion")).not.toBeInTheDocument();
    fireEvent.click(tab("Resumen"));

    expect(await screen.findByText("Avance de la medicion")).toBeInTheDocument();
  });

  it("sin pausa ni kill switch no hay aviso de medicion detenida", async () => {
    open();

    await screen.findByText("Avance de la medicion");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("un backend viejo sin el bloque control tampoco muestra el aviso", async () => {
    open({ "/health": { status: 200, body: HEALTH_BODY } }); // HEALTH_BODY no trae control

    await screen.findByText("Avance de la medicion");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("en pausa avisa que la medicion esta detenida, en todas las pestanas", async () => {
    open({
      "/health": { status: 200, body: CONTROL(true, false) },
      "/api/account": { status: 404, body: {} },
    });

    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent(/LA MEDICION ESTA DETENIDA/);
    expect(banner).toHaveTextContent(/sin senales, sombras ni etiquetado/);
    expect(banner).not.toHaveTextContent(/KILL SWITCH/);
    fireEvent.click(tab("Cuenta paper"));
    await screen.findByText(/Cuenta paper: no disponible todavia/);
    expect(screen.getByRole("alert")).toHaveTextContent(/LA MEDICION ESTA DETENIDA/);
  });

  it("con el kill switch activo lo dice sin afirmar que la medicion esta detenida", async () => {
    open({ "/health": { status: 200, body: CONTROL(false, true) } });

    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent(/KILL SWITCH ACTIVO: no se abren entradas nuevas/);
    expect(banner).not.toHaveTextContent(/MEDICION ESTA DETENIDA/);
  });

  it("en pausa y con kill switch muestra ambos avisos en un solo recuadro", async () => {
    open({ "/health": { status: 200, body: CONTROL(true, true) } });

    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent(/LA MEDICION ESTA DETENIDA/);
    expect(banner).toHaveTextContent(/KILL SWITCH ACTIVO/);
    expect(screen.getAllByRole("alert")).toHaveLength(1);
  });

  it("la atribucion de CoinGecko esta en el pie de cada pestana", async () => {
    open({
      "/api/account": { status: 404, body: {} },
      "/api/arm": { status: 404, body: {} },
      "/api/risk": { status: 404, body: {} },
      "/api/control/info": { status: 404, body: {} },
      "/api/control/audit": { status: 404, body: {} },
      "/api/universe": { status: 404, body: {} },
      "/api/backtest/verdicts": { status: 404, body: {} },
    });
    await screen.findByText("Avance de la medicion");

    for (const name of [
      "Resumen", "Cuenta paper", "Brazo ejecutado", "Riesgo y controles", "Universo y backtest",
    ]) {
      fireEvent.click(tab(name));
      const footer = screen.getByRole("contentinfo");
      const link = within(footer).getByRole("link", { name: "Data provided by CoinGecko" });
      expect(link).toHaveAttribute("href", "https://www.coingecko.com");
      expect(link).toHaveAttribute("target", "_blank");
      expect(link.getAttribute("rel")).toContain("noopener");
    }
  });

  it("en Universo la atribucion esta junto a los datos y tambien en el pie", async () => {
    open({
      "/api/universe": { status: 200, body: UNIVERSE_MIN },
      "/api/backtest/verdicts": { status: 404, body: {} },
    });
    await screen.findByText("Avance de la medicion");

    fireEvent.click(tab("Universo y backtest"));

    await screen.findByText("BTCUSDT");
    expect(screen.getAllByRole("link", { name: "Data provided by CoinGecko" })).toHaveLength(2);
  });

  it.each([
    ["activas con sus conteos", PUSH, "activas (4 enviadas, 1 fallos)"],
    ["activas sin fallos no mencionan fallos", { ...PUSH, failed_total: 0 }, "activas (4 enviadas)"],
    ["apagadas si el interruptor esta en false", { ...PUSH, enabled: false, active: false }, "apagadas"],
    ["sin armar si esta habilitado pero no arranco", { ...PUSH, active: false }, "sin armar"],
    ["n/d en un backend que aun no las tiene", undefined, "n/d"],
  ])("el Resumen muestra las notificaciones de Telegram: %s", async (_name, push, expected) => {
    open({
      "/health": { status: 200, body: push ? { ...HEALTH_BODY, telegram_push: push } : HEALTH_BODY },
    });

    const label = await screen.findByText("Notificaciones Telegram");
    expect(label.nextSibling).toHaveTextContent(expected);
  });

  it("el estado de las notificaciones nunca muestra texto de mensajes ni credenciales", async () => {
    open({
      "/health": {
        status: 200,
        body: { ...HEALTH_BODY, telegram_push: { ...PUSH, last_error: "TelegramApiError" } },
      },
    });

    await screen.findByText("Notificaciones Telegram");
    expect(document.body.textContent).not.toMatch(/token|chat_id|bot\d+:/i);
  });
});
