from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crypto_bot.portfolio import run_from_files


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Athena's deterministic frozen-signal portfolio simulator")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--signals", required=True, type=Path)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--candles", required=True, type=Path)
    parser.add_argument("--signal-sha256", required=True)
    parser.add_argument("--candle-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--resume", type=Path)
    args = parser.parse_args()
    result = run_from_files(config_path=args.config, signal_path=args.signals,
        metadata_path=args.metadata, candle_path=args.candles,
        expected_signal_sha256=args.signal_sha256, expected_candle_sha256=args.candle_sha256,
        output_dir=args.output, checkpoint_path=args.checkpoint, resume_from=args.resume)
    print(f"status={result.status} equity={result.reconciliation['equity']} ")
    print(f"journal_entries={len(result.ledger.journal)} closed_positions={len(result.ledger.completed_positions)}")
    print(f"output={args.output.resolve()}")
    return 0 if result.status != "insolvent" else 2


if __name__ == "__main__":
    raise SystemExit(main())
