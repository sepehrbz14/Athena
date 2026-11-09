import unittest
import json
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone

from crypto_bot.backtest import BacktestConfig, IntrabarBacktester, _IncrementalMarketData, _PendingEntry
from crypto_bot.controls import KillSwitchMode
from crypto_bot.models import Candle, Position, Side
from crypto_bot.research import causal_forward_outcomes
from crypto_bot.research import capture_signals
from crypto_bot.regimes import MarketRegime
from crypto_bot.strategies import StrategyEnsemble
from crypto_bot.models import Signal
from crypto_bot.controls import KillSwitch, KillSwitchConfig


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

    def test_raw_signal_parity_normal_and_independent_capture(self):
        class Always:
            name='always'; allowed_regimes=set(MarketRegime)
            def signal(self, market): return Signal(Side.LONG, 90, 120, .73, self.name, 'parity')
        t0=datetime(2025,1,1,tzinfo=timezone.utc)
        bars=[Candle(t0+timedelta(minutes=15*i),100,101,99,100,1) for i in range(5)]
        ensemble=StrategyEnsemble([Always()])
        md=_IncrementalMarketData(); engine=IntrabarBacktester()
        md.update(bars[0]); md.refresh_1h_features(); md.refresh_4h_features(engine.regime_classifier); md.refresh_15m_features()
        normal_raw, _ = ensemble.evaluate(engine._snapshot_from_market_data(md))
        row=next(r for r in capture_signals(bars,ensemble=ensemble) if r['candle_index']==0)
        signal=normal_raw['always']
        self.assertEqual((row['timestamp'],row['strategy'],row['direction'],row['confidence'],row['stop_price'],row['take_profit']),
                         (bars[0].timestamp.isoformat(),'always',signal.side.value,signal.confidence,signal.stop_price,signal.take_profit))

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
        state=_IncrementalMarketData()
        t0=datetime(2025,1,1,tzinfo=timezone.utc)
        state.update(Candle(t0,100,101,99,100,1))
        state.reset_after_discontinuity()
        self.assertEqual(state.candles_15m,[])
        self.assertEqual(state.candles_1h,[])
        self.assertEqual(state.candles_4h,[])

    def test_portfolio_states_do_not_change_independent_capture(self):
        class Always:
            name='always'; allowed_regimes=set(MarketRegime)
            def signal(self, market): return Signal(Side.LONG,90,None,.8,self.name,'test')
        t0=datetime(2025,1,1,tzinfo=timezone.utc)
        bars=[Candle(t0+timedelta(minutes=15*i),100,101,99,100,1) for i in range(6)]
        ensemble=StrategyEnsemble([Always()]); expected=capture_signals(bars,ensemble=ensemble)
        position=Position(Side.LONG,1,100,90,None,t0,'held',0)
        pending=_PendingEntry(Signal(Side.LONG,90,None,.8,'pending','test'),1,t0,1,1)
        ks=KillSwitch(KillSwitchConfig()); ks.observe(t0,10000); ks.observe(t0+timedelta(minutes=15),8000)
        self.assertTrue(ks.is_halted); self.assertIsNotNone(position); self.assertEqual(pending.signal.strategy,'pending')
        actual=capture_signals(bars,ensemble=ensemble)
        self.assertEqual([(r['timestamp'],r['strategy'],r['direction'],r['confidence'],r['stop_price'],r['take_profit']) for r in expected],
                         [(r['timestamp'],r['strategy'],r['direction'],r['confidence'],r['stop_price'],r['take_profit']) for r in actual])

    def test_backtest_preserves_pending_entry_across_gap(self):
        class Always:
            name='always'; allowed_regimes=set(MarketRegime)
            def signal(self, market): return Signal(Side.LONG,90,None,.8,self.name,'test')
        t0=datetime(2025,1,1,tzinfo=timezone.utc)
        bars=[Candle(t0,100,101,99,100,1000),Candle(t0+timedelta(minutes=45),100,101,99,100,1000),
              Candle(t0+timedelta(hours=1),100,101,99,100,1000)]
        with tempfile.TemporaryDirectory() as tmp:
            result=IntrabarBacktester(BacktestConfig(diagnostic_mode=True,diagnostics_dir=tmp,force_close_at_end=True)).run(bars,StrategyEnsemble([Always()]))
            events=[json.loads(line) for line in (Path(tmp)/'events.jsonl').read_text().splitlines()]
        self.assertEqual(result.diagnostics['trades_opened'],1)
        self.assertTrue(any(e.get('event_type')=='DATA_DISCONTINUITY' for e in events))

    def test_pending_entry_after_multi_hour_gap_waits_for_fresh_volume(self):
        class Always:
            name='always'; allowed_regimes=set(MarketRegime)
            def signal(self, market): return Signal(Side.LONG,90,None,.8,self.name,'test')
        t0=datetime(2025,1,1,tzinfo=timezone.utc)
        bars=[Candle(t0,100,101,99,100,1000),
              Candle(t0+timedelta(hours=4),100,101,99,100,1000),
              Candle(t0+timedelta(hours=4,minutes=15),100,101,99,100,1000)]
        with tempfile.TemporaryDirectory() as tmp:
            cfg=BacktestConfig(diagnostic_mode=True,diagnostics_dir=tmp,force_close_at_end=True,max_participation_rate=.05)
            result=IntrabarBacktester(cfg).run(bars,StrategyEnsemble([Always()]))
            events=[json.loads(line) for line in (Path(tmp)/'events.jsonl').read_text().splitlines()]
        waits=[e for e in events if e.get('event_type')=='ORDER_WAITING_FOR_LIQUIDITY']
        fills=[e for e in events if e.get('event_type')=='ORDER_FILLED']
        self.assertEqual(waits[0]['liquidity_status'],'stale_volume_after_gap')
        self.assertEqual(waits[0]['prior_candle_volume'],None)
        self.assertEqual(fills[0]['timestamp'],bars[2].timestamp.isoformat())
        self.assertEqual(result.diagnostics['trades_opened'],1)

    def test_backtest_preserves_open_position_across_gap(self):
        class Always:
            name='always'; allowed_regimes=set(MarketRegime)
            def signal(self, market): return Signal(Side.LONG,90,None,.8,self.name,'test')
        t0=datetime(2025,1,1,tzinfo=timezone.utc)
        bars=[Candle(t0+timedelta(minutes=15*i),100,101,99,100,1000) for i in range(3)]
        bars.append(Candle(t0+timedelta(minutes=60),100,101,99,100,1000))
        result=IntrabarBacktester(BacktestConfig(force_close_at_end=True)).run(bars,StrategyEnsemble([Always()]))
        self.assertEqual(len(result.trades),1)
        self.assertEqual(result.trades[0].exit_reason,'end_of_test')

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

    def test_gap_metadata_tracks_warmup_and_feature_readiness(self):
        class Always:
            name='always'; allowed_regimes=set(MarketRegime)
            def signal(self, market): return Signal(Side.LONG,90,None,.8,self.name,'test')
        t0=datetime(2025,1,1,tzinfo=timezone.utc)
        bars=[Candle(t0,100,101,99,100,1),Candle(t0+timedelta(minutes=15),100,101,99,100,1),
              Candle(t0+timedelta(minutes=45),100,101,99,100,1)]
        row=capture_signals(bars,ensemble=StrategyEnsemble([Always()]))[-1]
        self.assertEqual(row['gap_id'],1)
        self.assertEqual(row['gap_missing_intervals'],1)
        self.assertEqual(row['post_gap_warmup_progress_candles'],1)
        self.assertEqual(row['gap_previous_observed_timestamp'],bars[1].timestamp.isoformat())
        self.assertEqual(row['gap_next_observed_timestamp'],bars[2].timestamp.isoformat())
        self.assertIn('regime_known',row['feature_readiness'])


if __name__ == '__main__':
    unittest.main()
