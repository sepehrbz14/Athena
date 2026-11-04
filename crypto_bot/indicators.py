from __future__ import annotations

from math import sqrt
from typing import Sequence

from .models import Candle


def sma(values: Sequence[float], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema(values: Sequence[float], period: int) -> float | None:
    """EMA seeded from the first available value; returns None before `period` samples."""
    if period <= 0 or len(values) < period:
        return None
    multiplier = 2 / (period + 1)
    value = values[0]
    for sample in values[1:]:
        value = sample * multiplier + value * (1 - multiplier)
    return value


def standard_deviation(values: Sequence[float], period: int) -> float | None:
    window = values[-period:]
    if period <= 1 or len(window) < period:
        return None
    average = sum(window) / period
    return sqrt(sum((value - average) ** 2 for value in window) / period)


def atr(candles: Sequence[Candle], period: int = 14) -> float | None:
    if period <= 0 or len(candles) < period + 1:
        return None
    true_ranges = []
    for previous, current in zip(candles[-(period + 1):-1], candles[-period:]):
        true_ranges.append(max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        ))
    return sum(true_ranges) / period


def rsi(values: Sequence[float], period: int = 14) -> float | None:
    if period <= 0 or len(values) < period + 1:
        return None
    deltas = [b - a for a, b in zip(values[-(period + 1):-1], values[-period:])]
    gains = sum(max(delta, 0) for delta in deltas) / period
    losses = sum(max(-delta, 0) for delta in deltas) / period
    if losses == 0:
        return 100.0
    relative_strength = gains / losses
    return 100 - (100 / (1 + relative_strength))


def percentage_return(values: Sequence[float], period: int) -> float | None:
    if period <= 0 or len(values) <= period or values[-period - 1] == 0:
        return None
    return values[-1] / values[-period - 1] - 1


def resample(candles: Sequence[Candle], minutes: int) -> list[Candle]:
    """Aggregate completed lower-timeframe candles without looking into the future.

    The final bucket may be incomplete, but only data known as of its last source
    candle is used. This is appropriate for context in an event-driven backtest.
    """
    if minutes <= 0:
        raise ValueError("minutes must be positive")
    buckets: dict[object, list[Candle]] = {}
    for candle in candles:
        stamp = candle.timestamp.replace(second=0, microsecond=0)
        if minutes >= 60 and minutes % 60 == 0:
            hours = minutes // 60
            key = stamp.replace(hour=stamp.hour - (stamp.hour % hours), minute=0)
        else:
            bucket_minute = stamp.minute - (stamp.minute % minutes)
            key = stamp.replace(minute=bucket_minute)
        buckets.setdefault(key, []).append(candle)
    aggregate: list[Candle] = []
    for timestamp, group in buckets.items():
        aggregate.append(Candle(
            timestamp=timestamp,
            open=group[0].open,
            high=max(item.high for item in group),
            low=min(item.low for item in group),
            close=group[-1].close,
            volume=sum(item.volume or 0.0 for item in group),
        ))
    return aggregate
