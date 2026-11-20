"""Cross-lane opportunity pairing and accounting invariants for M3."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Sequence

from .m3_accounting import PairedWeeklyAccountEvidence
from .m3_counterfactual import PairedCounterfactualOpportunity, pair_counterfactual_opportunities
from .m3_policy import OpportunityDecision, PolicyError
from .m3_predictive import PredictiveOpportunityEvidence
from .m3_portfolio import OpportunityExecutionDisposition


class EvidenceValidationError(ValueError):
    """Evidence lanes cannot be reconciled on the shared opportunity universe."""


@dataclass(frozen=True, slots=True)
class PairedOpportunityEvidence:
    opportunity_id: str
    utc_week: str
    candidate_decision: OpportunityDecision
    incumbent_decision: OpportunityDecision
    candidate_predictive: PredictiveOpportunityEvidence
    incumbent_predictive: PredictiveOpportunityEvidence
    candidate_counterfactual: object
    incumbent_counterfactual: object
    candidate_executable: OpportunityExecutionDisposition
    incumbent_executable: OpportunityExecutionDisposition


def _pair_by_key(candidate: Sequence, incumbent: Sequence, lane: str, id_field: str = "opportunity_id"):
    if len(candidate) != len(incumbent):
        raise EvidenceValidationError(f"{lane} candidate/incumbent row counts differ")
    result = []
    for left, right in zip(candidate, incumbent):
        if getattr(left, id_field) != getattr(right, id_field) or left.utc_week != right.utc_week:
            raise EvidenceValidationError(f"{lane} rows do not pair on identical opportunity IDs and UTC weeks")
        result.append((left, right))
    return tuple(result)


def validate_paired_opportunity_evidence(
    *,
    opportunity_ids: Sequence[str],
    utc_week_by_id: dict[str, str],
    candidate_decisions: Sequence[OpportunityDecision],
    incumbent_decisions: Sequence[OpportunityDecision],
    candidate_predictive: Sequence[PredictiveOpportunityEvidence],
    incumbent_predictive: Sequence[PredictiveOpportunityEvidence],
    candidate_counterfactual: Sequence,
    incumbent_counterfactual: Sequence,
    candidate_executable: Sequence[OpportunityExecutionDisposition],
    incumbent_executable: Sequence[OpportunityExecutionDisposition],
) -> tuple[PairedOpportunityEvidence, ...]:
    ids = tuple(opportunity_ids)
    if len(ids) != len(set(ids)) or set(utc_week_by_id) != set(ids):
        raise EvidenceValidationError("shared opportunity universe has duplicate IDs or incomplete week mapping")
    lanes = (
        ("decisions", candidate_decisions, incumbent_decisions),
        ("predictive", candidate_predictive, incumbent_predictive),
        ("counterfactual", candidate_counterfactual, incumbent_counterfactual),
        ("executable", candidate_executable, incumbent_executable),
    )
    paired = {}
    for name, left, right in lanes:
        rows = _pair_by_key(left, right, name)
        if tuple(getattr(a, "opportunity_id") for a, _ in rows) != ids:
            raise EvidenceValidationError(f"{name} evidence must preserve exactly one row per shared opportunity in order")
        if any(a.utc_week != utc_week_by_id[a.opportunity_id] for a, _ in rows):
            raise EvidenceValidationError(f"{name} evidence week key differs from shared universe")
        paired[name] = rows
    output = []
    for index, oid in enumerate(ids):
        cd, id_ = paired["decisions"][index]
        cp, ip = paired["predictive"][index]
        cc, ic = paired["counterfactual"][index]
        ce, ie = paired["executable"][index]
        if cp.selected_signal_id != (cd.selected_signal.signal_id if cd.selected_signal else None):
            raise EvidenceValidationError("candidate predictive row does not identify its post-decision signal")
        if ip.selected_signal_id != (id_.selected_signal.signal_id if id_.selected_signal else None):
            raise EvidenceValidationError("incumbent predictive row does not identify its post-decision signal")
        if cc.selected_signal_id != (cd.selected_signal.signal_id if cd.selected_signal else None):
            raise EvidenceValidationError("candidate counterfactual row does not identify its decision")
        if ic.selected_signal_id != (id_.selected_signal.signal_id if id_.selected_signal else None):
            raise EvidenceValidationError("incumbent counterfactual row does not identify its decision")
        if ce.signal_id != (cd.selected_signal.signal_id if cd.selected_signal else None):
            raise EvidenceValidationError("candidate executable row does not identify its decision")
        if ie.signal_id != (id_.selected_signal.signal_id if id_.selected_signal else None):
            raise EvidenceValidationError("incumbent executable row does not identify its decision")
        output.append(PairedOpportunityEvidence(oid, utc_week_by_id[oid], cd, id_, cp, ip, cc, ic, ce, ie))
    # Also exercise the frozen counterfactual public pair validator explicitly.
    pair_counterfactual_opportunities(candidate_counterfactual, incumbent_counterfactual)
    return tuple(output)


def reconcile_weekly_accounting(paired_weeks: Sequence[PairedWeeklyAccountEvidence]) -> None:
    seen = set()
    for row in paired_weeks:
        if row.utc_week in seen or row.candidate.utc_week != row.utc_week or row.incumbent.utc_week != row.utc_week:
            raise EvidenceValidationError("weekly account records contain duplicate or mismatched UTC keys")
        seen.add(row.utc_week)
        try:
            candidate = Decimal(row.candidate.return_decimal)
            incumbent = Decimal(row.incumbent.return_decimal)
            delta = Decimal(row.paired_return_delta)
        except (InvalidOperation, TypeError) as exc:
            raise EvidenceValidationError("weekly paired returns must be decimal strings") from exc
        if not all(value.is_finite() for value in (candidate, incumbent, delta)) or delta != candidate - incumbent:
            raise EvidenceValidationError("weekly candidate/incumbent return delta does not reconcile")
        for account in (row.candidate, row.incumbent):
            start = Decimal(account.start_equity)
            end = Decimal(account.end_equity)
            result = Decimal(account.return_decimal)
            if start <= 0 or (end - start) / start != result:
                raise EvidenceValidationError("weekly mark-to-market return does not reconcile to boundary equity")
