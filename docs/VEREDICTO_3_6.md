# Procedimiento del veredicto de valor de la IA (subfase 3.6)

Cómo producir, una sola vez, el veredicto de `docs/FASE3_6_LLM.md` sección (i)
cuando la medición en vivo junte **300 conglomerados efectivos por lado**. La
herramienta es `scripts/ai_value_report.py` (lógica en
`app/trading/ai_value_report.py`). Todo se hace sobre una **copia** de la base;
la base de trabajo del bot nunca se abre con estas herramientas.

## Regla de procedimiento (decisión de Renzo, 2026-10-17, tomada ANTES de ver resultados)

Cuando el gate se abra:

1. **Hacer la copia** consistente de la base (una sola, la que se usará en todo).
2. **Correr el placebo oficial** sobre esa copia, con 1.000 repeticiones.
3. Si el placebo sale **NO INFORMATIVO** (menos del 50 % de las repeticiones llega al
   punto de análisis): **NO correr el veredicto.** Esperar **7 días más de datos** y
   repetir desde (1) con una copia nueva.
4. Si sale **CALIBRADO**: correr `ai_value_report.py` **una sola vez**, con `--out`,
   sobre **esa misma copia**.
5. Si sale **NO calibrado**: el criterio no se usa para decidir; se revisa el diseño.

**La espera depende solo de conteos (probabilidad de llegar al punto de análisis, N
efectivo), nunca de resultados.** Por eso la regla no abre la puerta a mirar y repetir
hasta que salga algo conveniente: nada de lo que decide cuándo repetir depende de PnL ni
de la diferencia entre APROBADA y RECHAZADA. El texto final de `ai_value_report.py`
repite este orden cuando imprime un veredicto.

## Regla de oro: una sola mirada

El punto de análisis es único. Antes de que ambos lados lleguen a 300 no se mira
ningún resultado, y la herramienta lo impone: con el gate cerrado
(`is_measurement_gate_open`, la misma función que protege la API y el dashboard)
solo imprime contadores, sin PnL, diferencia, IC ni veredicto, y sale con código 3.
El veredicto usa **todos** los datos de la copia en el momento de correr (no trunca
al instante exacto de los 300), por eso:

1. Sigue el orden de la regla de procedimiento de arriba **apenas** el gate se abra
   (`gate_open` en el dashboard o en `scripts/measurement_status.py`).
2. Guarda el informe con `--out` (registro oficial, UTF-8, no sobrescribe).
3. Volver a correr con más datos **no es un veredicto nuevo**: el registro oficial es
   el primero.

## Pasos

1. **Copia consistente** con la API de backup de sqlite (nunca copiar el archivo: en
   modo WAL puede quedar inconsistente):

   ```powershell
   .\.venv\Scripts\python.exe -c "import sqlite3; src = sqlite3.connect('file:data/minerva_estabilidad.db?mode=ro', uri=True); dst = sqlite3.connect('data/backups/veredicto_copia.db'); src.backup(dst); dst.close(); src.close()"
   ```

2. **Placebo oficial** (sección f; lo exige el diseño antes de la primera decisión
   real) sobre esa copia, con 1.000 repeticiones (el valor por defecto). Con una fecha en
   el nombre, porque `--out` no sobrescribe y un resultado NO INFORMATIVO también se
   guarda. **Nombre propio del placebo, que NO cumple el patrón del registro del
   veredicto** (si lo cumpliera, desbloquearía los resultados sin veredicto):

   ```powershell
   .\.venv\Scripts\python.exe scripts\placebo_check.py --db data\backups\veredicto_copia.db --out docs\PLACEBO_2026-12-05.txt
   ```

   Tarda unos 12 minutos (déjalo corriendo en segundo plano). Pega el bloque final en
   `docs/FASE3_6_LLM.md`, sección (f). Código de salida: 0 CALIBRADO, 7 NO INFORMATIVO
   (→ esperar 7 días y repetir desde el paso 1), 6 NO calibrado (→ no se usa el criterio).

3. **Solo si el placebo salió CALIBRADO**, el informe, una sola vez y sobre la misma
   copia. El nombre del `--out` **debe** cumplir el patrón
   `docs\VEREDICTO_3_6_REGISTRO*.txt` (con fecha): ese archivo es el **registro oficial**
   que desbloquea los resultados en la API y el dashboard (ver la sección siguiente):

   ```powershell
   .\.venv\Scripts\python.exe scripts\ai_value_report.py --db data\backups\veredicto_copia.db --out docs\VEREDICTO_3_6_REGISTRO_2026-12-05.txt
   ```

   Si el gate sigue cerrado, o si abrió pero tras excluir sombras no confiables
   (reconciliación fallida) y el piloto el N efectivo **limpio** sigue por debajo de
   300 en algún lado, el informe lo dice y no muestra nada (código 3); espera y
   repite. El archivo `--out` solo se crea cuando hay veredicto.

