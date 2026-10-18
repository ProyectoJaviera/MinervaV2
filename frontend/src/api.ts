import { type QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

// Capa de datos (Subfase D, plan pre-Fase 5 aprobado 2026-10-14): un solo
// punto de paso (apiFetch) para la cookie httpOnly (credentials: "include")
// y la reaccion a un 401 -- nunca se guarda nada sensible en el cliente.

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  onUnauthorized = fn;
}

export class ApiError extends Error {
  status: number;
  // Texto `detail` que manda el servidor en un error propio (p.ej. "escribe exactamente PAUSAR").
  detail: string | null;
  constructor(status: number, message: string, detail: string | null = null) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, credentials: "include" });
  if (response.status === 401) {
    onUnauthorized?.();
    throw new ApiError(401, "No autenticado");
  }
  if (!response.ok) {
    let detail: string | null = null;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // cuerpo vacio o no JSON: sin detalle
    }
    throw new ApiError(response.status, `${path}: HTTP ${response.status}`, detail);
  }
  return response.json() as Promise<T>;
}

function apiPost<T>(path: string, body: unknown): Promise<T> {
  return apiFetch<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// --- tipos: reflejan EXACTAMENTE las formas que devuelven los endpoints --------

export interface HealthResponse {
  status: "ok" | "stale";
  mode: string;
  // Pausa del scheduler (DETIENE la medicion) y kill switch. Opcional: un backend viejo no lo manda.
  control?: { paused: boolean; kill_switch_active: boolean };
  // Notificaciones push de Telegram (Fase 5): solo conteos, nunca el token ni el texto.
  telegram_push?: {
    enabled: boolean;
    active: boolean;
    sent_total: number;
    failed_total: number;
    dropped_total: number;
    consecutive_failures: number;
    last_sent_at: string | null;
    last_error: string | null;
  };
  last_beat_at: string | null;
  last_cycle_at: string | null;
  log_dropped_count: number;
  llm_labeling: {
    enabled: boolean;
    frozen_hash_ok: boolean;
    labeled_today: number;
    failed_today: number;
    budget_exceeded_today: number;
    budget_exhausted_at: string | null;
    cost_today_usd: number;
  };
}

export interface MeasurementStatus {
  frozen_hash: string;
  total_medicion_rows: number;
  ok_by_label: { APROBADA: number; RECHAZADA: number };
  total_ok: number;
  approval_rate: number | null;
  within_band: boolean | null;
  non_ok_counts: Record<string, number>;
  json_repaired_count: number;
  total_cost_usd: number;
  cost_per_row_usd: number | null;
  omitted_without_row: number;
  first_medicion_row_at: string | null;
  data_now: string | null;
  n_eff_approved: number;
  n_eff_rejected: number;
  shortfall_approved: number;
  shortfall_rejected: number;
  window_days: number;
  rate_approved_per_day: number | null;
  rate_rejected_per_day: number | null;
  days_needed_approved: number | null;
  days_needed_rejected: number | null;
  // Ritmo sobre tiempo EN MARCHA (solo el tiempo evaluando senales; un hueco mayor a
  // `offline_gap_hours` sin ciclos cuenta como apagado). Los campos de arriba son de
  // CALENDARIO (incluyen el tiempo apagado). Opcionales: un backend viejo no los manda;
  // null = sin marcas de evaluacion. Los porcentajes son fracciones 0-1. Sin PnL.
  offline_gap_hours?: number;
  running_window_days?: number | null;
  uptime_pct_window?: number | null;
  uptime_pct_total?: number | null;
  rate_approved_running_per_day?: number | null;
  rate_rejected_running_per_day?: number | null;
  days_needed_running_approved?: number | null;
  days_needed_running_rejected?: number | null;
  gate_open: boolean;
  // Gate abierto Y registro oficial del veredicto (docs/VEREDICTO_3_6_REGISTRO*.txt):
  // lo unico que habilita mostrar resultados de las sombras. Con gate_open=true y esto
  // en false hay que seguir docs/VEREDICTO_3_6.md (placebo oficial y veredicto).
  outcome_reveal_allowed: boolean;
}

export interface LlmDecisionEntry {
  signal_group_key: string;
  fase: string;
  status: string;
  decision: "APROBAR" | "RECHAZAR" | null;
  confianza: number | null;
  razonamiento: string | null;
  created_at: string;
  candle_close_time: string;
  decision_delay_s: number;
  cost_usd: number;
  latency_ms: number | null;
  json_repaired: boolean;
  error: string | null;
  symbol: string | null;
  side: "LONG" | "SHORT" | null;
}

export interface ShadowTradeEntry {
  id: number;
  symbol: string;
  side: "LONG" | "SHORT";
  strategy: string | null;
  status: "OPEN" | "CLOSED";
  leverage: number;
  margin_usdt: number;
  notional_usdt: number;
  qty: number;
  entry_price: number;
  opened_at: string;
  sl_price: number | null;
  tp_price: number | null;
  liq_price: number | null;
  sl_margin_loss_pct: number | null;
  contributing_strategies: string[];
  signal_group_key: string;
  candle_close_time: string | null;
  llm_decision: string;
  executed_in_real_account: boolean;
  // Presentes SOLO si la medicion ya junto MIN_EFFECTIVE_N por lado -- nunca
  // se infieren ni se calculan en el cliente si faltan (ver DashboardView).
  pnl_gross_usdt?: number | null;
  pnl_net_usdt?: number | null;
  exit_price?: number | null;
  close_reason?: string | null;
  closed_at?: string | null;
}

// --- Fase 4: noticias y memoria de lecciones (docs/FASE4_PLAN.md) ----------------

export interface NewsSourceHealth {
  last_success_at: string | null;
  consecutive_failures: number;
  last_error: string | null;
}

export interface NewsStatus {
  enabled: boolean;
  active: boolean;
  sources: Record<string, NewsSourceHealth>;
  items_24h: number;
  // Textos de atribucion de las fuentes: el dashboard DEBE mostrarlos junto a los datos.
  attribution: string[];
  fear_greed: {
    value: number;
    classification: string | null;
    ts: string;
    age_hours: number;
  } | null;
}

// Titular de un tercero: texto NO confiable. Se muestra siempre como texto (React lo escapa) y el
// enlace solo se usa si es http(s).
export interface NewsItem {
  id: number;
  source: string;
  title: string;
  domain: string | null;
  url: string | null;
  published_at: string | null;
  fetched_at: string;
  assets: string[];
  sentiment: number | null;
  sentiment_method: string | null;
}

export interface NewsRecent {
  items: NewsItem[];
  attribution: string[];
}

// Solo conteos y el interruptor: nunca tasas ni retornos.
export interface MemoryStatus {
  enabled: boolean;
  active: boolean;
  min_sample: number;
  closed_reliable: number;
  entries: number;
  applications: number;
  last_build_id: string | null;
  results_visible: boolean;
  message: string | null;
}

export interface LessonEntry {
  dimension: string;
  pattern: string;
  n: number;
  wins: number;
  winrate: number | null;
  ci_low: number | null;
  ci_high: number | null;
  base_winrate: number | null;
  mean_return_pct: number | null;
  half1_delta: number | null;
  half2_delta: number | null;
  status: string;
}

export interface MemoryLessons {
  results_visible: boolean;
  message?: string;
  lessons: LessonEntry[];
  build_id?: string | null;
  min_sample?: number;
  disclaimer?: string;
}

// --- Fase 5: cuenta paper, brazo, riesgo, universo, backtest y controles (docs/FASE5_PLAN.md) ---

// Posicion de la cuenta paper. Los campos de RESULTADO (opcionales) solo viajan si el backend dice
// `reveal_allowed`; el cliente nunca los calcula ni los muestra por su cuenta.
export interface AccountTrade {
  id: number;
  symbol: string;
  side: "LONG" | "SHORT";
  strategy: string | null;
  status: "OPEN" | "CLOSED";
  leverage: number;
  margin_usdt: number;
  notional_usdt: number;
  qty: number;
  entry_price: number;
  opened_at: string;
  closed_at: string | null;
  sl_price: number | null;
  tp_price: number | null;
  trailing_distance: number | null;
  liq_price: number | null;
  sl_margin_loss_pct: number | null;
  decision_source: string | null;
  pnl_gross_usdt?: number | null;
  pnl_net_usdt?: number | null;
  exit_price?: number | null;
  close_reason?: string | null;
  fee_entry_usdt?: number;
  fee_exit_usdt?: number | null;
  funding_paid_usdt?: number;
  slippage_entry_usdt?: number;
  slippage_exit_usdt?: number;
  best_price?: number | null;
  effective_stop?: number | null;
  fill_source?: string | null;
}

export interface AccountResults {
  balance_usdt: number;
  realized_pnl_usdt: number;
  unrealized_pnl_usdt: number | null;
  equity_usdt: number | null;
  marks_missing: string[];
  closed: number;
  wins: number;
  losses: number;
  profit_factor: number | null;
}

export interface AccountResponse {
  reveal_allowed: boolean;
  message: string | null;
  initial_capital_usdt: number;
  summary: {
    open: number;
    closed: number;
    max_simultaneous_positions: number;
    margin_in_use_usdt: number;
  };
  open: AccountTrade[];
  closed: AccountTrade[];
  results: AccountResults | null;
}

export interface ArmEntry {
  id: number;
  created_at: string;
  resolved_at: string | null;
  symbol: string;
  side: "LONG" | "SHORT";
  strategy: string | null;
  decision_delay_s: number;
  outcome: "OPENED" | "SKIPPED" | "PENDING";
  reason: string | null;
  trade_id: number | null;
  details: Record<string, unknown>;
}

export interface ArmResponse {
  status: {
    enabled: boolean;
    active: boolean;
    armed_since: string | null;
    opened_today: number;
    skipped_today: number;
  };
  config: {
    margin_usdt: number;
    leverage: number;
    max_decision_delay_s: number;
    eligible_strategies: string[];
  };
  totals: { opened: number; skipped: number; pending: number };
  skipped_by_reason: Record<string, number>;
  reason_legend: Record<string, string>;
  entries: ArmEntry[];
}

export interface RiskResponse {
  reveal_allowed: boolean;
  message: string | null;
  paused: boolean;
  kill_switch_active: boolean;
  measurement_stopped: boolean;
  entries_blocked_by: string[];
  circuit_breaker: {
    active: boolean;
    until: string | null;
    consecutive_losses_threshold: number;
    cooldown_hours: number;
  };
  drawdown_stop: { active: boolean; mode: string; max_drawdown_fraction: number };
  daily_loss: { locked: boolean; max_daily_loss_fraction: number };
  positions: { open: number; max: number; long: number; short: number; max_same_direction: number };
  scheduler: { last_cycle_at: string | null; stale: boolean; stale_after_minutes: number };
  // Derivados de RESULTADOS: solo con `reveal_allowed`.
  numbers: { consecutive_losses: number; drawdown_trigger_count: number } | null;
  rejections: {
    id: number;
    created_at: string;
    symbol: string;
    side: "LONG" | "SHORT";
    strategy: string | null;
    reason: string;
  }[];
  rejections_today_by_reason: Record<string, number>;
}

export interface UniverseCandidate {
  rank: number | null;
  coingecko_id: string;
  symbol: string;
  market_cap_usd: number | null;
  included: boolean;
  excluded_category: string | null;
  excluded_manual: boolean;
  has_bitunix_perp: boolean;
  price_sanity_ok: boolean | null;
  exclusion_reason: string | null;
  mark_price: number | null;
}

export interface UniverseResponse {
  refreshed_at: string | null;
  age_hours: number | null;
  stale: boolean;
  staleness_limit_hours: number;
  refresh_hours: number;
  universe_size: number;
  last_refresh_error: string | null;
  included: string[];
  candidates: UniverseCandidate[];
  changes: { at: string; added: string[]; removed: string[] }[];
  rule: string;
  attribution: { text: string; url: string };
}

export interface BacktestVerdictEntry {
  strategy: string;
  experimental: boolean;
  discarded: boolean;
  evidence_insufficient: boolean;
  eligible_for_account: boolean;
  reasons: string[];
  pf_oos_aggregate: number | null;
  pf_stressed: number | null;
  pct_symbols_pf_gt1: number | null;
  pct_folds_positive: number | null;
  max_drawdown_oos_pct: number | null;
  concentration_pct: number | null;
  combos_tested: number;
  total_trades_all_segments: number;
  run_at: string;
}

export interface BacktestResponse {
  note: string;
  eligible_strategies: string[];
  all_strategies_eligible: boolean;
  run_at: string | null;
  verdicts: BacktestVerdictEntry[];
}

export type ControlAction = "pause" | "resume" | "kill";

export interface ControlInfo {
  enabled: boolean;
  state: { paused: boolean; kill_switch: boolean };
  actions: Record<
    ControlAction,
    { label: string; confirm_phrase: string; warning: string; stops_measurement: boolean }
  >;
}

export interface ControlResult {
  ok: boolean;
  action: ControlAction;
  changed: boolean;
  before: { paused: boolean; kill_switch: boolean };
  after: { paused: boolean; kill_switch: boolean };
  actor: string;
  audit_id: number;
  at: string;
  drawdown_stop_active: boolean;
  message: string;
}

export interface ControlAuditEntry {
  id: number;
  created_at: string;
  channel: "DASHBOARD" | "TELEGRAM";
  actor: string;
  action: ControlAction;
  paused_before: boolean;
  kill_before: boolean;
  paused_after: boolean;
  kill_after: boolean;
  changed: boolean;
}

export interface MeResponse {
  username: string;
}

// --- auth ------------------------------------------------------------------

// `null` = sesion cerrada/vencida (confirmada). Ante un 401 la cache de "me"
// se escribe a mano con `clearSession` (sin refetch): un error de React Query
// conserva el ultimo `data` valido, asi que depender del error dejaria la UI
// con la sesion vieja.
export function useMe() {
  return useQuery<MeResponse | null>({
    queryKey: ["me"],
    queryFn: () => apiFetch<MeResponse>("/api/auth/me"),
    retry: false,
    refetchInterval: false,
  });
}

export function clearSession(queryClient: QueryClient): void {
  queryClient.setQueryData<MeResponse | null>(["me"], null);
  queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== "me" });
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (credentials: { username: string; password: string }) =>
      apiFetch<{ ok: boolean }>("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(credentials),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["me"] });
    },
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
    onSuccess: () => clearSession(queryClient),
  });
}

