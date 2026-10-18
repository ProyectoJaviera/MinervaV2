# Fase 6: Docker, pruebas de punta a punta, documentación final y limpieza (plan, 2026-10-18)

Alcance: `docs/SPEC.md` (fase 6: "Docker, pruebas E2E, guía de puesta en marcha en PC local y aviso de riesgos"; entregables:
README, `.env.example`, esquema de base de datos, instrucciones de ejecución e informe de métricas) y lo que pediste en la revisión
de `6be3d2f`. Cierra además dos pendientes heredados: el certificado raíz de inspección TLS en el contenedor (nota de la Fase 1 en
`app/core/http.py` y `PROGRESS.md`) y la reescritura del README, que sigue describiendo la Fase 1.

## Reglas (de tu revisión de 6be3d2f)

1. **Docker NO reemplaza el arranque actual.** El bot real sigue con `scripts/start_bot.ps1` en el puerto 8000 hasta la migración al
   PC 24/7. Nada de esta fase lo detiene, lo reinicia ni lo toca; las pruebas usan base temporal y el puerto 8001.
2. **Nada toca el prompt congelado ni la medición** (`SYSTEM_PROMPT`, `FEATURES_VERSION`, `FROZEN_PROMPT_SHA256`, sombras, etiquetas,
   `measurement_keys`). Las pruebas E2E *usan* esos componentes tal cual; no los modifican.
3. **Cero gasto en las pruebas**: Anthropic (cliente falso programable), Bitunix, CoinGecko y Telegram simulados; base temporal.
4. Sin secretos en el repo; `.env` fuera de la imagen; nunca leo `.env` (ni lo pasa compose a una prueba).
5. Sin costo nuevo: imágenes base oficiales públicas y herramientas gratuitas.

## Estado inicial verificado (2026-10-18)

| Hecho | Consecuencia |
|---|---|
| Docker CLI 29.6.2 y Compose v5.3.1 instalados, pero **el demonio no está activo** (Docker Desktop apagado) | No se puede construir ni arrancar una imagen en este PC. No lo enciendo por mi cuenta (servicio pesado en el PC del bot real, cuya medición es sensible a los cierres de vela). Se valida todo lo que no necesita demonio y se lista lo que falta probar |
| Existe un esqueleto de la Fase 1 (`docker/Dockerfile.backend`, `docker-compose.yml`): sin frontend, sin healthcheck, instala con `pip install -e .` antes de copiar el código, corre como root | Se reemplaza |
| `app/main.py` (`frontend/dist`) y `app/trading/verdict_record.py` (`docs/`) resuelven sus rutas **relativas a la ubicación del paquete** | La imagen no puede instalar `app` en `site-packages`: se ejecuta desde un árbol `/app` con `app/`, `scripts/`, `frontend/dist/` y `docs/` |
| `/health` pasa a `stale` si no hay ciclo del scheduler en 90 min, **también con una pausa deliberada** | El healthcheck de Docker acepta `stale` solo si `control.paused` es verdadero (pausa a propósito) |
| `uv pip freeze` del venv probado: 46 paquetes más el proyecto (Python 3.11.15; incluye pytest y ruff), sin `pip` dentro del venv | Se fija un archivo de versiones exactas para la imagen, generado desde ese conjunto probado y sin las herramientas de desarrollo |
| Node local v24.19.0, `frontend/package-lock.json` presente | Etapa del frontend con `node:24-slim` y `npm ci` |
| `start_bot.ps1` fija 8 variables según el puerto (`LLM_LIVE_LABELING_ENABLED`, `TELEGRAM_COMMANDS_ENABLED`, `PAPER_EXECUTION_ENABLED`, `NEWS_COLLECTION_ENABLED`, `TELEGRAM_PUSH_ENABLED` solo con 8000; `AUTO_OPEN_WITHOUT_LLM=false`, `LOG_LEVEL=INFO`, `LESSONS_MEMORY_ENABLED=false` siempre) | El compose reproduce exactamente esos valores, con un solo interruptor de prueba (`MINERVA_REAL_BOT=false` apaga los cinco) |

## 6.1 Docker

Archivos: `Dockerfile` (raíz), `.dockerignore`, `docker-compose.yml`, `docker/healthcheck.py`, `docker/requirements.lock`,
`docker/certs/` (certificados raíz opcionales), `scripts/export_docker_requirements.py`; se elimina `docker/Dockerfile.backend`.

