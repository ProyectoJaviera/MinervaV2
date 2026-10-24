"""Datos de mercado: clientes de Bitunix y CoinGecko, velas, funding y universo de activos.

Solo endpoints publicos y gratuitos (v1 no usa credenciales de Bitunix). Todos los
clientes HTTP se construyen con `app.core.http.build_async_http_client`. Ninguna pieza
de este paquete escribe en la cuenta paper: lo que persiste son caches (velas, funding,
contratos) y el universo.
"""

