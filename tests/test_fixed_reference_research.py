import unittest
import csv,json,tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from crypto_bot.models import Candle, Signal, Side
from crypto_bot.regimes import MarketRegime
from crypto_bot.research import capture_signals
from crypto_bot.research_evaluation import (_costed_trade, _weekly_cluster_bootstrap,
    _moving_block_bootstrap, _moving_block_indices, _calendar_week_trade_values, evaluate)
from crypto_bot.strategies import StrategyEnsemble


class FixedReferenceResearchTests(unittest.TestCase):
    def setUp(self):
        self.t=datetime(2025,1,1,tzinfo=timezone.utc)
        self.cfg={'candle_minutes':15,'slippage_bps':5,'fee_bps':5,'funding_bps_8h':1,
             'risk_budget':50,'reference_equity':10000,'max_position_notional_pct':1,'max_margin_fraction':.8,
             'leverage':2,'min_quantity':1e-6,'max_participation_rate':.05}

    def signal(self,side='long',stop=99,target=110):
        return {'signal_id':1,'timestamp':self.t.isoformat(),'direction':side,'strategy':'test','regime':'range',
                'reference_price':100,'stop_price':stop,'take_profit':target}

    def bars(self,*items):
        return [Candle(self.t+offset,open_,high,low,close,vol) for offset,open_,high,low,close,vol in items]

    def test_costed_stop_risk_includes_fees_slippage_and_reserve(self):
        candles=self.bars((timedelta(0),100,101,99,100,10000),(timedelta(minutes=15),100,101,98,99,10000))
        trade,reason=_costed_trade(self.signal(),candles,{self.t.isoformat():0},self.cfg)
        self.assertIsNone(reason); self.assertIsNotNone(trade)
        self.assertLessEqual(trade['modeled_loss_at_initial_stop'],50+1e-8)
        self.assertAlmostEqual(trade['gross_pnl']-trade['slippage']-trade['fees']-trade['funding_paid'],trade['net_pnl'])

    def test_long_and_short_stops_and_collision_use_stop_first(self):
        bars=self.bars((timedelta(0),100,101,99,100,10000),(timedelta(minutes=15),100,111,98,100,10000))
        long,_=_costed_trade(self.signal('long',99,101),bars,{self.t.isoformat():0},self.cfg)
        short,_=_costed_trade(self.signal('short',101,99),bars,{self.t.isoformat():0},self.cfg)
        self.assertEqual(long['exit_reason'],'stop_loss'); self.assertEqual(short['exit_reason'],'stop_loss')

    def test_stop_gap_executes_at_adverse_open(self):
        bars=self.bars((timedelta(0),100,101,99,100,10000),(timedelta(minutes=15),100,101,99.5,100,10000),
                       (timedelta(minutes=30),95,97,94,96,10000))
        trade,_=_costed_trade(self.signal('long',99,110),bars,{self.t.isoformat():0},self.cfg)
        self.assertEqual(trade['exit_reason'],'stop_loss'); self.assertTrue(trade['stop_gap_exit']); self.assertEqual(trade['raw_exit_price'],95)

    def test_multiple_consecutive_gaps_wait_for_contiguous_fresh_volume(self):
        bars=self.bars((timedelta(0),100,101,99,100,10000),(timedelta(hours=4),100,101,99,100,5000),
                       (timedelta(hours=8),100,101,99,100,4000),(timedelta(hours=8,minutes=15),100,101,99,100,3000),
                       (timedelta(hours=8,minutes=30),100,101,99,100,3000))
        trade,reason=_costed_trade(self.signal(),bars,{self.t.isoformat():0},self.cfg)
        self.assertIsNone(reason); self.assertEqual(trade['entry_timestamp'],bars[3].timestamp.isoformat())

    def test_insufficient_liquidity_excludes_instead_of_using_stale_volume(self):
        bars=self.bars((timedelta(0),100,101,99,100,0),(timedelta(minutes=15),100,101,99,100,0))
        trade,reason=_costed_trade(self.signal(),bars,{self.t.isoformat():0},self.cfg)
        self.assertIsNone(trade); self.assertEqual(reason,'insufficient_fresh_volume_after_gap')

    def test_notional_and_collateral_caps_bound_quantity(self):
        bars=self.bars((timedelta(0),100,101,99,100,100000),(timedelta(minutes=15),100,101,99,100,100000))
        wide_stop=self.signal('long',50,200)
        notional_cfg={**self.cfg,'max_position_notional_pct':.01}
        trade,_=_costed_trade(wide_stop,bars,{self.t.isoformat():0},notional_cfg)
        self.assertLessEqual(trade['quantity']*trade['entry_execution_price'],100+1e-8)
        collateral_cfg={**self.cfg,'leverage':.1,'max_position_notional_pct':10}
        trade,_=_costed_trade(wide_stop,bars,{self.t.isoformat():0},collateral_cfg)
        self.assertLessEqual(trade['quantity']*trade['entry_execution_price']/collateral_cfg['leverage'],8000+1e-8)

    def test_funding_accrues_across_gap_and_end_of_data_exit_reconciles(self):
        bars=self.bars((timedelta(0),100,101,99,100,10000),(timedelta(minutes=15),100,101,99,100,10000),
                       (timedelta(hours=4),100,101,99,100,10000))
        trade,_=_costed_trade(self.signal('long',50,200),bars,{self.t.isoformat():0},self.cfg)
        self.assertEqual(trade['exit_reason'],'end_of_data')
        self.assertGreater(trade['funding_paid'],0)
        self.assertAlmostEqual(trade['gross_pnl']-trade['slippage']-trade['fees']-trade['funding_paid'],trade['net_pnl'])

    def test_monthly_ledger_reconciliation_and_exclusion_accounting(self):
        bars=self.bars((timedelta(0),100,101,99,100,10000),(timedelta(minutes=15),100,101,99.5,100,10000),
                       (timedelta(minutes=30),100,101,98,99,10000))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); signal_path=root/'signals.csv'; out=root/'report'
            fields=['signal_id','timestamp','strategy','direction','regime','reference_price','stop_price','take_profit','forward_outcomes']
            with signal_path.open('w',newline='',encoding='utf-8') as f:
                w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
                w.writerow({'signal_id':1,'timestamp':self.t.isoformat(),'strategy':'test','direction':'long','regime':'range','reference_price':100,'stop_price':99,'take_profit':110,'forward_outcomes':'{}'})
                w.writerow({'signal_id':2,'timestamp':(self.t+timedelta(days=10)).isoformat(),'strategy':'test','direction':'long','regime':'range','reference_price':100,'stop_price':99,'take_profit':110,'forward_outcomes':'{}'})
            result=evaluate(signal_path,bars,out,iterations=10)
            self.assertEqual(result['executed_trade_count'],1)
            self.assertEqual(result['exclusions'],{'signal_timestamp_not_in_market_data':1})
            self.assertAlmostEqual(result['monthly_reconciliation']['test']['delta'],0)
            with (out/'completed_trade_ledger.csv').open(encoding='utf-8',newline='') as f:
                trade=next(csv.DictReader(f))
            self.assertAlmostEqual(float(trade['gross_pnl'])-float(trade['slippage'])-float(trade['fees'])-float(trade['funding_paid']),float(trade['net_pnl']))

    def test_weekly_cluster_bootstrap_reproducible_and_accurately_named(self):
        rows=[{'entry_timestamp':datetime(2025,1,1,tzinfo=timezone.utc).isoformat(),'net_pnl':1.0},
              {'entry_timestamp':datetime(2025,1,2,tzinfo=timezone.utc).isoformat(),'net_pnl':-0.5},
              {'entry_timestamp':datetime(2025,1,10,tzinfo=timezone.utc).isoformat(),'net_pnl':2.0}]
        a=_weekly_cluster_bootstrap(rows,17,100); b=_weekly_cluster_bootstrap(rows,17,100)
        self.assertEqual(a,b); self.assertEqual(a['seed'],17); self.assertIn('ISO week',a['block_unit'])

    def test_moving_block_bootstrap_is_reproducible_and_configurable(self):
        rows=[]
        for week,values in [(1,[1,-1]),(2,[2]),(3,[-2]),(4,[4]),(5,[-4]),(6,[6])]:
            for value in values:
                rows.append({'entry_timestamp':datetime.fromisocalendar(2025,week,1).replace(tzinfo=timezone.utc).isoformat(),'net_pnl':value})
        a=_moving_block_bootstrap(rows,41,150,block_length_weeks=2)
        b=_moving_block_bootstrap(rows,41,150,block_length_weeks=2)
        c=_moving_block_bootstrap(rows,41,150,block_length_weeks=3)
        self.assertEqual(a,b); self.assertEqual(a['seed'],41); self.assertEqual(a['iterations'],150)
        self.assertEqual(a['block_length_weeks'],2); self.assertNotEqual(a['expectancy_ci_95'],c['expectancy_ci_95'])

    def test_moving_block_construction_preserves_week_chronology(self):
        import random
        indexes=_moving_block_indices(11,3,random.Random(7))
        self.assertEqual(len(indexes),11)
        for offset in range(0,9,3):
            self.assertEqual(indexes[offset+1],indexes[offset]+1)
            self.assertEqual(indexes[offset+2],indexes[offset]+2)

    def test_moving_blocks_are_non_circular_at_right_boundary(self):
        class LastStart:
            def randint(self, low, high): return high
        indexes=_moving_block_indices(7,3,LastStart())
        self.assertEqual(indexes[:3],[4,5,6])
        self.assertEqual(indexes[3:6],[4,5,6])
        self.assertNotIn(0,indexes[:3])

    def test_calendar_weeks_handle_iso_year_transition(self):
        rows=[{'entry_timestamp':datetime(2024,12,29,tzinfo=timezone.utc).isoformat(),'net_pnl':1},
              {'entry_timestamp':datetime(2024,12,30,tzinfo=timezone.utc).isoformat(),'net_pnl':2},
              {'entry_timestamp':datetime(2025,1,6,tzinfo=timezone.utc).isoformat(),'net_pnl':3}]
        weeks,clusters,count=_calendar_week_trade_values(rows)
        self.assertEqual(weeks,['2024-W52','2025-W01','2025-W02'])
        self.assertEqual(count,3); self.assertEqual(clusters,[[1.0],[2.0],[3.0]])

    def test_calendar_week_assignment_normalizes_offset_timestamp_to_utc(self):
        rows=[{'entry_timestamp':'2025-01-05T23:30:00-02:00','net_pnl':1.0}]
        weeks,clusters,count=_calendar_week_trade_values(rows)
        self.assertEqual(weeks,['2025-W02'])
        self.assertEqual(clusters,[[1.0]])
        self.assertEqual(count,1)

    def test_bootstrap_rejects_invalid_iterations_and_block_lengths(self):
        for iterations in (0,-1,1.5,True):
            with self.subTest(iterations=iterations):
                with self.assertRaises(ValueError): _weekly_cluster_bootstrap([],1,iterations)
                with self.assertRaises(ValueError): _moving_block_bootstrap([],1,iterations,4)
        for length in (0,-1,1.5,True):
            with self.subTest(block_length=length):
                with self.assertRaises(ValueError): _moving_block_bootstrap([],1,10,length)
                with self.assertRaises(ValueError): _moving_block_indices(5,length,__import__('random').Random(1))

    def test_bootstrap_rejects_non_finite_pnl_and_naive_timestamps(self):
        for pnl in (float('nan'),float('inf'),float('-inf')):
            rows=[{'entry_timestamp':self.t.isoformat(),'net_pnl':pnl}]
            with self.subTest(pnl=pnl):
                with self.assertRaises(ValueError): _weekly_cluster_bootstrap(rows,1,10)
                with self.assertRaises(ValueError): _moving_block_bootstrap(rows,1,10,2)
        naive=[{'entry_timestamp':'2025-01-01T00:00:00','net_pnl':1.0}]
        with self.assertRaises(ValueError): _moving_block_bootstrap(naive,1,10,2)

    def test_zero_trade_weeks_are_retained_inside_calendar_span(self):
        rows=[{'entry_timestamp':datetime(2025,1,6,tzinfo=timezone.utc).isoformat(),'net_pnl':1},
              {'entry_timestamp':datetime(2025,1,20,tzinfo=timezone.utc).isoformat(),'net_pnl':-1}]
        weeks,clusters,count=_calendar_week_trade_values(rows)
        self.assertEqual(weeks,['2025-W02','2025-W03','2025-W04'])
        self.assertEqual(clusters,[[1.0],[],[-1.0]])
        self.assertEqual(count,2)

    def test_moving_bootstrap_handles_empty_and_single_week_data(self):
        empty=_moving_block_bootstrap([],3,20,4)
        self.assertIsNone(empty['expectancy_ci_95'])
        row=[{'entry_timestamp':datetime(2025,3,3,tzinfo=timezone.utc).isoformat(),'net_pnl':-2.0}]
        short=_moving_block_bootstrap(row,3,20,4)
        self.assertEqual(short['source_calendar_weeks'],1)
        self.assertEqual(short['block_length_weeks'],1)
        self.assertEqual(short['expectancy_ci_95'],[-2.0,-2.0])

    def test_infinite_profit_factor_bootstrap_is_json_serializable(self):
        rows=[{'entry_timestamp':datetime(2025,3,3,tzinfo=timezone.utc).isoformat(),'net_pnl':1.0},
              {'entry_timestamp':datetime(2025,3,4,tzinfo=timezone.utc).isoformat(),'net_pnl':2.0}]
        result=_moving_block_bootstrap(rows,5,40,2)
        encoded=json.dumps(result,allow_nan=False)
        self.assertIn('Infinity',encoded)

    def test_incomplete_higher_timeframe_features_have_no_future_leakage(self):
        class Always:
            name='always'; allowed_regimes=set(MarketRegime)
            def signal(self,market): return Signal(Side.LONG,90,110,.7,self.name,'test')
        t=datetime(2025,1,1,tzinfo=timezone.utc)
        bars=[Candle(t+timedelta(minutes=15*i),100+i,101+i,99+i,100+i,1) for i in range(20)]
        changed=list(bars)
        for i in range(8,20):
            stamp=changed[i].timestamp; p=500+i
            changed[i]=Candle(stamp,p,p+1,p-1,p,1)
        ens=StrategyEnsemble([Always()])
        first=capture_signals(bars,ensemble=ens); second=capture_signals(changed,ensemble=ens)
        a=next(r for r in first if r['candle_index']==5); b=next(r for r in second if r['candle_index']==5)
        self.assertEqual((a['regime'],a['features'],a['reference_price']),(b['regime'],b['features'],b['reference_price']))


if __name__=='__main__': unittest.main()
