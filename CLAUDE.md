# Minerva: bot de trading autónomo (PAPER TRADING v1)

## Contexto
Especificación completa en docs/SPEC.md y base previa en docs/openspec.md (nunca probada: evalúala críticamente, no la copies a ciegas). Lee SPEC.md al inicio de cada sesión.

## Reglas críticas
- v1 es 100% paper trading. Prohibido escribir código que envíe órdenes reales a Bitunix. La ejecución real solo existe como interfaz/documentación (BitunixBackend desactivado).
- Nunca leas, imprimas ni pidas archivos .env ni credenciales. Usa solo .env.example con valores vacíos. Nunca incluyas claves en código, tests, logs ni commits.
- No inventes endpoints, parámetros ni firmas de Bitunix/CoinGecko. Verifica con la documentación oficial o los ejemplos del repo oficial antes de implementar; si no puedes verificar, dilo.
- Trabaja por fases (ver SPEC.md). Al terminar cada fase: corre los tests, actualiza PROGRESS.md y detente a esperar mi aprobación. No avances de fase sin confirmación.

## Flujo de trabajo
- Antes de implementar una fase: investiga, propón un plan en Plan Mode y espera aprobación.
- Haz commits pequeños con mensajes claros (Conventional Commits). Nunca hagas push sin que yo lo pida.
- Pruebas: pytest + pytest-asyncio. Toda función de riesgo y de simulación (SL/TP/trailing, liquidación, comisiones, funding) debe tener tests con casos límite.
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