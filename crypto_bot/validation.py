from __future__ import annotations

from dataclasses import dataclass, field
from random import Random
from statistics import median
from typing import Callable, Generic, Sequence, TypeVar

from .models import BacktestResult, Candle, Trade


ParameterSet = TypeVar("ParameterSet")


@dataclass(frozen=True, slots=True)
class WalkForwardSlice(Generic[ParameterSet]):
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    parameters: ParameterSet
    test_result: BacktestResult


@dataclass(frozen=True, slots=True)
class WalkForwardResult(Generic[ParameterSet]):
    slices: tuple[WalkForwardSlice[ParameterSet], ...]

    @property
    def combined_net_pnl(self) -> float:
        return sum(item.test_result.net_pnl for item in self.slices)

    @property
    def profitable_slice_fraction(self) -> float:
        return sum(item.test_result.net_pnl > 0 for item in self.slices) / len(self.slices) if self.slices else 0.0


def walk_forward(
    candles: Sequence[Candle],
    *,
    train_bars: int,
    test_bars: int,
    step_bars: int,
    optimize: Callable[[Sequence[Candle]], ParameterSet],
    evaluate: Callable[[Sequence[Candle], ParameterSet], BacktestResult],
) -> WalkForwardResult[ParameterSet]:
    """Repeatedly optimize only on historical training bars, then test unseen bars.

    `optimize` never receives the corresponding test period. Reserve an entirely
    untouched final dataset outside this function for the last acceptance check.
    """
    if min(train_bars, test_bars, step_bars) <= 0:
        raise ValueError("walk-forward windows must be positive")
    slices: list[WalkForwardSlice[ParameterSet]] = []
    train_start = 0
    while train_start + train_bars + test_bars <= len(candles):
        train_end = train_start + train_bars
        test_end = train_end + test_bars
        parameters = optimize(candles[train_start:train_end])
        result = evaluate(candles[train_end:test_end], parameters)
        slices.append(WalkForwardSlice(train_start, train_end, train_end, test_end, parameters, result))
        train_start += step_bars
    return WalkForwardResult(tuple(slices))


@dataclass(frozen=True, slots=True)
class MonteCarloResult:
    iterations: int
    median_final_equity: float
    final_equity_p05: float
    final_equity_p95: float
    median_max_drawdown_pct: float
    max_drawdown_p95: float
    probability_of_loss: float
    run_diagnostics: tuple["MonteCarloRunDiagnostics", ...] = ()


@dataclass(frozen=True, slots=True)
class MonteCarloRunDiagnostics:
    """Independent, reproducible facts for one Monte Carlo permutation."""

    run_id: int
    trade_ids: tuple[int | None, ...]
    final_equity: float
    max_drawdown_pct: float


def monte_carlo_trade_order(
    trades: Sequence[Trade],
    *,
    initial_equity: float,
    iterations: int = 10_000,
    seed: int | None = None,
    diagnostic_mode: bool = False,
) -> MonteCarloResult:
    """Reshuffle observed trades to estimate sequence-risk and drawdown.

    This holds the recorded trade outcomes constant: it asks how ugly the path
    could become if the same edge arrives in an unfortunate order. It does not
    prove the strategy will retain that edge in the future.
    """
    if initial_equity <= 0:
        raise ValueError("initial_equity must be positive")
    if iterations <= 0:
        raise ValueError("iterations must be positive")
    outcomes = [trade.net_pnl for trade in trades]
    if not outcomes:
        raise ValueError("at least one trade is required")
    random = Random(seed)
    endings: list[float] = []
    drawdowns: list[float] = []
    losses = 0
    run_diagnostics: list[MonteCarloRunDiagnostics] = []
    indexed_outcomes = [(trade.trade_id, trade.net_pnl) for trade in trades]
    for run_id in range(iterations):
        path = indexed_outcomes.copy()
        random.shuffle(path)
        equity = peak = initial_equity
        maximum_drawdown = 0.0
        for _, pnl in path:
            equity += pnl
            peak = max(peak, equity)
            maximum_drawdown = max(maximum_drawdown, 1 - equity / peak if peak else 1.0)
        endings.append(equity)
        drawdowns.append(maximum_drawdown)
        losses += equity < initial_equity
        if diagnostic_mode:
            run_diagnostics.append(MonteCarloRunDiagnostics(run_id, tuple(trade_id for trade_id, _ in path), equity, maximum_drawdown))
    endings.sort()
    drawdowns.sort()
    return MonteCarloResult(
        iterations=iterations,
        median_final_equity=median(endings),
        final_equity_p05=_percentile(endings, 0.05),
        final_equity_p95=_percentile(endings, 0.95),
        median_max_drawdown_pct=median(drawdowns),
        max_drawdown_p95=_percentile(drawdowns, 0.95),
        probability_of_loss=losses / iterations,
        run_diagnostics=tuple(run_diagnostics),
    )


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    index = (len(values) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    fraction = index - lower
    return values[lower] * (1 - fraction) + values[upper] * fraction
