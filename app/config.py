"""Configuracion central de Minerva (pydantic-settings).

Todas las variables se leen desde `.env` (ver `.env.example`). Algunos campos
(modelos LLM, presupuesto diario, umbral de confluencia) no se usan todavia
en Fase 1 -- se declaran ahora para no reabrir esta configuracion en Fase 4.

Ninguna credencial tiene un valor por defecto no vacio; en v1 no se usan
credenciales de Bitunix (solo endpoints publicos de mercado).
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # --- Comisiones simuladas ---
    maker_fee_pct: float = Field(default=0.0002, alias="MAKER_FEE_PCT")
    taker_fee_pct: float = Field(default=0.0006, alias="TAKER_FEE_PCT")

    # --- Capital y tamano de posicion ---
    initial_capital_usdt: float = Field(default=100.0, alias="INITIAL_CAPITAL_USDT")
    default_margin_usdt: float = Field(default=10.0, alias="DEFAULT_MARGIN_USDT")
    leverage: int = Field(default=10, alias="LEVERAGE")
    margin_mode: str = Field(default="ISOLATED", alias="MARGIN_MODE")

    # --- Gestion de riesgo (motor completo en Fase 3; valores ya fijados) ---
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

    # --- Criterios de paso a dinero real (informativos) ---
    min_paper_trading_days: int = Field(default=30, alias="MIN_PAPER_TRADING_DAYS")
    min_closed_trades: int = Field(default=100, alias="MIN_CLOSED_TRADES")
    min_profit_factor: float = Field(default=1.3, alias="MIN_PROFIT_FACTOR")

    # --- CoinGecko (usado desde Fase 2+) ---
    coingecko_api_key: str = Field(default="", alias="COINGECKO_API_KEY")
    coingecko_base_url: str = Field(
        default="https://api.coingecko.com/api/v3", alias="COINGECKO_BASE_URL"
    )

    # --- Claude / Anthropic (usado desde Fase 4) ---
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    anthropic_haiku_model: str = Field(default="claude-haiku-4-5", alias="ANTHROPIC_HAIKU_MODEL")
    anthropic_sonnet_model: str = Field(default="claude-sonnet-5", alias="ANTHROPIC_SONNET_MODEL")
    llm_daily_budget_usd: float = Field(default=1.0, alias="LLM_DAILY_BUDGET_USD")
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


settings = Settings()
