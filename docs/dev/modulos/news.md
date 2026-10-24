# Guía del paquete `news`

> Parte de la [documentación técnica](../README.md) · Referencia automática: [`api/app/news.html`](../api/app/news.html)

## Qué hace el paquete

La **recolección de noticias** (Fase 4): una tarea propia que, fuente por fuente, descarga titulares y el índice Fear & Greed,
los limpia, deduplica y etiqueta (activo y sentimiento de forma **determinista**, con un léxico) y registra la salud de cada
fuente. Es gratis y **no usa la API de Anthropic**. Las noticias son texto **no confiable** de terceros: nunca entran al
prompt congelado de la medición.

Fuentes integradas, cada una verificada en su documentación oficial el 2026-10-17: GDELT (`gdelt`), los comunicados de política
monetaria de la Reserva Federal por RSS (`fed_monetary`) y el Fear & Greed de Alternative.me (`fear_greed`). Se descartaron
Cointelegraph, CoinDesk y Decrypt por términos o `robots.txt` que chocan con alimentar un LLM.

## Zona protegida

**No,** con una condición: **ningún módulo del núcleo de la medición** (prompts, etiquetador, servicio de decisión,
scheduler, brazo ejecutado, monitor) puede importar este paquete. Una prueba por AST lo vigila
(`tests/unit/test_news_runtime_isolation.py`). Ver [Qué se puede cambiar con seguridad](../ZONAS_PROTEGIDAS.md#qué-se-puede-cambiar-con-seguridad).

## Cómo se conecta

- `app/main.py` llama a `maybe_start_news_collector`, que crea la tarea solo con `NEWS_COLLECTION_ENABLED=true` (el bot real la
  enciende; el servidor de desarrollo nunca).
- Escribe `news_items`, `news_fetch_state`, `fear_greed` y `data_source_health` (claves `news:<id>`); lee `asset_universe`
  para las consultas por símbolo.
- Lo leen la API (`/api/news/*`), `GET /health` (`news_health_block`) y, en el futuro, el prompt v5 (`context.py`).

## Archivos

### `app/news/__init__.py`

**Responsabilidad.** Docstring del paquete: recolección gratuita y aislada; nada entra al prompt congelado.

**Funciones y clases clave.** Ninguna.

**Invariantes — no romper.** Ningún módulo del núcleo importa este paquete.

**Trampas conocidas.** Ninguna.

<!-- hechos:app/news/__init__.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/news.html`](../api/app/news.html)
- **Importa (internos):** ninguno
- **Lo importan:** ninguno
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** ninguno
<!-- /hechos:app/news/__init__.py -->

### `app/news/assets.py`

**Responsabilidad.** Reconocer qué activo menciona un titular, de forma **determinista** (sin LLM, sin costo).

**Funciones y clases clave.**
- `extract_assets()` — tickers base mencionados: cada activo se reconoce por su nombre (sin distinguir mayúsculas, palabra
  completa) y, si el ticker no es una palabra común, también por el ticker en MAYÚSCULAS exactas. Los nombres compuestos van
  primero para que «Bitcoin Cash» no cuente como BTC.
- `query_for()` — la consulta de GDELT de un símbolo; `base_ticker()` (`BTCUSDT` → `BTC`); `AssetAlias`.

**Invariantes — no romper.** Es una heurística de titulares **en inglés**: puede fallar (un titular que no nombra el activo
queda sin etiqueta) pero **nunca inventa** uno.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/news/assets.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/news/assets.html`](../api/app/news/assets.html)
- **Importa (internos):** ninguno
- **Lo importan:** `app.memory.entries`, `app.news.collector`, `app.news.context`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_news_collector.py`, `tests/unit/test_news_core.py`
<!-- /hechos:app/news/assets.py -->

### `app/news/collector.py`

**Responsabilidad.** El colector: una tarea propia que respeta el intervalo mínimo de cada fuente, pide con GET condicional
donde se puede y guarda titulares limpios, deduplicados y etiquetados.

**Funciones y clases clave.**
- `NewsCollector.run_forever()` / `run_once()` — revisa cada cierto tiempo qué fuentes están vencidas; `run_once` **nunca
  lanza** (salvo cancelación).
- `backoff_seconds()` — espera tras un fallo: 5 minutos × 2^(k−1) con tope de 6 horas (o lo que pida `Retry-After`).
- `SourceRateLimited` — un 429 corta el ciclo.

**Invariantes — no romper.**
- **Aislado y sin costo:** no importa nada del núcleo de la medición ni `anthropic`.
- Cada fuente corre en su propio `try/except` y con un timeout duro (`SOURCE_TIMEOUT_S`, 600 s): un fallo de una no afecta a las
  demás ni al resto del bot.
- GDELT limita a **una petición cada 5 s** (lo dice su propia API en el 429): las consultas de un ciclo se separan
  `request_gap_s`.
- Degradación controlada: tras un fallo la fuente espera su backoff y el contexto de noticias simplemente envejece; nada se
  bloquea.

**Trampas conocidas.** Deduplica contra una ventana de 48 h (`DEDUP_WINDOW`) y poda cada 24 h (`PRUNE_EVERY`).

<!-- hechos:app/news/collector.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/news/collector.html`](../api/app/news/collector.html)
- **Importa (internos):** `app.config`, `app.core.logging`, `app.news.assets`, `app.news.fetch`, `app.news.parsers`, `app.news.sentiment`, `app.news.sources`, `app.news.text`, `app.persistence.database`, `app.persistence.repositories.health_repo`, `app.persistence.repositories.news_repo`, `app.persistence.repositories.universe_repo`
- **Lo importan:** `app.api.deps`, `app.api.routes.health`, `app.api.routes.news`, `app.news.runtime`
- **Configuración que lee:** `NEWS_RETENTION_DAYS`, `news_sources_list`
- **Tablas que lee:** `asset_universe`, `data_source_health`, `news_fetch_state`, `news_items`
- **Tablas que escribe:** `data_source_health`, `fear_greed`, `news_fetch_state`, `news_items`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_news_collector.py`, `tests/unit/test_news_runtime_isolation.py`
<!-- /hechos:app/news/collector.py -->

### `app/news/context.py`

**Responsabilidad.** El contexto de noticias para una decisión **futura** (prompt `signal_review_v5`); nada de esto entra al
prompt congelado.

**Funciones y clases clave.**
- `build_news_context()` — titulares del activo, del mercado y de la Fed disponibles en `as_of`, con Fear & Greed; devuelve un
  `NewsContext`.

**Invariantes — no romper.**
- **Sin mirar al futuro:** solo noticias publicadas **y vistas por el bot** en o antes de `as_of`.
- **Texto no confiable:** cada titular se vuelve a sanear, se acota (`CONTEXT_TITLE_CHARS`, 160) y viaja como cadena de un
  JSON; el bloque va marcado `untrusted_text: true`.
- Sin titulares recientes o con las fuentes caídas el contexto es `None` con un estado (`NO_DATA` / `STALE`): nada se inventa.

**Trampas conocidas.** Lleva los textos de atribución de las fuentes de las que salió algo (requisito de sus términos).

<!-- hechos:app/news/context.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/news/context.html`](../api/app/news/context.html)
- **Importa (internos):** `app.news.assets`, `app.news.sources`, `app.news.text`, `app.persistence.database`, `app.persistence.repositories.health_repo`, `app.persistence.repositories.news_repo`
- **Lo importan:** `app.llm.prompts_v5`
- **Configuración que lee:** ninguno
- **Tablas que lee:** `data_source_health`, `fear_greed`, `news_items`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_news_context.py`, `tests/unit/test_prompt_v5_prepared.py`
<!-- /hechos:app/news/context.py -->

### `app/news/fetch.py`

**Responsabilidad.** El cliente HTTP de las fuentes de noticias.

**Funciones y clases clave.**
- `HttpFetcher.get()` — GET condicional (`ETag` / `Last-Modified`), `User-Agent` propio (`NEWS_USER_AGENT`), tope de tamaño de
  respuesta (`NEWS_MAX_RESPONSE_BYTES`), timeout duro y reintentos con backoff exponencial y *jitter* para fallos de red y 5xx.
- `FetchResult`, `FetchError`, `ResponseTooLargeError` y `parse_retry_after()`.

**Invariantes — no romper.**
- Un 429 **no** se reintenta dentro de la llamada: lo gestiona el colector con un enfriamiento.
- **No sigue redirecciones** y no usa credenciales (ninguna fuente integrada las necesita).
- Se construye con `build_async_http_client` como todo cliente del proyecto.

**Trampas conocidas.** `aclose()` no espera más de 5 segundos.

<!-- hechos:app/news/fetch.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/news/fetch.html`](../api/app/news/fetch.html)
- **Importa (internos):** `app.core.http`, `app.core.logging`
- **Lo importan:** `app.news.collector`, `app.news.runtime`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_news_collector.py`, `tests/unit/test_news_core.py`, `tests/unit/test_news_runtime_isolation.py`
<!-- /hechos:app/news/fetch.py -->

### `app/news/health.py`

**Responsabilidad.** El bloque `news` de `GET /health`: interruptor, si el colector quedó armado, salud por fuente y
titulares de las últimas 24 h.

**Funciones y clases clave.**
- `news_health_block()`.

**Invariantes — no romper.** Solo lectura de `data_source_health` y `news_items`; **nunca resultados de operaciones**. Las
noticias no son un dato crítico: una fuente caída no bloquea nada.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/news/health.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/news/health.html`](../api/app/news/health.html)
- **Importa (internos):** `app.config`, `app.news.sources`, `app.persistence.database`, `app.persistence.repositories.health_repo`, `app.persistence.repositories.news_repo`
- **Lo importan:** `app.api.routes.health`, `app.api.routes.news`
- **Configuración que lee:** `NEWS_COLLECTION_ENABLED`, `news_sources_list`
- **Tablas que lee:** `data_source_health`, `news_items`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** ninguno
<!-- /hechos:app/news/health.py -->

