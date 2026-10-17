# Procedimiento del veredicto de valor de la IA (subfase 3.6)

Cómo producir, una sola vez, el veredicto de `docs/FASE3_6_LLM.md` sección (i)
cuando la medición en vivo junte **300 conglomerados efectivos por lado**. La
herramienta es `scripts/ai_value_report.py` (lógica en
`app/trading/ai_value_report.py`). Todo se hace sobre una **copia** de la base;
la base de trabajo del bot nunca se abre con estas herramientas.

## Regla de oro: una sola mirada

El punto de análisis es único. Antes de que ambos lados lleguen a 300 no se mira
ningún resultado, y la herramienta lo impone: con el gate cerrado
(`is_measurement_gate_open`, la misma función que protege la API y el dashboard)
solo imprime contadores, sin PnL, diferencia, IC ni veredicto, y sale con código 3.
El veredicto usa **todos** los datos de la copia en el momento de correr (no trunca
al instante exacto de los 300), por eso:

1. Corre el informe **apenas** el gate se abra (`gate_open` en el dashboard o en
   `scripts/measurement_status.py`).
2. Guárdalo con `--out` (registro oficial, UTF-8, no sobrescribe).
3. Volver a correr con más datos **no es un veredicto nuevo**: el registro oficial es
   el primero.

## Pasos

1. **Placebo primero** (sección f; lo exige el diseño antes de la primera decisión
   real). Sobre una copia consistente (paso 2), y registrando el resultado en
   `docs/FASE3_6_LLM.md`. Ver la sección "Placebo" más abajo:

   ```powershell
   .\.venv\Scripts\python.exe scripts\placebo_check.py --db <copia> --out docs\PLACEBO_REGISTRO.txt
   ```

   Debe decir `Calibrado (frecuencia < 5%): SI` y no mostrar la advertencia de
   "poco informativo". Si dice NO, el criterio no se usa para decidir hasta
   corregirlo. Tarda unos 12 minutos (déjalo corriendo en segundo plano).

2. **Copia consistente** con la API de backup de sqlite (nunca copiar el archivo: en
   modo WAL puede quedar inconsistente):

   ```powershell
   .\.venv\Scripts\python.exe -c "import sqlite3; src = sqlite3.connect('file:data/minerva_estabilidad.db?mode=ro', uri=True); dst = sqlite3.connect('data/backups/veredicto_copia.db'); src.backup(dst); dst.close(); src.close()"
   ```

3. **Informe, una sola vez**:

   ```powershell
   .\.venv\Scripts\python.exe scripts\ai_value_report.py --db data\backups\veredicto_copia.db --out docs\VEREDICTO_3_6_REGISTRO.txt
   ```

   Si el gate sigue cerrado, o si abrió pero tras excluir sombras no confiables
   (reconciliación fallida) y el piloto el N efectivo **limpio** sigue por debajo de
   300 en algún lado, el informe lo dice y no muestra nada (código 3); espera y
   repite. El archivo `--out` solo se crea cuando hay veredicto.

## Placebo (`scripts/placebo_check.py`)

Relabela al azar cada conglomerado con la tasa de aprobación observada y aplica la
misma `ai_value_verdict`, 1.000 veces; si el azar da APORTA_VALOR con frecuencia
≥ 5 %, el criterio no está calibrado (sección f). Usa **exactamente los mismos filtros
que el veredicto** (`load_verdict_inputs`: hash congelado, sin piloto, sin sombras no
confiables) y lee la copia en modo solo lectura. No revela nada de la IA (las
etiquetas reales se sustituyen por azar), así que puede correrse **antes** de que el
gate se abra, y debe registrarse antes de la primera decisión real.

**Cuándo es informativo.** Una repetición solo puede dar APORTA_VALOR si el azar deja
≥ 300 conglomerados en cada lado; con pocos conglomerados casi todas dan INCONCLUSO y
"Calibrado: SI" no prueba nada. El script imprime la probabilidad exacta (binomial) de
que una repetición llegue a ese punto, la tasa estimada contando solo las que llegan, y
una ADVERTENCIA si es < 50 %. Con 300 por lado y ~50 % de aprobación (≈ 600
conglomerados) casi nadie llega (solo X = 300 exacto): conviene correr el placebo con
bastante más datos que el mínimo, o repetirlo cuando los haya. Con 640 conglomerados y
50 % llegan ~90 %.

**Salida y registro.** Al final imprime un bloque listo para copiar a la sección (f) de
`docs/FASE3_6_LLM.md`. `--out` lo guarda completo en UTF-8 y no sobrescribe un archivo
existente (código 5); no se crea si no hay nada que calibrar.

