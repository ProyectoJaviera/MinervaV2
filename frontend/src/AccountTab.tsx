import { type AccountTrade, useAccount } from "./api";
import { Card } from "./components/Card";
import { StatTile } from "./components/StatTile";
import { Unavailable } from "./components/Unavailable";
import { fmtDate, fmtNum, fmtSigned, fmtUsdt } from "./format";

// Fase 5: cuenta paper. Estructura (simbolo, lado, estrategia, niveles y horas) siempre; precio de
// salida, motivo de cierre, PnL y saldo SOLO si el servidor dice `reveal_allowed` Y manda los
// campos. Falla CERRADO: sin ambas cosas no se muestra nada de resultados, y el cliente nunca los
// calcula.
const HIDDEN_MESSAGE =
  "Resultados de la cuenta paper ocultos hasta el registro oficial del veredicto (regla del 2026-10-17).";

function Th({ children }: { children: React.ReactNode }) {
  return <th className="py-1 pr-2 font-normal">{children}</th>;
}

function TradeTable({
  title,
  trades,
  closed,
  showOutcome,
}: {
  title: string;
  trades: AccountTrade[];
  closed: boolean;
  showOutcome: boolean;
}) {
  // abiertas: 8 columnas; cerradas: 7, y 10 si ademas se muestran los resultados
  const columns = closed ? (showOutcome ? 10 : 7) : 8;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <caption className="mb-1 text-left text-sm text-gray-700 dark:text-gray-300">
          {title}
        </caption>
        <thead>
          <tr className="border-b border-gray-200 text-gray-500 dark:border-gray-700 dark:text-gray-400">
            <Th>Abierta</Th>
            {closed && <Th>Cerrada</Th>}
            <Th>Simbolo</Th>
            <Th>Estrategia</Th>
            <Th>Entrada</Th>
            <Th>SL</Th>
            <Th>TP</Th>
            {!closed && <Th>Liquidacion</Th>}
            {!closed && <Th>Margen</Th>}
            {closed && showOutcome && (
              <>
                <Th>Salida</Th>
                <Th>PnL neto</Th>
                <Th>Motivo de cierre</Th>
              </>
            )}
          </tr>
        </thead>
        <tbody className="tabular-nums">
          {trades.map((t) => (
            <tr key={t.id} className="border-b border-gray-100 dark:border-gray-800">
              <td className="whitespace-nowrap py-1 pr-2">{fmtDate(t.opened_at)}</td>
              {closed && <td className="whitespace-nowrap py-1 pr-2">{fmtDate(t.closed_at)}</td>}
              <td className="py-1 pr-2">
                {t.symbol} {t.side}
              </td>
              <td className="py-1 pr-2">{t.strategy ?? "-"}</td>
              <td className="py-1 pr-2">{fmtNum(t.entry_price)}</td>
              <td className="py-1 pr-2">{t.sl_price === null ? "sin SL" : fmtNum(t.sl_price)}</td>
              <td className="py-1 pr-2">{t.tp_price === null ? "sin TP" : fmtNum(t.tp_price)}</td>
              {!closed && <td className="py-1 pr-2">{fmtNum(t.liq_price)}</td>}
              {!closed && <td className="py-1 pr-2">{fmtUsdt(t.margin_usdt)}</td>}
              {closed && showOutcome && (
                <>
                  <td className="py-1 pr-2">{fmtNum(t.exit_price)}</td>
                  <td className="py-1 pr-2">{fmtSigned(t.pnl_net_usdt)}</td>
                  <td className="py-1 pr-2">{t.close_reason ?? "-"}</td>
                </>
              )}
            </tr>
          ))}
          {trades.length === 0 && (
            <tr>
              <td colSpan={columns} className="py-2 text-gray-400">
                Ninguna todavia.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

export function AccountTab() {
  const account = useAccount(100);

  if (account.isLoading) {
    return <p className="text-sm text-gray-500">Cargando...</p>;
  }
  if (account.isError || !account.data) {
    return (
      <Card title="Cuenta paper">
        <Unavailable error={account.error} what="Cuenta paper" />
      </Card>
    );
  }

  const data = account.data;
  const reveal = data.reveal_allowed === true;
  const showOutcome = reveal && data.closed.some((t) => "pnl_net_usdt" in t);
  const results = reveal ? data.results : null;

  return (
    <div className="space-y-4">
      <Card title="Cuenta paper">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatTile label="Capital inicial" value={fmtUsdt(data.initial_capital_usdt)} />
          <StatTile
            label="Posiciones abiertas"
            value={`${data.summary.open}/${data.summary.max_simultaneous_positions}`}
          />
          <StatTile label="Operaciones cerradas" value={String(data.summary.closed)} />
          <StatTile label="Margen en uso" value={fmtUsdt(data.summary.margin_in_use_usdt)} />
        </div>
        {!reveal && (
          <p
            role="note"
            className="mt-3 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-900/30 dark:text-amber-200"
          >
            {data.message ?? HIDDEN_MESSAGE}
          </p>
        )}
        {results && (
          <div className="mt-3 space-y-2">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <StatTile label="Saldo realizado" value={fmtUsdt(results.balance_usdt)} />
              <StatTile label="PnL realizado" value={fmtSigned(results.realized_pnl_usdt)} />
              <StatTile label="PnL flotante" value={fmtSigned(results.unrealized_pnl_usdt)} />
              <StatTile label="Equity" value={fmtUsdt(results.equity_usdt)} />
              <StatTile label="Ganadas / perdidas" value={`${results.wins} / ${results.losses}`} />
              <StatTile
                label="Profit factor"
                value={results.profit_factor === null ? "-" : results.profit_factor.toFixed(2)}
              />
            </div>
            {results.marks_missing.length > 0 && (
              <p className="text-xs text-gray-500 dark:text-gray-400">
                PnL flotante parcial: sin precio actual de {results.marks_missing.join(", ")}.
              </p>
            )}
          </div>
        )}
      </Card>

      <Card title="Posiciones">
        <div className="space-y-4">
          <TradeTable
            title="Abiertas"
            trades={data.open}
            closed={false}
            showOutcome={false}
          />
          <TradeTable
            title="Cerradas (las 100 mas recientes)"
            trades={data.closed}
            closed={true}
            showOutcome={showOutcome}
          />
        </div>
        <p className="mt-2 text-xs text-gray-400 dark:text-gray-500">
          Cuenta paper: dinero simulado, nunca ordenes reales. Las horas de cierre se muestran;
          el precio de salida, el motivo y el PnL solo con el registro oficial del veredicto.
        </p>
      </Card>
    </div>
  );
}
