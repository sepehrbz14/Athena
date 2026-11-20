from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import unittest

from crypto_bot import experiment_registry as m1
from crypto_bot.m3_admission import AdmittedAttempt
from crypto_bot.m3_chronology import Opportunity, OpportunityUniverse, SignalRef
from crypto_bot.m3_policy import (
    PolicyError, interpret_admitted_candidate, interpret_frozen_incumbent,
)


UTC = timezone.utc
T0 = datetime(2024, 2, 5, tzinfo=UTC)


def signal(signal_id, strategy, direction, *, at=T0, confidence="0.7", regime="range", incumbent=False):
    return SignalRef(signal_id, strategy, at, at + timedelta(minutes=15), direction,
                     confidence, regime, "100", "95", "110", incumbent)


def universe_fixture():
    first = Opportunity("opp-1", "BTCUSDT", T0 + timedelta(minutes=15), (
        signal(1, "breakout", "long", incumbent=True),
        signal(2, "momentum", "short", incumbent=False),
    ), "2024-W06")
    second_time = T0 + timedelta(days=1)
    second = Opportunity("opp-2", "BTCUSDT", second_time + timedelta(minutes=15), (
        signal(3, "momentum", "long", at=second_time, incumbent=False),
    ), "2024-W07")
    return OpportunityUniverse((first, second), ("2024-W06", "2024-W07", "2024-W08"))


def candidate_bytes(family, policy, members=("breakout", "momentum")):
    candidate = {
        "schema_version": "athena.evolution.candidate.v1",
        "candidate_id": "sha256:" + "0" * 64,
        "id_algorithm": "sha256_rfc8785_jcs_semantic_payload_v1",
        "candidate_revision": 1,
        "experiment_id": "sha256:" + "1" * 64,
        "family": family,
        "created_at_utc": "2026-11-20T00:00:00Z",
        "baseline": {
            "source_commit": "a" * 40, "phase4_simulator_commit": "b" * 40,
            "phase4_publication_commit": "c" * 40, "strategy_source_sha256": "d" * 64,
            "regime_source_sha256": "e" * 64, "execution_source_sha256": "f" * 64,
            "phase4_config_sha256": "0" * 64, "frozen_input_manifest_sha256": "1" * 64,
        },
        "parents": [{"reference_type": "incumbent_component", "reference_id": "baseline",
                     "relation": "baseline"}],
        "strategy_members": list(members), "policy": policy, "research_only": True,
    }
    candidate["candidate_id"] = m1.compute_candidate_id(candidate)
    return json.dumps(candidate, separators=(",", ":")).encode()


def admitted(raw):
    candidate = json.loads(raw)
    return AdmittedAttempt(
        attempt_id="sha256:" + "2" * 64, protocol_id=candidate["experiment_id"],
        candidate_id=candidate["candidate_id"], candidate_sha256=hashlib.sha256(raw).hexdigest(),
        protocol_sha256="3" * 64, registry_tip_sha256="4" * 64, checkpoint_sha256="5" * 64,
        inputs=(), research_classification="previously_observed_historical_diagnostic",
        previously_observed=True, eligible_for_evaluation=True,
        authoritative_evaluation_authorized=False,
    )


