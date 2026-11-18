from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crypto_bot.phase5_research import run_phase5


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate read-only Athena Phase 5 strategy diagnostics.")
    parser.add_argument("--output", type=Path, help="New, empty output directory; defaults to a unique dated reports directory.")
    parser.add_argument("--signals", type=Path, default=ROOT / "reports/independent_signal_research_gap_parity_2026-11-05/signals.csv")
    parser.add_argument("--signal-metadata", type=Path, default=ROOT / "reports/independent_signal_research_gap_parity_2026-11-05/run_metadata.json")
    parser.add_argument("--candles", type=Path, default=ROOT / "tests/tests/combined.csv")
    parser.add_argument("--independent-ledger", type=Path, default=ROOT / "reports/fixed_reference_clean_repro_2026-11-09/result/completed_trade_ledger.csv")
    parser.add_argument("--phase4-root", type=Path, default=ROOT / "reports/phase4_financial_audit_2026-11-14/final_c97b39f")
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or ROOT / "reports" / f"phase5_strategy_diagnostics_2026-11-18" / f"run_{stamp}"
    manifest = run_phase5(root=ROOT, output=output, signals_path=args.signals, signal_metadata_path=args.signal_metadata,
        candles_path=args.candles, independent_ledger_path=args.independent_ledger, phase4_root=args.phase4_root)
    print(f"output={output.resolve()}")
    print(f"source_commit={manifest['source'].get('source_commit')}")
    print(f"executable_positions={manifest['counts']['executable_portfolio_positions']}")
    print(f"independent_counterfactual_trades={manifest['counts']['independent_counterfactual_trades']}")
    print(f"holdout={manifest['final_out_of_sample_holdout']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
