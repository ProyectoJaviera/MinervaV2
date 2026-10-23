# Solución de problemas y preguntas frecuentes

Primero las dudas habituales y después los errores frecuentes con su solución. Si no encuentras lo tuyo, al final está
cómo pedir ayuda sin filtrar tus claves.

## Preguntas frecuentes

**¿Minerva opera con dinero real?** No. Es 100 % paper trading: usa solo datos públicos de mercado de Bitunix y simula las
operaciones. No tiene credenciales del exchange ni ejecución real (`BitunixBackend` es solo una interfaz documentada y
desactivada). Y, con la evidencia actual, no hay base para pasar a dinero real: ver [Paso a dinero real](PASO_DINERO_REAL.md).

**¿Es asesoría financiera?** No. Es un proyecto de investigación experimental. El trading de futuros apalancados, real o
simulado, puede perder dinero y nada de lo que hay aquí garantiza ganancias.

**¿Por qué no veo ganancias, saldo ni PnL?** Es a propósito. La medición del valor de la IA es *ciega*: ningún canal muestra
resultados hasta que se junten 300 conglomerados efectivos por lado **y** exista el registro oficial del veredicto. Mirarlos
antes sesgaría el resultado. Explicación completa: [Por qué se ocultan los resultados](METODOLOGIA.md#por-qué-se-ocultan-los-resultados).

**¿Cuánto tarda en haber un veredicto?** Meses. En la medición del autor se juntaban ≈ 1,25 conglomerados por día de
calendario y lado (≈ 5 por día con el bot encendido, que solo estuvo encendido el 23,6 % del tiempo). Un equipo apagado no
recupera medición. El dashboard (pestaña *Resumen*) muestra el avance y los días estimados.

**¿Cuánto cuesta?** Solo la API de Anthropic, del orden de 0,005 USD por señal etiquetada, con un tope diario de 1 USD y corte
duro. Detalle y fuentes: [Costos y límites](COSTOS_Y_LIMITES.md).

**¿Puedo cambiar el prompt, el modelo o las estrategias?** Puedes, pero **invalidas la medición en curso**: cualquier cambio
es otro experimento. El hash del prompt se comprueba en cada ciclo y, si cambia, el etiquetado se detiene solo; el modelo no
tiene esa vigilancia, así que cambiarlo contaminaría la medición sin aviso. Por qué:
[El prompt congelado](METODOLOGIA.md#el-prompt-congelado-y-por-qué).

**¿Por qué `/pause` y `/kill` detienen la medición?** Con el scheduler pausado no hay señales, sombras ni etiquetado. Cada
hora pausada es medición perdida, por eso piden confirmación y avisan. El kill switch además bloquea las entradas de la
cuenta paper.

**¿Qué pasa si apago el equipo?** El bot deja de medir mientras está apagado (no se recupera). Al volver a arrancar, las
posiciones y sombras abiertas se **reconcilian** con velas de 1 minuto: si el precio tocó un SL, un TP o la liquidación, se
cierran con ese resultado.

**¿Puedo correr dos instancias a la vez?** No sobre la misma base. Tampoco dos con el mismo token de Telegram (solo uno
recibe los comandos) ni con la misma clave de Anthropic (gastarías el doble). Para probar algo sin molestar al bot real,
usa un puerto distinto y el modo prueba (`MINERVA_REAL_BOT=false`).

**¿Es seguro exponerlo a internet?** No. La API escucha solo en `127.0.0.1`, no tiene HTTPS ni límite de intentos de login.
Ver [Seguridad](SEGURIDAD.md).

**¿Funciona en Linux o macOS?** El bot corre en una imagen Docker de Linux, así que con Docker la instalación es la misma.
Los scripts de arranque nativo, de mudanza y de tareas son de PowerShell y solo funcionan en Windows. El proyecto se
desarrolló y probó en Windows; lo que no se ejecutó en Linux o macOS está marcado como *no verificado* en las guías.

**¿Puedo usar otro exchange u otra IA?** No sin cambiar código: los datos vienen de los endpoints públicos de Bitunix y el
cliente del LLM usa el SDK de Anthropic.

**¿Cómo empiezo una medición nueva, de cero?** Es destructivo: borra la medición. Haz antes un respaldo
([Respaldos](USO.md#respaldos)); con Docker, `docker compose down -v` elimina el contenedor **y los volúmenes** (la base y
los logs) y `docker compose up -d` arranca con una base vacía. No lo hagas "para probar" en un bot que lleva semanas
midiendo.

## Errores frecuentes

### Instalación y arranque

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| `docker compose` dice que no puede conectar con la API o el demonio de Docker (`cannot connect to the Docker API`, `error during connect…`) | Docker Desktop no está abierto o aún está arrancando | Ábrelo y espera a que `docker info` responda. Con `restart: unless-stopped`, el bot vuelve solo cuando el motor arranca |
| `Bind for 127.0.0.1:8000 failed: port is already allocated` | Otro programa o contenedor usa el puerto 8000 (por ejemplo, un bot nativo) | Es intencional: no corras dos bots reales. Detén el otro. Para una instancia de prueba usa `MINERVA_PORT` |
| `docker compose …` escribe `The "XYZ…" variable is not set`, a veces con un trozo largo | El hash de la contraseña del `.env` lleva `$` sin comillas simples: Compose lo interpreta y muestra trozos | Pon `AUTH_PASSWORD_HASH='$2b$12$…'` **entre comillas simples** y limpia la pantalla (no la compartas). El bot no se ve afectado |
| El contenedor arranca pero el dashboard no pide ni acepta la contraseña | El `.env` no llegó al contenedor (no existe, o es una **carpeta**) | Debe existir **como archivo** en la raíz del repositorio antes de `docker compose up -d`. Si Docker Desktop creó una carpeta `.env`, bórrala, crea el archivo y recrea: `docker compose up -d --force-recreate` |
| El bot no arranca y el log dice `UnicodeDecodeError` | El `.env` se guardó en UTF-16 | Guárdalo como UTF-8 |
| Cambié el `.env` y no pasa nada | La configuración se lee solo al crear el contenedor | `docker compose up -d --force-recreate` (en la [ventana segura](USO.md#reinicios-seguros) si hay una medición) |
| El build falla por falta de red, con errores de certificado en una red corporativa (`CERTIFICATE_VERIFY_FAILED`) | La red re-firma el tráfico HTTPS (inspección TLS) y el contenedor no confía en ese certificado | Deja el certificado raíz de tu red como `docker/certs/<nombre>.crt` y reconstruye (`docker compose build`). Ver `docker/certs/README.md`. En nativo el cliente usa el almacén de certificados del sistema |
| `OPENSSL_Uplink … no OPENSSL_Applink` en Windows, al crear el entorno virtual o al usar `ssl` | Un build de Python sin el soporte que exige OpenSSL en Windows: pasó con el Python 3.12 (*python-build-standalone*) que descarga uv, en cuanto se crea un contexto SSL; el Python 3.11 de uv y el 3.14 de python.org no fallaron | Usa Python 3.11 (`uv venv --python 3.11` e `uv pip install -e ".[dev]"`, o `py -3.11 -m venv .venv`) o un Python de python.org. Un 3.12 de python.org no se ha comprobado |
| `uv pip install` o `pip install` fallan con «invalid peer certificate: UnknownIssuer» u otro error de certificado | La red re-firma el tráfico HTTPS (inspección TLS) | Con uv, añade `--system-certs` (usa el almacén de certificados del sistema; verificado con uv 0.11). Con pip, consulta su documentación de certificados: no se ha comprobado aquí |
| `pip install` dice «requires a different Python» | Tu `python` es 3.13 o superior (o anterior a 3.11) | Crea el entorno con Python 3.11 o 3.12 ([guía](GUIA_INSTALACION.md#antes-de-empezar)) |
| PowerShell dice que la ejecución de scripts está deshabilitada | Política de ejecución | Lanza los scripts como en la documentación: `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\…` |
| `start_bot.ps1` dice RECHAZADO | Hay algo escuchando en el puerto 8000, **o** existe `data\MIGRADO_A_DOCKER.txt` (el bot corre en Docker) | Es el bot real o un contenedor: no arranques otro. Mira `logs\medicion.log`. Para volver al arranque nativo: `rollback_from_docker.ps1` |
| Contenedor `unhealthy` | `/health` no responde, el ciclo se detuvo o `frozen_hash_ok` es falso | `docker inspect --format '{{json .State.Health}}' <contenedor>` da el motivo (el nombre lo ves con `docker compose ps`); `docker compose logs minerva` |
| El dashboard abre sin interfaz (solo texto de la API) | Falta `frontend/dist` (instalación nativa sin compilar) | `npm --prefix frontend ci` y `npm --prefix frontend run build`. En Docker se compila al construir la imagen |

### Dashboard y sesión

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| El login responde 401 "Usuario o contraseña incorrectos" | Usuario o contraseña equivocados, o el hash del `.env` está mutilado | Genera un hash nuevo con `scripts/hash_password.py` y ponlo entre comillas simples |
| El login responde 503 ("JWT_SECRET no está configurado o es muy corto") | `JWT_SECRET` vacío o con menos de 32 caracteres | Genera uno de 64 caracteres ([guía](GUIA_INSTALACION.md#34-jwt_secret)) y recrea el contenedor |
| "No autenticado" o "Sesión inválida o expirada" | La sesión dura 24 h, o cambiaste `JWT_SECRET` | Vuelve a iniciar sesión |
| Los botones de control aparecen desactivados | `DASHBOARD_CONTROLS_ENABLED=false` | Es lo esperado con esa opción; ponla en `true` para usarlos |

### Estado del bot

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| `/health` en `stale` | Pausa deliberada (`control.paused`), equipo suspendido, scheduler atascado (Bitunix caído) o proceso muerto | Mira `control.paused` en `/health`: si es `true`, `/resume`. Si no, revisa los logs de error y, si hace falta, reinicia en la [ventana segura](USO.md#reinicios-seguros) |
| `frozen_hash_ok: false` | El código del prompt difiere del congelado (otro commit, archivo editado) | **No sigas operando**: el etiquetado ya se detuvo solo. `git status` y `git log -1`; vuelve al commit original. Nunca edites `app/llm/prompts.py` |
| Aparece `Universo obsoleto o ausente … no se generan senales` | Normal en el primer arranque (el universo se descarga en segundos). Si persiste: CoinGecko no responde o falló el refresco | Espera al próximo refresco; mira el aviso rojo en *Universo y backtest* y `last_refresh_error` |
| No se abren posiciones | Datos obsoletos (WebSocket o velas atrasadas), estrategia no elegible, cupos, *circuit breaker*, pérdida diaria o stop por drawdown | Pestaña *Riesgo y controles* ("Entradas nuevas bloqueadas por") y *Brazo ejecutado* ("Omitidas por motivo") |
| Hay horas sin ninguna señal | Normal: muchos cierres de vela no producen señal | Nada. Las señales llegan despacio ([Qué esperar](GUIA_INSTALACION.md#6-qué-esperar-los-próximos-días)) |
| Stop por drawdown activo | El equity cayó el 20 % desde su pico | Primero investiga; luego [Levantar el stop por drawdown](USO.md#levantar-el-stop-por-drawdown) |
| Reloj del equipo desfasado, "datos obsoletos" sin causa | Hora del sistema mal | Sincroniza la hora; el scheduler se guía por ella |

### Etiquetado y costo

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| Las sombras quedan `SIN_LLM` y el contador sube | Presupuesto diario agotado, clave inválida o revocada, tu propio límite de gasto de Anthropic alcanzado (la API responde 400), la API de Anthropic con fallos o red caída | `/health.llm_labeling` (`budget_exceeded_today`, `failed_today`) y la tarjeta *Avance de la medición* (causas no OK). Revisa la clave y los límites en la consola |
| El log repite "Tarea de etiquetado en vivo falló de forma inesperada" y no hay filas nuevas en la medición | `ANTHROPIC_API_KEY` vacía con el etiquetado encendido | Pon la clave, o apaga el etiquetado con `MINERVA_REAL_BOT=false` mientras exploras |
| El costo del día parece alto | Más señales de lo habitual, o un tope muy alto | `/health.llm_labeling.cost_today_usd`; el corte duro es `LLM_DAILY_BUDGET_USD`. Si sospechas de la clave, revócala ([Seguridad](SEGURIDAD.md#si-se-filtra-una-credencial)) |

### Telegram

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| El bot de Telegram no responde a los comandos | Token o `chat_id` mal; `TELEGRAM_COMMANDS_ENABLED` en `false` (modo prueba o puerto distinto de 8000); otra instancia consumiendo el mismo token; el `chat_id` es negativo (grupo o canal); tu bot tiene un *webhook* configurado; nunca le escribiste primero al bot | Comprueba que solo corra un bot, que el `chat_id` sea el de tu chat **privado** (positivo) y que le hayas enviado `/start`. El bot ignora en silencio cualquier otro chat |
| No llegan avisos ni el resumen diario | `TELEGRAM_PUSH_ENABLED` apagado, token o `chat_id` vacíos, o la tarea programada no está registrada | `/health.telegram_push` muestra si está activo y el tipo del último error. El resumen y la alerta de caída son tareas aparte ([Tareas programadas](USO.md#tareas-programadas)) |
| Obtener el `chat_id` con `getUpdates` da una lista vacía | Aún no le escribiste al bot, o el bot de Minerva ya está corriendo y consume las actualizaciones | Escribe al bot y recarga; hazlo **antes** de arrancar Minerva |

### Respaldos y datos

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| `docker_db.py` termina con código 5 | Docker Desktop no está listo o falló la copia | Abre Docker Desktop y espera a que `docker info` responda; reintenta |
| `backup_db.py`, `restore_db.py` o `resume_drawdown_stop.py` dicen "el bot corre en Docker" | Existe la marca de la mudanza (`data\MIGRADO_A_DOCKER.txt`): la base nativa es una copia vieja | Usa `docker_db.py backup` o `docker compose exec …`; con `--db` explícito sí operan |
| `restore_db.py` dice que el destino existe | Hay una base en el destino | Es una protección. `--force` la aparta como `*.previo_*` (no la borra) |
| Borré el volumen o la base por error | — | Restaura el último respaldo ([Restaurar](USO.md#restaurar)). Sin respaldo no hay forma de recuperarla |
| Quiero comprobar si una copia de la base está sana | — | `python scripts/db_fingerprint.py --db <copia>` imprime la integridad y las filas por tabla |

## Cómo pedir ayuda

Abre un *issue* en el repositorio (hay plantillas) con: qué ocurre, tu sistema operativo, cómo lo instalaste (Docker o
nativo), `git log -1 --oneline`, el JSON de `GET /health` y las líneas relevantes del log.

**Antes de pegar nada, revisa que no lleve:**

- tu `.env`, claves (`sk-ant-…`), el token de Telegram, tu `chat_id`, hashes de contraseña ni `JWT_SECRET`;
- resultados de tu medición (PnL, saldo, motivos de cierre): sesgan el veredicto si los compartes;
- tu base de datos ni respaldos.

Si encontraste una **vulnerabilidad**, no abras un issue público: lee [Seguridad](SEGURIDAD.md#cómo-reportar-un-problema-de-seguridad).
