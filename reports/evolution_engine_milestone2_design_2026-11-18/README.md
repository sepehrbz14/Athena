# Evolution Engine Milestone 2 — Approved Design and Implementation Contracts

This package contains the approved Candidate Representation and Bounded Generator design, the ten M2 JSON schemas, the design-to-schema consistency audit, and the acceptance checklist. Runtime implementation is in `crypto_bot/evolution_engine_m2.py` and `crypto_bot/evolution_registry_security.py`.

The implementation remains offline and research-only. It provides a finite declarative catalog, deterministic candidate generation, M1 registry intake, external public identity, immutable signed checkpoints, key rotation, and explicit human-authorized recovery. It does not evaluate candidates, modify strategies, run historical optimization, use the prospective holdout, or integrate with paper/live trading.

Frozen M0 candidate/event schemas and all M0/M1 numerical contracts and golden vectors remain unchanged. The approved architecture and requirements are in [architecture.md](architecture.md), [design_schema_audit.md](design_schema_audit.md), and [acceptance_checklist.md](acceptance_checklist.md). No D0-1 through D0-11 value changed.
