# Athena Evolution Engine — Milestone 3 Design

**Status:** Accepted in principle; implementation authorization is conditional on explicit approval of the protocol-owner decision in §1. Authoritative evaluations require a separate later authorization and verified Linux isolation.
**Baseline:** `d22a1f45b8a3f43c0365ba0004e8f83686554f82`.
**Scope:** Isolated, historical-only evaluation of valid M1-registered/M2-generated declarative candidates. M0/M1/M2 contracts and Phase 3–5 artifacts remain unchanged.

## 1. Protocol-owner decision: weekly executable returns

**Decision status: APPROVED BY PROTOCOL OWNER on 2026-11-20.** The approved interpretation clarifies the M0 weekly equity calculation; it is not a blanket exception that turns all weeks without fills into mark-to-market weeks. A week with open exposure is an exposure week, even without fills; only a week with neither exposure nor trade activity receives the frozen zero-trade return of zero. No M0 schema or numeric constant changes.

Use UTC ISO weeks and mark account equity at each week boundary using the frozen Phase 4 equity/mark convention. If a position is open at any point during a week, weekly return is `E_week_end / E_week_start - 1`, including a week with no fills. Such a week is not classified as a zero-trade week because position exposure existed. Set weekly return to zero only when there was no open-position exposure during the week and no trade activity. Do not force-close at weekly boundaries; preserve frozen fold-end close behavior. Record marks, fills, funding, realized P&L, and unrealized P&L separately.

### Deterministic fixture

- Starting equity: 1,000.00; one long unit; no fees, slippage, or funding in this unit fixture.
- Enter Sunday 2026-11-22 at 100.00. At Monday 2026-11-23 00:00 UTC the marked equity is 1,000.00.
- Carry without fills through ISO week 2026-W48. Mark at Monday 2026-11-30 00:00 UTC at 110.00: equity is 1,010.00, so W48 return is `1010/1000 - 1 = 0.01` (1%). It is not a zero-trade week because exposure existed.
- Carry to Tuesday 2026-12-01 and exit at 112.00. The exit belongs to W49 and W49 equity return is computed from its own boundary marks; W48 is not rewritten.
- A parallel no-position/no-activity fixture over W48 yields exactly zero.

Acceptance: the fixture must deterministically assert ISO-week assignment, entry/exit attribution, boundary marks, returns, and no forced weekly close. Across consecutive weeks without external cash flows, multiplying `(1 + weekly_return)` must equal `ending_equity / starting_equity` within the ledger's exact decimal arithmetic. The no-exposure/no-activity control week is exactly zero.

## 2. Scientific and provenance constraints

- Predictive 96-bar labels anchor to the original signal candle timestamp/index and reference price, not `available_at`. `available_at` governs decision eligibility. Label endpoint must be strictly before a fold boundary; crossing labels are purged. The frozen embargo, warm-up, terminal entry blackout, and position horizon remain exactly as M0 defines.
- The opportunity set is the union of the frozen signal records by instrument and UTC `available_at`. Candidates may select, combine, filter, or abstain only over records in that set. They cannot create signals, timestamps, features, directions, stops, targets, or confidence values.
- Historical signal bytes are hash-pinned inputs, but the capture metadata lacks source commit/source-file hashes for signal generation. M3 will not regenerate or substitute these rows and will not claim source-reconstructed provenance. Historical protocols explicitly pinning these bytes and classifying them as frozen previously observed records may proceed. Any protocol requiring regenerated/source-reconstructed signals or making that provenance claim fails admission.
- Data through 2026-08-19 are previously observed historical research data, never an untouched holdout. M3 has no holdout access path.
- M3 preserves per-attempt raw evidence and M0-prescribed unadjusted statistics. M4 owns persistent-family Holm correction and authoritative qualification. M3 does not rank, qualify, promote, or emit Holm-adjusted claims.

## 3. Components and boundaries

Admission verifier → pinned read-only input resolver → chronological fold planner → candidate-independent opportunity builder → finite declarative policy interpreter → three isolated evidence lanes (predictive, independent counterfactual, executable portfolio) → accounting/evidence validator → immutable package sealer → M1 lifecycle adapter.

Use `experiment_registry.py` public APIs and verified M2 identity/checkpoint/catalog/generation records. Reuse frozen signal validation (`frozen_signals.py`), execution (`execution.py`), portfolio simulation and ledger (`portfolio.py`, `portfolio_ledger.py`, `portfolio_models.py`), and scientifically compatible descriptive calculations from Phase 5/research modules without changing their behavior. Verify all registered source/config hashes before use. Do not use the Phase 4 ledger as an evaluation ledger; each candidate and incumbent fold/scenario gets an independent fresh ledger.

The M1 transitions are fixed: `VALIDATED → RUNNING` using `record_evaluation_started`; `RUNNING → COMPLETED` via `complete_evaluation`; then `COMPLETED → INSUFFICIENT_EVIDENCE` via `mark_insufficient` where frozen sample/coverage floors fail; `VALIDATED → UNEVALUATED` via `skip_attempt`; and `RUNNING → TECHNICAL_EVALUATION_FAILURE` via `fail_attempt`. No new M1 state or schema is introduced. M3 does not call `reject_attempt` for performance outcomes.

## 4. Isolation, recovery, and outputs

Authoritative execution requires a dedicated restricted Linux VM on an identified hypervisor. Pin a reviewed immutable VM image digest; provide no network, credentials, keys, registry DB, host home, unrelated repository paths, or holdout. Mount inputs read-only and outputs exclusively with a hard byte cap. Run unprivileged, without privilege escalation/devices/host PID or IPC, and enforce M0 resource ceilings externally. Independently verify hypervisor/VM configuration, image digest, mounts, privileges, network denial, limits, and transfer boundary. Windows CI is supplemental. No evaluation admission until the signed verification record exists and a separate evaluation authorization is granted.

Use attempt-specific staging and immutable dated output directories. A sealed package without an M1 completion event is verified and finalized on the same attempt without rerunning the worker. A running attempt with no valid sealed package becomes terminal technical failure. Partial/unsealed packages are never complete. Conflicting digests fail closed. Recovery is idempotent; no overwrite or silent reconstruction.

Proposed M3 schemas: `evaluation_request.v1`, `fold_plan.v1`, `opportunity_universe.v1`, `opportunity_decision.v1`, `fold_evidence.v1`, `evaluation_package_manifest.v1`, `failure_record.v1`, and `recovery_record.v1`. Outputs go only to new `reports/evolution_engine_milestone3_<UTC-date>/<search-family>/<attempt>/<evaluation>/` paths.

## 5. Evaluation authorization gates

**Implementation gate:** opened by the protocol owner’s explicit approval on 2026-11-20. Synthetic fixtures and code-path tests are allowed before infrastructure verification, but no authoritative evaluation may launch.

**Evaluation gate:** separately requires a signed independent Linux VM verification record, all scientific acceptance checks, and explicit user authorization for authoritative historical evaluation. Holdout remains inaccessible regardless.

## 6. Explicit exclusions

No candidate generation/catalog changes, strategy or parameter changes, optimization, adaptive behavior, arbitrary code, candidate ranking/qualification/promotion, holdout access, Phase 3–5 artifact changes, paper/live trading, or broker integration.
