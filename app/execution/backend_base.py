"""Interfaz `ExecutionBackend` (SPEC.md, capacidades 1-2-6-7 de esta fase).

Las capacidades de TP/SL/trailing al 100% (3, 4, 5 de SPEC.md) se agregan a
esta interfaz en Fase 3, junto con el simulador realista (liquidacion,
reconciliacion, evaluacion a nivel de tick). No se agregan ahora para no
comprometer un diseno que todavia no esta validado con datos reales.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.persistence.models import Side, Trade


class ExecutionBackend(ABC):
    """Interfaz de un backend de ejecucion: abrir, cerrar, saldo y posiciones.

    Hoy solo `PaperBackend` la implementa de verdad. Cubre las capacidades 1, 2, 6 y 7 de
    la especificacion; el SL, el TP y el trailing viven en el monitor de posiciones.
    """
    @abstractmethod
    async def open_position(
        self,
        symbol: str,
        side: Side,
        margin_usdt: float,
        leverage: int,
        strategy: str | None = None,
        sl_margin_loss_pct: float | None = None,
        is_manual: bool = False,
    ) -> Trade:
        """Abre una posicion LONG o SHORT (capacidad 1 de la especificacion).

        Args:
            symbol: simbolo, p. ej. `BTCUSDT`.
            side: direccion.
            margin_usdt: margen solicitado, en USDT.
            leverage: apalancamiento.
            strategy: estrategia que origina la entrada, o `None` si es manual.
            sl_margin_loss_pct: riesgo planeado al abrir (porcentaje del margen); el motor de
                riesgo lo compara con `LIVE_SL_MARGIN_CAP_PCT`.
            is_manual: True para una entrada tecleada por una persona; solo esas pueden omitir
                `sl_margin_loss_pct` y saltar la lista de elegibilidad por estrategia.

        Returns:
            La operacion abierta.

        Raises:
            NotImplementedError: en la interfaz base.
        """
        raise NotImplementedError

    @abstractmethod
    async def close_position(self, trade_id: int, reason: str = "MANUAL") -> Trade:
        """Cierra una posicion de inmediato (capacidad 2 de la especificacion).

        Args:
            trade_id: `trades.id` de la posicion.
            reason: motivo del cierre.

        Returns:
            La operacion ya cerrada.

        Raises:
            NotImplementedError: en la interfaz base.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_balance(self) -> float:
        """Saldo total simulado (capacidad 6 de la especificacion).

        Returns:
            El saldo en USDT.

        Raises:
            NotImplementedError: en la interfaz base.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_positions(self) -> list[Trade]:
        """Posiciones abiertas (capacidad 7 de la especificacion).

        Returns:
            Lista de operaciones abiertas.

        Raises:
            NotImplementedError: en la interfaz base.
        """
        raise NotImplementedError
