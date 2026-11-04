"""Athena: a paper-first crypto trading research toolkit.

The package deliberately does not contain exchange credentials or live-order code.
Validate a strategy with realistic costs and paper trading before considering an
independently reviewed live execution integration.
"""

from .backtest import BacktestConfig, BacktestResult, IntrabarBacktester
from .models import Candle, Side, Signal
from .risk import PositionSizer, RiskConfig

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "Candle",
    "IntrabarBacktester",
    "PositionSizer",
    "RiskConfig",
    "Side",
    "Signal",
]
