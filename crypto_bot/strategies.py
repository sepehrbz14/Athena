from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from .indicators import atr, ema, percentage_return, sma, standard_deviation
from .models import Candle, Side, Signal
from .regimes import MarketRegime, RegimeAssessment


@dataclass(frozen=True, slots=True)
class MarketFeatures:
    """Cached values used by the built-in strategies at one candle close.

    They are mathematically identical to the indicators below, but avoid each
    strategy rebuilding the complete price history on every 15m candle.
    Third-party strategies can continue using the candle sequences unchanged.
    """

    execution_atr: float | None
    trend_fast_1h: float | None
    trend_slow_1h: float | None
    previous_high_15m: float | None
    previous_low_15m: float | None
    baseline_volume_15m: float | None
    momentum_15m: float | None
    mean_15m: float | None
    deviation_15m: float | None


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """Multi-timeframe data available at one decision point.

    Four-hour candles establish regime/context, one-hour candles describe
    structure, and fifteen-minute candles supply execution timing.
    """

    candles_4h: Sequence[Candle]
    candles_1h: Sequence[Candle]
    candles_15m: Sequence[Candle]
    regime: RegimeAssessment
    features: MarketFeatures | None = None


class Strategy(Protocol):
    name: str
    allowed_regimes: set[MarketRegime]

    def signal(self, market: MarketSnapshot) -> Signal | None:
        ...


def _stop_and_target(side: Side, entry: float, average_true_range: float, multiplier: float = 2.0) -> tuple[float, float]:
    distance = average_true_range * multiplier
    if side is Side.LONG:
        return entry - distance, entry + distance * 1.5
    return entry + distance, entry - distance * 1.5


class TrendFollower:
    name = "trend_following"
    allowed_regimes = {MarketRegime.BULL_TREND, MarketRegime.BEAR_TREND}

    def signal(self, market: MarketSnapshot) -> Signal | None:
        execution = market.candles_15m
        structure = market.candles_1h
        if len(execution) < 22 or len(structure) < 30:
            return None
        features = market.features
        if features is None:
            closes_1h = [c.close for c in structure]
            trend_fast, trend_slow = ema(closes_1h, 20), ema(closes_1h, 50)
            execution_atr = atr(execution, 14)
            previous_high = max(c.high for c in execution[-21:-1])
            previous_low = min(c.low for c in execution[-21:-1])
        else:
            trend_fast, trend_slow = features.trend_fast_1h, features.trend_slow_1h
            execution_atr = features.execution_atr
            previous_high, previous_low = features.previous_high_15m, features.previous_low_15m
        if trend_fast is None or trend_slow is None or execution_atr is None:
            return None
        if previous_high is None or previous_low is None:
            return None
        entry = execution[-1].close
        if market.regime.primary is MarketRegime.BULL_TREND and trend_fast > trend_slow and entry > previous_high:
            stop, target = _stop_and_target(Side.LONG, entry, execution_atr)
            return Signal(Side.LONG, stop, target, 0.75, self.name, "4H bull regime, 1H structure up, 15m breakout")
        if market.regime.primary is MarketRegime.BEAR_TREND and trend_fast < trend_slow and entry < previous_low:
            stop, target = _stop_and_target(Side.SHORT, entry, execution_atr)
            return Signal(Side.SHORT, stop, target, 0.75, self.name, "4H bear regime, 1H structure down, 15m breakdown")
        return None


class Breakout:
    name = "breakout"
    allowed_regimes = {MarketRegime.BULL_TREND, MarketRegime.BEAR_TREND, MarketRegime.HIGH_VOLATILITY}

    def signal(self, market: MarketSnapshot) -> Signal | None:
        candles = market.candles_15m
        if len(candles) < 22:
            return None
        features = market.features
        execution_atr = features.execution_atr if features else atr(candles, 14)
        if execution_atr is None:
            return None
        current = candles[-1]
        baseline_volume = features.baseline_volume_15m if features else sma([c.volume or 0 for c in candles[:-1]], 20)
        volume_confirmed = baseline_volume is not None and (current.volume or 0) >= baseline_volume * 1.2
        if not volume_confirmed:
            return None
        high = features.previous_high_15m if features else max(c.high for c in candles[-21:-1])
        low = features.previous_low_15m if features else min(c.low for c in candles[-21:-1])
        if high is None or low is None:
            return None
        if current.close > high:
            stop, target = _stop_and_target(Side.LONG, current.close, execution_atr, 1.75)
            return Signal(Side.LONG, stop, target, 0.7, self.name, "volume-confirmed 15m range breakout")
        if current.close < low:
            stop, target = _stop_and_target(Side.SHORT, current.close, execution_atr, 1.75)
            return Signal(Side.SHORT, stop, target, 0.7, self.name, "volume-confirmed 15m range breakdown")
        return None


