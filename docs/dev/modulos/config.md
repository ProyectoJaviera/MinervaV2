# Guía de `app/config.py`

> Parte de la [documentación técnica](../README.md) · Referencia automática: [`api/app/config.html`](../api/app/config.html)

## Qué hace

Un solo archivo con toda la configuración del bot (pydantic-settings). Cada variable de `.env` es un campo de la clase
`Settings` con su valor por defecto; el resto del código lee la instancia `settings` creada al final del módulo. La
referencia de **cada variable** (qué hace, valor por defecto y cuándo cambiarla) está en
[CONFIGURACION](../../CONFIGURACION.md); aquí está lo que hay que saber para tocar el código.

## Zona protegida

Algunas variables definen la medición (el modelo del LLM, el universo, el tope de SL, las comisiones y el apalancamiento
de las sombras...). La lista exacta, y por qué cada una no se puede cambiar mientras la medición corre, está en
[ZONAS_PROTEGIDAS](../ZONAS_PROTEGIDAS.md#parámetros-de-configuración-protegidos). Cambiar el **valor por defecto** de
un campo en este archivo cambia el comportamiento de todo despliegue que no fije esa variable.

## Cómo se conecta

- Se importa en casi todo el proyecto: `from app.config import settings` (o `Settings`, para recibirlo como parámetro).
- `.env.example` debe tener **todas** las variables y `docs/CONFIGURACION.md` debe describirlas: lo vigila
  `tests/unit/test_public_docs.py` (incluye que el valor por defecto documentado sea el del código).
- Los interruptores (`LLM_LIVE_LABELING_ENABLED`, `PAPER_EXECUTION_ENABLED`, `NEWS_COLLECTION_ENABLED`,
  `TELEGRAM_COMMANDS_ENABLED`, `TELEGRAM_PUSH_ENABLED`, `LESSONS_MEMORY_ENABLED`) están **apagados por defecto**: solo
  `scripts/start_bot.ps1` (puerto 8000) o el `docker-compose.yml` encienden los del bot real, y
  `scripts/run_dev_server.py` los fuerza a apagado (candado en el código, no depende del `.env`).

## Archivos

### `app/config.py`

**Responsabilidad.** Definir y validar la configuración: tipos, valores por defecto, alias de variable de entorno y las
propiedades derivadas (listas separadas por comas, fecha final del backtest en milisegundos).

**Funciones y clases clave.**
- `Settings` — un campo por variable, agrupados por tema (general, Bitunix, comisiones, capital, riesgo en vivo, vigencia
  de datos, monitor, criterios de dinero real, CoinGecko y universo, backtesting, Claude, noticias y memoria, Telegram,
  API del dashboard, base de datos). Cada campo se llena desde la variable de su `alias` (en mayúsculas) y, si no está,
  desde `.env` del **directorio de trabajo**; las variables del proceso ganan sobre el archivo.
- `Settings._validate_pct_units()` — falla al arrancar si un campo de riesgo en vivo `*_pct` está en la escala
  equivocada (`MAX_DRAWDOWN_PCT=20` en vez de `0.20`, o `LIVE_SL_MARGIN_CAP_PCT=0.5` en vez de `50.0`).
- Las propiedades `universe_exclude_categories_list`, `universe_manual_exclusions_list`,
  `backtest_control_symbols_list`, `real_account_eligible_strategies_list`, `news_sources_list` y
  `backtest_official_end_ms` convierten el texto del `.env` en listas o en milisegundos.
- `Settings.max_margin_for_new_trade()` — la fórmula del margen máximo de una operación nueva (el mínimo entre el margen
  configurado, el tope por activo y lo que cabe bajo el límite de posiciones); la usa el motor de riesgo.
- `settings` — la instancia única, creada **al importar** el módulo.

**Invariantes — no romper.**
- **Dos escalas para `*_pct`:** FRACCIÓN 0-1 (`0.20` = 20 %: comisiones, `MAX_DRAWDOWN_PCT`, `MAX_DAILY_LOSS_PCT`,
  `MAX_CAPITAL_PCT_PER_ASSET`, `TP_GAP_TOLERANCE_PCT`, los de backtesting y universo) y PORCENTAJE 0-100 (`50.0` = 50 %:
  `LIVE_SL_MARGIN_CAP_PCT`, `MAX_SL_MARGIN_LOSS_PCT`), porque se comparan con `sl_margin_loss_pct`, que ya viene en
  escala 0-100. Solo los de riesgo en vivo se validan al arrancar; los del backtest no, a propósito (los tests los usan
  «fuera de escala» para desactivar un tope).
- Ninguna credencial tiene un valor por defecto no vacío. En v1 no se usan credenciales de Bitunix (solo endpoints
  públicos de mercado).
- `ANTHROPIC_SONNET_MODEL` **no está cubierto por el hash congelado** (que cubre el prompt y las *features*): cambiar el
  modelo cambia la medición sin que nada lo avise; `scripts/measurement_identity.py` es el que lo detecta.
- `AUTO_OPEN_WITHOUT_LLM` se queda en `false` para siempre: las aperturas de la cuenta paper las hace el brazo ejecutado
  (`PAPER_EXECUTION_ENABLED`); con los dos en `true` el brazo se niega a armarse.

**Trampas conocidas.**
- `Settings()` lee `.env` del **directorio de trabajo** al importarse. Un script lanzado desde otra carpeta no encuentra
  el `.env` y arranca con los valores por defecto (por eso `scripts/telegram_alert.py`, `telegram_daily.py` y
  `resume_drawdown_stop.py` hacen `os.chdir` a la raíz del repositorio antes de importar `app`).
- `DATABASE_PATH` vale por defecto `./data/minerva.db`, una base que **ya no se escribe**; la del bot real la fija
  `scripts/start_bot.ps1` (nativo) o el `docker-compose.yml`. No uses `settings.database_path` para «la base real».
- `DEFAULT_MARGIN_USDT` bajó de 10 a 5; los resultados documentados de la Fase 2 se corrieron con 10 y no se tocan, pero
  un backtest nuevo sin fijar la variable usa 5.
- Fase 6 retiró campos que ningún módulo leía (`TRADING_SYMBOLS`, `KLINE_INTERVAL`, `STRATEGY_HISTORY_BARS`,
  `ACTIVE_STRATEGY`, `MARGIN_MODE`, `ANTHROPIC_HAIKU_MODEL`, `CONFLUENCE_SCORE_THRESHOLD`). Un `.env` que aún los tenga no
  falla (`extra="ignore"`), pero no hacen nada.
- Algunos comentarios de este archivo citan documentos con su ruta anterior a la reorganización de `docs/` (por
  ejemplo `docs/FASE3_PLAN.md`, hoy en `docs/historial/`); la información es correcta, la ruta no.
- La receta para agregar una variable está en [RECETAS](../RECETAS.md#agregar-una-variable-de-configuración).

<!-- hechos:app/config.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/config.html`](../api/app/config.html)
- **Importa (internos):** ninguno
- **Lo importan:** 61 módulos (api 11, backtesting 2, core 2, execution 1, llm 5, main 1, market 1, memory 3, news 3, scripts 19, telegram 4, trading 9)
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** 61 archivos (e2e 1, tests/ 1, unit 59)
<!-- /hechos:app/config.py -->
