# Registro de cambios

Resumen **por fases** de cómo se construyó Minerva (lo más reciente primero). Minerva todavía no tiene versiones
numeradas ni etiquetas: el repositorio se llevó por fases, y cada una cerró con las pruebas en verde y la aprobación
de su autor. El detalle commit por commit está en `git log`, y la bitácora completa del desarrollo en
[`docs/historial/`](docs/historial/README.md) (con [`docs/INFORME_FINAL.md`](docs/INFORME_FINAL.md) como resumen).

Las fechas son las de los commits (2026). **Nada de lo que hay aquí envía órdenes reales**: todo es paper trading.

## Documentación pública — 2026-10-23

- Nueva documentación para quien llega sin contexto: [`README.md`](README.md), [`docs/README.md`](docs/README.md)
  (índice), guía de instalación, configuración (todas las variables), arquitectura, metodología, uso diario,
  solución de problemas, glosario, seguridad y costos y límites.
- Los planes por fase, `PROGRESS.md` y los documentos internos pasaron a [`docs/historial/`](docs/historial/README.md).
- Licencia MIT, guía de contribución, plantillas de *issue* y de *pull request*.
- Pruebas que vigilan la documentación: enlaces y anclas, variables de `.env.example`, scripts y opciones citados,
  rutas de la API, comandos de Telegram y ausencia de rutas o nombres personales.
- Sin cambios en `app/`, en el prompt congelado ni en la medición.

## Fase 7 — Mudanza del bot a Docker y limpieza de consolas — 2026-10-18 a 2026-10-22

- Probado contra Docker real: 5 defectos corregidos que las pruebas estáticas no veían (respaldos en modo WAL de solo
  lectura, hash de contraseña mutilado por `env_file`, código de la imagen escribible, carpeta `.env` creada por Docker
  Desktop y arranque con Windows).
- `scripts/migrate_to_docker.ps1` (con `-DryRun`, ventana segura y vuelta atrás automática) y
  `scripts/rollback_from_docker.ps1`; `scripts/docker_db.py` (respaldo, copia y informes sobre una copia fresca),
  `scripts/db_fingerprint.py` y la marca `data/MIGRADO_A_DOCKER.txt` que protege la base nativa vieja.
- `scripts/hash_password.py` imprime el hash entre comillas simples (Compose interpolaba los `$`).
- Las pruebas y los scripts ya no abren ventanas de consola en Windows y ninguna prueba puede tocar el Programador de
  tareas real (trampa y guardianes).

## Fase 6 — Docker, pruebas de punta a punta, documentación y limpieza — 2026-10-18

- Imagen Docker multietapa (usuario sin privilegios, versiones exactas de dependencias, puerto solo en `127.0.0.1`),
  `docker-compose.yml`, healthcheck.
- 51 pruebas de punta a punta con el sistema real y solo el exterior simulado (incluido un barrido de que, con la
  puerta de resultados cerrada, ningún canal muestra resultados).
- Herramientas de operación: `backup_db.py`, `restore_db.py`, `resume_drawdown_stop.py`, `measurement_identity.py`,
  `export_schema_doc.py` (genera [`docs/ESQUEMA_BD.md`](docs/ESQUEMA_BD.md)).
- Limpieza: 7 campos de configuración sin uso retirados, `.env.example` completo, escaneo de secretos de lo versionado.

## Fase 5 — Dashboard, controles y notificaciones — 2026-10-14 a 2026-10-18

- Autenticación de un solo usuario (bcrypt, sesión en cookie `httpOnly`), API de solo lectura con listas blancas.
- Dashboard de cinco pestañas (*Resumen*, *Cuenta paper*, *Brazo ejecutado*, *Riesgo y controles*, *Universo y
  backtest*); controles de pausar, reanudar y kill switch con confirmación, mismo origen y auditoría.
- Notificaciones push por Telegram en una tarea aislada que solo lee la base.

## Fase 4 — Noticias y memoria de lecciones — 2026-10-17

- Recolección de noticias gratuita y determinista (GDELT, comunicados de la Fed, Fear & Greed), con atribución y salud
  por fuente. Memoria de lecciones y *post-mortem*. Ambas **apagadas** durante la medición y fuera del prompt congelado.
- Prompt v5 y *features* v3 preparados y **sin usar**.

## Fase 3 — Riesgo, simulación, señales, sombras, medición del LLM y brazo ejecutado — 2026-10-02 a 2026-10-17

- 3.1 y 3.2 (10-02): lógica de SL/TP/trailing compartida con el backtest y motor de riesgo en vivo.
- 3.3 a 3.5 (10-03 y 10-04): generador de señales, monitor de posiciones con reconciliación al reiniciar, operaciones
  sombra y análisis por conglomerados.
- 3.6 (10-04 a 10-17): decisión del LLM, `llm_logs`, etiqueta inmutable, presupuesto con corte duro, pilotos v1 a v4,
  **congelamiento del prompt** (10-12), etiquetado en vivo, regla de tres veredictos, placebo y herramientas del
  veredicto.
- Estabilidad (10-10 y 10-11): detección y reparación de huecos en las velas de 1 minuto que usa la reconciliación,
  *logging* no bloqueante, latido y `GET /health`.
- 3.7 (10-17): bot de Telegram por *long-polling* con confirmación de dos pasos.
- 3.8 y 3.9 (10-17): reporte de paso a dinero real (solo recomienda) y brazo ejecutado de la cuenta paper.
- Resultados ocultos hasta el registro oficial del veredicto (regla del 2026-10-17).

## Fase 2 — Universo dinámico y backtest — 2026-09-30 a 2026-10-04

- Universo (CoinGecko ∩ perpetuos USDT de Bitunix), seis estrategias por reglas y un motor de backtest sin sesgo de
  anticipación, con criterios de descarte escritos antes de correr.
- Resultado: **ninguna estrategia por reglas supera los criterios** (reejecución v2 vigente).

## Fase 1 — Estructura y datos de mercado — 2026-09-30

- Cliente REST y WebSocket públicos de Bitunix, caché de velas, indicadores, `PaperBackend`, persistencia con SQLite,
  scheduler, API y frontend mínimos.

## Fase 0 — Evaluación y diseño — 2026-09-30

- Evaluación crítica de un diseño previo (`openspec.md`, nunca probado), verificación de las APIs contra su
  documentación oficial y decisiones de diseño.
