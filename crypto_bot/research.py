"""Independent, causal strategy signal research capture (no portfolio simulation)."""
from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from time import perf_counter
from typing import Sequence

from .backtest import BacktestConfig, IntrabarBacktester, _IncrementalMarketData
from .models import Candle
from .strategies import StrategyEnsemble


DEFAULT_HORIZONS = (1, 4, 16, 96)


def capture_signals(candles: Sequence[Candle], *, candle_minutes: int = 15,
                    ensemble: StrategyEnsemble | None = None,
                    horizons: tuple[int, ...] = DEFAULT_HORIZONS) -> list[dict[str, object]]:
    """Capture every component output on each same-bar snapshot, regardless of portfolio state.

    On a timestamp discontinuity, indicator/resampling state is reset and features
    warm up from post-gap observations only. The discontinuity itself is retained
    as a flag on later outcomes; no price/candle is synthesized.
    """
    started = perf_counter()
    engine = IntrabarBacktester(BacktestConfig(candle_minutes=candle_minutes))
    ensemble = ensemble or StrategyEnsemble()
    market = _IncrementalMarketData()
    all_rows: list[dict[str, object]] = []
    prior: Candle | None = None
    for index, candle in enumerate(candles):
        gap = prior is not None and candle.timestamp - prior.timestamp > timedelta(minutes=candle_minutes)
        if gap:
            market.reset_after_discontinuity()
        market.update(candle)
        market.refresh_1h_features()
        market.refresh_4h_features(engine.regime_classifier)
        market.refresh_15m_features()
        snapshot = engine._snapshot_from_market_data(market)
        raw = ensemble.raw_signals(snapshot)
        selected = ensemble.select_from_raw(snapshot, raw)
        available = candle.timestamp + timedelta(minutes=candle_minutes)
        for strategy, signal in raw.items():
            if signal is None:
                continue
            all_rows.append({
                "signal_id": len(all_rows) + 1,
                "candle_index": index,
                "timestamp": candle.timestamp.isoformat(),
                "available_at": available.isoformat(),
                "strategy": strategy,
                "direction": signal.side.value,
                "regime": market.regime.primary.value,
                "regime_allowed": market.regime.primary in next(s.allowed_regimes for s in ensemble.strategies if s.name == strategy),
                "ensemble_selected": selected.signal is not None and selected.signal.strategy == strategy and selected.signal.side == signal.side,
                "reference_price": candle.close,
                "features": asdict(market.features) if market.features else {},
                "stop_price": signal.stop_price,
                "take_profit": signal.take_profit,
                "forward_outcomes": causal_forward_outcomes(candles, index, signal.side.sign,
                                                            candle.close, candle_minutes, horizons),
                "gap_before_signal": gap,
            })
        prior = candle
    # Runtime is exposed by the caller for reproducibility without contaminating rows.
    capture_signals.last_runtime_seconds = perf_counter() - started
    return all_rows


def causal_forward_outcomes(candles: Sequence[Candle], index: int, direction: int,
                            reference_price: float, candle_minutes: int = 15,
                            horizons: tuple[int, ...] = DEFAULT_HORIZONS) -> dict[str, object]:
    """Calculate returns and excursions after signal availability, censoring at gaps.

    Outcomes use bars strictly after the signal candle. Horizon N means N observed,
    contiguous bars; if a timestamp gap occurs before N bars, outcome is censored.
    """
    result: dict[str, object] = {}
    available = candles[index].timestamp + timedelta(minutes=candle_minutes)
    later: list[Candle] = []
    gap = False
    prev = candles[index]
    max_horizon = max(horizons, default=0)
    next_index = index + 1
    while next_index < len(candles):
        bar = candles[next_index]
        next_index += 1
        if bar.timestamp < available:
            continue
        if bar.timestamp - prev.timestamp != timedelta(minutes=candle_minutes):
            gap = True
            break
        later.append(bar)
        prev = bar
        if len(later) >= max_horizon:
            break
    for horizon in horizons:
        sample = later[:horizon]
        complete = len(sample) == horizon
        result[str(horizon)] = {
            "status": "complete" if complete else ("censored_gap" if gap else "insufficient_future_data"),
            "bars_observed": len(sample),
            "timestamp": sample[-1].timestamp.isoformat() if sample else None,
            "direction_adjusted_return_pct": direction * (sample[-1].close / reference_price - 1) if complete else None,
            "mfe_pct": max((direction * ((b.high if direction > 0 else b.low) / reference_price - 1) for b in sample), default=None),
            "mae_pct": min((direction * ((b.low if direction > 0 else b.high) / reference_price - 1) for b in sample), default=None),
        }
    return result


def export_signal_dataset(rows: Sequence[dict[str, object]], path: str | Path) -> None:
    """Write reproducible flat CSV; nested JSON-valued columns remain machine readable."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ["signal_id", "candle_index", "timestamp", "available_at", "strategy", "direction",
               "regime", "regime_allowed", "ensemble_selected", "reference_price", "features",
               "stop_price", "take_profit", "forward_outcomes", "gap_before_signal"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, separators=(",", ":")) if isinstance(v, (dict, list)) else v for k, v in row.items()})
