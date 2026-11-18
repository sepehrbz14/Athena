# Athena Evolution Engine — Milestone 0 Protocol and Schemas

**Status:** Final frozen Milestone 0 package submitted for review; no Evolution Engine implementation authorized.
**Repository baseline:** `deef43c6c33e5c00711095a2fee63aa49c9f09c9` (`codex/phase5-strategy-diagnostics`).
**Scope:** Research protocol, candidate and experiment contracts, registry event contract, completed proposed D0 decision record, and an implementation acceptance checklist.

## Review package

- [Milestone 0 specification](milestone0_specification.md) — repository facts, evidence lanes, candidate grammar, folds, statistical rules, safety, lifecycle, and scope boundaries.
- [Frozen protocol](frozen_protocol.md) — all D0-1 through D0-11 values, rationales, formulas, and testable acceptance conditions in one frozen specification.
- [Candidate specification JSON Schema](candidate_spec.schema.json) — immutable, declarative candidate contract.
- [Candidate-attempt manifest JSON Schema](candidate_attempt_manifest.schema.json) — sealed inventory including invalid and never-evaluated attempts.
- [Experiment protocol JSON Schema](experiment_protocol.schema.json) — pre-registration, candidate limits, chronology, costs, seeds, and statistical plan.
- [Append-only registry event JSON Schema](registry_event.schema.json) — proposed event envelope and lifecycle vocabulary; this is a schema proposal, not a registry implementation.
- [D0 decision record](unresolved_decisions.md) — eleven explicit, testable decisions with proposed numerical values, rationale, and acceptance tests; each requires user approval or amendment.
- [Acceptance checklist](acceptance_checklist.md) — document and eventual implementation gates.
- [Milestone 0 acceptance report](milestone0_acceptance_report.md) — repository baseline, schema validation, completed document checks, and the pending final review gate.

This final review revision makes candidate/control fold and execution parity mandatory, separates invalidity/insufficient evidence/technical failure/rejection, seals all attempts before validation, enforces one-shot post-lock holdout access, and encodes `NO_QUALIFYING_CANDIDATE` as a successful terminal outcome. The D0 values are frozen for final package review. These files are review artifacts only. No source code, existing strategy, portfolio configuration, frozen dataset, or historical report was changed. JSON Schema validation was run; no historical evaluation or application test suite was run.
