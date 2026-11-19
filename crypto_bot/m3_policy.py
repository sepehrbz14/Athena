"""Finite declarative M3 policy interpretation over frozen opportunities.

This module is a pure decision mapper. It performs no market-data I/O, label
scoring, trading simulation, portfolio accounting, registry writes, or outcome
inference. It only selects existing frozen signal records or abstains.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Mapping, Sequence

from . import experiment_registry as m1
from .m3_admission import AdmittedAttempt
from .m3_chronology import Opportunity, OpportunityUniverse, SignalRef


class PolicyError(ValueError):
    """Candidate policy or shared opportunity input is invalid."""


@dataclass(frozen=True, slots=True)
class OpportunityDecision:
    opportunity_id: str
    instrument_id: str
    available_at: str
    utc_week: str
    status: str
    reason: str
    selected_signal: SignalRef | None


def _confidence(signal: SignalRef) -> Decimal:
    try:
        value = Decimal(signal.confidence)
    except (InvalidOperation, TypeError) as exc:
        raise PolicyError("frozen signal confidence is malformed") from exc
    if not value.is_finite() or value <= 0 or value > 1:
        raise PolicyError("frozen signal confidence is outside the frozen schema range")
    return value


def _regime(opportunity: Opportunity) -> str:
    regimes = {signal.regime for signal in opportunity.signals}
    if len(regimes) > 1:
        raise PolicyError("signals at one opportunity disagree on the frozen regime")
    return next(iter(regimes), "unknown")


def _ordered_members(policy: Mapping, default: Sequence[str]) -> tuple[str, ...]:
    members = policy.get("members", default)
    if not isinstance(members, (list, tuple)) or not members:
        raise PolicyError("policy member order is missing")
    if any(not isinstance(item, str) for item in members) or len(set(members)) != len(members):
        raise PolicyError("policy members must be a unique ordered strategy list")
    fixed = policy.get("fixed_order")
    if fixed is not None:
        if (not isinstance(fixed, list) or len(fixed) != len(members) or
                set(fixed) != set(members)):
            raise PolicyError("fixed_order must be a permutation of the policy members")
        return tuple(fixed)
    return tuple(members)


def _first_by_order(signals: Mapping[str, SignalRef], order: Sequence[str]) -> SignalRef | None:
    return next((signals[strategy] for strategy in order if strategy in signals), None)


def _candidate_signal(opportunity: Opportunity, candidate: Mapping) -> tuple[SignalRef | None, str]:
    family = candidate.get("family")
    policy = candidate.get("policy")
    members = candidate.get("strategy_members")
    if not isinstance(policy, Mapping) or not isinstance(members, list):
        raise PolicyError("candidate is missing its frozen declarative policy")
    if len(set(members)) != len(members) or not members:
        raise PolicyError("candidate strategy_members must be a nonempty unique list")
    signal_map = {s.strategy: s for s in opportunity.signals}
    if len(signal_map) != len(opportunity.signals):
        raise PolicyError("opportunity contains duplicate strategy signals")
    if any(strategy not in members for strategy in signal_map):
        # Extra rows are valid union members, but cannot be selected by this candidate.
        signal_map = {k: v for k, v in signal_map.items() if k in members}

    if family == "regime_specialization":
        regime = _regime(opportunity)
        if regime == "unknown":
            return None, "unknown_regime"
        allowlist = policy.get("regime_allowlist")
        if not isinstance(allowlist, Mapping):
            raise PolicyError("regime specialization allowlist is malformed")
        allowed = allowlist.get(regime, ())
        if not isinstance(allowed, (list, tuple)):
            raise PolicyError("regime allowlist entry must be an ordered strategy list")
        if any(strategy not in members for strategy in allowed):
            raise PolicyError("regime allowlist contains a strategy outside candidate members")
        selected = _first_by_order(signal_map, allowed)
        return (selected, "selected_regime_specialist") if selected else (None, "no_eligible_signal")

    if family == "regime_selection_allocation":
        regime = _regime(opportunity)
        if regime == "unknown":
            return None, "unknown_regime"
        priorities = policy.get("regime_priority")
        if not isinstance(priorities, Mapping):
            raise PolicyError("regime priority map is malformed")
        order = priorities.get(regime, ())
        if not isinstance(order, (list, tuple)):
            raise PolicyError("regime priority must be an ordered strategy list")
        if any(strategy not in members for strategy in order):
            raise PolicyError("regime priority contains a strategy outside candidate members")
        selected = _first_by_order(signal_map, order)
        return (selected, "selected_regime_priority") if selected else (None, "no_eligible_signal")

    if family != "signal_combination":
        raise PolicyError("unsupported candidate family")
    rule = policy.get("rule")
    order = _ordered_members(policy, members)
    if any(strategy not in members for strategy in order):
        raise PolicyError("combination members contain a strategy outside candidate members")
    signals = {strategy: signal_map[strategy] for strategy in order if strategy in signal_map}
    if rule == "incumbent_ensemble":
        if any(signal.incumbent_selected is None for signal in signals.values()):
            raise PolicyError("incumbent ensemble flag is missing from frozen signal rows")
        selected = [signals[strategy] for strategy in order
                    if strategy in signals and signals[strategy].incumbent_selected]
        if len(selected) > 1:
            raise PolicyError("frozen incumbent marks multiple signals at one opportunity")
        return (selected[0], "selected_incumbent_ensemble") if selected else (None, "incumbent_abstained")
    if rule == "unanimous_direction":
        if len(signals) != len(order):
            return None, "missing_member_signal"
        directions = {signal.direction for signal in signals.values()}
        if len(directions) != 1:
            return None, "direction_disagreement"
        return signals[order[0]], "selected_unanimous"
    if rule == "majority_direction":
        if not signals:
            return None, "no_member_signal"
        counts = {direction: sum(signal.direction == direction for signal in signals.values())
                  for direction in ("long", "short")}
        winner = max(counts, key=counts.get)
        if counts[winner] * 2 <= len(signals):
            return None, "direction_tie"
        selected = _first_by_order(
            {strategy: signal for strategy, signal in signals.items() if signal.direction == winner}, order)
        assert selected is not None
        return selected, "selected_majority"
    if rule == "highest_existing_confidence":
        if not signals:
            return None, "no_member_signal"
        scored = [( _confidence(signal), signal) for signal in signals.values()]
        best = max(score for score, _ in scored)
        winners = [signal for score, signal in scored if score == best]
        if len(winners) != 1:
            return None, "confidence_tie"
        return winners[0], "selected_highest_existing_confidence"
    raise PolicyError("unsupported frozen signal-combination rule")


def _validate_inputs(
    admission: AdmittedAttempt, candidate_bytes: bytes, universe: OpportunityUniverse,
) -> dict:
    if not admission.eligible_for_evaluation:
        raise PolicyError("decision interpreter requires an admitted M1-validated attempt")
    if hashlib.sha256(candidate_bytes).hexdigest() != admission.candidate_sha256:
        raise PolicyError("candidate bytes differ from the admitted immutable snapshot")
    try:
        candidate = json.loads(candidate_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PolicyError("admitted candidate bytes are malformed") from exc
    if (not isinstance(candidate, dict) or candidate.get("candidate_id") != admission.candidate_id or
            candidate.get("candidate_id") != m1.compute_candidate_id(candidate) or
            candidate.get("experiment_id") != admission.protocol_id or candidate.get("research_only") is not True):
        raise PolicyError("candidate identity or research-only contract does not match admission")
    ids = [op.opportunity_id for op in universe.opportunities]
    if len(ids) != len(set(ids)):
        raise PolicyError("shared opportunity IDs are not unique")
    weeks = set(universe.utc_week_keys)
    if any(op.utc_week not in weeks for op in universe.opportunities):
        raise PolicyError("opportunity week is absent from the complete UTC week calendar")
    return candidate


def interpret_admitted_candidate(
    admission: AdmittedAttempt,
    candidate_bytes: bytes,
    universe: OpportunityUniverse,
) -> tuple[OpportunityDecision, ...]:
    """Emit exactly one select/abstain decision for every shared opportunity.

    The exact registered candidate bytes are required. Output rows contain only
    references to pre-existing signals. This function cannot load or score
    historical data and is not an evaluation orchestrator.
    """
    candidate = _validate_inputs(admission, candidate_bytes, universe)
    decisions = []
    for opportunity in universe.opportunities:
        selected, reason = _candidate_signal(opportunity, candidate)
        decisions.append(OpportunityDecision(
            opportunity_id=opportunity.opportunity_id,
            instrument_id=opportunity.instrument_id,
            available_at=opportunity.available_at.isoformat().replace("+00:00", "Z"),
            utc_week=opportunity.utc_week,
            status="SELECTED" if selected is not None else "ABSTAINED",
            reason=reason,
            selected_signal=selected,
        ))
    return tuple(decisions)


def interpret_frozen_incumbent(universe: OpportunityUniverse) -> tuple[OpportunityDecision, ...]:
    """Project frozen incumbent selection flags over the same complete universe."""
    decisions = []
    for opportunity in universe.opportunities:
        selected = [signal for signal in opportunity.signals if signal.incumbent_selected is True]
        if any(signal.incumbent_selected is None for signal in opportunity.signals):
            raise PolicyError("incumbent ensemble flag is missing from frozen signal rows")
        if len(selected) > 1:
            raise PolicyError("frozen incumbent marks multiple signals at one opportunity")
        signal = selected[0] if selected else None
        decisions.append(OpportunityDecision(
            opportunity_id=opportunity.opportunity_id,
            instrument_id=opportunity.instrument_id,
            available_at=opportunity.available_at.isoformat().replace("+00:00", "Z"),
            utc_week=opportunity.utc_week,
            status="SELECTED" if signal else "ABSTAINED",
            reason="selected_frozen_incumbent" if signal else "incumbent_abstained",
            selected_signal=signal,
        ))
    return tuple(decisions)

