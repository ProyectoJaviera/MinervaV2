# Seguridad

Minerva es una herramienta **local, de un solo usuario y 100 % paper trading**: no tiene credenciales de ningún
exchange y no mueve dinero. Aun así guarda secretos que importan (una clave de Anthropic que cuesta dinero, un token
que controla tu bot de Telegram) y expone una API con controles. Este documento dice qué protege qué, qué **nunca**
subir al repositorio y qué hacer si algo se filtra.

## Los secretos y qué permite cada uno

| Secreto | Variable | Quién lo tenga puede… | Si se filtra |
|---|---|---|---|
| Clave de Anthropic | `ANTHROPIC_API_KEY` | Gastar tu dinero en la API de Claude | Revócala **primero** en la consola de Anthropic; luego crea otra |
| Token del bot de Telegram | `TELEGRAM_BOT_TOKEN` | Controlar tu bot: enviar mensajes en su nombre y leer lo que le escriban | Revócalo con @BotFather (`/revoke`, o el comando equivalente de tu versión: compruébalo en el chat) |
| Hash de la contraseña | `AUTH_PASSWORD_HASH` | Intentar adivinar tu contraseña sin conexión (es un hash bcrypt, costoso de atacar, pero no es público por diseño) | Genera una contraseña nueva y un hash nuevo |
| Clave de firma de sesión | `JWT_SECRET` | Fabricar sesiones válidas del dashboard | Cámbiala: cierra todas las sesiones |
| Clave de CoinGecko | `COINGECKO_API_KEY` | Gastar tu cuota gratuita | Elimínala en su panel y crea otra |
| Identificador del chat | `TELEGRAM_CHAT_ID` | No es un secreto por sí mismo, pero identifica tu cuenta y quién puede dar órdenes | Cámbialo si cambias de cuenta |

## Dónde viven los secretos (y dónde nunca)

- **Solo en el archivo `.env`** de tu equipo, a partir de `.env.example` (que trae todas las credenciales vacías).
  `.env` está en `.gitignore` y en `.dockerignore`.
- **Nunca** en código, pruebas, documentos, logs, capturas, chats ni commits. Una prueba del repositorio
  (`tests/unit/test_repo_hygiene.py`) escanea todo lo que git versionaría y falla si aparece algo con forma de
  credencial: claves de Anthropic, tokens de Telegram, hashes bcrypt, claves privadas, tokens de GitHub o de AWS y
  asignaciones largas a variables de credencial. Es una revisión por patrones: no garantiza detectar formatos
  desconocidos.
- **Con Docker**, el `.env` no entra en la imagen ni en el entorno del contenedor: se monta de solo lectura en
  `/app/.env` y lo lee la propia aplicación. Por eso no aparece en `docker inspect`. (No se usa `env_file:` a
  propósito: Compose interpolaría los `$` del hash de la contraseña y lo mutilaría.)
- **El hash de la contraseña va entre comillas simples** en el `.env`. Sin ellas, cada comando `docker compose …`
  escribe trozos del hash en pantalla (el bot funciona igual, pero esas líneas acaban en capturas y chats).
- **Los logs no llevan credenciales** por diseño: los errores de Telegram, por ejemplo, registran solo el tipo de la
  excepción, nunca la URL ni el token.

### Qué nunca debe subirse a un repositorio

| Ruta | Por qué |
|---|---|
| `.env` y `.env.*` (menos `.env.example`) | Tus credenciales |
| `data/` y cualquier `*.db`, `*.db-wal`, `*.db-shm`, `*.sqlite*` | La base de la medición: son datos crudos de una medición *ciega* (mirarlos sesga el veredicto) y pueden incluir tus operaciones |
| `data/backups/` | Copias completas de esa base |
| `logs/` y `*.log` | Los logs de nivel INFO dejan pasar motivos de cierre y PnL de operaciones |
| `docker/certs/*.crt`, `*.pem`, `*.key` | Certificados específicos de tu red (y nunca una clave privada) |
| `.claude/settings.local*.json` | Permisos y rutas locales de tu herramienta de IA |

El `.gitignore` ya los cubre y hay una prueba que lo comprueba; no los fuerces con `git add -f`.

## Red y puertos

- **La API escucha solo en `127.0.0.1`.** Con Docker, el contenedor escucha dentro en `0.0.0.0` (para que funcione el
  reenvío de puertos de Docker) pero `docker-compose.yml` lo publica únicamente como `127.0.0.1:8000`: no queda
  expuesto a tu red local ni a internet. Sin Docker, `start_bot.ps1` arranca con `--host 127.0.0.1`.
- **No hay TLS.** Todo corre sobre HTTP plano en la máquina local, y la cookie de sesión va sin el atributo `Secure`
  a propósito (con `Secure` el navegador la descartaría en silencio sobre HTTP). **No expongas el puerto fuera del
  equipo** (ni con `MINERVA_PORT`, ni con un reenvío de puertos del router, ni con un túnel) sin poner delante un
  proxy inverso con TLS.
- **Tampoco hay límite de intentos de login ni bloqueo de cuenta.** Es una razón más para no exponer la API.
- **Conexiones de salida:** a las API públicas de Bitunix (REST y WebSocket), CoinGecko, Anthropic, Telegram y, para
  las noticias, GDELT, la Reserva Federal y Alternative.me. Ninguna usa credenciales del exchange.

## Autenticación, sesión y controles

- **Un solo usuario.** El login compara el usuario y verifica la contraseña contra un hash bcrypt; el mensaje de error
  no distingue "usuario inexistente" de "contraseña incorrecta".
