"""Frozen M0 per-attempt bootstrap evidence. No family correction or qualification."""
from __future__ import annotations

from dataclasses import dataclass
import math
import random
from typing import Mapping, Sequence


class StatisticalEvidenceError(ValueError):
    """Weekly evidence or registered bootstrap seeds are invalid."""


def seeds_from_m1_started_event(
    event: Mapping,
    *,
    attempt_id: str,
    protocol_id: str,
    fold_ids: Sequence[str],
    metric_ids: Sequence[str],
) -> dict[str, dict[str, int]]:
    """Reconstruct exact u64 seeds from the immutable M1 EVALUATION_STARTED event."""
    if event.get("event_type") != "EVALUATION_STARTED" or event.get("protocol_id") != protocol_id:
        raise StatisticalEvidenceError("bootstrap seeds must come from this attempt's M1 EVALUATION_STARTED event")
    payload = event.get("payload")
    if not isinstance(payload, Mapping) or payload.get("attempt_id") != attempt_id:
        raise StatisticalEvidenceError("M1 seed event attempt identity differs")
    expected = {(fold, metric) for fold in fold_ids for metric in metric_ids}
    rows = payload.get("seed_records")
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise StatisticalEvidenceError("M1 seed event is missing or duplicates required fold/metric seeds")
    result = {metric: {} for metric in metric_ids}
    for row in rows:
        if not isinstance(row, Mapping):
            raise StatisticalEvidenceError("M1 seed record is malformed")
        fold, metric, encoded = row.get("fold_id"), row.get("metric_id"), row.get("seed_u64_decimal")
        if (fold, metric) not in expected or fold in result.get(metric, {}):
            raise StatisticalEvidenceError("M1 seed records contain an unknown or duplicate fold/metric")
        if not isinstance(encoded, str) or not encoded.isascii() or not encoded.isdigit() or (len(encoded) > 1 and encoded[0] == "0"):
            raise StatisticalEvidenceError("M1 seed must be canonical unsigned decimal text")
        value = int(encoded)
        if not 0 <= value < 2**64:
            raise StatisticalEvidenceError("M1 seed is outside unsigned 64-bit range")
        result[metric][fold] = value
    if any(set(result[metric]) != set(fold_ids) for metric in metric_ids):
        raise StatisticalEvidenceError("M1 seed event does not cover the registered fold/metric product")
    return result


BOOTSTRAP_ITERATIONS = 100_000
BLOCK_LENGTH_WEEKS = 4
ALPHA = 0.05
SAMPLE_FLOORS = {
    "predictive_opportunities": 200,
    "predictive_occupied_weeks": 30,
    "counterfactual_completed_trades": 100,
    "counterfactual_occupied_weeks": 30,
    "executable_positions": 100,
    "executable_occupied_weeks": 30,
    "executable_outer_folds": 6,
}
REGIME_SAMPLE_FLOORS = {"regime_observations": 30, "regime_occupied_weeks": 12}


@dataclass(frozen=True, slots=True)
class RawEstimandEvidence:
    estimand_id: str
    estimate: float | None
    lower_95: float | None
    upper_95: float | None
    raw_one_sided_p: float
    status: str
    bootstrap_iterations: int
    block_length_weeks: int


@dataclass(frozen=True, slots=True)
class AttemptStatisticalEvidence:
    estimands: tuple[RawEstimandEvidence, ...]
    sample_status: str
    sample_warnings: tuple[str, ...]
    inference_owner: str = "M4 persistent-family validator"
    multiplicity_adjustment_applied: bool = False
    qualification_decision: str = "NOT_MADE_BY_M3"
    max_positive_fold_share: float | None = None


