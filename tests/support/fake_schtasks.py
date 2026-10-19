"""Un `schtasks` FALSO para probar la mudanza (Fase 7) sin tocar el Programador de tareas real.

    python fake_schtasks.py /Query /TN <nombre> /XML
    python fake_schtasks.py /Change /TN <nombre> /DISABLE | /ENABLE

El estado vive en el JSON que indica FAKE_SCHTASKS_STATE: {"<nombre>": "Enabled" | "Disabled"}. Una
tarea que no figura no existe. `fail` (lista de textos) hace fallar toda llamada que lo contenga.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
{enabled}  </Settings>
</Task>
"""


def main(argv: list[str]) -> int:
    path = Path(os.environ["FAKE_SCHTASKS_STATE"])
    state = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    calls = state.setdefault("_calls", [])
    calls.append(" ".join(argv))
    joined = " ".join(argv)
    if any(text in joined for text in state.get("_fail", [])):
        path.write_text(json.dumps(state), encoding="utf-8")
        sys.stderr.write("ERROR: Acceso denegado (fallo forzado por la prueba)\n")
        return 1
    flags = [a.upper() for a in argv]
    name = argv[flags.index("/TN") + 1] if "/TN" in flags else ""
    code = 0
    if "/QUERY" in flags:
        task = state.get(name)
        if task is None:
            sys.stderr.write("ERROR: El sistema no puede encontrar el archivo especificado.\n")
            code = 1
        else:
            disabled = "    <Enabled>false</Enabled>\n"
            print(XML.format(enabled=disabled if task == "Disabled" else ""))
    elif "/CHANGE" in flags:
        if name not in state:
            sys.stderr.write("ERROR: El sistema no puede encontrar el archivo especificado.\n")
            code = 1
        elif "/DISABLE" in flags:
            state[name] = "Disabled"
        elif "/ENABLE" in flags:
            state[name] = "Enabled"
    path.write_text(json.dumps(state), encoding="utf-8")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
