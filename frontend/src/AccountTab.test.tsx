import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AccountTab } from "./AccountTab";
import { mockFetch, withQueryClient } from "./testUtils";

// Valores de RESULTADO distintivos: con la puerta cerrada ninguno puede verse en pantalla.
const OPEN_TRADE = {
  id: 1, symbol: "BTCUSDT", side: "LONG", strategy: "ema_cross_9_21", status: "OPEN",
  leverage: 10, margin_usdt: 5, notional_usdt: 50, qty: 0.0007, entry_price: 64000.5,
  opened_at: "2026-10-18T09:00:00+00:00", closed_at: null, sl_price: 63100, tp_price: null,
  trailing_distance: null, liq_price: 58000, sl_margin_loss_pct: 14, decision_source: "LLM_APROBADA",
};

const CLOSED_HIDDEN = {
  id: 2, symbol: "ETHUSDT", side: "SHORT", strategy: "donchian_breakout_20", status: "CLOSED",
  leverage: 10, margin_usdt: 5, notional_usdt: 50, qty: 0.02, entry_price: 2500,
  opened_at: "2026-10-17T10:00:00+00:00", closed_at: "2026-10-17T15:30:00+00:00",
  sl_price: 2600, tp_price: 2300, trailing_distance: null, liq_price: 2750, sl_margin_loss_pct: 20,
  decision_source: "LLM_APROBADA",
};

const CLOSED_REVEALED = {
  ...CLOSED_HIDDEN,
  pnl_gross_usdt: 124.567891, pnl_net_usdt: 123.456789, exit_price: 987.654321,
  close_reason: "SENTINEL_REASON", fill_source: "TICK",
};

const SUMMARY = { open: 1, closed: 1, max_simultaneous_positions: 3, margin_in_use_usdt: 5 };

const RESULTS = {
  balance_usdt: 223.456789, realized_pnl_usdt: 123.456789, unrealized_pnl_usdt: -1.5,
  equity_usdt: 221.956789, marks_missing: ["SOLUSDT"], closed: 3, wins: 2, losses: 1,
  profit_factor: 3.1415,
};

function body(overrides: Record<string, unknown>) {
  return {
    reveal_allowed: false, message: "Resultados de la cuenta paper ocultos hasta el registro oficial del veredicto (regla del 2026-10-17).",
    initial_capital_usdt: 100, summary: SUMMARY, open: [OPEN_TRADE], closed: [CLOSED_HIDDEN],
    results: null, ...overrides,
  };
}

function mount(account: { status: number; body: unknown }) {
  vi.stubGlobal("fetch", mockFetch({ "/api/account": account }));
  return render(withQueryClient(<AccountTab />));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AccountTab: cuenta paper", () => {
  it("con la puerta cerrada muestra estructura, niveles y horas, y nada de resultados", async () => {
    mount({ status: 200, body: body({}) });

    expect(await screen.findByText("BTCUSDT LONG")).toBeInTheDocument();
    expect(screen.getByText("ETHUSDT SHORT")).toBeInTheDocument();
    expect(screen.getByText("64000.5")).toBeInTheDocument();
    expect(screen.getByText("sin TP")).toBeInTheDocument(); // un nivel ausente se dice, no se inventa
    expect(screen.getByText("Cerrada")).toBeInTheDocument(); // la hora de cierre si se ve
    expect(screen.getByText("1/3")).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent(/ocultos hasta el registro oficial/);
    for (const hidden of ["PnL neto", "Motivo de cierre", "Salida", "Saldo realizado", "Equity"]) {
      expect(screen.queryByText(hidden)).not.toBeInTheDocument();
    }
  });

  it("falla cerrado: aunque el backend mande resultados, sin reveal_allowed no se ven", async () => {
    mount({
      status: 200,
      body: body({ closed: [CLOSED_REVEALED], results: RESULTS }), // reveal_allowed sigue false
    });

    await screen.findByText("ETHUSDT SHORT");
    expect(screen.queryByText("PnL neto")).not.toBeInTheDocument();
    expect(screen.queryByText("Saldo realizado")).not.toBeInTheDocument();
    const page = document.body.textContent ?? "";
    for (const leaked of ["123.4568", "123.456789", "987.654321", "SENTINEL_REASON", "223.46"]) {
      expect(page).not.toContain(leaked);
    }
  });

  it("con la puerta abierta muestra las columnas de resultado y el resumen", async () => {
    mount({
      status: 200,
      body: body({ reveal_allowed: true, message: null, closed: [CLOSED_REVEALED], results: RESULTS }),
    });

    await screen.findByText("ETHUSDT SHORT");
    expect(screen.getByText("PnL neto")).toBeInTheDocument();
    expect(screen.getByText("Motivo de cierre")).toBeInTheDocument();
    expect(screen.getByText("SENTINEL_REASON")).toBeInTheDocument();
    expect(screen.getAllByText("+123.4568")).toHaveLength(2); // la ficha del resumen y la fila
    expect(screen.getByText("987.654321")).toBeInTheDocument();
    expect(screen.getByText("223.46 USDT")).toBeInTheDocument(); // saldo realizado
    expect(screen.getByText("221.96 USDT")).toBeInTheDocument(); // equity
    expect(screen.getByText("2 / 1")).toBeInTheDocument();
    expect(screen.getByText("3.14")).toBeInTheDocument();
    expect(screen.getByText(/sin precio actual de SOLUSDT/)).toBeInTheDocument();
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });

  it("con la puerta abierta pero un backend que no manda los campos no inventa columnas", async () => {
    mount({ status: 200, body: body({ reveal_allowed: true, message: null }) });

    await screen.findByText("ETHUSDT SHORT");
    expect(screen.queryByText("PnL neto")).not.toBeInTheDocument();
  });

  it("sin posiciones dice que no hay ninguna todavia", async () => {
    mount({
      status: 200,
      body: body({ open: [], closed: [], summary: { ...SUMMARY, open: 0, closed: 0 } }),
    });

    expect(await screen.findAllByText("Ninguna todavia.")).toHaveLength(2);
  });

  it("si el bot en ejecucion aun no tiene la ruta (404) lo dice en vez de romper", async () => {
    mount({ status: 404, body: { detail: "Not Found" } });

    const message = await screen.findByRole("status");
    expect(message).toHaveTextContent(/Cuenta paper: no disponible todavia/);
    expect(message).toHaveTextContent(/reiniciarlo con la Fase 5/);
  });

  it("ante otro error del servidor dice que no esta disponible", async () => {
    mount({ status: 500, body: {} });

    const message = await screen.findByRole("status");
    expect(message).toHaveTextContent(/no disponible \(error al consultar el servidor\)/);
    expect(within(message).queryByText(/Fase 5/)).not.toBeInTheDocument();
  });
});
