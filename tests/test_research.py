import unittest
from datetime import datetime, timedelta, timezone

from crypto_bot.backtest import BacktestConfig, IntrabarBacktester
from crypto_bot.controls import KillSwitchMode
from crypto_bot.models import Candle, Position, Side
from crypto_bot.research import causal_forward_outcomes
from crypto_bot.research import capture_signals
from crypto_bot.regimes import MarketRegime
from crypto_bot.strategies import StrategyEnsemble
from crypto_bot.models import Signal


class ResearchInfrastructureTests(unittest.TestCase):
    def test_raw_strategy_outputs_are_captured_independently(self):
        class Always:
            allowed_regimes = set(MarketRegime)
            def __init__(self, name): self.name = name
            def signal(self, market): return Signal(Side.LONG, market.candles_15m[-1].close * .9, None, .8, self.name, 'test')
        t0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
        bars = [Candle(t0+timedelta(minutes=15*i),100,101,99,100,1) for i in range(3)]
        names = ['breakout','mean_reversion','momentum','trend_following']
        ens = StrategyEnsemble([Always(n) for n in names])
        rows = capture_signals(bars, ensemble=ens)
        self.assertEqual({r['strategy'] for r in rows}, set(names))
        self.assertEqual(len(rows), 12)
        self.assertTrue(any(r['ensemble_selected'] for r in rows))

    def test_signal_capture_is_portfolio_mode_independent(self):
        t0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
        bars = [Candle(t0+timedelta(minutes=15*i),100,101,99,100,1) for i in range(80)]
        self.assertEqual(capture_signals(bars), capture_signals(bars))

    def test_capture_records_signals_without_portfolio_state(self):
        class Always:
            name='always'; allowed_regimes=set(MarketRegime)
            def signal(self, market): return Signal(Side.LONG, 90, None, .8, self.name, 'test')
        t0=datetime(2025,1,1,tzinfo=timezone.utc)
        bars=[Candle(t0+timedelta(minutes=15*i),100,101,99,100,1) for i in range(6)]
        rows=capture_signals(bars,ensemble=StrategyEnsemble([Always()]))
        # Every candle is captured, including bars conceptually inside an open
        # trade or after a portfolio halt; portfolio state is not an input.
        self.assertEqual([r['candle_index'] for r in rows],list(range(len(bars))))

    def test_future_changes_do_not_change_past_signal_features(self):
        class Always:
            name='always'; allowed_regimes=set(MarketRegime)
            def signal(self, market): return Signal(Side.LONG, 90, None, .8, self.name, 'test')
        t0=datetime(2025,1,1,tzinfo=timezone.utc)
        bars=[Candle(t0+timedelta(minutes=15*i),100+i,101+i,99+i,100+i,1) for i in range(10)]
        changed=list(bars); changed[-1]=Candle(changed[-1].timestamp,300,301,299,300,1)
        ens=StrategyEnsemble([Always()])
        a=capture_signals(bars,ensemble=ens); b=capture_signals(changed,ensemble=ens)
        self.assertEqual(a[0]['features'],b[0]['features'])
        self.assertEqual(a[0]['reference_price'],b[0]['reference_price'])

    def test_gap_resets_feature_warmup_state(self):
        from crypto_bot.backtest import _IncrementalMarketData
        state=_IncrementalMarketData()
        t0=datetime(2025,1,1,tzinfo=timezone.utc)
        state.update(Candle(t0,100,101,99,100,1))
        state.reset_after_discontinuity()
        self.assertEqual(state.candles_15m,[])
        self.assertEqual(state.candles_1h,[])
        self.assertEqual(state.candles_4h,[])

    def test_forward_outcomes_only_use_bars_after_signal_available(self):
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        bars = [Candle(start + timedelta(minutes=15*i), 100+i, 101+i, 99+i, 100+i, 1) for i in range(4)]
        out = causal_forward_outcomes(bars, 0, 1, bars[0].close)
        self.assertEqual(out['1']['timestamp'], bars[1].timestamp.isoformat())
        self.assertAlmostEqual(out['1']['direction_adjusted_return_pct'], .01)

    def test_funding_uses_elapsed_time_across_missing_candles(self):
        t0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
        config = BacktestConfig(funding_rate_per_8h=.001)
        engine = IntrabarBacktester(config)
        pos = Position(Side.LONG, 1, 100, 90, None, t0, 'test', 0)
        after_gap = Candle(t0 + timedelta(hours=24), 100, 100, 100, 100, 1)
        # 24h between candle labels plus the final 15m candle accrual.
        self.assertAlmostEqual(engine._apply_funding(pos, after_gap), .303125)

    def test_forward_excursions_and_gap_censoring(self):
        t0=datetime(2025,1,1,tzinfo=timezone.utc)
        bars=[Candle(t0,100,100,100,100,1),Candle(t0+timedelta(minutes=15),100,110,95,105,1),
              Candle(t0+timedelta(minutes=45),105,120,90,115,1)]
        out=causal_forward_outcomes(bars,0,1,100,horizons=(1,2))
        self.assertEqual(out['1']['status'],'complete')
        self.assertAlmostEqual(out['1']['mfe_pct'],.10)
        self.assertAlmostEqual(out['1']['mae_pct'],-.05)
        self.assertEqual(out['2']['status'],'censored_gap')


if __name__ == '__main__':
    unittest.main()
