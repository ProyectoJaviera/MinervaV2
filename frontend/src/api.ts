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
  gate_open: boolean;
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
