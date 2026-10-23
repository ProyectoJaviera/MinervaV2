# Documentación de Minerva

Índice de toda la documentación. Si es tu primera vez, empieza por el [README del repositorio](../README.md) (qué es y
qué problema investiga) y sigue por la columna que te corresponda.

> **Minerva es 100 % paper trading, experimental y no es asesoría financiera.** Nada de lo que hay aquí envía órdenes
> reales. La medición del valor de la IA es *ciega* y está en curso: no hay resultados publicados.

## Por dónde empezar

| Quiero… | Lee, en este orden |
|---|---|
| **Instalarlo y verlo funcionar** | [Guía de instalación](GUIA_INSTALACION.md) → [Configuración](CONFIGURACION.md) → [Uso diario](USO.md) |
| **Entender qué es y qué mide** | [README](../README.md) → [Metodología](METODOLOGIA.md) → [Arquitectura](ARQUITECTURA.md) → [Informe final](INFORME_FINAL.md) |
| **Operarlo día a día** | [Uso diario](USO.md) → [Solución de problemas](SOLUCION_PROBLEMAS.md) → [Seguridad](SEGURIDAD.md) |
| **Saber cuánto cuesta y qué no está verificado** | [Costos y límites](COSTOS_Y_LIMITES.md) |
| **Contribuir al código** | [CONTRIBUTING](../CONTRIBUTING.md) → [Arquitectura](ARQUITECTURA.md) → [Esquema de la base](ESQUEMA_BD.md) |
| **Ver cómo se construyó** | [Historial de desarrollo](historial/README.md) |

## Guías

| Documento | Qué contiene |
|---|---|
| [GUIA_INSTALACION.md](GUIA_INSTALACION.md) | Instalar desde cero en Windows (con notas para Linux y macOS): requisitos, `.env`, cada credencial paso a paso (Anthropic con límite de gasto, Telegram, usuario y contraseña, `JWT_SECRET`, CoinGecko), primer arranque con Docker, verificación con `/health` y la alternativa sin Docker |
| [CONFIGURACION.md](CONFIGURACION.md) | Referencia de **todas** las variables de `.env.example` (qué hace cada una, su valor por defecto y cuándo cambiarla), los interruptores y las variables de Docker Compose |
| [USO.md](USO.md) | Manual de uso diario: el dashboard pestaña por pestaña, los comandos de Telegram, rutinas, arranque y parada, reinicios seguros, respaldos, actualizar, mudar de equipo, tareas programadas y qué hacer cuando se abra el *gate* |
| [SOLUCION_PROBLEMAS.md](SOLUCION_PROBLEMAS.md) | Preguntas frecuentes y errores comunes con su solución |
| [MUDANZA_DOCKER.md](MUDANZA_DOCKER.md) | Para quien ya tiene un bot nativo en Windows: pasarlo a Docker conservando su base, y volver atrás |

## Conceptos

| Documento | Qué contiene |
|---|---|
| [METODOLOGIA.md](METODOLOGIA.md) | La pregunta, las sombras, las etiquetas, el prompt congelado, los conglomerados, el punto de análisis de 300 por lado, la regla de «una sola mirada», el placebo, el veredicto, δ = 3 %, por qué los resultados se ocultan y qué conclusiones permite y cuáles no |
| [ARQUITECTURA.md](ARQUITECTURA.md) | Componentes, flujo de datos con diagramas, base de datos, aislamiento entre tareas y decisiones de diseño |
| [GLOSARIO.md](GLOSARIO.md) | Los términos del proyecto, de *APROBADA* a *WAL* |
| [SEGURIDAD.md](SEGURIDAD.md) | Manejo de secretos, puertos solo en `127.0.0.1`, autenticación, qué nunca subir y cómo reportar un problema |
| [COSTOS_Y_LIMITES.md](COSTOS_Y_LIMITES.md) | El costo real medido de la API, las fuentes gratuitas con sus términos y atribuciones, los límites conocidos y todo lo **no verificado** |

## Referencia técnica y procedimientos

| Documento | Qué contiene |
|---|---|
| [ESQUEMA_BD.md](ESQUEMA_BD.md) | Esquema de la base de datos, tabla por tabla. **Se genera desde el código** (`python scripts/export_schema_doc.py`); no se edita a mano |
| [FASE3_6_LLM.md](FASE3_6_LLM.md) | Diseño detallado de la medición del valor del LLM: qué recibe el modelo, el prompt, `llm_logs`, la etiqueta inmutable, el control de costo, el placebo, la regla de decisión, la potencia estadística y los pilotos. Referencia técnica escrita durante el desarrollo |
| [VEREDICTO_3_6.md](VEREDICTO_3_6.md) | El procedimiento del veredicto, paso a paso, con los códigos de salida de cada herramienta |
| [PASO_DINERO_REAL.md](PASO_DINERO_REAL.md) | Criterios y riesgos de un eventual paso a dinero real (el reporte solo recomienda) y los puntos de Bitunix sin verificar |
| [INFORME_FINAL.md](INFORME_FINAL.md) | Qué se construyó por fase, el cumplimiento de la especificación, las decisiones clave, lo verificado y lo no verificado, los riesgos y los próximos pasos (fotografía del 2026-10-18) |

## Registro del desarrollo

[`historial/`](historial/README.md) guarda los planes por fase, la bitácora completa (`PROGRESS.md`), la especificación
inicial y los documentos internos: el registro de **cómo se construyó**, fase por fase, con ayuda de un asistente de IA.
No es documentación de uso. Su README explica qué es cada cosa y dónde quedó cada archivo.

## Convenciones de estos documentos

- **Idioma:** español; los identificadores de código (variables, funciones, tablas, columnas) están en inglés.
- **«No verificado»** marca lo que no se pudo comprobar. Nunca se afirma como hecho. La lista consolidada está en
  [Costos y límites](COSTOS_Y_LIMITES.md#lo-no-verificado).
- **Fechas y cifras** de medición llevan siempre su fecha: son una fotografía, no un dato en vivo. Para el estado actual
  de tu instalación, mira el dashboard.
- **Las pruebas vigilan estos documentos** (`tests/unit/test_public_docs.py`): los enlaces y las anclas deben resolver,
  todas las variables de `.env.example` deben estar descritas, y los scripts y opciones citados deben existir.
- **Esta carpeta se monta en el contenedor** de solo lectura y el bot la consulta en cada petición para saber si existe
  el registro oficial del veredicto. **No crees a mano archivos llamados `VEREDICTO_3_6_REGISTRO*.txt`**: abrirían la
  puerta de los resultados sin veredicto. Los genera `scripts/ai_value_report.py` (ver [Uso diario](USO.md#cuando-se-abre-el-gate)).
  Los archivos `PLACEBO_*.txt` son el registro del placebo y tampoco se crean a mano.
