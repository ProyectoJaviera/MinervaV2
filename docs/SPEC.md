ROL
Eres un arquitecto de software senior y trader cuantitativo con especialidad en criptomonedas y futuros perpetuos. Construirás "Minerva v1.0", un bot de trading autónomo asistido por IA.

ALCANCE DE ESTA VERSIÓN
- 100% PAPER TRADING. No se envía ninguna orden real al exchange. La v1 no debe contener código que opere con dinero real activable por error; deja la capa de ejecución real como interfaz (ExecutionBackend) con una implementación PaperBackend ahora y una BitunixBackend documentada para una versión futura.
- Las credenciales de Bitunix NO se usan en v1. Solo datos públicos de mercado. Nunca incluyas claves en el código ni en ejemplos; configuración vía .env y .env.example sin valores reales.
- El bot es TOTALMENTE AUTOMÁTICO: la IA decide abrir y cerrar operaciones sin aprobación humana. Telegram solo NOTIFICA la actividad (más comandos de control: pausa, reanudar, estado).

DECISIONES YA TOMADAS
- LLM: Claude (API de Anthropic, SDK asíncrono, salidas JSON validadas con esquema). Propón qué modelo usar para cada tarea (p. ej. uno económico para clasificar noticias y uno más capaz para decisiones) y estima el costo mensual.
- Ejecución: PC local (Windows/Linux/macOS), Docker Compose.
- Paper trading mínimo: 30 días corridos, y los que hagan falta hasta cumplir los criterios de rentabilidad definidos abajo.
- Tamaño de posición simulada: 10 USDT de margen o más, configurable. Apalancamiento 10x, margen aislado.

STACK
- Backend: Python 3.11+, asyncio, FastAPI, WebSockets, SQLite (aiosqlite) o PostgreSQL si lo justificas.
- Frontend: propón y justifica (sugerencia: React + PWA/Capacitor para convertir a APK Android después). Responsivo, modo oscuro, tiempo real vía WebSocket.
- Autenticación en el frontend aunque sea local.

FUENTES (verifica la documentación oficial antes de codificar)
- Bitunix Futures API: https://www.bitunix.com/api-docs/ y https://github.com/BitunixOfficial/open-api . Úsalos para: datos de mercado públicos (REST/WebSocket), especificaciones de contratos (mínimos, precisión, comisiones, funding) y para dejar diseñada la futura capa de ejecución real.
- CoinGecko: https://docs.coingecko.com/ . Evalúa si aporta valor (ranking top 10, market cap, datos globales) considerando límites del plan gratuito, y dime qué usarás.

UNIVERSO DE ACTIVOS
- Top 10 por capitalización según CoinGecko, refrescado cada 24 h.
- Excluye stablecoins y tokens envueltos/derivados (USDT, USDC, stETH, WBTC, etc.).
- Incluye solo los que tengan perpetuo USDT en Bitunix.
- Si un activo sale del universo con una posición abierta, gestiona el cierre de forma definida (documenta la regla).

SIMULADOR DE PAPER TRADING (realista)
- Precios reales de Bitunix (mark price y último precio).
- Simula: comisiones taker/maker reales, funding cada periodo, slippage configurable, liquidación en margen aislado a 10x, cantidad mínima y precisión por símbolo.
- SL, TP y trailing se evalúan con datos de tick/WebSocket, no solo al cierre de vela, para no sobreestimar resultados.
- Reconciliación al reiniciar: si el PC estuvo apagado, recalcula con datos históricos del periodo caído si algún SL/TP/liquidación se habría activado.

CAPACIDADES DEL BACKEND (con pruebas para cada una, detrás de la interfaz ExecutionBackend)
1. Abrir LONG o SHORT.
2. Cerrar una posición de inmediato.
3. Take profit del 100% de la posición.
4. Stop loss del 100% de la posición.
5. Trailing stop del 100% de la posición.
6. Consultar saldo total (simulado, con capital inicial configurable, p. ej. 1000 USDT).
7. Consultar posiciones abiertas.
8. Precios en tiempo real del top 10.
9. Diseño de la futura BitunixBackend con los mismos métodos (sin activarla).

