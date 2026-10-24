"""API HTTP de Minerva (FastAPI): lectura de estado y tres controles, nada de operar.

Solo `GET /health`, `GET /positions` y `GET /trades` son publicos (y los dos ultimos
ocultan los resultados hasta el registro oficial); el resto de `/api/` exige login. Las
unicas escrituras son login y logout y `POST /api/control/{action}` (pausar, reanudar,
kill switch). Ningun endpoint abre ni cierra posiciones. Todo resultado de la cuenta o
de las sombras sale por una lista BLANCA de `serializers.py` y solo con
`is_outcome_reveal_allowed`.
"""

