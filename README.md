# Minerva -- bot de trading autonomo (PAPER TRADING v1)

> **MODO PAPER TRADING.** Esta version nunca envia ordenes reales a Bitunix.
> Solo usa datos publicos de mercado y simula operaciones localmente. El
> trading (real o simulado) puede perder dinero; nada aqui garantiza
> ganancias.

Ver `docs/SPEC.md` (especificacion vigente), `docs/openspec.md` (borrador
previo evaluado en `docs/FASE0.md`) y `PROGRESS.md` (estado por fase).

## Requisitos

- Python 3.11+ (probado con 3.11 y 3.12; ver nota sobre 3.14 mas abajo).
- Node 18+ (solo para el frontend mínimo).
- [uv](https://docs.astral.sh/uv/) recomendado para manejar el entorno, o
  `pip` estandar.

## Puesta en marcha (local, sin Docker)

```bash
# Backend
uv venv --python 3.12 .venv
uv pip install -e ".[dev]"
cp .env.example .env   # opcional: valores por defecto ya son seguros
uvicorn app.main:app --reload

# Frontend (en otra terminal)
cd frontend
npm install
npm run dev
```

Endpoints disponibles: `GET /health`, `GET /positions`, `GET /trades`
(sin autenticacion todavia; se agrega en Fase 5).

## Puesta en marcha (Docker Compose)

```bash
docker compose up --build
```

(Esqueleto minimo de Fase 1; la configuracion final -- multi-stage, frontend
incluido, healthchecks -- es Fase 6.)

## Tests y lint

```bash
pytest
ruff check .
```

Los tests de integracion contra la red real de Bitunix estan marcados
`@pytest.mark.slow` y se omiten por defecto; para correrlos:

```bash
MINERVA_RUN_LIVE_TESTS=1 pytest tests/integration -m slow
```

## Nota sobre Python 3.14 en Windows

Si tu interprete de Python 3.14 (via `uv python install` u otra fuente)
falla con `OPENSSL_Uplink(...): no OPENSSL_Applink` al usar el modulo `ssl`
(incluso con `ssl.create_default_context()` puro), es un problema conocido
de ese build especifico en Windows, no de este proyecto. Usa Python 3.11 o
3.12 mientras tanto.

## Nota sobre redes corporativas / proxies TLS

El cliente de Bitunix usa `truststore` (almacen de certificados del sistema
operativo) en vez del bundle embebido de `certifi`, para funcionar tambien
en redes con inspeccion TLS corporativa. Si tu red no tiene esto, funciona
igual sin configuracion adicional.

## Estructura

Ver `docs/FASE0.md` para la justificacion de la estructura de carpetas, el
esquema de base de datos y la eleccion de frontend. Resumen:

```
app/            Backend (FastAPI + asyncio)
  market/       Clientes de datos publicos de Bitunix (REST/WS) + cache OHLCV
  indicators/   EMA/RSI/ATR con pandas puro
  strategies/   Estrategias por reglas (BaseStrategy + implementaciones)
  execution/    ExecutionBackend (PaperBackend activo; BitunixBackend SOLO documentado)
  persistence/  aiosqlite + repositorios
  api/          Rutas FastAPI
frontend/       React + Vite (pantalla minima de posiciones/trades, sin auth aun)
tests/          pytest + pytest-asyncio
docs/           SPEC.md, openspec.md, FASE0.md
```
