"""Integrated per-fold M3 evidence engine over an admitted byte snapshot.

The function is deliberately below the production authorization boundary: the
future caller must verify the signed independent Linux permit, record M1's
RUNNING event, and provide this admitted snapshot. It never opens paths, changes
M1, ranks candidates, qualifies results, or accesses a holdout.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Mapping

from .m3_accounting import PairedWeeklyAccountEvidence, pair_weekly_account_evidence, weekly_account_evidence
from .m3_admission import AdmittedAttempt
from .m3_chronology import FoldWindow, OpportunityUniverse, build_fold_windows, build_opportunity_universe
from .m3_counterfactual import pair_counterfactual_opportunities, score_counterfactual_decisions
from .m3_evidence import PairedOpportunityEvidence, validate_paired_opportunity_evidence
from .m3_policy import OpportunityDecision, interpret_admitted_candidate, interpret_frozen_incumbent
from .m3_portfolio import PairedPortfolioFoldResult, run_paired_executable_fold
from .m3_predictive import PredictiveOpportunityEvidence, score_predictive_decisions
from .m3_isolation import VerifiedEvaluationPermit


class FoldEvaluationError(RuntimeError):
    """A pinned fold cannot produce a complete paired evidence package."""


@dataclass(frozen=True, slots=True)
class FoldEvidence:
    fold_id: str
    scored_interval: tuple[datetime, datetime]
    universe: OpportunityUniverse
    candidate_decisions: tuple[OpportunityDecision, ...]
    incumbent_decisions: tuple[OpportunityDecision, ...]
    candidate_predictive: tuple[PredictiveOpportunityEvidence, ...]
    incumbent_predictive: tuple[PredictiveOpportunityEvidence, ...]
    paired_counterfactual: tuple
    executable: PairedPortfolioFoldResult
    paired_opportunities: tuple[PairedOpportunityEvidence, ...]
    paired_weekly_account: tuple[PairedWeeklyAccountEvidence, ...]
    candidate_stress_weekly: tuple[tuple[str, tuple], ...]
    incumbent_stress_weekly: tuple[tuple[str, tuple], ...]


def evaluate_fold_evidence(
    admission: AdmittedAttempt,
    candidate_bytes: bytes,
    fold: FoldWindow,
    *,
    instrument_id: str,
    dataset_manifest_sha256: str,
    permit: VerifiedEvaluationPermit,
) -> FoldEvidence:
    """Evaluate one pre-registered outer fold in three isolated evidence lanes.

    Inputs are exact in-memory snapshots produced by admission. The decision
    engine sees only hash-pinned signal rows; the label scorer is invoked after
    both decision vectors exist. Every lane retains the complete union universe.
    """
    if not admission.eligible_for_evaluation:
        raise FoldEvaluationError("fold evaluation requires an admitted M1 attempt")
    input_hashes = {item.key: item.sha256 for item in admission.inputs}
    if (not isinstance(permit, VerifiedEvaluationPermit) or permit.attempt_id != admission.attempt_id or
            permit.protocol_id != admission.protocol_id or permit.candidate_sha256 != admission.candidate_sha256 or
            permit.protocol_sha256 != admission.protocol_sha256 or
            permit.registry_tip_sha256 != admission.registry_tip_sha256 or
            permit.checkpoint_sha256 != admission.checkpoint_sha256 or
            permit.input_hashes_sha256 != hashlib.sha256(json.dumps(dict(sorted(input_hashes.items())),
                sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()):
        raise FoldEvaluationError("verified Linux isolation and one-attempt authorization permit is absent or mismatched")
    try:
        protocol = json.loads(admission.protocol_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FoldEvaluationError("admitted protocol bytes are malformed") from exc
    if permit.source_commit != protocol.get("source", {}).get("commit"):
        raise FoldEvaluationError("Linux permit source commit differs from the admitted frozen protocol")
    registered_folds = build_fold_windows(protocol)
    if fold not in registered_folds:
        raise FoldEvaluationError("requested fold is not an exact frozen registered M0 boundary")
    manifest_hashes = [item.sha256 for item in admission.inputs if item.key == "phase5_manifest"]
    if len(manifest_hashes) != 1 or manifest_hashes[0] != dataset_manifest_sha256:
        raise FoldEvaluationError("opportunity IDs must bind the exact admitted Phase 5 dataset manifest")
    start, end = fold.outer_test
    try:
        signal_inputs = [item for item in admission.inputs if item.key == "signals"]
        if len(signal_inputs) != 1:
            raise FoldEvaluationError("admission lacks exactly one pinned signal snapshot")
        raw_signals = signal_inputs[0].content
        universe = build_opportunity_universe(raw_signals, instrument_id=instrument_id,
            dataset_manifest_sha256=dataset_manifest_sha256, eligible_interval=(start, end))
        candidate = interpret_admitted_candidate(admission, candidate_bytes, universe)
        incumbent = interpret_frozen_incumbent(universe)
        # Label joins occur after both decision vectors are immutable.
        candidate_predictive = score_predictive_decisions(admission, candidate, universe,
            scored_interval=(start, end))
        incumbent_predictive = score_predictive_decisions(admission, incumbent, universe,
            scored_interval=(start, end))
        candidate_cf = score_counterfactual_decisions(admission, candidate, universe,
            scored_interval=(start, end))
        incumbent_cf = score_counterfactual_decisions(admission, incumbent, universe,
            scored_interval=(start, end))
        paired_cf = pair_counterfactual_opportunities(candidate_cf, incumbent_cf)
        executable = run_paired_executable_fold(admission, candidate, incumbent, universe,
            scored_interval=(start, end), permit=permit)
        ids = tuple(item.opportunity_id for item in universe.opportunities)
        weeks = {item.opportunity_id: item.utc_week for item in universe.opportunities}
        paired_rows = validate_paired_opportunity_evidence(opportunity_ids=ids,
            utc_week_by_id=weeks, candidate_decisions=candidate, incumbent_decisions=incumbent,
            candidate_predictive=candidate_predictive, incumbent_predictive=incumbent_predictive,
            candidate_counterfactual=candidate_cf, incumbent_counterfactual=incumbent_cf,
            candidate_executable=executable.candidate_opportunities,
            incumbent_executable=executable.incumbent_opportunities)
        week_keys = universe.utc_week_keys
        candidate_weekly = weekly_account_evidence(executable.candidate,
            scored_interval=(start, end), week_keys=week_keys)
        incumbent_weekly = weekly_account_evidence(executable.incumbent,
            scored_interval=(start, end), week_keys=week_keys)
        paired_weekly = pair_weekly_account_evidence(candidate_weekly, incumbent_weekly)
        candidate_stress = []
        incumbent_stress = []
        for (candidate_name, candidate_result), (incumbent_name, incumbent_result) in zip(
                executable.candidate_cost_stress, executable.incumbent_cost_stress):
            if candidate_name != incumbent_name:
                raise FoldEvaluationError("paired executable stress scenario order differs")
            candidate_stress.append((candidate_name, weekly_account_evidence(candidate_result,
                scored_interval=(start, end), week_keys=week_keys)))
            incumbent_stress.append((incumbent_name, weekly_account_evidence(incumbent_result,
                scored_interval=(start, end), week_keys=week_keys)))
        return FoldEvidence(fold.fold_id, (start, end), universe, candidate, incumbent,
            candidate_predictive, incumbent_predictive, paired_cf, executable, paired_rows,
            paired_weekly, tuple(candidate_stress), tuple(incumbent_stress))
    except FoldEvaluationError:
        raise
    except Exception as exc:
        raise FoldEvaluationError(f"fold {fold.fold_id} failed closed before evidence publication") from exc


def evaluate_registered_outer_folds(
    admission: AdmittedAttempt,
    candidate_bytes: bytes,
    *,
    instrument_id: str,
    dataset_manifest_sha256: str,
    permit: VerifiedEvaluationPermit,
) -> tuple[FoldEvidence, ...]:
    """Run all and only the folds frozen in the admitted M0 protocol.

    Any failed fold aborts the attempt-level result; callers must persist a
    terminal M1 technical failure and must not publish a partial package as
    completed.
    """
    try:
        protocol = json.loads(admission.protocol_bytes.decode("utf-8"))
        folds = build_fold_windows(protocol)
        if len(folds) < 6:
            raise FoldEvaluationError("historical diagnostics require all six frozen outer folds")
        results = tuple(evaluate_fold_evidence(admission, candidate_bytes, fold,
            instrument_id=instrument_id, dataset_manifest_sha256=dataset_manifest_sha256,
            permit=permit) for fold in folds)
        if tuple(item.fold_id for item in results) != tuple(item.fold_id for item in folds):
            raise FoldEvaluationError("attempt output omitted or reordered a registered outer fold")
        return results
    except FoldEvaluationError:
        raise
    except Exception as exc:
        raise FoldEvaluationError("registered outer-fold evaluation failed closed") from exc