## Cuándo se ven los resultados (API, dashboard y reportes)

Una sola condición, `is_outcome_reveal_allowed` (`app/trading/measurement_status.py`):

> **gate abierto Y existe un registro oficial del veredicto**: un archivo
> `docs/VEREDICTO_3_6_REGISTRO*.txt` que contiene la línea `Veredicto: <X>` del informe
> con `X` = `APORTA_VALOR`, `NO_APORTA_VALOR` o `INCONCLUSO`.

Abrir el gate **no basta**: si el placebo sale NO INFORMATIVO se esperan 7 días y en ese
tiempo no se deben ver resultados. La usan `GET /api/shadow-trades/recent` (campos de
resultado: `pnl_*`, `exit_price`, `close_reason`, `closed_at`), el dashboard y
`scripts/shadow_report.py`; `GET /api/measurement/status` expone `gate_open` y
`outcome_reveal_allowed` por separado.

| Gate | Registro oficial | API / dashboard / `shadow_report.py` |
|---|---|---|
| cerrado | (cualquiera) | nada; el dashboard explica que faltan los 300 conglomerados |
| abierto | no existe, o sin un veredicto válido | nada; el dashboard dice **"Gate abierto: seguir docs/VEREDICTO_3_6.md"** |
| abierto | válido | se muestran los resultados |

El registro se lee en cada petición: aparece el archivo y los resultados se desbloquean
**sin reiniciar el bot**. Un archivo vacío, ilegible, de más de 1 MB, sin la línea
`Veredicto: <X>`, o con el nombre del placebo (`PLACEBO_<fecha>.txt`) **no cuenta**
(falla cerrado). El dashboard además ignora cualquier campo de resultado si
`outcome_reveal_allowed` no es `true` (también con un backend viejo sin el campo).
Para volver a ocultar los resultados basta con mover o renombrar el registro.

## Placebo (`scripts/placebo_check.py`)

Relabela al azar cada conglomerado con la tasa de aprobación observada y aplica la
misma `ai_value_verdict`, 1.000 veces (sección f). Usa **exactamente los mismos filtros
que el veredicto** (`load_verdict_inputs`: hash congelado, sin piloto, sin sombras no
confiables) y lee la copia en modo solo lectura. No revela nada de la IA (las
etiquetas reales se sustituyen por azar), así que puede correrse **antes** de que el
gate se abra, y debe registrarse antes de la primera decisión real.

**Criterio de "Calibrado" (decisión del 2026-10-17, antes de ver datos).** Una repetición
solo puede dar APORTA_VALOR si el azar deja ≥ 300 conglomerados en cada lado; con pocos
conglomerados casi todas dan INCONCLUSO y una frecuencia bruta baja no prueba nada. Por
eso:

- Se calcula la probabilidad exacta (binomial) de que una repetición llegue a ese punto.
- Si es **< 50 %**: **NO INFORMATIVO** (ni SI ni NO), código de salida 7.
- Si es **≥ 50 %**: se divide la frecuencia de APORTA_VALOR por azar entre esa
  probabilidad (tasa condicional) y **CALIBRADO** si es **< 5 %** (código 0); si no,
  **NO calibrado** (código 6).
- La frecuencia bruta y su IC de Wilson se siguen imprimiendo, rotulados
  `[informativo]`, pero ya no deciden. Ejemplo: frecuencia bruta 4,8 % con probabilidad
  de llegar 80 % → tasa condicional 6 % → NO calibrado, aunque la bruta sea < 5 %.

Con 300 por lado y ~50 % de aprobación (≈ 600 conglomerados) casi nadie llega (solo
X = 300 exacto): el placebo saldrá NO INFORMATIVO y se esperarán 7 días más de datos (la
regla de procedimiento de arriba). Con 640 conglomerados y 50 % llegan ~90 %.

**Salida y registro.** Al final imprime un bloque listo para copiar a la sección (f) de
`docs/FASE3_6_LLM.md`. `--out` lo guarda completo en UTF-8 y no sobrescribe un archivo
existente (código 5). Se guarda también un resultado NO INFORMATIVO o NO calibrado
(son resultados del procedimiento); no se crea si no hay nada que calibrar.

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
| 0 | veredicto impreso (y guardado si se pasó `--out`) | CALIBRADO |
| 2 | error de uso (argparse) | error de uso (argparse) |
| 3 | rechazado: gate cerrado o punto de análisis no alcanzado | nada que calibrar todavía |
| 4 | bandera de pruebas rechazada: la base no es sintética | ídem |
| 5 | `--out` apunta a un archivo que ya existe | ídem |
| 6 | — | NO calibrado: el criterio no se usa para decidir |
| 7 | — | NO INFORMATIVO: no correr el veredicto; esperar 7 días de datos y repetir |
