from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
from io import StringIO
from pathlib import Path
import unittest

from crypto_bot.m3_admission import AdmittedAttempt, PinnedInput
from crypto_bot.m3_chronology import BAR, Opportunity, OpportunityUniverse, SignalRef
from crypto_bot.m3_policy import OpportunityDecision
from crypto_bot.m3_predictive import PredictiveEvidenceError, score_predictive_decisions


UTC = timezone.utc
T0 = datetime(2024, 2, 5, tzinfo=UTC)


def stamp(index):
    return T0 + BAR * index


def signal(signal_id, index):
    return SignalRef(signal_id, "breakout", stamp(index), stamp(index + 1), "long",
                     "0.7", "range", "100", "95", "110", False)


def outcome(timestamp, score="1.25"):
    return {"96": {"status": "complete", "bars_observed": 96,
                    "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                    "direction_adjusted_return_pct": score}}


def signal_bytes(*rows):
    output = StringIO(newline="")
    fields = ["signal_id", "timestamp", "available_at", "strategy", "direction",
              "reference_price", "forward_outcomes"]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return output.getvalue().encode()


def candle_bytes(gap_at=None):
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
    for index in range(201):
        if index == gap_at:
            continue
        writer.writerow([int(stamp(index).timestamp() * 1000), 1, 1, 1, 1, 1])
    return output.getvalue().encode()


def admission_with_inputs(signal_data, candle_data):
    inputs = tuple(PinnedInput(key, Path(f"missing-{key}.csv"), hashlib.sha256(data).hexdigest(), len(data), data)
                   for key, data in (("signals", signal_data), ("candles", candle_data)))
    return AdmittedAttempt(
        attempt_id="sha256:"+"a"*64, protocol_id="sha256:"+"b"*64,
        candidate_id="sha256:"+"c"*64, candidate_sha256="d"*64,
        protocol_sha256="e"*64, registry_tip_sha256="f"*64, checkpoint_sha256="0"*64,
        inputs=inputs, research_classification="previously_observed_historical_diagnostic",
        previously_observed=True, eligible_for_evaluation=True,
        authoritative_evaluation_authorized=False,
    )


def fixtures(gap_at=None):
    refs = (signal(1, 0), signal(2, 1), signal(3, 5))
    ops = tuple(Opportunity(f"op-{i}", "BTCUSDT", ref.available_at, (ref,), "2024-W06")
               for i, ref in enumerate(refs, 1))
    universe = OpportunityUniverse(ops, ("2024-W06", "2024-W07"))
    decisions = (
        OpportunityDecision("op-1", "BTCUSDT", refs[0].available_at.isoformat().replace("+00:00", "Z"),
                            "2024-W06", "SELECTED", "selected", refs[0]),
        OpportunityDecision("op-2", "BTCUSDT", refs[1].available_at.isoformat().replace("+00:00", "Z"),
                            "2024-W06", "ABSTAINED", "test_abstention", None),
        OpportunityDecision("op-3", "BTCUSDT", refs[2].available_at.isoformat().replace("+00:00", "Z"),
                            "2024-W06", "SELECTED", "selected", refs[2]),
    )
    labels = signal_bytes(
        {"signal_id": 1, "timestamp": refs[0].signal_timestamp.isoformat(),
         "available_at": refs[0].available_at.isoformat(), "strategy": "breakout", "direction": "long",
         "reference_price": "100", "forward_outcomes": json.dumps(outcome(stamp(96)))},
        {"signal_id": 2, "timestamp": refs[1].signal_timestamp.isoformat(),
         "available_at": refs[1].available_at.isoformat(), "strategy": "breakout", "direction": "long",
         "reference_price": "100", "forward_outcomes": "NOT JSON; abstention must not inspect it"},
        {"signal_id": 3, "timestamp": refs[2].signal_timestamp.isoformat(),
         "available_at": refs[2].available_at.isoformat(), "strategy": "breakout", "direction": "long",
         "reference_price": "100", "forward_outcomes": "NOT JSON; boundary purge must precede label access"},
    )
    return admission_with_inputs(labels, candle_bytes(gap_at)), decisions, universe


class M3PredictiveScoringTests(unittest.TestCase):
    def test_postdecision_scores_use_original_timestamp_and_keep_abstain_purge_rows(self):
        admission, decisions, universe = fixtures()
        evidence = score_predictive_decisions(admission, decisions, universe,
                                              scored_interval=(T0, stamp(97)))
        self.assertEqual([row.opportunity_id for row in evidence], ["op-1", "op-2", "op-3"])
        self.assertEqual([row.status for row in evidence], ["SCORED", "SCORED_ZERO", "PURGED"])
        self.assertEqual(evidence[0].score_decimal, "0.0125")
        self.assertEqual(evidence[1].score_decimal, "0")
        self.assertEqual(evidence[2].reason, "96_bar_label_reaches_or_crosses_scored_boundary")
        # The source row's available_at is one bar later; label timestamp remains t0+96 bars.
        self.assertEqual(decisions[0].available_at, stamp(1).isoformat().replace("+00:00", "Z"))

    def test_label_equal_to_boundary_is_strictly_purged(self):
        admission, decisions, universe = fixtures()
        evidence = score_predictive_decisions(admission, decisions[:1],
            OpportunityUniverse(universe.opportunities[:1], universe.utc_week_keys),
            scored_interval=(T0, stamp(96)))
        self.assertEqual(evidence[0].status, "PURGED")
        self.assertIsNone(evidence[0].score_decimal)

    def test_gap_censors_selected_label_without_removing_opportunity(self):
        admission, decisions, universe = fixtures(gap_at=50)
        evidence = score_predictive_decisions(admission, decisions[:1],
            OpportunityUniverse(universe.opportunities[:1], universe.utc_week_keys),
            scored_interval=(T0, stamp(97)))
        self.assertEqual(evidence[0].opportunity_id, "op-1")
        self.assertEqual(evidence[0].status, "CENSORED")

    def test_decision_vector_must_match_shared_universe_exactly(self):
        admission, decisions, universe = fixtures()
        with self.assertRaisesRegex(PredictiveEvidenceError, "every shared opportunity"):
            score_predictive_decisions(admission, decisions[:1], universe,
                                       scored_interval=(T0, stamp(97)))

    def test_corrupted_admitted_snapshot_fails_closed(self):
        admission, decisions, universe = fixtures()
        from dataclasses import replace
        corrupted = replace(admission, inputs=(replace(admission.inputs[0], content=b"changed"), *admission.inputs[1:]))
        with self.assertRaisesRegex(PredictiveEvidenceError, "byte snapshot failed integrity"):
            score_predictive_decisions(corrupted, decisions, universe, scored_interval=(T0, stamp(97)))


if __name__ == "__main__":
    unittest.main()

