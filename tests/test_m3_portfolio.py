from __future__ import annotations

import csv
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from io import StringIO
from pathlib import Path
import unittest

from crypto_bot.m3_admission import AdmittedAttempt, PinnedInput
from crypto_bot.m3_chronology import BAR, Opportunity, OpportunityUniverse, SignalRef
from crypto_bot.m3_policy import OpportunityDecision
from crypto_bot.m3_portfolio import PortfolioEvaluationError, run_paired_executable_fold


UTC = timezone.utc
T0 = datetime(2024, 1, 1, tzinfo=UTC)


def _candle_bytes(count=921):
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
    for i in range(count):
        writer.writerow([int((T0 + BAR*i).timestamp()*1000), 100, 100.5, 99.5, 100, 100000])
    return output.getvalue().encode()


def _decision(oid, signal):
    available = signal.available_at if signal is not None else T0 + BAR*817
    return OpportunityDecision(oid, "BTCUSDT", available.isoformat().replace("+00:00", "Z"),
        "2024-W02", "SELECTED" if signal else "ABSTAINED", "synthetic", signal)


def _admission(candles):
    root = Path(__file__).resolve().parents[1]
    config = (root / "configs" / "athena2_portfolio_baseline_v1.json").read_bytes()
    config_doc = json.loads(config)
    canonical_config = json.dumps(config_doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    config_hash = hashlib.sha256(canonical_config).hexdigest()
    signal_bytes = b"synthetic-signal-snapshot"
    protocol = {"source": {"phase4_config_sha256": config_hash},
                "cost_policy": {"phase4_config_sha256": config_hash,
                    "fee_bps_per_side": 5, "slippage_bps_per_side": 5,
                    "funding_bps_per_8h": 1, "max_participation_percent": 5}}
    protocol_bytes = json.dumps(protocol, sort_keys=True).encode()
    items = []
    for key, data in (("signals", signal_bytes), ("candles", candles), ("phase4_config", config)):
        items.append(PinnedInput(key, Path("unused"), hashlib.sha256(data).hexdigest(), len(data), data))
    return AdmittedAttempt("synthetic-attempt", "synthetic-protocol", "synthetic-candidate",
        "a"*64, hashlib.sha256(protocol_bytes).hexdigest(), "b"*64, "c"*64, tuple(items),
        "previously_observed_historical_diagnostic", True, True, False, b"{}", protocol_bytes)


class M3PortfolioTests(unittest.TestCase):
    def test_candidate_and_incumbent_receive_fresh_ledgers_and_identical_cost_stresses(self):
        candles = _candle_bytes()
        admission = _admission(candles)
        t_signal = T0 + BAR*816
        t_available = t_signal + BAR
        signal = SignalRef(7, "breakout", t_signal, t_available, "long", "0.7", "range", "100", "95", "110", False)
        opportunity = Opportunity("opp-7", "BTCUSDT", t_available, (signal,), "2024-W02")
        universe = OpportunityUniverse((opportunity,), ("2024-W02",))
        pair = run_paired_executable_fold(
            admission, (_decision("opp-7", signal),), (_decision("opp-7", None),), universe,
            scored_interval=(t_signal, T0 + BAR*920))
        self.assertIsNot(pair.candidate.ledger, pair.incumbent.ledger)
        self.assertNotEqual(pair.candidate.ledger.account_id, pair.incumbent.ledger.account_id)
        self.assertEqual(len(pair.candidate.ledger.completed_positions), 1)
        self.assertEqual(len(pair.incumbent.ledger.completed_positions), 0)
        self.assertEqual(pair.candidate_opportunities[0].status, "COMPLETED")
        self.assertEqual(pair.incumbent_opportunities[0].status, "ABSTAINED")
        self.assertEqual([name for name, _ in pair.candidate_cost_stress], [
            "fee_10bps_per_side_only", "slippage_10bps_per_side_only", "funding_2bps_per_8h_only"])
        self.assertTrue(all(run.ledger is not pair.candidate.ledger for _, run in pair.candidate_cost_stress))
        self.assertTrue(all(run.ledger is not pair.incumbent.ledger for _, run in pair.incumbent_cost_stress))

    def test_candidate_incumbent_mismatch_fails_before_simulation(self):
        candles = _candle_bytes()
        admission = _admission(candles)
        t_signal = T0 + BAR*816
        signal = SignalRef(7, "breakout", t_signal, t_signal+BAR, "long", "0.7", "range", "100", "95", "110", False)
        opportunity = Opportunity("opp-7", "BTCUSDT", t_signal+BAR, (signal,), "2024-W02")
        universe = OpportunityUniverse((opportunity,), ("2024-W02",))
        with self.assertRaisesRegex(PortfolioEvaluationError, "not paired"):
            run_paired_executable_fold(admission, (_decision("opp-7", signal),),
                (_decision("wrong", None),), universe, scored_interval=(t_signal, T0+BAR*920))


if __name__ == "__main__":
    unittest.main()
