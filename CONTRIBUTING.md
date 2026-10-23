# Cómo contribuir a Minerva

Gracias por el interés. Minerva es un proyecto de investigación pequeño y con reglas estrictas: antes de
tocar nada, lee las tres de abajo.

## Las tres reglas que no se negocian

1. **Todo es paper trading.** No se aceptan cambios que envíen órdenes reales a ningún exchange ni que activen
   `BitunixBackend` (existe solo como interfaz documentada y desactivada). Una prueba por AST comprueba que
   nada de `app/` lo importa.
2. **No se toca el prompt congelado durante una medición.** La medición del valor del LLM depende de que el
   texto del prompt, las features y el modelo no cambien. No edites `app/llm/prompts.py` ni
   `app/llm/market_features.py` (sus huellas están fijadas en `tests/unit/test_prompt_v5_prepared.py`), no cambies
   `FROZEN_PROMPT_SHA256` ni `FEATURES_VERSION`, y no cambies el modelo por defecto (`ANTHROPIC_SONNET_MODEL`).
   Si el hash deja de coincidir, el etiquetado se detiene solo. Una idea de prompt nuevo es un *experimento
   nuevo* (hay un borrador sin usar en `app/llm/prompts_v5.py`): abre un issue antes de escribir código y
   consulta [`docs/METODOLOGIA.md`](docs/METODOLOGIA.md).
3. **Los resultados no se muestran sin el registro oficial.** Cualquier canal nuevo (API, dashboard, Telegram,
   informes) que pueda enseñar PnL, saldo, precios de salida o motivos de cierre debe pasar por
   `is_outcome_reveal_allowed` (`app/trading/measurement_status.py`) y fallar *cerrado*. Hay un barrido de
   punta a punta (`tests/e2e/`) que lo vigila.

## Preparar el entorno

Necesitas Python 3.11 o 3.12 (`requires-python = ">=3.11,<3.13"`; con 3.13 o superior `pip install` se niega a
instalar el proyecto, así que si tu `python` es más nuevo crea el entorno con `py -3.12 -m venv .venv` en Windows,
`python3.12 -m venv .venv` en Linux o macOS, o `uv venv --python 3.12`) y, para el dashboard, Node.js 22.22 o
superior (se usa Node 24 en la imagen Docker).

```bash
python -m venv .venv
# Linux/macOS:
.venv/bin/python -m pip install -e ".[dev]"
# Windows (PowerShell):
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

npm --prefix frontend ci
```

Las pruebas no necesitan credenciales ni red. Copia `.env.example` a `.env` **solo si vas a arrancar el bot**
(ver [`docs/GUIA_INSTALACION.md`](docs/GUIA_INSTALACION.md)); si tienes uno, `app/config.py` lo lee también al
correr las pruebas, igual que al ejecutar el bot.

## Antes de abrir un pull request

Todo esto debe estar en verde:

```bash
pytest                              # unas 2.200 pruebas (unitarias, de integración y de punta a punta; unos 11 min)
ruff check .
npm --prefix frontend run test      # Vitest
npm --prefix frontend run build     # tsc + vite build
```

- La suite completa tarda; mientras desarrollas corre solo lo que tocas (`pytest tests/unit/test_risk_engine.py`).
- Las pruebas contra la red real de Bitunix están marcadas `@pytest.mark.slow` y se omiten por defecto. Para
  correrlas: `MINERVA_RUN_LIVE_TESTS=1 pytest tests/integration -m slow`.
- Toda función de riesgo o de simulación (SL/TP/trailing, liquidación, comisiones, funding) necesita pruebas con
  casos límite, no solo el camino feliz.
- Si cambias el esquema de la base, regenera su documento: `python scripts/export_schema_doc.py` (y
  `python scripts/export_schema_doc.py --check` para verificar que está al día).
- Si cambias las dependencias de ejecución de `pyproject.toml`, regenera las versiones exactas de la imagen:
  `python scripts/export_docker_requirements.py` (con `--check` verifica).
- Si cambias variables de configuración, documentos o comandos, la documentación tiene pruebas que lo exigen
  (`tests/unit/test_public_docs.py`): enlaces y anclas que resuelven, todas las variables de `.env.example`
  descritas en [`docs/CONFIGURACION.md`](docs/CONFIGURACION.md), scripts y opciones que existen de verdad.

### Pruebas y Windows

El proyecto se desarrolló en Windows. Las pruebas **no abren ventanas de consola** (todo proceso hijo se lanza
con `CREATE_NO_WINDOW` y los `Start-Process` con `-WindowStyle Hidden`) y **no pueden tocar el Programador de
tareas real**: `tests/conftest.py` lo sustituye por una trampa durante toda la sesión. No lo evites; lo vigilan
`tests/unit/test_no_console_windows.py` y `tests/unit/test_no_real_task_scheduler.py`.

## Convenciones

- **Commits pequeños, de una línea, con [Conventional Commits](https://www.conventionalcommits.org/)**:
  `feat: …`, `fix: …`, `docs: …`, `test: …`, `refactor: …`, `chore: …`. Un cambio lógico por commit.
- **Idioma**: documentación, comentarios y mensajes al usuario en español; identificadores de código (variables,
  funciones, clases, tablas y columnas) en inglés.
- **Python**: tipado con *type hints*, formato y lint con `ruff` (`line-length = 100`), `asyncio` para todo lo que
  espera red o disco.
- **Toda decisión del LLM se valida con un esquema (pydantic) y se guarda completa en la base de datos.**
- **Cada pieza opcional nueva** tiene su interruptor propio, apagado por defecto, corre en una tarea aislada
  (un fallo suyo no alcanza al scheduler, al etiquetado ni al monitor) y trae pruebas de aislamiento.
- **Nunca** incluyas claves, tokens, `chat_id`, hashes de contraseñas ni datos de una base real en código, pruebas,
  documentos o commits. Una prueba (`tests/unit/test_repo_hygiene.py`) escanea el repositorio y falla si aparece
  algo con forma de credencial.
- **Lo que no puedas verificar, dilo.** No inventes endpoints, parámetros ni firmas de los servicios externos:
  contrasta con su documentación oficial y marca como *no verificado* lo que no puedas comprobar (mira
  [`docs/COSTOS_Y_LIMITES.md`](docs/COSTOS_Y_LIMITES.md) para el formato).

## Proponer un cambio

1. Abre un *issue* (hay plantillas) si el cambio no es trivial, sobre todo si toca el riesgo, la simulación o la
   medición.
2. Crea una rama, haz commits pequeños y abre el *pull request* con la plantilla.
3. Explica el **porqué**, qué probaste y qué *no* pudiste probar.

Para vulnerabilidades o credenciales expuestas **no abras un issue público**: sigue
[`docs/SEGURIDAD.md`](docs/SEGURIDAD.md).

## Desarrollo con ayuda de IA

El proyecto se construyó, fase por fase, con un asistente de IA (Claude Code). Las instrucciones que se le dieron
están en [`CLAUDE.md`](CLAUDE.md) y el registro del desarrollo en
[`docs/historial/`](docs/historial/README.md). Si contribuyes con ayuda de una herramienta de IA, aplican las mismas
reglas: tú eres responsable de lo que envías y de haberlo probado.
