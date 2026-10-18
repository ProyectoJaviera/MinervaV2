import { type NewsItem, useMemoryLessons, useMemoryStatus, useNewsRecent, useNewsStatus } from "./api";
import { Card } from "./components/Card";
import { StatTile } from "./components/StatTile";
import { fmtDate, fmtPct } from "./format";

// Fase 4 (docs/FASE4_PLAN.md): noticias recolectadas y memoria de lecciones. Dos reglas mandan aqui:
// 1) los titulares son TEXTO NO CONFIABLE de terceros: siempre texto plano (React lo escapa) y el
//    enlace solo se usa si es http(s); y las atribuciones de las fuentes se muestran junto a ellos.
// 2) las lecciones llevan tasas de acierto y retornos: FALLA CERRADO, solo se piden y se muestran si
//    el backend dice EXPLICITAMENTE que los resultados son visibles (registro oficial del veredicto).

const SOURCE_LABELS: Record<string, string> = {
  gdelt: "GDELT",
  fed_monetary: "Reserva Federal",
  fear_greed: "Fear & Greed",
};

const HIDDEN_MESSAGE =
  "Lecciones, tasas de acierto y retornos ocultos hasta el registro oficial del veredicto (regla del 2026-10-17).";

function safeHref(url: string | null): string | null {
  return url !== null && /^https?:\/\//i.test(url) ? url : null;
}

