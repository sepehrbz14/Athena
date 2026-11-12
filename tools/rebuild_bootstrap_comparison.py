from __future__ import annotations

import argparse,csv,json,sys,subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from crypto_bot.research_evaluation import _moving_block_bootstrap,sha256


def main():
    p=argparse.ArgumentParser(description='Recompute bootstrap uncertainty from frozen completed-trade ledgers only.')
    p.add_argument('--sensitivity-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--seed',type=int,default=20261105);p.add_argument('--iterations',type=int,default=2000);p.add_argument('--block-length-weeks',type=int,default=4)
    a=p.parse_args(); scenarios=['fee0_slip0','fee2_slip2','fee5_slip5','fee10_slip10']; audits=[]; results=[]
    expected=[(0,0),(2,2),(5,5),(10,10)]; common_sha=None; common_count=None; common_commit=None; common_code_hashes=None
    for folder,(fee,slip) in zip(scenarios,expected):
        base=a.sensitivity_root/folder
        meta=json.loads((base/'run_metadata.json').read_text(encoding='utf-8'))
        ev=json.loads((base/'evaluation.json').read_text(encoding='utf-8'))
        if meta['code_worktree_dirty'] is not False: raise ValueError(f'{folder}: worktree was not clean')
        if meta['configuration']['fee_bps']!=fee or meta['configuration']['slippage_bps']!=slip or meta['configuration']['funding_bps_8h']!=1.0:
            raise ValueError(f'{folder}: scenario costs do not match expected configuration')
        if common_sha is None:
            common_sha=meta['source_signal_sha256'];common_count=meta['signal_rows'];common_commit=meta['code_commit_sha'];common_code_hashes=meta['code_file_sha256']
        if (meta['source_signal_sha256']!=common_sha or meta['signal_rows']!=common_count or
            meta['code_commit_sha']!=common_commit or meta['code_file_sha256']!=common_code_hashes):
            raise ValueError(f'{folder}: signal data/count or code snapshot differs across scenarios')
        if ev['metadata']['signal_rows']!=common_count: raise ValueError(f'{folder}: evaluation signal count mismatch')
        audits.append({'scenario':folder,'code_commit_sha':meta['code_commit_sha'],'code_worktree_dirty':meta['code_worktree_dirty'],
            'source_signal_sha256':meta['source_signal_sha256'],'signal_rows':meta['signal_rows'],'trade_rows':ev['executed_trade_count'],
            'code_file_sha256':meta['code_file_sha256'],'fee_bps':fee,'slippage_bps':slip,'funding_bps_8h':1.0,'metadata_configuration_matches':True})
        with (base/'completed_trade_ledger.csv').open(encoding='utf-8',newline='') as f: ledger=list(csv.DictReader(f))
        if len(ledger)!=common_count: raise ValueError(f'{folder}: ledger count mismatch')
        for strategy,stats in ev['trade_performance_by_strategy'].items():
            rows=[r for r in ledger if r['strategy']==strategy]
            old=stats.get('weekly_block_bootstrap') or stats.get('weekly_cluster_bootstrap_legacy')
            new=_moving_block_bootstrap(rows,a.seed,a.iterations,a.block_length_weeks)
            results.append({'scenario':folder,'strategy':strategy,'trade_count':len(rows),
                'legacy_expectancy_ci':old['expectancy_ci_95'],'legacy_profit_factor_ci':old['profit_factor_ci_95'],
                'moving_block_expectancy_ci':new['expectancy_ci_95'],'moving_block_profit_factor_ci':new['profit_factor_ci_95'],
                'moving_block_length_weeks':new['block_length_weeks'],'iterations':a.iterations,'seed':a.seed})
    a.output.mkdir(parents=True,exist_ok=True)
    payload={'source':'existing frozen scenario trade ledgers; no backtest rerun','audit':audits,
        'common_code_snapshot_sha':common_commit,'common_signal_sha256':common_sha,'common_signal_count':common_count,
        'bootstrap_code_commit_sha':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'bootstrap_code_worktree_dirty':bool(subprocess.check_output(['git','status','--porcelain'],text=True).strip()),
        'bootstrap_code_sha256':sha256(ROOT/'crypto_bot'/'research_evaluation.py'),
        'moving_block_configuration':{'method':'non-circular moving-block bootstrap over consecutive calendar weeks','block_length_weeks':a.block_length_weeks,'iterations':a.iterations,'seed':a.seed},
        'legacy_method':'independently resampled ISO-week clusters','results':results}
    (a.output/'bootstrap_comparison.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
    with (a.output/'bootstrap_comparison.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(results[0]));w.writeheader();w.writerows(results)
    lines=['# Bootstrap methodology comparison','','The moving-block intervals below are primary. They were recomputed from the frozen completed-trade ledgers only; no signal generation, sizing, execution simulation, or backtest was rerun. The legacy values are the original independent-week cluster bootstrap.','',f"- Clean code snapshot: `{common_commit}`",f"- Signal SHA-256: `{common_sha}`",f"- Signal count per scenario: {common_count:,}",f"- Moving-block bootstrap: {a.iterations:,} iterations; {a.block_length_weeks} consecutive calendar weeks per non-circular sampled block; seed {a.seed}.",'','Four scenario metadata files were audited: all have a clean worktree, the same code snapshot, signal hash and signal count, and their fee/slippage/funding settings match their scenario labels. Full audit details are in `bootstrap_comparison.json`.','','| Scenario | Strategy | Trades | Legacy expectancy CI | Moving-block expectancy CI | Legacy PF CI | Moving-block PF CI |','|---|---|---:|---:|---:|---:|---:|']
    for r in results:
        fmt=lambda v:f"[{v[0]:.2f}, {v[1]:.2f}]"
        lines.append(f"| {r['scenario']} | {r['strategy']} | {r['trade_count']} | {fmt(r['legacy_expectancy_ci'])} | {fmt(r['moving_block_expectancy_ci'])} | {fmt(r['legacy_profit_factor_ci'])} | {fmt(r['moving_block_profit_factor_ci'])} |")
    lines+=['','These confidence intervals describe the variability under their resampling schemes; neither establishes independent observations or predictive validity.']
    (a.output/'bootstrap_comparison.md').write_text('\n'.join(lines),encoding='utf-8')

if __name__=='__main__':main()
