"""M4 persistent-family validation over immutable M1/M2/M3 records.

This module never runs a candidate. It verifies registered history, signed
checkpoints, sealed M3 packages and raw weekly evidence, then independently
reconstructs M0 bootstrap statistics and family-wide Holm adjusted p-values.
Qualification is deliberately fail-closed until the proposed, versioned M0
simultaneous-bound amendment is approved by the protocol owner.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import random
from typing import Any, Mapping, Sequence, TYPE_CHECKING
import jsonschema

from . import experiment_registry as m1
if TYPE_CHECKING:
    from .evolution_registry_security import ExternalSecurityStore

ALPHA = 0.05
BOOTSTRAP_ITERATIONS = 100_000
BLOCK_LENGTH_WEEKS = 4
HYPOTHESES = (
    "predictive_absolute_weekly_score",
    "predictive_paired_weekly_delta",
    "robust_executable_absolute_weekly_return",
    "executable_paired_weekly_delta",
)
SCENARIOS = (
    "baseline",
    "fee_10bps_per_side_only",
    "slippage_10bps_per_side_only",
    "funding_2bps_per_8h_only",
)
EVIDENCE_SCHEMA = "athena.m4.m3-weekly-statistical-input.v1"
REPORT_SCHEMA = "athena.m4.persistent-family-report.v4"
INVERSION_AMENDMENT_SCHEMA = "athena.evolution.m0-bonferroni-bound-amendment.v1"
APPROVED_AMENDMENT_ID = "sha256:693ba1f2f357788ebf9348685c44e4d03ef4915a322e31c72e1cd13654d2f1a9"
APPROVED_AMENDMENT_DOCUMENT_SHA256 = "0245084071fdd3f56ac1bc5f122dbe246612bca62b8d2919338b0505819e7a07"
PROTOCOL_OWNER_DECISION_SHA256 = "497ba433f6fdb7af8e0ff184064ec74f0ca7932b90e3f236ec4750dcaf6a26f8"
_M4_REPORT_ROOT = Path(__file__).resolve().parents[1] / "reports" / "evolution_engine_milestone4"
_WEEKLY_SCHEMA = Path(__file__).resolve().parents[1] / "schemas" / "athena_m4_weekly_statistical_input_v1.schema.json"
_REPORT_SCHEMA = Path(__file__).resolve().parents[1] / "schemas" / "athena_m4_persistent_family_report_v4.schema.json"
_INVERSION_AMENDMENT_SCHEMA = (Path(__file__).resolve().parents[1] / "schemas"
                               / "athena_m0_bonferroni_bound_amendment_v1.schema.json")
_PROTOCOL_OWNER_DECISION = (Path(__file__).resolve().parents[1] / "reports"
                            / "evolution_engine_m0_bonferroni_bound_amendment_2026-11-23"
                            / "protocol_owner_decision.json")


class M4ValidationError(RuntimeError):
    """Registry, trust, evidence or publication integrity failed."""


class QualificationBlocked(M4ValidationError):
    """The proposed M0 simultaneous-bound amendment has not been approved."""


@dataclass(frozen=True, slots=True)
class FamilyValidation:
    report: Mapping[str, Any]
    report_sha256: str
    output_path: Path


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _validate_weekly_schema(value: Mapping[str, Any]) -> None:
    try:
        schema = json.loads(_WEEKLY_SCHEMA.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(value, schema)
    except Exception as exc:
        raise M4ValidationError("M3 weekly statistical evidence fails the frozen M4 intake schema") from exc


def _validate_report_schema(value: Mapping[str, Any]) -> None:
    try:
        schema = json.loads(_REPORT_SCHEMA.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(value, schema)
    except Exception as exc:
        raise M4ValidationError("M4 family report fails its immutable output schema") from exc


def _safe_report_path(value: str | Path) -> Path:
    target = Path(value).absolute()
    if _M4_REPORT_ROOT.is_symlink():
        raise M4ValidationError("M4 report root cannot be a symlink")
    try:
        relative = target.relative_to(_M4_REPORT_ROOT.absolute())
    except ValueError as exc:
        raise M4ValidationError("M4 outputs must use a separate reports/evolution_engine_milestone4 directory") from exc
    if len(relative.parts) < 2:
        raise M4ValidationError("M4 output must be placed in a dated report directory")
    try:
        date.fromisoformat(relative.parts[0])
    except ValueError as exc:
        raise M4ValidationError("M4 report directory must use a UTC YYYY-MM-DD date") from exc
    cursor = _M4_REPORT_ROOT
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise M4ValidationError("M4 output path cannot contain symlinks")
    return target


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise M4ValidationError(f"{name} must be a finite JSON number")
    return float(value)


def holm_adjust(p_values: Sequence[float]) -> tuple[float, ...]:
    """Return standard Holm step-down adjusted p-values in input order.

    Ties are ordered by original family position for deterministic reporting;
    the adjusted values are tie-invariant.
    """
    if not p_values:
        raise M4ValidationError("Holm family cannot be empty")
    values = tuple(_finite_number(value, "p-value") for value in p_values)
    if any(value < 0 or value > 1 for value in values):
        raise M4ValidationError("p-values must lie in [0,1]")
    order = sorted(range(len(values)), key=lambda index: (values[index], index))
    adjusted = [1.0] * len(values)
    running = 0.0
    size = len(values)
    for rank, index in enumerate(order):
        running = max(running, (size - rank) * values[index])
        adjusted[index] = min(1.0, running)
    return tuple(adjusted)


def holm_rejection_set(p_values: Sequence[float], *, alpha: float = ALPHA) -> frozenset[int]:
    """Return Holm step-down rejections, preserving input order for exact ties."""
    values = tuple(_finite_number(value, "p-value") for value in p_values)
    if not values or any(value < 0 or value > 1 for value in values):
        raise M4ValidationError("Holm family must contain p-values in [0,1]")
    alpha = _finite_number(alpha, "Holm alpha")
    if not 0 < alpha < 1:
        raise M4ValidationError("Holm alpha must lie strictly between 0 and 1")
    order = sorted(range(len(values)), key=lambda index: (values[index], index))
    rejected: set[int] = set()
    size = len(values)
    for rank, index in enumerate(order):
        if values[index] <= alpha / (size - rank):
            rejected.add(index)
        else:
            break
    return frozenset(rejected)


def bootstrap_p_value_at_threshold(estimate: float, centered_errors: Sequence[float],
                                   threshold: float) -> float:
    """M0 amendment marginal upper-tail p-value for H0: theta <= threshold.

    The +1 correction and >= comparison preserve the frozen zero-null
    convention exactly. ``centered_errors`` are bootstrap draws minus the
    observed estimate, in their deterministic M1-seeded order.
    """
    estimate = _finite_number(estimate, "estimand")
    threshold = _finite_number(threshold, "effect threshold")
    if not centered_errors:
        raise M4ValidationError("bootstrap p-value function requires its complete resample vector")
    errors = tuple(_finite_number(value, "centered bootstrap error") for value in centered_errors)
    tail = sum(error >= estimate - threshold for error in errors)
    return (1 + tail) / (len(errors) + 1)


def holm_adjust_at_thresholds(
    estimates: Sequence[float | None],
    centered_errors: Sequence[Sequence[float] | None],
    thresholds: Sequence[float],
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Evaluate each marginal p-function at its own threshold, then Holm-adjust all m."""
    if not estimates or len(estimates) != len(centered_errors) or len(estimates) != len(thresholds):
        raise M4ValidationError("threshold vector, estimates, and bootstrap errors must have equal nonzero length")
    raw: list[float] = []
    for estimate, errors, threshold in zip(estimates, centered_errors, thresholds):
        if estimate is None or errors is None:
            raw.append(1.0)
        else:
            raw.append(bootstrap_p_value_at_threshold(estimate, errors, threshold))
    raw_values = tuple(raw)
    return raw_values, holm_adjust(raw_values)


