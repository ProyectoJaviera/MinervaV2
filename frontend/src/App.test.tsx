import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { jsonResponse, withQueryClient } from "./testUtils";

const HEALTH_BODY = {
  status: "ok",
  mode: "PAPER_TRADING",
  last_beat_at: null,
  last_cycle_at: null,
  log_dropped_count: 0,
  llm_labeling: {
    enabled: false,
    frozen_hash_ok: true,
    labeled_today: 0,
    failed_today: 0,
    budget_exceeded_today: 0,
    budget_exhausted_at: null,
    cost_today_usd: 0,
  },
};

const MEASUREMENT_BODY = {
  frozen_hash: "abc",
  total_medicion_rows: 0,
  ok_by_label: { APROBADA: 0, RECHAZADA: 0 },
  total_ok: 0,
  approval_rate: null,
  within_band: null,
  non_ok_counts: {},
  json_repaired_count: 0,
  total_cost_usd: 0,
  cost_per_row_usd: null,
  omitted_without_row: 0,
  first_medicion_row_at: null,
  data_now: null,
  n_eff_approved: 0,
  n_eff_rejected: 0,
  shortfall_approved: 300,
  shortfall_rejected: 300,
  window_days: 0,
  rate_approved_per_day: null,
  rate_rejected_per_day: null,
  days_needed_approved: null,
  days_needed_rejected: null,
  gate_open: false,
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
  document.documentElement.classList.remove("dark");
});

describe("App: gate de sesion", () => {
  it("muestra LoginView cuando /api/auth/me devuelve 401", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetchRoutedByPath({ "/api/auth/me": { status: 401, body: { detail: "No autenticado" } } })
    );

    render(withQueryClient(<App />));

    expect(await screen.findByText(/iniciar sesion/i)).toBeInTheDocument();
  });

  it("muestra DashboardView cuando /api/auth/me devuelve un username", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetchRoutedByPath({
        "/api/auth/me": { status: 200, body: { username: "renzo" } },
        "/health": { status: 200, body: HEALTH_BODY },
        "/api/measurement/status": { status: 200, body: MEASUREMENT_BODY },
        "/api/llm/decisions/recent": { status: 200, body: [] },
        "/api/shadow-trades/recent": { status: 200, body: [] },
      })
    );

    render(withQueryClient(<App />));

    expect(await screen.findByText(/Sesion: renzo/i)).toBeInTheDocument();
  });

  it("siempre muestra el banner de MODO PAPER TRADING, incluso sin sesion", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetchRoutedByPath({ "/api/auth/me": { status: 401, body: { detail: "No autenticado" } } })
    );

    render(withQueryClient(<App />));

    expect(screen.getByText(/MODO PAPER TRADING/i)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/iniciar sesion/i)).toBeInTheDocument());
  });
});
