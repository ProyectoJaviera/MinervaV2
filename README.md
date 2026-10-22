# Minerva: bot de trading autónomo asistido por IA (PAPER TRADING v1)

> ## ⚠ Aviso de riesgos
>
> **Esta versión nunca envía órdenes reales.** Es 100 % *paper trading*: usa solo datos públicos de
> mercado de Bitunix y simula las operaciones localmente. Aun así, el trading de futuros apalancados
> (real o simulado) **puede perder dinero y nada de lo que hay aquí garantiza ganancias**. Que el
> simulador gane no implica que una cuenta real ganaría: la ejecución real tiene latencia, comisiones,
> deslizamiento y límites que el simulador solo aproxima (`docs/PASO_DINERO_REAL.md`).
>
> **Estado de la evidencia (2026-10-18):** el backtest de las estrategias por reglas **no encontró
> ventaja demostrable** (ninguna superó los criterios congelados de antemano), y el valor que aporta el
> filtro de IA todavía **no se ha medido**: la medición va en curso y es *ciega* a propósito (ver
> abajo). No hay base para pasar a dinero real. Lee `docs/INFORME_FINAL.md`.

Minerva evalúa, en cada cierre de vela, un universo dinámico de criptomonedas (top por capitalización
con perpetuo USDT en Bitunix) con seis estrategias por reglas; cada señal se simula como *operación
sombra*, Claude (Anthropic) la etiqueta `APROBADA` o `RECHAZADA`, y la cuenta paper solo abre las
aprobadas a tiempo, pasando por un motor de riesgo (y solo de las estrategias elegibles:
`REAL_ACCOUNT_ELIGIBLE_STRATEGIES`). El objetivo de esta versión es **medir con rigor si el filtro
de IA aporta valor** antes de que cualquiera piense en dinero real.

## Cómo funciona (arquitectura)

```text
  Bitunix (REST + WebSocket públicos)      CoinGecko (universo)      Noticias (GDELT, Fed, Fear&Greed)
              │                                    │                            │ (solo informativas)
              ▼                                    ▼                            ▼
   ┌──────────────────────────── proceso único: FastAPI + asyncio ─────────────────────────────┐
   │  Scheduler ──► Generador de señales ──► Libro de SOMBRAS ──► Etiquetador en vivo (Claude)  │
   │  (cada 60 s)   (6 estrategias)           (1 por señal)         prompt CONGELADO + hash      │
   │                                                                      │ APROBADA a ≤ 60 s    │
   │                                                                      ▼                      │
   │   Monitor de posiciones ◄── ticks WS ── PaperBackend ◄── Brazo ejecutado ◄── Motor de       │
   │   (SL, TP, trailing,       + reconciliación  (cuenta paper)   (solo APROBADA)    riesgo      │
   │    liquidación)            al reiniciar                                                      │
   │                                                                                              │
   │  SQLite (WAL)  ◄── todo se registra: señales, sombras, consultas al LLM, rechazos, auditoría │
   └────────┬──────────────────────────────┬──────────────────────────────┬─────────────────────┘
            ▼                              ▼                              ▼
     API /api/* (login)             Telegram (comandos +            Dashboard React
     solo lectura + 3 controles     avisos push + resumen diario)   (5 pestañas, mismo origen)
```

**Una señal, paso a paso:** (1) el cierre de una vela dispara el generador; cada evaluación queda en
`signals` (HOLD incluido); (2) las señales accionables de un mismo símbolo y vela se agrupan; (3) se abre
una **sombra** (margen ilimitado, mismo cálculo de costos que la cuenta paper); (4) el etiquetador
consulta a Claude con el prompt congelado y guarda la decisión *completa* en `llm_logs`; (5) si es
`APROBADA` y llegó en ≤ 60 s, el **brazo ejecutado** pide abrir una posición a través del motor de
riesgo (kill switch, stop por drawdown, circuit breaker, pérdida diaria, datos obsoletos, elegibilidad
por estrategia, cupos, tope de SL); (6) el **monitor** vigila SL, TP, trailing y liquidación con ticks en
vivo; (7) si el bot estuvo caído, **reconcilia** con velas de 1 minuto lo que habría ocurrido.

