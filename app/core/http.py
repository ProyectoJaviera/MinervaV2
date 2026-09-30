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
"""

from __future__ import annotations

import ssl

import httpx
import truststore


def build_async_http_client(timeout: float = 10.0) -> httpx.AsyncClient:
    ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    return httpx.AsyncClient(timeout=timeout, verify=ssl_context)
