from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from .indicators import atr, ema, percentage_return, standard_deviation
from .models import Candle


class MarketRegime(str, Enum):
    BULL_TREND = "bull_trend"
    BEAR_TREND = "bear_trend"
    RANGE = "range"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    NEWS_SHOCK = "news_or_event_shock"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RegimeAssessment:
    primary: MarketRegime
    trend_strength: float
    volatility_pct: float
    momentum_pct: float
    detail: str


@dataclass(frozen=True, slots=True)
class RegimeConfig:
    fast_ema: int = 20
    slow_ema: int = 50
    atr_period: int = 14
    high_volatility_pct: float = 0.035
    low_volatility_pct: float = 0.008
    trend_threshold_atr: float = 1.25
    shock_atr_multiple: float = 3.0


class RegimeClassifier:
    """Classifies context from a higher timeframe, never a future candle."""

    def __init__(self, config: RegimeConfig | None = None) -> None:
        self.config = config or RegimeConfig()

    def assess(self, candles: Sequence[Candle]) -> RegimeAssessment:
        required = max(self.config.slow_ema, self.config.atr_period + 1) + 1
        if len(candles) < required:
            return RegimeAssessment(MarketRegime.UNKNOWN, 0.0, 0.0, 0.0, "insufficient higher-timeframe history")

        closes = [candle.close for candle in candles]
        fast = ema(closes, self.config.fast_ema)
        slow = ema(closes, self.config.slow_ema)
        average_true_range = atr(candles, self.config.atr_period)
        momentum = percentage_return(closes, self.config.fast_ema) or 0.0
        if fast is None or slow is None or average_true_range is None:
            return RegimeAssessment(MarketRegime.UNKNOWN, 0.0, 0.0, momentum, "indicators unavailable")

        return self.assess_precomputed(
            candle_count=len(candles),
            latest_close=closes[-1],
            previous_close=closes[-2],
            fast_ema=fast,
            slow_ema=slow,
            average_true_range=average_true_range,
            momentum=momentum,
        )

    def assess_precomputed(
        self,
        *,
        candle_count: int,
        latest_close: float,
        previous_close: float,
        fast_ema: float | None,
        slow_ema: float | None,
        average_true_range: float | None,
        momentum: float | None,
    ) -> RegimeAssessment:
        """Classify cached indicator values using exactly ``assess``'s rules."""
        required = max(self.config.slow_ema, self.config.atr_period + 1) + 1
        if candle_count < required:
            return RegimeAssessment(MarketRegime.UNKNOWN, 0.0, 0.0, 0.0, "insufficient higher-timeframe history")
        momentum = momentum or 0.0
        if fast_ema is None or slow_ema is None or average_true_range is None:
            return RegimeAssessment(MarketRegime.UNKNOWN, 0.0, 0.0, momentum, "indicators unavailable")

        volatility_pct = average_true_range / latest_close
        trend_strength = (fast_ema - slow_ema) / average_true_range if average_true_range else 0.0
        latest_move_in_atr = abs(latest_close - previous_close) / average_true_range
        if latest_move_in_atr >= self.config.shock_atr_multiple:
            return RegimeAssessment(
                MarketRegime.NEWS_SHOCK,
                trend_strength,
                volatility_pct,
                momentum,
                "event/shock proxy: latest higher-timeframe move is unusually large versus ATR",
            )
        if volatility_pct >= self.config.high_volatility_pct:
            return RegimeAssessment(MarketRegime.HIGH_VOLATILITY, trend_strength, volatility_pct, momentum, "ATR is unusually high")
        if volatility_pct <= self.config.low_volatility_pct:
            return RegimeAssessment(MarketRegime.LOW_VOLATILITY, trend_strength, volatility_pct, momentum, "ATR is unusually low")
        if trend_strength >= self.config.trend_threshold_atr and momentum > 0:
            return RegimeAssessment(MarketRegime.BULL_TREND, trend_strength, volatility_pct, momentum, "fast EMA above slow EMA")
        if trend_strength <= -self.config.trend_threshold_atr and momentum < 0:
            return RegimeAssessment(MarketRegime.BEAR_TREND, trend_strength, volatility_pct, momentum, "fast EMA below slow EMA")
        return RegimeAssessment(MarketRegime.RANGE, trend_strength, volatility_pct, momentum, "no persistent directional trend")


def realized_volatility(candles: Sequence[Candle], period: int = 20) -> float | None:
    """Close-to-close standard deviation, expressed as a decimal fraction."""
    if len(candles) < period + 1:
        return None
    returns = [b.close / a.close - 1 for a, b in zip(candles[-(period + 1):-1], candles[-period:])]
    return standard_deviation(returns, period)
