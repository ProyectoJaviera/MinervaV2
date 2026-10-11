"""Logging no bloqueante (incidente de estabilidad 2026-10-11: el bot se
congelo ~4h porque un pipe de consola trabado bloqueo el `write()` sincrono
del `StreamHandler` de `logging.basicConfig`, y con eso el event loop
completo). Ver el docstring de `app/core/logging.py` para el diseno."""

from __future__ import annotations

import logging
import logging.handlers
import queue
import threading
import time

import pytest

import app.core.logging as core_logging
from app.config import settings as real_settings
from app.core.logging import (
    CONSOLE_QUEUE_MAXSIZE,
    LOG_FORMAT,
    BoundedQueueHandler,
    _stop_listener,
    _UniqueSentinelQueueListener,
    get_dropped_log_count,
    setup_logging,
    shutdown_logging,
)


def _record(level: int, msg: str = "x") -> logging.LogRecord:
    return logging.LogRecord("t", level, __file__, 1, msg, None, None)


# --- BoundedQueueHandler: nunca bloquea, cuenta descartes, prioriza WARNING+ -


def test_enqueue_never_raises_and_returns_even_when_the_queue_is_full():
    handler = BoundedQueueHandler(queue.Queue(maxsize=2))
    handler.enqueue(_record(logging.INFO))
    handler.enqueue(_record(logging.INFO))
    # La cola ya esta llena: esto no debe lanzar ni bloquear.
    handler.enqueue(_record(logging.INFO))
    assert handler.dropped_count == 1


def test_info_and_debug_are_dropped_silently_without_touching_stderr(capsys):
    handler = BoundedQueueHandler(queue.Queue(maxsize=1))
    handler.enqueue(_record(logging.DEBUG))
    handler.enqueue(_record(logging.INFO))  # cola llena -> se descarta
    assert handler.dropped_count == 1
    captured = capsys.readouterr()
    assert captured.err == "" and captured.out == ""


def test_a_warning_evicts_an_older_low_priority_record_instead_of_being_dropped():
    q: queue.Queue = queue.Queue(maxsize=2)
    handler = BoundedQueueHandler(q)
    handler.enqueue(_record(logging.INFO, "viejo-1"))
    handler.enqueue(_record(logging.INFO, "viejo-2"))
    handler.enqueue(_record(logging.WARNING, "importante"))  # cola llena de INFO

    assert handler.dropped_count == 1  # se perdio un INFO viejo, no el WARNING
    remaining = [q.get_nowait().msg for _ in range(q.qsize())]
    assert "importante" in remaining
    assert len(remaining) == 2  # sigue acotada a maxsize


def test_a_new_warning_is_dropped_only_if_the_queue_is_full_of_warnings_too():
    q: queue.Queue = queue.Queue(maxsize=1)
    handler = BoundedQueueHandler(q)
    handler.enqueue(_record(logging.ERROR, "primero"))
    handler.enqueue(_record(logging.WARNING, "segundo"))  # nada barato para sacrificar

    assert handler.dropped_count == 1  # se descarta el NUEVO, no el que ya estaba
    assert q.get_nowait().msg == "primero"


def test_dropped_count_is_visible_through_get_dropped_log_count(monkeypatch, tmp_path):
    monkeypatch.setattr(real_settings, "log_dir", str(tmp_path))
    setup_logging()
    try:
        core_logging._file_handler.dropped_count = 3
        core_logging._console_handler.dropped_count = 4
        assert get_dropped_log_count() == 7
    finally:
        shutdown_logging()


# --- uvicorn configura handlers sincronos propios: hay que quitarselos ------


class _BlockingHandler(logging.Handler):
    """Simula el handler sincrono que uvicorn deja en `uvicorn.access`/
    `uvicorn.error` antes de que corra `setup_logging` -- si no se le quita,
    bloquea igual que el incidente original."""

    def __init__(self, sleep_seconds: float) -> None:
        super().__init__()
        self.sleep_seconds = sleep_seconds
        self.calls = 0

    def emit(self, record: logging.LogRecord) -> None:
        self.calls += 1
        time.sleep(self.sleep_seconds)