### `app/news/parsers.py`

**Responsabilidad.** Los parsers de las fuentes: reciben texto y devuelven datos; **puros**, sin red ni base.

**Funciones y clases clave.**
- `parse_gdelt()` (GDELT DOC 2.0 `artlist`), `parse_rss()` (RSS 2.0 y Atom de la Fed) y `parse_fear_greed()`.
- `UnsafeXmlError` (se rechaza cualquier DTD o entidad antes de parsear, contra expansión de entidades) y `RateLimitedError`.

**Invariantes — no romper.** Todo lo que viene de fuera es **no confiable**: un campo ausente o de otro tipo se omite y un
elemento malo no tumba al resto.

**Trampas conocidas.**
- Un 429 de GDELT responde **texto** («Please limit requests to one every 5 seconds…»), y una consulta sin resultados responde `{}`.
- **No verificado:** el formato real de los artículos de GDELT no se pudo comprobar desde el equipo de desarrollo (la API respondió
  429): se sigue la documentación.
- La Fed antepone un BOM al XML.

<!-- hechos:app/news/parsers.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/news/parsers.html`](../api/app/news/parsers.html)
- **Importa (internos):** `app.news.text`
- **Lo importan:** `app.news.collector`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_news_core.py`
<!-- /hechos:app/news/parsers.py -->

