# Guía del paquete `indicators`

> Parte de la [documentación técnica](../README.md) · Referencia automática: [`api/app/indicators.html`](../api/app/indicators.html)

## Qué hace el paquete

Indicadores técnicos calculados con **pandas puro** (no se usa `pandas-ta`, por decisión de la Fase 0: mantenimiento
incierto). Todos son **causales**: el valor de una vela depende solo de esa vela y de las anteriores, y por eso se pueden
calcular una sola vez sobre toda la serie sin mirar al futuro.

## Zona protegida

**Sí.** Estos cálculos son los de las estrategias: cambiar una fórmula (o el modo de suavizado) cambia las señales que
nacen y, con ellas, la medición. Ver [ZONAS_PROTEGIDAS](../ZONAS_PROTEGIDAS.md#estrategias-y-parámetros).

## Cómo se conecta

Solo lo usan las estrategias (`app/strategies/`). Los indicadores que ve el LLM (`indicators_by_strategy` en sus
*features*) no se recalculan al armar el mensaje: se leen de la fila de `signals` que el generador de señales guardó.

## Archivos

### `app/indicators/__init__.py`

**Responsabilidad.** Solo el docstring del paquete.

**Funciones y clases clave.** Ninguna.

**Invariantes.** Ninguno.

**Trampas conocidas.** Ninguna.

<!-- hechos:app/indicators/__init__.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/indicators.html`](../api/app/indicators.html)
- **Importa (internos):** ninguno
- **Lo importan:** ninguno
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** ninguno
<!-- /hechos:app/indicators/__init__.py -->

### `app/indicators/engine.py`

**Responsabilidad.** Seis funciones puras sobre `pandas.Series` o `DataFrame`: `ema`, `rsi`, `sma`, `bollinger_bands`,
`donchian_channel` y `atr`.

**Funciones y clases clave.**
- `ema(series, span)` — media móvil exponencial con `adjust=False`.
- `rsi(series, period)` — RSI de Wilder (suavizado `ewm(alpha=1/period)`); las primeras `period` posiciones son NaN; vale
  100 si no hay pérdidas y sí ganancias, y 50 con el precio plano.
- `sma(series, period)` — media simple; las primeras `period − 1` posiciones son NaN.
- `bollinger_bands(series, period, num_std)` — `(superior, media, inferior)` con desviación estándar **poblacional**
  (`ddof=0`, la convención de Bollinger).
- `donchian_channel(df, period)` — máximo y mínimo de las últimas `period` velas, **la actual incluida** (la estrategia
  lo desplaza una vela, ver [strategies](strategies.md#appstrategiesdonchian_breakoutpy)).
- `atr(df, period)` — ATR de Wilder sobre el rango verdadero.

**Invariantes — no romper.**
- Causalidad: ningún valor puede depender de una vela futura. Es lo que permite que `precompute` de una estrategia dé
  **exactamente** el valor que daría recalcular vela a vela.
- Cada función lanza `ValueError` si el periodo es menor que 1 o falta una columna requerida.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/indicators/engine.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/indicators/engine.html`](../api/app/indicators/engine.html)
- **Importa (internos):** ninguno
- **Lo importan:** `app.strategies.donchian_breakout`, `app.strategies.ema_cross`, `app.strategies.mean_reversion_rsi_bb`, `app.strategies.trend_atr_stop`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_indicators.py`
<!-- /hechos:app/indicators/engine.py -->