def test_setup_logging_detaches_and_silences_uvicorns_blocking_handlers(monkeypatch, tmp_path):
    monkeypatch.setattr(real_settings, "log_dir", str(tmp_path))
    blocking = _BlockingHandler(sleep_seconds=2.0)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = [blocking]
        lg.propagate = False

    setup_logging()
    try:
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            lg = logging.getLogger(name)
            assert lg.handlers == []
            assert lg.propagate is True

        start = time.monotonic()
        logging.getLogger("uvicorn.access").warning("GET /health 200")
        elapsed = time.monotonic() - start
        assert elapsed < 0.2, "el log de uvicorn.access no debe pasar por el handler trabado"
        assert blocking.calls == 0
    finally:
        shutdown_logging()
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            logging.getLogger(name).handlers = []


def test_setup_logging_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setattr(real_settings, "log_dir", str(tmp_path))
    setup_logging()
    try:
        first = (core_logging._file_listener, core_logging._console_listener)
        setup_logging()  # segunda llamada: no-op
        assert (core_logging._file_listener, core_logging._console_listener) == first
        assert len(logging.getLogger().handlers) == 2
    finally:
        shutdown_logging()


# --- formato: no romper los Select-String / grep ya existentes -------------


def test_log_format_keeps_the_pipe_separator():
    assert LOG_FORMAT == "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


# --- apagado: archivo vacia su cola, consola nunca cuelga -------------------


class _RecordingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.received: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.received.append(record.getMessage())


def test_stopping_the_file_listener_drains_every_queued_record_first():
    q: queue.Queue = queue.Queue(maxsize=100)
    recorder = _RecordingHandler()
    listener = _UniqueSentinelQueueListener(q, recorder)
    listener.start()
    for i in range(50):
        q.put_nowait(_record(logging.INFO, f"linea-{i}"))

    _stop_listener(listener, join_timeout=None)

    assert listener._thread is None
    assert recorder.received == [f"linea-{i}" for i in range(50)]


class _HangingHandler(logging.Handler):
    """Un handler cuyo `emit` queda bloqueado hasta que el TEST libera
    `release_event` -- simula el pipe de consola trabado del incidente SIN
    dejar un hilo colgado para siempre. `Handler.handle()` toma `self.lock`
    (un `RLock`) antes de llamar a `emit` y lo libera recien al volver: un
    `Event()` nuevo que JAMAS se pone (la version anterior de esta prueba)
    deja ese lock tomado para siempre, y `logging.shutdown()` -- registrado
    via `atexit`, recorre TODO handler alguna vez creado, sin importar si
    esta adjunto a algun logger -- hace `h.acquire()` sobre ese mismo lock al
    salir del proceso: pytest ya reporto la prueba como "passed", pero el
    PROCESO queda colgado para siempre (confirmado en Python 3.13; en algunos
    entornos no se manifiesta, pero el riesgo es real en cualquiera)."""

    def __init__(self) -> None:
        super().__init__()
        self.release_event = threading.Event()

    def emit(self, record: logging.LogRecord) -> None:
        self.release_event.wait()


def test_stopping_the_console_listener_never_hangs_even_if_its_handler_is_stuck():
    q: queue.Queue = queue.Queue(maxsize=CONSOLE_QUEUE_MAXSIZE)
    handler = _HangingHandler()
    listener = _UniqueSentinelQueueListener(q, handler)
    listener.start()
    try:
        q.put_nowait(_record(logging.INFO, "esto nunca termina de escribirse"))
        time.sleep(0.05)  # deja que el hilo entre al handler trabado

        start = time.monotonic()
        _stop_listener(listener, join_timeout=0.3)
        elapsed = time.monotonic() - start

        assert elapsed < 1.0, "shutdown_logging no debe colgarse esperando a consola"
        # El hilo (daemon) sigue vivo, trabado en emit() -- eso es lo que se
        # prueba arriba (el apagado no lo espera). Pero no puede quedar asi
        # para siempre: ver el docstring de `_HangingHandler`.
    finally:
        handler.release_event.set()
        thread = listener._thread
        if thread is not None:
            thread.join(timeout=2.0)
            assert not thread.is_alive(), (
                "el hilo deberia terminar (y soltar el lock del handler) tras liberar el evento"
            )


@pytest.mark.parametrize("name", ["httpx"])
def test_setup_logging_still_silences_noisy_third_party_loggers(name, monkeypatch, tmp_path):
    monkeypatch.setattr(real_settings, "log_dir", str(tmp_path))
    setup_logging()
    try:
        assert logging.getLogger(name).level == logging.WARNING
    finally:
        shutdown_logging()
