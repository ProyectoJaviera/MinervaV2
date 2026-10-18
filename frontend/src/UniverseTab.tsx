import { useBacktestVerdicts, useUniverse } from "./api";
import { CoinGeckoAttribution } from "./components/Attribution";
import { Card } from "./components/Card";
import { StatTile } from "./components/StatTile";
import { Unavailable } from "./components/Unavailable";
import { fmtAgo, fmtCompact, fmtDate, fmtNum, fmtPercentValue } from "./format";

// Fase 5: universo vigente y veredictos de backtest por estrategia. El universo viene de CoinGecko:
// su atribucion ("Data provided by CoinGecko", enlazada) va junto a la tabla, tal como pide su guia
// de marca. Los veredictos son del backtest historico (Fase 2), no de la medicion en vivo.
function UniverseCard() {
  const universe = useUniverse();

  if (universe.isLoading) {
    return <p className="text-sm text-gray-500">Cargando universo...</p>;
  }
  if (universe.isError || !universe.data) {
    return (
      <Card title="Universo vigente">
        <Unavailable error={universe.error} what="Universo" />
      </Card>
    );
  }

  const u = universe.data;
  return (
    <Card title="Universo vigente">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatTile label="Actualizado" value={fmtDate(u.refreshed_at)} />
        <StatTile
          label="Antiguedad"
          value={u.refreshed_at ? fmtAgo(u.refreshed_at) : "sin snapshot"}
        />
        <StatTile label="Activos incluidos" value={`${u.included.length} de ${u.universe_size}`} />
        <StatTile label="Refresco" value={`cada ${u.refresh_hours} h`} />
      </div>
      {u.stale && (
        <p
          role="alert"
          className="mt-3 rounded border border-red-300 bg-red-50 p-3 text-sm text-red-900 dark:border-red-700 dark:bg-red-900/30 dark:text-red-200"
        >
          Universo OBSOLETO (mas de {u.staleness_limit_hours} h sin refrescar): el generador no
          produce senales hasta que un refresco tenga exito (la medicion se detiene; los cierres de
          posiciones no se ven afectados).
        </p>
      )}
      {u.last_refresh_error && (
        <p className="mt-3 text-sm text-red-700 dark:text-red-400">
          Ultimo refresco fallido: {u.last_refresh_error}
        </p>
      )}
      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-left text-sm">
          <caption className="mb-1 text-left text-sm text-gray-700 dark:text-gray-300">
            Candidatos del ultimo refresco (CoinGecko, ordenados por capitalizacion)
          </caption>
          <thead>
            <tr className="border-b border-gray-200 text-gray-500 dark:border-gray-700 dark:text-gray-400">
              <th className="py-1 pr-2 font-normal">#</th>
              <th className="py-1 pr-2 font-normal">Simbolo</th>
              <th className="py-1 pr-2 font-normal">Capitalizacion</th>
              <th className="py-1 pr-2 font-normal">Estado</th>
              <th className="py-1 pr-2 font-normal">Ultimo precio</th>
              <th className="py-1 pr-2 font-normal">Motivo</th>
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {u.candidates.map((c) => (
              <tr key={`${c.coingecko_id}-${c.symbol}`} className="border-b border-gray-100 dark:border-gray-800">
                <td className="py-1 pr-2">{c.rank ?? "-"}</td>
                <td className="py-1 pr-2">{c.symbol}</td>
                <td className="py-1 pr-2">{fmtCompact(c.market_cap_usd)}</td>
                <td
                  className={
                    "py-1 pr-2 font-medium " +
                    (c.included ? "text-green-700 dark:text-green-400" : "text-gray-500")
                  }
                >
                  {c.included ? "INCLUIDO" : "excluido"}
                </td>
                <td className="py-1 pr-2">{c.mark_price === null ? "-" : fmtNum(c.mark_price)}</td>
                <td className="py-1 pr-2 text-xs text-gray-600 dark:text-gray-400">
                  {c.exclusion_reason ?? ""}
                </td>
              </tr>
            ))}
            {u.candidates.length === 0 && (
              <tr>
                <td colSpan={6} className="py-2 text-gray-400">
                  Sin snapshot todavia.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {/* Atribucion junto a los datos (debajo de la tabla), como pide la guia de CoinGecko. */}
      <CoinGeckoAttribution className="mt-1" />
      <div className="mt-3">
        <p className="mb-1 text-sm text-gray-700 dark:text-gray-300">Cambios recientes</p>
        {u.changes.length === 0 ? (
          <p className="text-sm text-gray-400">Sin cambios en los ultimos refrescos.</p>
        ) : (
          <ul className="space-y-0.5 text-sm">
            {u.changes.map((c) => (
              <li key={c.at}>
                {fmtDate(c.at)}: entran {c.added.join(", ") || "ninguno"}; salen{" "}
                {c.removed.join(", ") || "ninguno"}
              </li>
            ))}
          </ul>
        )}
      </div>
      <p className="mt-3 text-xs text-gray-500 dark:text-gray-400">{u.rule}</p>
    </Card>
  );
}

function VerdictsCard() {
  const verdicts = useBacktestVerdicts();

  if (verdicts.isLoading) {
    return <p className="text-sm text-gray-500">Cargando veredictos...</p>;
  }
  if (verdicts.isError || !verdicts.data) {
    return (
      <Card title="Veredictos de backtest por estrategia">
        <Unavailable error={verdicts.error} what="Veredictos de backtest" />
      </Card>
    );
  }

  const data = verdicts.data;
  return (
    <Card title="Veredictos de backtest por estrategia">
      <p className="mb-2 text-sm text-gray-700 dark:text-gray-300">
        Estrategias elegibles para la cuenta paper:{" "}
        {data.all_strategies_eligible ? "todas" : data.eligible_strategies.join(", ")}
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-gray-200 text-gray-500 dark:border-gray-700 dark:text-gray-400">
              <th className="py-1 pr-2 font-normal">Estrategia</th>
              <th className="py-1 pr-2 font-normal">Veredicto</th>
              <th className="py-1 pr-2 font-normal">En la cuenta</th>
              <th className="py-1 pr-2 font-normal">PF OOS</th>
              <th className="py-1 pr-2 font-normal">PF con estres</th>
              <th className="py-1 pr-2 font-normal">Simbolos PF&gt;1</th>
              <th className="py-1 pr-2 font-normal">Folds +</th>
              <th className="py-1 pr-2 font-normal">DD OOS</th>
              <th className="py-1 pr-2 font-normal">Concentracion</th>
              <th className="py-1 pr-2 font-normal">Operaciones</th>
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {data.verdicts.map((v) => (
              <tr key={v.strategy} className="border-b border-gray-100 align-top dark:border-gray-800">
                <td className="py-1 pr-2">
                  {v.strategy}
                  {v.experimental && (
                    <span className="ml-1 rounded bg-gray-100 px-1 text-xs text-gray-600 dark:bg-gray-700 dark:text-gray-300">
                      experimental
                    </span>
                  )}
                </td>
                <td
                  className={
                    "py-1 pr-2 font-medium " +
                    (v.discarded
                      ? "text-red-700 dark:text-red-400"
                      : v.evidence_insufficient
                        ? "text-amber-700 dark:text-amber-400"
                        : "text-green-700 dark:text-green-400")
                  }
                >
                  {v.discarded
                    ? "DESCARTADA"
                    : v.evidence_insufficient
                      ? "evidencia insuficiente"
                      : "pasa"}
                  {v.reasons.length > 0 && (
                    <ul className="mt-0.5 list-disc pl-4 text-xs font-normal text-gray-600 dark:text-gray-400">
                      {v.reasons.map((reason) => (
                        <li key={reason}>{reason}</li>
                      ))}
                    </ul>
                  )}
                </td>
                <td className="py-1 pr-2">{v.eligible_for_account ? "elegible" : "solo sombra"}</td>
                <td className="py-1 pr-2">{v.pf_oos_aggregate === null ? "-" : v.pf_oos_aggregate.toFixed(2)}</td>
                <td className="py-1 pr-2">{v.pf_stressed === null ? "-" : v.pf_stressed.toFixed(2)}</td>
                <td className="py-1 pr-2">{fmtPercentValue(v.pct_symbols_pf_gt1)}</td>
                <td className="py-1 pr-2">{fmtPercentValue(v.pct_folds_positive)}</td>
                <td className="py-1 pr-2">{fmtPercentValue(v.max_drawdown_oos_pct)}</td>
                <td className="py-1 pr-2">{fmtPercentValue(v.concentration_pct)}</td>
                <td className="py-1 pr-2">{v.total_trades_all_segments}</td>
              </tr>
            ))}
            {data.verdicts.length === 0 && (
              <tr>
                <td colSpan={10} className="py-2 text-gray-400">
                  Sin veredictos de backtest en la base.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
        {data.note}
        {data.run_at && ` Corrida del ${fmtDate(data.run_at)}.`}
      </p>
    </Card>
  );
}

export function UniverseTab() {
  return (
    <div className="space-y-4">
      <UniverseCard />
      <VerdictsCard />
    </div>
  );
}