def sample_gate(counts: Mapping[str, int], *, regime_claim_requested: bool = False) -> tuple[str, tuple[str, ...]]:
    warnings = []
    floors = dict(SAMPLE_FLOORS)
    if regime_claim_requested:
        floors.update(REGIME_SAMPLE_FLOORS)
    else:
        # Regime coverage remains reported, but D0-4 applies its 30/12 floor
        # only to a regime-specific claim, not to the four overall estimands.
        for name in REGIME_SAMPLE_FLOORS:
            count = counts.get(name)
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise StatisticalEvidenceError(f"sample count {name} must be a nonnegative integer")
    for name, floor in floors.items():
        count = counts.get(name)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise StatisticalEvidenceError(f"sample count {name} must be a nonnegative integer")
        if count < floor:
            warnings.append(f"{name}={count} below frozen floor {floor}")
    return ("INSUFFICIENT_EVIDENCE" if warnings else "SAMPLE_FLOORS_MET", tuple(warnings))


def _finite(values: Sequence[float], label: str) -> tuple[float, ...]:
    result = tuple(float(value) for value in values)
    if not result or any(not math.isfinite(value) for value in result):
        raise StatisticalEvidenceError(f"{label} must contain finite weekly observations")
    return result


def _percentile(sorted_values: Sequence[float], probability: float) -> float:
    # Linear interpolation at (n-1)*p, frozen here for reproducible percentile bounds.
    position = (len(sorted_values) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return sorted_values[lower] * (1.0 - fraction) + sorted_values[upper] * fraction


def _block_indices(length: int, rng: random.Random) -> tuple[int, ...]:
    if length < BLOCK_LENGTH_WEEKS:
        raise StatisticalEvidenceError("each bootstrap fold must contain at least four consecutive weeks")
    indices: list[int] = []
    while len(indices) < length:
        start = rng.randrange(0, length - BLOCK_LENGTH_WEEKS + 1)
        indices.extend(range(start, start + BLOCK_LENGTH_WEEKS))
    return tuple(indices[:length])


def _mean(values: Sequence[float]) -> float:
    return math.fsum(values) / len(values)


def evaluate_confirmatory_estimands(
    *,
    week_keys: Sequence[str],
    fold_by_week: Mapping[str, str],
    predictive_candidate: Mapping[str, float],
    predictive_incumbent: Mapping[str, float],
    executable_candidate_scenarios: Mapping[str, Mapping[str, float]],
    executable_incumbent_baseline: Mapping[str, float],
    seeds_by_metric_and_fold: Mapping[str, Mapping[str, int]],
    sample_counts: Mapping[str, int],
    regime_claim_requested: bool = False,
) -> AttemptStatisticalEvidence:
    """Compute the four frozen raw M0 estimates and uncertainty measures.

    Fold-specific seed records are M1-derived. Non-circular 4-week blocks are
    shared across all paired lanes and cost scenarios within each fold. No Holm
    adjustment or candidate qualification is attempted here.
    """
    weeks = tuple(week_keys)
    if not weeks or len(set(weeks)) != len(weeks):
        raise StatisticalEvidenceError("weekly keys must be a nonempty unique sequence")
    fold_values = [fold_by_week.get(week) for week in weeks]
    if any(not isinstance(fold, str) or not fold for fold in fold_values):
        raise StatisticalEvidenceError("every scored week must map to a registered outer fold")
    folds = tuple(sorted(set(fold_values)))
    lane_maps: list[Mapping[str, float]] = [predictive_candidate, predictive_incumbent,
                                           executable_incumbent_baseline]
    lane_maps.extend(executable_candidate_scenarios.values())
    required_scenarios = {"baseline", "fee_10bps_per_side_only", "slippage_10bps_per_side_only", "funding_2bps_per_8h_only"}
    if set(executable_candidate_scenarios) != required_scenarios:
        raise StatisticalEvidenceError("candidate executable inputs must include the frozen baseline and three one-factor stresses")
    for lane in lane_maps:
        if set(lane) != set(weeks):
            raise StatisticalEvidenceError("all candidate/incumbent lanes must cover identical UTC week keys")
        if any(not math.isfinite(float(lane[week])) for week in weeks):
            raise StatisticalEvidenceError("weekly lane contains non-finite evidence")
    for fold in folds:
        fold_weeks = [week for week in weeks if fold_by_week[week] == fold]
        if len(fold_weeks) < BLOCK_LENGTH_WEEKS:
            raise StatisticalEvidenceError("registered outer fold has fewer than four scored weeks")

    sample_status, warnings = sample_gate(sample_counts, regime_claim_requested=regime_claim_requested)
    fold_positive = {
        fold: math.fsum(max(float(executable_candidate_scenarios["baseline"][week]), 0.0)
                        for week in weeks if fold_by_week[week] == fold)
        for fold in folds
    }
    total_positive = math.fsum(fold_positive.values())
    concentration = max(fold_positive.values(), default=0.0) / total_positive if total_positive > 0 else None
    estimand_ids = (
        "predictive_absolute_weekly_score", "predictive_paired_weekly_delta",
        "robust_executable_absolute_weekly_return", "executable_paired_weekly_delta",
    )
    if sample_status == "INSUFFICIENT_EVIDENCE":
        return AttemptStatisticalEvidence(tuple(RawEstimandEvidence(
            key, None, None, None, 1.0, "INSUFFICIENT_EVIDENCE_P1_PLACEHOLDER",
            BOOTSTRAP_ITERATIONS, BLOCK_LENGTH_WEEKS) for key in estimand_ids),
            sample_status, warnings, max_positive_fold_share=concentration)

    for key in estimand_ids:
        metric_seeds = seeds_by_metric_and_fold.get(key)
        if not isinstance(metric_seeds, Mapping) or set(metric_seeds) != set(folds):
            raise StatisticalEvidenceError(f"M1-derived seeds are missing for {key}")
        if any(not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed < 2**64
               for seed in metric_seeds.values()):
            raise StatisticalEvidenceError("bootstrap seed must be an unsigned 64-bit integer")

    point = [
        _mean([float(predictive_candidate[w]) for w in weeks]),
        _mean([float(predictive_candidate[w]) - float(predictive_incumbent[w]) for w in weeks]),
        min(_mean([float(values[w]) for w in weeks]) for values in executable_candidate_scenarios.values()),
        _mean([float(executable_candidate_scenarios["baseline"][w]) - float(executable_incumbent_baseline[w]) for w in weeks]),
    ]
    distributions = [[] for _ in estimand_ids]
    rng_by_metric_fold = {
        key: {fold: random.Random(seeds_by_metric_and_fold[key][fold]) for fold in folds}
        for key in estimand_ids
    }
    fold_weeks_map = {fold: tuple(w for w in weeks if fold_by_week[w] == fold) for fold in folds}
    for _ in range(BOOTSTRAP_ITERATIONS):
        samples = []
        for metric_index, key in enumerate(estimand_ids):
            sample_weeks = []
            for fold in folds:
                fw = fold_weeks_map[fold]
                indices = _block_indices(len(fw), rng_by_metric_fold[key][fold])
                sample_weeks.extend(fw[index] for index in indices)
            if metric_index == 0:
                value = _mean([float(predictive_candidate[w]) for w in sample_weeks])
            elif metric_index == 1:
                value = _mean([float(predictive_candidate[w]) - float(predictive_incumbent[w]) for w in sample_weeks])
            elif metric_index == 2:
                value = min(_mean([float(values[w]) for w in sample_weeks])
                            for values in executable_candidate_scenarios.values())
            else:
                value = _mean([float(executable_candidate_scenarios["baseline"][w]) -
                               float(executable_incumbent_baseline[w]) for w in sample_weeks])
            samples.append(value)
        for index, value in enumerate(samples):
            distributions[index].append(value)

    output = []
    for key, estimate, draws in zip(estimand_ids, point, distributions):
        draws.sort()
        lower = _percentile(draws, ALPHA / 2)
        upper = _percentile(draws, 1 - ALPHA / 2)
        null_tail = sum(1 for value in draws if (value - estimate) >= estimate)
        raw_p = (1 + null_tail) / (BOOTSTRAP_ITERATIONS + 1)
        output.append(RawEstimandEvidence(key, estimate, lower, upper, raw_p, "RAW_UNADJUSTED",
                                          BOOTSTRAP_ITERATIONS, BLOCK_LENGTH_WEEKS))
    return AttemptStatisticalEvidence(tuple(output), sample_status, warnings,
                                      max_positive_fold_share=concentration)
