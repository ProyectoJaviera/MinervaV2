# Guía de instalación

Cómo instalar Minerva **desde cero** y dejarlo corriendo: de clonar el repositorio a ver tu primer `status: ok`.
Tiempo estimado: 20 a 30 minutos (la mayor parte, crear las cuentas y las claves).

> **Minerva es 100 % paper trading, experimental y no es asesoría financiera.** Nada de lo que instales aquí envía
> órdenes reales ni toca tu dinero. Lo único que puede costar dinero es la API de Anthropic (Claude): consulta
> [Costos y límites](COSTOS_Y_LIMITES.md) antes de poner la tarjeta.

Qué vas a conseguir: un bot que, en un solo proceso, evalúa cada cierre de vela un universo de 10 criptomonedas
con seis estrategias por reglas, deja que Claude etiquete cada señal como `APROBADA` o `RECHAZADA` y abre posiciones
*simuladas* solo con las aprobadas. Qué pregunta responde y cómo: [Metodología](METODOLOGIA.md).

## Antes de empezar

| Para | Necesitas | Notas |
|---|---|---|
| Descargar el código | **Git** | |
| Ejecutar el bot (la forma recomendada) | **Docker Desktop** (Windows, macOS) o **Docker Engine con Compose v2** (Linux) | Comprueba con `docker --version` y `docker compose version`. La imagen pesa ≈ 500 MB |
| Herramientas auxiliares (contraseña, respaldos, informes) | **Python 3.11 o 3.12** | `requires-python = ">=3.11,<3.13"`: con Python 3.13 o superior `pip install` se niega a instalar el proyecto. Si tu `python` es más nuevo, crea el entorno con uno compatible (Windows: `py -3.11 -m venv .venv`; Linux y macOS: `python3.12 -m venv .venv`; con [uv](https://docs.astral.sh/uv/): `uv venv --python 3.11` e `uv pip install -e ".[dev]"`). **Windows con uv:** el Python 3.12 que descarga uv (*python-build-standalone*) abortó con `OPENSSL_Uplink … no OPENSSL_Applink` en cuanto se crea un contexto SSL (en el equipo de desarrollo falló al crear el entorno con `python -m venv` y al correr las pruebas); su Python 3.11 funciona (con el que se desarrolló) y el Python 3.14 de python.org también ejecuta esa operación (un 3.12 de python.org no se ha comprobado). El bot en sí corre dentro de Docker; Python solo hace falta en tu máquina para estos scripts (hay alternativa para la contraseña, ver más abajo) |
| La IA (etiquetado de señales) | Cuenta en la **Claude Console** con facturación | Única fuente de costo. Ver [Costos y límites](COSTOS_Y_LIMITES.md) |
| Avisos y comandos en el teléfono | Cuenta de **Telegram** | Opcional y gratuito |
| Universo de monedas | Cuenta gratuita de **CoinGecko** | Opcional pero recomendada |
| Compilar el dashboard sin Docker | **Node.js 22.22 o superior** (24 recomendado) | Solo para la alternativa sin Docker; la imagen Docker ya compila el dashboard |

Si **ya tienes un bot nativo corriendo con una medición en curso** (con `start_bot.ps1`), no sigas esta guía: usa
[Pasar un bot nativo a Docker](MUDANZA_DOCKER.md), que conserva tu base.

## 1. Clonar el repositorio

```bash
git clone https://github.com/ProyectoJaviera/MinervaV2.git
cd MinervaV2
```

Los comandos de esta guía se ejecutan desde la raíz del repositorio (la carpeta que acabas de clonar, que en el resto
de la documentación se llama `<carpeta del repo>`). El nombre de la carpeta no importa: Docker Compose usa siempre el
proyecto `minerva` (`name: minerva` en `docker-compose.yml`).

## 2. Crear el archivo `.env`

El `.env` guarda tus claves y tu configuración. **Nunca se sube al repositorio** (está en `.gitignore`) ni entra en la
imagen Docker. Cópialo desde la plantilla, que trae todas las variables con sus valores por defecto y las
credenciales vacías:

```powershell
Copy-Item .env.example .env      # Windows (PowerShell)
```

```bash
cp .env.example .env             # Linux y macOS
```

- Debe llamarse exactamente `.env` y ser un **archivo** (si no existe, Docker Desktop para Windows crea una *carpeta*
  con ese nombre y el contenedor arrancaría sin credenciales y sin avisar). En Windows, comprueba que el Explorador no
  le puso `.txt`.
- Edítalo con un editor de texto y guárdalo como **UTF-8**. Un archivo en UTF-16 (que algunos editores de Windows
  ofrecen) impide que el bot arranque (`UnicodeDecodeError`); un UTF-8 con BOM y los saltos de línea de Windows sí
  funcionan.
- Formato: `NOMBRE=valor`, sin espacios alrededor del `=`. Los comentarios empiezan con `#`.
- Todas las variables están explicadas en [Configuración](CONFIGURACION.md).

## 3. Conseguir y configurar cada credencial

### 3.1 Clave de Anthropic (con límite de gasto)

La clave permite que el bot llame a Claude para etiquetar cada señal. Es **de pago por uso**: según lo medido, cada
señal etiquetada cuesta del orden de 0,005 USD (ver [Costos y límites](COSTOS_Y_LIMITES.md)).

1. Crea una cuenta en la Claude Console (<https://platform.claude.com/>) y configura la facturación.
2. **Pon primero un límite de gasto** propio: en la consola, página *Billing*
   (`platform.claude.com/settings/billing`), sección *Spend limits* → *Set limit* (o *Adjust limit*). Es la red de
   seguridad si algo fallara en el bot. Un valor de pocos dólares al mes basta de sobra para el ritmo que mide el
   proyecto. Según la documentación de Anthropic, al alcanzar *tu* límite las peticiones devuelven HTTP 400; en
   Minerva eso deja las señales sin etiqueta (`SIN_LLM`) hasta que lo subas o llegue el mes siguiente.
3. Crea una clave de API siguiendo la guía oficial (<https://platform.claude.com/docs/en/get-api-key>). Se muestra
   una sola vez: cópiala ya. (Los nombres de los menús de la consola cambian con el tiempo: verifícalos allí.)
4. Pégala en el `.env`:

   ```text
   ANTHROPIC_API_KEY=sk-ant-...
   ```

El bot suma además su **propio tope diario con corte duro**: `LLM_DAILY_BUDGET_USD` (1 USD por defecto). Los dos
topes se complementan; ver [Configuración](CONFIGURACION.md#claude-anthropic).

> **Sin clave el bot arranca, pero no mide.** Con el etiquetado encendido y `ANTHROPIC_API_KEY` vacía, cada intento
> de etiquetar falla con un error en el log y la señal queda `SIN_LLM`: no se gasta nada, pero la medición no avanza.
> Para explorar el sistema sin clave, arráncalo en modo prueba (ver [Probar sin gastar](#probar-sin-gastar)).

### 3.2 Bot de Telegram (opcional): token y `chat_id`

Telegram da avisos de posiciones, alertas de riesgo, comandos (`/status`, `/pause`…) y el resumen diario. Si no lo
quieres, deja `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` vacíos: el bot lo registra como aviso y sigue funcionando sin
Telegram.

1. En Telegram, abre una conversación con **@BotFather** (el bot oficial de Telegram), envía `/newbot` y responde a
   las preguntas: un nombre y un *username* que **termine en `bot`**. BotFather te devuelve el **token**.
2. Abre una conversación con **tu bot nuevo** (búscalo por su username) desde la cuenta donde quieres recibir los
   avisos y envíale cualquier mensaje, por ejemplo `/start`. Debe ser un chat **privado**.
3. Obtén tu `chat_id` **antes de arrancar Minerva** (un token solo admite un consumidor de `getUpdates`, y el bot de
   Minerva lo consumiría): abre en el navegador, sustituyendo `<TOKEN>` por tu token,

   ```text
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```

   y busca en la respuesta `"chat":{"id":123456789,…}`. Ese número es tu `chat_id`. Si `result` aparece vacío
   (`[]`), envía otro mensaje al bot y recarga. La URL contiene tu token: ábrela en una ventana privada y no la
   compartas. (`getUpdates` no funciona si el bot tiene configurado un *webhook*; uno nuevo no lo tiene.)
4. Complétalo en el `.env`:

   ```text
   TELEGRAM_BOT_TOKEN=<token de BotFather>
   TELEGRAM_CHAT_ID=<tu chat_id>
   # (sin los signos < >; por ejemplo TELEGRAM_CHAT_ID=123456789)
   ```

El `chat_id` debe ser el de un chat **privado** (un número positivo): un identificador de grupo o canal (negativo) se
rechaza y el bot no responderá a nadie. Solo ese chat puede dar órdenes; cualquier otro se ignora en silencio.

### 3.3 Usuario y contraseña del dashboard

El dashboard exige iniciar sesión. En el `.env` va el **usuario** y el **hash bcrypt** de tu contraseña (nunca la
contraseña):

```text
AUTH_USERNAME=elige-un-usuario
AUTH_PASSWORD_HASH='$2b$12$...'
```

Para generar el hash tienes dos caminos.

**Con Python en tu máquina** (instala antes las dependencias; son las mismas que usa el bot):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe scripts\hash_password.py
```

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python scripts/hash_password.py
```

**Solo con Docker**, sin instalar Python (necesita la imagen construida: `docker compose build`, ≈ 1 minuto la
primera vez):

```bash
docker compose build
docker run --rm -it minerva:local python scripts/hash_password.py
```

El script pide la contraseña dos veces (sin mostrarla) y escribe la línea completa `AUTH_PASSWORD_HASH='…'`.
Cópiala **tal cual, con sus comillas simples**, al `.env`. Las comillas importan: el hash lleva signos `$` y, sin
ellas, Docker Compose los toma por variables y escribe trozos de tu hash en pantalla. La contraseña no puede pasar de
72 bytes en UTF-8 (límite de bcrypt) y el script te avisa si te pasas.

### 3.4 `JWT_SECRET`

Es la clave que firma tu sesión del dashboard (cookie `httpOnly` de 24 horas). Debe tener **al menos 32 caracteres**;
con menos, el login responde 503 y el resto de la API rechaza todo. Genera una cadena aleatoria:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

(Con Docker y sin Python: `docker run --rm minerva:local python -c "import secrets; print(secrets.token_hex(32))"`.)
Pégala en el `.env`:

```text
JWT_SECRET=<la cadena de 64 caracteres>
```

Cambiar este valor más adelante cierra todas las sesiones abiertas.

### 3.5 Clave de CoinGecko (opcional, recomendada)

CoinGecko da el ranking por capitalización con el que se arma el universo (las 10 monedas mayores que tengan
perpetuo USDT en Bitunix).

1. Crea una cuenta gratuita en <https://www.coingecko.com/en/api/pricing> (plan *Demo*).
2. En el *Developer Dashboard* (<https://www.coingecko.com/en/developers/dashboard>) pulsa *+ Add New Key*.
3. Pégala en el `.env`:

   ```text
   COINGECKO_API_KEY=...
   ```

Según su página de precios (consultada el 2026-10-23), el plan Demo no pide tarjeta, permite 100 llamadas por minuto
y 10.000 al mes, y **exige atribución** ("Data provided by CoinGecko"; el dashboard la muestra). El uso de Minerva
(unas pocas llamadas al día) está muy por debajo. El bot envía la clave en la cabecera `x-cg-demo-api-key`.
**Sin clave** una llamada pública de prueba respondió bien el 2026-10-23, pero el plan formal la exige y los límites
sin clave no están documentados en lo que se consultó: ponla.

### 3.6 Tu zona horaria (recomendado)

Un par de cosas se miden por "día": el tope diario del gasto en Claude y el resumen diario. Se calculan en la zona
`REPORT_TIMEZONE`, que **por defecto es `America/Santiago`**. Ponla en la tuya (nombre de la base de datos de zonas
horarias IANA, por ejemplo `Europe/Madrid`, `America/Mexico_City`, `UTC`):

```text
REPORT_TIMEZONE=Europe/Madrid
```

### 3.7 Revisa el `.env`

Antes de arrancar, comprueba (sin pegar su contenido en ningún chat):

- [ ] `ANTHROPIC_API_KEY` empieza por `sk-ant-` (o la dejaste vacía a propósito).
- [ ] `AUTH_USERNAME` tiene valor y `AUTH_PASSWORD_HASH='$2b$12$…'` va **entre comillas simples**.
- [ ] `JWT_SECRET` tiene 32 caracteres o más.
- [ ] `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` están ambos, o ambos vacíos.
- [ ] Ningún valor lleva espacios ni comillas dobles alrededor (salvo el hash, con comillas simples).
- [ ] El archivo se llama `.env` y está en la raíz del repositorio, junto a `docker-compose.yml`.

## 4. Primer arranque con Docker

Con Docker Desktop **abierto** (o el servicio de Docker activo en Linux), desde la raíz del repositorio:

```bash
docker compose up -d
docker compose ps
```

- La primera vez construye la imagen (3 etapas: compila el dashboard, instala las dependencias con versiones exactas y
  arma la imagen final): ≈ 1 minuto con buena conexión. Necesita internet (imágenes base, paquetes de Python y de
  Node).
- Después arranca el contenedor `minerva` (puerto `127.0.0.1:8000`, base y logs en volúmenes de Docker).
  `docker compose ps` pasa a `healthy` en menos de un minuto; el contenedor tiene hasta 5 minutos de margen antes de
  marcarse como no sano.
- **Con `docker compose up -d` a secas arranca como "bot real"**: etiquetado con Claude, brazo ejecutado de la cuenta
  paper, noticias y notificaciones de Telegram encendidos (los que tengan sus credenciales). Es lo previsto para una
  medición. Para explorar sin gastar, mira [Probar sin gastar](#probar-sin-gastar).
- El contenedor se reinicia solo (`restart: unless-stopped`) si Docker o el equipo se reinician; ver
  [Uso diario](USO.md#arrancar-y-detener).

### Probar sin gastar

Si todavía no tienes clave de Anthropic, o solo quieres ver el dashboard, arranca en **modo prueba**: sin etiquetado
del LLM, sin Telegram, sin brazo ejecutado, sin noticias. Añade esta línea al `.env` (Docker Compose la lee de ahí):

```text
MINERVA_REAL_BOT=false
```

y arranca con `docker compose up -d`. El bot sigue generando señales y operaciones sombra, pero no llama a ninguna
API de pago. Cuando quieras la medición real, quita la línea (o ponla en `true`) y recrea el contenedor:
`docker compose up -d --force-recreate`.

### Procesador antiguo (sin SSE4)

Si el equipo tiene un procesador anterior a ≈ 2008-2010 (por ejemplo un Intel Atom N570 de netbook, que llega solo
hasta SSSE3), la imagen normal arranca y se cae al instante con **`Instrucción ilegal`** (`Illegal instruction`) al
importar NumPy: la versión 2.4 de NumPy, la que fija `docker/requirements.lock`, exige un procesador x86-64-v2
(SSE4.2). Para esos equipos hay una variante, `docker/requirements.old-cpu.lock`, **idéntica a la principal salvo
`numpy==2.3.5`** (pandas 3.0.6 es compatible con esa versión). Construye la imagen indicándola:

```bash
docker compose build --build-arg REQUIREMENTS_FILE=docker/requirements.old-cpu.lock
docker compose up -d --no-build
```

- `--no-build` importa: un `docker compose up -d --build` posterior reconstruiría la imagen con el archivo por
  defecto (`docker/requirements.lock`) y el síntoma volvería. En cada actualización de versión repite el
  `docker compose build --build-arg …` y luego `docker compose up -d --no-build`.
- Comprobación rápida con el bot ya arrancado: `docker compose exec minerva python -c "import numpy; print(numpy.__version__)"`
  debe imprimir `2.3.5` (con la imagen normal imprime `2.4.6`).
- Sin el argumento la construcción no cambia: sigue usando `docker/requirements.lock` (NumPy 2.4.6).
- Qué se comprobó y qué no, en [Entorno de desarrollo](dev/ENTORNO_DESARROLLO.md#variante-para-procesadores-sin-sse4).
  **No verificado:** el arranque completo del bot en un Atom N570 real; lo comprobado fue la importación y el uso de
  las librerías de código nativo bajo una CPU emulada sin SSE4, y el resultado idéntico de los cálculos de la medición.
- Un solo bot por clave: si migras de otro equipo, **no dejes los dos encendidos** (el mismo token de Telegram, la
  misma clave de Anthropic y dos bases que se desincronizan).

## 5. Comprobar que funciona

1. **Estado del contenedor:** `docker compose ps` debe decir `healthy`.
2. **`/health`:** abre <http://127.0.0.1:8000/health> en el navegador (no pide sesión). En la primera respuesta
   `status` puede tardar unos segundos en pasar a `ok` (el primer ciclo del scheduler). Con los valores por defecto
   ("bot real") y todas las credenciales puestas, deberías ver:

   | Campo | Valor sano | Qué significa |
   |---|---|---|
   | `status` | `ok` | Hubo un ciclo completo de señales en los últimos 90 minutos |
   | `mode` | `PAPER_TRADING` | Siempre |
   | `llm_labeling.enabled` | `true` | El etiquetado con Claude está encendido |
   | `llm_labeling.frozen_hash_ok` | `true` | El prompt es el congelado de la medición. Si es `false`, el etiquetado se detiene solo |
   | `paper_execution.active` | `true` | El brazo ejecutado de la cuenta paper está armado |
   | `telegram_push.enabled` / `active` | `true` / `true` | Solo si pusiste token y `chat_id` |

3. **Dashboard:** abre <http://127.0.0.1:8000/>, entra con el usuario y la contraseña que elegiste y mira la pestaña
   *Resumen*. Qué muestra cada pestaña: [Uso diario](USO.md#el-dashboard).
4. **Telegram (si lo configuraste):** el bot envía un mensaje de arranque al chat; prueba `/status`.
5. **Registros:** `docker compose logs --tail 50 minerva`. Los mensajes normales del primer arranque incluyen un
   aviso `Universo obsoleto o ausente … no se generan senales hasta el proximo refresco` (el universo se descarga en
   segundos) y, durante algo más de un minuto, el progreso de la descarga inicial de velas de cada moneda.
   Si quieres mantener la medición ciega, evita leer los logs más allá del arranque: los de nivel INFO dejan pasar
   motivos de cierre y PnL de operaciones (ver [Límites conocidos](COSTOS_Y_LIMITES.md#límites-conocidos)).

### Si algo no sale

Mira [Solución de problemas](SOLUCION_PROBLEMAS.md): cubre el `.env` que es una carpeta, el hash sin comillas, el
puerto 8000 ocupado, el login que no entra, `status: stale` y los errores de certificado en redes corporativas.

## 6. Qué esperar los próximos días

- **No hay resultados que ver, y es a propósito.** La medición del valor de la IA es *ciega*: hasta que se junten 300
  "conglomerados efectivos" por lado **y** exista un registro oficial del veredicto, ningún canal muestra PnL, saldo,
  precios de salida ni motivos de cierre. La pestaña *Resumen* muestra el avance (x/300 por lado), la salud y el
  costo. Por qué: [Metodología](METODOLOGIA.md#por-qué-se-ocultan-los-resultados).
- **Las señales llegan despacio.** Las velas de 1 h cierran cada hora, las de 4 h a las 00, 04, 08, 12, 16 y 20 UTC y
  la de 1 d a las 00 UTC; muchos cierres no producen ninguna señal. En la medición del autor (octubre de 2026) se
  juntaban ≈ 1,25 conglomerados por día de calendario y lado (≈ 5 por día con el bot encendido, pero solo estuvo
  encendido el 23,6 % del tiempo): llegar a 300 por lado se mide en **meses**. Un equipo que se apaga o se suspende
  pierde medición para siempre.
- **El costo es pequeño.** Con el ritmo observado, el orden de magnitud son unos pocos dólares al mes como máximo; el
  tope diario de 1 USD lo acota.
- **Déjalo encendido y no lo toques.** No cambies el modelo, el prompt ni `LLM_MAX_TOKENS` mientras mides, y reinicia
  solo en la [ventana segura](USO.md#reinicios-seguros).

## 7. Detener, reiniciar y actualizar

```bash
docker compose stop              # parada ordenada (SIGTERM, hasta 30 s)
docker compose up -d             # volver a arrancar
docker compose restart minerva   # reinicio ordenado (solo en la ventana segura)
```

Un cambio en el `.env` solo lo ve el contenedor al *crearse*: tras editarlo, `docker compose up -d --force-recreate`
(los volúmenes se conservan). Actualizar a una versión nueva, respaldar y restaurar: [Uso diario](USO.md).

## 8. Alternativa sin Docker (solo Windows)

El proyecto se desarrolló y se operó primero de forma nativa en Windows. Hay un par de scripts de PowerShell que lo
arrancan y lo detienen sin ventana de consola. **No los uses a la vez que Docker** (dos procesos con el mismo token de
Telegram, la misma clave y la base duplicada).

```powershell
# 1. Entorno de Python y dependencias (la misma venv que usaste para el hash de la contraseña)
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

# 2. Dashboard (Node.js 22.22 o superior)
npm --prefix frontend ci
npm --prefix frontend run build

# 3. Arrancar y detener el bot (puerto 8000, sin consola)
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_bot.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop_bot.ps1
```

`start_bot.ps1` fija sus propias variables (con **independencia** del `.env`): `DATABASE_PATH` en
`data\minerva_estabilidad.db`, los interruptores del "bot real" encendidos solo con el puerto 8000 y la memoria de
lecciones apagada. Se niega a arrancar si el puerto ya está ocupado, espera a que `/health` responda (hasta 180 s) y
escribe su bitácora en `logs\medicion.log`. `stop_bot.ps1` es un corte duro (Windows no da una señal de apagado
cooperativo a un proceso sin consola): SQLite en modo WAL lo tolera, pero un etiquetado en vuelo se pierde; por eso se
reinicia en la ventana segura. El detalle de cada interruptor está en [Configuración](CONFIGURACION.md#interruptores).

## 9. Notas para Linux y macOS

- **Con Docker** todo lo de esta guía es igual (cambia solo cómo copias el `.env` y las rutas de Python). La imagen es
  de Linux, así que la parte de Docker es la misma que se probó en Windows.
- **Sin Docker** no hay scripts de arranque (los de PowerShell usan comandos de Windows). Puedes lanzar el servidor
  tú mismo con las mismas variables que fija `start_bot.ps1`; por ejemplo:

  ```bash
  LLM_LIVE_LABELING_ENABLED=true TELEGRAM_COMMANDS_ENABLED=true PAPER_EXECUTION_ENABLED=true \
  NEWS_COLLECTION_ENABLED=true TELEGRAM_PUSH_ENABLED=true AUTO_OPEN_WITHOUT_LLM=false \
  LESSONS_MEMORY_ENABLED=false .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
  ```

  **No verificado:** el proyecto se desarrolló y se probó en Windows; este comando usa las mismas variables y el mismo
  punto de entrada, pero no se ejecutó en Linux ni en macOS. Para el dashboard hace falta compilarlo antes
  (`npm --prefix frontend ci` y `npm --prefix frontend run build`).
- **Permisos del `.env` en Linux:** el contenedor corre con un usuario sin privilegios (uid 10001) y el `.env` se
  monta de solo lectura. Si restringes el archivo a tu usuario (`chmod 600`), puede que el contenedor no logre leerlo.
  **No verificado**; si ves que arranca sin credenciales, prueba con `chmod 644 .env`.

## Siguientes pasos

- Aprende a usarlo: [Uso diario](USO.md) (dashboard, Telegram, respaldos, reinicios seguros).
- Ajusta la configuración: [Configuración](CONFIGURACION.md).
- Entiende qué se mide y qué no: [Metodología](METODOLOGIA.md).
- Revisa la [Seguridad](SEGURIDAD.md) antes de dejar el bot encendido de forma permanente.
