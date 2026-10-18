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

---

# Implementación (2026-10-18)

Subfases 6.1 a 6.6 hechas y en `master`. Commits: `eebf2ad` (plan), `b9bfa11` (6.1 Docker), `d7f91bf` (6.2 y 6.3, pruebas de punta a
punta), `6877449` (herramientas de operación y esquema), `85ee8e9` (README), `2092d23` (manual), `37820f2`, `713d377` y `4ce4456`
(limpieza y vigilancia de la identidad de la medición) y el de la documentación final. **No se tocó el prompt congelado, la medición ni el
bot del puerto 8000** (solo se comprobó que seguía escuchando).

## Lo construido

| Pieza | Dónde |
|---|---|
| Imagen multietapa (frontend, dependencias con versiones exactas, runtime sin privilegios), `.dockerignore`, compose, healthcheck, certificados raíz opcionales | `Dockerfile`, `docker-compose.yml`, `docker/`, `.dockerignore` (90 pruebas estáticas en `tests/unit/test_docker_setup.py`) |
| Pruebas de punta a punta con el sistema real y el exterior simulado | `tests/e2e/` (51 pruebas, ~18 s) |
| Respaldo y restauración consistentes y verificados; levantar el stop por drawdown; esquema de la base generado | `scripts/backup_db.py`, `scripts/restore_db.py`, `scripts/resume_drawdown_stop.py`, `scripts/export_schema_doc.py` → `docs/ESQUEMA_BD.md` |
| Vigilancia de la identidad de la medición (modelo, prompt congelado, features) | `scripts/measurement_identity.py` |
| Documentación final | `README.md`, `docs/MANUAL_OPERACION.md`, `docs/INFORME_FINAL.md` |
| Limpieza y pruebas de higiene | `tests/unit/test_env_example.py`, `tests/unit/test_repo_hygiene.py`, `tests/unit/test_final_docs.py`; `.env.example` completo; `.gitignore` reforzado |

## Ajustes respecto al plan y decisiones (anotadas según Autonomía)

1. **Tres adiciones fuera de la lista pedida**, todas nacidas de hallazgos y todas de solo lectura o sin efecto sobre la medición:
   `resume_drawdown_stop.py` (el stop duro era irreversible en la práctica: los avisos prometían una acción que no existía),
   `measurement_identity.py` (el hash congelado no cubre el modelo) y las pruebas de higiene del repositorio.
2. **`.env.example`**: se interpretó "todas las variables, sin valores" como *todas las variables de `Settings`*, con las credenciales
   vacías y los demás valores iguales a los del código (una prueba exige que copiar la plantilla dé exactamente los defaults). Faltaban 31.
3. **Siete campos de configuración sin ninguna referencia se retiraron** (ver el docstring de `app/config.py`); un `.env` que aún los tenga
   no falla.
4. **El código muerto se contrastó a mano**: vulture no ve decoradores (rutas), ni nombres repetidos en otros módulos (`symbols`), así que
   cada hallazgo se verificó antes de borrar. Se conservó lo que es interfaz exigida por la especificación o vocabulario documentado.
5. **Los documentos históricos no se reescribieron** (`PROGRESS.md`, `docs/FASE*_PLAN.md`): son bitácora. Las pruebas de documentación
   solo revisan los documentos vivos (README, manual, informe final, migración, veredicto y esquema).
6. **El README reemplaza al de la Fase 1**; el manual reemplaza a las instrucciones sueltas y enlaza a `docs/MIGRACION_PC.md`, que
   conserva el detalle de las tareas del Programador de Windows (con un aviso al inicio y la mención de los scripts nuevos).
7. **`docker/Dockerfile.backend`** (esqueleto de la Fase 1) se eliminó; el `docker-compose.yml` anterior se reemplazó.

## Verificación

