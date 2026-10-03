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
        """Abre una posicion LONG o SHORT (capacidad 1 de SPEC.md).
        `sl_margin_loss_pct` (Fase 3 subfase 3.2) es el riesgo planeado al
        abrir -- el motor de riesgo lo compara contra
        `Settings.live_sl_margin_cap_pct`. `is_manual` (subfase 3.2,
        correccion) marca una entrada tecleada por un humano: solo esas
        pueden omitir `sl_margin_loss_pct` y saltar la lista de
        elegibilidad por estrategia -- toda entrada generada por una
        estrategia automatica debe declarar ambos."""
        raise NotImplementedError

    @abstractmethod
    async def close_position(self, trade_id: int, reason: str = "MANUAL") -> Trade:
        """Cierra una posicion de inmediato (capacidad 2 de SPEC.md)."""
        raise NotImplementedError

    @abstractmethod
    async def get_balance(self) -> float:
        """Saldo total simulado (capacidad 6 de SPEC.md)."""
        raise NotImplementedError

    @abstractmethod
    async def get_positions(self) -> list[Trade]:
        """Posiciones abiertas (capacidad 7 de SPEC.md)."""
        raise NotImplementedError
