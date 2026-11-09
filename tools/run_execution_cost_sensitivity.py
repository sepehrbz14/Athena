from __future__ import annotations

import argparse,csv,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from crypto_bot.io import load_ohlcv_csv
from crypto_bot.research_evaluation import evaluate

def main():
    p=argparse.ArgumentParser(description='Frozen-signal execution-cost sensitivity; no parameter selection.')
    p.add_argument('--signals',type=Path,required=True);p.add_argument('--candles',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--funding-bps-8h',type=float,default=1.0);p.add_argument('--seed',type=int,default=20261105);p.add_argument('--bootstrap-iterations',type=int,default=2000)
    a=p.parse_args(); bars=load_ohlcv_csv(a.candles); scenarios=[(0,0),(2,2),(5,5),(10,10)]; summary=[]
    a.output.mkdir(parents=True,exist_ok=True)
    for fee,slip in scenarios:
        tag=f'fee{fee}_slip{slip}'
        result=evaluate(a.signals,bars,a.output/tag,seed=a.seed,iterations=a.bootstrap_iterations,fee_bps=fee,slippage_bps=slip,funding_bps_8h=a.funding_bps_8h)
        for strategy,metrics in result['trade_performance_by_strategy'].items():
            summary.append({'scenario':tag,'fee_bps_per_side':fee,'slippage_bps_per_side':slip,'funding_bps_8h':a.funding_bps_8h,'strategy':strategy,**metrics})
    with (a.output/'sensitivity_summary.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(summary[0]));w.writeheader();w.writerows(summary)
    payload={'purpose':'execution-cost sensitivity only; no scenario selected or recommended','scenarios':[{'fee_bps_per_side':f,'slippage_bps_per_side':s,'funding_bps_8h':a.funding_bps_8h} for f,s in scenarios],
             'seed':a.seed,'bootstrap_iterations':a.bootstrap_iterations,'results':summary}
    (a.output/'sensitivity.json').write_text(json.dumps(payload,indent=2,allow_nan=False),encoding='utf-8')
    lines=['# Frozen-signal execution-cost sensitivity','','All scenarios use the same frozen signals and fixed-reference-risk sizing rules. Funding is held at the configured rate and reported separately. These are independent overlapping trades, not executable portfolios. No scenario is selected as preferred.','','| Scenario | Strategy | Trades | Gross P&L | Fees | Slippage | Funding | Net P&L | Expectancy | PF |','|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in summary:lines.append(f"| {r['fee_bps_per_side']} / {r['slippage_bps_per_side']} bps | {r['strategy']} | {r['trade_count']} | ${r['gross_pnl']:,.2f} | ${r['fees']:,.2f} | ${r['slippage']:,.2f} | ${r['funding_paid']:,.2f} | ${r['net_pnl']:,.2f} | ${r['expectancy']:.2f} | {r['profit_factor']:.2f} |")
    lines+=['','Funding is kept constant across the grid and its signed value is shown separately (`funding_paid`; negative values are net credits). Cost levels are analytical scenarios, not recommendations. See each scenario subdirectory for trade ledger, predictive results, monthly/regime summaries and reproducibility metadata.']
    (a.output/'sensitivity_report.md').write_text('\n'.join(lines),encoding='utf-8')
if __name__=='__main__':main()
