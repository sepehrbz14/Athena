import unittest
from datetime import datetime, timedelta, timezone

from crypto_bot.models import Candle, Signal, Side
from crypto_bot.regimes import MarketRegime
from crypto_bot.research import capture_signals
from crypto_bot.research_evaluation import _costed_trade, _bootstrap
from crypto_bot.strategies import StrategyEnsemble


class FixedReferenceResearchTests(unittest.TestCase):
    def test_costed_stop_risk_includes_fees_slippage_and_reserve(self):
        t=datetime(2025,1,1,tzinfo=timezone.utc)
        candles=[Candle(t,100,101,99,100,10000),Candle(t+timedelta(minutes=15),100,101,98,99,10000)]
        signal={'signal_id':1,'timestamp':t.isoformat(),'direction':'long','strategy':'test','regime':'range',
                'reference_price':100,'stop_price':99,'take_profit':110}
        cfg={'candle_minutes':15,'slippage_bps':5,'fee_bps':5,'funding_bps_8h':1,
             'risk_budget':50,'reference_equity':10000,'max_position_notional_pct':1,'max_margin_fraction':.8,
             'leverage':2,'min_quantity':1e-6,'max_participation_rate':.05}
        trade,reason=_costed_trade(signal,candles,{t.isoformat():0},cfg)
        self.assertIsNone(reason); self.assertIsNotNone(trade)
        self.assertLessEqual(trade['modeled_loss_at_initial_stop'],50+1e-8)
        self.assertAlmostEqual(trade['gross_pnl']-trade['slippage']-trade['fees']-trade['funding_paid'],trade['net_pnl'])

    def test_weekly_bootstrap_reproducible_and_cluster_configured(self):
        rows=[{'entry_timestamp':datetime(2025,1,1,tzinfo=timezone.utc).isoformat(),'net_pnl':1.0},
              {'entry_timestamp':datetime(2025,1,2,tzinfo=timezone.utc).isoformat(),'net_pnl':-0.5},
              {'entry_timestamp':datetime(2025,1,10,tzinfo=timezone.utc).isoformat(),'net_pnl':2.0}]
        a=_bootstrap(rows,17,100); b=_bootstrap(rows,17,100)
        self.assertEqual(a,b); self.assertEqual(a['seed'],17); self.assertIn('ISO week',a['block_unit'])

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
