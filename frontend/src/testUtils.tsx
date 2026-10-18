import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { vi } from "vitest";

export function withQueryClient(element: ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });
  return <QueryClientProvider client={queryClient}>{element}</QueryClientProvider>;
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export type MockRoute =
  | { status: number; body: unknown }
  | ((init?: RequestInit) => { status: number; body: unknown });

// `fetch` falso que enruta por metodo y ruta: la clave es "/ruta" (GET) o "POST /ruta". Una ruta no
// mockeada lanza (la consulta falla, como un servidor sin esa funcion). Se puede inspeccionar con
// `mock.calls` para ver que se pidio y con que cuerpo.
export function mockFetch(routes: Record<string, MockRoute>) {
  return vi.fn((input: string | URL | Request, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const path = url.split("?")[0];
    const method = (init?.method ?? "GET").toUpperCase();
    const route = routes[`${method} ${path}`] ?? (method === "GET" ? routes[path] : undefined);
    if (!route) throw new Error(`ruta no mockeada en el test: ${method} ${path}`);
    const { status, body } = typeof route === "function" ? route(init) : route;
    return Promise.resolve(jsonResponse(body, status));
  });
}

export function calledPaths(fetchMock: ReturnType<typeof mockFetch>, method = "GET"): string[] {
  return fetchMock.mock.calls
    .filter(([, init]) => ((init as RequestInit | undefined)?.method ?? "GET").toUpperCase() === method)
    .map(([input]) => String(input).split("?")[0]);
}
