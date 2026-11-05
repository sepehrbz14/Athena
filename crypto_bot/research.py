"""Mode-independent signal capture and causal forward outcomes."""
from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
from typing import Sequence

from .backtest import BacktestConfig, IntrabarBacktester, _IncrementalMarketData
from .models import Candle
from .strategies import StrategyEnsemble


def capture_signals(candles: Sequence[Candle], *, candle_minutes: int = 15,
                    ensemble: StrategyEnsemble | None = None) -> list[dict[str, object]]:
    """Capture selected ensemble signals on every eligible bar, independent of portfolio state."""
    engine = IntrabarBacktester(BacktestConfig(candle_minutes=candle_minutes))
    ensemble = ensemble or StrategyEnsemble()
    market = _IncrementalMarketData()
    captured: list[dict[str, object]] = []
    for index, candle in enumerate(candles):
        market.update(candle)
        market.refresh_1h_features()
        market.refresh_4h_features(engine.regime_classifier)
        market.refresh_15m_features()
        decision = ensemble.decide(engine._snapshot_from_market_data(market))
        if decision.signal is None:
            continue
        sig = decision.signal
        captured.append({
            "signal_id": len(captured) + 1,
            "candle_index": index,
            "timestamp": candle.timestamp.isoformat(),
            "available_at": (candle.timestamp + timedelta(minutes=candle_minutes)).isoformat(),
            "strategy": sig.strategy,
            "direction": sig.side.value,
            "regime": market.regime.primary.value,
            "reference_price": candle.close,
            "features": asdict(market.features) if market.features else {},
            "stop_price": sig.stop_price,
            "take_profit": sig.take_profit,
            "forward_outcomes": causal_forward_outcomes(candles, index, sig.side.sign, candle.close, candle_minutes),
        })
    return captured


def causal_forward_outcomes(candles: Sequence[Candle], index: int, direction: int,
                            reference_price: float, candle_minutes: int = 15,
                            horizons: tuple[int, ...] = (1, 4, 16, 96)) -> dict[str, object]:
    """Use only candles whose open timestamp is at/after signal availability."""
    available = candles[index].timestamp + timedelta(minutes=candle_minutes)
    result: dict[str, object] = {}
    for horizon in horizons:
        later = [c for c in candles[index + 1:] if c.timestamp >= available]
        future = later[horizon - 1] if len(later) >= horizon else None
        result[str(horizon)] = None if future is None else {
            "timestamp": future.timestamp.isoformat(),
            "directional_return_pct": direction * (future.close / reference_price - 1),
        }
    return result
