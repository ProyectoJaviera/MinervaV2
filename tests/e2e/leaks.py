"""Barrido de FUGAS de resultados para las pruebas E2E: recorre TODOS los canales por los que el
sistema habla con la persona (API de lectura, rutas heredadas, `/health`, comandos de Telegram,
avisos push y resumen diario) y busca resultados de operaciones cerradas que, con la puerta de
resultados cerrada, no deben salir NUNCA. Tres detectores independientes:

1. **Claves** prohibidas en cualquier JSON, a cualquier profundidad (`pnl_net_usdt`, `exit_price`,
   `close_reason`, ...).
2. **Centinelas numericos**: las representaciones (repr, 2/4/6 decimales, con signo) de cada valor
   de resultado REAL de la base -- PnL, precio de salida, comisiones de salida, saldo, equity, PnL
   flotante, factor de beneficio --, descontando los valores que SI se muestran legitimamente
   (p. ej. el precio de salida de un cierre por TP es el propio TP, que es un nivel visible).
3. **Etiquetas** que solo aparecen en el modo revelado (`PnL flotante 1.23`, `Balance realizado:`,
   `Motivo TP`, ...).

Un barrido que nunca encontrara nada no probaria nada: la prueba de sensibilidad abre la puerta y
exige que cada detector SI encuentre cosas.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from tests.e2e.world import World

# Claves de resultado: no pueden aparecer en ninguna respuesta JSON mientras la puerta este cerrada.
FORBIDDEN_KEYS = frozenset({
    "pnl_net_usdt", "pnl_gross_usdt", "exit_price", "close_reason", "fee_exit_usdt",
    "fee_entry_usdt", "funding_paid_usdt", "slippage_exit_usdt", "slippage_entry_usdt",
    "best_price", "effective_stop", "fill_source", "realized_pnl_usdt", "unrealized_pnl_usdt",
    "equity_usdt", "balance_usdt", "profit_factor", "wins", "losses", "winrate",
    "consecutive_losses", "drawdown_trigger_count",
})

# Solo en las sombras: la hora de cierre tambien es un resultado (en la cuenta paper se muestra a
# proposito, por pedido de Renzo; ver docs/FASE5_PLAN.md).
SHADOW_ONLY_FORBIDDEN = frozenset({"closed_at"})

# Etiquetas de texto que solo salen cuando los resultados son visibles.
REVEAL_ONLY_TEXT = {
    "pnl_flotante": re.compile(r"PnL flotante\s+[-+]?\d"),
    "balance_realizado": re.compile(r"Balance realizado:\s*[-+]?\d"),
    "equity": re.compile(r"Equity \(balance \+ PnL flotante\):\s*\d"),
    "estado_balance": re.compile(r"\| balance \d"),
    "metricas_pf": re.compile(r"\| PF [\dn]"),
    "cierre_motivo": re.compile(r"Motivo (SL|TP|TRAILING|LIQUIDATION)\b"),
    "cierre_pnl": re.compile(r"PnL neto [-+]?\d"),
    "cierre_salida": re.compile(r"salida \d"),
}

JSON_ROUTES_WITH_SESSION = (
    "/api/account", "/api/arm", "/api/risk", "/api/universe", "/api/backtest/verdicts",
    "/api/control/info", "/api/control/audit", "/api/measurement/status",
    "/api/llm/decisions/recent", "/api/shadow-trades/recent", "/api/news/status",
    "/api/news/recent", "/api/memory/status", "/api/memory/lessons?status=ALL",
    "/api/memory/reports?kind=POSTMORTEM", "/api/memory/reports?kind=CALIBRATION",
)
JSON_ROUTES_OPEN = ("/health", "/trades", "/positions")
TELEGRAM_COMMANDS = ("/status", "/positions", "/balance", "/metrics", "/help")


@dataclass
class Channel:
    name: str
    text: str
    parsed: Any = None  # el JSON ya interpretado (solo canales JSON)


async def collect_channels(world: World) -> dict[str, Channel]:
    """Lo que cada canal le dice HOY a la persona, tal cual."""
    channels: dict[str, Channel] = {}
    await world.login()
    for path in JSON_ROUTES_OPEN + JSON_ROUTES_WITH_SESSION:
        response = await world.get(path)
        assert response.status_code == 200, f"{path} -> {response.status_code}: {response.text}"
        channels[path] = Channel(path, response.text, response.json())
    for command in TELEGRAM_COMMANDS:
        reply = await world.say(command)
        assert reply, command
        channels[f"telegram {command}"] = Channel(f"telegram {command}", reply)
    await world.notifications()  # vacia lo pendiente
    for i, message in enumerate(world.push_client.sent):
        channels[f"push #{i}"] = Channel(f"push #{i}", message)
    channels["resumen diario"] = Channel("resumen diario", await world.daily_summary())
    return channels


def numeric_renderings(value: float | None) -> set[str]:
    """Como podria aparecer `value` en un JSON o en un mensaje de texto."""
    if value is None or abs(value) < 1e-9:  # un cero ("0.0000") no es un centinela: choca con todo
        return set()
    out = {repr(float(value)), f"{value:.4f}", f"{value:.6f}", f"{value:+.4f}", f"{value:.8g}"}
    if abs(value) >= 1:
        out.add(f"{value:.2f}")
    # Los muy cortos ("0.00", "5") chocarian con cualquier numero legitimo y no dicen nada.
    return {r for r in out if len(r.lstrip("+-")) >= 4}


LEGIT_FIELDS = (
    "entry_price", "sl_price", "tp_price", "liq_price", "trailing_distance", "qty",
    "notional_usdt", "margin_usdt", "leverage", "sl_margin_loss_pct", "decision_delay_s",
)
RESULT_FIELDS = (
    "pnl_net_usdt", "pnl_gross_usdt", "exit_price", "fee_exit_usdt", "slippage_exit_usdt",
    "funding_paid_usdt",
)


async def sentinels_for(world: World) -> set[str]:
    """Las representaciones de cada valor de RESULTADO de la base, sin las de lo que se muestra
    legitimamente. Incluye saldo, equity, PnL flotante y factor de beneficio de la cuenta."""
    rows = [*await world.trades(), *await world.shadows()]
    legit: set[str] = set()
    for row in rows:
        for name in LEGIT_FIELDS:
            legit |= numeric_renderings(row.get(name))
    # Los precios ACTUALES de mercado son datos publicos que el dashboard muestra (el mark del
    # universo): si el ultimo precio coincide con el de una salida, no es una fuga.
    for price in (*world.rest.last.values(), *world.rest.mark.values(),
                  *world.monitor.marks.values()):
        legit |= numeric_renderings(price)
    values: list[float] = []
    for row in rows:
        values += [row[name] for name in RESULT_FIELDS if row.get(name) is not None]
    closed = [t for t in await world.trades("CLOSED") if t["pnl_net_usdt"] is not None]
    wins = sum(t["pnl_net_usdt"] for t in closed if t["pnl_net_usdt"] > 0)
    losses = -sum(t["pnl_net_usdt"] for t in closed if t["pnl_net_usdt"] < 0)
    if wins and losses:
        values.append(wins / losses)
    values.append(await world.backend.get_balance())
    values.append(await world.backend.get_equity(marks=dict(world.monitor.marks)))
    for position in await world.backend.get_positions():
        mark = world.monitor.marks.get(position.symbol)
        if mark is not None:
            values.append(world.backend.unrealized_pnl_usdt(position, mark))
    found: set[str] = set()
    for value in values:
        found |= numeric_renderings(value)
    return found - legit


def keys_in(obj: Any) -> set[str]:
    if isinstance(obj, dict):
        return set(obj) | {k for v in obj.values() for k in keys_in(v)}
    if isinstance(obj, list):
        return {k for item in obj for k in keys_in(item)}
    return set()


def find_number(text: str, rendering: str) -> re.Match[str] | None:
    """`rendering` como NUMERO completo: no vale como parte de otra cifra ("1.72" dentro de
    "21.725" o de "0.00001.72" no es una fuga del valor 1.72)."""
    return re.search(rf"(?<![\d.]){re.escape(rendering)}(?!\d)", text)


def leaks_in(channel: Channel, sentinels: set[str]) -> list[str]:
    """Todo lo que NO deberia estar en este canal con la puerta cerrada."""
    problems = [f"{channel.name}: clave prohibida {k!r}"
                for k in sorted(keys_in(channel.parsed) & FORBIDDEN_KEYS)]
    if channel.name == "/api/shadow-trades/recent":
        problems += [f"{channel.name}: clave prohibida {k!r}"
                     for k in sorted(keys_in(channel.parsed) & SHADOW_ONLY_FORBIDDEN)]
    for sentinel in sorted(sentinels):
        match = find_number(channel.text, sentinel)
        if match:
            around = channel.text[max(0, match.start() - 30):match.end() + 30]
            problems.append(f"{channel.name}: centinela {sentinel!r} en ...{around!r}...")
    problems += [f"{channel.name}: etiqueta de resultados {label!r}"
                 for label, pattern in REVEAL_ONLY_TEXT.items() if pattern.search(channel.text)]
    return problems


def all_leaks(channels: dict[str, Channel], sentinels: set[str]) -> list[str]:
    return [p for channel in channels.values() for p in leaks_in(channel, sentinels)]


def revealed_labels(channels: dict[str, Channel]) -> set[str]:
    return {
        label for channel in channels.values()
        for label, pattern in REVEAL_ONLY_TEXT.items() if pattern.search(channel.text)
    }


def pretty(channels: dict[str, Channel]) -> str:  # para diagnosticar un fallo
    return json.dumps({k: v.text[:400] for k, v in channels.items()}, indent=1, ensure_ascii=False)