// --- datos del dashboard -----------------------------------------------------

// Las 4 queries de abajo solo se llaman desde DashboardView, que App.tsx
// nunca monta hasta confirmar la sesion -- no necesitan su propio "enabled".

export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: () => apiFetch<HealthResponse>("/health"),
  });
}

export function useMeasurementStatus() {
  return useQuery({
    queryKey: ["measurement-status"],
    queryFn: () => apiFetch<MeasurementStatus>("/api/measurement/status"),
  });
}

export function useRecentDecisions(limit = 20) {
  return useQuery({
    queryKey: ["llm-decisions-recent", limit],
    queryFn: () => apiFetch<LlmDecisionEntry[]>(`/api/llm/decisions/recent?limit=${limit}`),
  });
}

export function useRecentShadows(limit = 20) {
  return useQuery({
    queryKey: ["shadow-trades-recent", limit],
    queryFn: () => apiFetch<ShadowTradeEntry[]>(`/api/shadow-trades/recent?limit=${limit}`),
  });
}

export function useNewsStatus() {
  return useQuery({
    queryKey: ["news-status"],
    queryFn: () => apiFetch<NewsStatus>("/api/news/status"),
  });
}

export function useNewsRecent(limit = 8) {
  return useQuery({
    queryKey: ["news-recent", limit],
    queryFn: () => apiFetch<NewsRecent>(`/api/news/recent?limit=${limit}`),
  });
}

