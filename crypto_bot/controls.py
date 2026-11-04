from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum


class KillReason(str, Enum):
    DAILY_LOSS = "daily_loss_limit"
    MAX_DRAWDOWN = "max_drawdown_limit"
    EXCHANGE_ERROR = "exchange_api_error"
    DATA_DISAGREEMENT = "data_feed_disagreement"
    UNEXPECTED_VOLATILITY = "unexpected_volatility"


class KillSwitchMode(str, Enum):
    """Safety gates entries; research records trips without gating them."""

    SAFETY = "safety"
    OBSERVATIONAL = "observational"


@dataclass(frozen=True, slots=True)
class KillSwitchConfig:
    max_daily_loss_pct: float = 0.03
    max_drawdown_pct: float = 0.10
    max_consecutive_exchange_errors: int = 2
    max_price_feed_difference_pct: float = 0.005
    max_atr_pct: float = 0.08

    def __post_init__(self) -> None:
        if not 0 < self.max_daily_loss_pct < 1 or not 0 < self.max_drawdown_pct < 1:
            raise ValueError("loss limits must be between 0 and 1")


@dataclass(slots=True)
class KillSwitch:
    """Blocks entries upon abnormal conditions; it never forces a new trade."""

    config: KillSwitchConfig = field(default_factory=KillSwitchConfig)
    peak_equity: float | None = None
    day: date | None = None
    day_start_equity: float | None = None
    daily_locked: bool = False
    permanent_reason: KillReason | None = None
    exchange_errors: int = 0
    events: list[tuple[datetime, KillReason, str]] = field(default_factory=list)

    @property
    def is_halted(self) -> bool:
        return self.daily_locked or self.permanent_reason is not None

    def observe(self, timestamp: datetime, equity: float, atr_pct: float | None = None) -> None:
        if equity <= 0:
            self._trip_permanent(timestamp, KillReason.MAX_DRAWDOWN, "equity is non-positive")
            return
        if self.peak_equity is None:
            self.peak_equity = equity
        self.peak_equity = max(self.peak_equity, equity)
        if self.day != timestamp.date():
            self.day = timestamp.date()
            self.day_start_equity = equity
            self.daily_locked = False
        if self.day_start_equity and equity <= self.day_start_equity * (1 - self.config.max_daily_loss_pct):
            self._trip_daily(timestamp, KillReason.DAILY_LOSS, "daily loss threshold exceeded")
        if equity <= self.peak_equity * (1 - self.config.max_drawdown_pct):
            self._trip_permanent(timestamp, KillReason.MAX_DRAWDOWN, "peak-to-valley drawdown threshold exceeded")
        if atr_pct is not None and atr_pct >= self.config.max_atr_pct:
            self._trip_permanent(timestamp, KillReason.UNEXPECTED_VOLATILITY, "ATR percentage exceeds configured maximum")

    def report_exchange_error(self, timestamp: datetime, message: str) -> None:
        self.exchange_errors += 1
        if self.exchange_errors >= self.config.max_consecutive_exchange_errors:
            self._trip_permanent(timestamp, KillReason.EXCHANGE_ERROR, message)

    def report_exchange_success(self) -> None:
        self.exchange_errors = 0

    def compare_prices(self, timestamp: datetime, primary: float, secondary: float) -> None:
        if primary <= 0 or secondary <= 0:
            self._trip_permanent(timestamp, KillReason.DATA_DISAGREEMENT, "non-positive data-feed price")
            return
        difference = abs(primary - secondary) / primary
        if difference >= self.config.max_price_feed_difference_pct:
            self._trip_permanent(timestamp, KillReason.DATA_DISAGREEMENT, f"price feeds differ by {difference:.2%}")

    def allow_new_trade(self) -> tuple[bool, str]:
        if self.permanent_reason:
            return False, f"kill switch: {self.permanent_reason.value}"
        if self.daily_locked:
            return False, "kill switch: daily_loss_limit"
        return True, "ok"

    def _trip_daily(self, timestamp: datetime, reason: KillReason, message: str) -> None:
        if not self.daily_locked:
            self.daily_locked = True
            self.events.append((timestamp, reason, message))

    def _trip_permanent(self, timestamp: datetime, reason: KillReason, message: str) -> None:
        if self.permanent_reason is None:
            self.permanent_reason = reason
            self.events.append((timestamp, reason, message))