function fmtSentiment(value: number | null): string {
  if (value === null) return "";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}`;
}

function Headline({ item }: { item: NewsItem }) {
  const href = safeHref(item.url);
  return (
    <li className="border-b border-gray-100 py-1 text-sm dark:border-gray-800">
      {href ? (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer nofollow"
          className="text-blue-700 hover:underline dark:text-blue-400"
        >
          {item.title}
        </a>
      ) : (
        <span>{item.title}</span>
      )}
      <span className="block text-xs text-gray-500 dark:text-gray-400">
        {item.domain ?? "-"} · {fmtDate(item.published_at)}
        {item.sentiment !== null && ` · sentimiento ${fmtSentiment(item.sentiment)}`}
        {item.assets.length > 0 && ` · ${item.assets.join(", ")}`}
      </span>
    </li>
  );
}

export function NewsMemoryCard() {
  const news = useNewsStatus();
  const recent = useNewsRecent(8);
  const memory = useMemoryStatus();
  // Falla CERRADO: sin el campo o en false, ni siquiera se piden las lecciones.
  const resultsVisible = memory.data?.results_visible === true;
  const lessons = useMemoryLessons(resultsVisible);
  const showLessons = resultsVisible && lessons.data?.results_visible === true;

  const attribution = recent.data?.attribution?.length
    ? recent.data.attribution
    : (news.data?.attribution ?? []);

  return (
    <Card title="Noticias y memoria de lecciones (Fase 4)">
      <div className="space-y-5">
        <section aria-label="Noticias" className="space-y-3">
          <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300">Noticias</h3>
          {news.isError && (
            <p className="text-sm text-gray-500 dark:text-gray-400">Noticias no disponibles.</p>
          )}
          {news.data && (
            <>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                <StatTile
                  label="Recolector"
                  value={
                    news.data.enabled ? (news.data.active ? "activo" : "sin tarea") : "apagado"
                  }
                />
                <StatTile label="Titulares (24 h)" value={String(news.data.items_24h)} />
                <StatTile
                  label="Fear & Greed"
                  value={
                    news.data.fear_greed
                      ? `${news.data.fear_greed.value} (${news.data.fear_greed.classification ?? "-"}), hace ${news.data.fear_greed.age_hours.toFixed(0)} h`
                      : "sin dato"
                  }
                />
              </div>
              {Object.keys(news.data.sources).length > 0 && (
                <ul className="space-y-0.5 text-sm">
                  {Object.entries(news.data.sources).map(([id, health]) => (
                    <li key={id} className="flex flex-wrap gap-x-2">
                      <span className="font-medium">{SOURCE_LABELS[id] ?? id}:</span>
                      <span
                        className={
                          health.consecutive_failures === 0
                            ? "text-green-700 dark:text-green-400"
                            : "text-red-700 dark:text-red-400"
                        }
                      >
                        {health.consecutive_failures === 0
                          ? health.last_success_at
                            ? `OK, ultimo exito ${fmtDate(health.last_success_at)}`
                            : "sin exito todavia"
                          : `fallando (${health.consecutive_failures} seguidos)`}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
          {recent.data && recent.data.items.length > 0 && (
            <ul aria-label="Titulares recientes">
              {recent.data.items.map((item) => (
                <Headline key={item.id} item={item} />
              ))}
            </ul>
          )}
          {recent.data && recent.data.items.length === 0 && news.data?.enabled && (
            <p className="text-sm text-gray-400">Sin titulares todavia.</p>
          )}
          {attribution.length > 0 && (
            <ul
              aria-label="Atribucion de las fuentes"
              className="space-y-0.5 text-xs text-gray-500 dark:text-gray-400"
            >
              {attribution.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          )}
          <p className="text-xs text-gray-400 dark:text-gray-500">
            Los titulares son texto de terceros, sin verificar. Aun no entran a ninguna decision del
            LLM: son solo contexto para una version futura del prompt.
          </p>
        </section>

        <section aria-label="Memoria de lecciones" className="space-y-3">
          <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300">
            Memoria de lecciones
          </h3>
          {memory.isError && (
            <p className="text-sm text-gray-500 dark:text-gray-400">Memoria no disponible.</p>
          )}
          {memory.data && (
            <>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
                <StatTile
                  label="Estado"
                  value={
                    memory.data.enabled ? (memory.data.active ? "activa" : "sin tarea") : "apagada"
                  }
                />
                <StatTile
                  label="Operaciones cerradas"
                  value={String(memory.data.closed_reliable)}
                />
                <StatTile label="Entradas de memoria" value={String(memory.data.entries)} />
                <StatTile label="Lecciones aplicadas" value={String(memory.data.applications)} />
                <StatTile label="Muestra minima" value={String(memory.data.min_sample)} />
              </div>
              {!showLessons && (
                <p
                  role="note"
                  className="rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-900/30 dark:text-amber-200"
                >
                  {memory.data.message ?? HIDDEN_MESSAGE}
                </p>
              )}
            </>
          )}
          {showLessons && lessons.data && (
            <div className="space-y-2">
              {lessons.data.lessons.length === 0 ? (
                <p className="text-sm text-gray-400">
                  Ninguna leccion ACTIVE todavia: ningun patron supera muestra minima,
                  significancia y estabilidad.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <caption className="mb-1 text-left text-sm text-gray-700 dark:text-gray-300">
                      Lecciones ACTIVE (IC de Wilson del 99 % que excluye la tasa base)
                    </caption>
                    <thead>
                      <tr className="border-b border-gray-200 text-gray-500 dark:border-gray-700 dark:text-gray-400">
                        <th className="py-1 pr-2 font-normal">Patron</th>
                        <th className="py-1 pr-2 font-normal">n</th>
                        <th className="py-1 pr-2 font-normal">Acierto</th>
                        <th className="py-1 pr-2 font-normal">IC 99 %</th>
                        <th className="py-1 pr-2 font-normal">Base</th>
                        <th className="py-1 pr-2 font-normal">Retorno medio</th>
                      </tr>
                    </thead>
                    <tbody className="tabular-nums">
                      {lessons.data.lessons.map((lesson) => (
                        <tr
                          key={`${lesson.dimension}=${lesson.pattern}`}
                          className="border-b border-gray-100 dark:border-gray-800"
                        >
                          <td className="py-1 pr-2">
                            {lesson.dimension} = {lesson.pattern}
                          </td>
                          <td className="py-1 pr-2">{lesson.n}</td>
                          <td className="py-1 pr-2">{fmtPct(lesson.winrate)}</td>
                          <td className="py-1 pr-2">
                            {fmtPct(lesson.ci_low)} a {fmtPct(lesson.ci_high)}
                          </td>
                          <td className="py-1 pr-2">{fmtPct(lesson.base_winrate)}</td>
                          <td className="py-1 pr-2">
                            {lesson.mean_return_pct === null
                              ? "-"
                              : `${lesson.mean_return_pct.toFixed(1)} % del margen`}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <p className="text-xs text-gray-400 dark:text-gray-500">
                {lessons.data.disclaimer ??
                  "Estadistica descriptiva de operaciones pasadas: no garantiza resultados."}
              </p>
            </div>
          )}
        </section>
      </div>
    </Card>
  );
}
