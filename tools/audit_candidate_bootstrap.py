"""Compare block-length sensitivity for the recorded causal forward outcomes."""
from __future__ import annotations

import argparse
import csv
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from crypto_bot.io import load_ohlcv_csv
from run_chronological_windows import WINDOWS


BLOCK_LENGTHS = (32, 64, 128)
REPLICATES = 200


def quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    left, right = int(position), min(int(position) + 1, len(ordered) - 1)
    return ordered[left] + (ordered[right] - ordered[left]) * (position - left)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("outcomes_csv", type=Path)
    parser.add_argument("dataset_csv", type=Path)
    parser.add_argument("output_csv", type=Path)
    parser.add_argument("--replicates", type=int, default=REPLICATES)
    args = parser.parse_args()

    candles = load_ohlcv_csv(args.dataset_csv)
    candle_indices: dict[str, dict[str, int]] = {}
    for name, start, end in WINDOWS:
        scored = [c.timestamp for c in candles if start <= c.timestamp < end]
        candle_indices[name] = {stamp.isoformat(): index for index, stamp in enumerate(scored)}
    groups: dict[tuple[str, str, int], list[tuple[int, float]]] = defaultdict(list)
    with args.outcomes_csv.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["strategy"] not in {"trend_following", "breakout", "momentum", "mean_reversion"}:
                continue
            groups[(row["window"], row["strategy"], int(row["horizon_candles"]))].append(
                (candle_indices[row["window"]][row["decision_candle_open_timestamp"]],
                 float(row["forward_direction_adjusted_return_pct"]))
            )

    rows = []
    for group, observations in sorted(groups.items()):
        window, strategy, horizon = group
        for block_length in BLOCK_LENGTHS:
            by_block: dict[int, list[float]] = defaultdict(list)
            for index, value in observations:
                by_block[index // block_length].append(value)
            blocks = [by_block[key] for key in sorted(by_block)]
            seed = 81_337 + sum(map(ord, window + strategy)) + horizon * 31 + block_length
            rng = random.Random(seed)
            means = []
            for _ in range(args.replicates):
                chosen = [blocks[rng.randrange(len(blocks))] for _ in blocks]
                sample_size = sum(len(block) for block in chosen)
                means.append(sum(sum(block) for block in chosen) / sample_size)
            values = [value for _, value in observations]
            rows.append({
                "window": window,
                "strategy": strategy,
                "horizon_candles": horizon,
                "sample_count": len(values),
                "block_length_candles": block_length,
                "replicates": args.replicates,
                "mean_return_pct": statistics.fmean(values),
                "block_bootstrap_mean_95ci_low_pct": quantile(means, 0.025),
                "block_bootstrap_mean_95ci_high_pct": quantile(means, 0.975),
            })
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} group/block estimates to {args.output_csv}")


if __name__ == "__main__":
    main()
