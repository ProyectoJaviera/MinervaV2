import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ArmTab } from "./ArmTab";
import { mockFetch, withQueryClient } from "./testUtils";

const ARM = {
  status: {
    enabled: true, active: true, armed_since: "2026-10-17T23:12:13+00:00",
    opened_today: 1, skipped_today: 4,
  },
  config: {
    margin_usdt: 5, leverage: 10, max_decision_delay_s: 60,
    eligible_strategies: ["ema_cross_9_21", "funding_contrarian_experimental"],
  },
  totals: { opened: 1, skipped: 5, pending: 0 },
  skipped_by_reason: { RISK_STRATEGY_NOT_ELIGIBLE: 3, BELOW_MIN_ORDER: 2 },
  reason_legend: {
    RISK_STRATEGY_NOT_ELIGIBLE: "Riesgo: la estrategia no esta en la lista de elegibles.",
    BELOW_MIN_ORDER: "El tamano no alcanza el minimo operable del contrato.",
  },
  entries: [
    {
      id: 3, created_at: "2026-10-18T11:00:20+00:00", resolved_at: "2026-10-18T11:00:28+00:00",
      symbol: "TRXUSDT", side: "LONG", strategy: "ema_cross_9_21", decision_delay_s: 21.4,
      outcome: "SKIPPED", reason: "BELOW_MIN_ORDER", trade_id: null,
      details: { qty: 148.9, min_trade_volume: 150 },
    },
    {
      id: 2, created_at: "2026-10-18T07:00:20+00:00", resolved_at: "2026-10-18T07:00:31+00:00",
      symbol: "BTCUSDT", side: "SHORT", strategy: "funding_contrarian_experimental",
      decision_delay_s: 18, outcome: "OPENED", reason: null, trade_id: 7, details: {},
    },
    {
      id: 1, created_at: "2026-10-18T06:00:20+00:00", resolved_at: null,
      symbol: "ETHUSDT", side: "LONG", strategy: null, decision_delay_s: 30,
      outcome: "PENDING", reason: null, trade_id: null, details: {},
    },
  ],
};

function mount(arm: { status: number; body: unknown }) {
  vi.stubGlobal("fetch", mockFetch({ "/api/arm": arm }));
  return render(withQueryClient(<ArmTab />));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ArmTab: registro del brazo ejecutado", () => {
  it("muestra el estado, los conteos y la configuracion del brazo", async () => {
    mount({ status: 200, body: ARM });

    expect(await screen.findByText("activo")).toBeInTheDocument();
    expect(screen.getByText("5 USDT x10")).toBeInTheDocument();
    expect(screen.getByText("60 s")).toBeInTheDocument();
    expect(screen.getByText(/ema_cross_9_21, funding_contrarian_experimental/)).toBeInTheDocument();
    expect(screen.getByText("Abiertas hoy").nextSibling).toHaveTextContent("1");
    expect(screen.getByText("Omitidas hoy").nextSibling).toHaveTextContent("4");
    expect(screen.getByText("Abiertas (total)").nextSibling).toHaveTextContent("1");
    expect(screen.getByText("Omitidas (total)").nextSibling).toHaveTextContent("5");
  });

  it("lista las omitidas por motivo con su explicacion", async () => {
    mount({ status: 200, body: ARM });

    await screen.findByText("Omitidas por motivo");
    const card = screen.getByText("Omitidas por motivo").closest("section") as HTMLElement;
    expect(within(card).getByText("RISK_STRATEGY_NOT_ELIGIBLE")).toBeInTheDocument();
    expect(within(card).getByText(/la estrategia no esta en la lista de elegibles/)).toBeInTheDocument();
    expect(within(card).getByText(/minimo operable del contrato/)).toBeInTheDocument();
  });

  it("la tabla distingue abierta, omitida y en curso, con el motivo y la posicion", async () => {
    mount({ status: 200, body: ARM });

    await screen.findByText("TRXUSDT LONG");
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    expect(within(rows[0]).getByText("OMITIDA")).toBeInTheDocument();
    expect(within(rows[0]).getByText("BELOW_MIN_ORDER")).toBeInTheDocument();
    expect(within(rows[0]).getByText("21.4 s")).toBeInTheDocument();
    expect(within(rows[1]).getByText("ABIERTA")).toBeInTheDocument();
    expect(within(rows[1]).getByText("#7")).toBeInTheDocument();
    expect(within(rows[2]).getByText("EN CURSO")).toBeInTheDocument();
    // sin estrategia ni motivo ni posicion todavia: tres guiones, nada inventado
    expect(within(rows[2]).getAllByText("-")).toHaveLength(3);
  });

  it("nunca muestra PnL ni resultados", async () => {
    mount({ status: 200, body: ARM });

    await screen.findByText("TRXUSDT LONG");
    const page = document.body.textContent ?? "";
    expect(page).not.toMatch(/PnL (neto|realizado)/);
    expect(screen.getByText(/Sin PnL\./)).toBeInTheDocument();
  });

  it("con el brazo apagado lo dice y no inventa filas", async () => {
    mount({
      status: 200,
      body: {
        ...ARM,
        status: { enabled: false, active: false, armed_since: null, opened_today: 0, skipped_today: 0 },
        totals: { opened: 0, skipped: 0, pending: 0 },
        skipped_by_reason: {},
        entries: [],
      },
    });

    expect(await screen.findByText("apagado")).toBeInTheDocument();
    expect(screen.getByText("Ninguna omitida todavia.")).toBeInTheDocument();
    expect(screen.getByText("Sin decisiones todavia.")).toBeInTheDocument();
  });

  it("habilitado pero sin armar se distingue de apagado", async () => {
    mount({
      status: 200,
      body: { ...ARM, status: { ...ARM.status, active: false } },
    });

    expect(await screen.findByText("sin armar")).toBeInTheDocument();
  });

  it("sin estrategias elegibles configuradas dice que son todas", async () => {
    mount({ status: 200, body: { ...ARM, config: { ...ARM.config, eligible_strategies: [] } } });

    expect(await screen.findByText(/Estrategias elegibles para la cuenta: todas/)).toBeInTheDocument();
  });

  it("si el bot en ejecucion aun no tiene la ruta (404) lo dice", async () => {
    mount({ status: 404, body: { detail: "Not Found" } });

    expect(await screen.findByRole("status")).toHaveTextContent(
      /Brazo ejecutado: no disponible todavia/
    );
  });
});
