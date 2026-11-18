"""Contract and failure-path tests for the immutable Milestone 1 registry."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import contextmanager

from crypto_bot import experiment_registry as registry


ZERO = "0" * 64
COMMIT = "1" * 40
PHASE4_SIM = "4" * 40
PHASE4_PUB = "6" * 40


def protocol_document(attempt_count: int = 1) -> dict:
    return registry.prepare_protocol_identity({
        "schema_version": "athena.evolution.experiment_protocol.v1",
        "id_algorithm": "sha256_rfc8785_jcs_semantic_payload_v1",
        "protocol_revision": 1,
        "created_at_utc": "2026-11-18T00:00:00Z",
        "locked_at_utc": "2026-11-18T00:00:00Z",
        "source": {
            "commit": COMMIT, "worktree_clean": True,
            "strategy_source_sha256": ZERO, "regime_source_sha256": "1" * 64,
            "execution_source_sha256": "2" * 64, "phase4_config_sha256": "3" * 64,
            "schema_bundle_sha256": registry.schema_bundle_sha256(),
        },
        "protocol_parent_ids": [],
        "research_classification": "previously_observed_historical_diagnostic",
        "hypothesis": {
            "statement": "The frozen strategy has positive robust predictive and executable performance.",
            "primary_estimand": "robust_executable_absolute_weekly_return",
            "absolute_edge_required": True,
        },
        "confirmatory_estimands": [
            "predictive_absolute_weekly_score", "predictive_paired_weekly_delta",
            "robust_executable_absolute_weekly_return", "executable_paired_weekly_delta",
        ],
        "families": ["regime_specialization", "signal_combination", "regime_selection_allocation"],
        "limits": {
            "max_candidates_per_family": 12, "max_families": 3, "max_candidates_total": 36,
            "budget_scope": "cumulative_persistent_search_family_across_protocols_commits_and_reruns",
            "candidate_set_sealed_before_outcomes": True,
        },
        "candidate_set": {
            "attempt_count": attempt_count, "attempt_manifest_sha256": ZERO,
            "incumbent_control_id": "frozen-incumbent-v1", "registered_before_evaluation": True,
            "includes_invalid_and_unevaluated_attempts": True,
        },
        "data": {
            "dataset_id": "frozen-phase5", "signals_sha256": "4" * 64,
            "candles_sha256": "5" * 64, "phase5_manifest_sha256": "6" * 64,
            "observed_through_utc": "2026-08-19T23:59:59Z", "previously_examined": True,
            "availability_clock": "signal_available_at_utc",
        },
        "evidence_lanes": ["predictive_signal", "independent_counterfactual", "executable_portfolio"],
        "cost_policy": {
            "phase4_config_sha256": "3" * 64, "fee_policy": "frozen_phase4_per_side",
            "slippage_policy": "frozen_phase4_adverse_fill",
            "funding_policy": "frozen_phase4_elapsed_time_and_sign_convention",
            "participation_policy": "frozen_phase4_max_participation", "fee_bps_per_side": 5,
            "slippage_bps_per_side": 5, "funding_bps_per_8h": 1, "max_participation_percent": 5,
            "stress_scenarios": ["fee_10bps_per_side_only", "slippage_10bps_per_side_only", "funding_2bps_per_8h_only"],
            "fee_stress_bps_per_side": 10, "slippage_stress_bps_per_side": 10,
            "funding_stress_bps_per_8h": 2, "zero_cost_can_qualify": False,
        },
        "candidate_vs_incumbent": {key: True for key in (
            "mandatory", "same_folds", "same_input_hashes", "same_warmup_and_eligible_time_universe",
            "same_costs", "same_execution_model", "same_phase4_config", "paired_calendar_weeks", "independent_ledgers",
        )},
        "chronology": {
            "clock": "UTC_signal_availability", "fold_method": "nested_expanding_rolling_origin",
            "min_training_months": 24, "inner_validation_months": 6, "outer_test_months": 6,
            "outer_step_months": 6, "minimum_eligible_outer_folds": 6,
            "nonoverlapping_outer_tests": True, "max_label_horizon_bars": 96,
            "max_position_horizon_bars": 17664, "embargo_bars": 96,
            "terminal_entry_blackout_bars": 96, "purge_policy": "purge_label_or_trade_outcome_crossing_boundary",
            "warmup_policy": "816_prior_contiguous_15m_bars_reset_after_gap_causal_only_unscored",
            "state_reset": "fresh_account_orders_and_safety_state_per_fold",
            "fold_end_policy": "frozen_phase4_force_close_at_end_with_costs_reported", "folds": [],
        },
        "preprocessing": {"allowed_transforms": [], "fit_scope": "training_fold_only", "full_series_fit_allowed": False},
        "statistics": {
            "alpha": 0.05, "multiplicity_method": "holm_familywise",
            "correction_family": "persistent_search_family_all_linked_protocols_all_families_all_attempts_four_hypotheses_each",
            "all_attempts_count": True,
            "no_valid_p_value_policy": "conservative_p_equals_1_placeholder; attempt remains in denominator and cannot qualify",
            "bootstrap": {"method": "non_circular_moving_block_bootstrap", "block_unit": "UTC_calendar_week",
                "block_length_weeks": 4, "iterations": 100000, "interval": "95_percentile", "paired_candidate_control": True},
            "one_sided_p_value_method": "centered_null_moving_block_bootstrap_plus_one_tail_count_divided_by_100001;_Holm_stepdown",
            "minimum_samples": {"predictive_signals": 200, "predictive_occupied_weeks": 30,
                "counterfactual_trades": 100, "counterfactual_occupied_weeks": 30,
                "executable_positions": 100, "executable_occupied_weeks": 30,
                "executable_outer_folds": 6, "regime_observations": 30, "regime_occupied_weeks": 12},
            "confirmatory_hypotheses_per_attempt": 4,
            "absolute_profitability_gate": "holm_adjusted_robust_executable_absolute_lower_95_bound_above_zero",
            "relative_comparison_also_required": True,
            "paired_improvement_gate": "holm_adjusted_baseline_cost_executable_candidate_minus_incumbent_lower_95_bound_above_zero",
            "max_single_fold_share_of_positive_total": 0.5, "no_extra_interval_width_cutoff": True,
        },
        "seed_policy": {"master_seed": 20261118,
            "derivation": "sha256(UTF8 protocol_id|attempt_id|fold_id|metric_id|master_seed); first 8 bytes unsigned big-endian",
            "decision_randomness_allowed": False},
        "holdout_policy": {"current_data_through_2026_08_19_is_untouched": False, "status": "pending",
            "minimum_future_weeks": 52, "trailing_label_tail_bars": 96,
            "boundary_lock_rule": "exact_UTC_start_end_frozen_before_accrual;_no_sample_or_outcome_driven_extension",
            "single_confirmatory_opening": True, "inspection_consumes_holdout": True,
            "generator_access": "never", "candidate_selection_access": "never", "ordinary_evaluator_access": "never",
            "confirmatory_evaluator_access": "one_shot_read_only_after_protocol_and_candidate_hash_lock"},
        "resource_limits": {"max_wall_seconds_per_attempt": 1800, "max_wall_seconds_search_family": 86400,
            "max_memory_mb": 8192, "max_output_bytes_per_attempt": 536870912,
            "max_output_bytes_search_family": 10737418240, "max_workers": 1, "offline_only": True},
        "output_policy": {"root_template": "reports/evolution_research/<UTC-date>/<protocol-id>/",
            "create_exclusively": True, "overwrite_allowed": False, "hash_chain_events": True},
        "research_only": True,
    }, attempt_count=attempt_count)


def candidate_bytes(protocol_id: str, family: str = "regime_specialization") -> bytes:
    value = {
        "schema_version": "athena.evolution.candidate.v1",
        "id_algorithm": "sha256_rfc8785_jcs_semantic_payload_v1", "candidate_revision": 1,
        "experiment_id": protocol_id, "family": family, "created_at_utc": "2026-11-18T00:00:00Z",
        "baseline": {"source_commit": COMMIT, "phase4_simulator_commit": PHASE4_SIM,
            "phase4_publication_commit": PHASE4_PUB, "strategy_source_sha256": ZERO,
            "regime_source_sha256": "1" * 64, "execution_source_sha256": "2" * 64,
            "phase4_config_sha256": "3" * 64, "frozen_input_manifest_sha256": "7" * 64},
        "parents": [{"reference_type": "incumbent_component", "reference_id": "trend_following", "relation": "baseline"}],
        "strategy_members": ["trend_following"],
        "policy": {"kind": "regime_specialization", "classifier": "repository_regime_classifier_pinned_by_baseline_hash",
            "regime_allowlist": {"bull_trend": ["trend_following"]}, "unknown_policy": "abstain"},
        "research_only": True,
    }
    value["candidate_id"] = registry.compute_candidate_id(value)
    return registry.canonical_json_bytes(value)


@contextmanager
def db_connection(path: Path):
    connection = sqlite3.connect(path)
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


class ExperimentRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "registry"
        self.registry = registry.ExperimentRegistry.create(self.root)
        self.protocol = protocol_document()
        raw = candidate_bytes(self.protocol["protocol_id"])
        self.protocol_id, self.attempts, self.manifest_hash = self.registry.register_protocol_with_attempts(
            self.protocol, [registry.AttemptSubmission("regime_specialization", raw)]
        )
        self.attempt_id = self.attempts[0]

    def tearDown(self):
        self.temp.cleanup()

    def test_candidate_and_seed_ids_are_deterministic(self):
        raw = candidate_bytes(self.protocol_id)
        candidate = json.loads(raw)
        self.assertEqual(candidate["candidate_id"], registry.compute_candidate_id(candidate))
        self.assertEqual(registry.derive_seed(self.protocol_id, self.attempt_id, "outer-1", "return"),
                         registry.derive_seed(self.protocol_id, self.attempt_id, "outer-1", "return"))
        self.assertNotEqual(registry.derive_seed(self.protocol_id, self.attempt_id, "outer-1", "return"),
                            registry.derive_seed(self.protocol_id, self.attempt_id, "outer-2", "return"))
        self.assertEqual(registry.canonical_json_bytes({"b": 2, "a": 1}), b'{"a":1,"b":2}')

    def test_golden_protocol_candidate_attempt_and_seed_reconstruct(self):
        protocol = protocol_document()
        self.assertEqual(protocol["protocol_id"],
            "sha256:9b7f82d541c910cfa6786189e488f9dd9b514ee3b6179b6beaa430aff24aafb5")
        raw = candidate_bytes(protocol["protocol_id"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
            "2b881495cb1efb0b45f7e3409d308578c510409c43bfa84d8c15e62380c29f79")
        candidate = json.loads(raw)
        self.assertEqual(candidate["candidate_id"],
            "sha256:6dccf9c39cc9f6a28e70461f108ac47462c44928890cecbe12b331b7a5e4e102")
        attempt = registry.compute_attempt_id(protocol["protocol_id"], 1, "regime_specialization",
                                              hashlib.sha256(raw).hexdigest())
        self.assertEqual(attempt,
            "sha256:67289e7b7c86cbaa7ace9f7e9c10ea0a7f06180315306bf1a75e0f439e6569ee")
        self.assertEqual(registry.derive_seed(protocol["protocol_id"], attempt, "outer-1", "net"),
                         18292073701005286295)

    def test_schema_bundle_hash_is_stable_for_lf_and_crlf_checkouts(self):
        source = registry._M0_SCHEMA_DIR
        with tempfile.TemporaryDirectory(dir=self.temp.name) as temp_dir:
            lf = Path(temp_dir) / "lf"
            crlf = Path(temp_dir) / "crlf"
            lf.mkdir()
            crlf.mkdir()
            for name in registry._SCHEMA_NAMES:
                raw = (source / name).read_bytes().replace(b"\r\n", b"\n")
                (lf / name).write_bytes(raw)
                (crlf / name).write_bytes(raw.replace(b"\n", b"\r\n"))
            self.assertEqual(registry.schema_bundle_sha256(lf), registry.schema_bundle_sha256(crlf))

    def test_registration_is_sealed_before_validation_and_records_identity(self):
        events = self.registry.events()
        self.assertEqual([e["event_type"] for e in events], [
            "PROTOCOL_PRE_REGISTERED", "CANDIDATE_ATTEMPT_REGISTERED", "CANDIDATE_SET_SEALED"])
        self.assertEqual(self.registry.attempt_states()[self.attempt_id], "SUBMITTED")
        self.assertEqual(self.registry.attempt_bytes(self.attempt_id), candidate_bytes(self.protocol_id))
        self.assertEqual(self.registry.verify().attempt_count, 1)
        self.assertEqual(self.protocol["source"]["commit"], COMMIT)
        self.assertEqual(len(self.manifest_hash), 64)

    def test_valid_transition_sequence_and_reopen_reconstruction(self):
        self.assertEqual(self.registry.validate_attempt(self.attempt_id), "VALIDATED")
        self.registry.record_evaluation_started(self.attempt_id, ["outer-1"], ["net"])
        started = next(event for event in self.registry.events() if event["event_type"] == "EVALUATION_STARTED")
        seed_record = started["payload"]["seed_records"][0]
        self.assertEqual(set(seed_record), {"fold_id", "metric_id", "seed_u64_decimal"})
        expected_seed = registry.derive_seed(self.protocol_id, self.attempt_id, "outer-1", "net")
        self.assertEqual(seed_record["seed_u64_decimal"], str(expected_seed))
        self.assertGreater(expected_seed, 2**53)
        self.assertEqual(set(started["payload"]["dataset_hashes"]), {
            "signals_sha256", "candles_sha256", "phase5_manifest_sha256"})
        self.assertEqual(set(started["payload"]["configuration_hashes"]), {
            "strategy_source_sha256", "regime_source_sha256", "execution_source_sha256",
            "phase4_config_sha256", "schema_bundle_sha256"})
        self.assertEqual(self.registry.complete_evaluation(self.attempt_id, result_sha256=ZERO,
            wall_seconds=1, peak_memory_mb=2, artifact_bytes=3,
            confirmatory_p_values={name: 0.5 for name in registry._CONFIRMATORY_HYPOTHESES}), "COMPLETED")
        reopened = registry.ExperimentRegistry(self.root)
        self.assertEqual(reopened.attempt_states()[self.attempt_id], "COMPLETED")
        rows = reopened.multiplicity_entries()
        self.assertEqual(len(rows), 4)
        self.assertEqual({row["p_value"] for row in rows}, {0.5})
        self.assertTrue(reopened.verify().valid)
        standalone = registry.verify_event_stream(reopened.events(), expected_tip_sha256=reopened.verify().tip_sha256)
        self.assertEqual(standalone.attempt_count, 1)

    def test_invalid_spec_is_counted_with_four_conservative_placeholders(self):
        bad = Path(self.temp.name) / "bad"
        r = registry.ExperimentRegistry.create(bad)
        p = protocol_document()
        _, ids, _ = r.register_protocol_with_attempts(p, [registry.AttemptSubmission("regime_specialization", b"{")])
        self.assertEqual(r.validate_attempt(ids[0]), "INVALID_CANDIDATE")
        rows = r.multiplicity_entries()
        self.assertEqual(len(rows), 4)
        self.assertEqual({row["p_value"] for row in rows}, {1.0})
        self.assertTrue(all(row["placeholder"] for row in rows))

    def test_interrupted_registration_rolls_back_all_protocol_attempts_and_manifest(self):
        for stop in (1, 2, 3):
            path = Path(self.temp.name) / f"interrupted-{stop}"
            r = registry.ExperimentRegistry.create(path)
            p = protocol_document()
            with self.assertRaises(RuntimeError):
                r.register_protocol_with_attempts(p, [registry.AttemptSubmission(
                    "regime_specialization", candidate_bytes(p["protocol_id"]) )], fault_after_event=stop)
            self.assertEqual(r.events(), [])
            with db_connection(r.database_path) as db:
                self.assertEqual(db.execute("select count(*) from attempt_specs").fetchone()[0], 0)
            self.assertTrue(r.verify().valid)

    def test_duplicate_protocol_and_existing_registry_path_are_refused(self):
        with self.assertRaises(registry.RegistryError):
            registry.ExperimentRegistry.create(self.root)
        with self.assertRaises(registry.DuplicateIdentifierError):
            self.registry.register_protocol_with_attempts(self.protocol, [registry.AttemptSubmission(
                "regime_specialization", candidate_bytes(self.protocol_id))])

    def test_duplicate_candidate_content_counts_as_distinct_attempt_and_family_cap_never_resets(self):
        path = Path(self.temp.name) / "cumulative"
        r = registry.ExperimentRegistry.create(path)
        p1 = protocol_document(12)
        same = candidate_bytes(p1["protocol_id"])
        _, ids, _ = r.register_protocol_with_attempts(p1, [
            registry.AttemptSubmission("regime_specialization", same) for _ in range(12)
        ])
        self.assertEqual(len(set(ids)), 12)
        self.assertEqual(len({json.loads(r.attempt_bytes(item))["candidate_id"] for item in ids}), 1)
        p2 = protocol_document(1)
        p2["protocol_revision"] = 2
        p2["protocol_parent_ids"] = [p1["protocol_id"]]
        p2 = registry.prepare_protocol_identity(p2, attempt_count=1)
        with self.assertRaises(registry.SearchFamilyLimitError):
            r.register_protocol_with_attempts(p2, [registry.AttemptSubmission(
                "regime_specialization", candidate_bytes(p2["protocol_id"]))])
        self.assertEqual(r.verify().attempt_count, 12)

    def test_integrity_verifier_rejects_forbidden_holdout_event_even_with_valid_chain(self):
        events = self.registry.events()
        raw = registry._event_hashes(
            sequence=len(events) + 1, event_time_utc="2026-11-18T00:00:01Z",
            protocol_id=self.protocol_id, event_type="HOLDOUT_RELEASED", actor_type="custodian",
            payload={"access": "forbidden"}, previous_event_sha256=events[-1]["event_sha256"],
        )
        with db_connection(self.registry.database_path) as db:
            db.execute(
                "insert into events(sequence,event_id,event_sha256,protocol_id,event_type,event_json) values(?,?,?,?,?,?)",
                (raw["sequence"], raw["event_id"], raw["event_sha256"], raw["protocol_id"],
                 raw["event_type"], registry.canonical_json_bytes(raw)),
            )
        with self.assertRaises(registry.RegistryIntegrityError):
            registry.verify_registry(self.root)
        # The event-stream verifier remains generic and checks cryptographic
        # structure; registry policy validation rejects the holdout action.
        self.assertTrue(registry.verify_event_stream([*events, raw]).valid)

    def test_database_triggers_prevent_update_and_delete(self):
        with db_connection(self.registry.database_path) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("update events set event_type='X' where sequence=1")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("delete from events where sequence=1")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("update attempt_specs set spec_bytes=x'' where attempt_id=?", (self.attempt_id,))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("delete from attempt_specs where attempt_id=?", (self.attempt_id,))

    def test_tampering_and_missing_attempt_rows_are_detected(self):
        with db_connection(self.registry.database_path) as db:
            event = json.loads(db.execute("select event_json from events where sequence=2").fetchone()[0])
            event["payload"]["assigned_family"] = "signal_combination"
            db.execute("pragma recursive_triggers=on")
            db.execute("drop trigger events_no_update")
            db.execute("update events set event_json=? where sequence=2", (json.dumps(event).encode(),))
        with self.assertRaises(registry.RegistryIntegrityError):
            self.registry.verify()

        path = Path(self.temp.name) / "missing"
        r = registry.ExperimentRegistry.create(path)
        p = protocol_document()
        _, ids, _ = r.register_protocol_with_attempts(p, [registry.AttemptSubmission(
            "regime_specialization", candidate_bytes(p["protocol_id"]))])
        with db_connection(r.database_path) as db:
            db.execute("drop trigger attempt_specs_no_delete")
            db.execute("delete from attempt_specs where attempt_id=?", (ids[0],))
        with self.assertRaises(registry.RegistryIntegrityError):
            r.verify()

    def test_invalid_transitions_rejected_and_skipped_attempt_counted(self):
        with self.assertRaises(registry.InvalidTransitionError):
            self.registry.record_evaluation_started(self.attempt_id, [], [])
        self.registry.skip_attempt(self.attempt_id, "campaign stopped before evaluation")
        with self.assertRaises(registry.InvalidTransitionError):
            self.registry.validate_attempt(self.attempt_id)
        self.assertEqual(self.registry.attempt_states()[self.attempt_id], "UNEVALUATED")
        self.assertEqual(len(self.registry.multiplicity_entries()), 4)

    def test_no_qualifying_candidate_is_a_valid_terminal_outcome(self):
        self.assertEqual(self.registry.validate_attempt(self.attempt_id), "VALIDATED")
        self.registry.record_evaluation_started(self.attempt_id, ["f1"], ["m1"])
        self.registry.complete_evaluation(self.attempt_id, result_sha256=ZERO,
            wall_seconds=1, peak_memory_mb=1, artifact_bytes=1)
        self.registry.reject_attempt(self.attempt_id, "ABSOLUTE_EDGE_UNPROVEN")
        outcome = self.registry.close_with_no_qualifying_candidate()
        self.assertEqual(outcome["event_type"], "NO_QUALIFYING_CANDIDATE")
        self.assertTrue(self.registry.verify().valid)

    def test_technical_and_unevaluated_attempts_block_valid_no_qualifier(self):
        self.registry.skip_attempt(self.attempt_id, "not run")
        with self.assertRaises(registry.InvalidTransitionError):
            self.registry.close_with_no_qualifying_candidate()

    def test_holdout_access_is_denied_and_protocol_cannot_evaluate_holdout(self):
        with self.assertRaises(registry.HoldoutAccessError):
            self.registry.append_holdout_event("HOLDOUT_RELEASED")
        p = protocol_document()
        p["research_classification"] = "prospective_holdout_confirmatory"
        p = registry.prepare_protocol_identity(p, attempt_count=1)
        r = registry.ExperimentRegistry.create(Path(self.temp.name) / "holdout")
        _, ids, _ = r.register_protocol_with_attempts(p, [registry.AttemptSubmission(
            "regime_specialization", candidate_bytes(p["protocol_id"]))])
        self.assertEqual(r.validate_attempt(ids[0]), "VALIDATED")
        with self.assertRaises(registry.HoldoutAccessError):
            r.record_evaluation_started(ids[0], ["f1"], ["m1"])

    def test_resource_cap_violation_is_a_counted_technical_failure(self):
        self.registry.validate_attempt(self.attempt_id)
        self.registry.record_evaluation_started(self.attempt_id, ["f1"], ["m1"])
        status = self.registry.complete_evaluation(self.attempt_id, result_sha256=ZERO,
            wall_seconds=1801, peak_memory_mb=1, artifact_bytes=1)
        self.assertEqual(status, "TECHNICAL_EVALUATION_FAILURE")
        self.assertEqual(self.registry.attempt_states()[self.attempt_id], "TECHNICAL_EVALUATION_FAILURE")
        self.assertEqual({r["p_value"] for r in self.registry.multiplicity_entries()}, {1.0})

    def test_frozen_numeric_decisions_are_schema_constants(self):
        schema = json.loads((registry._M0_SCHEMA_DIR / "experiment_protocol.schema.json").read_text())
        properties = schema["properties"]
        self.assertEqual(schema["properties"]["limits"]["properties"]["max_candidates_total"]["const"], 36)
        self.assertEqual(properties["limits"]["properties"]["max_candidates_per_family"]["const"], 12)
        self.assertEqual(properties["limits"]["properties"]["max_families"]["const"], 3)
        chronology = properties["chronology"]["properties"]
        self.assertEqual([chronology[key]["const"] for key in (
            "min_training_months", "inner_validation_months", "outer_test_months", "outer_step_months",
            "minimum_eligible_outer_folds", "max_label_horizon_bars", "max_position_horizon_bars",
            "embargo_bars", "terminal_entry_blackout_bars")], [24, 6, 6, 6, 6, 96, 17664, 96, 96])
        self.assertIn("816_prior_contiguous_15m_bars", chronology["warmup_policy"]["const"])
        self.assertEqual(schema["properties"]["chronology"]["properties"]["embargo_bars"]["const"], 96)
        statistics = properties["statistics"]["properties"]
        self.assertEqual(statistics["alpha"]["const"], 0.05)
        self.assertEqual(statistics["bootstrap"]["properties"]["block_length_weeks"]["const"], 4)
        self.assertEqual(statistics["bootstrap"]["properties"]["iterations"]["const"], 100000)
        self.assertEqual(statistics["minimum_samples"]["properties"], {
            "predictive_signals": {"const": 200}, "predictive_occupied_weeks": {"const": 30},
            "counterfactual_trades": {"const": 100}, "counterfactual_occupied_weeks": {"const": 30},
            "executable_positions": {"const": 100}, "executable_occupied_weeks": {"const": 30},
            "executable_outer_folds": {"const": 6}, "regime_observations": {"const": 30},
            "regime_occupied_weeks": {"const": 12},
        })
        costs = properties["cost_policy"]["properties"]
        self.assertEqual([costs[key]["const"] for key in (
            "fee_bps_per_side", "slippage_bps_per_side", "funding_bps_per_8h", "max_participation_percent",
            "fee_stress_bps_per_side", "slippage_stress_bps_per_side", "funding_stress_bps_per_8h")],
            [5, 5, 1, 5, 10, 10, 2])
        self.assertEqual(properties["seed_policy"]["properties"]["master_seed"]["const"], 20261118)
        holdout = properties["holdout_policy"]["properties"]
        self.assertEqual([holdout[key]["const"] for key in ("minimum_future_weeks", "trailing_label_tail_bars")], [52, 96])
        resources = properties["resource_limits"]["properties"]
        self.assertEqual([resources[key]["const"] for key in (
            "max_wall_seconds_per_attempt", "max_wall_seconds_search_family", "max_memory_mb",
            "max_output_bytes_per_attempt", "max_output_bytes_search_family", "max_workers")],
            [1800, 86400, 8192, 536870912, 10737418240, 1])


if __name__ == "__main__":
    unittest.main()