| Decisión | Motivo |
|---|---|
| **Tres etapas**: `node:24-slim` (build del frontend) → `python:3.11-slim` (venv con las dependencias) → `python:3.11-slim` (imagen final) | La imagen final no lleva Node, compiladores ni caché de pip |
| **Versiones exactas** en `docker/requirements.lock` (el conjunto que pasó 1.552 pruebas) | Una versión nueva de pandas o pydantic podría cambiar indicadores y comparabilidad de la medición sin que nada lo avise. Sin `uvloop` a propósito: no está en el conjunto probado |
| Solo se instalan **dependencias**, no el proyecto; el código se copia a `/app` | Las rutas relativas al paquete (frontend, docs) deben seguir funcionando |
| **Usuario no root** (`minerva`, uid 10001); `/app/data` y `/app/logs` creados con ese dueño | El volumen nuevo hereda el dueño; `cap_drop: ALL` y `no-new-privileges` en el compose |
| **`.env` fuera de la imagen** (`.dockerignore` lo excluye junto con `data/`, `logs/`, `.git`, `*.db`, `tests/`, `node_modules/`) y entra solo como variables de entorno en tiempo de ejecución (`env_file`) | Las claves nunca quedan en una capa |
| **Volúmenes con nombre** `minerva_data` y `minerva_logs` (no carpetas del host) | SQLite en modo WAL no es fiable sobre el sistema de archivos compartido de Docker Desktop (Windows/macOS); y así `docker compose up` en este PC nunca abre la base real de `data/` |
| `docs/` se monta **solo lectura** desde el host | El registro oficial del veredicto (`docs/VEREDICTO_3_6_REGISTRO*.txt`) se lee en cada petición: al crearlo en el host se desbloquea sin reconstruir la imagen |
| Puerto publicado como `127.0.0.1:${MINERVA_PORT:-8000}:8000` | La API nunca queda expuesta a la LAN. El 8000 por defecto choca con el bot real de este PC: si alguien lo levanta por error, Docker falla al enlazar el puerto *antes* de arrancar el proceso (no hay segundo bot) |
| `restart: ${MINERVA_RESTART:-no}` | Por defecto no reinicia solo: en este PC un `restart: always` podría levantar un segundo bot al iniciar Docker. En el PC 24/7 se pone `unless-stopped` |
| Healthcheck `python docker/healthcheck.py` (sin curl en la imagen), `start_period` de 5 min | Sano = HTTP 200 y `status: ok` (o `stale` con pausa deliberada). Una prueba lo ejecuta contra un servidor local |
| Certificado raíz opcional: `docker/certs/*.crt` se instala con `update-ca-certificates` al construir | Cierra el pendiente de la Fase 1 (`truststore` lee el almacén del sistema de la imagen). Los `.crt` locales no se versionan |

**Validación sin demonio**: sintaxis del compose con `docker compose config` (en un directorio temporal con un `.env` falso, nunca el real),
pruebas estáticas del Dockerfile/compose/dockerignore (puerto en loopback, `.env` excluido, paridad de interruptores con
`start_bot.ps1`, healthcheck, usuario no root, sin secretos), resolución del archivo de versiones para Linux con `uv pip compile
--python-platform` (comprueba que existen ruedas para cada versión), `npm ci` + build en una copia limpia (los mismos comandos de la etapa
del frontend), y arranque en el 8001 desde un árbol con la forma de la imagen (sin `.env`, `tests/` ni `docs/`).
**Lo que no se puede probar sin demonio y queda para ti**: `docker compose build`, que el contenedor arranque y quede `healthy`,
la herencia de dueño del volumen y el montaje de `docs/` (ver "Qué falta probar").

## 6.2 y 6.3 Pruebas de punta a punta (`tests/e2e/`)

Un arnés (`tests/e2e/world.py`) arma el sistema **real** sobre una base de archivo temporal: scheduler, generador de señales con las
estrategias reales, libro de sombras, etiquetador con el servicio de decisión real, brazo ejecutado, `PaperBackend`, motor de riesgo,
monitor de posiciones, enrutador de comandos de Telegram, notificador push y la aplicación FastAPI real (`app.main.app`, sin lifespan,
llamada por ASGI). Solo se simula el exterior: Anthropic (un cliente programable que decide por símbolo), Bitunix REST (precios,
contratos; las velas se siembran en la caché), el WebSocket (los ticks se inyectan) y Telegram (un cliente que registra mensajes).
El generador usa el reloj real (`time.time()`) y las velas se siembran para que la última cierre "ahora": el retraso de decisión real
queda en segundos, así que se prueba también la regla de 60 s.

