"""Persistencia de Minerva: la base SQLite (`database.py`), los modelos pydantic de sus
filas (`models.py`) y un repositorio por grupo de tablas en `repositories/`.

Regla de la capa: ningun otro paquete escribe SQL; todo pasa por un repositorio. El
esquema esta en `database.SCHEMA` y se documenta en `docs/ESQUEMA_BD.md`.
"""

