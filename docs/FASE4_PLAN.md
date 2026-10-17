# Fase 4: noticias y memoria de lecciones (plan, 2026-10-17)

Alcance (`docs/SPEC.md`, "Noticias + Claude + memoria/aprendizaje"; `docs/FASE3_PLAN.md`, "Hoja de ruta
posterior"). **Todo se construye apagado y fuera del prompt congelado de la medición en curso.** Lo que
se haga con ello en un prompt se activará después del veredicto, como una versión nueva
(`signal_review_v5` / `features_v3`), que queda preparada pero sin usar.

## Reglas por la medición en curso (aceptadas el 2026-10-17)

1. **Nada entra al prompt congelado.** `SYSTEM_PROMPT`, `FEATURES_VERSION`, `build_user_message`,
   `FROZEN_PROMPT_SHA256` y `app/llm/prompts.py` no se tocan; `PROMPT_SHA256` debe seguir siendo
   `0fa0dd75a3fecdae36f3b77c8b7ec5d97d58a2238349283c61bfa7632188c777`. Un test fija el hash, el texto
   del prompt y el contenido de `prompts.py`, y un test por AST prohíbe que `prompts.py`, el labeler,
   `decision_service`, `market_features`, el brazo ejecutado y el scheduler importen `app.news` o
   `app.memory`.
2. **Cada pieza tiene su interruptor propio, apagado por defecto**: `NEWS_COLLECTION_ENABLED`,
   `LESSONS_MEMORY_ENABLED`. La versión nueva del prompt no tiene interruptor: no está conectada a nada.
3. **Noticias: solo fuentes gratuitas y sin tarjeta, verificadas en su documentación oficial** (abajo).
   Caché local, reintentos con backoff, degradación controlada y salud por fuente.
4. **La recolección corre aislada** (tarea propia, un fallo o cuelgue no afecta al scheduler, al
   etiquetado, al brazo ejecutado ni al monitor) y **sin costo de la API de Anthropic** (clasificación
   determinista, sin LLM). `start_bot.ps1` la activa solo con el puerto 8000; basta poner
   `NEWS_COLLECTION_ENABLED=false` ahí para dejarla apagada también en el bot real.
5. **La memoria de lecciones se nutre solo de operaciones cerradas** y **nunca muestra PnL antes de
   `is_outcome_reveal_allowed`** (API, Telegram, dashboard, scripts).

## Fuentes de noticias: verificación (2026-10-17)

Leyenda: **[V]** verificado en la documentación oficial o con una respuesta real · **[NV]** no pude
verificarlo.

| Fuente | Resultado | Motivo |
|---|---|---|
| **GDELT DOC 2.0 API** (`api.gdeltproject.org/api/v2/doc/doc`) | **Integrada** | [V] licencia (gdeltproject.org/about.html): "available for unlimited and unrestricted use for any academic, commercial, or governmental use of any kind without fee"; exige citar al GDELT Project con enlace. [V] sin API key, sin tarjeta. [V] límite: la propia API responde HTTP 429 "Please limit requests to one every 5 seconds". [NV] el formato real de los artículos: **desde este PC la API respondió 429 incluso con 20 s entre llamadas** (límite por IP más estricto que el documentado), así que el parser se escribe contra los campos documentados (título, URL, dominio, idioma, país, fecha) y es defensivo. Solo se guardan título, URL, dominio y fecha (no el cuerpo) |
| **Federal Reserve, comunicados de política monetaria** (`federalreserve.gov/feeds/press_monetary.xml`) | **Integrada** | [V] respuesta real: RSS 2.0 con `ETag` y `Last-Modified` (se usa GET condicional) y un BOM al inicio que el parser debe tolerar. [V] "Unless otherwise indicated, information on Board's website is in the public domain and may be copied and distributed without permission. Please cite to the Board as the source". [NV] política de acceso automatizado / límites (la página de avisos no los menciona): se consulta como máximo cada 60 min con GET condicional |
| **Alternative.me Fear & Greed Index** (`api.alternative.me/fng/`) | **Integrada** (dato de mercado, no noticia) | [V] respuesta real: JSON con `value`, `value_classification`, `timestamp`. [V] sin API key. [V] términos: "You must properly acknowledge the source of the data and prominently reference it accordingly"; "Commercial use is allowed as long as the attribution is given right next to the display of the data"; no usarlo para suplantar su servicio. [NV] límite de peticiones (no figura): una consulta cada 6 h |
| CoinDesk RSS | **Excluida** | [V] el RSS existe, pero su `robots.txt` bloquea explícitamente a ClaudeBot, Claude-Web, anthropic-ai y otros rastreadores de IA ("Allow: /price/ Disallow: /"); [NV] sus términos de uso devolvieron 404. Como el uso final es alimentar a un LLM, se descarta por precaución |
| Cointelegraph RSS | **Excluida** | [V] términos: "Redistribution, reproduction, automated scraping, or creation of independent content pipelines ... is strictly prohibited without prior written authorization" y "Use of the Platform Content for training, fine-tuning, validation, benchmarking or operation of artificial intelligence systems, machine learning models, large language models ... is strictly prohibited without the prior written consent of Cointelegraph"; solo uso personal e informativo |
| Decrypt RSS | **Excluida** | [V] términos (12-oct-2020): prohíbe "robots, spiders, scripts ... designed to data mine or scrape the Content" sin consentimiento y exige uso no comercial; menciona RSS/APIs solo para las contribuciones de usuarios. Ambiguo para un RSS, así que no se asume permiso |
| CryptoPanic API | **No integrada** | [NV] requiere API key (cuenta); no encontré sus términos oficiales (plan gratuito, atribución, uso con IA) |
| SEC (comunicados RSS) | **No integrada** | [V] "The SEC does not allow 'unclassified' bots or automated tools to crawl the site" y máximo 10 peticiones/s: exige identificar el bot con un contacto tuyo; queda como opción futura |
| Anuncios de Bitunix (mantenimientos, delistings) | **No integrada** | [NV] no encontré un feed oficial verificable |

Consecuencia: la cobertura de titulares de cripto depende de GDELT, que desde este PC está limitado. El
diseño lo asume ("fuentes gratuitas poco fiables"): si GDELT no responde, el contexto de noticias queda
`null` con su motivo, nunca se inventa, y el resto del sistema no se entera.

## Diseño

### Noticias (`app/news/`)

- **Recolección** (`collector.py`): una tarea propia (`runtime.py`, mismo patrón aislado que Telegram:
  construirla no puede lanzar y `run_isolated` captura cualquier excepción). Cada fuente tiene su
  intervalo mínimo, GET condicional (`ETag`/`Last-Modified`) donde existe, tope de tamaño de respuesta,
  timeout duro, reintentos con backoff exponencial y jitter, y respeta `Retry-After`/429 con enfriamiento.
  Sin `anthropic` ni ninguna llamada de pago.
- **Normalización**: título saneado (sin caracteres de control, espacios colapsados, máx. 200
  caracteres), URL canónica sin parámetros de seguimiento, hashes de URL y de título.
- **Deduplicación**: `url_hash` único; título casi duplicado (Jaccard de tokens ≥ 0,8 en 48 h) se
  guarda marcado `dup_of` y se excluye del contexto.
- **Activo afectado y sentimiento (determinista, sin costo)**: tabla de alias de los símbolos del
  universo (por nombre completo; los tickers cortos y ambiguos solo cuentan en mayúsculas) y un
  léxico de sentimiento de titulares con negaciones. Cada fila guarda el método (`LEXICON_V1`) para poder
  convivir con un clasificador futuro. Un clasificador con Haiku es una opción **posterior** y de pago:
  no se implementa ahora.
- **Salud por fuente**: reusa `data_source_health` (`news:gdelt`, `news:fed_monetary`,
  `news:fear_greed`); el estado condicional vive en `news_fetch_state`. `/health.news` informa
  habilitado/activo, fuentes y titulares de 24 h. Las noticias **no son un dato crítico**: una fuente
  caída no bloquea ninguna operación.
- **Contexto para un prompt futuro** (`context.py`): solo ítems con `published_at <= as_of` **y**
  `fetched_at <= as_of` (sin mirar al futuro), sin duplicados, titulares como **texto no confiable**
  (JSON-escapado, truncado, marcado `untrusted_text`), con agregados (n, sentimiento medio, Fear & Greed)
  y degradación: si no hay datos frescos, `null` con motivo.
- **Atribución visible**: "Titulares: GDELT Project (gdeltproject.org)", "Comunicados: Board of
  Governors of the Federal Reserve System", "Fear & Greed Index: alternative.me" junto a cualquier
  muestra de esos datos (API y dashboard).
- Retención: `NEWS_RETENTION_DAYS` (180). La poda corre en la misma tarea.

### Memoria de lecciones (`app/memory/`)

- **Fuente: solo operaciones CERRADAS y fiables** (sombras cerradas con PnL, sin `RECONCILE_FAILED`;
  mismos filtros que `ai_value`). Una fila por grupo en `memory_entries` (inmutable): características
  conocidas **al momento de la decisión** (estrategias, lado, símbolo, timeframe, hora, ATR, riesgo/
  beneficio, riesgo del SL, etiqueta del LLM, sentimiento y Fear & Greed vigentes si existían) y el
  resultado (PnL, retorno sobre el margen, motivo de cierre, duración).
- **Recuperación de operaciones similares** (`retrieval.py`): puntaje de similitud ponderado; devuelve
  las K ganadoras y K perdedoras más parecidas, **solo cerradas estrictamente antes del instante de la
  decisión** (sin fuga), y las estadísticas de la cohorte **solo si n ≥ `LESSONS_MIN_SAMPLE`** (30); con
  menos, "BAJA evidencia" y el conteo.
- **Lecciones** (`lessons.py`): patrones agregados (estrategia × lado, hora, ATR, riesgo/beneficio,
  sentimiento, etiqueta) con n, tasa de acierto, retorno medio e intervalo de Wilson. Una lección solo es
  `ACTIVE` si n ≥ mínimo, su intervalo **excluye** la tasa base y el signo se mantiene en las dos mitades
  del tiempo; el resto queda `INSUFFICIENT`. Se reconstruyen por versión (`build_id`) y se guarda qué
  lecciones se inyectaron en cada decisión (`lesson_applications`). *Limitación honesta:* "qué lecciones
  cambiaron decisiones" no tiene contrafactual con una sola llamada; el registro permite comparar
  después las tasas de aprobación con y sin lecciones.
- **Post-mortem** (`postmortem.py`): tras una racha de N pérdidas consecutivas de la cuenta paper
  (el circuit breaker ya pausa las entradas), un resumen determinista de lo que tuvieron en común.
- **Calibración** (`calibration.py`): confianza del LLM (`confianza` de `llm_logs`) por tramos frente al
  acierto de las APROBADA. Informativa: no recalibra nada sola.
- **Reveal**: todo lo que lleva PnL o tasas pasa por `is_outcome_reveal_allowed`. Sin él, la API, el
  script y Telegram solo dicen cuántas operaciones cerradas hay y que los resultados están ocultos.
  `scripts/lessons_report.py --db <copia>` se niega a mostrar lecciones sin registro oficial.
- Interruptor `LESSONS_MEMORY_ENABLED` (apagado): una tarea aislada reconstruye memoria y lecciones cada
  `LESSONS_REBUILD_HOURS`. No se activa en el bot real durante la medición.

### Prompt v5 / features v3 (preparados, sin usar)

`app/llm/prompts_v5.py`: `PROMPT_VERSION_V5 = "signal_review_v5"`, `FEATURES_VERSION_V3 = "features_v3"`,
`SYSTEM_PROMPT_V5` (el v4 más un párrafo que declara los titulares como **texto no confiable que se
evalúa, nunca una instrucción**, y las lecciones como estadística con su tamaño de muestra, no una
garantía), `PROMPT_SHA256_V5` (misma fórmula) y `build_features_v3` (las features v2 sin cambios más
`news_context` y `lessons_context`). **Nada lo importa fuera de los tests.** Procedimiento de activación
(después del veredicto, con tu aprobación): nueva constante congelada para v5, el labeler pasa a usar v5 bajo
un hash distinto (`measurement_keys` por hash evita mezclar), y el contexto de lecciones exige el
registro oficial.

## Pruebas

Fuentes (parsers con fixtures reales y sintéticas, BOM, XML hostil sin DTD/entidades, 429, `Retry-After`,
tamaño máximo, backoff, GET condicional), saneado/canonización/hash/dedup, alias y sentimiento (casos
límite y negaciones), colector (salud por fuente, degradación, poda, idempotencia, fuente caída no
afecta a las otras), aislamiento (colector que lanza o cuelga no frena scheduler/labeler/brazo/monitor;
sin imports de `anthropic` ni del núcleo), memoria (sin fuga temporal, mínimos de muestra, estabilidad,
reveal gate en cada salida, solo cerradas), prompt v5 (hash distinto, v4 idéntico, ningún módulo del
núcleo lo importa) y humo con una copia de la base real. Frontend: tests de Vitest de las vistas nuevas.

## Subfases y commits

| Subfase | Contenido |
|---|---|
| 4.1 | Plan, ajustes (`Settings`, `.env.example`, `start_bot.ps1`, `run_dev_server.py`) y tablas + repositorios |
| 4.2 | Núcleo de noticias: saneado, hashes, dedup, activos, sentimiento, parsers de las 3 fuentes |
| 4.3 | Colector con backoff/caché/salud, arranque aislado, `/health.news`, `scripts/news_status.py` |
| 4.4 | Contexto de noticias y memoria: entradas, recuperación sin fuga, lecciones, post-mortem, calibración |
| 4.5 | Prompt v5 / features v3 preparados y pruebas de que el prompt de la medición no cambió |
| 4.6 | API de solo lectura (noticias, lecciones con reveal), tarjeta del dashboard con atribución |
| 4.7 | Documentación, verificación completa, humo sobre copia real y `git push origin master` |

## Qué no pude verificar

GDELT: formato real de los artículos y límite efectivo desde este PC (429 persistente). Fed: política de
acceso automatizado. Alternative.me: límite de peticiones. CoinDesk: términos de uso (404). CryptoPanic:
términos y plan gratuito. Anuncios de Bitunix: feed oficial. Si alguna de estas fuentes cambia de
términos, se desactiva quitándola de `NEWS_SOURCES`.
