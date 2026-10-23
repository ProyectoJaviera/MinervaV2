# Historial de desarrollo

Esta carpeta es el **registro de cómo se construyó Minerva**, fase por fase, con ayuda de un asistente de IA
(Claude Code). No es documentación de uso: para instalar, configurar y operar el bot ve al
[índice de la documentación](../README.md). Para un resumen de todo lo construido, de lo verificado y de lo que
no se pudo verificar, lee el [informe final](../INFORME_FINAL.md).

> **Léelo como una bitácora, no como una guía.** Estos documentos se escribieron *durante* el desarrollo: hablan
> en primera y segunda persona (el autor y el asistente), describen decisiones que luego cambiaron, citan archivos
> que ya no existen (por ejemplo `docker/Dockerfile.backend`) y mencionan el equipo concreto donde se desarrolló.
> Sus comandos pueden estar desactualizados. Lo vigente está en los documentos de `docs/`.

## Cómo se trabajó

El asistente recibió unas instrucciones fijas ([`CLAUDE.md`](../../CLAUDE.md)) y una especificación inicial
([`SPEC.md`](SPEC.md)). Las reglas principales: todo es paper trading; nunca leer ni escribir credenciales; no
inventar endpoints ni parámetros de servicios externos (verificar contra su documentación oficial o decir que no se
pudo); trabajar **por fases**, y al terminar cada una correr las pruebas, actualizar la bitácora
([`PROGRESS.md`](PROGRESS.md)) y detenerse a esperar la aprobación del autor antes de seguir.

El ciclo de cada fase fue: investigar → proponer un plan por escrito → aprobación → implementar con commits
pequeños y pruebas → verificar contra datos reales (siempre sobre una *copia* de la base) → documentar lo hecho y
lo que no se pudo verificar.

## Qué hay en cada documento

| Fase | Tema | Documentos |
|---|---|---|
| 0 | Evaluación del diseño previo y verificación de las APIs | [`FASE0.md`](FASE0.md), [`openspec.md`](openspec.md) (el diseño previo, nunca probado), [`SPEC.md`](SPEC.md) (la especificación inicial) |
| 1 | Estructura y datos de mercado | Está en [`PROGRESS.md`](PROGRESS.md) |
| 2 | Universo dinámico y backtest | [`FASE2_PLAN.md`](FASE2_PLAN.md), [`FASE2_CRITERIOS.md`](FASE2_CRITERIOS.md) (criterios escritos *antes* de correr), [`FASE2_BLOQUEO_RED.md`](FASE2_BLOQUEO_RED.md), [`FASE2_INTEGRIDAD_VELAS.md`](FASE2_INTEGRIDAD_VELAS.md), [`FASE2_REEJECUCION.md`](FASE2_REEJECUCION.md), [`FASE2_REEJECUCION_TABLAS.md`](FASE2_REEJECUCION_TABLAS.md), [`FASE2_RESULTADOS.md`](FASE2_RESULTADOS.md), [`FASE2_RIESGO.md`](FASE2_RIESGO.md), [`FASE2_RIESGO_v1.md`](FASE2_RIESGO_v1.md) |
| 3 | Riesgo, simulador, señales, sombras, medición del LLM, brazo ejecutado | [`FASE3_PLAN.md`](FASE3_PLAN.md), [`FASE3_7_TELEGRAM.md`](FASE3_7_TELEGRAM.md), [`FASE3_9_BRAZO_EJECUTADO.md`](FASE3_9_BRAZO_EJECUTADO.md); el diseño de la medición ([`FASE3_6_LLM.md`](../FASE3_6_LLM.md)) sigue en `docs/` (ver abajo) |
| 4 | Noticias y memoria de lecciones | [`FASE4_PLAN.md`](FASE4_PLAN.md) |
| 5 | Dashboard completo, controles y notificaciones | [`FASE5_PLAN.md`](FASE5_PLAN.md) |
| 6 | Docker, pruebas de punta a punta, documentación y limpieza | [`FASE6_PLAN.md`](FASE6_PLAN.md), [`MANUAL_OPERACION.md`](MANUAL_OPERACION.md) (el manual original, sustituido por [`USO.md`](../USO.md)) |
| 7 | Mudanza del bot a Docker | [`MUDANZA_DOCKER_ENSAYO.md`](MUDANZA_DOCKER_ENSAYO.md) (la guía original con el ensayo general y lo que falló), [`MIGRACION_PC.md`](MIGRACION_PC.md) (tareas programadas y mudanza nativa, sustituido por [`USO.md`](../USO.md)); la guía vigente es [`MUDANZA_DOCKER.md`](../MUDANZA_DOCKER.md) |
| Todas | Bitácora completa, en orden cronológico (decisiones, hallazgos, errores corregidos, cifras) | [`PROGRESS.md`](PROGRESS.md) |

