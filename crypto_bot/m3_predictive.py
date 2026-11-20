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


def _source_row(signal, raw: dict[str, str]) -> None:
    """Verify a universe signal is the same frozen decision-time row."""
    if (_utc(raw.get("timestamp")) != signal.signal_timestamp or
            _utc(raw.get("available_at")) != signal.available_at or
            raw.get("strategy") != signal.strategy or
            raw.get("direction") != signal.direction or
            _decimal(raw.get("reference_price"), "reference_price") !=
            _decimal(signal.reference_price, "reference_price")):
        raise PredictiveEvidenceError("opportunity signal fields differ from admitted frozen row")


def _complete_label(raw: dict[str, str], endpoint: datetime) -> Decimal | None:
    """Return a complete 96-bar score, or None when the frozen label is censored."""
    try:
        container = json.loads(raw.get("forward_outcomes", ""))
        label = container[str(LABEL_HORIZON_BARS)]
    except (KeyError, TypeError, json.JSONDecodeError):
        return None
    if (not isinstance(label, dict) or label.get("status") != "complete" or
            label.get("bars_observed") != LABEL_HORIZON_BARS):
        return None
    if _utc(label.get("timestamp")) != endpoint:
        raise PredictiveEvidenceError("stored 96-bar label endpoint differs from pinned candle timeline")
    try:
        return _decimal(label.get("direction_adjusted_return_pct"), "direction_adjusted_return_pct")
    except PredictiveEvidenceError:
        return None


def _opportunity_label_state(
    opportunity, signals: dict[int, dict[str, str]], timeline,
    scored_end: datetime,
) -> tuple[str, str, dict[int, Decimal]]:
    """Determine label eligibility independently of candidate select/abstain choice.

    A shared opportunity may contain several strategy rows. Its timestamp-derived
    horizon eligibility must agree for all of them; a missing/incomplete row
    censors the whole opportunity so policy selection cannot change the eligible
    observation universe.
    """
    if not opportunity.signals:
        return "CENSORED", "opportunity_has_no_frozen_signal_rows", {}
    timestamps = {signal.signal_timestamp for signal in opportunity.signals}
    if len(timestamps) != 1 or any(signal.available_at != opportunity.available_at for signal in opportunity.signals):
        raise PredictiveEvidenceError("signals at one opportunity do not share a single label anchor")
    signal_timestamp = next(iter(timestamps))
    try:
        endpoint = label_endpoint(signal_timestamp, timeline)
    except ChronologyError as exc:
        raise PredictiveEvidenceError("opportunity signal cannot be resolved on the admitted candle timeline") from exc
    if endpoint is None:
        return "CENSORED", "label_horizon_crosses_candle_gap_or_data_end", {}
    if not label_survives_boundary(endpoint, scored_end):
        return "PURGED", "96_bar_label_reaches_or_crosses_scored_boundary", {}

    scores: dict[int, Decimal] = {}
    incomplete = False
    for signal in opportunity.signals:
        raw = signals.get(signal.signal_id)
        if raw is None:
            raise PredictiveEvidenceError("opportunity signal is absent from admitted frozen signal bytes")
        _source_row(signal, raw)
        score = _complete_label(raw, endpoint)
        if score is None:
            incomplete = True
        else:
            scores[signal.signal_id] = score
    if incomplete or len(scores) != len(opportunity.signals):
        return "CENSORED", "96_bar_label_incomplete_for_shared_opportunity", {}
    return "ELIGIBLE", "complete_eligible_96_bar_label", scores


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
    rows with no numeric score. An abstention scores zero only when the shared
    opportunity has a complete, eligible 96-bar label.
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
    for decision, opportunity in zip(decisions, universe.opportunities):
        state, reason, scores = _opportunity_label_state(opportunity, signals, timeline, end)
        if state == "PURGED":
            evidence.append(PredictiveOpportunityEvidence(
                decision.opportunity_id, decision.utc_week, "PURGED", reason,
                decision.selected_signal.signal_id if decision.selected_signal else None, None,
            ))
        elif state == "CENSORED":
            evidence.append(PredictiveOpportunityEvidence(
                decision.opportunity_id, decision.utc_week, "CENSORED", reason,
                decision.selected_signal.signal_id if decision.selected_signal else None, None,
            ))
        elif decision.status == "ABSTAINED":
            evidence.append(PredictiveOpportunityEvidence(
                decision.opportunity_id, decision.utc_week, "SCORED_ZERO",
                "frozen_abstention_on_complete_eligible_opportunity", None, "0",
            ))
        else:
            selected = decision.selected_signal
            assert selected is not None
            score = scores[selected.signal_id] / Decimal(100)
            evidence.append(PredictiveOpportunityEvidence(
                decision.opportunity_id, decision.utc_week, "SCORED",
                "frozen_direction_adjusted_96_bar_return", selected.signal_id, str(score),
            ))
    return tuple(evidence)

