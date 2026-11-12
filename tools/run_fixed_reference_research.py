from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from crypto_bot.io import load_ohlcv_csv
from crypto_bot.research_evaluation import evaluate


def main() -> None:
    parser=argparse.ArgumentParser(description='Evaluate frozen strategy signals as independent fixed-reference-risk trades.')
    parser.add_argument('--signals',type=Path,required=True)
    parser.add_argument('--candles',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--seed',type=int,default=20261105)
    parser.add_argument('--bootstrap-iterations',type=int,default=2000)
    parser.add_argument('--bootstrap-block-length-weeks',type=int,default=4)
    parser.add_argument('--fee-bps',type=float,default=5.0)
    parser.add_argument('--slippage-bps',type=float,default=5.0)
    parser.add_argument('--funding-bps-8h',type=float,default=1.0)
    args=parser.parse_args()
    report=evaluate(args.signals,load_ohlcv_csv(args.candles),args.output,seed=args.seed,iterations=args.bootstrap_iterations,
                    fee_bps=args.fee_bps,slippage_bps=args.slippage_bps,funding_bps_8h=args.funding_bps_8h,
                    block_length_weeks=args.bootstrap_block_length_weeks)
    print(f"signals={report['metadata']['signal_rows']} trades={report['executed_trade_count']} exclusions={report['metadata']['excluded_signals']}")
    print(args.output/'research_report.md')


if __name__=='__main__': main()