## Lo que sigue en `docs/` (y por qué)

Algunos documentos de fase **no** se movieron porque el código y las herramientas de la medición los citan por su
ruta (en mensajes que ve quien opera el bot):

- [`VEREDICTO_3_6.md`](../VEREDICTO_3_6.md): el procedimiento del veredicto. El dashboard, Telegram y los informes
  dicen «seguir docs/VEREDICTO_3_6.md».
- [`PASO_DINERO_REAL.md`](../PASO_DINERO_REAL.md): criterios y riesgos de un eventual paso a dinero real.
- [`FASE3_6_LLM.md`](../FASE3_6_LLM.md): el diseño detallado de la medición del valor del LLM. El placebo imprime
  «Para copiar al registro de docs/FASE3_6_LLM.md» y ahí se guarda su resultado.
- [`ESQUEMA_BD.md`](../ESQUEMA_BD.md): se genera desde el código.

## Rutas antiguas

Los comentarios del código (`app/`, `scripts/`, `tests/`) y los propios documentos de esta carpeta citan los
archivos con la ruta que tenían cuando se escribieron. Esta tabla dice dónde están ahora:

| Ruta antigua | Ruta actual |
|---|---|
| `PROGRESS.md` | [`docs/historial/PROGRESS.md`](PROGRESS.md) |
| `docs/SPEC.md` | [`docs/historial/SPEC.md`](SPEC.md) |
| `docs/openspec.md` | [`docs/historial/openspec.md`](openspec.md) |
| `docs/FASE0.md` | [`docs/historial/FASE0.md`](FASE0.md) |
| `docs/FASE2_*.md` (nueve documentos) | `docs/historial/FASE2_*.md` |
| `docs/FASE3_PLAN.md`, `docs/FASE3_7_TELEGRAM.md`, `docs/FASE3_9_BRAZO_EJECUTADO.md` | `docs/historial/` (mismo nombre) |
| `docs/FASE4_PLAN.md`, `docs/FASE5_PLAN.md`, `docs/FASE6_PLAN.md` | `docs/historial/` (mismo nombre) |
| `docs/MANUAL_OPERACION.md` | [`docs/historial/MANUAL_OPERACION.md`](MANUAL_OPERACION.md); lo vigente: [`docs/USO.md`](../USO.md) |
| `docs/MIGRACION_PC.md` | [`docs/historial/MIGRACION_PC.md`](MIGRACION_PC.md); lo vigente: [`docs/USO.md`](../USO.md) |
| `docs/MUDANZA_DOCKER.md` (versión con el ensayo) | [`docs/historial/MUDANZA_DOCKER_ENSAYO.md`](MUDANZA_DOCKER_ENSAYO.md); la guía vigente conserva el nombre [`docs/MUDANZA_DOCKER.md`](../MUDANZA_DOCKER.md) |

## Un aviso sobre lo que leerás aquí

Las cifras de avance de la medición (conglomerados efectivos, costos acumulados, disponibilidad) son las de **la
fecha de cada nota**; no se actualizan. El avance actual lo muestra el dashboard de tu propia instalación. Y como
la medición del valor del LLM es *ciega* a propósito, **este repositorio no publica resultados de esa medición**:
ni PnL, ni tasas de acierto, ni la comparación entre señales aprobadas y rechazadas. Lo que sí hay son los
resultados del backtest de las estrategias por reglas (Fase 2), las cifras de los pilotos del prompt (validez del
JSON, tasa de aprobación, costo) y las de las pruebas.
