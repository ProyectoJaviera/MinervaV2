"""Configuracion de logging para Minerva.

Regla critica (CLAUDE.md): nunca se debe loguear una credencial. Este modulo
no imprime valores de configuracion sensibles; quien agregue logs debe evitar
volcar `settings.anthropic_api_key`, `settings.telegram_bot_token`, etc.

**Incidente de estabilidad 2026-10-11**: el bot se congelo ~4h porque la
consola quedo pausada (Tee-Object externo, fuera de este repo) y el pipe de
stderr se llamo -- `logging.basicConfig` escribe SINCRONO: el `write()` del
`StreamHandler` bloqueo, y como todo corre en un solo hilo (asyncio), bloqueo
el event loop completo. Ningun log del codigo puede volver a bloquear asi:

- Cada llamada a `logger.info/.../()` encola el registro (`BoundedQueueHandler.
  enqueue`, siempre `_nowait`) y retorna de inmediato -- nunca escribe nada
  ella misma.
- DOS `QueueListener` independientes, cada uno en su propio hilo, consumen esas
  colas: uno hacia un `RotatingFileHandler` (destino PRINCIPAL, en
  `settings.log_dir`) y otro hacia la consola (best-effort). Si el hilo de
  consola se traba escribiendo, solo se traba ESE hilo -- nunca el event loop,
  nunca el archivo (cola y listener separados).
- Si una cola se llena, se descarta DEBUG/INFO en silencio (no hay fallback a
  stderr: ese podria ser el mismo pipe trabado). Un WARNING o mas grave NUNCA
  se descarta mientras haya algo de menor prioridad que sacrificar en su
  lugar -- solo se pierde un WARNING+ si la cola esta llena de puro WARNING+
  (caso extremo). Los descartes se cuentan (`get_dropped_log_count`), expuestos
  en el heartbeat y en `/health`.
- uvicorn configura SUS PROPIOS handlers sincronos en los loggers `uvicorn`,
  `uvicorn.error` y `uvicorn.access` antes de que el lifespan (y por lo tanto
  `setup_logging`) llegue a correr. Si no se les quitan esos handlers, un log
  de acceso/error de uvicorn escribe DIRECTO a ellos (nunca pasa por nuestras
  colas) y puede volver a bloquear el proceso exactamente como el incidente
  original -- `_detach_uvicorn_handlers` los limpia y los deja propagar al
  logger raiz.

Ver `app/core/heartbeat.py` (latido cada `settings.heartbeat_seconds`) y
`GET /health` (`last_beat_at`, `last_cycle_at`, `log_dropped_count`).
"""

from __future__ import annotations

import logging
import logging.handlers
import queue
import threading
from pathlib import Path

from app.config import settings

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

# Constantes del modulo (decision explicita: no configurables por Settings,
# solo `log_dir` y `heartbeat_seconds` lo son -- ver app/config.py).
FILE_QUEUE_MAXSIZE = 20_000
CONSOLE_QUEUE_MAXSIZE = 1_000
LOG_ROTATE_MAX_BYTES = 10_000_000
LOG_ROTATE_BACKUP_COUNT = 5
LOG_FILE_NAME = "minerva.log"
# `QueueListener.stop()` estandar hace `_thread.join()` SIN timeout -- si el
# hilo de consola esta trabado en una escritura, `shutdown_logging()` se
# colgaria con el. El de archivo si espera sin limite (vacia su cola antes de
# salir, I/O local que no deberia trabarse nunca).
CONSOLE_STOP_JOIN_TIMEOUT_SECONDS = 2.0

_UVICORN_LOGGER_NAMES = ("uvicorn", "uvicorn.error", "uvicorn.access")


class BoundedQueueHandler(logging.handlers.QueueHandler):
    """`QueueHandler` que jamas bloquea al hilo que llama a `logger.algo()`:
    `enqueue` es siempre `_nowait`. Ver el docstring del modulo para la
    politica de descarte (prioriza WARNING+ sobre DEBUG/INFO, cuenta todo lo
    que descarta, nunca usa `handleError`/stderr)."""

    def __init__(self, q: queue.Queue) -> None:
        super().__init__(q)
        self.dropped_count = 0

    def enqueue(self, record: logging.LogRecord) -> None:
        """Encola el registro sin esperar jamas; con la cola llena aplica la politica de descarte.

        DEBUG e INFO se descartan (y se cuentan). Un WARNING o mas grave desaloja UN registro
        viejo de menor prioridad; solo se pierde si la cola esta llena de WARNING o mas grave.

        Args:
            record: el registro de log a encolar.
        """
        try:
            self.queue.put_nowait(record)
            return
        except queue.Full:
            pass
        if record.levelno < logging.WARNING:
            self.dropped_count += 1
            return
        # WARNING+ : se intenta hacer lugar desalojando UN registro viejo.
        try:
            evicted = self.queue.get_nowait()
        except queue.Empty:
            evicted = None
        if evicted is not None and getattr(evicted, "levelno", logging.WARNING) >= logging.WARNING:
            # Lo desalojado tambien era importante (o es el centinela del
            # listener, que no tiene `levelno` y cae en el default WARNING de
            # `getattr` -- nunca se lo pierde): se devuelve y se descarta el
            # registro NUEVO en su lugar, no hay nada barato que sacrificar.
            try:
                self.queue.put_nowait(evicted)
            except queue.Full:
                pass
            self.dropped_count += 1
            return
        if evicted is not None:
            self.dropped_count += 1  # el viejo DEBUG/INFO desalojado se perdio
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            self.dropped_count += 1

    def handleError(self, record: logging.LogRecord) -> None:
        """No hace nada, a proposito: escribir a stderr podria bloquear el mismo pipe del incidente.

        Args:
            record: el registro que fallo (se ignora; el descarte ya quedo contado en `enqueue`).
        """
        # Nunca escribir a stderr desde aqui: podria ser el mismo pipe
        # trabado del incidente. Un descarte ya quedo contado en `enqueue`.
        pass


