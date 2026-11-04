"""Append one completed candle to Athena's local simulation-only paper runner."""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crypto_bot.io import load_ohlcv_csv
from crypto_bot.paper import PaperOnlyRunner


def main() -> None:
    parser = argparse.ArgumentParser(description="Paper-only completed-candle replay. This tool cannot submit real orders.")
    parser.add_argument("--history", type=Path, required=True, help="frozen historical warm-up CSV")
    parser.add_argument("--new-candle", type=Path, required=True, help="CSV containing exactly one completed 15m candle")
    parser.add_argument("--state-dir", type=Path, required=True, help="new isolated paper-run directory")
    parser.add_argument("--observed-at", help="UTC ISO receipt time (defaults to current UTC time)")
    args = parser.parse_args()

    history = load_ohlcv_csv(args.history)
    incoming = load_ohlcv_csv(args.new_candle)
    if len(incoming) != 1:
        parser.error("--new-candle must contain exactly one completed candle")
    observed_at = datetime.fromisoformat(args.observed_at.replace("Z", "+00:00")) if args.observed_at else datetime.now(timezone.utc)
    digest = hashlib.sha256(args.history.read_bytes()).hexdigest()
    runner = PaperOnlyRunner(history, args.state_dir, digest)
    result = runner.append_completed_candle(incoming[0], observed_at)
    print(f"PAPER ONLY: {result['status']} at {result['timestamp']}; no real orders were sent.")
    if result["replayed"]:
        print(f"Closed trades: {result['trades_closed']} | Last marked equity: ${result['marked_equity']:,.2f}")
        print(f"Audit files: {args.state_dir.resolve()}")


if __name__ == "__main__":
    main()
