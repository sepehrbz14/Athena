"""Observer-only diagnostics for backtest runs."""
from __future__ import annotations

import csv
import json
from contextlib import contextmanager
from collections import Counter, defaultdict
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from time import perf_counter
from typing import Any


class LogLevel(str, Enum):
    OFF = "OFF"
    ERROR = "ERROR"
    WARN = "WARN"
    INFO = "INFO"
    DEBUG = "DEBUG"
    TRACE = "TRACE"


class PerformanceTimings:
    """Opt-in wall-clock timings that observe, but never direct, a run."""

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled
        self._seconds: Counter[str] = Counter()
        self._calls: Counter[str] = Counter()

    @contextmanager
    def measure(self, stage: str):
        if not self.enabled:
            yield
            return
        started = perf_counter()
        try:
            yield
        finally:
            self._seconds[stage] += perf_counter() - started
            self._calls[stage] += 1

    def summary(self) -> dict[str, dict[str, float | int]]:
        return {
            stage: {
                "seconds": self._seconds[stage],
                "calls": self._calls[stage],
                "average_ms": self._seconds[stage] * 1_000 / self._calls[stage],
            }
            for stage in sorted(self._calls)
        }


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if is_dataclass(value):
        return {key: _json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


class BacktestDiagnostics:
    """Collects facts about a run without participating in any decisions."""

    def __init__(self, level: LogLevel | str = LogLevel.INFO, *, example_limit: int = 5, capture_all: bool = False) -> None:
        configured_level = LogLevel(level)
        # Diagnostic mode promises a complete artifact set even if callers left
        # the normal console-oriented level at OFF.
        self.level = LogLevel.DEBUG if capture_all and configured_level is LogLevel.OFF else configured_level
        self.example_limit = example_limit
        self.capture_all = capture_all
        self.events: list[dict[str, Any]] = []
        self.rejections: list[dict[str, Any]] = []
        self.signals: list[dict[str, Any]] = []
        self.orders: list[dict[str, Any]] = []
        self.trades: list[dict[str, Any]] = []
        self.rejection_counts: Counter[str] = Counter()
        self.rejection_examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.strategy_signals: Counter[str] = Counter()
        self.strategy_entries: Counter[str] = Counter()
        self.counts: Counter[str] = Counter()

    def record(self, event_type: str, **data: Any) -> dict[str, Any]:
        event = {"event_type": event_type, **_json_value(data)}
        if self.level is not LogLevel.OFF and (self.capture_all or self.level in (LogLevel.DEBUG, LogLevel.TRACE) or event_type in {"ENTRY_REJECTED", "ENTRY_SKIPPED_KILL_SWITCH", "KILL_SWITCH_ACTIVATED", "KILL_SWITCH_WOULD_TRIGGER", "ENTRY_ACCEPTED", "POSITION_CLOSED"}):
            self.events.append(event)
        return event

    def signal(self, **data: Any) -> None:
        self.counts["signals_generated"] += 1
        self.strategy_signals[str(data["strategy"])] += 1
        event = self.record("SIGNAL", **data)
        if self.capture_all or self.level in (LogLevel.DEBUG, LogLevel.TRACE):
            self.signals.append(event)

    def rejection(self, reason: Enum | str, **data: Any) -> None:
        key = reason.value if isinstance(reason, Enum) else str(reason)
        self.counts["entry_attempts"] += 1
        self.counts["entries_rejected"] += 1
        self.rejection_counts[key] += 1
        event = self.record("ENTRY_REJECTED", rejection_reason=key, **data)
        self.rejections.append(event)
        if len(self.rejection_examples[key]) < self.example_limit:
            self.rejection_examples[key].append(event)

    def entry_accepted(self, **data: Any) -> None:
        self.counts["entry_attempts"] += 1
        self.counts["entries_accepted"] += 1
        self.strategy_entries[str(data["strategy"])] += 1
        event = self.record("ENTRY_ACCEPTED", **data)
        self.orders.append(event)

    def entry_skipped_kill_switch(self, **data: Any) -> None:
        """A safety gate is not an order/risk-engine rejection."""
        self.counts["entry_attempts"] += 1
        self.counts["entries_skipped_kill_switch"] += 1
        event = self.record("ENTRY_SKIPPED_KILL_SWITCH", **data)
        self.orders.append(event)

    def order(self, event_type: str, **data: Any) -> None:
        event = self.record(event_type, **data)
        self.orders.append(event)
        if event_type == "PARTIAL_FILL":
            self.counts["partial_fills"] += 1
        elif event_type == "ORDER_REJECTED":
            self.counts["rejected_orders"] += 1

    def trade(self, event_type: str, **data: Any) -> None:
        event = self.record(event_type, **data)
        self.trades.append(event)
        if event_type == "POSITION_OPENED":
            self.counts["trades_opened"] += 1
        elif event_type == "POSITION_CLOSED":
            self.counts["trades_closed"] += 1

    def summary(self, *, trades: list[Any] | tuple[Any, ...] = (), initial_equity: float = 0.0, **final: Any) -> dict[str, Any]:
        return {
            "candles_processed": self.counts["candles_processed"],
            "signals_generated": self.counts["signals_generated"],
            "entry_attempts": self.counts["entry_attempts"],
            "entries_accepted": self.counts["entries_accepted"],
            "entries_rejected": self.counts["entries_rejected"],
            "entries_skipped_kill_switch": self.counts["entries_skipped_kill_switch"],
            "rejection_reasons": dict(self.rejection_counts),
            "rejection_examples": dict(self.rejection_examples),
            "trades_opened": self.counts["trades_opened"],
            "trades_closed": self.counts["trades_closed"],
            "partial_fills": self.counts["partial_fills"],
            "rejected_orders": self.counts["rejected_orders"],
            "kill_switch_activations": self.counts["kill_switch_activations"],
            "kill_switch_would_trigger": self.counts["kill_switch_would_trigger"],
            "strategy_performance": self._strategy_performance(trades, initial_equity),
            **_json_value(final),
        }

    def _strategy_performance(self, trades: list[Any] | tuple[Any, ...], initial_equity: float) -> dict[str, dict[str, float | int | None]]:
        """Attribute losses in the worst realized drawdown window by strategy."""
        equity = peak = initial_equity
        peak_index = 0
        worst_start = worst_end = 0
        worst_drawdown = 0.0
        for index, trade in enumerate(trades):
            equity += trade.net_pnl
            if equity > peak:
                peak = equity
                peak_index = index + 1
            drawdown = peak - equity
            if drawdown > worst_drawdown:
                worst_drawdown = drawdown
                worst_start, worst_end = peak_index, index + 1
        drawdown_losses: Counter[str] = Counter()
        for trade in trades[worst_start:worst_end]:
            drawdown_losses[trade.strategy] += max(-trade.net_pnl, 0.0)

        by_strategy: dict[str, dict[str, float | int | None]] = {}
        strategies = set(self.strategy_signals) | set(self.strategy_entries) | {trade.strategy for trade in trades}
        for strategy in sorted(strategies):
            strategy_trades = [trade for trade in trades if trade.strategy == strategy]
            gross_profit = sum(trade.net_pnl for trade in strategy_trades if trade.net_pnl > 0)
            gross_loss = -sum(trade.net_pnl for trade in strategy_trades if trade.net_pnl < 0)
            net_pnl = sum(trade.net_pnl for trade in strategy_trades)
            by_strategy[strategy] = {
                "signals": self.strategy_signals[strategy],
                "entries": self.strategy_entries[strategy],
                "wins": sum(trade.net_pnl > 0 for trade in strategy_trades),
                "losses": sum(trade.net_pnl < 0 for trade in strategy_trades),
                "net_pnl": net_pnl,
                "expectancy": net_pnl / len(strategy_trades) if strategy_trades else None,
                "profit_factor": gross_profit / gross_loss if gross_loss else None,
                "drawdown_contribution": drawdown_losses[strategy],
            }
        return by_strategy

    def export(self, directory: str | Path, summary: dict[str, Any]) -> None:
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        (target / "backtest_summary.json").write_text(json.dumps(_json_value(summary), indent=2), encoding="utf-8")
        self._write_csv(target / "rejections.csv", self.rejections)
        self._write_csv(target / "signals.csv", self.signals)
        self._write_csv(target / "orders.csv", self.orders)
        self._write_csv(target / "trades.csv", self.trades)
        with (target / "events.jsonl").open("w", encoding="utf-8") as handle:
            for event in self.events:
                handle.write(json.dumps(event, default=_json_value) + "\n")

    @staticmethod
    def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
        fields = sorted({key for row in rows for key in row})
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: json.dumps(value) if isinstance(value, (dict, list)) else value for key, value in row.items()})
