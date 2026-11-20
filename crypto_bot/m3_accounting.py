"""Opportunity pairing and weekly accounting for the M3 executable lane."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Sequence

from .m3_chronology import ChronologyError, complete_utc_week_keys
from .portfolio import PortfolioResult


class AccountingEvidenceError(ValueError):
    """M3 opportunity or weekly-account evidence does not reconcile."""


@dataclass(frozen=True, slots=True)
class WeeklyAccountEvidence:
    utc_week: str
    interval_start_utc: str
    interval_end_utc: str
    start_equity: str
    end_equity: str
    return_decimal: str
    position_exposure: bool
    trade_activity: bool


@dataclass(frozen=True, slots=True)
class PairedWeeklyAccountEvidence:
    utc_week: str
    candidate: WeeklyAccountEvidence
    incumbent: WeeklyAccountEvidence
    paired_return_delta: str


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise AccountingEvidenceError("weekly boundaries and marks must be explicit UTC")
    return value.astimezone(timezone.utc)


def _decimal(value, field: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AccountingEvidenceError(f"{field} is not a decimal equity value") from exc
    if not result.is_finite():
        raise AccountingEvidenceError(f"{field} must be finite")
    return result


def _event_time(event: dict) -> datetime:
    try:
        value = datetime.fromisoformat(str(event["event_time"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as exc:
        raise AccountingEvidenceError("portfolio event has an invalid UTC timestamp") from exc
    return _utc(value)


def _boundary_mark(result: PortfolioResult, boundary: datetime) -> Decimal:
    eligible = []
    for row in result.equity_curve:
        mark_time = _utc(row["time"])
        if mark_time <= boundary:
            eligible.append((mark_time, _decimal(row["equity"], "marked equity")))
    if eligible:
        return max(eligible, key=lambda item: item[0])[1]
    initial = [entry.amount_usdt for entry in result.ledger.journal if entry.entry_type == "INITIAL_DEPOSIT"]
    if len(initial) != 1:
        raise AccountingEvidenceError("fresh portfolio ledger must contain exactly one initial deposit")
    return _decimal(initial[0], "initial equity")


def _weekly_activity(result: PortfolioResult, start: datetime, end: datetime) -> tuple[bool, bool]:
    intervals: list[tuple[datetime, datetime | None]] = []
    events = sorted(result.events, key=_event_time)
    open_positions: dict[str, datetime] = {}
    activity = False
    for event in events:
        when = _event_time(event)
        event_type = event.get("event_type")
        position_id = event.get("position_id")
        if event_type in {"ORDER_PARTIALLY_FILLED", "ORDER_FILLED"} and position_id:
            open_positions.setdefault(str(position_id), when)
            if start <= when < end:
                activity = True
        elif event_type == "POSITION_CLOSED" and position_id:
            opened = open_positions.pop(str(position_id), None)
            if opened is not None:
                intervals.append((opened, when))
            if start <= when < end:
                activity = True
    intervals.extend((opened, None) for opened in open_positions.values())
    exposure = any(opened < end and (closed is None or closed > start) for opened, closed in intervals)
    return exposure, activity


def weekly_account_evidence(
    result: PortfolioResult,
    *,
    scored_interval: tuple[datetime, datetime],
    week_keys: Sequence[str] | None = None,
) -> tuple[WeeklyAccountEvidence, ...]:
    start, end = map(_utc, scored_interval)
    if start >= end:
        raise AccountingEvidenceError("scored UTC interval must be ordered")
    try:
        expected_keys = complete_utc_week_keys((start, end))
    except ChronologyError as exc:
        raise AccountingEvidenceError("scored interval cannot be assigned UTC week keys") from exc
    keys = tuple(week_keys) if week_keys is not None else expected_keys
    if keys != expected_keys:
        raise AccountingEvidenceError("weekly account keys differ from the complete scored UTC calendar")
    output = []
    for key in keys:
        year, week = key.split("-W")
        monday = datetime.fromisocalendar(int(year), int(week), 1).replace(tzinfo=timezone.utc)
        week_start = max(start, monday)
        week_end = min(end, monday + timedelta(days=7))
        if week_start >= week_end:
            raise AccountingEvidenceError("UTC week key has an empty scored interval")
        equity_start = _boundary_mark(result, week_start)
        equity_end = _boundary_mark(result, week_end)
        exposure, activity = _weekly_activity(result, week_start, week_end)
        if not exposure and not activity:
            weekly_return = Decimal(0)
        else:
            if equity_start <= 0:
                raise AccountingEvidenceError("weekly return cannot be computed from nonpositive starting equity")
            weekly_return = equity_end / equity_start - Decimal(1)
        output.append(WeeklyAccountEvidence(
            key, week_start.isoformat().replace("+00:00", "Z"),
            week_end.isoformat().replace("+00:00", "Z"), str(equity_start), str(equity_end),
            str(weekly_return), exposure, activity,
        ))
    return tuple(output)


def pair_weekly_account_evidence(candidate: Sequence[WeeklyAccountEvidence],
                                 incumbent: Sequence[WeeklyAccountEvidence]) -> tuple[PairedWeeklyAccountEvidence, ...]:
    if len(candidate) != len(incumbent):
        raise AccountingEvidenceError("candidate/incumbent weekly row counts differ")
    output = []
    for left, right in zip(candidate, incumbent):
        if left.utc_week != right.utc_week or left.interval_start_utc != right.interval_start_utc or left.interval_end_utc != right.interval_end_utc:
            raise AccountingEvidenceError("candidate/incumbent weekly calendar is not identically paired")
        delta = _decimal(left.return_decimal, "candidate weekly return") - _decimal(right.return_decimal, "incumbent weekly return")
        output.append(PairedWeeklyAccountEvidence(left.utc_week, left, right, str(delta)))
    return tuple(output)