- **Sesión:** JWT firmado con HS256 en una cookie `httpOnly` y `SameSite=Strict`, de 24 horas. Si `JWT_SECRET` está
  vacío o tiene menos de 32 caracteres, el login responde 503 y todas las rutas protegidas responden 401 (nunca se
  firma un token con una clave insegura).
- **Rutas sin login:** `GET /health`, `GET /positions` y `GET /trades`. Pasan por listas blancas campo a campo y,
  mientras no exista el registro oficial del veredicto, no incluyen PnL, saldo ni resultados. `/health` no lleva
  credenciales. Todo lo demás de la API exige sesión.
- **Controles del dashboard** (pausar, reanudar, kill switch): exigen sesión, que el interruptor
  `DASHBOARD_CONTROLS_ENABLED` esté encendido (en `false`, la API los rechaza con 403), mismo origen (se rechaza una
  petición de otro sitio), contenido JSON y una **palabra de confirmación exacta** (`PAUSAR`, `REANUDAR`, `KILL`).
  Cada acción queda auditada (quién, cuándo, canal, estado antes y después). Son las únicas escrituras de control de
  toda la API; **no abren ni cierran posiciones**.
- **Telegram:** solo se atiende a un chat **privado** cuyo identificador coincida exactamente con `TELEGRAM_CHAT_ID`.
  Cualquier otro chat, usuario o grupo se ignora *en silencio* (ni respuesta ni error, para no confirmar que el bot
  existe). `/pause`, `/resume` y `/kill` piden una confirmación de dos pasos dentro de 60 segundos, y al arrancar el
  bot descarta los mensajes acumulados mientras estuvo caído.

## El contenedor

La imagen se construyó con la idea de que un proceso comprometido pueda hacer lo menos posible:

- El proceso corre como un usuario **sin privilegios** (uid 10001) con `cap_drop: ALL` y `no-new-privileges`.
- El **código de la imagen pertenece a root y es de solo lectura** para ese usuario: solo puede escribir en
  `/app/data`, `/app/logs` y `/tmp`.
- La imagen no lleva secretos, ni datos, ni documentos, ni pruebas (`.dockerignore`); las dependencias van con versiones
  **exactas** (`docker/requirements.lock`).
- `./docs` se monta de solo lectura (el registro oficial del veredicto se lee en cada petición).

## Otras defensas por diseño

- **No hay ejecución real.** `BitunixBackend` existe solo como interfaz documentada: lanza `NotImplementedError` al
  instanciarse y una prueba por AST garantiza que nada de `app/` lo importa.
- **El texto de las noticias es texto no confiable.** Se sanea, se trunca y se marca como tal. Además, **no entra al
  prompt de la medición** (los módulos de noticias y de memoria tienen prohibido por prueba ser importados por el
  etiquetador). Un borrador de prompt futuro (`prompts_v5`, sin usar) declara los titulares como texto a evaluar, nunca
  como instrucciones.
- **La respuesta del LLM se valida con un esquema** antes de usarse y se guarda completa; una respuesta inválida nunca
  se convierte en una etiqueta.
- **Las fuentes externas están aisladas:** un fallo de Telegram, noticias o memoria no alcanza al scheduler, al
  etiquetado ni al monitor, y un dato crítico obsoleto bloquea nuevas entradas.

## Si se filtra una credencial

1. **Revócala primero** (antes de limpiar nada): Anthropic en su consola; Telegram con @BotFather; CoinGecko en su
   panel. Después crea una nueva y ponla en el `.env`.
2. Recrea el contenedor para que lea el archivo nuevo: `docker compose up -d --force-recreate` (en la
   [ventana segura](USO.md#reinicios-seguros) si hay una medición en curso).
3. Comprueba que no hubo uso indebido: la página de uso de la consola de Anthropic, y en Telegram que el bot no
   envió nada que no esperabas.
4. **Si el `.env` o una clave llegó a un commit**, trátala como filtrada aunque la borres en el siguiente commit: el
   historial de git la conserva. Rota todo lo que contenía. Reescribir el historial es una medida aparte y delicada.
5. Si la filtración fue el hash o `JWT_SECRET`: genera otra contraseña (`scripts/hash_password.py`) y otro
   `JWT_SECRET`, que además cierra todas las sesiones abiertas.

## Cómo reportar un problema de seguridad

**No abras un issue público con los detalles de una vulnerabilidad ni pegues nunca una credencial en él.** Usa el
reporte privado de GitHub: en la pestaña *Security* del repositorio, *Report a vulnerability* (disponible solo si el
propietario del repositorio activó el reporte privado de vulnerabilidades). Si no aparece esa opción, abre un
issue **sin detalles técnicos** que diga únicamente que tienes un reporte de seguridad y pide un canal privado.

Incluye: qué ocurre, cómo reproducirlo, qué versión (`git log -1 --oneline`) y el impacto que ves. No hace falta que
adjuntes tus credenciales ni tu base de datos.

## Límites conocidos

- Un solo usuario, sin roles; sin límite de intentos de login; sin HTTPS (solo loopback).
- El `.env` está en claro en el disco. Protege el equipo con el cifrado de disco y los permisos del sistema operativo.
- Los respaldos de la base (`data/backups/`) no están cifrados.
- Docker Desktop y el propio equipo son parte de tu superficie: quien pueda ejecutar `docker` en tu máquina puede leer
  los volúmenes.
- La integridad de la medición ciega depende en parte de la disciplina de quien opera el bot: los logs de nivel INFO
  dejan pasar resultados (ver [Costos y límites](COSTOS_Y_LIMITES.md#límites-conocidos)).
- La revisión de secretos del repositorio es por patrones, no una auditoría de seguridad independiente.
