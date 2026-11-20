"""Read-only M3 counterfactual evidence over frozen Phase 5 trade rows.

This module never replays or edits the Phase 5 ledger. It selects immutable
per-signal counterfactual rows for candidate and incumbent decisions, retaining
every shared opportunity including abstentions, purges, and missing trades.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from io import BytesIO, TextIOWrapper
from typing import Sequence

from .m3_admission import AdmittedAttempt, AdmissionError
from .m3_policy import OpportunityDecision, PolicyError


class CounterfactualEvidenceError(ValueError):
    """Frozen counterfactual inputs or paired opportunity rows are invalid."""


@dataclass(frozen=True, slots=True)
class CounterfactualOpportunityEvidence:
    opportunity_id: str
    utc_week: str
    status: str
    selected_signal_id: int | None
    signal_timestamp: str | None
    entry_timestamp: str | None
    exit_timestamp: str | None
    gross_pnl: str | None
    fees: str | None
    slippage: str | None
    funding_contribution: str | None
    net_pnl: str | None
    exit_reason: str | None


@dataclass(frozen=True, slots=True)
class PairedCounterfactualOpportunity:
    opportunity_id: str
    utc_week: str
    candidate: CounterfactualOpportunityEvidence
    incumbent: CounterfactualOpportunityEvidence


def _snapshot(admission: AdmittedAttempt, key: str) -> bytes:
    matches = [item for item in admission.inputs if item.key == key]
    if len(matches) != 1:
        raise AdmissionError(f"admission must carry exactly one pinned {key} snapshot")
    item = matches[0]
    if (len(item.content) != item.size_bytes or
            hashlib.sha256(item.content).hexdigest() != item.sha256):
        raise AdmissionError(f"pinned {key} bytes differ from the admitted digest")
    return item.content


def _decimal(value: str, field: str) -> Decimal:
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise CounterfactualEvidenceError(f"counterfactual {field} is not numeric") from exc
    if not result.is_finite():
        raise CounterfactualEvidenceError(f"counterfactual {field} must be finite")
    return result


def _utc(value: str, field: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise CounterfactualEvidenceError(f"counterfactual {field} is not an ISO timestamp") from exc
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise CounterfactualEvidenceError(f"counterfactual {field} must be explicit UTC")
    return result.astimezone(timezone.utc)


def _verify_frozen_costs(admission: AdmittedAttempt) -> None:
    try:
        protocol = json.loads(admission.protocol_bytes)
        metadata = json.loads(_snapshot(admission, "counterfactual_metadata"))
        configuration = metadata["configuration"]
        cost_policy = protocol["cost_policy"]
    except (AdmissionError, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CounterfactualEvidenceError("pinned counterfactual cost metadata is incomplete") from exc
    frozen = {
        "fee_bps_per_side": Decimal("5"), "slippage_bps_per_side": Decimal("5"),
        "funding_bps_per_8h": Decimal("1"), "max_participation_percent": Decimal("5"),
    }
    for key, expected in frozen.items():
        if _decimal(str(cost_policy.get(key)), key) != expected:
            raise CounterfactualEvidenceError(f"frozen protocol changes baseline counterfactual {key}")
    configured = {
        "fee_bps": Decimal("5"), "slippage_bps": Decimal("5"),
        "funding_bps_8h": Decimal("1"), "max_participation_rate": Decimal("0.05"),
    }
    for key, expected in configured.items():
        if _decimal(str(configuration.get(key)), key) != expected:
            raise CounterfactualEvidenceError(f"Phase 5 counterfactual assumptions differ for {key}")
    if configuration.get("overlap_policy") != "each signal is a separate independent counterfactual trade; no shared capital or aggregate liquidity; overlaps retained":
        raise CounterfactualEvidenceError("Phase 5 counterfactual overlap policy is not the frozen independent-trade policy")


def _ledger_rows(admission: AdmittedAttempt) -> dict[int, dict[str, str]]:
    try:
        text = TextIOWrapper(BytesIO(_snapshot(admission, "counterfactual_ledger")), encoding="utf-8", newline="")
        reader = csv.DictReader(text)
        required = {"signal_id", "strategy", "direction", "signal_timestamp", "entry_timestamp",
                    "exit_timestamp", "execution_gross_pnl", "gross_pnl", "fees", "slippage",
                    "funding_paid", "net_pnl", "exit_reason"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise CounterfactualEvidenceError("pinned counterfactual ledger lacks required columns")
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise CounterfactualEvidenceError("pinned counterfactual ledger has duplicate columns")
        result: dict[int, dict[str, str]] = {}
        for row in reader:
            try:
                signal_id = int(row["signal_id"])
            except (TypeError, ValueError) as exc:
                raise CounterfactualEvidenceError("counterfactual signal_id is malformed") from exc
            if signal_id <= 0 or signal_id in result:
                raise CounterfactualEvidenceError("counterfactual signal IDs must be positive and unique")
            gross = _decimal(row["execution_gross_pnl"], "execution_gross_pnl")
            reference_gross = _decimal(row["gross_pnl"], "gross_pnl")
            fees = _decimal(row["fees"], "fees")
            slippage = _decimal(row["slippage"], "slippage")
            funding = -_decimal(row["funding_paid"], "funding_paid")
            net = _decimal(row["net_pnl"], "net_pnl")
            if reference_gross - slippage != gross:
                raise CounterfactualEvidenceError("counterfactual reference gross does not reconcile to execution gross and slippage")
            if net != reference_gross - slippage - fees + funding:
                raise CounterfactualEvidenceError("counterfactual net P&L does not reconcile to gross, fees, and funding")
            for field in ("signal_timestamp", "entry_timestamp", "exit_timestamp"):
                row[field] = _utc(row[field], field).isoformat().replace("+00:00", "Z")
            result[signal_id] = row
        return result
    except (AdmissionError, UnicodeDecodeError, csv.Error) as exc:
        raise CounterfactualEvidenceError("pinned counterfactual ledger is malformed") from exc


def _decision_alignment(decisions: Sequence[OpportunityDecision], opportunity_ids: Sequence[str], weeks: Sequence[str]):
    if len(decisions) != len(opportunity_ids):
        raise CounterfactualEvidenceError("decision vector must retain every shared opportunity")
    if len(weeks) != len(decisions):
        raise CounterfactualEvidenceError("paired opportunity weeks are missing")
    if tuple(row.opportunity_id for row in decisions) != tuple(opportunity_ids):
        raise CounterfactualEvidenceError("decision IDs differ from candidate-independent opportunity order")
    if any(row.utc_week != week for row, week in zip(decisions, weeks)):
        raise CounterfactualEvidenceError("decision UTC week differs from candidate-independent opportunity")
    for row in decisions:
        if row.status == "SELECTED":
            if row.selected_signal is None:
                raise CounterfactualEvidenceError("selected opportunity has no frozen signal reference")
        elif row.status != "ABSTAINED" or row.selected_signal is not None:
            raise CounterfactualEvidenceError("decision status is outside select/abstain vocabulary")


def score_counterfactual_decisions(
    admission: AdmittedAttempt,
    decisions: Sequence[OpportunityDecision],
    opportunities,
    *,
    scored_interval: tuple[datetime, datetime],
) -> tuple[CounterfactualOpportunityEvidence, ...]:
    """Project precomputed independent trades onto the exact shared opportunity IDs.

    The result is descriptive counterfactual evidence. It never reruns or writes
    the historical Phase 5 ledger and cannot qualify a strategy.
    """
    if not admission.eligible_for_evaluation:
        raise CounterfactualEvidenceError("counterfactual evidence requires M1-validated admission")
    start, end = scored_interval
    if (start.tzinfo is None or end.tzinfo is None or start.utcoffset() != timedelta(0) or
            end.utcoffset() != timedelta(0) or start >= end):
        raise CounterfactualEvidenceError("scored interval must be ordered explicit UTC")
    ids = tuple(item.opportunity_id for item in opportunities.opportunities)
    weeks = tuple(item.utc_week for item in opportunities.opportunities)
    _decision_alignment(decisions, ids, weeks)
    _verify_frozen_costs(admission)
    rows = _ledger_rows(admission)
    evidence: list[CounterfactualOpportunityEvidence] = []
    for decision, opportunity in zip(decisions, opportunities.opportunities):
        if not start <= opportunity.available_at < end:
            raise CounterfactualEvidenceError("opportunity lies outside the scored interval")
        selected = decision.selected_signal
        if selected is None:
            evidence.append(CounterfactualOpportunityEvidence(
                decision.opportunity_id, decision.utc_week, "ABSTAINED", None,
                None, None, None, None, None, None, None, None, None,
            ))
            continue
        if selected not in opportunity.signals:
            raise CounterfactualEvidenceError("selected signal is not in its shared opportunity")
        trade = rows.get(selected.signal_id)
        if trade is None:
            evidence.append(CounterfactualOpportunityEvidence(
                decision.opportunity_id, decision.utc_week, "NO_COUNTERFACTUAL_TRADE",
                selected.signal_id, selected.signal_timestamp.isoformat().replace("+00:00", "Z"),
                None, None, None, None, None, None, None, None,
            ))
            continue
        if trade["strategy"] != selected.strategy or trade["direction"] != selected.direction or trade["signal_timestamp"] != selected.signal_timestamp.isoformat().replace("+00:00", "Z"):
            raise CounterfactualEvidenceError("counterfactual row identity differs from selected frozen signal")
        exit_time = _utc(trade["exit_timestamp"], "exit_timestamp")
        if exit_time >= end:
            evidence.append(CounterfactualOpportunityEvidence(
                decision.opportunity_id, decision.utc_week, "PURGED", selected.signal_id,
                trade["signal_timestamp"], trade["entry_timestamp"], trade["exit_timestamp"],
                None, None, None, None, None, trade["exit_reason"],
            ))
            continue
        entry_time = _utc(trade["entry_timestamp"], "entry_timestamp")
        if entry_time < start:
            raise CounterfactualEvidenceError("counterfactual entry precedes its scored opportunity interval")
        evidence.append(CounterfactualOpportunityEvidence(
            decision.opportunity_id, decision.utc_week, "COMPLETED", selected.signal_id,
            trade["signal_timestamp"], trade["entry_timestamp"], trade["exit_timestamp"],
            str(_decimal(trade["gross_pnl"], "gross_pnl")),
            str(_decimal(trade["fees"], "fees")),
            str(_decimal(trade["slippage"], "slippage")),
            str(-_decimal(trade["funding_paid"], "funding_paid")),
            str(_decimal(trade["net_pnl"], "net_pnl")), trade["exit_reason"],
        ))
    if len(evidence) != len(opportunities.opportunities):
        raise AssertionError("counterfactual output lost an opportunity row")
    return tuple(evidence)


def pair_counterfactual_opportunities(candidate, incumbent) -> tuple[PairedCounterfactualOpportunity, ...]:
    if len(candidate) != len(incumbent):
        raise PolicyError("candidate and incumbent counterfactual rows have different counts")
    paired = []
    for left, right in zip(candidate, incumbent):
        if left.opportunity_id != right.opportunity_id or left.utc_week != right.utc_week:
            raise PolicyError("candidate and incumbent counterfactual IDs/weeks are not paired")
        paired.append(PairedCounterfactualOpportunity(left.opportunity_id, left.utc_week, left, right))
    return tuple(paired)
