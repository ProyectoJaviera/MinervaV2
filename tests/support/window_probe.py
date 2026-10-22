"""Sondeo de ventanas de consola VISIBLES (solo Windows, solo lectura).

`console_windows()` enumera las ventanas de nivel superior visibles cuya clase es la de una consola
y devuelve, de cada una, su titulo y la linea de comandos del proceso dueno. Sirve para comprobar de
verdad (no solo por analisis estatico) que un lanzador de las pruebas no abre una consola visible.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
from dataclasses import dataclass

CONSOLE_CLASSES = ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS")
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_COMMAND_LINE_INFORMATION = 60


class _UnicodeString(ctypes.Structure):
    _fields_ = [("Length", ctypes.c_ushort), ("MaximumLength", ctypes.c_ushort),
                ("Buffer", ctypes.c_void_p)]


@dataclass(frozen=True)
class ConsoleWindow:
    hwnd: int
    title: str
    cmd: str


def _command_line(pid: int) -> str:
    kernel32, ntdll = ctypes.windll.kernel32, ctypes.windll.ntdll
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buffer = ctypes.create_string_buffer(8192)
        returned = wt.ULONG(0)
        status = ntdll.NtQueryInformationProcess(
            handle, PROCESS_COMMAND_LINE_INFORMATION, buffer, 8192, ctypes.byref(returned),
        )
        if status != 0:
            return ""
        text = _UnicodeString.from_buffer(buffer)
        return ctypes.wstring_at(text.Buffer, text.Length // 2)
    finally:
        kernel32.CloseHandle(handle)


def console_windows() -> list[ConsoleWindow]:
    """Las consolas VISIBLES que existen ahora mismo."""
    user32 = ctypes.windll.user32
    found: list[ConsoleWindow] = []
    callback_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def callback(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            if cls.value in CONSOLE_CLASSES:
                title = ctypes.create_unicode_buffer(2048)
                user32.GetWindowTextW(hwnd, title, 2048)
                pid = wt.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                found.append(ConsoleWindow(int(hwnd), title.value, _command_line(pid.value)))
        return True

    user32.EnumWindows(callback_type(callback), 0)
    return found