Tres ideas de diseño que conviene tener presentes:

- **Medición ciega.** Hasta juntar 300 conglomerados efectivos por lado *y* existir un registro oficial
  del veredicto, ningún canal (API, dashboard, Telegram, avisos, resumen diario) muestra PnL, precio de
  salida, motivo de cierre, saldo ni equity. La puerta falla cerrada (`app/trading/reveal.py`).
- **El prompt está congelado.** Su hash (`FROZEN_PROMPT_SHA256`) se comprueba en cada ciclo; si el
  código lo cambiara, el etiquetado se detiene solo en vez de contaminar la medición.
- **Nada del exterior es fiable por defecto.** Datos gratuitos con caché local, reintentos con espera,
  salud por fuente, bloqueo de nuevas operaciones con datos obsoletos y tareas aisladas: un fallo de
  Telegram, noticias o memoria no alcanza al scheduler, al etiquetado ni al monitor.

## Requisitos

| Para | Necesitas |
|---|---|
| Ejecutar el bot (nativo) | Python **3.11 o 3.12** (`requires-python = ">=3.11,<3.13"`), acceso a internet. Windows es donde se ha desarrollado y operado |
| Compilar el dashboard | Node 18 o superior y `npm` (la imagen Docker usa Node 24) |
| Usar el LLM | Clave de la API de Anthropic (**lo único que cuesta dinero**; tope diario configurable, 1 USD por defecto) |
| Avisos y comandos | Un bot de Telegram (gratis) |
| Docker (opcional) | Docker Engine / Docker Desktop con Compose v2 |

Todo lo demás (datos de mercado, universo, noticias) usa fuentes gratuitas sin tarjeta.

## Puesta en marcha

### A) Nativa en Windows (la forma en uso hoy)

```powershell
# 1. Entorno de Python (con uv; con pip estándar también sirve) y dependencias
uv venv --python 3.11 .venv
uv pip install --python .venv\Scripts\python.exe -e ".[dev]"

# 2. Configuración: copia la plantilla y completa SOLO tus datos. Nunca subas ni pegues el .env.
Copy-Item .env.example .env
.\.venv\Scripts\python.exe scripts\hash_password.py     # genera AUTH_PASSWORD_HASH (pide la clave oculta)
# JWT_SECRET: una cadena aleatoria de al menos 32 caracteres. TELEGRAM_*, ANTHROPIC_API_KEY: ver el manual.

# 3. Dashboard (una vez, y tras cada cambio del frontend)
npm --prefix frontend ci
npm --prefix frontend run build

# 4. Comprobar que todo pasa antes de arrancar
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check .

# 5. Arrancar / detener el bot real (puerto 8000, sin consola visible, con las variables fijas de hoy)
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_bot.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop_bot.ps1
```

Para probar sin tocar nada real: `python scripts/run_dev_server.py --source-db <respaldo>` levanta una
copia temporal en el puerto **8001** con el LLM, Telegram, el brazo, las noticias y los avisos
apagados por construcción.

### B) Con Docker (un solo comando; la forma prevista para el PC 24/7)

> **⚠ Si hoy el bot real corre nativo, NO uses `docker compose up -d` para pasarlo a Docker.** Con el bot nativo apagado
> crearía un volumen *vacío* y empezaría una medición nueva, sin historial y con el etiquetado real encendido (gasta
> API). La mudanza correcta es con el script de más abajo (`migrate_to_docker.ps1`), que copia la base, la verifica y
> puede deshacerse. `docker compose up -d` a secas es para **después** de la mudanza, o para una instalación nueva.

```powershell
docker compose up -d       # la primera vez construye la imagen (~1 min) y arranca el bot real
docker compose ps          # STATUS pasa a "healthy" en ~1 min
docker compose logs -f minerva
docker compose stop        # parada ordenada (SIGTERM, hasta 30 s)
```