ANÁLISIS
- Datos técnicos: velas multi-timeframe e indicadores (tendencia, momentum, volatilidad, volumen, funding rate, open interest si está disponible).
- Noticias: fuentes confiables (RSS/APIs), deduplicación, extracción de activo afectado y sentimiento, con límites de frecuencia para controlar costos.
- Estrategias: investiga y propón de 3 a 5 con lógica clara. Incluye BACKTEST con datos históricos y reporta por estrategia: winrate, profit factor, drawdown máximo, comisiones y funding incluidos. Descarta las que no muestren ventaja.
- Motor de decisión: puntaje de confluencia por reglas + Claude con todo el contexto, que devuelve JSON: {accion: ABRIR_LONG|ABRIR_SHORT|CERRAR|ESPERAR, activo, confianza, porcentaje_exito_estimado, razonamiento, factores_clave, riesgos, sl, tp, trailing}.
- El porcentaje de éxito debe justificarse: combina el puntaje de reglas con la tasa de acierto histórica de operaciones similares, e indica el tamaño de muestra. Con pocas operaciones similares, márcalo como "baja evidencia".
- Umbral mínimo configurable de confianza para abrir.
- Control de costos de la API: presupuesto diario configurable, caché de análisis y frecuencia máxima de llamadas.

MEMORIA Y APRENDIZAJE
- Guarda cada operación con TODO el contexto de la decisión: indicadores, noticias, prompt, respuesta de Claude, estimación de éxito, resultado, PnL, comisiones, funding, duración y motivo de cierre.
- Aprendizaje sin reentrenar: antes de cada decisión, recupera las operaciones pasadas más similares (ganadoras y perdedoras) y pásaselas a Claude. Genera periódicamente un reporte de lecciones aprendidas y úsalo en los prompts.
- Evita el sobreajuste: exige muestra mínima antes de que una lección influya, y registra qué lecciones cambiaron decisiones.
- Post-mortem automático tras N pérdidas consecutivas (configurable) y pausa automática.
- Evalúa si el porcentaje de éxito estimado está calibrado (p. ej. las operaciones con 70% de éxito estimado ganan cerca de 70%) y muéstralo en el dashboard.

GESTIÓN DE RIESGO (obligatoria, aunque sea simulado)
- Máximo de posiciones simultáneas, pérdida diaria máxima, circuit breaker por pérdidas consecutivas y kill switch manual (API, frontend y Telegram).
- Límite de exposición por activo y correlación entre posiciones (p. ej. no abrir 5 LONG en altcoins a la vez).
- Manejo de errores de datos: si falla el feed de precios o hay datos obsoletos, el bot no abre operaciones y notifica.

CRITERIOS PARA CONSIDERAR PASAR A DINERO REAL (se evalúan, no son automáticos)
Mínimo 30 días corridos de paper trading continuo y, además, en ese periodo:
- Profit factor mayor a 1.3 después de comisiones y funding.
- Drawdown máximo inferior a un límite que yo defina (propón un valor).
- Mínimo X operaciones cerradas (propón un número estadísticamente razonable).
- Resultados no dependientes de una sola operación o de un solo activo.
- Sin errores críticos de reconciliación.
El bot debe generar un informe automático de estas métricas.

NOTIFICACIONES TELEGRAM
- Aperturas, cierres (con motivo y PnL), activaciones de SL/TP/trailing, circuit breaker, errores y resumen diario.
- Comandos: /status, /positions, /balance, /pause, /resume, /metrics, /kill.

FRONTEND
- Dashboard tipo exchange: saldo simulado, PnL no realizado, posiciones abiertas en tiempo real (entrada, precio marca, ROI, liquidación, SL/TP/trailing), precios del top 10, decisiones recientes de la IA con su razonamiento y porcentaje de éxito.
- Controles: pausar/reanudar, cerrar posición, cerrar todo, editar parámetros de riesgo.
- Historial de operaciones con filtros y PnL agrupado por día, semana, mes y año, con gráficos (curva de equity, winrate, drawdown).
- Vista de noticias analizadas, memoria/lecciones de la IA y avance hacia los criterios de paso a dinero real.
- Indicador claro y permanente de "MODO PAPER TRADING".

ENTREGA POR FASES (cada una con criterios de aceptación y pruebas)
0. Evaluación del documento, preguntas y decisiones de diseño.
1. Estructura, configuración, datos de mercado de Bitunix (REST/WS) y pruebas.
2. Indicadores, estrategias y backtesting.
3. Simulador de paper trading, gestión de riesgo y ejecución.
4. Noticias + Claude + memoria/aprendizaje.
5. API del backend + frontend + Telegram.
6. Docker, pruebas E2E, guía de puesta en marcha en PC local y aviso de riesgos: el bot puede perder dinero y nada garantiza ganancias.

ENTREGABLES
Estructura de carpetas, código completo por fase, README, .env.example, esquema de base de datos, instrucciones de ejecución y el informe de métricas de paper trading.