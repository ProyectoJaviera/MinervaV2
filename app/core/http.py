"""Modulo de red centralizado: TODOS los clientes HTTP asincronos del
proyecto (Bitunix hoy; CoinGecko en Fase 2, cualquier otro despues) deben
construirse con `build_async_http_client()` en vez de instanciar
`httpx.AsyncClient` directamente.

Usa `truststore` (almacen de certificados del sistema operativo) en vez del
bundle embebido de `certifi`, para funcionar tambien en redes con
inspeccion TLS corporativa -- y exactamente igual en redes sin proxy.

Nota para Fase 6 (Docker): un contenedor recien construido NO tiene el
certificado raiz de una inspeccion TLS corporativa en su almacen de
confianza del sistema operativo (a diferencia de la maquina host, que si lo
tiene instalado). Si el bot corre detras de un proxy con inspeccion TLS,
la imagen Docker debera montar/instalar ese certificado raiz en el
contenedor (ver PROGRESS.md, pendiente de Fase 6) o las llamadas a Bitunix/
CoinGecko fallaran con `CERTIFICATE_VERIFY_FAILED` dentro del contenedor
aunque funcionen en la maquina host.

Keep-alive deshabilitado (`max_keepalive_connections=0`): se observo en la
corrida real de Fase 2 una conexion persistente reutilizada que quedaba
colgada esperando una respuesta que nunca llegaba -- ni el timeout de httpx
ni `asyncio.wait_for` lograban cortarla (la cancelacion no se propagaba a
tiempo a la operacion de socket subyacente en este entorno). Forzar una
conexion TCP nueva en cada request es mas lento (~20-50ms extra por
llamada) pero elimina la clase de problema por completo.
"""

from __future__ import annotations

import ssl

import httpx
import truststore

NO_KEEPALIVE_LIMITS = httpx.Limits(max_keepalive_connections=0)


def build_async_http_client(timeout: float = 10.0) -> httpx.AsyncClient:
    ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    return httpx.AsyncClient(timeout=timeout, verify=ssl_context, limits=NO_KEEPALIVE_LIMITS)