def invert_bootstrap_p_value(estimate: float, centered_errors: Sequence[float],
                             local_alpha: float) -> float | None:
    """Return sup{delta: p(delta) <= local_alpha}, or None for an empty set."""
    estimate = _finite_number(estimate, "estimand")
    local_alpha = _finite_number(local_alpha, "local confidence level")
    if not 0 < local_alpha < 1:
        raise M4ValidationError("local confidence level must lie strictly between 0 and 1")
    if not centered_errors:
        return None
    errors = tuple(_finite_number(value, "centered bootstrap error") for value in centered_errors)
    if local_alpha < 1.0 / (len(errors) + 1):
        return None
    # p(delta) is right-continuous and changes only at estimate-error.
    for threshold in sorted({estimate - error for error in errors}):
        if bootstrap_p_value_at_threshold(estimate, errors, threshold) > local_alpha:
            return threshold
    return None


def holm_simultaneous_lower_bounds(
    p_values_at_zero: Sequence[float],
    estimates: Sequence[float | None],
    centered_errors: Sequence[Sequence[float] | None],
    *, alpha: float = ALPHA,
) -> tuple[float | None, ...]:
    """Holm-compatible simultaneous one-sided lower confidence bounds.

    This is the equal-weight Holm closed-testing confidence construction. If
    all family nulls reject, use alpha/m marginal inversions. Otherwise,
    rejected hypotheses receive their tested boundary (zero), and the
    nonrejected set shares alpha equally. Missing/p=1 hypotheses stay in the
    complete family and receive no numeric bound.
    """
    p_values = tuple(_finite_number(value, "p-value") for value in p_values_at_zero)
    if not p_values or any(value < 0 or value > 1 for value in p_values):
        raise M4ValidationError("Holm-bound family must contain p-values in [0,1]")
    if len(estimates) != len(p_values) or len(centered_errors) != len(p_values):
        raise M4ValidationError("Holm-bound estimates/errors must match the persistent family size")
    for p_value, estimate, errors in zip(p_values, estimates, centered_errors):
        if estimate is None or errors is None:
            if p_value != 1.0:
                raise M4ValidationError("a missing bootstrap function must retain its frozen p=1 placeholder")
        else:
            reconstructed_p = bootstrap_p_value_at_threshold(estimate, errors, 0.0)
            if not math.isclose(p_value, reconstructed_p, rel_tol=0, abs_tol=1e-14):
                raise M4ValidationError("zero-threshold p-value differs from its arbitrary-threshold function")
    alpha = _finite_number(alpha, "Holm alpha")
    if not 0 < alpha < 1:
        raise M4ValidationError("Holm alpha must lie strictly between 0 and 1")
    rejected = holm_rejection_set(p_values, alpha=alpha)
    size = len(p_values)
    if len(rejected) == size:
        local_level = alpha / size
        result = []
        for estimate, errors in zip(estimates, centered_errors):
            if estimate is None or errors is None:
                result.append(None)
            else:
                bound = invert_bootstrap_p_value(estimate, errors, local_level)
                result.append(None if bound is None else max(0.0, bound))
        return tuple(result)
    nonrejected_count = size - len(rejected)
    local_level = alpha / nonrejected_count
    result = []
    for index, (estimate, errors) in enumerate(zip(estimates, centered_errors)):
        if index in rejected:
            result.append(0.0)
        elif estimate is None or errors is None:
            result.append(None)
        else:
            result.append(invert_bootstrap_p_value(estimate, errors, local_level))
    return tuple(result)


def bonferroni_simultaneous_lower_bounds(
    estimates: Sequence[float | None],
    centered_errors: Sequence[Sequence[float] | None],
    *, alpha: float = ALPHA,
) -> tuple[float | None, ...]:
    """Invert full-family Bonferroni tests to simultaneous one-sided bounds.

    Every hypothesis in the persistent family uses the same marginal level
    alpha/m, including unresolved p=1 placeholders. Under valid marginal
    p-functions, the union bound gives simultaneous coverage at least 1-alpha
    for arbitrary dependence and unequal marginal distributions. Holm
    adjusted p-values remain a separate family-testing output.
    """
    if not estimates or len(estimates) != len(centered_errors):
        raise M4ValidationError("Bonferroni estimates/errors must have equal nonzero length")
    alpha = _finite_number(alpha, "Bonferroni alpha")
    if not 0 < alpha < 1:
        raise M4ValidationError("Bonferroni alpha must lie strictly between 0 and 1")
    local_level = alpha / len(estimates)
    bounds: list[float | None] = []
    for estimate, errors in zip(estimates, centered_errors):
        if estimate is None or errors is None:
            bounds.append(None)
        else:
            bounds.append(invert_bootstrap_p_value(estimate, errors, local_level))
    return tuple(bounds)


