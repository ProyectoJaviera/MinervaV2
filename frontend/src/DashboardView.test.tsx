import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DashboardView } from "./DashboardView";
import { jsonResponse, withQueryClient } from "./testUtils";

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

    render(withQueryClient(<DashboardView username="renzo" />));

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

    render(withQueryClient(<DashboardView username="renzo" />));

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

    render(withQueryClient(<DashboardView username="renzo" />));

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

    render(withQueryClient(<DashboardView username="renzo" />));

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

    render(withQueryClient(<DashboardView username="renzo" />));

    await screen.findByText("BTCUSDT LONG");
    expect(screen.queryByText("PnL neto")).not.toBeInTheDocument();
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

    render(withQueryClient(<DashboardView username="renzo" />));

    await screen.findByText("BTCUSDT LONG");
    expect(screen.getByText(/junte 300 conglomerados efectivos por lado/i)).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
