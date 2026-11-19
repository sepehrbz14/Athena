"""Post-decision predictive label scoring for frozen 96-bar signal outcomes.

No decision, opportunity, or candidate-policy module imports or calls this
module. It reads labels only after a complete decision vector is supplied.
This is raw per-opportunity evidence, not qualification or family inference.
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
from .m3_chronology import (
    LABEL_HORIZON_BARS, ChronologyError, OpportunityUniverse,
    candle_timeline_from_bytes, label_endpoint, label_survives_boundary,
)
from .m3_policy import OpportunityDecision, PolicyError


class PredictiveEvidenceError(ValueError):
    """Post-decision labels or their pinned provenance are malformed."""


@dataclass(frozen=True, slots=True)
class PredictiveOpportunityEvidence:
    opportunity_id: str
    utc_week: str
    status: str
    reason: str
    selected_signal_id: int | None
    score_decimal: str | None


def _utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise PredictiveEvidenceError("label timestamps must be ISO-8601 UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise PredictiveEvidenceError("label timestamps must be explicit UTC")
    return parsed.astimezone(timezone.utc)


def _decimal(value: str, field: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise PredictiveEvidenceError(f"label {field} must be numeric") from exc
    if not parsed.is_finite():
        raise PredictiveEvidenceError(f"label {field} must be finite")
    return parsed


def _pinned_input(admission: AdmittedAttempt, key: str) -> bytes:
    matches = [item for item in admission.inputs if item.key == key]
    if len(matches) != 1:
        raise AdmissionError(f"admission must carry exactly one pinned {key} snapshot")
    item = matches[0]
    if len(item.content) != item.size_bytes or hashlib.sha256(item.content).hexdigest() != item.sha256:
        raise AdmissionError(f"pinned {key} bytes differ from the admitted digest")
    return item.content


def _decision_alignment(
    decisions: Sequence[OpportunityDecision], universe: OpportunityUniverse,
    scored_interval: tuple[datetime, datetime],
) -> tuple[datetime, datetime]:
    if len(decisions) != len(universe.opportunities):
        raise PredictiveEvidenceError("decision vector must contain every shared opportunity")
    start, end = scored_interval
    if start.tzinfo is None or end.tzinfo is None or start.utcoffset() != timedelta(0) or end.utcoffset() != timedelta(0) or start >= end:
        raise PredictiveEvidenceError("scored interval must be ordered explicit UTC")
    for decision, opportunity in zip(decisions, universe.opportunities):
        if (decision.opportunity_id != opportunity.opportunity_id or
                decision.instrument_id != opportunity.instrument_id or
                decision.utc_week != opportunity.utc_week or
                decision.available_at != opportunity.available_at.isoformat().replace("+00:00", "Z")):
            raise PredictiveEvidenceError("decision rows are not paired to the exact shared opportunity order")
        if not (start <= opportunity.available_at < end):
            raise PredictiveEvidenceError("opportunity lies outside the scored interval")
        if decision.status == "SELECTED":
            if decision.selected_signal is None or decision.selected_signal not in opportunity.signals:
                raise PredictiveEvidenceError("selected signal was not present in its frozen opportunity")
        elif decision.status == "ABSTAINED":
            if decision.selected_signal is not None:
                raise PredictiveEvidenceError("abstention cannot carry a selected signal")
        else:
            raise PredictiveEvidenceError("decision status is outside the frozen select/abstain vocabulary")
    return start, end


def _signal_rows(signal_bytes: bytes) -> dict[int, dict[str, str]]:
    try:
        text = TextIOWrapper(BytesIO(signal_bytes), encoding="utf-8", newline="")
        reader = csv.DictReader(text)
        required = {"signal_id", "timestamp", "direction", "reference_price", "forward_outcomes"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise PredictiveEvidenceError("pinned signal rows lack post-decision label fields")
        rows: dict[int, dict[str, str]] = {}
        for row in reader:
            try:
                signal_id = int(row["signal_id"])
            except (TypeError, ValueError) as exc:
                raise PredictiveEvidenceError("pinned signal_id is malformed") from exc
            if signal_id <= 0 or signal_id in rows:
                raise PredictiveEvidenceError("pinned signal IDs must be positive and unique")
            rows[signal_id] = row
        return rows
    except (UnicodeDecodeError, csv.Error) as exc:
        raise PredictiveEvidenceError("pinned signal CSV is malformed") from exc


def score_predictive_decisions(
    admission: AdmittedAttempt,
    decisions: Sequence[OpportunityDecision],
    universe: OpportunityUniverse,
    *,
    scored_interval: tuple[datetime, datetime],
) -> tuple[PredictiveOpportunityEvidence, ...]:
    """Join the frozen 96-bar labels only after decisions, preserving every ID.

    The signal and candle snapshots come from admission memory, never by
    reopening their paths. Censored and purged selected signals remain explicit
    rows with no numeric score; M0 abstentions score exactly zero.
    """
    if not admission.eligible_for_evaluation:
        raise PredictiveEvidenceError("predictive scoring requires an admitted M1-validated attempt")
    start, end = _decision_alignment(decisions, universe, scored_interval)
    try:
        signal_bytes = _pinned_input(admission, "signals")
        candle_bytes = _pinned_input(admission, "candles")
    except AdmissionError as exc:
        raise PredictiveEvidenceError("admitted signal/candle byte snapshot failed integrity checks") from exc
    try:
        timeline = candle_timeline_from_bytes(candle_bytes)
    except ChronologyError as exc:
        raise PredictiveEvidenceError("admitted candle timeline is invalid") from exc
    signals = _signal_rows(signal_bytes)

    evidence = []
    for decision in decisions:
        if decision.status == "ABSTAINED":
            evidence.append(PredictiveOpportunityEvidence(
                decision.opportunity_id, decision.utc_week, "SCORED_ZERO",
                "frozen_abstention", None, "0",
            ))
            continue

        selected = decision.selected_signal
        assert selected is not None
        raw = signals.get(selected.signal_id)
        if raw is None:
            raise PredictiveEvidenceError("selected signal is absent from admitted frozen signal bytes")
        if (_utc(raw["timestamp"]) != selected.signal_timestamp or
                raw["direction"] != selected.direction or
                _decimal(raw["reference_price"], "reference_price") != _decimal(selected.reference_price, "reference_price")):
            raise PredictiveEvidenceError("selected signal fields differ from the admitted frozen row")

        try:
            endpoint = label_endpoint(selected.signal_timestamp, timeline)
        except ChronologyError as exc:
            raise PredictiveEvidenceError("selected signal cannot be resolved on the admitted candle timeline") from exc
        if endpoint is None:
            evidence.append(PredictiveOpportunityEvidence(
                decision.opportunity_id, decision.utc_week, "CENSORED",
                "label_horizon_crosses_candle_gap_or_data_end", selected.signal_id, None,
            ))
            continue
        if not label_survives_boundary(endpoint, end):
            evidence.append(PredictiveOpportunityEvidence(
                decision.opportunity_id, decision.utc_week, "PURGED",
                "96_bar_label_reaches_or_crosses_scored_boundary", selected.signal_id, None,
            ))
            continue

        try:
            label_container = json.loads(raw["forward_outcomes"])
            label = label_container[str(LABEL_HORIZON_BARS)]
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            evidence.append(PredictiveOpportunityEvidence(
                decision.opportunity_id, decision.utc_week, "CENSORED",
                "96_bar_label_missing", selected.signal_id, None,
            ))
            continue
        if not isinstance(label, dict) or label.get("status") != "complete" or label.get("bars_observed") != LABEL_HORIZON_BARS:
            evidence.append(PredictiveOpportunityEvidence(
                decision.opportunity_id, decision.utc_week, "CENSORED",
                "96_bar_label_not_complete", selected.signal_id, None,
            ))
            continue
        if _utc(label.get("timestamp")) != endpoint:
            raise PredictiveEvidenceError("stored 96-bar label endpoint differs from pinned candle timeline")
        score = _decimal(label.get("direction_adjusted_return_pct"), "direction_adjusted_return_pct") / Decimal(100)
        evidence.append(PredictiveOpportunityEvidence(
            decision.opportunity_id, decision.utc_week, "SCORED",
            "frozen_direction_adjusted_96_bar_return", selected.signal_id, str(score),
        ))
    return tuple(evidence)