def _verify_protocol_owner_decision() -> None:
    """Verify the separately recorded approval without mutating proposal bytes."""
    try:
        data = _PROTOCOL_OWNER_DECISION.read_bytes()
        decision = json.loads(data.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise M4ValidationError("approved M0 protocol-owner decision record is unavailable or invalid") from exc
    if _sha(_canonical(decision)) != PROTOCOL_OWNER_DECISION_SHA256:
        raise M4ValidationError("M0 protocol-owner decision record digest differs from the pinned approval")
    expected = {
        "schema_version": "athena.evolution.m0-protocol-owner-decision.v1",
        "decision_status": "APPROVED",
        "decision_date": "2026-11-24",
        "base_protocol": "athena.evolution.m0-frozen.v1",
        "amendment_schema": INVERSION_AMENDMENT_SCHEMA,
        "amendment_id": APPROVED_AMENDMENT_ID,
        "amendment_document_sha256": APPROVED_AMENDMENT_DOCUMENT_SHA256,
    }
    if any(decision.get(key) != value for key, value in expected.items()):
        raise M4ValidationError("M0 protocol-owner decision does not approve the pinned amendment identity")


def _validate_inversion_amendment(amendment: Mapping[str, Any]) -> tuple[str, str, str]:
    """Validate immutable proposal bytes and recognize the pinned approval record."""
    try:
        schema = json.loads(_INVERSION_AMENDMENT_SCHEMA.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(amendment, schema)
    except Exception as exc:
        raise M4ValidationError("M0 Bonferroni-bound amendment fails its strict versioned schema") from exc
    payload = dict(amendment)
    amendment_id = payload.pop("amendment_id")
    if amendment_id != "sha256:" + _sha(_canonical(payload)):
        raise M4ValidationError("M0 Holm-inversion amendment identity hash mismatch")
    document_sha256 = _sha(_canonical(amendment))
    if amendment_id != APPROVED_AMENDMENT_ID or document_sha256 != APPROVED_AMENDMENT_DOCUMENT_SHA256:
        raise M4ValidationError("M4 accepts only the exact protocol-owner-approved amendment identity")
    _verify_protocol_owner_decision()
    return amendment_id, document_sha256, "APPROVED_BY_PROTOCOL_OWNER"


def _block_indices(length: int, rng: random.Random) -> tuple[int, ...]:
    if length < BLOCK_LENGTH_WEEKS:
        raise M4ValidationError("each registered fold needs at least four consecutive scored weeks")
    result: list[int] = []
    while len(result) < length:
        start = rng.randrange(0, length - BLOCK_LENGTH_WEEKS + 1)
        result.extend(range(start, start + BLOCK_LENGTH_WEEKS))
    return tuple(result[:length])


def _mean(values: Sequence[float]) -> float:
    if not values:
        raise M4ValidationError("cannot average an empty weekly vector")
    return math.fsum(values) / len(values)


def _percentile(sorted_values: Sequence[float], probability: float) -> float:
    position = (len(sorted_values) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction


def reconstruct_raw_statistics(evidence: Mapping[str, Any], *, expected_seeds: Mapping[str, Mapping[str, int]]) -> dict[str, Any]:
    """Independently reproduce all four M0 estimands from weekly M3 evidence."""
    if evidence.get("schema") != EVIDENCE_SCHEMA:
        raise M4ValidationError("M3 weekly evidence uses an unknown M4 intake schema")
    _validate_weekly_schema(evidence)
    weeks = evidence.get("week_keys")
    folds = evidence.get("fold_by_week")
    if not isinstance(weeks, list) or not weeks or len(set(weeks)) != len(weeks) or any(not isinstance(w, str) for w in weeks):
        raise M4ValidationError("weekly evidence needs unique ordered UTC week keys")
    if not isinstance(folds, dict) or set(folds) != set(weeks) or any(not isinstance(folds[w], str) or not folds[w] for w in weeks):
        raise M4ValidationError("every week must map to one registered outer fold")
    fold_ids = tuple(dict.fromkeys(folds[w] for w in weeks))
    by_fold = {fold: tuple(w for w in weeks if folds[w] == fold) for fold in fold_ids}
    if any(len(rows) < BLOCK_LENGTH_WEEKS for rows in by_fold.values()):
        raise M4ValidationError("outer fold contains fewer than four scored weeks")
    lanes = evidence.get("weekly")
    if not isinstance(lanes, dict):
        raise M4ValidationError("weekly evidence is missing its lane vectors")

    def vector(key: str, raw_value: Any | None = None) -> dict[str, float]:
        raw = lanes.get(key) if raw_value is None else raw_value
        if not isinstance(raw, dict) or set(raw) != set(weeks):
            raise M4ValidationError(f"weekly vector {key} does not cover exactly the frozen UTC weeks")
        return {w: _finite_number(raw[w], f"{key}[{w}]") for w in weeks}

    pred_c = vector("predictive_candidate")
    pred_i = vector("predictive_incumbent")
    exec_i = vector("executable_incumbent_baseline")
    scenario_vectors = lanes.get("executable_candidate_scenarios")
    if not isinstance(scenario_vectors, dict) or set(scenario_vectors) != set(SCENARIOS):
        raise M4ValidationError("weekly evidence must include all four frozen candidate cost scenarios")
    exec_c = {scenario: vector(f"executable_candidate_scenarios.{scenario}", scenario_vectors[scenario])
              for scenario in SCENARIOS}

    counts = evidence.get("sample_counts")
    expected_count_names = {
        "predictive_opportunities", "predictive_occupied_weeks",
        "counterfactual_completed_trades", "counterfactual_occupied_weeks",
        "executable_positions", "executable_occupied_weeks", "executable_outer_folds",
        "regime_observations", "regime_occupied_weeks",
    }
    if not isinstance(counts, dict) or set(counts) != expected_count_names or any(
            isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in counts.values()):
        raise M4ValidationError("sample counts do not match the frozen M0 evidence fields")
    if counts["executable_outer_folds"] != len(fold_ids):
        raise M4ValidationError("reported executable fold count differs from the weekly evidence")
    if not isinstance(evidence.get("regime_claim_requested"), bool):
        raise M4ValidationError("regime_claim_requested must be an explicit Boolean evidence field")
    floors = {
        "predictive_opportunities": 200, "predictive_occupied_weeks": 30,
        "counterfactual_completed_trades": 100, "counterfactual_occupied_weeks": 30,
        "executable_positions": 100, "executable_occupied_weeks": 30,
        "executable_outer_folds": 6,
    }
    if evidence["regime_claim_requested"]:
        floors.update({"regime_observations": 30, "regime_occupied_weeks": 12})
    fold_positive = {
        fold: math.fsum(max(exec_c["baseline"][w], 0.0) for w in by_fold[fold])
        for fold in fold_ids
    }
    positive_total = math.fsum(fold_positive.values())
    concentration = max(fold_positive.values(), default=0.0) / positive_total if positive_total > 0 else None
    insufficient = any(counts[key] < floor for key, floor in floors.items())
    if insufficient:
        return {
            "sample_status": "INSUFFICIENT_EVIDENCE",
            "sample_warnings": [f"{key}={counts[key]} below frozen floor {floor}"
                                for key, floor in floors.items() if counts[key] < floor],
            "estimands": {key: {"estimate": None, "lower_95": None, "upper_95": None,
                                "raw_one_sided_p": 1.0, "status": "INSUFFICIENT_EVIDENCE_P1_PLACEHOLDER"}
                          for key in HYPOTHESES},
            "centered_bootstrap_errors": {key: None for key in HYPOTHESES},
            "max_positive_fold_share": concentration,
        }

    point = {
        HYPOTHESES[0]: _mean([pred_c[w] for w in weeks]),
        HYPOTHESES[1]: _mean([pred_c[w] - pred_i[w] for w in weeks]),
        HYPOTHESES[2]: min(_mean([exec_c[s][w] for w in weeks]) for s in SCENARIOS),
        HYPOTHESES[3]: _mean([exec_c["baseline"][w] - exec_i[w] for w in weeks]),
    }
    if set(expected_seeds) != set(HYPOTHESES) or any(set(expected_seeds[h]) != set(fold_ids) for h in HYPOTHESES):
        raise M4ValidationError("M1 EVALUATION_STARTED seeds do not cover every metric and fold")
    rngs = {}
    for metric in HYPOTHESES:
        rngs[metric] = {}
        for fold in fold_ids:
            seed = expected_seeds[metric][fold]
            if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**64:
                raise M4ValidationError("bootstrap seed is outside unsigned 64-bit range")
            rngs[metric][fold] = random.Random(seed)
    draws = {metric: [] for metric in HYPOTHESES}
    for _ in range(BOOTSTRAP_ITERATIONS):
        sampled = {}
        for metric in HYPOTHESES:
            sample = []
            for fold in fold_ids:
                rows = by_fold[fold]
                sample.extend(rows[index] for index in _block_indices(len(rows), rngs[metric][fold]))
            sampled[metric] = sample
        for metric in HYPOTHESES:
            sample = sampled[metric]
            if metric == HYPOTHESES[0]:
                value = _mean([pred_c[w] for w in sample])
            elif metric == HYPOTHESES[1]:
                value = _mean([pred_c[w] - pred_i[w] for w in sample])
            elif metric == HYPOTHESES[2]:
                value = min(_mean([exec_c[s][w] for w in sample]) for s in SCENARIOS)
            else:
                value = _mean([exec_c["baseline"][w] - exec_i[w] for w in sample])
            draws[metric].append(value)

    estimands = {}
    for metric in HYPOTHESES:
        series = sorted(draws[metric])
        estimate = point[metric]
        tail = sum(value - estimate >= estimate for value in series)
        estimands[metric] = {
            "estimate": estimate,
            "lower_95": _percentile(series, 0.025),
            "upper_95": _percentile(series, 0.975),
            "raw_one_sided_p": (1 + tail) / (BOOTSTRAP_ITERATIONS + 1),
            "status": "RAW_UNADJUSTED",
        }
    return {"sample_status": "SAMPLE_FLOORS_MET", "sample_warnings": [],
            "estimands": estimands,
            "centered_bootstrap_errors": {
                metric: [value - point[metric] for value in draws[metric]]
                for metric in HYPOTHESES
            },
            "max_positive_fold_share": concentration}


def _extract_started_seeds(events: Sequence[Mapping[str, Any]], attempt_id: str) -> dict[str, dict[str, int]]:
    rows = [event for event in events if event.get("event_type") == "EVALUATION_STARTED"
            and event.get("payload", {}).get("attempt_id") == attempt_id]
    if len(rows) != 1:
        raise M4ValidationError("completed attempt must have exactly one M1 EVALUATION_STARTED event")
    payload = rows[0]["payload"]
    seed_records = payload.get("seed_records")
    if not isinstance(seed_records, list):
        raise M4ValidationError("M1 start event has no seed records")
    folds = list(dict.fromkeys(row.get("fold_id") for row in seed_records if isinstance(row, Mapping)))
    metrics = list(dict.fromkeys(row.get("metric_id") for row in seed_records if isinstance(row, Mapping)))
    if not folds or any(not isinstance(value, str) or not value for value in folds):
        raise M4ValidationError("M1 start event fold IDs are malformed")
    if tuple(metrics) != HYPOTHESES:
        raise M4ValidationError("M1 start event hypothesis order differs from frozen M0")
    result = {metric: {} for metric in metrics}
    for row in seed_records:
        if not isinstance(row, Mapping) or row.get("metric_id") not in result or row.get("fold_id") not in folds:
            raise M4ValidationError("M1 seed record has an unknown fold or metric")
        encoded = row.get("seed_u64_decimal")
        if not isinstance(encoded, str) or not encoded.isascii() or not encoded.isdigit() or (len(encoded) > 1 and encoded[0] == "0"):
            raise M4ValidationError("M1 seed is not canonical unsigned decimal text")
        seed = int(encoded)
        if not 0 <= seed < 2**64 or row["fold_id"] in result[row["metric_id"]]:
            raise M4ValidationError("M1 seed is duplicated or outside the u64 range")
        result[row["metric_id"]][row["fold_id"]] = seed
    if any(set(result[metric]) != set(folds) for metric in metrics):
        raise M4ValidationError("M1 seed records do not cover the exact fold × hypothesis product")
    return result


def _verify_checkpoint_attempt(store: ExternalSecurityStore, expected_hash: str) -> None:
    try:
        latest, _anchor = store._trusted_anchor_without_database()
        digest = latest["checkpoint_sha256"]
        while digest is not None:
            checkpoint = store._load_checkpoint_by_hash(digest)
            if digest == expected_hash:
                return
            digest = checkpoint.get("previous_checkpoint_sha256")
        raise M4ValidationError("attempt M2 checkpoint is not a member of the independently trusted signed chain")
    except M4ValidationError:
        raise
    except Exception as exc:
        raise M4ValidationError("M2 historical checkpoint or external trust anchor failed verification") from exc


def _verify_trusted_prefix(store: ExternalSecurityStore, events: Sequence[Mapping[str, Any]], family_id: str) -> dict[str, Any]:
    """Verify M2's signed prefix while allowing only later M1 evaluation events.

    M3 lifecycle events are appended through the frozen M1 API after candidate
    generation. M2 checkpoints cover the sealed candidate family, so the M2
    trust anchor is a prefix of the complete M1 chain rather than its current
    tip. No protocol, candidate, or generation event is allowed in that suffix.
    """
    try:
        last, _anchor = store._trusted_anchor_without_database()
    except Exception as exc:
        raise M4ValidationError("M2 external identity, anchor, or signed checkpoint history is unverifiable") from exc
    if last.get("search_family_id") != family_id:
        raise M4ValidationError("trusted M2 identity belongs to a different search family")
    count = last.get("event_count")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= len(events):
        raise M4ValidationError("trusted M2 event prefix length is invalid")
    if events[count - 1].get("event_sha256") != last.get("event_tip_sha256"):
        raise M4ValidationError("trusted M2 checkpoint tip is not an exact prefix of the verified M1 chain")
    allowed_suffix = {
        "EVALUATION_STARTED", "EVALUATION_COMPLETED", "EVALUATION_FAILED",
        "CANDIDATE_EVALUATION_SKIPPED", "CANDIDATE_TECHNICAL_FAILURE",
        "CANDIDATE_INSUFFICIENT_EVIDENCE", "CANDIDATE_REJECTED",
        "RESEARCH_ADVANCED_PENDING_PROSPECTIVE", "APPROVED_FOR_FURTHER_RESEARCH",
    }
    if any(event.get("event_type") not in allowed_suffix for event in events[count:]):
        raise M4ValidationError("unanchored suffix contains non-M3 lifecycle mutations")
    return last


def _package_stats(path: Path) -> Mapping[str, Any]:
    try:
        data = json.loads((path / "statistics.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise M4ValidationError("sealed M3 package lacks valid statistics.json") from exc
    if data.get("multiplicity_adjustment_applied") is not False or data.get("qualification_decision") != "NOT_MADE_BY_M3":
        raise M4ValidationError("M3 package must contain raw statistics without correction or qualification")
    return data


def _validate_registered_folds(evidence: Mapping[str, Any], protocol: Mapping[str, Any],
                              package_fold_plan: Any) -> None:
    """Rebuild exact scored UTC weeks/fold ownership from frozen M0 boundaries."""
    from .m3_chronology import build_fold_windows, complete_utc_week_keys
    try:
        windows = build_fold_windows(protocol)
        expected_weeks: list[str] = []
        expected_owner: dict[str, str] = {}
        for fold in windows:
            for week in complete_utc_week_keys(fold.outer_test):
                if week in expected_owner:
                    raise M4ValidationError("registered outer folds share a UTC scored week")
                expected_weeks.append(week)
                expected_owner[week] = fold.fold_id
    except M4ValidationError:
        raise
    except Exception as exc:
        raise M4ValidationError("M1 protocol does not contain valid frozen chronological outer folds") from exc
    if evidence.get("week_keys") != expected_weeks or evidence.get("fold_by_week") != expected_owner:
        raise M4ValidationError("weekly evidence calendar or fold ownership differs from exact frozen M0 outer folds")
    if not isinstance(package_fold_plan, Mapping) or package_fold_plan.get("fold_ids") != [fold.fold_id for fold in windows]:
        raise M4ValidationError("M3 package fold-plan identity differs from frozen registered M0 folds")


def _verify_stats_equal(stored: Mapping[str, Any], rebuilt: Mapping[str, Any]) -> None:
    rows = stored.get("estimands")
    if not isinstance(rows, list) or len(rows) != len(HYPOTHESES):
        raise M4ValidationError("M3 statistics must contain exactly the four frozen estimands")
    by_id = {row.get("estimand_id"): row for row in rows if isinstance(row, dict)}
    if set(by_id) != set(HYPOTHESES) or len(by_id) != len(rows):
        raise M4ValidationError("M3 statistics contain duplicate or missing confirmatory estimands")
    if stored.get("sample_status") != rebuilt["sample_status"]:
        raise M4ValidationError("M3 sample status differs from independent reconstruction")
    if stored.get("sample_warnings") != rebuilt["sample_warnings"]:
        raise M4ValidationError("M3 sample-floor warnings differ from independent reconstruction")
    if stored.get("bootstrap_iterations") not in (None, BOOTSTRAP_ITERATIONS):
        raise M4ValidationError("M3 bootstrap iteration count differs from frozen M0")
    if stored.get("block_length_weeks") not in (None, BLOCK_LENGTH_WEEKS):
        raise M4ValidationError("M3 bootstrap block length differs from frozen M0")
    share = stored.get("max_positive_fold_share")
    expected_share = rebuilt["max_positive_fold_share"]
    if share is None and expected_share is None:
        pass
    elif isinstance(share, bool) or not isinstance(share, (int, float)) or expected_share is None or not math.isclose(
            float(share), float(expected_share), rel_tol=0, abs_tol=1e-14):
        raise M4ValidationError("M3 fold concentration differs from independent reconstruction")
    for name in HYPOTHESES:
        old, new = by_id[name], rebuilt["estimands"][name]
        for key in ("estimate", "lower_95", "upper_95", "raw_one_sided_p"):
            old_value = old.get(key)
            new_value = new.get(key)
            if old_value is None and new_value is None:
                continue
            if isinstance(old_value, bool) or not isinstance(old_value, (int, float)) or not math.isclose(
                    float(old_value), float(new_value), rel_tol=0, abs_tol=1e-14):
                raise M4ValidationError(f"M3 statistic mismatch for {name}.{key}")
        if old.get("bootstrap_iterations") not in (None, BOOTSTRAP_ITERATIONS) or old.get("block_length_weeks") not in (None, BLOCK_LENGTH_WEEKS):
            raise M4ValidationError(f"M3 bootstrap parameters differ from frozen M0 for {name}")
        if old.get("status") != new["status"]:
            raise M4ValidationError(f"M3 status differs from independent reconstruction for {name}")


def _completed_hash(events: Sequence[Mapping[str, Any]], attempt_id: str) -> str | None:
    rows = [e for e in events if e.get("event_type") == "EVALUATION_COMPLETED"
            and e.get("payload", {}).get("attempt_id") == attempt_id]
    if len(rows) > 1:
        raise M4ValidationError("attempt has multiple completion events")
    return rows[0]["payload"].get("result_sha256") if rows else None


def validate_family(registry: m1.ExperimentRegistry, *,
                    external_store: ExternalSecurityStore,
                    package_paths: Mapping[str, str | Path],
                    output_path: str | Path,
                    inversion_amendment: Mapping[str, Any] | None = None) -> FamilyValidation:
    """Verify and publish one complete family report; never evaluates candidates."""
    from .m3_lifecycle import PackageError, verify_package

    amendment_identity: dict[str, str] | None = None
    if inversion_amendment is not None:
        amendment_id, amendment_sha, approval_status = _validate_inversion_amendment(inversion_amendment)
        amendment_identity = {
            "schema_version": INVERSION_AMENDMENT_SCHEMA,
            "amendment_id": amendment_id,
            "document_sha256": amendment_sha,
            "status": approval_status,
        }

    events = registry.events()
    if Path(external_store.registry.root).resolve() != Path(registry.root).resolve():
        raise M4ValidationError("M2 external trust store is bound to a different M1 registry")
    try:
        verified_db = registry.verify()
        verified_stream = m1.verify_event_stream(events, expected_tip_sha256=verified_db.tip_sha256)
    except Exception as exc:
        raise M4ValidationError("M1 database/event chain verification failed") from exc
    if verified_stream.search_family_id != verified_db.search_family_id:
        raise M4ValidationError("M1 independent stream and database disagree on search family")
    if verified_db.search_family_id is None:
        raise M4ValidationError("M1 registry has no persistent search-family identity")
    latest_checkpoint = _verify_trusted_prefix(external_store, events, verified_db.search_family_id)
    protocols = {e["protocol_id"]: e["payload"]["protocol"] for e in events
                 if e.get("event_type") == "PROTOCOL_PRE_REGISTERED"}
    registrations = [e for e in events if e.get("event_type") == "CANDIDATE_ATTEMPT_REGISTERED"]
    if not registrations:
        raise M4ValidationError("persistent family contains no registered attempts")
    states = registry.attempt_states()
    entries = registry.multiplicity_entries()
    grouped = {}
    for row in entries:
        grouped.setdefault(row["attempt_id"], {})[row["hypothesis"]] = row
    if len(entries) != len(registrations) * 4 or any(set(grouped.get(e["payload"]["attempt_id"], {})) != set(HYPOTHESES) for e in registrations):
        raise M4ValidationError("M1 multiplicity reconstruction omitted or duplicated attempt hypotheses")

    report_attempts = []
    hypotheses = []
    centered_errors_by_hypothesis: dict[tuple[str, str], Sequence[float] | None] = {}
    for event in sorted(registrations, key=lambda item: item["payload"]["ordinal"]):
        payload = event["payload"]
        attempt_id = payload["attempt_id"]
        state = states.get(attempt_id)
        if state is None:
            raise M4ValidationError("registered attempt has no reconstructed M1 lifecycle state")
        multiplicity_rows = grouped[attempt_id]
        if state not in {"COMPLETED", "INSUFFICIENT_EVIDENCE", "REJECTED", "RESEARCH_ADVANCED_PENDING_PROSPECTIVE"}:
            if any(multiplicity_rows[name]["p_value"] != 1.0 for name in HYPOTHESES):
                raise M4ValidationError("unresolved or unsuccessful attempt does not retain four conservative p=1 entries")
        row_out = {"attempt_id": attempt_id, "ordinal": payload["ordinal"],
                   "protocol_id": event["protocol_id"], "status": state,
                   "hypotheses": []}
        protocol_for_attempt = protocols[event["protocol_id"]]
        source_for_attempt = protocol_for_attempt.get("source", {})
        row_out["provenance"] = {
            "candidate_spec_sha256": payload["candidate_spec_bytes_sha256"],
            "protocol_sha256": _sha(m1.canonical_json_bytes(protocol_for_attempt)),
            "source_commit": source_for_attempt.get("commit"),
            "source_dirty": not source_for_attempt.get("worktree_clean"),
            "dataset_hashes": {key: value for key, value in protocol_for_attempt.get("data", {}).items()
                               if key.endswith("sha256")},
            "configuration_hashes": {key: value for key, value in source_for_attempt.items()
                                     if key.endswith("sha256")},
        }
        if state == "COMPLETED" or state in {"INSUFFICIENT_EVIDENCE", "REJECTED", "RESEARCH_ADVANCED_PENDING_PROSPECTIVE"}:
            package = package_paths.get(attempt_id)
            if package is None:
                raise M4ValidationError(f"attempt {attempt_id} has completed evidence but no sealed M3 package")
            path = Path(package)
            try:
                manifest, package_sha = verify_package(path, expected_attempt_id=attempt_id)
            except PackageError as exc:
                raise M4ValidationError(f"attempt {attempt_id} M3 package failed integrity verification") from exc
            if manifest.get("protocol_id") != event["protocol_id"]:
                raise M4ValidationError("M3 package protocol differs from its M1 attempt")
            protocol = protocols[event["protocol_id"]]
            if manifest.get("candidate_sha256") != payload["candidate_spec_bytes_sha256"]:
                raise M4ValidationError("M3 package candidate digest differs from immutable M1 candidate bytes")
            if manifest.get("protocol_sha256") != _sha(m1.canonical_json_bytes(protocol)):
                raise M4ValidationError("M3 package protocol digest differs from immutable M1 protocol bytes")
            source = protocol.get("source", {})
            data = protocol.get("data", {})
            expected_dirty = not source.get("worktree_clean")
            expected_inputs = {
                "signals": data.get("signals_sha256"),
                "candles": data.get("candles_sha256"),
                "phase5_manifest": data.get("phase5_manifest_sha256"),
                "phase4_config": source.get("phase4_config_sha256"),
            }
            package_inputs = manifest.get("input_hashes")
            required_input_names = {
                "signals", "candles", "phase5_manifest", "counterfactual_ledger",
                "counterfactual_metadata", "phase4_config",
            }
            if manifest.get("source_commit") != source.get("commit") or manifest.get("source_dirty") is not expected_dirty:
                raise M4ValidationError("M3 package source commit or dirty state differs from the frozen protocol")
            if not isinstance(package_inputs, dict) or set(package_inputs) != required_input_names or any(
                    package_inputs.get(name) != digest for name, digest in expected_inputs.items()):
                raise M4ValidationError("M3 package omits or conflicts with a protocol-pinned historical input hash")
            if any(not isinstance(value, str) or len(value) != 64
                   or any(character not in "0123456789abcdef" for character in value)
                   for value in package_inputs.values()):
                raise M4ValidationError("M3 package contains a malformed pinned-input hash")
            if manifest.get("signal_generation_provenance") != (
                    "hash-pinned frozen signal rows; source-generation provenance not reconstructed"):
                raise M4ValidationError("M3 package does not preserve the frozen signal provenance limitation")
            if manifest.get("checkpoint_sha256") is None:
                raise M4ValidationError("M3 package has no M2 checkpoint identity")
            _verify_checkpoint_attempt(external_store, manifest["checkpoint_sha256"])
            # The package's M1 tip must equal its particular trusted historical
            # M2 checkpoint prefix, even when the family later advances.
            checkpoint = external_store._load_checkpoint_by_hash(manifest["checkpoint_sha256"])
            if checkpoint.get("event_tip_sha256") != manifest.get("registry_tip_sha256"):
                raise M4ValidationError("package registry tip differs from its signed M2 checkpoint")
            completed_sha = _completed_hash(events, attempt_id)
            if completed_sha is None or completed_sha != package_sha:
                raise M4ValidationError("M3 package manifest hash differs from M1 completion record")
            started_event = next(e for e in events if e.get("event_type") == "EVALUATION_STARTED"
                                 and e.get("payload", {}).get("attempt_id") == attempt_id)
            if manifest.get("seed_records") != started_event["payload"].get("seed_records"):
                raise M4ValidationError("M3 package seed records differ from the immutable M1 start event")
            completion_event = next(e for e in events if e.get("event_type") == "EVALUATION_COMPLETED"
                                    and e.get("payload", {}).get("attempt_id") == attempt_id)
            package_resources = manifest.get("resource_usage", {})
            completion_resources = completion_event["payload"].get("resource_usage", {})
            if any(package_resources.get(key) != completion_resources.get(key)
                   for key in ("wall_seconds", "peak_memory_mb", "worker_count")):
                raise M4ValidationError("M3 package resource record differs from the M1 completion event")
            stats = _package_stats(path)
            try:
                evidence_bytes = (path / "weekly_evidence.json").read_bytes()
                evidence = json.loads(evidence_bytes.decode("utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise M4ValidationError("M3 package lacks independently reconstructible weekly_evidence.json") from exc
            if _canonical(evidence) != evidence_bytes:
                raise M4ValidationError("weekly evidence must use canonical JSON bytes")
            _validate_registered_folds(evidence, protocol, manifest.get("fold_plan"))
            seeds = _extract_started_seeds(events, attempt_id)
            rebuilt = reconstruct_raw_statistics(evidence, expected_seeds=seeds)
            _verify_stats_equal(stats, rebuilt)
            completed = next((e["payload"].get("confirmatory_p_values", {}) for e in events
                              if e.get("event_type") == "EVALUATION_COMPLETED"
                              and e.get("payload", {}).get("attempt_id") == attempt_id), {})
            if any(name not in HYPOTHESES for name in completed):
                raise M4ValidationError("M1 completion record contains an unknown confirmatory hypothesis")
            if state == "INSUFFICIENT_EVIDENCE" and rebuilt["sample_status"] != "INSUFFICIENT_EVIDENCE":
                raise M4ValidationError("M1 insufficient-evidence state conflicts with reconstructed sample gates")
            if state in {"COMPLETED", "REJECTED", "RESEARCH_ADVANCED_PENDING_PROSPECTIVE"} and rebuilt["sample_status"] != "SAMPLE_FLOORS_MET":
                raise M4ValidationError("M1 completed disposition conflicts with reconstructed insufficient sample floors")
            for name in HYPOTHESES:
                p = rebuilt["estimands"][name]["raw_one_sided_p"]
                if name not in completed:
                    if multiplicity_rows[name]["p_value"] != 1.0 or not multiplicity_rows[name]["placeholder"]:
                        raise M4ValidationError("a missing M1 raw p-value did not receive its conservative p=1 placeholder")
                    family_p, source = 1.0, "frozen_p1_placeholder_missing_m1_stat"
                    centered_errors_by_hypothesis[(attempt_id, name)] = None
                else:
                    if not math.isclose(float(completed[name]), p, rel_tol=0, abs_tol=1e-14):
                        raise M4ValidationError("M1 completion p-value differs from independent raw-statistic reconstruction")
                    if not math.isclose(float(multiplicity_rows[name]["p_value"]), p, rel_tol=0, abs_tol=1e-14):
                        raise M4ValidationError("M1 multiplicity entry differs from independently reconstructed attempt p-value")
                    family_p, source = p, "independently_reconstructed"
                    centered_errors_by_hypothesis[(attempt_id, name)] = rebuilt[
                        "centered_bootstrap_errors"][name]
                hypotheses.append({"attempt_id": attempt_id, "ordinal": payload["ordinal"],
                                   "hypothesis": name, "raw_p": family_p,
                                   "reconstructed_raw_p": p,
                                   "reconstructed_estimate": rebuilt["estimands"][name]["estimate"],
                                   "source": source,
                                   "simultaneous_lower_bound": None,
                                   "bound_status": "NOT_COMPUTED"})
            row_out["evidence"] = {"package_sha256": package_sha, "sample_status": rebuilt["sample_status"],
                                   "estimands": rebuilt["estimands"],
                                   "max_positive_fold_share": rebuilt["max_positive_fold_share"],
                                   "m2_checkpoint_sha256": manifest["checkpoint_sha256"],
                                   "m3_input_hashes": manifest["input_hashes"]}
        else:
            # All incomplete/unsuccessful dispositions count four times at p=1.
            for name in HYPOTHESES:
                hypotheses.append({"attempt_id": attempt_id, "ordinal": payload["ordinal"],
                                   "hypothesis": name, "raw_p": 1.0, "source": "frozen_p1_placeholder",
                                   "reconstructed_estimate": None,
                                   "simultaneous_lower_bound": None,
                                   "bound_status": "NOT_COMPUTED"})
                centered_errors_by_hypothesis[(attempt_id, name)] = None
        for name in HYPOTHESES:
            row_out["hypotheses"].append(name)
        report_attempts.append(row_out)

    hypotheses.sort(key=lambda item: (item["ordinal"], HYPOTHESES.index(item["hypothesis"])))
    adjusted = holm_adjust([item["raw_p"] for item in hypotheses])
    for row, value in zip(hypotheses, adjusted):
        row["holm_adjusted_p"] = value
    inversion_status = "AMENDMENT_NOT_SUPPLIED"
    if inversion_amendment is not None:
        ordered_hypotheses = sorted(hypotheses, key=lambda item: (
            item["ordinal"], HYPOTHESES.index(item["hypothesis"])))
        bounds = bonferroni_simultaneous_lower_bounds(
            [row.get("reconstructed_estimate") for row in ordered_hypotheses],
            [centered_errors_by_hypothesis.get((row["attempt_id"], row["hypothesis"]))
             for row in ordered_hypotheses],
            alpha=ALPHA,
        )
        # The amendment is still proposed; output is evidence for review only.
        for row, bound in zip(ordered_hypotheses, bounds):
            row["simultaneous_lower_bound"] = bound
            missing_function = centered_errors_by_hypothesis.get((row["attempt_id"], row["hypothesis"])) is None
            row["bound_status"] = (
                "P1_PLACEHOLDER_NO_FINITE_BOUND" if missing_function
                else "APPROVED_METHOD_BOUND_NOT_QUALIFICATION"
            )
        inversion_status = "APPROVED_BONFERRONI_BOUNDS_CALCULATED"
    unresolved = [row["attempt_id"] for row in report_attempts
                  if row["status"] in {"UNEVALUATED", "TECHNICAL_EVALUATION_FAILURE", "RUNNING", "VALIDATED", "SUBMITTED"}]
    already_advanced = any(row["status"] == "RESEARCH_ADVANCED_PENDING_PROSPECTIVE" for row in report_attempts)
    if len(hypotheses) > 144:
        raise M4ValidationError("family multiplicity count exceeds frozen 144-hypothesis ceiling")
    complete_attempt_family = not unresolved
    report = {
        "schema": REPORT_SCHEMA,
        "search_family_id": verified_db.search_family_id,
        "protocol_ids": sorted(protocols),
        "registry_tip_sha256": verified_db.tip_sha256,
        "registry_event_count": verified_db.event_count,
        "trusted_checkpoint_sha256": latest_checkpoint["checkpoint_sha256"],
        "attempt_count": len(report_attempts),
        "hypothesis_count": len(hypotheses),
        "attempts": report_attempts,
        "hypotheses": hypotheses,
        "holm_alpha": ALPHA,
        "holm_method": "holm_stepdown_familywise",
        "qualification_status": "QUALIFICATION_NOT_RUN" if amendment_identity else "BLOCKED_AMENDMENT_NOT_SUPPLIED",
        "inversion_amendment": amendment_identity,
        "inversion_status": inversion_status,
        "family_completion_status": "ALL_ATTEMPTS_TERMINAL" if complete_attempt_family else "ATTEMPTS_UNRESOLVED",
        "family_outcome": None,
        "eligible_terminal_outcome_if_qualification_enabled": (
            "NO_QUALIFYING_CANDIDATE" if complete_attempt_family and not already_advanced else None
        ),
        "no_qualifying_candidate_authoritative": False,
        "unresolved_attempt_ids": unresolved,
        "notes": [
            "M4 reconstructs evidence and Holm adjusted p-values; it does not execute candidates.",
            "The exact M0 full-family Bonferroni-bound amendment is approved by the pinned protocol-owner decision record.",
            "Holm-adjusted p-values remain family-testing evidence; simultaneous numerical bounds use full-family alpha/m marginal inversion.",
            "Qualification was not run by M4 family reconstruction; method approval does not authorize historical evaluation, candidate qualification, or holdout access.",
            "Bootstrap bound coverage remains conditional on valid marginal bootstrap tests; approval does not establish exact finite-sample validity.",
            "All data through 2026-08-19 remain previously observed research data, never untouched holdout data.",
        ],
    }
    _validate_report_schema(report)
    output = _safe_report_path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise M4ValidationError("family report publication is immutable and refuses overwrite")
    data = _canonical(report)
    try:
        with output.open("xb") as handle:
            handle.write(data)
            handle.flush()
            import os
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise M4ValidationError("family report was concurrently published") from exc
    return FamilyValidation(report, _sha(data), output)


def qualify_family(*_args: Any, **_kwargs: Any) -> None:
    """Method is approved; qualification execution remains a separate authorization gate."""
    raise QualificationBlocked(
        "M0 bound methodology is approved, but historical qualification execution has not been separately authorized"
    )
