import { type QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

// Capa de datos (Subfase D, plan pre-Fase 5 aprobado 2026-10-14): un solo
// punto de paso (apiFetch) para la cookie httpOnly (credentials: "include")
// y la reaccion a un 401 -- nunca se guarda nada sensible en el cliente.

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  onUnauthorized = fn;
}

class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, credentials: "include" });
  if (response.status === 401) {
    onUnauthorized?.();
    throw new ApiError(401, "No autenticado");
  }
  if (!response.ok) {
    throw new ApiError(response.status, `${path}: HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

// --- tipos: reflejan EXACTAMENTE las formas que devuelven los endpoints --------

export interface HealthResponse {
  status: "ok" | "stale";
  mode: string;
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
