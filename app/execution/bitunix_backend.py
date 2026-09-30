"""`BitunixBackend` -- DISENO/DOCUMENTACION de la futura ejecucion real. NO
SE INSTANCIA EN NINGUN PUNTO DEL CODIGO. Regla critica de CLAUDE.md: v1 es
100% paper trading; prohibido enviar ordenes reales a Bitunix.

Esta clase existe solo para dejar fijada la interfaz (mismos metodos que
`PaperBackend`, via `ExecutionBackend`) que tendria la ejecucion real en una
version futura, y para documentar lo que YA esta verificado vs. lo que falta
verificar antes de escribir una sola linea de esa implementacion:

Verificado en docs/FASE0.md (Fase 0), autenticacion REST de Bitunix:
    Headers: `api-key`, `nonce` (string aleatorio), `timestamp` (ms),
    `sign` (HMAC-SHA256), `Content-Type: application/json`.

NO verificado todavia (requiere nueva investigacion contra la documentacion
oficial antes de implementar, por regla de CLAUDE.md -- "no inventes
endpoints, parametros ni firmas"):
    - Ruta y payload exactos para colocar/cancelar ordenes a mercado.
    - Endpoints para fijar apalancamiento y modo de margen aislado.
    - Endpoint de saldo de cuenta y de posiciones activas autenticadas.
    - Cualquier limite de tasa especifico para endpoints autenticados.

Cuando exista una version futura con dinero real, cada metodo debera:
1. Firmar la peticion segun el esquema de arriba.
2. Verificar explicitamente contra la doc oficial cada endpoint antes de
   codificarlo (no reutilizar lo que se infirio para v1).
3. Requerir una confirmacion explicita fuera de este codigo (no un flag de
   entorno) para poder activarse.
"""

from __future__ import annotations

from app.execution.backend_base import ExecutionBackend
from app.persistence.models import Side, Trade


class BitunixBackend(ExecutionBackend):
    def __init__(self, *_args, **_kwargs) -> None:
        raise NotImplementedError(
            "BitunixBackend es solo un diseno documentado para v1. No se "
            "instancia: v1 es 100% paper trading (ver CLAUDE.md)."
        )

    async def open_position(
        self,
        symbol: str,
        side: Side,
        margin_usdt: float,
        leverage: int,
        strategy: str | None = None,
    ) -> Trade:
        raise NotImplementedError("Ejecucion real desactivada en v1.")

    async def close_position(self, trade_id: int, reason: str = "MANUAL") -> Trade:
        raise NotImplementedError("Ejecucion real desactivada en v1.")

    async def get_balance(self) -> float:
        raise NotImplementedError("Ejecucion real desactivada en v1.")

    async def get_positions(self) -> list[Trade]:
        raise NotImplementedError("Ejecucion real desactivada en v1.")
