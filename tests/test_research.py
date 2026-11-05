import unittest
from datetime import datetime, timedelta, timezone

from crypto_bot.backtest import BacktestConfig, IntrabarBacktester
from crypto_bot.controls import KillSwitchMode
from crypto_bot.models import Candle, Position, Side
from crypto_bot.research import causal_forward_outcomes


class ResearchInfrastructureTests(unittest.TestCase):
    def test_forward_outcomes_only_use_bars_after_signal_available(self):
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        bars = [Candle(start + timedelta(minutes=15*i), 100+i, 101+i, 99+i, 100+i, 1) for i in range(4)]
        out = causal_forward_outcomes(bars, 0, 1, bars[0].close)
        self.assertEqual(out['1']['timestamp'], bars[1].timestamp.isoformat())
        self.assertAlmostEqual(out['1']['directional_return_pct'], .01)

    def test_funding_uses_elapsed_time_across_missing_candles(self):
        t0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
        config = BacktestConfig(funding_rate_per_8h=.001)
        engine = IntrabarBacktester(config)
        pos = Position(Side.LONG, 1, 100, 90, None, t0, 'test', 0)
        after_gap = Candle(t0 + timedelta(hours=24), 100, 100, 100, 100, 1)
        # 24h between candle labels plus the final 15m candle accrual.
        self.assertAlmostEqual(engine._apply_funding(pos, after_gap), .303125)


if __name__ == '__main__':
    unittest.main()
