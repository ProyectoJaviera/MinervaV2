import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { UniverseTab } from "./UniverseTab";
import { mockFetch, withQueryClient } from "./testUtils";

const ATTRIBUTION = { text: "Data provided by CoinGecko", url: "https://www.coingecko.com" };

const UNIVERSE = {
  refreshed_at: "2026-10-18T11:12:13+00:00", age_hours: 2.5, stale: false,
  staleness_limit_hours: 48, refresh_hours: 24, universe_size: 10, last_refresh_error: null,
  included: ["BTCUSDT", "ETHUSDT"],
  candidates: [
    {
      rank: 1, coingecko_id: "bitcoin", symbol: "BTCUSDT", market_cap_usd: 1648722303443,
      included: true, excluded_category: null, excluded_manual: false, has_bitunix_perp: true,
      price_sanity_ok: true, exclusion_reason: null, mark_price: 64000.5,
    },
    {
      rank: 2, coingecko_id: "tether", symbol: "USDTUSDT", market_cap_usd: 183480179936,
      included: false, excluded_category: "stablecoins", excluded_manual: false,
      has_bitunix_perp: false, price_sanity_ok: null,
      exclusion_reason: "Categoria excluida: stablecoins", mark_price: null,
    },
    {
      rank: 16, coingecko_id: "chainlink", symbol: "LINKUSDT", market_cap_usd: 9200309345,
      included: false, excluded_category: null, excluded_manual: false, has_bitunix_perp: true,
      price_sanity_ok: true, exclusion_reason: "Fuera del tamano del universo (10 activos)",
      mark_price: null,
    },
  ],
  changes: [{ at: "2026-10-14T11:36:24+00:00", added: ["XMRUSDT"], removed: ["LINKUSDT"] }],
  rule: "Un activo que sale del universo con una posicion abierta NO se cierra por la fuerza.",
  attribution: ATTRIBUTION,
};

const VERDICTS = {
  note: "Backtest historico de la Fase 2, no la medicion en vivo del LLM.",
  eligible_strategies: ["ema_cross_9_21", "funding_contrarian_experimental"],
  all_strategies_eligible: false,
  run_at: "2026-10-01T23:06:32+00:00",
  verdicts: [
    {
      strategy: "ema_cross_9_21", experimental: false, discarded: true, evidence_insufficient: false,
      eligible_for_account: true,
      reasons: ["PF OOS agregado 1.12 < minimo 1.2", "Drawdown OOS 85.0% > 50.0%"],
      pf_oos_aggregate: 1.1233, pf_stressed: 1.0523, pct_symbols_pf_gt1: 50,
      pct_folds_positive: 70.37, max_drawdown_oos_pct: 85.04, concentration_pct: 65.73,
      combos_tested: 10, total_trades_all_segments: 556, run_at: "2026-10-01T23:05:02+00:00",
    },
    {
      strategy: "donchian_breakout_20", experimental: false, discarded: true,
      evidence_insufficient: false, eligible_for_account: false, reasons: [],
      pf_oos_aggregate: 0.9033, pf_stressed: 0.829, pct_symbols_pf_gt1: 40,
      pct_folds_positive: 62.07, max_drawdown_oos_pct: 163.1, concentration_pct: null,
      combos_tested: 20, total_trades_all_segments: 900, run_at: "2026-10-01T23:06:04+00:00",
    },
    {
      strategy: "funding_contrarian_experimental", experimental: true, discarded: false,
      evidence_insufficient: false, eligible_for_account: true, reasons: [],
      pf_oos_aggregate: 1.4346, pf_stressed: 1.3423, pct_symbols_pf_gt1: 75,
      pct_folds_positive: 50, max_drawdown_oos_pct: 15.38, concentration_pct: 116.8,
      combos_tested: 10, total_trades_all_segments: 300, run_at: "2026-10-01T23:06:20+00:00",
    },
  ],
};

