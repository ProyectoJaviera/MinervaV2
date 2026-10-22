"""Ayudantes de las pruebas que vigilan el CODIGO FUENTE del repositorio (no su comportamiento):
sin ventanas de consola y sin acceso al Programador de tareas real."""

from __future__ import annotations

import ast
import re


def powershell_code(text: str) -> str:
    """El codigo de un .ps1 sin comentarios de bloque (`<# ... #>`) ni de linea."""
    text = re.sub(r"<#.*?#>", "", text, flags=re.DOTALL)
    return re.sub(r"(?m)(^|\s)#.*$", r"\1", text)


def embedded_strings(source: str, *, with_docstrings: bool = True) -> list[str]:
    """Los textos literales de un .py (los f-strings, con `{}` en lugar de cada valor). Con
    `with_docstrings=False` se omiten los docstrings de modulo, clase y funcion."""
    tree = ast.parse(source)
    skipped: set[int] = set()
    if not with_docstrings:
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                first = node.body[0] if node.body else None
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                    skipped.add(id(first.value))
    for node in ast.walk(tree):  # los trozos de un f-string no se leen sueltos: va entero
        if isinstance(node, ast.JoinedStr):
            skipped.update(id(part) for part in node.values if isinstance(part, ast.Constant))
    texts = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in skipped:
                texts.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            texts.append("".join(
                part.value if isinstance(part, ast.Constant) else "{}" for part in node.values
            ))
    return texts