- La **base y los logs viven en volúmenes internos de Docker** (`minerva_data`, `minerva_logs`), no en carpetas de
  Windows: SQLite en modo WAL no es fiable sobre ellas. Por eso los respaldos y los informes se hacen con
  `python scripts\docker_db.py ...` (copia con la API de backup *dentro* del contenedor, SHA-256 verificado al
  sacarla al host). Ver el manual, §5 y §9.
- El `.env` **no entra en la imagen**: se monta de solo lectura en `/app/.env`. El puerto se publica solo en
  `127.0.0.1:8000`, el código de la imagen es de solo lectura para el usuario del proceso (uid 10001, sin
  capacidades) y el contenedor se reinicia solo (`restart: unless-stopped`).
- Para que el bot vuelva tras reiniciar el PC, **Docker Desktop debe iniciar con Windows**
  ([`docs/MUDANZA_DOCKER.md`](docs/MUDANZA_DOCKER.md) §5).
- **No lo levantes a la vez que `start_bot.ps1`** (dos procesos con el mismo token de Telegram y la misma clave):
  tras la mudanza, `start_bot.ps1` se niega a arrancar.

**Pasar a Docker el bot real que hoy corre nativo**, con su base y su medición intactas (guía completa en
[`docs/MUDANZA_DOCKER.md`](docs/MUDANZA_DOCKER.md)):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\migrate_to_docker.ps1 -DryRun   # ensayo: no cambia nada
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\migrate_to_docker.ps1            # la mudanza (ventana segura; escribe MIGRAR)
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\rollback_from_docker.ps1         # la vuelta atrás (escribe VOLVER)
```

Después de arrancar abre `http://127.0.0.1:8000/` (dashboard) y verifica `GET /health`. Detalle de la operación
diaria, los reinicios y los respaldos: [`docs/MANUAL_OPERACION.md`](docs/MANUAL_OPERACION.md).

## Qué verás

- **Dashboard** (`/`, con login): *Resumen* (salud, costo del LLM, avance de la medición, noticias),
  *Cuenta paper* (posiciones con niveles y horas, sin resultados mientras la puerta esté cerrada),
  *Brazo ejecutado* (abiertas y omitidas con su motivo), *Riesgo y controles* (breaker, kill switch,
  pausa; botones con confirmación explícita) y *Universo y backtest* (con "Data provided by CoinGecko").
- **Telegram**: `/status`, `/positions`, `/balance`, `/metrics`, `/pause`, `/resume`, `/kill`. Los tres
  últimos piden confirmación (`/pause` y luego `/pause SI` dentro de 60 s) y `/pause` y `/kill`
  **detienen la medición**. Además, avisos de posiciones abiertas y cerradas, cambios de universo y
  alertas de riesgo, y un resumen diario a las 09:15 (hora de Chile) por una tarea aparte.
- **API** (`/api/...`, con sesión) de solo lectura, más `POST /api/control/{pause|resume|kill}`; `/health`
  sin sesión para vigilancia.

## Pruebas

```bash
pytest                              # 1.956 pruebas: unitarias, de integración y de punta a punta (~11 min)
ruff check .
npm --prefix frontend run test      # Vitest
npm --prefix frontend run build
```

Los tests de integración contra la red real de Bitunix están marcados `@pytest.mark.slow` y se omiten
por defecto; para correrlos: `MINERVA_RUN_LIVE_TESTS=1 pytest tests/integration -m slow`.

Las **pruebas de punta a punta** (`tests/e2e/`) arman el sistema *real* sobre una base temporal y simulan
solo el exterior (Anthropic, Bitunix, WebSocket, Telegram): ciclo de vela 4 h LONG y SHORT, TP, SL,
trailing, liquidación, circuit breaker con post-mortem, pausa y kill desde Telegram y dashboard,
reinicio con reconciliación y el barrido de que ningún canal muestra resultados con la puerta cerrada.
Cero red y cero gasto: un candado bloquea cualquier conexión a un nombre que no sea local.

