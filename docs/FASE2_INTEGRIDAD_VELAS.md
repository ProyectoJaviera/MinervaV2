# Integridad de velas 1m/1h/4h/1d -- incidente de estabilidad (2026-10-10)

Investigación y corrección de la Etapa 1 pedida tras el incidente de la prueba de
estabilidad (3 sombras con reconciliación fallida al reiniciar tras ~5,75 días
apagado). Diagnóstico completo del incidente en el historial de la conversación;
este documento se concentra en la causa de las velas 1m incompletas, la corrección
implementada, y el efecto cuantificado sobre los datos de la Fase 2.

## 1. Semántica de `endTime` verificada empíricamente

Antes de tocar la paginación, se verificó contra la API pública real de Bitunix
(lectura, sin credenciales, con `BitunixRestClient.get_kline` tal cual está en el
repo) qué vela queda como la "última" para distintos `endTime`:

| Consulta | `endTime` | Última vela devuelta |
|---|---|---|
| Q1 | alineado a una vela (`T`) | `T - 1 paso` (la vela `T` queda AFUERA) |
| Q2 | `T + 30s` (a mitad de la vela `T`) | `T` |
| Q3 | `T + 59,999s` (1 ms antes de que `T` cierre) | `T` |
| Q4 | `T + 1 paso` (alineado a la vela siguiente) | `T` |

Los cuatro resultados son consistentes solo con una regla: **`endTime` es
estrictamente EXCLUSIVO sobre `open_time`** (`open_time < endTime`). No es ni
inclusivo sobre `open_time`, ni depende del cierre de la vela (`open_time +
duración <= endTime` queda descartado por Q3: la vela `T`, que a esa hora todavía
no "cerró" en ese sentido, sí se devuelve).

**Bajo esta semántica confirmada, `cursor = oldest_time - 1` en `_download_range`
(app/market/ohlcv_history.py) ya era matemáticamente correcto** -- excluye
exactamente la vela ya guardada e incluye la anterior. No se cambió esa fórmula.

## 2. El hallazgo real: Bitunix omite una vela real en algunas respuestas anchas

Con la fórmula de paginación descartada como causa, se reprodujo la descarga real
(`download_missing`) contra la API en vivo, con una base temporal vacía (no se tocó
ninguna base del proyecto para esto). El patrón de huecos de 1 minuto, aislados,
cada ~200 minutos, se reprodujo de inmediato.

Rastreando página por página: **las costuras entre páginas sucesivas nunca tienen
hueco** (`seam_gap=False` en las 5 páginas trazadas). El hueco aparece DENTRO de una
sola respuesta de `limit=200` sin `startTime` -- exactamente el patrón de
`_download_range`. Verificado en vivo, tres veces:

- La misma vela, pedida con una ventana chica (`limit` bajo, con o sin
  `startTime`), **SÍ está presente**.
- La misma vela, pedida con `limit=200` y sin `startTime` (el patrón real de
  `_download_range`), **puede faltar**.
- Agregar `startTime` a la llamada ancha (`limit=200`) **no lo arregla** -- el
  mismo rango, exactamente la misma vela ausente.

No se encontró una regla determinista para predecir cuándo pasa, ni documentación
oficial de Bitunix disponible sobre esto (no se pudo verificar más allá de la propia
observación empírica, repetida). Conclusión: **no es un bug de nuestra fórmula de
paginación -- es un comportamiento de Bitunix al servir respuestas grandes**, que no
se puede corregir ajustando los parámetros de la llamada.

## 3. Corrección: verificar y reparar, no confiar solo en la paginación

En vez de perseguir una fórmula de paginación "perfecta" (no existe una que evite el
comportamiento de Bitunix), `app/market/ohlcv_history.py` agrega:

- **`find_gaps(open_times, step_ms, start=None, end=None)`**: huecos en una serie
  cacheada, puro, sin red.
- **`fill_gaps(client, db, symbol, interval, price_type, gap_times, step_ms)`**:
  repara huecos puntuales con pedidos ESTRECHOS (agrupando huecos contiguos en una
  sola llamada). Devuelve lo que de verdad se recuperó -- si ni con ventana chica
  aparece, no lo inventa, lo deja y lo reporta.
- **`download_missing`** llama a `_verify_and_repair` al final (haya tocado la red o
  no): verifica densidad y repara lo que falte. Una cache vieja con huecos de antes
  se autorepara en la siguiente llamada normal, no solo con una reparación manual.

Tests en `tests/unit/test_ohlcv_history.py`: `find_gaps`/`fill_gaps` puros, y un
cliente falso (`FlakyRestClient`) que simula el hallazgo real (respuesta ancha omite
una vela, estrecha no) para 1m, 1h, 4h y 1d -- confirmando que `download_missing`
termina sin huecos en los cuatro casos, y que un hueco genuinamente irreparable (la
vela nunca existe, ni estrecho) no hace que `download_missing` lance.

