import { useEffect, useState } from "react";

// Pantalla minima de Fase 1: solo lista posiciones abiertas y el historial
// de operaciones via polling. Sin autenticacion, sin WebSocket, sin
// controles todavia -- el dashboard completo llega en Fase 5.

interface Trade {
  id: number;
  symbol: string;
  side: "LONG" | "SHORT";
  status: "OPEN" | "CLOSED";
  strategy: string | null;
  entry_price: number;
  exit_price: number | null;
  qty: number;
  margin_usdt: number;
  pnl_net_usdt: number | null;
  opened_at: string;
  closed_at: string | null;
}

const POLL_MS = 5000;

function useFetchList(path: string): Trade[] {
  const [data, setData] = useState<Trade[]>([]);

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      try {
        const res = await fetch(path);
        if (!res.ok) return;
        const json = await res.json();
        if (!cancelled) setData(json);
      } catch {
        // red/servidor caido: se reintenta en el siguiente ciclo, sin romper la UI
      }
    }
    poll();
    const interval = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [path]);

  return data;
}

function TradeTable({ trades, title }: { trades: Trade[]; title: string }) {
  return (
    <section style={{ marginBottom: 32 }}>
      <h2>{title}</h2>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ textAlign: "left", borderBottom: "1px solid #444" }}>
            <th>Simbolo</th>
            <th>Lado</th>
            <th>Estrategia</th>
            <th>Entrada</th>
            <th>Salida</th>
            <th>Qty</th>
            <th>Margen</th>
            <th>PnL neto</th>
            <th>Abierta</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t) => (
            <tr key={t.id} style={{ borderBottom: "1px solid #222" }}>
              <td>{t.symbol}</td>
              <td>{t.side}</td>
              <td>{t.strategy ?? "-"}</td>
              <td>{t.entry_price.toFixed(2)}</td>
              <td>{t.exit_price?.toFixed(2) ?? "-"}</td>
              <td>{t.qty.toFixed(6)}</td>
              <td>{t.margin_usdt.toFixed(2)}</td>
              <td>{t.pnl_net_usdt?.toFixed(4) ?? "-"}</td>
              <td>{new Date(t.opened_at).toLocaleString()}</td>
            </tr>
          ))}
          {trades.length === 0 && (
            <tr>
              <td colSpan={9} style={{ opacity: 0.6, padding: "8px 0" }}>
                Sin datos todavia.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </section>
  );
}

export default function App() {
  const positions = useFetchList("/positions");
  const trades = useFetchList("/trades");

  return (
    <main style={{ fontFamily: "sans-serif", padding: 24, maxWidth: 1000, margin: "0 auto" }}>
      <div
        style={{
          background: "#7a1f1f",
          color: "white",
          padding: "8px 16px",
          borderRadius: 4,
          marginBottom: 24,
          fontWeight: "bold",
        }}
      >
        MODO PAPER TRADING -- ninguna orden real se envia a Bitunix
      </div>
      <h1>Minerva</h1>
      <TradeTable trades={positions} title="Posiciones abiertas" />
      <TradeTable trades={trades} title="Historial de operaciones" />
    </main>
  );
}
