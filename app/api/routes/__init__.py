"""Rutas de la API, una por modulo; `app/main.py` las registra.

Los routers de `/api/` declaran `dependencies=[Depends(require_auth)]` a nivel de router,
para que una ruta nueva quede protegida por construccion.
"""

