# Entorno de desarrollo

Cómo preparar el entorno, correr el bot de desarrollo sin tocar el real, depurar, ver los logs, correr
las pruebas por partes y regenerar la documentación técnica. Todo lo de aquí es seguro para la
medición en curso: el bot de desarrollo corre en otro puerto, sobre **una copia** de la base, con el
LLM, Telegram, el brazo ejecutado, las noticias y la memoria apagados por construcción.

## Las reglas que valen siempre

1. **El bot real (puerto 8000, contenedor `minerva`) no se toca.** No lo detengas, reinicies ni
   reconstruyas desde una sesión de desarrollo. Desplegar un cambio es una acción deliberada, en la
   [ventana segura](RECETAS.md#desplegar-un-cambio-al-contenedor).
2. **Todo desarrollo y prueba usa una base temporal y el puerto 8001.** Nunca apuntes `DATABASE_PATH` a
   `data/` ni abras la base de trabajo con una herramienta nueva: se trabaja sobre una copia.
3. **Nunca imprimas, copies ni subas el `.env`.** Las pruebas no necesitan credenciales ni red.
4. **Antes de entregar un script que lea la base real**, ejecútalo contra una copia temporal de esa base
   (con la ruta comprobada con un `assert` distinta de `settings.database_path`): las pruebas con datos
   sintéticos no bastan.
5. **Lee [`ZONAS_PROTEGIDAS.md`](ZONAS_PROTEGIDAS.md)** antes de tocar algo que no sea documentación.

## Preparar el entorno

**Python 3.11 o 3.12** (`requires-python = ">=3.11,<3.13"`). Con 3.13 o superior `pip install` se
niega a instalar el proyecto. En Windows, con [uv](https://docs.astral.sh/uv/) usa su Python 3.11: el
3.12 que descarga uv aborta con `OPENSSL_Uplink` en cuanto se crea un contexto SSL (detalle en
[`../../CONTRIBUTING.md`](../../CONTRIBUTING.md#preparar-el-entorno)).

```powershell
# Windows (PowerShell), con uv:
uv venv --python 3.11
uv pip install -e ".[dev]"

# o con venv y pip:
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

```bash
# Linux y macOS:
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

El extra `dev` agrega `pytest`, `pytest-asyncio`, `ruff`, `pyyaml`, `packaging` y `pdoc==16.0.0` (versión
exacta: la usa la referencia automática). Detrás de una red con inspección TLS, añade `--system-certs` a
`uv pip install`.

**Node.js 22.22 o superior** para el dashboard (la imagen Docker usa Node 24):

```bash
npm --prefix frontend ci
```

**El `.env`** solo hace falta para arrancar el bot. Cópialo desde `.env.example` (que tiene todas las
variables, con valores vacíos para las credenciales) y llénalo siguiendo
[`../GUIA_INSTALACION.md`](../GUIA_INSTALACION.md). Dos precauciones:

- `app/config.py` crea `settings` al importarse y lee el `.env` **del directorio de trabajo**; si existe,
  también lo leen las pruebas. Corre las pruebas y los scripts desde la raíz del repositorio.
- Para el login del dashboard necesitas `AUTH_USERNAME`, `AUTH_PASSWORD_HASH` y `JWT_SECRET`;
  `scripts/hash_password.py` genera el hash. El hash va **entre comillas simples** en el `.env`.

En todos los comandos de abajo, `python` es el del entorno virtual (`.\.venv\Scripts\python.exe` en
Windows, `.venv/bin/python` en Linux y macOS).

## Correr las pruebas

La suite completa tiene unas 2.300 pruebas y tarda unos 11 o 12 minutos; **mientras desarrollas corre
solo lo que tocas.** Más sobre cómo está organizada y qué protege cada parte en
[`PRUEBAS.md`](PRUEBAS.md).

```bash
python -m pytest                                  # todo (≈ 11-12 min)
python -m pytest tests/unit                       # solo las unitarias
python -m pytest tests/e2e                        # solo las de punta a punta
python -m pytest tests/unit/test_risk_engine.py   # un archivo
python -m pytest tests/unit/test_risk_engine.py::test_nombre   # una prueba
python -m pytest -k "trailing and not tick"       # por expresión
python -m pytest -x --lf                          # parar en el primer fallo / repetir lo que falló
python -m pytest -p no:cacheprovider              # sin dejar .pytest_cache
```

- `asyncio_mode = "auto"` (en `pyproject.toml`): una función `async def test_...` se ejecuta sin decorador.
- Las pruebas marcadas `@pytest.mark.slow` (`tests/integration/test_bitunix_live.py`) van contra la red real
  de Bitunix y **se omiten por defecto**. Para correrlas:
  `MINERVA_RUN_LIVE_TESTS=1 python -m pytest tests/integration -m slow` (en PowerShell:
  `$env:MINERVA_RUN_LIVE_TESTS = "1"` antes del comando).
- Ninguna prueba abre ventanas de consola ni toca el Programador de tareas real (ver
  [`PRUEBAS.md`](PRUEBAS.md#las-pruebas-guardianas)).

**Lint y formato:**

```bash
python -m ruff check .          # lint (E, F, I, UP, B; línea de 100 caracteres; destino py311)
python -m ruff check . --fix    # arregla lo que se pueda (orden de imports, etc.)
```

Un `ruff check .` limpio es condición para dar una tarea por terminada.

> **No ejecutes `ruff format .` sobre el repositorio.** El código no está formateado con `ruff format`
> (`ruff format --check .` reporta muchos archivos por reescribir) y aplicarlo en bloque tocaría cientos de
> líneas y también `app/llm/prompts.py` y `app/llm/market_features.py`, cuyos bytes están fijados por hash
> (ver [`ZONAS_PROTEGIDAS.md`](ZONAS_PROTEGIDAS.md#prompt-y-features)). Lo que se exige es `ruff check .`;
> formatea a mano el código que escribes, con el estilo del archivo que tocas.

**Frontend:**

```bash
npm --prefix frontend run test        # Vitest (una vez)
npm --prefix frontend run typecheck   # tsc --noEmit
npm --prefix frontend run build       # tsc --noEmit && vite build, deja frontend/dist
```

**No hay integración continua configurada** (el repositorio no define flujos de GitHub Actions): las
comprobaciones corren en tu equipo. El orden para dar algo por bueno es `ruff`, las pruebas que tocas, la suite completa, y `vitest` y
`build` si tocaste el frontend.

## Correr el bot de desarrollo en el puerto 8001

`scripts/run_dev_server.py` copia una base real a un archivo temporal y arranca el bot completo sobre
esa copia, en el puerto 8001 por defecto. **Nunca toca el bot real ni su base.**

```bash
python scripts/run_dev_server.py --source-db <base a copiar>
python scripts/run_dev_server.py --source-db <base a copiar> --port 8002
```

- `--source-db` es obligatorio a propósito (no hay un valor por defecto derivado de la configuración).
- La copia se hace con la API de backup de SQLite en modo solo lectura, por lo que es consistente aunque
  la base de origen esté en uso. El script imprime la ruta de la copia (`.../minerva_dev_server_*/dev.db`).
- **Lo que el script fuerza apagado, sin importar el `.env`:** `LLM_LIVE_LABELING_ENABLED`,
  `ANTHROPIC_API_KEY` (a vacío), `TELEGRAM_COMMANDS_ENABLED`, `TELEGRAM_BOT_TOKEN` (a vacío),
  `PAPER_EXECUTION_ENABLED`, `NEWS_COLLECTION_ENABLED`, `LESSONS_MEMORY_ENABLED` y
  `TELEGRAM_PUSH_ENABLED`. Es un candado en el código: sin él, la instancia de desarrollo llamaría de
  verdad a la API de Anthropic en cuanto el scheduler generara una sombra nueva (gasto real) y competiría
  por el token de Telegram (un token solo admite un consumidor de `getUpdates`).
- Sí hace llamadas de red públicas de solo lectura y sin costo al arrancar (descubrimiento del universo en
  CoinGecko y descarga de velas de Bitunix); por eso el primer arranque tarda.
- Cuando el bot corre en Docker, **la base viva está en un volumen**, no en `data/`; el archivo
  `data/minerva_estabilidad.db` es entonces una copia vieja. Saca una copia fresca con
  `python scripts/docker_db.py export --out-dir data/backups --prefix dev` (imprime su ruta, verificada con
  SHA-256) y pásala a `--source-db`. Con el bot nativo de Windows, `python scripts/backup_db.py`.
- Para comprobar que está vivo: `python scripts/check_health.py --port 8001` (0 = `ok`, 1 = `stale`,
  2 = sin respuesta), o `http://127.0.0.1:8001/health`.

### Con el dashboard

El proxy de Vite (`frontend/vite.config.ts`) manda `/api` y `/health` a `http://localhost:8001`: **el
desarrollo del dashboard es siempre contra el puerto 8001**, nunca el 8000.

```bash
python scripts/run_dev_server.py --source-db <copia>     # una terminal
npm --prefix frontend run dev                            # otra; Vite imprime la URL (5173 por defecto)
```

Con el servidor de Vite, los cambios del frontend se ven al guardar. Sin él, `npm --prefix frontend run
build` deja `frontend/dist`, que el backend sirve en `/` si existe (el servidor de desarrollo también).
El login usa el usuario y la contraseña de tu `.env`.

## Depurar

- **Una prueba que falla:** `python -m pytest <ruta>::<prueba> -x -vv`; con `--pdb` entra al depurador en el
  fallo. `-s` deja pasar la salida estándar.
- **El bot de desarrollo:** `LOG_LEVEL=DEBUG python scripts/run_dev_server.py --source-db <copia>` (en
  PowerShell, fija `$env:LOG_LEVEL = "DEBUG"` antes). `breakpoint()` funciona en el proceso del servidor, con
  la terminal donde lo lanzaste.
- **El estado de la base:** ábrela siempre en solo lectura y sobre una copia:

  ```bash
  python -c "import sqlite3; c = sqlite3.connect('file:<copia>?mode=ro', uri=True); print(c.execute('select count(*) from signals').fetchone())"
  ```

  Con la medición ciega **imprime solo conteos**, nunca PnL, tasas ni lecciones activas, hasta que exista el
  registro oficial del veredicto (ver [`ZONAS_PROTEGIDAS.md`](ZONAS_PROTEGIDAS.md#ocultamiento-de-resultados)).
- **El avance de la medición:** `python scripts/measurement_status.py --db <copia>` y, para confirmar que hay
  una sola identidad (modelo, prompt, versión de features), `python scripts/measurement_identity.py --db
  <copia>`. Con Docker: `python scripts/docker_db.py run measurement_status.py`.
- **Qué falló en silencio:** ver la tabla de [`FLUJO_EJECUCION.md`](FLUJO_EJECUCION.md#qué-falla-en-silencio-y-cómo-verlo).
- **Un script que falla al leer la base real** es lo que la regla de la copia previene: ensáyalo contra una
  copia con datos reales, no solo con los sintéticos de las pruebas.

## Ver los logs

- **Archivo:** `logs/minerva.log` en el bot nativo y en el de desarrollo (en el de desarrollo,
  `<carpeta de la copia>/logs/minerva.log`; `LOG_DIR` lo fija el script). Rota a los 10 MB con 5 respaldos.
  El formato es `fecha | NIVEL | módulo | mensaje`.
- **Por qué un log nunca bloquea el bot:** `logger.info(...)` solo encola; dos hilos independientes
  escriben al archivo y a la consola. Si la cola se llena se descartan los mensajes de menor prioridad y se
  cuentan (`log_dropped_count` en `/health` y en el latido). Ver `app/core/logging.py`.
- **Con Docker:** `docker compose logs --tail 200 minerva` (de solo lectura). **Evita `-f` y no dejes los
  logs abiertos mientras la medición sea ciega:** los registros de INFO incluyen líneas como
  `Monitor cerro trade=… motivo=…` y `Posicion cerrada (paper) … pnl_neto=…`, que muestran motivos de
  cierre y PnL (es un límite conocido, ver [`ZONAS_PROTEGIDAS.md`](ZONAS_PROTEGIDAS.md#ocultamiento-de-resultados)).
- **Qué mirar primero:** `GET /health`, que resume el ciclo del scheduler (`last_cycle_at`, `status`), el
  latido, el etiquetado (`frozen_hash_ok`), el brazo ejecutado, las noticias y el push.

## Docker

La imagen (`Dockerfile`, `minerva:local`) tiene tres etapas: compila el frontend con Node, instala en un
venv las versiones **exactas** de `docker/requirements.lock` (solo ruedas binarias) y arma una imagen final
sin Node ni compiladores. Copia `app/` y `scripts/` (no `tests/` ni `docs/`), corre como un usuario sin
privilegios con el código de solo lectura y no lleva secretos: el `.env` se monta de solo lectura en
`/app/.env` y `./docs` también, de solo lectura, en `/app/docs`.

- **Qué lleva la imagen y qué no:** las dependencias de ejecución, no las de `dev` (`pytest`, `ruff`,
  `pdoc`...). Por eso `scripts/build_dev_docs.py` y las pruebas se corren en tu entorno, no en el contenedor.
- **Cuando cambias las dependencias** de `pyproject.toml`, regenera las versiones exactas:
  `python scripts/export_docker_requirements.py` (con `--check` verifica que estén al día).
- **El contenedor sigue con el código viejo** hasta que se reconstruye la imagen y se recrea. Un
  `docker compose restart` no aplica un cambio de código.
- **Las pruebas de Docker no usan el demonio:** verifican el healthcheck, la paridad de los interruptores del
  compose con `scripts/start_bot.ps1`, las propiedades de seguridad del `Dockerfile` y del compose, y usan un
  `docker` falso (`tests/support/fake_docker.py`) para los scripts de la mudanza. Si el CLI de Docker está
  instalado, una prueba valida `docker compose config` en una carpeta temporal con un `.env` **falso**.
- **No ejecutes `docker compose up`, `down`, `build`, `stop` ni `restart` del proyecto `minerva`** desde una
  sesión de desarrollo: son las acciones que afectan al bot real. El despliegue está en
  [`RECETAS.md`](RECETAS.md#desplegar-un-cambio-al-contenedor). Para probar el compose sin tocar el real,
  usa otro nombre de proyecto (`-p minerva-docs`), `--no-build`, un `.env` de mentira
  (`MINERVA_ENV_FILE`) y otro puerto (`MINERVA_PORT=8001`, `MINERVA_REAL_BOT=false`,
  `MINERVA_RESTART=no`): así no comparte volúmenes ni nombre con el bot real. *No verificado en esta
  documentación: no se ejecutó ese flujo mientras se escribía; los nombres de las variables salen de
  `docker-compose.yml`.*

## Documentación técnica

Esta documentación tiene una parte **generada** y otra escrita a mano (ver
[el README](README.md#cómo-está-escrita-y-cómo-se-mantiene-esta-documentación)).

### El formato de los docstrings

Todo módulo, clase y función pública de `app/` y `scripts/` lleva un docstring con este formato (Google
más una sección propia). Un guardián (`tests/unit/test_docstrings.py`) lo exige y falla indicando qué falta:

```python
async def open_position(self, symbol: str, side: Side, margin_usdt: float) -> Trade:
    """Abre una posicion en la cuenta paper al precio real de mercado.

    Explicacion de lo que hace y de las reglas que importan, en prosa.

    Args:
        symbol: simbolo, p. ej. `BTCUSDT`.
        side: direccion.
        margin_usdt: margen a comprometer, en USDT.

    Returns:
        El `Trade` abierto, ya con su `id`.

    Raises:
        RiskRejectedError: si el motor de riesgo bloquea la entrada.

    Efectos:
        Lee `contract_specs_cache`. Red: GET `tickers` de Bitunix. Escribe `trades` y
        `risk_rejections`.
    """
```

- **Qué se audita:** los módulos, las clases y las funciones y métodos **públicos** (los que no empiezan por
  `_`). Lo privado no está obligado, pero documentarlo no estorba.
- **`Args:`** si la función recibe parámetros (sin contar `self` ni `cls`): una entrada por cada uno y
  **ninguna** de un parámetro que no existe. **`Returns:`** (o `Yields:`) si devuelve un valor; las
  propiedades (`@property`) quedan exentas. **`Raises:`** si lanza una excepción de forma explícita
  (`raise X`): debe nombrar cada clase lanzada.
- **`Efectos:`** es la sección propia del proyecto y es obligatoria si la función toca la base (`execute`,
  `fetch_*`, cualquier `*_repo.*`), la red (clientes REST, Telegram, `httpx`), el disco (`open`,
  `write_text`...) o crea tareas asyncio. Dice qué **tablas lee y escribe** (con sus nombres reales), qué
  **servicio externo** llama (Bitunix, CoinGecko, Anthropic, Telegram) y qué **archivos** toca.
- **El formato del texto:** en español y, por la convención del código existente, sin tildes; líneas de a
  lo sumo 100 caracteres (lo exige `ruff`); los identificadores de código, en inglés. Primera línea: una
  frase que diga para qué sirve.
- **Las excepciones del guardián:** `app/llm/prompts.py` y `app/llm/market_features.py` (están fijados por
  hash: ni un byte puede cambiar, ni un docstring), que solo deben tener el docstring; los métodos cuyo
  cuerpo es `...` (interfaces `Protocol`), que se documentan en el docstring de la clase.
- **Un cambio de docstrings es seguro para la medición:** el AST de la función no cambia (el script
  `check_measurement_safety.py` lo clasifica como `solo documentacion`).

### Regenerar y verificar

```bash
python scripts/build_dev_docs.py                  # regenera lo desactualizado
python scripts/build_dev_docs.py --force          # regenera la referencia pdoc aunque parezca al día
python scripts/build_dev_docs.py --check          # solo verifica; sale con 1 si algo está viejo
python scripts/build_dev_docs.py --check --strict # además regenera y compara (solo si el entorno es igual)
```

`build_dev_docs.py` hace tres cosas: (1) genera la referencia pdoc de `app/` y `scripts/` en
`docs/dev/api/`; (2) rellena los **hechos derivados del código** de cada archivo en las guías de
`modulos/`; (3) actualiza el grafo de paquetes de [`README.md`](README.md#mapa-de-dependencias-entre-paquetes).

- **pdoc importa los módulos**, así que corre en un **proceso aparte** con un directorio de trabajo
  vacío, el entorno limpio (sin las variables de `.env.example` ni `MINERVA_*`) y `os.chdir` anulado: nunca
  lee tu `.env` ni vuelca la configuración en las páginas. Al terminar, el resultado se rechaza si
  contiene una ruta local o algo con forma de credencial.
- **`docs/dev/api/manifest.json`** guarda una huella de la *superficie documentada* de cada archivo (firmas,
  docstrings y asignaciones de módulo y clase; **no** los cuerpos de las funciones) y de cada página
  generada. `--check` compara contra eso, así que cambiar el cuerpo de una función no obliga a regenerar,
  pero cambiar un docstring o una firma sí. La versión de pdoc, de Python y la plataforma quedan en el
  manifiesto: `--check --strict` regenera y compara solo si el entorno es el mismo.
- **Un archivo nuevo** en `app/` o `scripts/` añade un borrador en su guía con `_Pendiente de documentar._`;
  mientras quede alguno, `--check` falla. Escríbelo (responsabilidad, funciones clave, invariantes,
  trampas) y vuelve a correr.
- **Las pruebas que vigilan esto:** `tests/unit/test_build_dev_docs.py` (el generador),
  `tests/unit/test_dev_docs_facts.py` (los hechos), `tests/unit/test_docstrings.py` (el formato) y
  `tests/unit/test_dev_docs.py` (que los documentos de `docs/dev/` estén completos, los enlaces resuelvan y la
  referencia esté al día).
- **`docs/dev/api/` está marcado como generado** en `.gitattributes`: no lo edites a mano ni resuelvas
  conflictos en él a mano; regenéralo.

## Gancho pre-commit opcional (no instalado)

Este repositorio **no instala ningún gancho** de git (en `.git/hooks/` solo están los `.sample`). Si
quieres que git avise antes de confirmar un cambio que toca una zona protegida de la medición, créalo tú:
guarda esto como `.git/hooks/pre-commit`:

```sh
#!/bin/sh
# Avisa y frena el commit si lo que vas a confirmar toca una zona protegida de la medición.
exec .venv/Scripts/python.exe scripts/check_measurement_safety.py --staged
```

- Git ejecuta el gancho desde la raíz del repositorio; en Windows lo ejecuta el `sh` de Git para Windows.
- En Linux o macOS la ruta del intérprete es `.venv/bin/python` y el archivo debe ser ejecutable
  (`chmod +x .git/hooks/pre-commit`).
- El script solo **lee** (`git` y archivos del repositorio); frena el commit con código `1` si hay cambios
  en zonas protegidas. Si el cambio está revisado y es deliberado, confirma con `git commit --no-verify`.
- Añade `--strict` a la línea si quieres que también frene con las zonas "para revisar".
- Un gancho local no se versiona ni se comparte: cada clon lo crea por su cuenta. Para ver qué detectaría
  sin instalar nada: `python scripts/check_measurement_safety.py --staged`.

Qué es cada zona y qué hacer con un aviso: [`ZONAS_PROTEGIDAS.md`](ZONAS_PROTEGIDAS.md#cómo-comprobarlo-scriptscheck_measurement_safetypy).

## Lo no verificado

- **El flujo de pruebas del compose con otro nombre de proyecto** (ver la sección de Docker) se describe a
  partir de `docker-compose.yml` y de lo que ya registra el proyecto; no se ejecutó al escribir esta guía.
- **El número de pruebas y el tiempo de la suite** (unas 2.300 y unos 11 o 12 minutos) son de una máquina
  concreta; varían con el equipo.