class M3PolicyTests(unittest.TestCase):
    def test_candidate_and_incumbent_emit_same_ids_and_keep_abstentions(self):
        universe = universe_fixture()
        raw = candidate_bytes("signal_combination", {
            "kind": "signal_combination", "rule": "majority_direction",
            "members": ["breakout", "momentum"], "tie_policy": "abstain",
            "signal_resolution": "retain_originating_component_stop_target_and_signal_fields",
        })
        candidate = interpret_admitted_candidate(admitted(raw), raw, universe)
        incumbent = interpret_frozen_incumbent(universe)
        self.assertEqual([d.opportunity_id for d in candidate], [d.opportunity_id for d in incumbent])
        self.assertEqual(len(candidate), len(universe.opportunities))
        self.assertEqual([d.status for d in candidate], ["ABSTAINED", "SELECTED"])
        self.assertEqual([d.reason for d in candidate], ["direction_tie", "selected_majority"])
        self.assertEqual(candidate[0].utc_week, "2024-W06")
        self.assertEqual(incumbent[0].selected_signal.signal_id, 1)
        self.assertEqual(candidate[1].selected_signal.signal_id, 3)
        self.assertIs(candidate[1].selected_signal, universe.opportunities[1].signals[0])
        self.assertIn("2024-W08", universe.utc_week_keys)  # empty calendar week retained

    def test_unanimous_requires_every_member_and_preserves_source_signal(self):
        universe = universe_fixture()
        raw = candidate_bytes("signal_combination", {
            "kind": "signal_combination", "rule": "unanimous_direction",
            "members": ["breakout", "momentum"], "tie_policy": "abstain",
            "signal_resolution": "retain_originating_component_stop_target_and_signal_fields",
        })
        decisions = interpret_admitted_candidate(admitted(raw), raw, universe)
        self.assertEqual(decisions[0].reason, "direction_disagreement")
        self.assertEqual(decisions[1].reason, "missing_member_signal")
        self.assertIsNone(decisions[0].selected_signal)

    def test_highest_existing_confidence_tie_abstains_and_unique_high_wins(self):
        universe = universe_fixture()
        tie_raw = candidate_bytes("signal_combination", {
            "kind": "signal_combination", "rule": "highest_existing_confidence",
            "members": ["breakout", "momentum"], "tie_policy": "abstain",
            "signal_resolution": "retain_originating_component_stop_target_and_signal_fields",
        })
        tied = interpret_admitted_candidate(admitted(tie_raw), tie_raw, universe)
        self.assertEqual(tied[0].reason, "confidence_tie")
        unique_universe = OpportunityUniverse((replace(universe.opportunities[0], signals=(
            universe.opportunities[0].signals[0],
            replace(universe.opportunities[0].signals[1], confidence="0.8"),
        )),), ("2024-W06",))
        selected = interpret_admitted_candidate(admitted(tie_raw), tie_raw, unique_universe)
        self.assertEqual(selected[0].selected_signal.signal_id, 2)

    def test_incumbent_ensemble_rule_uses_only_frozen_selection_flags(self):
        universe = universe_fixture()
        raw = candidate_bytes("signal_combination", {
            "kind": "signal_combination", "rule": "incumbent_ensemble",
            "members": ["breakout", "momentum"], "tie_policy": "abstain",
            "signal_resolution": "retain_originating_component_stop_target_and_signal_fields",
        })
        decisions = interpret_admitted_candidate(admitted(raw), raw, universe)
        self.assertEqual(decisions[0].selected_signal.signal_id, 1)
        self.assertIsNone(decisions[1].selected_signal)

    def test_unregistered_or_not_validated_attempt_is_not_interpreted(self):
        universe = universe_fixture()
        raw = candidate_bytes("signal_combination", {
            "kind": "signal_combination", "rule": "majority_direction",
            "members": ["breakout", "momentum"], "tie_policy": "abstain",
            "signal_resolution": "retain_originating_component_stop_target_and_signal_fields",
        })
        invalid_admission = replace(admitted(raw), eligible_for_evaluation=False)
        with self.assertRaisesRegex(PolicyError, "admitted M1-validated"):
            interpret_admitted_candidate(invalid_admission, raw, universe)
        with self.assertRaisesRegex(PolicyError, "identity or research-only"):
            interpret_admitted_candidate(replace(admitted(raw), protocol_id="sha256:"+"9"*64), raw, universe)

    def test_regime_specialization_and_declared_priority_only_select_existing_rows(self):
        universe = universe_fixture()
        spec_raw = candidate_bytes("regime_specialization", {
            "kind": "regime_specialization",
            "classifier": "repository_regime_classifier_pinned_by_baseline_hash",
            "regime_allowlist": {"range": ["momentum", "breakout"]},
            "unknown_policy": "abstain",
        })
        spec = interpret_admitted_candidate(admitted(spec_raw), spec_raw, universe)
        self.assertEqual(spec[0].selected_signal.signal_id, 2)
        self.assertEqual(spec[1].selected_signal.signal_id, 3)
        self.assertTrue(all(d.selected_signal in op.signals for d, op in zip(spec, universe.opportunities)))

        priority_raw = candidate_bytes("regime_selection_allocation", {
            "kind": "regime_selection_allocation",
            "classifier": "repository_regime_classifier_pinned_by_baseline_hash",
            "regime_priority": {"range": ["breakout", "momentum"]},
            "risk_budget_mode": "frozen_phase4", "unknown_policy": "abstain",
        })
        priority = interpret_admitted_candidate(admitted(priority_raw), priority_raw, universe)
        self.assertEqual(priority[0].selected_signal.signal_id, 1)

    def test_unknown_regime_and_no_eligible_signal_abstain(self):
        op = Opportunity("opp-u", "BTCUSDT", T0, (signal(1, "breakout", "long", regime="unknown"),), "2024-W06")
        universe = OpportunityUniverse((op,), ("2024-W06",))
        raw = candidate_bytes("regime_selection_allocation", {
            "kind": "regime_selection_allocation",
            "classifier": "repository_regime_classifier_pinned_by_baseline_hash",
            "regime_priority": {"range": ["breakout"]},
            "risk_budget_mode": "frozen_phase4", "unknown_policy": "abstain",
        })
        decision = interpret_admitted_candidate(admitted(raw), raw, universe)[0]
        self.assertEqual(decision.status, "ABSTAINED")
        self.assertEqual(decision.reason, "unknown_regime")

    def test_malformed_conflicting_regime_or_incumbent_flags_fail_closed(self):
        op = Opportunity("opp-r", "BTCUSDT", T0, (
            signal(1, "breakout", "long", regime="range"),
            signal(2, "momentum", "short", regime="bull_trend"),
        ), "2024-W06")
        universe = OpportunityUniverse((op,), ("2024-W06",))
        raw = candidate_bytes("regime_selection_allocation", {
            "kind": "regime_selection_allocation",
            "classifier": "repository_regime_classifier_pinned_by_baseline_hash",
            "regime_priority": {"range": ["breakout"]},
            "risk_budget_mode": "frozen_phase4", "unknown_policy": "abstain",
        })
        with self.assertRaisesRegex(PolicyError, "disagree on the frozen regime"):
            interpret_admitted_candidate(admitted(raw), raw, universe)
        missing_flag = OpportunityUniverse((replace(op, signals=(
            replace(op.signals[0], incumbent_selected=None), op.signals[1],
        )),), ("2024-W06",))
        with self.assertRaisesRegex(PolicyError, "flag is missing"):
            interpret_frozen_incumbent(missing_flag)

    def test_candidate_bytes_must_match_admission_snapshot(self):
        raw = candidate_bytes("signal_combination", {
            "kind": "signal_combination", "rule": "majority_direction",
            "members": ["breakout", "momentum"], "tie_policy": "abstain",
            "signal_resolution": "retain_originating_component_stop_target_and_signal_fields",
        })
        with self.assertRaisesRegex(PolicyError, "differ from the admitted"):
            interpret_admitted_candidate(admitted(raw), raw + b" ", universe_fixture())


if __name__ == "__main__":
    unittest.main()