function mount(
  universe: { status: number; body: unknown },
  verdicts: { status: number; body: unknown } = { status: 200, body: VERDICTS }
) {
  vi.stubGlobal(
    "fetch",
    mockFetch({ "/api/universe": universe, "/api/backtest/verdicts": verdicts })
  );
  return render(withQueryClient(<UniverseTab />));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("UniverseTab: universo vigente", () => {
  it("muestra los candidatos con su estado y el motivo de cada exclusion", async () => {
    mount({ status: 200, body: UNIVERSE });

    await screen.findByText("BTCUSDT");
    const btc = screen.getByText("BTCUSDT").closest("tr") as HTMLElement;
    expect(within(btc).getByText("INCLUIDO")).toBeInTheDocument();
    expect(within(btc).getByText("1.6T")).toBeInTheDocument();
    expect(within(btc).getByText("64000.5")).toBeInTheDocument();
    const tether = screen.getByText("USDTUSDT").closest("tr") as HTMLElement;
    expect(within(tether).getByText("excluido")).toBeInTheDocument();
    expect(within(tether).getByText("Categoria excluida: stablecoins")).toBeInTheDocument();
    expect(screen.getByText(/Fuera del tamano del universo \(10 activos\)/)).toBeInTheDocument();
    expect(screen.getByText("2 de 10")).toBeInTheDocument();
  });

  it("lleva la atribucion de CoinGecko enlazada, justo debajo de la tabla", async () => {
    mount({ status: 200, body: UNIVERSE });

    await screen.findByText("BTCUSDT");
    const link = screen.getByRole("link", { name: "Data provided by CoinGecko" });
    expect(link).toHaveAttribute("href", "https://www.coingecko.com");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
    const table = screen.getAllByRole("table")[0];
    // la atribucion viene DESPUES de la tabla de candidatos y antes de los cambios recientes
    expect(table.compareDocumentPosition(link) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(
      link.compareDocumentPosition(screen.getByText("Cambios recientes")) &
        Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();
  });

  it("lista los cambios recientes y la regla del activo que sale", async () => {
    mount({ status: 200, body: UNIVERSE });

    expect(await screen.findByText(/entran XMRUSDT; salen LINKUSDT/)).toBeInTheDocument();
    expect(screen.getByText(/NO se cierra por la fuerza/)).toBeInTheDocument();
  });

  it("avisa cuando el universo esta obsoleto y cuando fallo el ultimo refresco", async () => {
    mount({
      status: 200,
      body: { ...UNIVERSE, stale: true, age_hours: 60, last_refresh_error: "2026 CoinGeckoApiError" },
    });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/Universo OBSOLETO \(mas de 48 h/);
    expect(alert).toHaveTextContent(/no produce senales/);
    expect(screen.getByText(/Ultimo refresco fallido: 2026 CoinGeckoApiError/)).toBeInTheDocument();
  });

  it("sin cambios ni snapshot lo dice", async () => {
    mount({
      status: 200,
      body: {
        ...UNIVERSE, refreshed_at: null, age_hours: null, stale: true, included: [],
        candidates: [], changes: [],
      },
    });

    expect(await screen.findByText("Sin snapshot todavia.")).toBeInTheDocument();
    expect(screen.getByText("Sin cambios en los ultimos refrescos.")).toBeInTheDocument();
    expect(screen.getByText("sin snapshot")).toBeInTheDocument();
  });

  it("si el bot en ejecucion aun no tiene la ruta (404) lo dice, y los veredictos siguen", async () => {
    mount({ status: 404, body: {} });

    const notices = await screen.findAllByRole("status");
    expect(notices[0]).toHaveTextContent(/Universo: no disponible todavia/);
    expect((await screen.findAllByText(/DESCARTADA/)).length).toBeGreaterThanOrEqual(1);
  });
});

describe("UniverseTab: veredictos de backtest por estrategia", () => {
  it("distingue descartada, pasa y experimental, con los motivos y la elegibilidad", async () => {
    mount({ status: 200, body: UNIVERSE });

    await screen.findByText("funding_contrarian_experimental");
    const ema = screen.getByText("ema_cross_9_21").closest("tr") as HTMLElement;
    expect(within(ema).getByText(/DESCARTADA/)).toBeInTheDocument();
    expect(within(ema).getByText("PF OOS agregado 1.12 < minimo 1.2")).toBeInTheDocument();
    expect(within(ema).getByText("elegible")).toBeInTheDocument();
    expect(within(ema).getByText("1.12")).toBeInTheDocument();
    expect(within(ema).getByText("70.4%")).toBeInTheDocument();
    expect(within(ema).getByText("556")).toBeInTheDocument();
    const donchian = screen.getByText("donchian_breakout_20").closest("tr") as HTMLElement;
    expect(within(donchian).getByText("solo sombra")).toBeInTheDocument();
    expect(within(donchian).getAllByText("-")).toHaveLength(1); // concentracion sin dato
    const funding = screen.getByText("funding_contrarian_experimental").closest("tr") as HTMLElement;
    expect(within(funding).getByText("pasa")).toBeInTheDocument();
    expect(within(funding).getByText("experimental")).toBeInTheDocument();
  });

  it("aclara que es un backtest historico y no la medicion en vivo", async () => {
    mount({ status: 200, body: UNIVERSE });

    expect(await screen.findByText(/no la medicion en vivo del LLM/)).toBeInTheDocument();
    expect(screen.getByText(/Corrida del /)).toBeInTheDocument();
    expect(
      screen.getByText(/Estrategias elegibles para la cuenta paper: ema_cross_9_21, funding/)
    ).toBeInTheDocument();
  });

  it("con todas las estrategias elegibles lo dice", async () => {
    mount(
      { status: 200, body: UNIVERSE },
      { status: 200, body: { ...VERDICTS, all_strategies_eligible: true, eligible_strategies: [] } }
    );

    expect(
      await screen.findByText(/Estrategias elegibles para la cuenta paper: todas/)
    ).toBeInTheDocument();
  });

  it("sin veredictos en la base dice que no hay", async () => {
    mount({ status: 200, body: UNIVERSE }, { status: 200, body: { ...VERDICTS, verdicts: [], run_at: null } });

    expect(await screen.findByText("Sin veredictos de backtest en la base.")).toBeInTheDocument();
  });

  it("un error en los veredictos no rompe el universo", async () => {
    mount({ status: 200, body: UNIVERSE }, { status: 500, body: {} });

    expect(await screen.findByText("BTCUSDT")).toBeInTheDocument();
    expect(await screen.findByRole("status")).toHaveTextContent(
      /Veredictos de backtest: no disponible \(error al consultar el servidor\)/
    );
  });
});
