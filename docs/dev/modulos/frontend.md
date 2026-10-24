# Guía de `frontend/src`

> Parte de la [documentación técnica](../README.md) · No tiene referencia pdoc (es TypeScript): el código y las pruebas junto a cada archivo son la referencia.

## Qué hace

El dashboard: una página de React 18 con TypeScript, React Query 5 y Tailwind 4 (Vite 5; pruebas con Vitest y Testing Library). Es una **vista de solo lectura** de la API más tres controles (pausar, reanudar y
kill switch). Lo compila `npm --prefix frontend run build` en `frontend/dist`, que el backend sirve en `/` si existe. Hay **cinco pestañas**: *Resumen*, *Cuenta paper*, *Brazo ejecutado*,
*Riesgo y controles* y *Universo y backtest*; cada una pide sus datos solo cuando se abre. Muestra un banner permanente «MODO PAPER TRADING».

## Zona protegida

**No** para el diseño del dashboard (se puede cambiar con libertad), con tres reglas que **no se pueden relajar**: (1) **falla cerrado**: el cliente nunca calcula resultados (PnL, saldo, equity,
motivo de cierre) ni los muestra si el servidor no los manda explícitamente; (2) los titulares de noticias son texto **no confiable** (texto plano, enlace solo `http(s)`); (3) la atribución de
CoinGecko debe verse junto a sus datos. Ver [Ocultamiento de resultados](../ZONAS_PROTEGIDAS.md#ocultamiento-de-resultados) y la receta
[Agregar una pestaña del dashboard](../RECETAS.md#agregar-una-pestaña-del-dashboard).

## Cómo se conecta

- `main.tsx` monta `App` dentro de un `QueryClientProvider` (por defecto `refetchInterval` de 45 s y un reintento).
- `api.ts` es la **única** capa de datos: `apiFetch` envía la cookie de sesión (`credentials: "include"`) y reacciona a un 401; un hook `use*` por endpoint. Los tipos TypeScript **reflejan exactamente**
  lo que devuelve cada endpoint: si cambias un contrato del backend, cambia también el tipo y su prueba.
- En desarrollo, `vite.config.ts` hace *proxy* de `/api` y `/health` al **puerto 8001** (servidor de desarrollo con una copia de la base), nunca al bot real del 8000.
- Un 401 de cualquier consulta limpia la sesión escribiendo la caché de `me` (`clearSession`), sin refetch.

## Archivos de configuración (fuera de `src/`)

| Archivo | Para qué |
|---|---|
| `frontend/package.json` | Scripts: `dev`, `build` (`tsc --noEmit && vite build`), `preview`, `typecheck` y `test` (`vitest run`). Dependencias de ejecución: `react`, `react-dom` y `@tanstack/react-query`; el resto son de desarrollo |
| `frontend/vite.config.ts` | Plugins de React y Tailwind y el *proxy* al puerto 8001 |
| `frontend/vitest.config.ts` | Entorno `jsdom`, `globals: true` y `src/setupTests.ts` |
| `frontend/tsconfig.json` | Modo `strict`, `noEmit`, solo compila `src` |
| `frontend/index.html` | La página que carga `/src/main.tsx` |
| `frontend/src/index.css` | `@import "tailwindcss"` y la variante `dark` por clase |

## Archivos

### `frontend/src/AccountTab.tsx`

**Responsabilidad.** La pestaña *Cuenta paper*: posiciones abiertas y cerradas de la cuenta.

**Funciones y clases clave.**
- `AccountTab` (consulta `GET /api/account`) y `TradeTable`.

**Invariantes — no romper.** Estructura (símbolo, lado, estrategia, niveles, horas) siempre; precio de salida, motivo de cierre, PnL y saldo **solo** si el servidor dice `reveal_allowed` **y** manda los campos. Falla
cerrado: sin ambas cosas no se muestra nada de resultados, y el cliente nunca los calcula.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:frontend/src/AccountTab.tsx -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Importa (de `frontend/src/`):** `frontend/src/api.ts`, `frontend/src/components/Card.tsx`, `frontend/src/components/StatTile.tsx`, `frontend/src/components/Unavailable.tsx`, `frontend/src/format.ts`
- **Lo importan:** `frontend/src/DashboardView.tsx`
- **Rutas HTTP que cita:** ninguno
- **Pruebas:** `frontend/src/AccountTab.test.tsx`
<!-- /hechos:frontend/src/AccountTab.tsx -->

### `frontend/src/App.tsx`

**Responsabilidad.** La raíz: banner de modo paper, modo claro u oscuro y la decisión entre `LoginView` y `DashboardView`.

**Funciones y clases clave.**
- `App`; `useDarkMode` (guarda la preferencia en `localStorage` con `try/catch`, y si no hay, usa `prefers-color-scheme`) y el registro del manejador de 401.

**Invariantes — no romper.** El banner «MODO PAPER TRADING -- ninguna orden real se envia a Bitunix» siempre visible. `DashboardView` nunca se monta hasta confirmar la sesión: por eso sus consultas no
necesitan `enabled`.

**Trampas conocidas.** `jsdom` no implementa `matchMedia`: `setupTests.ts` lo simula.

<!-- hechos:frontend/src/App.tsx -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Importa (de `frontend/src/`):** `frontend/src/DashboardView.tsx`, `frontend/src/LoginView.tsx`, `frontend/src/api.ts`
- **Lo importan:** `frontend/src/main.tsx`
- **Rutas HTTP que cita:** ninguno
- **Pruebas:** `frontend/src/App.test.tsx`
<!-- /hechos:frontend/src/App.tsx -->

### `frontend/src/ArmTab.tsx`

**Responsabilidad.** La pestaña *Brazo ejecutado*: cada decisión APROBADA que el brazo consideró, abierta u omitida, con su motivo.

**Funciones y clases clave.**
- `ArmTab` (consulta `GET /api/arm`).

**Invariantes — no romper.** Nunca trae resultados: los detalles ya pasan por una lista blanca en el servidor.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:frontend/src/ArmTab.tsx -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Importa (de `frontend/src/`):** `frontend/src/api.ts`, `frontend/src/components/Card.tsx`, `frontend/src/components/StatTile.tsx`, `frontend/src/components/Unavailable.tsx`, `frontend/src/format.ts`
- **Lo importan:** `frontend/src/DashboardView.tsx`
- **Rutas HTTP que cita:** ninguno
- **Pruebas:** `frontend/src/ArmTab.test.tsx`
<!-- /hechos:frontend/src/ArmTab.tsx -->

### `frontend/src/DashboardView.tsx`

**Responsabilidad.** El dashboard con sus cinco pestañas y el aviso de medición detenida.

**Funciones y clases clave.**
- `DashboardView`, la lista `TABS` y `ActiveTab` (devuelve el componente de la pestaña activa; por defecto *Resumen*).

**Invariantes — no romper.** Si el scheduler está en pausa o el kill switch activo (de `GET /health`), el aviso `role="alert"` se ve en **todas** las pestañas: pausar **detiene la medición**. Las pruebas leen
las etiquetas de `TABS` y la documentación de uso las contrasta con el código.

**Trampas conocidas.** El pie lleva la atribución de CoinGecko.

<!-- hechos:frontend/src/DashboardView.tsx -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Importa (de `frontend/src/`):** `frontend/src/AccountTab.tsx`, `frontend/src/ArmTab.tsx`, `frontend/src/RiskTab.tsx`, `frontend/src/SummaryTab.tsx`, `frontend/src/UniverseTab.tsx`, `frontend/src/api.ts`, `frontend/src/components/Attribution.tsx`, `frontend/src/components/Tabs.tsx`
- **Lo importan:** `frontend/src/App.tsx`
- **Rutas HTTP que cita:** ninguno
- **Pruebas:** `frontend/src/DashboardView.test.tsx`
<!-- /hechos:frontend/src/DashboardView.tsx -->

### `frontend/src/LoginView.tsx`

**Responsabilidad.** El formulario de inicio de sesión.

**Funciones y clases clave.**
- `LoginView` (usa `useLogin`).

**Invariantes — no romper.** No guarda nada sensible en el cliente: la sesión es una cookie `httpOnly` del servidor.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:frontend/src/LoginView.tsx -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Importa (de `frontend/src/`):** `frontend/src/api.ts`
- **Lo importan:** `frontend/src/App.tsx`
- **Rutas HTTP que cita:** ninguno
- **Pruebas:** ninguno
<!-- /hechos:frontend/src/LoginView.tsx -->

### `frontend/src/NewsMemoryCard.tsx`

**Responsabilidad.** La tarjeta de noticias recolectadas y memoria de lecciones del *Resumen*.

**Funciones y clases clave.**
- `NewsMemoryCard`, `safeHref()` (el enlace solo se usa si es `http(s)`) y `fmtSentiment()`.

**Invariantes — no romper.**
- Los titulares son **texto no confiable**: siempre texto plano (React lo escapa); las atribuciones de las fuentes se muestran junto a ellos.
- Las lecciones llevan tasas de acierto y retornos: **falla cerrado**, solo se piden (`useMemoryLessons(enabled)`) y se muestran si el backend dice explícitamente que los resultados son visibles.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:frontend/src/NewsMemoryCard.tsx -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Importa (de `frontend/src/`):** `frontend/src/api.ts`, `frontend/src/components/Card.tsx`, `frontend/src/components/StatTile.tsx`, `frontend/src/format.ts`
- **Lo importan:** `frontend/src/SummaryTab.tsx`
- **Rutas HTTP que cita:** ninguno
- **Pruebas:** `frontend/src/NewsMemoryCard.test.tsx`
<!-- /hechos:frontend/src/NewsMemoryCard.tsx -->

### `frontend/src/RiskTab.tsx`

**Responsabilidad.** La pestaña *Riesgo y controles*: estado de riesgo y los tres controles.

**Funciones y clases clave.**
- `RiskTab` (consulta `GET /api/risk`, `GET /api/control/info` y `GET /api/control/audit`; ejecuta con `useControlAction`).

**Invariantes — no romper.**
- El estado (pausa, kill, breaker, stop por drawdown, pérdida diaria, cupos) siempre; la racha de pérdidas y los cruces del drawdown (derivados de resultados) **solo** si el servidor manda `numbers`.
- Los controles **detienen la medición**: piden confirmación explícita con la palabra exacta y la advertencia sale **del servidor** (la misma que Telegram). El servidor la vuelve a exigir: el diálogo no es la única defensa.

**Trampas conocidas.** Tras una acción se invalidan las consultas `risk`, `health`, `control-info` y `control-audit`.

<!-- hechos:frontend/src/RiskTab.tsx -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Importa (de `frontend/src/`):** `frontend/src/api.ts`, `frontend/src/components/Card.tsx`, `frontend/src/components/ConfirmDialog.tsx`, `frontend/src/components/StatTile.tsx`, `frontend/src/components/Unavailable.tsx`, `frontend/src/format.ts`
- **Lo importan:** `frontend/src/DashboardView.tsx`
- **Rutas HTTP que cita:** ninguno
- **Pruebas:** `frontend/src/RiskTab.test.tsx`
<!-- /hechos:frontend/src/RiskTab.tsx -->

### `frontend/src/SummaryTab.tsx`

**Responsabilidad.** La pestaña *Resumen*: salud, costo del LLM, avance de la medición hacia los 300 por lado, noticias y memoria, decisiones del LLM y sombras.

**Funciones y clases clave.**
- `SummaryTab`; `pushText()` (estado de las notificaciones de Telegram).

**Invariantes — no romper.** Solo conteos: nunca PnL. Las causas no OK (`TIMEOUT`, `ERROR_HTTP`, `INVALID`, `BUDGET_EXCEEDED`) se muestran con etiquetas legibles.

**Trampas conocidas.** Es lo que antes era todo el dashboard; las demás pestañas nacieron en la Fase 5.

<!-- hechos:frontend/src/SummaryTab.tsx -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Importa (de `frontend/src/`):** `frontend/src/NewsMemoryCard.tsx`, `frontend/src/api.ts`, `frontend/src/components/BandIndicator.tsx`, `frontend/src/components/Card.tsx`, `frontend/src/components/ProgressBar.tsx`, `frontend/src/components/StatTile.tsx`, `frontend/src/format.ts`
- **Lo importan:** `frontend/src/DashboardView.tsx`
- **Rutas HTTP que cita:** ninguno
- **Pruebas:** ninguno
<!-- /hechos:frontend/src/SummaryTab.tsx -->

### `frontend/src/UniverseTab.tsx`

**Responsabilidad.** La pestaña *Universo y backtest*: el universo vigente y los veredictos de backtest por estrategia.

**Funciones y clases clave.**
- `UniverseTab` y `UniverseCard` (`GET /api/universe`, `GET /api/backtest/verdicts`).

**Invariantes — no romper.** La atribución de CoinGecko («Data provided by CoinGecko», enlazada) va junto a la tabla. Los veredictos son del backtest histórico de la Fase 2, **no** de la medición en vivo.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:frontend/src/UniverseTab.tsx -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Importa (de `frontend/src/`):** `frontend/src/api.ts`, `frontend/src/components/Attribution.tsx`, `frontend/src/components/Card.tsx`, `frontend/src/components/StatTile.tsx`, `frontend/src/components/Unavailable.tsx`, `frontend/src/format.ts`
- **Lo importan:** `frontend/src/DashboardView.tsx`
- **Rutas HTTP que cita:** ninguno
- **Pruebas:** `frontend/src/UniverseTab.test.tsx`
<!-- /hechos:frontend/src/UniverseTab.tsx -->

### `frontend/src/api.ts`

**Responsabilidad.** La capa de datos: el cliente HTTP, los tipos de cada respuesta y un hook de React Query por endpoint.

**Funciones y clases clave.**
- `apiFetch` (privada; la cookie de sesión y el 401) y `apiPost`; `ApiError` (con el `status` y el `detail` del servidor).
- `useMe`, `useLogin`, `useLogout`, `clearSession`, `useHealth`, `useMeasurementStatus`, `useRecentDecisions`, `useRecentShadows`, `useNewsStatus`, `useNewsRecent`, `useMemoryStatus`, `useMemoryLessons`, `useAccount`, `useArm`,
  `useRisk`, `useUniverse`, `useBacktestVerdicts`, `useControlInfo`, `useControlAudit` y `useControlAction`.

**Invariantes — no romper.** Un solo punto de paso para la cookie `httpOnly` y la reacción a un 401; nunca se guarda nada sensible en el cliente. Los tipos reflejan **exactamente** las formas de los endpoints.

**Trampas conocidas.** Ante un 401 la caché de `me` se escribe a mano (`clearSession`): un error de React Query conserva el último `data` válido y dejaría la interfaz con la sesión vieja.

<!-- hechos:frontend/src/api.ts -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Importa (de `frontend/src/`):** ninguno
- **Lo importan:** `frontend/src/AccountTab.tsx`, `frontend/src/App.tsx`, `frontend/src/ArmTab.tsx`, `frontend/src/DashboardView.tsx`, `frontend/src/LoginView.tsx`, `frontend/src/NewsMemoryCard.tsx`, `frontend/src/RiskTab.tsx`, `frontend/src/SummaryTab.tsx`, `frontend/src/UniverseTab.tsx`, `frontend/src/components/Unavailable.tsx`
- **Rutas HTTP que cita:** `/api/account`, `/api/arm`, `/api/auth/login`, `/api/auth/logout`, `/api/auth/me`, `/api/backtest/verdicts`, `/api/control/${action}`, `/api/control/audit`, `/api/control/info`, `/api/llm/decisions/recent`, `/api/measurement/status`, `/api/memory/lessons`, `/api/memory/status`, `/api/news/recent`, `/api/news/status`, `/api/risk`, `/api/shadow-trades/recent`, `/api/universe`, `/health`
- **Pruebas:** ninguno
<!-- /hechos:frontend/src/api.ts -->

### `frontend/src/components/Attribution.tsx`

**Responsabilidad.** La atribución obligatoria de CoinGecko.

**Funciones y clases clave.**
- `CoinGeckoAttribution`, `COINGECKO_TEXT` y `COINGECKO_URL`.

**Invariantes — no romper.** «Data provided by CoinGecko», enlazada a `https://www.coingecko.com`, con letra de al menos 10 px (`text-xs` = 12 px). Verificado el 2026-10-18 en la guía oficial.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:frontend/src/components/Attribution.tsx -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Importa (de `frontend/src/`):** ninguno
- **Lo importan:** `frontend/src/DashboardView.tsx`, `frontend/src/UniverseTab.tsx`
- **Rutas HTTP que cita:** ninguno
- **Pruebas:** ninguno
<!-- /hechos:frontend/src/components/Attribution.tsx -->

### `frontend/src/components/BandIndicator.tsx`

**Responsabilidad.** Posiciona la tasa de aprobación (0-100 %) sobre una escala con la franja aceptable (15-85 %) sombreada.

**Funciones y clases clave.**
- `BandIndicator`.

**Invariantes — no romper.** El color de estado va **siempre** junto al texto «dentro»/«fuera de la banda», nunca solo el color del marcador.

**Trampas conocidas.** Los límites 15 y 85 repiten `APPROVAL_BAND` del backend; si cambian allí, cambian aquí.

<!-- hechos:frontend/src/components/BandIndicator.tsx -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Importa (de `frontend/src/`):** ninguno
- **Lo importan:** `frontend/src/SummaryTab.tsx`
- **Rutas HTTP que cita:** ninguno
- **Pruebas:** ninguno
<!-- /hechos:frontend/src/components/BandIndicator.tsx -->

### `frontend/src/components/Card.tsx`

**Responsabilidad.** La tarjeta con título que envuelve cada bloque.

**Funciones y clases clave.**
- `Card`.

**Invariantes.** Ninguno propio.

**Trampas conocidas.** Ninguna.

<!-- hechos:frontend/src/components/Card.tsx -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Importa (de `frontend/src/`):** ninguno
- **Lo importan:** `frontend/src/AccountTab.tsx`, `frontend/src/ArmTab.tsx`, `frontend/src/NewsMemoryCard.tsx`, `frontend/src/RiskTab.tsx`, `frontend/src/SummaryTab.tsx`, `frontend/src/UniverseTab.tsx`
- **Rutas HTTP que cita:** ninguno
- **Pruebas:** ninguno
<!-- /hechos:frontend/src/components/Card.tsx -->

### `frontend/src/components/ConfirmDialog.tsx`

**Responsabilidad.** El diálogo de confirmación explícita de los controles.

**Funciones y clases clave.**
- `ConfirmDialog`.

**Invariantes — no romper.** Muestra la advertencia del servidor **tal cual** y solo habilita «Confirmar» cuando se escribió la palabra exacta.

**Trampas conocidas.** Es la primera defensa, no la única: el servidor vuelve a exigir la palabra.

<!-- hechos:frontend/src/components/ConfirmDialog.tsx -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Importa (de `frontend/src/`):** ninguno
- **Lo importan:** `frontend/src/RiskTab.tsx`
- **Rutas HTTP que cita:** ninguno
- **Pruebas:** `frontend/src/components/ConfirmDialog.test.tsx`
<!-- /hechos:frontend/src/components/ConfirmDialog.tsx -->

### `frontend/src/components/ProgressBar.tsx`

**Responsabilidad.** La barra de progreso hacia un objetivo (por ejemplo el N efectivo hacia 300).

**Funciones y clases clave.**
- `ProgressBar`.

**Invariantes — no romper.** Nunca más del 100 % de ancho aunque el valor supere el objetivo; el número exacto va en el texto, no solo en el ancho.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:frontend/src/components/ProgressBar.tsx -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Importa (de `frontend/src/`):** ninguno
- **Lo importan:** `frontend/src/SummaryTab.tsx`
- **Rutas HTTP que cita:** ninguno
- **Pruebas:** `frontend/src/components/ProgressBar.test.tsx`
<!-- /hechos:frontend/src/components/ProgressBar.tsx -->

### `frontend/src/components/StatTile.tsx`

**Responsabilidad.** Un par etiqueta-valor.

**Funciones y clases clave.**
- `StatTile`.

**Invariantes.** Ninguno propio.

**Trampas conocidas.** Ninguna.

<!-- hechos:frontend/src/components/StatTile.tsx -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Importa (de `frontend/src/`):** ninguno
- **Lo importan:** `frontend/src/AccountTab.tsx`, `frontend/src/ArmTab.tsx`, `frontend/src/NewsMemoryCard.tsx`, `frontend/src/RiskTab.tsx`, `frontend/src/SummaryTab.tsx`, `frontend/src/UniverseTab.tsx`
- **Rutas HTTP que cita:** ninguno
- **Pruebas:** ninguno
<!-- /hechos:frontend/src/components/StatTile.tsx -->

### `frontend/src/components/Tabs.tsx`

**Responsabilidad.** Las pestañas accesibles (patrón WAI-ARIA *tabs*).

**Funciones y clases clave.**
- `Tabs`, `TabDef`, `tabId()` y `tabPanelId()`.

**Invariantes — no romper.** `role` `tablist`/`tab`, `aria-selected`, `aria-controls` y navegación con flechas, Inicio y Fin; el panel lo dibuja quien las usa con `tabPanelId`.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:frontend/src/components/Tabs.tsx -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Importa (de `frontend/src/`):** ninguno
- **Lo importan:** `frontend/src/DashboardView.tsx`
- **Rutas HTTP que cita:** ninguno
- **Pruebas:** `frontend/src/components/Tabs.test.tsx`
<!-- /hechos:frontend/src/components/Tabs.tsx -->

### `frontend/src/components/Unavailable.tsx`

**Responsabilidad.** El mensaje cuando una consulta nueva falla.

**Funciones y clases clave.**
- `Unavailable`.

**Invariantes — no romper.** Un 404 significa que el bot en ejecución **todavía no tiene la función** (el frontend compilado se sirve en caliente, pero el backend solo cambia al reiniciarlo): se dice así en vez de dejar la pestaña rota.

**Trampas conocidas.** Cualquier otro error muestra un mensaje genérico.

<!-- hechos:frontend/src/components/Unavailable.tsx -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Importa (de `frontend/src/`):** `frontend/src/api.ts`
- **Lo importan:** `frontend/src/AccountTab.tsx`, `frontend/src/ArmTab.tsx`, `frontend/src/RiskTab.tsx`, `frontend/src/UniverseTab.tsx`
- **Rutas HTTP que cita:** ninguno
- **Pruebas:** ninguno
<!-- /hechos:frontend/src/components/Unavailable.tsx -->

### `frontend/src/format.ts`

**Responsabilidad.** Los formateadores de fechas, porcentajes, USDT y números.

**Funciones y clases clave.**
- `fmtDays`, `fmtDate`, `fmtPct`, `fmtUsd`, `fmtNum`, `fmtUsdt`, `fmtSigned` y los de la Fase 5 (`fmtAgo`, `fmtCompact`, `fmtPercentValue`).

**Invariantes — no romper.** `fmtDays` usa los **mismos textos** de casos especiales que `app/trading/measurement_status.py::_fmt_pace_days`: no reinventar la semántica del lado de Python. `undefined` equivale a un backend viejo que
todavía no manda el campo.

**Trampas conocidas.** `fmtNum` no usa separador de miles.

<!-- hechos:frontend/src/format.ts -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Importa (de `frontend/src/`):** ninguno
- **Lo importan:** `frontend/src/AccountTab.tsx`, `frontend/src/ArmTab.tsx`, `frontend/src/NewsMemoryCard.tsx`, `frontend/src/RiskTab.tsx`, `frontend/src/SummaryTab.tsx`, `frontend/src/UniverseTab.tsx`
- **Rutas HTTP que cita:** ninguno
- **Pruebas:** ninguno
<!-- /hechos:frontend/src/format.ts -->

### `frontend/src/main.tsx`

**Responsabilidad.** El punto de entrada: monta `App` en `#root` con el `QueryClientProvider`.

**Funciones y clases clave.** Ninguna (solo montaje).

**Invariantes.** `refetchInterval` de 45 s y `retry: 1` por defecto para todas las consultas.

**Trampas conocidas.** Ninguna.

<!-- hechos:frontend/src/main.tsx -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Importa (de `frontend/src/`):** `frontend/src/App.tsx`
- **Lo importan:** ninguno
- **Rutas HTTP que cita:** ninguno
- **Pruebas:** ninguno
<!-- /hechos:frontend/src/main.tsx -->

### `frontend/src/setupTests.ts`

**Responsabilidad.** La preparación de Vitest: `jest-dom` y una simulación mínima de `matchMedia`.

**Funciones y clases clave.** Ninguna.

**Invariantes.** `jsdom` no implementa `matchMedia` y `App.tsx` lo usa para el valor inicial del modo oscuro.

**Trampas conocidas.** Ninguna.

<!-- hechos:frontend/src/setupTests.ts -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Importa (de `frontend/src/`):** ninguno
- **Lo importan:** ninguno
- **Rutas HTTP que cita:** ninguno
- **Pruebas:** ninguno
<!-- /hechos:frontend/src/setupTests.ts -->

### `frontend/src/testUtils.tsx`

**Responsabilidad.** Las utilidades de las pruebas: `withQueryClient`, `jsonResponse`, `mockFetch` y `calledPaths`.

**Funciones y clases clave.**
- `mockFetch()` — un `fetch` falso que enruta por método y ruta (`"/ruta"` para GET, `"POST /ruta"`); una ruta no mockeada **lanza** (la consulta falla, como un servidor sin esa función). `calledPaths()` inspecciona qué se pidió.

**Invariantes — no romper.** El cliente de pruebas usa `retry: false` y `refetchInterval: false` para que no haya sondeos ni reintentos en segundo plano.

**Trampas conocidas.** Una prueba que no mockea una ruta que el componente pide falla con «ruta no mockeada en el test».

<!-- hechos:frontend/src/testUtils.tsx -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Importa (de `frontend/src/`):** ninguno
- **Lo importan:** ninguno
- **Rutas HTTP que cita:** ninguno
- **Pruebas:** `frontend/src/AccountTab.test.tsx`, `frontend/src/App.test.tsx`, `frontend/src/ArmTab.test.tsx`, `frontend/src/DashboardView.test.tsx`, `frontend/src/NewsMemoryCard.test.tsx`, `frontend/src/RiskTab.test.tsx`, `frontend/src/UniverseTab.test.tsx`
<!-- /hechos:frontend/src/testUtils.tsx -->
