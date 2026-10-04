"""Configuracion central de Minerva (pydantic-settings).

Todas las variables se leen desde `.env` (ver `.env.example`). Algunos campos
(modelos LLM, presupuesto diario, umbral de confluencia) no se usan todavia
en Fase 1 -- se declaran ahora para no reabrir esta configuracion en Fase 4.

Ninguna credencial tiene un valor por defecto no vacio; en v1 no se usan
credenciales de Bitunix (solo endpoints publicos de mercado).
"""

from __future__ import annotations

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# **Unidades de los campos "_pct" (Fase 3, subfase 3.2, correccion)**: el
# nombre `_pct` por si solo NO dice la escala -- el codebase mezcla dos
# convenciones y mezclarlas es un error facil (p.ej. escribir
# `MAX_DRAWDOWN_PCT=20` pensando "20%" cuando el codigo espera `0.20`):
#   - FRACCION [0, 1] (0.20 = 20%): se multiplica/compara directo contra
#     una proporcion ya calculada como fraccion (equity, capital, conteos).
#   - PORCENTAJE [0, 100] (50.0 = 50%): se compara directo contra
#     `sl_margin_loss_pct`, que el motor de riesgo/backtest calcula como
#     `(distancia_precio / precio) * leverage * 100` -- ya en escala 0-100.
# Documentado para TODOS los campos "_pct" junto a su `Field(...)`, pero
# `_FRACTION_PCT_FIELDS`/`_PERCENT_PCT_FIELDS` (que `_validate_pct_units`
# usa para fallar fuerte al arrancar) cubren deliberadamente solo la
# configuracion de riesgo EN VIVO que el usuario edita en `.env` con
# consecuencias reales -- los parametros de investigacion del backtest
# (`max_sl_margin_loss_pct`, `backtest_fallback_sl_pct/tp_pct`, etc.) se
# usan a proposito con valores "fuera de escala" en los tests existentes
# para desactivar un tope o forzar un caso limite (p.ej.
# `MAX_SL_MARGIN_LOSS_PCT=1000.0` para que el tope de riesgo nunca se
# alcance en un escenario sintetico) -- validarlos aqui romperia esos tests
# sin aportar seguridad real (nunca controlan dinero, ni siquiera de papel).
_FRACTION_PCT_FIELDS = (
    "maker_fee_pct", "taker_fee_pct", "max_drawdown_pct", "max_daily_loss_pct",
    "max_capital_pct_per_asset", "tp_gap_tolerance_pct",
)
_PERCENT_PCT_FIELDS = ("live_sl_margin_cap_pct",)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- General ---
    env: str = Field(default="development", alias="ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    report_timezone: str = Field(default="America/Santiago", alias="REPORT_TIMEZONE")

    # --- Bitunix (solo endpoints publicos en v1) ---
    bitunix_rest_base_url: str = Field(
        default="https://fapi.bitunix.com", alias="BITUNIX_REST_BASE_URL"
    )
    bitunix_ws_public_url: str = Field(
        default="wss://fapi.bitunix.com/public/", alias="BITUNIX_WS_PUBLIC_URL"
    )
    bitunix_rate_limit_per_sec: int = Field(default=10, alias="BITUNIX_RATE_LIMIT_PER_SEC")
    trading_symbols: str = Field(default="BTCUSDT", alias="TRADING_SYMBOLS")
    kline_interval: str = Field(default="4h", alias="KLINE_INTERVAL")
    scheduler_poll_seconds: int = Field(default=60, alias="SCHEDULER_POLL_SECONDS")
    strategy_history_bars: int = Field(default=100, alias="STRATEGY_HISTORY_BARS")
    active_strategy: str = Field(default="ema_cross_9_21", alias="ACTIVE_STRATEGY")

    # --- Comisiones simuladas (FRACCION 0-1: 0.0006 = 0.06%) ---
    maker_fee_pct: float = Field(default=0.0002, alias="MAKER_FEE_PCT")
    taker_fee_pct: float = Field(default=0.0006, alias="TAKER_FEE_PCT")

    # --- Capital y tamano de posicion ---
    initial_capital_usdt: float = Field(default=100.0, alias="INITIAL_CAPITAL_USDT")
    default_margin_usdt: float = Field(default=10.0, alias="DEFAULT_MARGIN_USDT")
    leverage: int = Field(default=10, alias="LEVERAGE")
    margin_mode: str = Field(default="ISOLATED", alias="MARGIN_MODE")

    # --- Gestion de riesgo (motor en vivo desde Fase 3 subfase 3.2) ---
    # FRACCION 0-1 (0.20 = 20%) -- ver la nota de unidades al inicio del archivo.
    max_drawdown_pct: float = Field(default=0.20, alias="MAX_DRAWDOWN_PCT")
    max_daily_loss_pct: float = Field(default=0.05, alias="MAX_DAILY_LOSS_PCT")
    max_simultaneous_positions: int = Field(default=3, alias="MAX_SIMULTANEOUS_POSITIONS")
    max_capital_pct_per_asset: float = Field(default=0.10, alias="MAX_CAPITAL_PCT_PER_ASSET")
    consecutive_losses_circuit_breaker: int = Field(
        default=4, alias="CONSECUTIVE_LOSSES_CIRCUIT_BREAKER"
    )
    circuit_breaker_cooldown_hours: float = Field(
        default=8.0, alias="CIRCUIT_BREAKER_COOLDOWN_HOURS"
    )
    # Maximo de posiciones abiertas en la MISMA direccion (LONG o SHORT) a
    # la vez, sin importar en que simbolos -- acota el riesgo de
    # correlacion entre altcoins que `docs/FASE2_RIESGO.md` no puede medir
    # (ver docs/FASE3_PLAN.md seccion 4, tercera ronda de ajustes).
    max_same_direction_positions: int = Field(default=2, alias="MAX_SAME_DIRECTION_POSITIONS")
    # Tope de perdida del SL sobre el margen para operaciones EN VIVO --
    # analogo a MAX_SL_MARGIN_LOSS_PCT del backtest, pero configurable por
    # separado porque la evidencia que lo informa (docs/FASE2_RIESGO.md) es
    # distinta de los criterios de descarte del backtest.
    # PORCENTAJE 0-100 (50.0 = 50%, NO 0.50) -- se compara contra
    # `sl_margin_loss_pct`, que ya viene en esa escala (ver la nota de
    # unidades al inicio del archivo).
    live_sl_margin_cap_pct: float = Field(default=50.0, alias="LIVE_SL_MARGIN_CAP_PCT")
    # Modo del stop por drawdown: "duro" detiene nuevas entradas de verdad
    # (reanudacion manual); "alerta" solo notifica y sigue operando. En
    # AMBOS modos se incrementa un contador persistente de cuantas veces se
    # habria activado, para no perder esa senal si se corrio en "alerta".
    drawdown_stop_mode: str = Field(default="duro", alias="DRAWDOWN_STOP_MODE")
    # Estrategias habilitadas para ejecutarse en la cuenta REAL (paper, no
    # dinero real) -- las demas solo generan senales/operaciones sombra
    # (Fase 3 punto 2). Vacio = sin restriccion (todas elegibles).
    real_account_eligible_strategies: str = Field(
        default="ema_cross_9_21,funding_contrarian_experimental,"
        "funding_contrarian_percentile_experimental",
        alias="REAL_ACCOUNT_ELIGIBLE_STRATEGIES",
    )

    # --- Vigencia de datos y apertura sin LLM (Fase 3, subfase 3.3) ---
    # Funding obsoleto: el ultimo evento cacheado tiene mas antiguedad que el
    # intervalo de funding del contrato mas este margen (horas).
    funding_stale_margin_hours: float = Field(default=1.0, alias="FUNDING_STALE_MARGIN_HOURS")
    # Vela obsoleta: la ultima vela cerrada cerro hace mas de un intervalo mas
    # esta tolerancia (segundos) respecto a ahora.
    candle_stale_tolerance_seconds: float = Field(
        default=300.0, alias="CANDLE_STALE_TOLERANCE_SECONDS"
    )
    # Sin LLM (subfase 3.6 aun no existe) la apertura automatica en la cuenta
    # real esta desactivada; las senales se registran igual en `signals`.
    auto_open_without_llm: bool = Field(default=False, alias="AUTO_OPEN_WITHOUT_LLM")

    # --- Monitor de posiciones y feed en vivo (Fase 3, subfase 3.4) ---
    # Sin latido del WebSocket publico durante estos segundos (3x el ping de
    # 15s) no se abren entradas nuevas. Nunca bloquea cierres.
    ws_stale_after_seconds: float = Field(default=45.0, alias="WS_STALE_AFTER_SECONDS")
    # FRACCION 0-1: un TP solo se rellena al precio observado si el tick lo
    # supera por mas de esta fraccion (hueco evidente); si no, al nominal.
    tp_gap_tolerance_pct: float = Field(default=0.002, alias="TP_GAP_TOLERANCE_PCT")
    # Sin tick de un simbolo con posicion abierta durante estos segundos: alerta y
    # consulta el precio por REST. Nunca bloquea cierres.
    tick_stale_seconds: float = Field(default=60.0, alias="TICK_STALE_SECONDS")
    # Cada cuanto el monitor refresca marcas, funding, equity y estado (segundos).
    monitor_tick_seconds: float = Field(default=15.0, alias="MONITOR_TICK_SECONDS")

    # --- Criterios de paso a dinero real (informativos) ---
    min_paper_trading_days: int = Field(default=30, alias="MIN_PAPER_TRADING_DAYS")
    min_closed_trades: int = Field(default=100, alias="MIN_CLOSED_TRADES")
    min_profit_factor: float = Field(default=1.3, alias="MIN_PROFIT_FACTOR")

    # --- CoinGecko / universo dinamico (Fase 2) ---
    coingecko_api_key: str = Field(default="", alias="COINGECKO_API_KEY")
    coingecko_base_url: str = Field(
        default="https://api.coingecko.com/api/v3", alias="COINGECKO_BASE_URL"
    )
    universe_size: int = Field(default=10, alias="UNIVERSE_SIZE")
    universe_candidate_pool: int = Field(default=30, alias="UNIVERSE_CANDIDATE_POOL")
    universe_refresh_hours: float = Field(default=24.0, alias="UNIVERSE_REFRESH_HOURS")
    universe_staleness_hours: float = Field(default=48.0, alias="UNIVERSE_STALENESS_HOURS")
    universe_exclude_categories: str = Field(
        default="stablecoins,wrapped-tokens,liquid-staking-tokens",
        alias="UNIVERSE_EXCLUDE_CATEGORIES",
    )
    universe_manual_exclusions: str = Field(default="", alias="UNIVERSE_MANUAL_EXCLUSIONS")
    # FRACCION 0-1 (0.05 = 5%).
    universe_price_sanity_tolerance_pct: float = Field(
        default=0.05, alias="UNIVERSE_PRICE_SANITY_TOLERANCE_PCT"
    )

    # --- Backtesting (Fase 2) ---
    backtest_initial_capital: float = Field(default=100.0, alias="BACKTEST_INITIAL_CAPITAL")
    backtest_slippage_bps: float = Field(default=5.0, alias="BACKTEST_SLIPPAGE_BPS")
    backtest_min_trades_per_cell: int = Field(default=30, alias="BACKTEST_MIN_TRADES_PER_CELL")
    backtest_min_trades_total: int = Field(default=100, alias="BACKTEST_MIN_TRADES_TOTAL")
    backtest_min_profit_factor: float = Field(default=1.2, alias="BACKTEST_MIN_PROFIT_FACTOR")
    # Los 4 campos que siguen son FRACCION 0-1 (0.50 = 50%).
    backtest_max_drawdown_pct: float = Field(default=0.50, alias="BACKTEST_MAX_DRAWDOWN_PCT")
    backtest_concentration_limit_pct: float = Field(
        default=0.40, alias="BACKTEST_CONCENTRATION_LIMIT_PCT"
    )
    backtest_min_pct_symbols_pf_gt1: float = Field(
        default=0.50, alias="BACKTEST_MIN_PCT_SYMBOLS_PF_GT1"
    )
    backtest_min_pct_folds_positive: float = Field(
        default=0.50, alias="BACKTEST_MIN_PCT_FOLDS_POSITIVE"
    )
    backtest_stress_fee_multiplier: float = Field(
        default=2.0, alias="BACKTEST_STRESS_FEE_MULTIPLIER"
    )
    backtest_stress_slippage_multiplier: float = Field(
        default=2.0, alias="BACKTEST_STRESS_SLIPPAGE_MULTIPLIER"
    )
    # FRACCION 0-1.
    backtest_oos_split_pct: float = Field(default=0.30, alias="BACKTEST_OOS_SPLIT_PCT")
    backtest_walk_forward_fold_months: int = Field(
        default=6, alias="BACKTEST_WALK_FORWARD_FOLD_MONTHS"
    )
    backtest_walk_forward_step_months: int = Field(
        default=2, alias="BACKTEST_WALK_FORWARD_STEP_MONTHS"
    )
    backtest_control_symbols: str = Field(
        default="BTCUSDT,ETHUSDT", alias="BACKTEST_CONTROL_SYMBOLS"
    )
    # Tope de riesgo por operacion: si el SL configurado de una estrategia
    # perderia mas de este % del margen (a 10x), la entrada se omite (no se
    # fuerza un SL mas ajustado) y se registra como "omitida por riesgo".
    # PORCENTAJE 0-100 (50.0 = 50%, NO 0.50) -- misma escala que
    # `live_sl_margin_cap_pct`, ver la nota de unidades al inicio del archivo.
    max_sl_margin_loss_pct: float = Field(default=50.0, alias="MAX_SL_MARGIN_LOSS_PCT")
    # SL/TP de respaldo, en FRACCION 0-1 de movimiento de precio (0.05 =
    # 5%, NO 50.0 -- a pesar del nombre "_pct", esta escala es distinta de
    # `max_sl_margin_loss_pct` arriba) para estrategias que no implementan
    # stop_price/take_profit_price (None).
    backtest_fallback_sl_pct: float = Field(default=0.05, alias="BACKTEST_FALLBACK_SL_PCT")
    backtest_fallback_tp_pct: float = Field(default=0.10, alias="BACKTEST_FALLBACK_TP_PCT")
    # Numero de corridas de `simulate_portfolio_monte_carlo` (barajando el
    # desempate de entry_time con una semilla fija) por estrategia --
    # informativo, no participa en los criterios de descarte.
    backtest_portfolio_sim_runs: int = Field(default=200, alias="BACKTEST_PORTFOLIO_SIM_RUNS")
    # Fecha final FIJA (UTC) del backtest "oficial" -- congelada para que
    # dos corridas de `scripts/run_backtest.py` den exactamente los mismos
    # numeros (antes se usaba la hora real de cada corrida, haciendo cada
    # resultado irreproducible). Ver docs/FASE2_CRITERIOS.md.
    backtest_official_end_date: str = Field(
        default="2026-10-01", alias="BACKTEST_OFFICIAL_END_DATE"
    )

    # --- Claude / Anthropic (usado desde Fase 4) ---
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    anthropic_haiku_model: str = Field(default="claude-haiku-4-5", alias="ANTHROPIC_HAIKU_MODEL")
    anthropic_sonnet_model: str = Field(default="claude-sonnet-5", alias="ANTHROPIC_SONNET_MODEL")
    llm_daily_budget_usd: float = Field(default=1.0, alias="LLM_DAILY_BUDGET_USD")
    # FRACCION 0-1 (score de confluencia, no un porcentaje de precio).
    confluence_score_threshold: float = Field(
        default=0.6, alias="CONFLUENCE_SCORE_THRESHOLD"
    )

    # --- Telegram (usado desde Fase 5) ---
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")

    # --- Frontend / API (usado desde Fase 5) ---
    auth_username: str = Field(default="", alias="AUTH_USERNAME")
    auth_password_hash: str = Field(default="", alias="AUTH_PASSWORD_HASH")
    jwt_secret: str = Field(default="", alias="JWT_SECRET")

    # --- Base de datos ---
    database_path: str = Field(default="./data/minerva.db", alias="DATABASE_PATH")

    @property
    def symbols(self) -> list[str]:
        """Lista de simbolos configurados en TRADING_SYMBOLS (separados por coma)."""
        return [s.strip().upper() for s in self.trading_symbols.split(",") if s.strip()]

    @property
    def universe_exclude_categories_list(self) -> list[str]:
        return [c.strip() for c in self.universe_exclude_categories.split(",") if c.strip()]

    @property
    def universe_manual_exclusions_list(self) -> list[str]:
        """Ids de CoinGecko (no simbolos) a excluir manualmente, ademas del
        filtro automatico por categoria -- ver docs/FASE2_PLAN.md punto 6."""
        return [c.strip().lower() for c in self.universe_manual_exclusions.split(",") if c.strip()]

    @property
    def backtest_control_symbols_list(self) -> list[str]:
        return [s.strip().upper() for s in self.backtest_control_symbols.split(",") if s.strip()]

    @property
    def real_account_eligible_strategies_list(self) -> list[str]:
        """Vacio = sin restriccion (todas las estrategias son elegibles)."""
        return [
            s.strip() for s in self.real_account_eligible_strategies.split(",") if s.strip()
        ]

    @property
    def backtest_official_end_ms(self) -> int:
        """`backtest_official_end_date` ("YYYY-MM-DD", UTC) en epoch ms."""
        from datetime import UTC, datetime

        dt = datetime.strptime(self.backtest_official_end_date, "%Y-%m-%d").replace(tzinfo=UTC)
        return int(dt.timestamp() * 1000)

    def max_margin_for_new_trade(
        self,
        current_capital: float,
        margin_committed_on_symbol: float,
        margin_committed_total: float,
    ) -> float:
        """Margen maximo permitido para una nueva operacion (ver docs/FASE0.md / plan Fase 1).

        Formula acordada: min(margen configurado, 10% del capital actual menos lo ya
        comprometido en ese simbolo, margen disponible bajo el limite de posiciones
        simultaneas). No se usa aun en Fase 1 (PaperBackend no aplica limites de riesgo
        todavia -- eso es Fase 3), pero se deja implementada para reutilizarla sin
        reabrir esta configuracion.
        """
        per_asset_cap = max(
            0.0, current_capital * self.max_capital_pct_per_asset - margin_committed_on_symbol
        )
        # Bajo el limite de N posiciones simultaneas, el margen disponible total se
        # reparte como máximo en porciones de default_margin_usdt cada una.
        remaining_slots_margin = max(
            0.0,
            self.default_margin_usdt * self.max_simultaneous_positions - margin_committed_total,
        )
        return max(0.0, min(self.default_margin_usdt, per_asset_cap, remaining_slots_margin))

    @model_validator(mode="after")
    def _validate_pct_units(self) -> Settings:
        """Falla fuerte al arrancar si un campo "_pct" quedo fuera de su
        escala declarada (ver `_FRACTION_PCT_FIELDS`/`_PERCENT_PCT_FIELDS`
        arriba) -- p.ej. `MAX_DRAWDOWN_PCT=20` (deberia ser `0.20`) o
        `LIVE_SL_MARGIN_CAP_PCT=0.5` (deberia ser `50.0`). Deliberadamente
        estricto: estos valores controlan limites de riesgo, un error de
        escala silencioso puede desactivar un limite por completo (una
        fraccion de 20.0 nunca se alcanza) o activarlo siempre (un
        porcentaje de 0.5 se cruza con cualquier operacion)."""
        for name in _FRACTION_PCT_FIELDS:
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(
                    f"{name}={value!r} debe ser una FRACCION entre 0.0 y 1.0 (p.ej. 0.20 "
                    "para 20%), no un porcentaje 0-100."
                )
        for name in _PERCENT_PCT_FIELDS:
            value = getattr(self, name)
            if not 0.0 <= value <= 100.0:
                raise ValueError(
                    f"{name}={value!r} debe ser un PORCENTAJE entre 0.0 y 100.0 (p.ej. 50.0 "
                    "para 50%), no una fraccion 0-1."
                )
        return self


settings = Settings()