class _UniqueSentinelQueueListener(logging.handlers.QueueListener):
    """`QueueListener` normal, salvo que el centinela de apagado es un objeto
    propio (no el `None` por defecto): `BoundedQueueHandler.enqueue` puede
    desalojar cualquier cosa de la cola para hacerle lugar a un WARNING+, y
    con el centinela por defecto (`None`) no se podria distinguir "cola vacia"
    de "se desalojo el centinela" -- con uno unico no hay ambiguedad."""

    _sentinel = object()


def _stop_listener(
    listener: logging.handlers.QueueListener, join_timeout: float | None
) -> None:
    """Igual que `listener.stop()`, pero (a) tolera que la cola este llena
    justo al encolar el centinela (el `stop()` de la libreria no lo hace: usa
    `put_nowait` sin capturar `queue.Full`) y (b) permite un `join` con
    timeout -- el de consola nunca debe colgar `shutdown_logging()`."""
    try:
        listener.queue.put_nowait(listener._sentinel)
    except queue.Full:
        try:
            listener.queue.get_nowait()
        except queue.Empty:
            pass
        try:
            listener.queue.put_nowait(listener._sentinel)
        except queue.Full:
            pass  # ultimo recurso: el hilo (daemon) puede quedar sin apagarse.
    thread: threading.Thread | None = listener._thread
    if thread is not None:
        thread.join(timeout=join_timeout)
        if not thread.is_alive():
            listener._thread = None


_file_listener: logging.handlers.QueueListener | None = None
_console_listener: logging.handlers.QueueListener | None = None
_file_handler: BoundedQueueHandler | None = None
_console_handler: BoundedQueueHandler | None = None


def _detach_uvicorn_handlers() -> None:
    """Ver el docstring del modulo. Uvicorn ya corrio su propia configuracion
    de logging antes de este punto (el lifespan arranca despues) -- esto
    limpia lo que haya puesto y deja que todo propague al logger raiz."""
    for name in _UVICORN_LOGGER_NAMES:
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True


def setup_logging() -> None:
    """Idempotente: una segunda llamada en el mismo proceso no hace nada (ver
    `tests/unit/test_logging.py` y el test de `httpx` en
    `test_universe_refresh.py`, que la llaman directo)."""
    global _file_listener, _console_listener, _file_handler, _console_handler
    if _file_listener is not None:
        return

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    formatter = logging.Formatter(LOG_FORMAT)

    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    file_real_handler = logging.handlers.RotatingFileHandler(
        log_dir / LOG_FILE_NAME, maxBytes=LOG_ROTATE_MAX_BYTES,
        backupCount=LOG_ROTATE_BACKUP_COUNT, encoding="utf-8",
    )
    file_real_handler.setFormatter(formatter)
    console_real_handler = logging.StreamHandler()
    console_real_handler.setFormatter(formatter)

    file_queue: queue.Queue = queue.Queue(maxsize=FILE_QUEUE_MAXSIZE)
    console_queue: queue.Queue = queue.Queue(maxsize=CONSOLE_QUEUE_MAXSIZE)
    _file_handler = BoundedQueueHandler(file_queue)
    _console_handler = BoundedQueueHandler(console_queue)

    _file_listener = _UniqueSentinelQueueListener(file_queue, file_real_handler)
    _console_listener = _UniqueSentinelQueueListener(console_queue, console_real_handler)
    _file_listener.start()
    _console_listener.start()

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(_file_handler)
    root.addHandler(_console_handler)

    # httpx registra cada peticion a INFO: con LOG_LEVEL=INFO el log se llena de
    # llamadas a la API sin valor operativo. Los avisos y errores siguen visibles.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _detach_uvicorn_handlers()


def shutdown_logging() -> None:
    """Para el lifespan de FastAPI (y para que los tests no dejen hilos
    corriendo entre si). Primero el archivo (espera sin limite, vacia su
    cola), despues consola (con timeout, nunca cuelga el apagado)."""
    global _file_listener, _console_listener, _file_handler, _console_handler
    if _file_listener is not None:
        _stop_listener(_file_listener, join_timeout=None)
        _file_listener = None
    if _console_listener is not None:
        _stop_listener(_console_listener, join_timeout=CONSOLE_STOP_JOIN_TIMEOUT_SECONDS)
        _console_listener = None
    _file_handler = None
    _console_handler = None


def get_dropped_log_count() -> int:
    """Total de registros DEBUG/INFO descartados por cola llena (archivo +
    consola) desde el ultimo `setup_logging()`. Expuesto en el heartbeat y en
    `GET /health` para notar un cuelgue de consola antes de que importe."""
    total = 0
    if _file_handler is not None:
        total += _file_handler.dropped_count
    if _console_handler is not None:
        total += _console_handler.dropped_count
    return total


def get_logger(name: str) -> logging.Logger:
    """El logger de un modulo; se usa como `logger = get_logger(__name__)`.

    Args:
        name: nombre del logger, normalmente `__name__`.

    Returns:
        El `logging.Logger` de ese nombre.
    """
    return logging.getLogger(name)
