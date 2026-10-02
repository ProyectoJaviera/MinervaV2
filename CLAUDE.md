# Minerva: bot de trading autónomo (PAPER TRADING v1)

## Contexto
Especificación completa en docs/SPEC.md y base previa en docs/openspec.md (nunca probada: evalúala críticamente, no la copies a ciegas). Lee SPEC.md al inicio de cada sesión.

## Idioma
A partir de ahora, todas las respuestas, resúmenes y documentos para este proyecto deben estar en español. Los identificadores de código (variables, funciones, clases, tablas, columnas) siguen en inglés.

## Reglas críticas
- v1 es 100% paper trading. Prohibido escribir código que envíe órdenes reales a Bitunix. La ejecución real solo existe como interfaz/documentación (BitunixBackend desactivado).
- Nunca leas, imprimas ni pidas archivos .env ni credenciales. Usa solo .env.example con valores vacíos. Nunca incluyas claves en código, tests, logs ni commits.
- No inventes endpoints, parámetros ni firmas de Bitunix/CoinGecko. Verifica con la documentación oficial o los ejemplos del repo oficial antes de implementar; si no puedes verificar, dilo.
- Trabaja por fases (ver SPEC.md). Al terminar cada fase: corre los tests, actualiza PROGRESS.md y detente a esperar mi aprobación. No avances de fase sin confirmación.

## Flujo de trabajo
- Antes de implementar una fase: investiga, propón un plan en Plan Mode y espera aprobación.
- Haz commits pequeños con mensajes claros (Conventional Commits). Nunca hagas push sin que yo lo pida.
- Pruebas: pytest + pytest-asyncio. Toda función de riesgo y de simulación (SL/TP/trailing, liquidación, comisiones, funding) debe tener tests con casos límite.
- Antes de dar por terminada cualquier tarea que toque el pipeline de datos o la persistencia, ejecuta un smoke test de punta a punta con un subconjunto pequeño; que los tests unitarios pasen no es suficiente.
- Todo script que lea la base real debe ejecutarse antes de entregarlo contra una copia temporal de esa base (ruta distinta de `settings.database_path`, verificada con un assert explícito) y confirmar que termina sin errores con sus datos reales; los tests con datos sintéticos no bastan para esto (ver el fallo real de `scripts/analyze_risk.py` que esta regla previene).
- Mantén PROGRESS.md: estado por fase, decisiones y pendientes.

## REGLAS DE TRABAJO
- Trabaja por FASES. Entrega una fase, espera mi aprobación y sigue con la siguiente.
- FASE 0 (antes de escribir código): evalúa el documento adjunto (openspec.md), dime qué reutilizas y qué descartas y por qué, y hazme las preguntas que aún necesites (máximo 10, agrupadas).
- Si no puedes abrir un enlace, dímelo y pídeme que pegue la documentación. No inventes endpoints, parámetros ni firmas.

## Stack y convenciones
- Python 3.11+, asyncio, FastAPI, SQLite (aiosqlite). Tipado con type hints; formateo con ruff.
- Toda decisión del LLM se valida con esquema (pydantic) y se registra completa en la base de datos.
- El bot corre en Docker Compose en mi PC, no dentro de Claude Code. Puedes levantarlo para probar, siempre en modo paper.

## Comandos
- Tests: pytest
- Lint: ruff check .
- Levantar: docker compose up --build

## Restricción de costos (obligatoria)
- Lo único que puede costar dinero es la API de Claude (Anthropic), con presupuesto diario y corte duro configurables.
- Todo lo demás debe ser gratuito y sin tarjeta de crédito: APIs, librerías, datos, hosting (PC local) y herramientas.
- Antes de integrar cualquier servicio externo, verifica en su documentación oficial: plan gratuito real, límites de uso, términos (uso no comercial, atribución) y si pide tarjeta. Si no cumple, propón una alternativa gratuita o descártalo.
- Diseña para fuentes gratuitas poco fiables: caché local, reintentos con backoff, degradación controlada, registro de salud por fuente y bloqueo de nuevas operaciones si los datos críticos están obsoletos.
- Respeta límites de uso y atribuciones (p. ej. "Data provided by CoinGecko" visible en el frontend).

## Formato de comandos (para evitar solicitudes de permiso)
- El directorio de trabajo ya es la raíz del proyecto: NO uses `cd`.
- Un solo comando por llamada. No encadenes con `&&`, `;` ni `&`.
- Para cambiar de carpeta dentro de un comando usa las opciones propias de la herramienta (p. ej. `npm --prefix frontend run build`, `git -C ...`), no `cd`.
- Mensajes de commit de una sola línea con `git commit -m "tipo: descripción"`, sin saltos de línea ni trailers.
- No redirijas salidas a archivos temporales (`> build.log`, `> out.txt`) ni borres archivos después; lee la salida directamente.