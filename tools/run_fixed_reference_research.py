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
    args=parser.parse_args()
    report=evaluate(args.signals,load_ohlcv_csv(args.candles),args.output,seed=args.seed,iterations=args.bootstrap_iterations)
    print(f"signals={report['metadata']['signal_rows']} trades={report['executed_trade_count']} exclusions={report['metadata']['excluded_signals']}")
    print(args.output/'research_report.md')


if __name__=='__main__': main()