### `app/news/runtime.py`

**Responsabilidad.** El arranque **aislado** del colector dentro del `lifespan`, con las mismas garantías que el bot de Telegram.

**Funciones y clases clave.**
- `maybe_start_news_collector()` — si corresponde crea la tarea y la agrega a `tasks` (para que el cierre ordenado la cancele).
- `build_news_collector()` — el colector, o `None` si está desactivado; **no puede lanzar**.
- `run_isolated()` — corre el colector capturando cualquier `Exception` que escape.

**Invariantes — no romper.** Apagado por defecto; construirlo no puede lanzar (se registra solo el tipo de la excepción); su fallo
no alcanza al scheduler, al etiquetado, al brazo ni al monitor.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/news/runtime.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/news/runtime.html`](../api/app/news/runtime.html)
- **Importa (internos):** `app.config`, `app.core.logging`, `app.news.collector`, `app.news.fetch`, `app.persistence.database`
- **Lo importan:** `app.main`
- **Configuración que lee:** `NEWS_COLLECTION_ENABLED`, `NEWS_MAX_RESPONSE_BYTES`, `NEWS_USER_AGENT`, `news_sources_list`
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_news_runtime_isolation.py`
<!-- /hechos:app/news/runtime.py -->

### `app/news/sentiment.py`