**Tiempo medido** (2026-10-17, datos sintéticos, 640 conglomerados, 50 % de
aprobación, 1.000 repeticiones × 2.000 remuestreos, un solo proceso en un PC de
12 núcleos con otras aplicaciones abiertas): **694 s = 11,6 min** (0,69 s por
repetición); resultado 25/1000 por azar (2,5 %, IC de Wilson [1,7 %; 3,7 %]) →
calibrado. El costo crece con el número de conglomerados (cada repetición hace un
bootstrap sobre todos): esperable ~1 s por repetición (~17 min) con ~1.000.

| `--n-repeats` | tiempo (640 conglomerados) | IC de Wilson si la frecuencia fuera 2,5 % |
|---|---|---|
| 100 | ~1,1 min | [0,6 %; 7,0 %] |
| 300 | ~3,4 min | [1,4 %; 5,2 %] |
| 1.000 | ~11,5 min | [1,7 %; 3,7 %] |

**Recomendación:** para el **registro oficial** correr las 1.000 repeticiones del
diseño una sola vez, en segundo plano (≈ 12-20 min); con 300 el límite superior del IC
ya roza el 5 % y no permite afirmar que está por debajo. Para pruebas rápidas o
ensayos usar `--n-repeats 100`-`300`. **Propuesta si se quiere acelerar sin cambiar el
cálculo** (no implementada): pre-sortear en serie las etiquetas de todas las
repeticiones con la misma semilla (el consumo del generador es solo ese) y evaluar los
veredictos en un pool de procesos; como cada veredicto usa su propio generador del
bootstrap con semilla fija, el resultado sería idéntico al secuencial y el tiempo
bajaría casi lineal con los núcleos (12 núcleos: ~1-2 min). Exige replicar el bucle de
`placebo_calibration` (que no se toca), con una prueba de equivalencia exacta contra
ella.

## Qué contiene el informe

- **Veredicto principal** (`APORTA_VALOR` / `NO_APORTA_VALOR` / `INCONCLUSO`, marca
  `MAGNITUD_BAJA`): bootstrap por conglomerados sobre la fracción del margen,
  `MIN_EFFECTIVE_N=300`, δ = 3 %, tope de SIN_LLM del 10 % contado sobre los grupos
  de la medición (hash congelado), evaluado antes de mirar el IC.
- **Sección (m)**: partición de todos los grupos sombra (medición, piloto, otro hash,
  omitidos sin fila anteriores/posteriores a la activación, no confiables), todos los
  excluidos **fuera** del veredicto; SIN_LLM por causa, franja horaria UTC y tercil
  de `atr_pct`; perfil de los omitidos posteriores (lado, estrategia) frente a los
  medidos. La causa exacta de un omitido (demora > 1800 s, sombra cerrada antes de
  etiquetar, hash no coincidente) no se persiste, así que no se distingue.
- **Sensibilidad**: el mismo veredicto sin los grupos con JSON reparado; ROBUSTO si la
  categoría y la marca coinciden, SENSIBLE si no. Con menos de 300 de N efectivo es
  orientativa.
- **Desglose** por estrategia representativa y por símbolo, **solo si el veredicto es
  APORTA_VALOR**. Es exploratorio: no cambia el veredicto y no corrige comparaciones
  múltiples.

## Qué hacer con cada resultado

- **APORTA_VALOR**: revisar sensibilidad y desglose antes de cualquier decisión de
  activar la IA en la cuenta real (decisión aparte, no automática).
- **NO_APORTA_VALOR**: la IA no compensa su coste ni la dependencia de la API.
- **INCONCLUSO**: leer el motivo (SIN_LLM > 10 %, IC que cruza entre 0 y δ). Con
  `N = 300` por lado lo esperable si el efecto es pequeño; ver la sección (j).

## Qué NO hace

- No toca el bot real, `data/` (más allá de que tú crees la copia), `.env` ni el
  prompt congelado.
- Sin la bandera de pruebas no acepta nada que no sea la medición bajo el hash
  congelado real. `--allow-synthetic-for-tests` solo existe para las pruebas: se
  rechaza (código 4) si alguna fila de `llm_logs` tiene el modelo real o el hash
  real, o sea con cualquier copia de la base real.
- No ejecuta el placebo ni compara con la cuenta real (la sombra entra al precio de
  cierre de la vela, sin demora ni slippage).

## Códigos de salida

| Código | `ai_value_report.py` | `placebo_check.py` |
|---|---|---|
| 0 | veredicto impreso (y guardado si se pasó `--out`) | calibrado |
| 2 | error de uso (argparse) | error de uso (argparse) |
| 3 | rechazado: gate cerrado o punto de análisis no alcanzado | nada que calibrar todavía |
| 4 | bandera de pruebas rechazada: la base no es sintética | ídem |
| 5 | `--out` apunta a un archivo que ya existe | ídem |
| 6 | — | NO calibrado: el criterio no se usa para decidir |
