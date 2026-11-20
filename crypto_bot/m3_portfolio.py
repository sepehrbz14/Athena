"""Fresh paired executable portfolio runs using the frozen Phase 4 engine.

This adapter creates new candidate/incumbent ledgers for each fold and never
opens, copies, or writes the frozen Phase 4 historical ledger. Authoritative
calls are gated separately; unit tests may exercise it with synthetic inputs.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from io import BytesIO, TextIOWrapper
from typing import Sequence

from jsonschema import Draft202012Validator

from .execution import BarExecutionModel
from .m3_admission import AdmittedAttempt, AdmissionError
from .m3_chronology import (
    BAR, CandleTimeline, Opportunity, OpportunityUniverse, SignalRef,
    candle_timeline_from_bytes, is_entry_in_terminal_blackout, warmup_ready,
)
from .m3_policy import OpportunityDecision, PolicyError
from .m3_isolation import VerifiedEvaluationPermit
from .models import Candle, Side
from .portfolio import PortfolioResult, PortfolioSimulator
from .portfolio_ledger import D
from .portfolio_models import FrozenSignal, PortfolioConfig, SignalDisposition


class PortfolioEvaluationError(ValueError):
    """Pinned execution inputs or fold constraints are invalid."""


@dataclass(frozen=True, slots=True)
class OpportunityExecutionDisposition:
    opportunity_id: str
    utc_week: str
    status: str
    reason: str
    signal_id: int | None
    position_id: str | None
    net_pnl: str | None


@dataclass(frozen=True, slots=True)
class PairedPortfolioFoldResult:
    candidate: PortfolioResult
    incumbent: PortfolioResult
    candidate_opportunities: tuple[OpportunityExecutionDisposition, ...]
    incumbent_opportunities: tuple[OpportunityExecutionDisposition, ...]
    candidate_cost_stress: tuple[tuple[str, PortfolioResult], ...]
    incumbent_cost_stress: tuple[tuple[str, PortfolioResult], ...]


def _pinned(admission: AdmittedAttempt, key: str) -> bytes:
    found = [item for item in admission.inputs if item.key == key]
    if len(found) != 1:
        raise AdmissionError(f"admission must carry exactly one pinned {key} snapshot")
    item = found[0]
    if len(item.content) != item.size_bytes or hashlib.sha256(item.content).hexdigest() != item.sha256:
        raise AdmissionError(f"pinned {key} bytes differ from the admitted digest")
    return item.content


def _utc(value: str) -> datetime:
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise PortfolioEvaluationError("pinned market timestamp is invalid") from exc
    if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
        raise PortfolioEvaluationError("pinned market timestamps must be explicit UTC")
    return stamp.astimezone(timezone.utc)


def _number(value: str, field: str, *, optional: bool = False) -> float | None:
    if optional and value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PortfolioEvaluationError(f"pinned candle {field} is not numeric") from exc
    if not math.isfinite(number):
        raise PortfolioEvaluationError(f"pinned candle {field} must be finite")
    return number


def _candles_from_bytes(content: bytes) -> tuple[Candle, ...]:
    try:
        text = TextIOWrapper(BytesIO(content), encoding="utf-8", newline="")
        reader = csv.DictReader(text)
        required = {"timestamp", "open", "high", "low", "close", "volume"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise PortfolioEvaluationError("pinned candle CSV lacks required OHLCV columns")
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise PortfolioEvaluationError("pinned candle CSV contains duplicate columns")
        rows = []
        for row in reader:
            timestamp = _utc(row["timestamp"]) if not row["timestamp"].lstrip("-+").isdigit() else None
            if timestamp is None:
                raw = Decimal(row["timestamp"])
                if not raw.is_finite():
                    raise PortfolioEvaluationError("pinned candle epoch timestamp must be finite")
                seconds = raw / Decimal(1000) if raw > Decimal("10000000000") else raw
                try:
                    timestamp = datetime.fromtimestamp(float(seconds), tz=timezone.utc)
                except (OverflowError, OSError, ValueError) as exc:
                    raise PortfolioEvaluationError("pinned candle epoch timestamp is outside supported range") from exc
            rows.append(Candle(timestamp, _number(row["open"], "open"), _number(row["high"], "high"),
                               _number(row["low"], "low"), _number(row["close"], "close"),
                               _number(row["volume"], "volume", optional=True)))
    except (AttributeError, KeyError, InvalidOperation, UnicodeDecodeError, csv.Error) as exc:
        raise PortfolioEvaluationError("pinned candle CSV is malformed") from exc
    CandleTimeline.from_timestamps([item.timestamp for item in rows])
    return tuple(rows)


def _config_from_bytes(admission: AdmittedAttempt) -> PortfolioConfig:
    config_bytes = _pinned(admission, "phase4_config")
    try:
        protocol = json.loads(admission.protocol_bytes)
        raw = json.loads(config_bytes.decode("utf-8"))
        schema_path = __import__("pathlib").Path(__file__).resolve().parents[1] / "schemas" / "athena2_portfolio_config_v1.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(raw)
    except Exception as exc:
        raise PortfolioEvaluationError("frozen Phase 4 configuration or schema is invalid") from exc
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    config_hash = hashlib.sha256(canonical).hexdigest()
    cost_policy = protocol.get("cost_policy", {})
    if (config_hash != protocol.get("source", {}).get("phase4_config_sha256") or
            config_hash != cost_policy.get("phase4_config_sha256")):
        raise PortfolioEvaluationError("Phase 4 configuration hash differs from frozen protocol")
    execution_data = {key: raw["execution"][key] for key in (
        "fee_rate", "slippage_bps", "funding_rate_per_8h", "candle_minutes", "max_participation_rate")}
    config = PortfolioConfig(
        config_id=raw["config_id"], initial_equity_usdt=D(raw["initial_equity_usdt"]),
        instrument_allowlist=tuple(raw["instrument_allowlist"]), per_trade_risk_pct=D(raw["per_trade_risk_pct"]),
        aggregate_open_pending_stop_risk_pct=D(raw["aggregate_open_pending_stop_risk_pct"]),
        leverage=D(raw["leverage"]), maximum_gross_exposure_equity_multiple=D(raw["maximum_gross_exposure_equity_multiple"]),
        maximum_net_exposure_equity_multiple=D(raw["maximum_net_exposure_equity_multiple"]),
        maximum_active_positions=int(raw["maximum_active_positions"]), pyramiding_enabled=bool(raw["pyramiding_enabled"]),
        minimum_equity_usdt=D(raw["minimum_equity_usdt"]), cash_buffer_pct=D(raw["cash_buffer_pct"]),
        minimum_quantity_btc=D(raw["minimum_quantity_btc"]), priority_strategy_order=tuple(raw["priority_strategy_order"]),
        daily_loss_limit_pct=D(raw["daily_loss_limit_pct"]), maximum_drawdown_pct=D(raw["maximum_drawdown_pct"]),
        pending_order_expiry_eligible_candles=int(raw["pending_order_expiry_eligible_candles"]),
        execution=BarExecutionModel(**execution_data), force_close_at_end=bool(raw["execution"]["force_close_at_end"]),
        raw_config=raw, sha256=config_hash,
    )
    expected = {
        "fee_bps_per_side": Decimal("5"), "slippage_bps_per_side": Decimal("5"),
        "funding_bps_per_8h": Decimal("1"), "max_participation_percent": Decimal("5"),
    }
    actual = {
        "fee_bps_per_side": D(config.execution.fee_rate) * Decimal("10000"),
        "slippage_bps_per_side": D(config.execution.slippage_bps),
        "funding_bps_per_8h": D(config.execution.funding_rate_per_8h) * Decimal("10000"),
        "max_participation_percent": D(config.execution.max_participation_rate) * Decimal("100"),
    }
    if any(D(cost_policy.get(key)) != value or actual[key] != value for key, value in expected.items()):
        raise PortfolioEvaluationError("Phase 4 costs differ from frozen M0 execution assumptions")
    if not config.force_close_at_end:
        raise PortfolioEvaluationError("frozen Phase 4 config does not force-close positions at fold end")
    return config


def _signal_ref_map(opportunity: Opportunity) -> dict[int, SignalRef]:
    rows = {item.signal_id: item for item in opportunity.signals}
    if len(rows) != len(opportunity.signals):
        raise PortfolioEvaluationError("opportunity contains duplicate signal IDs")
    return rows


def _build_run_inputs(
    admission: AdmittedAttempt, decisions: Sequence[OpportunityDecision], opportunities: OpportunityUniverse,
    candles: tuple[Candle, ...], timeline: CandleTimeline, config: PortfolioConfig,
    scored_interval: tuple[datetime, datetime], account_id: str,
):
    start, end = scored_interval
    if start.tzinfo is None or end.tzinfo is None or start.utcoffset() != timedelta(0) or end.utcoffset() != timedelta(0) or start >= end:
        raise PortfolioEvaluationError("fold interval must be ordered explicit UTC")
    if len(decisions) != len(opportunities.opportunities):
        raise PortfolioEvaluationError("portfolio decisions must retain every shared opportunity")
    for decision, opportunity in zip(decisions, opportunities.opportunities):
        if (decision.opportunity_id != opportunity.opportunity_id or decision.utc_week != opportunity.utc_week or
                decision.instrument_id != opportunity.instrument_id or
                decision.available_at != opportunity.available_at.isoformat().replace("+00:00", "Z")):
            raise PortfolioEvaluationError("portfolio decision vector is not aligned to the shared opportunity universe")
    start_indices = [index for index, candle in enumerate(candles) if candle.timestamp >= start]
    end_indices = [index for index, candle in enumerate(candles) if candle.timestamp < end]
    if not start_indices or not end_indices:
        raise PortfolioEvaluationError("fold interval is outside the pinned candle timeline")
    global_start, global_end = start_indices[0], end_indices[-1] + 1
    warm_start = max(0, global_start - 816)
    fold_candles = candles[warm_start:global_end]
    by_id = {op.opportunity_id: op for op in opportunities.opportunities}
    emitted: list[FrozenSignal] = []
    diagnostics: list[SignalDisposition] = []
    outcome_by_id: dict[str, OpportunityExecutionDisposition] = {}
    for raw_row, (decision, opportunity) in enumerate(zip(decisions, opportunities.opportunities), 1):
        signal = decision.selected_signal
        status = "ABSTAINED" if signal is None else "EXCLUDED"
        reason = "candidate_abstained" if signal is None else "outside_scored_interval"
        if signal is not None:
            if signal not in opportunity.signals:
                raise PortfolioEvaluationError("selected signal is absent from candidate-independent opportunity")
            signal_index = timeline.resolve(signal.signal_timestamp)
            if not warmup_ready(signal_index, timeline.timestamps):
                status, reason = "EXCLUDED", "816_bar_warmup_not_ready"
            elif is_entry_in_terminal_blackout(opportunity.available_at, end):
                status, reason = "EXCLUDED", "terminal_entry_blackout"
            elif not start <= opportunity.available_at < end:
                status, reason = "EXCLUDED", "outside_scored_interval"
            else:
                local_index = signal_index - warm_start
                candle = fold_candles[local_index]
                if signal.available_at != opportunity.available_at:
                    raise PortfolioEvaluationError("selected signal availability differs from opportunity timestamp")
                if not math.isclose(float(signal.reference_price), candle.close, rel_tol=1e-12, abs_tol=1e-9):
                    raise PortfolioEvaluationError("frozen signal reference price does not match pinned candle close")
                try:
                    frozen = FrozenSignal(
                        signal.signal_id, opportunity.instrument_id, signal.signal_timestamp, signal.available_at,
                        signal.strategy, Side(signal.direction), D(signal.confidence), D(signal.reference_price),
                        D(signal.stop_price), D(signal.take_profit) if signal.take_profit else None,
                        next(item.sha256 for item in admission.inputs if item.key == "signals"),
                        next(item.sha256 for item in admission.inputs if item.key == "candles"), local_index)
                except (StopIteration, ValueError) as exc:
                    raise PortfolioEvaluationError("selected frozen signal cannot be normalized for Phase 4 engine") from exc
                emitted.append(frozen)
                status, reason = "ELIGIBLE", "selected_and_chronologically_eligible"
        diagnostics.append(SignalDisposition(raw_row, signal.signal_id if signal else None,
            opportunity.instrument_id, opportunity.available_at, signal.strategy if signal else None,
            signal is not None, "eligible" if status == "ELIGIBLE" else "filtered", reason))
        outcome_by_id[opportunity.opportunity_id] = OpportunityExecutionDisposition(
            opportunity.opportunity_id, opportunity.utc_week, status, reason,
            signal.signal_id if signal else None, None, None)
    return fold_candles, emitted, diagnostics, outcome_by_id


def _run_one(
    admission: AdmittedAttempt, decisions: Sequence[OpportunityDecision], opportunities: OpportunityUniverse,
    candles: tuple[Candle, ...], timeline: CandleTimeline, config: PortfolioConfig,
    scored_interval: tuple[datetime, datetime], account_id: str,
):
    fold_candles, signals, dispositions, outcomes = _build_run_inputs(
        admission, decisions, opportunities, candles, timeline, config, scored_interval, account_id)
    source = {"attempt_id": admission.attempt_id, "protocol_sha256": admission.protocol_sha256,
              "candidate_sha256": admission.candidate_sha256, "registry_tip_sha256": admission.registry_tip_sha256,
              "signal_sha256": next(item.sha256 for item in admission.inputs if item.key == "signals"),
              "candle_sha256": next(item.sha256 for item in admission.inputs if item.key == "candles"),
              "_dispositions": dispositions}
    result = PortfolioSimulator(config, account_id=account_id).run(fold_candles, signals, source_manifest=source)
    completed_by_signal: dict[int, object] = {}
    for item in result.ledger.completed_positions:
        try:
            signal_id = int(item.position_id.rsplit(":", 1)[1])
        except (ValueError, IndexError) as exc:
            raise PortfolioEvaluationError("Phase 4 completed position ID does not identify its signal") from exc
        completed_by_signal[signal_id] = item
    for oid, item in tuple(outcomes.items()):
        if item.status != "ELIGIBLE" or item.signal_id not in completed_by_signal:
            if item.status == "ELIGIBLE":
                outcomes[oid] = replace(item, status="NO_FILL", reason="selected_signal_did_not_complete_a_position")
            continue
        position = completed_by_signal[item.signal_id]
        outcomes[oid] = replace(item, status="COMPLETED", reason="phase4_execution_model_completed_position",
                                position_id=position.position_id, net_pnl=str(position.final_net_pnl))
    return result, tuple(outcomes[item.opportunity_id] for item in opportunities.opportunities)


def _stressed_config(config: PortfolioConfig, scenario: str) -> PortfolioConfig:
    raw = json.loads(json.dumps(config.raw_config))
    execution = dict(raw["execution"])
    if scenario == "fee_10bps_per_side_only":
        execution["fee_rate"] = 0.001
    elif scenario == "slippage_10bps_per_side_only":
        execution["slippage_bps"] = 10.0
    elif scenario == "funding_2bps_per_8h_only":
        execution["funding_rate_per_8h"] = 0.0002
    else:
        raise PortfolioEvaluationError("unknown frozen one-factor cost stress")
    raw["execution"] = execution
    encoded = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    model = replace(config.execution, fee_rate=float(execution["fee_rate"]),
                    slippage_bps=float(execution["slippage_bps"]),
                    funding_rate_per_8h=float(execution["funding_rate_per_8h"]))
    return replace(config, execution=model, raw_config=raw, sha256=hashlib.sha256(encoded).hexdigest())


def run_paired_executable_fold(
    admission: AdmittedAttempt,
    candidate_decisions: Sequence[OpportunityDecision],
    incumbent_decisions: Sequence[OpportunityDecision],
    opportunities: OpportunityUniverse,
    *,
    scored_interval: tuple[datetime, datetime],
    permit: VerifiedEvaluationPermit,
) -> PairedPortfolioFoldResult:
    """Run synthetic or separately-authorized candidate/incumbent fresh ledgers.

    A verified Linux attestation and separate exact-scope evaluation permit are
    mandatory even for direct callers. Synthetic fixtures may construct an
    in-memory permit solely in tests.
    """
    if not admission.eligible_for_evaluation:
        raise PortfolioEvaluationError("executable portfolio evaluation requires M1-validated admission")
    import json
    input_hashes = {item.key: item.sha256 for item in admission.inputs}
    input_digest = hashlib.sha256(json.dumps(dict(sorted(input_hashes.items())), sort_keys=True,
        separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    try:
        protocol = json.loads(admission.protocol_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PortfolioEvaluationError("admitted protocol snapshot is malformed") from exc
    if (not isinstance(permit, VerifiedEvaluationPermit) or permit.attempt_id != admission.attempt_id or
            permit.protocol_id != admission.protocol_id or permit.candidate_sha256 != admission.candidate_sha256 or
            permit.protocol_sha256 != admission.protocol_sha256 or permit.registry_tip_sha256 != admission.registry_tip_sha256 or
            permit.checkpoint_sha256 != admission.checkpoint_sha256 or permit.input_hashes_sha256 != input_digest or
            permit.source_commit != protocol.get("source", {}).get("commit")):
        raise PortfolioEvaluationError("verified Linux isolation/authorization permit is absent or mismatched")
    if len(candidate_decisions) != len(incumbent_decisions):
        raise PortfolioEvaluationError("candidate and incumbent decision counts differ")
    for candidate, incumbent in zip(candidate_decisions, incumbent_decisions):
        if candidate.opportunity_id != incumbent.opportunity_id or candidate.utc_week != incumbent.utc_week:
            raise PortfolioEvaluationError("candidate and incumbent decisions are not paired on identical opportunities")
    candle_bytes = _pinned(admission, "candles")
    candles = _candles_from_bytes(candle_bytes)
    timeline = candle_timeline_from_bytes(candle_bytes)
    config = _config_from_bytes(admission)
    candidate, candidate_rows = _run_one(admission, candidate_decisions, opportunities, candles, timeline,
        config, scored_interval, f"m3:{admission.attempt_id}:candidate")
    incumbent, incumbent_rows = _run_one(admission, incumbent_decisions, opportunities, candles, timeline,
        config, scored_interval, f"m3:{admission.attempt_id}:incumbent")
    if candidate.ledger is incumbent.ledger or candidate.ledger.account_id == incumbent.ledger.account_id:
        raise AssertionError("candidate and incumbent must have independent fresh portfolio ledgers")
    for left, right in zip(candidate_rows, incumbent_rows):
        if left.opportunity_id != right.opportunity_id or left.utc_week != right.utc_week:
            raise AssertionError("candidate/incumbent executable evidence lost opportunity pairing")

    stress = tuple((name, _stressed_config(config, name)) for name in (
        "fee_10bps_per_side_only", "slippage_10bps_per_side_only", "funding_2bps_per_8h_only"))
    candidate_stress, incumbent_stress = [], []
    for label, stressed_config in stress:
        left, _ = _run_one(admission, candidate_decisions, opportunities, candles, timeline,
            stressed_config, scored_interval, f"m3:{admission.attempt_id}:candidate:{label}")
        right, _ = _run_one(admission, incumbent_decisions, opportunities, candles, timeline,
            stressed_config, scored_interval, f"m3:{admission.attempt_id}:incumbent:{label}")
        if left.ledger is right.ledger or left.ledger.account_id == right.ledger.account_id:
            raise AssertionError("stress candidate/incumbent ledgers must be separate")
        candidate_stress.append((label, left))
        incumbent_stress.append((label, right))
    return PairedPortfolioFoldResult(candidate, incumbent, candidate_rows, incumbent_rows,
                                     tuple(candidate_stress), tuple(incumbent_stress))