| Escenario | Qué se comprueba de punta a punta |
|---|---|
| Vela 4 h **LONG** → TP | señal (fila en `signals`), sombra, etiqueta `APROBADA` (fila en `llm_logs`, hash congelado), brazo (`paper_execution_log` ABIERTA), posición paper con niveles, ticks, cierre por TP en sombra y posición, balance, `/health` |
| Vela 4 h **SHORT** → SL | igual en corto; el SL se llena al precio observado (peor caso); racha de pérdidas 1 |
| Idempotencia | un segundo ciclo con la misma vela no duplica señales, sombras ni aperturas |
| `RECHAZADA` / fallo del LLM / decisión tardía | la sombra se mide pero el brazo no abre (`SIN_LLM`, `LATE_DECISION`), con su motivo consultable |
| **Trailing** | estrategia con trailing real (`trend_atr_stop`): el stop sube con el precio y cierra como `TRAILING` |
| **Liquidación** | cierre `LIQUIDATION` solo si el mark REST lo confirma; el último precio solo no basta |
| **Circuit breaker + post-mortem** | 4 pérdidas seguidas activan el breaker; la señal siguiente queda omitida (`RISK_CIRCUIT_BREAKER_ACTIVE`); el post-mortem se guarda una sola vez por racha |
| **Pausa y kill** desde Telegram y desde el dashboard | la pausa detiene señales, sombras y etiquetado pero **no** los cierres; el kill bloquea entradas; reanudar lo levanta; la auditoría registra canal y actor; el push avisa con el origen |
| **Reinicio con reconciliación** | tras "apagar" el sistema, el SL / TP / liquidación ocurridos con el bot caído se reproducen con velas 1 m y cierran con `CANDLE_RECON`; la posición sin cruce sigue abierta; una fila `PENDING` del brazo se resuelve; el push no repite lo ya avisado |
| **Reveal cerrado** | con operaciones cerradas con valores centinela, **ningún canal** (API de lectura, rutas heredadas `/trades` y `/positions`, `/health`, Telegram `/status` `/positions` `/balance` `/metrics`, push, resumen diario, post-mortem) muestra PnL, precio de salida, motivo de cierre, comisiones, saldo ni equity. Un barrido recursivo de claves prohibidas y de las representaciones numéricas de cada centinela |
| Sensibilidad del barrido | con la puerta abierta (simulada) el mismo barrido **sí** encuentra los centinelas: así se sabe que la prueba no es ciega |

## 6.4 Documentación (español)

- `README.md` reescrito: qué es, aviso de riesgos, arquitectura (diagrama de componentes y flujo de una señal), requisitos, puesta en
  marcha nativa (Windows, `start_bot.ps1`) y con Docker, estructura de carpetas, pruebas, enlaces.
- `docs/MANUAL_OPERACION.md`: rutina diaria, reinicios (ventana horaria), respaldos, migración al PC 24/7 (nativa y con Docker),
  claves y API key (rotación), controles (pausa / reanudar / kill) y qué detienen, alertas, cuándo y cómo seguir `docs/VEREDICTO_3_6.md`
  cuando se abra el gate, solución de problemas y qué no hacer.
- `docs/INFORME_FINAL.md`: qué se construyó por fase, decisiones clave, qué está verificado y qué no, riesgos, y próximos pasos tras el
  veredicto. Incluye la nota heredada: el backtest valida solo las estrategias por reglas; el valor del LLM se valida únicamente con
  paper trading hacia adelante.
- `docs/ESQUEMA_BD.md`: esquema de la base **generado** desde el DDL real por `scripts/export_schema_doc.py`; una prueba falla si el
  documento se desincroniza del código (entregable de la SPEC).
- Dos scripts operativos pequeños y probados que el manual necesita: `scripts/backup_db.py` (respaldo consistente con la API de backup de
  sqlite, con verificación de integridad y retención) y `scripts/restore_db.py` (importa un respaldo a la ruta configurada; se niega a
  sobrescribir), para la migración nativa y la de Docker sin comillas de PowerShell.

## 6.5 Limpieza

- Código muerto: análisis con `vulture` (en un entorno aislado de `uv`, sin tocar el venv del bot) y verificación a mano de cada hallazgo
  antes de borrar nada; solo se elimina lo que no usa ni producción ni pruebas.
- TODOs resueltos y notas obsoletas (la de `app/core/http.py` sobre el contenedor, los "pendiente de Fase 6" de `PROGRESS.md`, el
  README de la Fase 1).
- `.env.example`: una prueba exige que *toda* variable de `Settings` aparezca y que las credenciales estén vacías.
- Secretos: prueba que escanea los archivos versionados (claves de Anthropic, tokens de Telegram, hashes bcrypt, asignaciones
  `*_TOKEN/_SECRET/_PASSWORD/_API_KEY` con valor) + revisión única del historial de git (solo conteos, sin imprimir coincidencias).
- `.gitignore`: `.claude/settings.local*.json` y `docker/certs/*.crt`.

## Subfases y commits

| Subfase | Contenido |
|---|---|
| 6.1 | Dockerfile multietapa, `.dockerignore`, compose, healthcheck, lock de versiones, certificados opcionales, pruebas estáticas |
| 6.2 | Arnés E2E y escenarios del ciclo completo (LONG/SHORT, TP/SL, idempotencia, rechazo / fallo / tardía) |
| 6.3 | E2E de trailing, liquidación, breaker + post-mortem, pausa/kill, reinicio con reconciliación y barrido de resultados |
| 6.4 | `backup_db.py`, `restore_db.py`, `export_schema_doc.py`, README, MANUAL_OPERACION, INFORME_FINAL, ESQUEMA_BD |
| 6.5 | Limpieza: código muerto, notas obsoletas, `.env.example` y secretos con sus pruebas |
| 6.6 | Verificación completa (pytest, ruff, vitest, build), humo con copia real de los scripts nuevos, `git push origin master` |

## Qué requiere reinicio

Se confirma al terminar (lista definitiva en "Implementación"). Adelanto: Docker, pruebas, documentación y scripts nuevos **no** requieren
reiniciar el bot real; solo lo haría una limpieza de código muerto en `app/`, y aun así sin cambio de comportamiento.
