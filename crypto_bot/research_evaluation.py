"""Fixed-reference-risk independent-trade evaluation of a frozen signal dataset."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from .backtest import valid_prior_volume
from .models import Candle, Side


def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()


def _costed_trade(signal: dict[str, Any], candles: list[Candle], index_by_time: dict[str,int], cfg: dict[str,Any]) -> tuple[dict[str,Any]|None,str|None]:
    ts=signal['timestamp']; i=index_by_time.get(ts)
    if i is None: return None,'signal_timestamp_not_in_market_data'
    if i+1>=len(candles): return None,'no_future_entry_candle'
    entry_i=i+1
    expected=candles[i].timestamp+timedelta(minutes=cfg['candle_minutes'])
    # After a gap, prior observed volume is stale. Wait until one post-gap
    # candle completes, then enter at the next observed open.
    side=Side(signal['direction']); sign=side.sign
    stop=float(signal['stop_price']); target=float(signal['take_profit']) if signal.get('take_profit') not in ('',None) else None
    prior_volume=None
    # Pending remains live across gaps, but each candidate open must have a
    # fresh, contiguous completed prior candle, matching IntrabarBacktester.
    while entry_i<len(candles):
        candidate=candles[entry_i]
        prior_volume,_stale=valid_prior_volume(candles,entry_i,cfg['candle_minutes'])
        candidate_exec=candidate.open*(1+sign*cfg['slippage_bps']/10000)
        if (side is Side.LONG and stop>=candidate_exec) or (side is Side.SHORT and stop<=candidate_exec):
            return None,'entry_gap_invalidates_stop'
        if prior_volume is not None and prior_volume>0:
            break
        entry_i+=1
    if entry_i>=len(candles): return None,'insufficient_fresh_volume_after_gap'
    entry_bar=candles[entry_i]
    raw_entry=entry_bar.open; slip=cfg['slippage_bps']/10000; fee=cfg['fee_bps']/10000
    entry_exec=raw_entry*(1+sign*slip)
    stop_exec=stop*(1-sign*slip)
    # Stop-risk budget includes price distance, expected adverse slippage and
    # both fees. Funding is separately accrued by actual elapsed holding time;
    # it is a variable carry cost and cannot be bounded ex ante without a time stop.
    unit_stop_loss=abs(entry_exec-stop_exec)+fee*(entry_exec+stop_exec)
    if unit_stop_loss<=0: return None,'invalid_unit_risk'
    q=cfg['risk_budget']/unit_stop_loss
    q=min(q,cfg['reference_equity']*cfg['max_position_notional_pct']/entry_exec,
          cfg['reference_equity']*cfg['max_margin_fraction']*cfg['leverage']/entry_exec)
    if q<cfg['min_quantity']: return None,'below_minimum_contract_quantity'
    prior_volume=prior_volume or 0
    q=min(q,prior_volume*cfg['max_participation_rate'])
    if q<cfg['min_quantity']: return None,'insufficient_fresh_volume'
    entry_fee=entry_exec*q*fee
    funding=0.0; last_funding=entry_bar.timestamp
    exit_raw=None; exit_ts=None; reason=None
    for bar in candles[entry_i:]:
        close_ts=bar.timestamp+timedelta(minutes=cfg['candle_minutes'])
        hours=max(0,(close_ts-last_funding).total_seconds()/3600)
        funding += sign*q*bar.close*cfg['funding_bps_8h']/10000*hours/8
        last_funding=close_ts
        if side is Side.LONG:
            if bar.low<=stop: exit_raw=min(stop,bar.open); reason='stop_loss'
            elif target is not None and bar.high>=target: exit_raw=target; reason='take_profit'
        else:
            if bar.high>=stop: exit_raw=max(stop,bar.open); reason='stop_loss'
            elif target is not None and bar.low<=target: exit_raw=target; reason='take_profit'
        if reason:
            exit_ts=bar.timestamp; break
    if exit_raw is None:
        bar=candles[-1]; exit_raw=bar.close; exit_ts=bar.timestamp; reason='end_of_data'
    exit_exec=exit_raw*(1-sign*slip)
    raw_gross=sign*q*(exit_raw-raw_entry)
    slippage=q*(abs(entry_exec-raw_entry)+abs(exit_exec-exit_raw))
    execution_gross=raw_gross-slippage
    exit_fee=exit_exec*q*fee; fees=entry_fee+exit_fee
    net=execution_gross-fees-funding
    held_hours=max(0,(exit_ts-entry_bar.timestamp).total_seconds()/3600)
    modeled_stop_loss=(abs(entry_exec-stop_exec)+fee*(entry_exec+stop_exec))*q
    return {'signal_id':signal['signal_id'],'strategy':signal['strategy'],'regime':signal.get('regime','unknown'),
        'direction':side.value,'signal_timestamp':ts,'entry_timestamp':entry_bar.timestamp.isoformat(),
        'exit_timestamp':exit_ts.isoformat(),'reference_price':signal['reference_price'],
        'raw_entry_price':raw_entry,'entry_execution_price':entry_exec,'raw_exit_price':exit_raw,
        'exit_execution_price':exit_exec,'stop_price':stop,'target_price':target,'quantity':q,
        'risk_budget':cfg['risk_budget'],'modeled_loss_at_initial_stop':modeled_stop_loss,
        'initial_stop_budget_breach':modeled_stop_loss>cfg['risk_budget']+1e-8,
        'stop_gap_exit':reason=='stop_loss' and ((side is Side.LONG and exit_raw<stop) or (side is Side.SHORT and exit_raw>stop)),
        'gross_pnl':raw_gross,'execution_gross_pnl':execution_gross,'entry_fee':entry_fee,
        'exit_fee':exit_fee,'fees':fees,'slippage':slippage,'funding_paid':funding,
        'net_pnl':net,'exit_reason':reason,'holding_hours':held_hours,
        'reconciliation_delta':raw_gross-slippage-fees-funding-net,
        'risk_limited_by':'participation' if q+1e-12 < cfg['risk_budget']/unit_stop_loss else 'risk_budget_or_exposure_cap'},None


def _metrics(rows: list[dict[str,Any]]) -> dict[str,Any]:
    pnls=[float(r['net_pnl']) for r in rows]; wins=[p for p in pnls if p>0]; losses=[p for p in pnls if p<0]
    return {'trade_count':len(rows),'wins':len(wins),'win_rate':len(wins)/len(rows) if rows else None,
        'gross_pnl':sum(float(r['gross_pnl']) for r in rows),'execution_gross_pnl':sum(float(r['execution_gross_pnl']) for r in rows),
        'fees':sum(float(r['fees']) for r in rows),'slippage':sum(float(r['slippage']) for r in rows),
        'funding_paid':sum(float(r['funding_paid']) for r in rows),'net_pnl':sum(pnls),
        'expectancy':mean(pnls) if pnls else None,'profit_factor':sum(wins)/abs(sum(losses)) if losses else None}


def _wilson(wins:int,n:int,z:float=1.95996398454)->list[float]|None:
    if not n:return None
    p=wins/n; d=1+z*z/n; center=(p+z*z/(2*n))/d
    half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return [max(0,center-half),min(1,center+half)]


def _moving_block_indices(week_count:int,block_length_weeks:int,rng:random.Random)->list[int]:
    """Draw non-circular consecutive weekly blocks, truncated to source span."""
    if week_count<=0:return []
    length=min(max(1,block_length_weeks),week_count)
    out=[]
    while len(out)<week_count:
        start=rng.randint(0,week_count-length)
        out.extend(range(start,start+length))
    return out[:week_count]


def _calendar_week_trade_values(rows:list[dict[str,Any]])->tuple[list[str],list[list[float]],int]:
    """Return contiguous ISO weeks (including empty weeks) and trade clusters."""
    from datetime import date
    by_week=defaultdict(list)
    for row in rows:
        by_week[datetime.fromisoformat(row['entry_timestamp']).date().strftime('%G-W%V')].append(float(row['net_pnl']))
    if not by_week:return [],[],0
    ordered=sorted(by_week)
    year,week=map(int,ordered[0].split('-W')); end_year,end_week=map(int,ordered[-1].split('-W'))
    cursor=date.fromisocalendar(year,week,1); end=date.fromisocalendar(end_year,end_week,1)
    keys=[]; clusters=[]
    while cursor<=end:
        iso=cursor.isocalendar(); key=f'{iso.year}-W{iso.week:02d}'
        keys.append(key); clusters.append(by_week.get(key,[])); cursor+=timedelta(days=7)
    return keys,clusters,len(by_week)


def _ci_bounds(values:list[float])->list[float|str]:
    values.sort()
    bounds=[values[int(.025*(len(values)-1))],values[int(.975*(len(values)-1))]]
    return ['Infinity' if math.isinf(x) and x>0 else '-Infinity' if math.isinf(x) else x for x in bounds]


def _weekly_cluster_bootstrap(rows:list[dict[str,Any]],seed:int,iterations:int)->dict[str,Any]:
    """Legacy independent ISO-week cluster resampling, retained for comparison."""
    weeks=defaultdict(list)
    for r in rows: weeks[datetime.fromisoformat(r['entry_timestamp']).strftime('%G-W%V')].append(float(r['net_pnl']))
    keys=sorted(weeks); rng=random.Random(seed); expectancy=[]; pf=[]
    if not keys:return {'seed':seed,'iterations':iterations,'block_unit':'ISO week','expectancy_ci_95':None,'profit_factor_ci_95':None}
    for _ in range(iterations):
        sample=[p for _ in keys for p in weeks[rng.choice(keys)]]
        w=[p for p in sample if p>0]; l=[p for p in sample if p<0]
        expectancy.append(sum(sample)/len(sample) if sample else 0)
        pf.append(sum(w)/abs(sum(l)) if l else (float('inf') if w else 0))
    def ci(vals):
        return _ci_bounds(vals)
    return {'seed':seed,'iterations':iterations,'block_unit':'ISO week sampled with replacement; trades within week stay clustered',
            'expectancy_ci_95':ci(expectancy),'profit_factor_ci_95':ci(pf)}


def _moving_block_bootstrap(rows:list[dict[str,Any]],seed:int,iterations:int,block_length_weeks:int=4)->dict[str,Any]:
    """Moving-block bootstrap of consecutive calendar-week trade clusters."""
    calendar,clusters,unique_trade_weeks=_calendar_week_trade_values(rows)
    if not calendar:
        return {'method':'moving_block_bootstrap','seed':seed,'iterations':iterations,'block_length_weeks':block_length_weeks,
                'expectancy_ci_95':None,'profit_factor_ci_95':None,'unique_trade_weeks':0}
    weekly=[]
    for vals in clusters:
        winners=sum(x for x in vals if x>0); losers=sum(x for x in vals if x<0)
        weekly.append((sum(vals),winners,losers,len(vals)))
    rng=random.Random(seed); expectancy=[]; profit_factors=[]
    for _ in range(iterations):
        sample=_moving_block_indices(len(weekly),block_length_weeks,rng)
        pnl=wins=losses=count=0.0
        for ix in sample:
            t,w,l,n=weekly[ix]; pnl+=t; wins+=w; losses+=l; count+=n
        expectancy.append(pnl/count if count else 0.0)
        profit_factors.append(wins/abs(losses) if losses else (float('inf') if wins else 0.0))
    def ci(vals):
        return _ci_bounds(vals)
    return {'method':'moving_block_bootstrap','seed':seed,'iterations':iterations,
        'block_length_weeks':min(max(1,block_length_weeks),len(weekly)),
        'source_calendar_weeks':len(weekly),'unique_trade_weeks':unique_trade_weeks,
        'methodology':'sample consecutive, non-circular calendar-week blocks with replacement; retain trade clusters within each week; concatenate and truncate to source span',
        'expectancy_ci_95':ci(expectancy),'profit_factor_ci_95':ci(profit_factors)}


def evaluate(signal_csv:Path,candles:list[Candle],output:Path,*,seed:int=20261105,iterations:int=2000,
             fee_bps:float=5.0,slippage_bps:float=5.0,funding_bps_8h:float=1.0,
             block_length_weeks:int=4)->dict[str,Any]:
    with signal_csv.open(encoding='utf-8',newline='') as f: signals=list(csv.DictReader(f))
    index={c.timestamp.isoformat():i for i,c in enumerate(candles)}
    cfg={'reference_equity':10000.0,'risk_per_trade_pct':0.005,'risk_budget':50.0,'leverage':2.0,
         'fee_bps':fee_bps,'slippage_bps':slippage_bps,'funding_bps_8h':funding_bps_8h,
         'max_position_notional_pct':1.0,'max_margin_fraction':0.8,'max_participation_rate':0.05,
         'min_quantity':0.000001,'candle_minutes':15,'stop_target_policy':'existing stops/targets; adverse stop first; gap through stop fills at open',
         'sizing_policy':'$50 stop-risk includes entry/exit slippage and fees; funding is variable elapsed-time carry, included in net P&L but excluded from ex-ante stop-risk sizing because exit time is unknown',
         'overlap_policy':'each signal is a separate independent counterfactual trade; no shared capital or aggregate liquidity; overlaps retained'}
    trades=[]; exclusions=Counter()
    for s in signals:
        s['signal_id']=int(s['signal_id']); s['reference_price']=float(s['reference_price']); s['stop_price']=float(s['stop_price'])
        trade,reason=_costed_trade(s,candles,index,cfg)
        if reason: exclusions[reason]+=1
        else: trades.append(trade)
    # Same-time overlapping trades are independent counterfactuals. Count a
    # trade overlapping any earlier entry/exit interval, and peak concurrency.
    starts=sorted((r['entry_timestamp'],r['exit_timestamp'],i) for i,r in enumerate(trades))
    active=[]; overlap=set(); max_concurrency=0
    from datetime import datetime as dt
    for start,end,i in starts:
        st=dt.fromisoformat(start); active=[j for j in active if dt.fromisoformat(trades[j]['exit_timestamp'])>st]
        if active: overlap.add(i); overlap.update(active)
        active.append(i); max_concurrency=max(max_concurrency,len(active))
    output.mkdir(parents=True,exist_ok=True)
    with (output/'completed_trade_ledger.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(trades[0]) if trades else ['signal_id']); w.writeheader(); w.writerows(trades)
    strategies=defaultdict(list); regimes=defaultdict(list); months=defaultdict(list); weeks=defaultdict(list)
    for t in trades:
        strategies[t['strategy']].append(t); regimes[(t['strategy'],t['regime'])].append(t)
        months[(t['strategy'],t['exit_timestamp'][:7])].append(t)
        weeks[datetime.fromisoformat(t['entry_timestamp']).strftime('%G-W%V')].append(t)
    summary={'trade_performance_by_strategy':{k:_metrics(v) for k,v in strategies.items()},
      'market_regime_performance':{f'{k[0]}|{k[1]}':_metrics(v) for k,v in regimes.items()},
      'monthly_performance':{f'{k[0]}|{k[1]}':_metrics(v) for k,v in sorted(months.items())},
      'exit_reason_counts_by_strategy':{k:dict(Counter(t['exit_reason'] for t in v)) for k,v in strategies.items()},
      'unique_signal_weeks':len(weeks),'trades_per_week':{k:len(v) for k,v in sorted(weeks.items())},
      'overlap_trade_count':len(overlap),'overlap_trade_pct':len(overlap)/len(trades) if trades else 0,
      'maximum_concurrent_independent_trades':max_concurrency,'exclusions':dict(exclusions),'executed_trade_count':len(trades)}
    for strat,rows in strategies.items():
        stats=summary['trade_performance_by_strategy'][strat]
        stats['win_rate_wilson_95']=_wilson(stats['wins'],stats['trade_count'])
        stats['weekly_cluster_bootstrap_legacy']=_weekly_cluster_bootstrap(rows,seed,iterations)
        stats['moving_block_bootstrap']=_moving_block_bootstrap(rows,seed,iterations,block_length_weeks)
        # Cumulative realized-trade diagnostic, not capital/equity available.
        eq=cfg['reference_equity']; peak=eq; maxdd=0; curve=[]
        for row in sorted(rows,key=lambda r:(r['exit_timestamp'],int(r['signal_id']))):
            eq+=row['net_pnl']; peak=max(peak,eq); maxdd=max(maxdd,(peak-eq)/peak if peak else 0)
            curve.append({'timestamp':row['exit_timestamp'],'strategy':strat,'cumulative_net_pnl':eq-cfg['reference_equity'],'reference_plus_cumulative_pnl':eq,'drawdown_from_running_peak_pct':(peak-eq)/peak if peak else None})
        stats['diagnostic_max_drawdown_pct']=maxdd
        if not (output/'independent_trade_diagnostic_curve.csv').exists():
            pass
        # curves accumulated below
    all_curves=[]
    for strat,rows in strategies.items():
        eq=cfg['reference_equity']; peak=eq
        for row in sorted(rows,key=lambda r:(r['exit_timestamp'],int(r['signal_id']))):
            eq+=row['net_pnl']; peak=max(peak,eq)
            all_curves.append({'timestamp':row['exit_timestamp'],'strategy':strat,'cumulative_net_pnl':eq-cfg['reference_equity'],'reference_plus_cumulative_pnl':eq,'drawdown_from_running_peak_pct':(peak-eq)/peak if peak else None})
    with (output/'independent_trade_diagnostic_curve.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(all_curves[0]) if all_curves else ['timestamp']);w.writeheader();w.writerows(all_curves)
    # Predictive outcomes are summarized independently of execution profitability.
    pred=defaultdict(list)
    for s in signals:
        regime=s.get('regime','unknown')
        for h,o in json.loads(s['forward_outcomes']).items():
            if o.get('status')=='complete' and o.get('direction_adjusted_return_pct') is not None:
                pred[(s['strategy'],regime,h)].append(o)
    predictive={f'{k[0]}|{k[1]}|{k[2]}':{'sample_size':len(v),
        'directional_accuracy':sum(float(x['direction_adjusted_return_pct'])>0 for x in v)/len(v),
        'directional_accuracy_wilson_95':_wilson(sum(float(x['direction_adjusted_return_pct'])>0 for x in v),len(v)),
        'mean_direction_adjusted_return_pct':mean(float(x['direction_adjusted_return_pct']) for x in v),
        'mean_mfe_pct':mean(float(x['mfe_pct']) for x in v if x.get('mfe_pct') is not None),
        'mean_mae_pct':mean(float(x['mae_pct']) for x in v if x.get('mae_pct') is not None)} for k,v in pred.items()}
    monthly_totals={}
    for strat,rows in strategies.items():
        trade_total=sum(float(r['net_pnl']) for r in rows)
        month_total=sum(float(v['net_pnl']) for k,v in summary['monthly_performance'].items() if k.startswith(strat+'|'))
        monthly_totals[strat]={'trade_ledger_net_pnl':trade_total,'monthly_net_pnl':month_total,'delta':trade_total-month_total}
        if abs(trade_total-month_total)>1e-7: raise AssertionError(f'monthly ledger does not reconcile for {strat}')
    summary['monthly_reconciliation']=monthly_totals
    code_files=[Path(__file__),Path(__file__).with_name('research.py'),Path(__file__).resolve().parents[1]/'tools'/'run_fixed_reference_research.py',Path(__file__).resolve().parents[1]/'tools'/'run_execution_cost_sensitivity.py']
    dirty=bool(subprocess.check_output(['git','status','--porcelain'],text=True).strip())
    meta={'source_signal_dataset':str(signal_csv.resolve()),'source_signal_sha256':sha256(signal_csv),
      'dataset_timestamp_start':candles[0].timestamp.isoformat(),'dataset_timestamp_end':candles[-1].timestamp.isoformat(),
      'candle_count':len(candles),'signal_rows':len(signals),'configuration':cfg,'code_commit_sha':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
      'code_worktree_dirty':dirty,'code_file_sha256':{p.name:sha256(p) for p in code_files},
      'random_seed':seed,'bootstrap_iterations':iterations,'bootstrap_block_length_weeks':block_length_weeks,'evaluation_timestamp_utc':datetime.now(timezone.utc).isoformat(),
      'excluded_signals':dict(exclusions),'trades':len(trades)}
    (output/'run_metadata.json').write_text(json.dumps(meta,indent=2),encoding='utf-8')
    (output/'predictive_performance.json').write_text(json.dumps(predictive,indent=2),encoding='utf-8')
    for filename, rows in [('strategy_performance.csv',summary['trade_performance_by_strategy']),
                           ('market_regime_performance.csv',summary['market_regime_performance']),
                           ('monthly_performance.csv',summary['monthly_performance']),
                           ('predictive_performance.csv',predictive)]:
        with (output/filename).open('w',newline='',encoding='utf-8') as f:
            w=csv.writer(f); w.writerow(['group',*next(iter(rows.values())).keys()] if rows else ['group'])
            for key,value in rows.items(): w.writerow([key,*value.values()])
    report={'metadata':meta,**summary,'predictive_performance':predictive,'trading_performance':summary['trade_performance_by_strategy'],
      'curve_warning':'independent_trade_diagnostic_curve is a sum of overlapping independent-trade P&L. Trades do not share capital or aggregate liquidity; it is not executable portfolio equity.',
      'confidence_warning':'Intervals describe resampled chronological blocks, not proof that trades or weeks are independent.'}
    (output/'evaluation.json').write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
    lines=['# Fixed-reference-risk independent-trade research evaluation','','This report is a counterfactual per-signal research evaluation, not an executable portfolio. Signals overlap and do not share capital or aggregate liquidity. See `run_metadata.json` for the frozen inputs, hash, commit, seed and parameters.','','Sizing targets a $50 initial-stop loss including adverse entry/exit slippage and entry/exit fees, subject to leverage collateral, notional, contract and per-trade volume caps. Variable funding is accrued from actual simulated elapsed holding time and included in net P&L, but excluded from ex-ante stop-risk sizing because exit time is unknown. Stop gaps can exceed the modeled stop loss and are flagged per trade.','','## Predictive performance','','Directional accuracy and forward returns are signal prediction measures, not trading profitability. Forward return, MFE and MAE sample sizes and means by strategy, regime and horizon are in `predictive_performance.csv` and `.json`.','','## Trading performance','','| Strategy | Trades | Win rate (Wilson 95%) | Gross P&L | Fees | Slippage | Funding paid | Net P&L | Expectancy (moving-block CI) | Profit factor (moving-block CI) | Diagnostic max DD |','|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for k,v in summary['trade_performance_by_strategy'].items():
      ci=v['moving_block_bootstrap']; lines.append(f"| {k} | {v['trade_count']} | {v['win_rate']:.1%} ({v['win_rate_wilson_95'][0]:.1%}–{v['win_rate_wilson_95'][1]:.1%}) | ${v['gross_pnl']:,.2f} | ${v['fees']:,.2f} | ${v['slippage']:,.2f} | ${v['funding_paid']:,.2f} | ${v['net_pnl']:,.2f} | ${v['expectancy']:.2f} ({ci['expectancy_ci_95'][0]:.2f}–{ci['expectancy_ci_95'][1]:.2f}) | {v['profit_factor']:.2f} ({ci['profit_factor_ci_95'][0]:.2f}–{ci['profit_factor_ci_95'][1]:.2f}) | {v['diagnostic_max_drawdown_pct']:.2%} |")
    lines+=['','## Monthly and regime breakdowns','','See `monthly_performance.csv` and `market_regime_performance.csv`. Monthly ledger reconciliation by strategy is in `evaluation.json` and has been checked to within $1e-7. Exit reason counts (stop, target, end of data) are also in `evaluation.json`.','', '## Clustering and exclusions',f"- Unique signal weeks: {summary['unique_signal_weeks']}",f"- Trades overlapping another independent trade: {summary['overlap_trade_count']} ({summary['overlap_trade_pct']:.1%}); maximum concurrent: {summary['maximum_concurrent_independent_trades']}.",f"- Excluded signals by reason: `{json.dumps(dict(exclusions),sort_keys=True)}`",f"- Primary interval: moving-block bootstrap, seed {seed}, {iterations} iterations, {block_length_weeks}-week consecutive non-circular calendar blocks. Legacy independent-week cluster intervals are retained in `evaluation.json`. Intervals do not prove independence.",'',report['curve_warning'],'']
    (output/'research_report.md').write_text('\n'.join(lines),encoding='utf-8')
    return report
