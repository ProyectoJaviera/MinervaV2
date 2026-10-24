"""Minerva: bot de trading autonomo en PAPER TRADING (la v1 nunca envia ordenes reales).

Paquetes: `api` (rutas FastAPI), `backtesting` (simulacion historica de la Fase 2),
`core` (scheduler, logging, latido, HTTP), `execution` (cuenta paper), `indicators`,
`llm` (decision de Claude sobre cada senal), `market` (Bitunix, CoinGecko, universo),
`memory` (lecciones, apagada), `news` (noticias, fuera del prompt), `persistence`
(SQLite), `strategies` (reglas), `telegram` (comandos y avisos) y `trading` (senales,
riesgo, sombras, monitor y medicion). El mapa completo esta en `docs/dev/README.md`.
"""