## 4. Reparación de los 3 símbolos del incidente (sobre una copia)

`scripts/repair_ohlcv_gaps.py` corrido sobre una COPIA de
`data/minerva_estabilidad.db` (nunca la base de trabajo, ruta verificada con
assert):

| Símbolo | `price_type` | Huecos | Reparados |
|---|---|---|---|
| DOGEUSDT | LAST_PRICE / MARK_PRICE | 40 / 40 | 40 / 40 |
| TRXUSDT | LAST_PRICE / MARK_PRICE | 41 / 41 | 41 / 41 |
| XRPUSDT | LAST_PRICE / MARK_PRICE | 41 / 41 | 41 / 41 |

**Total: 244 huecos, 244 reparados, 0 sin reparar.** Verificado de forma
independiente (consulta SQL directa, sin reusar `find_gaps`): 0 huecos en las 6
series tras la reparación.

**Confirmación de punta a punta:** se reconstruyó el escenario exacto del incidente
(`PositionMonitor._reconcile_trade` para la sombra TRXUSDT todavía abierta, con la
ventana completa de 5,75 días) sobre la copia ya reparada. Resultado: `"ABIERTA"` --
reconcilió con velas (`CANDLE_RECON`) sin lanzar, en vez del `CRITICAL`/`"ERROR"`
original. La reparación resuelve el incidente en el caso real.

## 5. Huecos en las series 1h/4h/1d de la Fase 2, y su efecto

Cuantificado sobre `data/backups/minerva_fase2_v2.db` (solo lectura, `mode=ro`): 60
combinaciones símbolo/intervalo/`price_type`, **236 huecos en total**, concentrados
en 2 de los 10 símbolos:

| Símbolo | Intervalo | Huecos (LAST+MARK) |
|---|---|---|
| ZECUSDT | 1h | 142 |
| ZECUSDT | 4h | 34 |
| ZECUSDT | 1d | 4 |
| LINKUSDT | 4h | 40 |
| DOGEUSDT | 1h | 8 |
| BNBUSDT | 4h | 6 |
| TRXUSDT | 4h | 2 |

**Son de una naturaleza distinta a los del incidente de estabilidad.** No son
aislados de 1 vela: son bloques contiguos (el de ZECUSDT 1h empieza con al menos 5
horas seguidas desde 2023-09-12 17:00; el de LINKUSDT 4h son 20 velas seguidas, ~3,3
días, desde 2023-10-01). Se verificó en vivo contra la API real: pedido directamente
ese rango de ZECUSDT, **Bitunix hoy tampoco devuelve esas velas** -- es un hueco real
y permanente del historial del exchange para ese período (probablemente una
simbología/par más chica, de 2023), no el hallazgo de la sección 2.

**`get_cached_or_raise` (el que usa el motor de backtest) no los detecta**: su
chequeo de integridad solo verifica que la CABEZA y la COLA del rango pedido estén
cubiertas, no que no haya huecos INTERNOS -- por diseño no revisa densidad. Por eso
estos 236 huecos nunca hicieron fallar la Fase 2 con `MissingHistoricalDataError`:
el motor corrió con esas velas simplemente ausentes de la serie, sin aviso.

**Efecto estimado (subconjunto representativo, sin relanzar el backtest completo):**
se corrió `run_backtest` + `compute_metrics` para ZECUSDT en sus 7 combinaciones de
estrategia/timeframe afectadas (las que ya reporta `docs/FASE2_REEJECUCION.md`),
sobre una copia, antes y después de intentar `fill_gaps`. `fill_gaps` repara 0 de los
77 huecos de ZECUSDT (son el hueco real de la sección 5, no el de la sección 2), así
que el resultado "antes" y "después" es **idéntico** -- no hay nada que recalcular
porque no hay nada reparable. Los resultados de ZECUSDT en los 7 casos (PF entre 0,70
y 1,13, con solo 1 a 474 operaciones según la celda) ya estaban fuera de los
criterios de aprobación por otras razones documentadas en
`docs/FASE2_REEJECUCION.md`; estos huecos de 2023 no cambian ninguna conclusión de
la Fase 2 (ninguna estrategia pasa los criterios, con o sin este dato).

**Pendiente de tu decisión, no implementado:** si vale la pena que
`get_cached_or_raise`/el motor de backtest detecten y reporten huecos INTERNOS (no
solo de borde) para que una futura corrida los muestre explícitamente en vez de
corrér en silencio con menos velas de las esperadas. No se tocó el motor de backtest
ni se volvió a correr la grilla completa (se pidió no hacerlo sin consultar).
