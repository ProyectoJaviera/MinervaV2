"""Nucleo transversal: el scheduler, el logging que nunca bloquea, el latido y el cliente HTTP.

Todo lo demas depende de este paquete, y este paquete casi no depende de nada (solo de
`app.config` y, el scheduler, de los paquetes que orquesta).
"""