## Estructura del repositorio

```text
app/            Backend (FastAPI + asyncio)
  api/          Rutas HTTP y serializadores con lista blanca de campos
  core/         Scheduler, latido, registro (logging) no bloqueante, cliente HTTP
  execution/    ExecutionBackend: PaperBackend (activo) y BitunixBackend (SOLO documentado, no se usa)
  indicators/   EMA, RSI, ATR, Bollinger, Donchian con pandas puro
  llm/          Prompt congelado, cliente, servicio de decisión, presupuesto
  market/       Bitunix REST/WS, CoinGecko, velas y funding en caché, universo
  memory/       Memoria de lecciones y post-mortem (apagada durante la medición)
  news/         Recolección de noticias gratuitas (texto no confiable)
  persistence/  aiosqlite, esquema y repositorios
  strategies/   Las 6 estrategias por reglas
  telegram/     Comandos, avisos push, cliente
  trading/      Generador de señales, sombras, brazo ejecutado, monitor, motor de riesgo, informes
  backtesting/  Motor de backtest de la Fase 2
frontend/       React + Vite + Tailwind (dashboard) con Vitest
scripts/        Arranque/parada, respaldos, informes de la medición y del veredicto, mudanza a Docker
                (migrate_to_docker.ps1, rollback_from_docker.ps1, docker_db.py), herramientas
tests/          unit/, integration/, e2e/ y support/ (docker, schtasks y bot nativo falsos para probar la mudanza)
docker/         Healthcheck, versiones exactas de dependencias, variables de Compose y certificados raíz opcionales
docs/           Especificación, planes por fase, manual de operación, informe final, esquema de la base
```

## Documentación

| Documento | Para qué |
|---|---|
| [`docs/MANUAL_OPERACION.md`](docs/MANUAL_OPERACION.md) | **Empieza aquí para operar**: rutina diaria, reinicios, respaldos, migración, claves, cuando se abra el gate |
| [`docs/MUDANZA_DOCKER.md`](docs/MUDANZA_DOCKER.md) | Pasar el bot real a Docker con un script (y volver atrás), qué se probó y qué falló, Docker Desktop con Windows |
| [`docs/INFORME_FINAL.md`](docs/INFORME_FINAL.md) | Qué se construyó, decisiones, qué está verificado y qué no, riesgos y próximos pasos |
| [`docs/ESQUEMA_BD.md`](docs/ESQUEMA_BD.md) | Esquema de la base de datos (generado del código) |
| [`docs/SPEC.md`](docs/SPEC.md) | Especificación vigente |
| [`docs/VEREDICTO_3_6.md`](docs/VEREDICTO_3_6.md) | Procedimiento del veredicto de valor de la IA (una sola mirada) |
| [`docs/PASO_DINERO_REAL.md`](docs/PASO_DINERO_REAL.md) | Criterios y riesgos de un eventual paso a dinero real (solo recomienda) |
| [`docs/MIGRACION_PC.md`](docs/MIGRACION_PC.md) | Tareas programadas de Windows y mudanza nativa |
| `docs/FASE*_PLAN.md`, `PROGRESS.md` | Plan, decisiones y avance de cada fase |

## Notas

- **Python 3.14 en Windows:** algunos builds (p. ej. el de `uv python install`) fallan con
  `OPENSSL_Uplink(...): no OPENSSL_Applink` al usar `ssl`; es del build, no de este proyecto. Usa 3.11 o 3.12.
- **Redes con inspección TLS:** el cliente HTTP usa `truststore` (el almacén de certificados del sistema).
  En Docker, el certificado raíz de esa red se instala dejando un `.crt` en `docker/certs/`.
- **Seguridad:** la API escucha solo en `127.0.0.1`; los controles exigen sesión, mismo origen, JSON y
  una palabra de confirmación exacta, y quedan auditados. Nunca pegues el `.env`, claves ni tokens en
  chats, commits ni logs.