**Responsabilidad.** El sentimiento de un titular, **determinista y sin costo** (`LEXICON_V1`).

**Funciones y clases clave.**
- `score_headline()` — puntaje en [−1, 1] con un léxico de titulares financieros, una ventana de negación de tres tokens
  y una saturación suave; `label()` — `POSITIVE`, `NEGATIVE` o `NEUTRAL` (banda de ±0,15).

**Invariantes — no romper.** Cada fila guarda el método (`METHOD`) para convivir con un clasificador mejor más adelante (un LLM
barato, de pago, que **no** se activa en esta fase).

**Trampas conocidas.** Es deliberadamente simple: no entiende contexto, ironía ni el tamaño real de la noticia.

<!-- hechos:app/news/sentiment.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/news/sentiment.html`](../api/app/news/sentiment.html)
- **Importa (internos):** ninguno
- **Lo importan:** `app.news.collector`, `scripts.news_status`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_news_core.py`
<!-- /hechos:app/news/sentiment.py -->

### `app/news/sources.py`

**Responsabilidad.** El registro de las fuentes **integradas** y verificadas.

**Funciones y clases clave.**
- `enabled_sources()` — las fuentes conocidas entre las pedidas en `NEWS_SOURCES`, en orden de registro (las desconocidas se
  ignoran); `attribution_lines()`; `NewsSource` y su `health_key()` (`news:<id>`).

**Invariantes — no romper.** Una fuente no listada aquí no existe para el colector, aunque aparezca en `NEWS_SOURCES`. Para agregar
una fuente hay que verificar antes su plan gratuito, límites, términos y `robots.txt`
(ver [RECETAS](../RECETAS.md#agregar-una-fuente-de-noticias)).

**Trampas conocidas.** La atribución de cada fuente es obligatoria junto a cualquier muestra de sus datos.

<!-- hechos:app/news/sources.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/news/sources.html`](../api/app/news/sources.html)
- **Importa (internos):** ninguno
- **Lo importan:** `app.api.routes.news`, `app.news.collector`, `app.news.context`, `app.news.health`, `scripts.news_status`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_news_context.py`, `tests/unit/test_news_core.py`, `tests/unit/test_phase4_api_routes.py`
<!-- /hechos:app/news/sources.py -->

### `app/news/text.py`

**Responsabilidad.** Saneado y canonización de titulares y URLs, hashes de deduplicación y similitud.

**Funciones y clases clave.**
- `sanitize_headline()` — quita etiquetas HTML y caracteres de control o de formato y acota la longitud (`MAX_TITLE_CHARS`, 200).
- `canonical_url()`, `url_hash()` (SHA-256: la clave única `news_items.url_hash`), `title_tokens()`, `title_hash()`, `jaccard()` y
  `find_near_duplicate()`.

**Invariantes — no romper.** Limpiar un titular **no lo vuelve confiable**: cualquier prompt que lo use debe tratarlo como dato a
evaluar, jamás como instrucción.

**Trampas conocidas.** Quita parámetros de rastreo (`fbclid`, `gclid`…) para canonizar la URL.

<!-- hechos:app/news/text.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/news/text.html`](../api/app/news/text.html)
- **Importa (internos):** ninguno
- **Lo importan:** `app.api.routes.news`, `app.news.collector`, `app.news.context`, `app.news.parsers`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_news_core.py`
<!-- /hechos:app/news/text.py -->