export function useMemoryStatus() {
  return useQuery({
    queryKey: ["memory-status"],
    queryFn: () => apiFetch<MemoryStatus>("/api/memory/status"),
  });
}

// Solo se pide con `enabled`, y el llamador lo pone en true unicamente cuando el backend dijo,
// explicitamente, que los resultados ya son visibles (falla cerrado).
export function useMemoryLessons(enabled: boolean) {
  return useQuery({
    queryKey: ["memory-lessons"],
    queryFn: () => apiFetch<MemoryLessons>("/api/memory/lessons"),
    enabled,
  });
}

// --- Fase 5 ----------------------------------------------------------------------------------

export function useAccount(limit = 100) {
  return useQuery({
    queryKey: ["account", limit],
    queryFn: () => apiFetch<AccountResponse>(`/api/account?limit=${limit}`),
  });
}

export function useArm(limit = 50) {
  return useQuery({
    queryKey: ["arm", limit],
    queryFn: () => apiFetch<ArmResponse>(`/api/arm?limit=${limit}`),
  });
}

export function useRisk() {
  return useQuery({
    queryKey: ["risk"],
    queryFn: () => apiFetch<RiskResponse>("/api/risk"),
  });
}

export function useUniverse() {
  return useQuery({
    queryKey: ["universe"],
    queryFn: () => apiFetch<UniverseResponse>("/api/universe"),
  });
}

export function useBacktestVerdicts() {
  return useQuery({
    queryKey: ["backtest-verdicts"],
    queryFn: () => apiFetch<BacktestResponse>("/api/backtest/verdicts"),
  });
}

export function useControlInfo() {
  return useQuery({
    queryKey: ["control-info"],
    queryFn: () => apiFetch<ControlInfo>("/api/control/info"),
  });
}

export function useControlAudit(limit = 20) {
  return useQuery({
    queryKey: ["control-audit", limit],
    queryFn: () =>
      apiFetch<{ entries: ControlAuditEntry[] }>(`/api/control/audit?limit=${limit}`),
  });
}

// Ejecuta pausar / reanudar / kill switch. `confirm` es la palabra exacta que pide el servidor.
export function useControlAction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ action, confirm }: { action: ControlAction; confirm: string }) =>
      apiPost<ControlResult>(`/api/control/${action}`, { confirm }),
    onSuccess: () => {
      for (const key of ["risk", "health", "control-info", "control-audit"]) {
        queryClient.invalidateQueries({ queryKey: [key] });
      }
    },
  });
}