class Momentum:
    name = "momentum"
    allowed_regimes = {MarketRegime.BULL_TREND, MarketRegime.BEAR_TREND}

    def signal(self, market: MarketSnapshot) -> Signal | None:
        candles = market.candles_15m
        if len(candles) < 30:
            return None
        features = market.features
        change = features.momentum_15m if features else percentage_return([c.close for c in candles], 12)
        execution_atr = features.execution_atr if features else atr(candles, 14)
        if change is None or execution_atr is None:
            return None
        if market.regime.primary is MarketRegime.BULL_TREND and change > 0.012:
            stop, target = _stop_and_target(Side.LONG, candles[-1].close, execution_atr, 1.8)
            return Signal(Side.LONG, stop, target, min(0.9, 0.55 + change * 10), self.name, "trend-aligned 12-bar momentum")
        if market.regime.primary is MarketRegime.BEAR_TREND and change < -0.012:
            stop, target = _stop_and_target(Side.SHORT, candles[-1].close, execution_atr, 1.8)
            return Signal(Side.SHORT, stop, target, min(0.9, 0.55 + abs(change) * 10), self.name, "trend-aligned 12-bar momentum")
        return None


class MeanReversion:
    name = "mean_reversion"
    allowed_regimes = {MarketRegime.RANGE, MarketRegime.LOW_VOLATILITY}

    def signal(self, market: MarketSnapshot) -> Signal | None:
        candles = market.candles_15m
        if len(candles) < 30:
            return None
        features = market.features
        if features is None:
            closes = [c.close for c in candles]
            mean = sma(closes, 20)
            deviation = standard_deviation(closes, 20)
            execution_atr = atr(candles, 14)
        else:
            closes = ()
            mean = features.mean_15m
            deviation = features.deviation_15m
            execution_atr = features.execution_atr
        if mean is None or not deviation or execution_atr is None:
            return None
        latest_close = candles[-1].close
        z_score = (latest_close - mean) / deviation
        if z_score <= -2:
            stop, _ = _stop_and_target(Side.LONG, latest_close, execution_atr, 1.5)
            return Signal(Side.LONG, stop, mean, min(0.85, 0.55 + abs(z_score) / 10), self.name, "range lower-band mean reversion")
        if z_score >= 2:
            stop, _ = _stop_and_target(Side.SHORT, latest_close, execution_atr, 1.5)
            return Signal(Side.SHORT, stop, mean, min(0.85, 0.55 + abs(z_score) / 10), self.name, "range upper-band mean reversion")
        return None


@dataclass(frozen=True, slots=True)
class EnsembleDecision:
    signal: Signal | None
    considered: tuple[str, ...]
    rejected: tuple[str, ...]


class StrategyEnsemble:
    """Only permits strategies intended for the current detected regime.

    A consensus is required when multiple allowed strategies emit opposite
    directions. This reduces correlation-driven overtrading instead of selecting
    whichever strategy had the most attractive historic curve.
    """

    def __init__(self, strategies: Sequence[Strategy] | None = None, minimum_confidence: float = 0.6) -> None:
        self.strategies = list(strategies or [TrendFollower(), MeanReversion(), Breakout(), Momentum()])
        self.minimum_confidence = minimum_confidence

    def decide(self, market: MarketSnapshot) -> EnsembleDecision:
        return self.evaluate(market)[1]

    def evaluate(self, market: MarketSnapshot) -> tuple[dict[str, Signal | None], EnsembleDecision]:
        """Shared normal/research evaluation for identical market snapshots."""
        raw = self.raw_signals(market)
        return raw, self.select_from_raw(market, raw)

    def select_from_raw(self, market: MarketSnapshot, raw: dict[str, Signal | None]) -> EnsembleDecision:
        allowed = [strategy for strategy in self.strategies if market.regime.primary in strategy.allowed_regimes]
        rejected = tuple(strategy.name for strategy in self.strategies if strategy not in allowed)
        candidates = [raw[strategy.name] for strategy in allowed if raw[strategy.name] is not None]
        if not candidates:
            return EnsembleDecision(None, tuple(s.name for s in allowed), rejected)
        score = {
            Side.LONG: sum(signal.confidence for signal in candidates if signal.side is Side.LONG),
            Side.SHORT: sum(signal.confidence for signal in candidates if signal.side is Side.SHORT),
        }
        chosen_side = Side.LONG if score[Side.LONG] >= score[Side.SHORT] else Side.SHORT
        same_direction = [signal for signal in candidates if signal.side is chosen_side]
        chosen = max(same_direction, key=lambda signal: signal.confidence)
        consensus_confidence = score[chosen_side] / len(same_direction)
        if consensus_confidence < self.minimum_confidence:
            return EnsembleDecision(None, tuple(s.name for s in allowed), rejected)
        opposing = score[Side.SHORT if chosen_side is Side.LONG else Side.LONG]
        if opposing > score[chosen_side] * 0.8:
            return EnsembleDecision(None, tuple(s.name for s in allowed), rejected)
        return EnsembleDecision(chosen, tuple(s.name for s in allowed), rejected)

    def raw_signals(self, market: MarketSnapshot) -> dict[str, Signal | None]:
        """Evaluate every component on this snapshot, independent of regime gating."""
        return {strategy.name: strategy.signal(market) for strategy in self.strategies}
