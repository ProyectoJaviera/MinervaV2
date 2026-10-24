"""Repositorios: funciones asincronas que leen y escriben las tablas, una por grupo.

Cada modulo recibe una `Database` abierta como primer argumento y devuelve modelos de
`app.persistence.models` (o dataclasses propias). Las tablas que llevan resultados
(PnL, tasas) no se exponen hacia fuera sin `is_outcome_reveal_allowed`.
"""