| Qué | Resultado |
|---|---|
| `pytest` | **1.796 pasaron y 4 se omitieron** (las 4 de red real, que exigen `MINERVA_RUN_LIVE_TESTS=1`) en 5 min 24 s; 1.800 recogidas: 1.745 unitarias, 4 de integración y 51 de punta a punta (antes de la Fase 6: 1.552 pasaron) |
| `ruff check .` | limpio |
| `npm --prefix frontend run test -- --run` | 106 pasaron (10 archivos) |
| `npm --prefix frontend run build` | `tsc --noEmit` y `vite build` limpios |
| `docker compose config` (sin demonio, con un `.env` falso en un directorio temporal) | válido; sin secretos en la salida |
| Dependencias para Linux (`uv pip compile --python-platform`) | el `docker/requirements.lock` está al día (`export_docker_requirements.py --check`) |
| Esquema de la base | `export_schema_doc.py --check`: `docs/ESQUEMA_BD.md` al día |
| Respaldo / restauración sobre **copia real** (ruta distinta verificada) | mismas filas en las 28 tablas; el origen no cambia; sin `--force` se niega a pisar un destino existente (código 5) |
| `resume_drawdown_stop.py` sobre copia real | informativo sin escribir; con el stop inactivo se niega (código 3); forzado el stop en la copia, lo levanta, deja el contador intacto y no imprime el equity |
| `measurement_identity.py` sobre copia real | una sola combinación (`claude-sonnet-5-5`, `signal_review_v4`, `features_v2`, hash congelado) en las filas de MEDICION; solo conteos y fechas |
| **Arranque real en el 8001** tras la limpieza (`run_dev_server.py` sobre una copia, sondeo de solo lectura) | arranca completo; `frozen_hash_ok` verdadero; push, etiquetado y brazo apagados; 22 rutas, las únicas de escritura son login, logout y `/api/control/{action}`; sin sesión todo da 401; frontend servido. Lo detuvo el propio script que lo lanzó; el 8000 siguió escuchando |
| Historial de git | 0 coincidencias de patrones de credenciales y 0 archivos prohibidos entre los 374 que alguna vez se añadieron (solo conteos) |

## Qué no se pudo verificar (qué falta probar)

Nada de esto lo toqué: **Docker Desktop estaba apagado** y no lo encendí por mi cuenta.

1. `docker compose build` (resolución real de las imágenes base, `pip install --only-binary` en Linux, `npm ci`, el flujo de certificados).
2. Que el contenedor arranque y quede `healthy` (healthcheck real, `start_period`).
3. Que el volumen `minerva_data` herede el dueño del usuario `minerva` y que SQLite en modo WAL funcione sobre él.
4. El montaje de `./docs` en solo lectura y que el registro oficial del veredicto se vea dentro del contenedor.
5. `restore_db.py` y `backup_db.py` dentro del contenedor, y `docker compose cp`.
6. Comportamiento específico de Linux (rutas, señales: la parada ordenada con SIGTERM).

**Procedimiento de prueba sin riesgo** (no toca el bot real: puerto 8001, sin LLM, sin Telegram, sin brazo, sin noticias ni push, y con volúmenes
propios y vacíos; después lo borras con `down -v`):

```powershell
$env:MINERVA_REAL_BOT = "false"; $env:MINERVA_PORT = "8001"
docker compose build
docker compose up -d
docker compose ps                                  # STATUS → healthy (hasta ~5 min)
Invoke-RestMethod http://127.0.0.1:8001/health     # mode PAPER_TRADING; telegram_push.enabled false
docker compose exec minerva ls -ld /app/data /app/logs   # dueño: minerva
docker compose exec minerva python scripts/backup_db.py
docker compose exec minerva python scripts/measurement_identity.py --db /app/data/backups/<el respaldo>.db
docker compose down -v                             # borra los volúmenes de PRUEBA
```

## Qué requiere reinicio (definitiva)

| Pieza | ¿Reinicio del bot real? |
|---|---|
| Docker, pruebas, documentación, scripts nuevos (`backup_db`, `restore_db`, `resume_drawdown_stop`, `export_schema_doc`, `export_docker_requirements`, `measurement_identity`) | No |
| 9 archivos de `app/`: docstrings (`core/http.py`, `llm/__init__.py`, `market/bitunix_ws.py`, `strategies/base.py`, `trading/signal_generator.py`) y retirada de código sin uso (`config.py`, `strategies/registry.py`, `backtest_repo.py`, `funding_repo.py`) | No lo exigen; **se cargan con el reinicio ya pendiente de la Fase 5** y no cambian el comportamiento |
| Backend de la Fase 5 (rutas nuevas, `control_audit`, push, cambios de `/pause` `/resume` `/kill`) | **Sí, un solo reinicio**, en :10-:40 de una hora UTC que no sea de cierre de 4 h (ver `docs/MANUAL_OPERACION.md`, §4) |

El frontend compilado (`frontend/dist`) se sirve en caliente; esta fase lo reconstruyó con las mismas fuentes (sin cambios).
