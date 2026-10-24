"""Modulo de red centralizado: TODOS los clientes HTTP asincronos del
proyecto (Bitunix hoy; CoinGecko en Fase 2, cualquier otro despues) deben
construirse con `build_async_http_client()` en vez de instanciar
`httpx.AsyncClient` directamente.

Usa `truststore` (almacen de certificados del sistema operativo) en vez del
bundle embebido de `certifi`, para funcionar tambien en redes con
inspeccion TLS corporativa -- y exactamente igual en redes sin proxy.

Docker: un contenedor recien construido NO tiene el certificado raiz de una
inspeccion TLS corporativa en su almacen de confianza del sistema operativo
(a diferencia de la maquina host, que si lo tiene instalado), y `truststore`
lee el almacen de la IMAGEN. Si el bot corre detras de un proxy con inspeccion
TLS, el certificado raiz se instala al construir la imagen: se deja un `.crt`
en `docker/certs/` (ver `docker/certs/README.md`); sin eso las llamadas a
Bitunix/CoinGecko fallan con `CERTIFICATE_VERIFY_FAILED` dentro del
contenedor aunque funcionen en la maquina host.

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
    """Construye el cliente HTTP asincrono que debe usar TODO el proyecto.

    Usa el almacen de certificados del sistema operativo (`truststore`) y desactiva el
    keep-alive (ver el docstring del modulo para el porque de ambas decisiones).

    Args:
        timeout: segundos de espera maxima por peticion.

    Returns:
        Un `httpx.AsyncClient` nuevo; quien lo crea debe cerrarlo con `aclose()`.
    """
    ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    return httpx.AsyncClient(timeout=timeout, verify=ssl_context, limits=NO_KEEPALIVE_LIMITS)
