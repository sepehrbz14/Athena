# Milestone 0 Specification — Evolution Engine Protocol and Schemas

## 1. Purpose and boundary

Milestone 0 defines data contracts and research rules only. It does not implement an Experiment Generator, Research Evaluator, registry service, strategy-combination runtime, candidate selector, or promotion mechanism. In particular, these schemas are specifications for review; they are not wired into Athena execution.

The research objective is to test whether the existing strategy family has demonstrated predictive and executable potential under predeclared, realistic assumptions. The system must also be able to conclude **`NO_QUALIFYING_CANDIDATE`** or that the available evidence does not demonstrate an edge. It must never declare a winner merely because that candidate lost less than other candidates.

## 2. Repository facts verified at the approved baseline

The current checkout is exactly `deef43c6c33e5c00711095a2fee63aa49c9f09c9`; tracked source, schemas, tests, configs, and reports have no local modifications. The worktree also contains unrelated untracked local audit/reproduction artifacts; those are not part of this specification and must not be swept into future commits.

Repository facts relevant to the design:

- [`crypto_bot/strategies.py`](../../crypto_bot/strategies.py) defines the `Strategy` protocol and four fixed implementations: `trend_following`, `breakout`, `momentum`, and `mean_reversion`. Their signals include direction, confidence, rationale, stop, and optional target. `StrategyEnsemble` regime-gates, aggregates confidence by side, suppresses near-conflicting directions, and chooses one component signal. This implementation and its parameters are baseline code, not mutable candidate configuration.
- [`crypto_bot/regimes.py`](../../crypto_bot/regimes.py) defines `bull_trend`, `bear_trend`, `range`, `high_volatility`, `low_volatility`, `news_or_event_shock`, and `unknown`, with a fixed higher-timeframe causal classifier. The classifier has a configured priority (shock, high volatility, low volatility, trend, range); a candidate regime map must use the decision-time output and pin this classifier/config version.
- [`crypto_bot/research.py`](../../crypto_bot/research.py) captures raw component signals independently of portfolio state and stores forward outcomes as labels. [`crypto_bot/frozen_signals.py`](../../crypto_bot/frozen_signals.py) validates hashes, schema, timestamps, and selected-signal semantics. The frozen signal capture has 51,292 rows; its forward outcomes are never decision features.
- [`crypto_bot/execution.py`](../../crypto_bot/execution.py) is the canonical bar execution contract for fills, fees, slippage, funding, gaps, participation, and adverse stop/target resolution. It retains OHLC intrabar-order ambiguity and the legacy funding-charge sign convention.
- [`crypto_bot/portfolio.py`](../../crypto_bot/portfolio.py) and [`crypto_bot/portfolio_ledger.py`](../../crypto_bot/portfolio_ledger.py) implement account-constrained execution and an append-only Decimal journal. Phase 3 invariants include immutable completed-position accounting and transactional ledger operations.
- [`crypto_bot/validation.py`](../../crypto_bot/validation.py) has a generic `walk_forward()` callback helper, but it does not enforce nested selection, purge/embargo, fold-state reset, cost policy, multiple-testing accounting, registry lineage, or holdout release. It is not a complete Evolution Engine evaluator contract.
- [`crypto_bot/phase5_research.py`](../../crypto_bot/phase5_research.py) and the Phase 5 run artifacts keep predictive labels, independent counterfactual trades, and the executable portfolio separate. The Phase 5 hardening record says all 19 frozen input hashes match the authoritative run and the future holdout is pending.
- The approved Phase 4 config is [`configs/athena2_portfolio_baseline_v1.json`](../../configs/athena2_portfolio_baseline_v1.json), byte SHA-256 `bf5eeff4de2b58487f917cefecd9b8b7dc94ec7a72bf125729f34ab65c861627`. Its primary execution assumptions are 5 bps fees per side, 5 bps slippage, 1 bp per 8 hours funding, 5% maximum participation, one active position, fixed risk caps, and `force_close_at_end=true`. Its hash and contents are frozen.
- The Phase 4 replay has 91 completed positions, starting equity `$10,000`, final equity `$9,051.510973339474987446164828`, net loss `$948.489026660525012553835172`, and zero insolvency deficit. The preserved audit replay source is `c97b39f`; the approved corrected simulator source is `414cd27`; the approved final publication is `6b6d3b9`. These are distinct lineage references.
- Phase 5’s verified independent counterfactual lane has 51,292 overlapping hypothetical trades. All four strategies had negative net expectancy under its existing transaction-cost assumptions. The Phase 4 executable lane remains a small, account-constrained population and is not a standalone per-strategy portfolio.
- The existing report windows and all supplied market data through **2026-08-19** were previously examined. They are historical diagnostics only. The repository has no untouched holdout artifact.

The frozen signal CSV, candles, ledgers, historical reports, Phase 3/4 artifacts, and approved config remain read-only. Proposed schema hashes and source references are recorded in each experiment protocol; the historical artifacts themselves are never copied over or amended by this milestone.

## 3. Evidence lanes and allowed claims

Every metric row and report section must carry a lane identifier. Metrics from distinct lanes must not be summed, averaged, or presented as the same population.

| Lane | Observation and source | Permitted interpretation | Prohibited interpretation |
|---|---|---|---|
| `predictive_signal` | Causal candidate decision/signal and forward candle labels; signal-close reference conventions declared. | Directional accuracy, return-label expectancy, calibration, and label excursions, with horizon and overlap warnings. | Trade P&L, fill performance, portfolio returns, or proof of executable edge. |
| `independent_counterfactual` | Per-signal hypothetical trade under fixed reference sizing; overlapping trades may share neither capital nor liquidity. | Strategy/candidate trade path and after-cost counterfactual expectancy under declared costs. | Account equity curve, portfolio drawdown, aggregate investable P&L, or Phase 4 replay. |
| `executable_portfolio` | Candidate-selected signal stream through a fresh account replay using the frozen Phase 4 risk/execution configuration and isolated candidate ledgers. | Account-constrained outcome for the exact candidate, fixed configuration, data window, and execution assumptions. | A rewrite of the approved Phase 4 ledger or a claim about another sizing/configuration. |

The Phase 4 portfolio replay is a fixed reference benchmark. Its 91 positions, balances, hashes, and journal remain immutable. A candidate executable run starts from a fresh `$10,000` account in its own directory and its own ledger. It records the exact Phase 4 config hash and applies strategy selection upstream; the checked-in config is never edited. The candidate selector must emit at most the same canonical signal contract consumed by the Phase 4 simulator. Fixed account constraints, costs, fill rules, funding convention, safety controls, and end-of-run behavior remain unchanged. If an allocation experiment cannot be represented without altering those invariants, it is out of the initial candidate grammar and requires a separately reviewed protocol/configuration design.

The pinned Phase 4 run is not assumed to be a sufficiently sampled incumbent edge estimate. It is a read-only historical benchmark reference, not the incumbent sample used for fold-by-fold inference. For every candidate evaluation, the incumbent control is freshly replayed into its own new control ledger alongside the candidate’s separate ledger. Candidate and control must use identical input hashes, fold boundaries, warm-up, included time universe, cost assumptions, execution model, Phase 4 risk configuration, end-of-fold behavior, and paired calendar weeks. The only permitted difference is the candidate signal-selection policy. No candidate or control run may read, amend, append to, or reuse the frozen Phase 4 completed-position ledger as its evaluated ledger.

## 4. Candidate specification and version identity

The candidate schema is `candidate_spec.schema.json` (`athena.evolution.candidate.v1`). It is declarative, bounded, and intentionally has no free-form source, numeric strategy thresholds, fitted weights, or arbitrary callback field.

### Identity and lineage

- `candidate_id` is `sha256:<64 lowercase hex>`, computed from RFC 8785/JCS canonical JSON of all semantic fields except `candidate_id`, `created_at_utc`, and presentation-only `display_name`. `id_algorithm` pins the rule. No implementation may invent an ID from a noncanonical serializer.
- `schema_version` versions the document contract. `candidate_revision` starts at 1 for a root candidate; any semantic change creates a new candidate ID, increments the revision, and names the prior candidate as a parent. A prior record is never edited in place.
- Every candidate names its experiment/protocol ID, family, exact source commit, hashes for relevant strategy/regime/execution source, Phase 4 config hash, frozen input manifest hash, and ordered parent lineage. Root incumbent identities pin existing implementation and source hashes; they do not pretend the current code has an independent semver release.
- Parent references must resolve to an already registered immutable candidate or a pinned incumbent component. Cycles, missing parents, duplicate semantic IDs, and lineage mutation fail closed.

### Allowed grammar for the initial research design

The only candidate families are:

1. **`regime_specialization`** — a fixed map from existing regime labels to allowlisted existing strategy IDs. Unknown regime defaults to abstain. The exact existing classifier is pinned. No reclassification thresholds or features are fitted.
2. **`signal_combination`** — deterministic combination of raw existing component signals using a finite rule vocabulary: unchanged incumbent ensemble, unanimous direction, majority direction, or highest existing confidence. Ties abstain. A chosen signal keeps the originating component’s existing stop/target; no synthesized stop/target or threshold is permitted.
3. **`regime_selection_allocation`** — a fixed ordered strategy priority for each regime, with risk-budget mode exactly `frozen_phase4`. This can choose which existing signal receives the existing allocation opportunity; it cannot increase, redistribute, or adapt risk, leverage, quantity, concurrent positions, or account limits. The current baseline has one active position, so richer capital-weight allocation is deferred.

A noncandidate `incumbent_control` is evaluated alongside candidates. The incumbent control is not counted as a proposed hypothesis, but is retained in the trial family for paired comparisons. Generator output must be a finite manifest sealed before any validation or test outcomes are read. Any strategy parameter, entry/exit threshold, feature, stop/target, sizing rule, code change, adaptive state, or live/paper behavior falls outside this schema and requires separate approval.

## 5. Experiment protocol and immutable registry

`experiment_protocol.schema.json` (`athena.evolution.experiment_protocol.v1`) is the pre-registration contract. The separate [`candidate_attempt_manifest.schema.json`](candidate_attempt_manifest.schema.json) is sealed as its candidate-set inventory before parsing/validation. Every entry has a globally monotonic search-family ordinal, assigned family, exact submitted-byte hash, and unique attempt ID derived from protocol ID + ordinal + family + byte hash; malformed inputs therefore still receive stable, countable IDs. The manifest hash and attempt count are pinned by the protocol. It records:

- immutable protocol ID, creation/lock time, owning source commit, dirty state, schema/tool hashes, declared hypothesis and primary estimand;
- one or more allowed families, maximum attempt count per family and total, attempt-manifest hash, parent/control identities, and a statement that all submitted attempts are enumerated before validation or outcomes are exposed;
- dataset IDs, exact hashes, data end, market/instrument, signal and candle availability conventions, regime version, Phase 4 config hash, costs, and output-root policy;
- outer and inner chronological fold generation, minimum history, validation/test block sizes, maximum label horizon, position horizon policy, purge and embargo calculations, warm-up policy, and fold-state reset;
- preprocessing allowlist and fit scope; for this initial grammar, no estimated transform or learned regime boundary is permitted;
- primary/secondary metrics, minimum sample gates, bootstrap seed/method/replicates/block length, alpha, correction family, multiplicity method, and pass/reject criteria;
- holdout status, custody/release rule, and whether the data are previously examined.

`registry_event.schema.json` (`athena.evolution.registry_event.v1`) defines a tamper-evident append-only log envelope. Every event has monotonically increasing sequence, unique event ID, timestamp, protocol ID, event type, payload hash, previous-event hash, and event hash. The payload hash is SHA-256 over RFC 8785 canonical payload JSON. The event ID hashes canonical JSON over sequence, event time, protocol ID, type, actor, payload hash, and previous-event hash. The event hash covers canonical full event JSON excluding `event_sha256`. The first event has sequence 1 and a null previous hash; later events must point to the immediately preceding event hash. Event history includes protocol registration, candidate-attempt intake, candidate-set sealing, candidate registration or invalidity, evaluation start/completion/failure/skip, evidence rejection, insufficient evidence, no-qualifying-candidate conclusion, research-only approval, and prospective holdout seal/release. Corrections are new linked events. No delete, rewrite, status replacement, or rerun-in-place is permitted.

Every attempted candidate has an `attempt_id` derived from protocol ID, globally monotonic search-family ordinal, assigned family, and hash of submitted candidate bytes; it appears in the sealed attempt manifest **before candidate validation or any result access**. Linked manifests collectively include valid candidates, malformed/invalid candidates, candidates never reached because of a campaign abort, and candidates skipped after an earlier technical failure. Thus a bad spec cannot disappear before the multiplicity denominator is fixed. Each attempt has a terminal disposition: `INVALID_CANDIDATE`, `UNEVALUATED`, `TECHNICAL_EVALUATION_FAILURE`, `INSUFFICIENT_EVIDENCE`, or `REJECTED`; successful gate passage is `RESEARCH_ADVANCED_PENDING_PROSPECTIVE`, not automatic approval. All attempts remain in their family count. For Holm family-wise correction, an attempt with no valid p-value due invalidity, skip, technical failure, insufficient evidence, or missing statistic contributes p=1 and cannot qualify. A rerun is a new attempt and never replaces the original. The 12-per-family/36-total limits apply cumulatively across the persistent search family, not per protocol.

Candidate and campaign dispositions are distinct and must not be conflated:

- **`REJECTED`** means a valid candidate completed the registered evaluation but failed one or more pre-registered scientific/fitness gates. This is a substantive research result.
- **`INSUFFICIENT_EVIDENCE`** means a valid evaluation completed, but a minimum sample/occupied-week/regime-coverage/fold-count floor failed, or uncertainty could not be computed because cluster coverage was insufficient. No separate confidence-interval-width cutoff is defined. When minimum evidence is computable but an adjusted lower bound fails its registered edge gate, the candidate is `REJECTED` as unproven. Neither status is evidence of an edge.
- **`TECHNICAL_EVALUATION_FAILURE`** means evaluation did not complete because of a system, infrastructure, resource, input-integrity, or accounting failure. Partial metrics are not a result. The attempt remains in the multiplicity denominator; a rerun is a new linked attempt.
- **`INVALID_CANDIDATE`** means the submitted candidate cannot be parsed, schema-validated, or lineage-validated. It is counted from its submitted bytes but is not evaluated.
- **`UNEVALUATED`** means a sealed attempt was not started, for example because a prior campaign technical failure stopped execution. It remains recorded and counted; it has no metrics.

Campaign lifecycle includes `PROPOSED`, `PRE_REGISTERED`, `CANDIDATE_SET_SEALED`, `RUNNING`, `COMPLETED`, `TECHNICAL_FAILURE`, `NO_QUALIFYING_CANDIDATE`, `RESEARCH_ADVANCED_PENDING_PROSPECTIVE`, and `HOLDOUT_CONSUMED`. Attempt dispositions are derived from immutable events. No “promoted to production” or trading state exists.

## 6. Chronological validation contract

### Data and fold chronology

The reviewed source spans 2019-12-31 through 2026-08-19. Proposed research folds use UTC signal availability times and have strictly ordered train, validation, and outer-test regions. Because these dates have already been examined, all such folds are labeled `previously_observed_historical_diagnostic`; none is final OOS evidence.

Proposed nested expanding-window defaults (the explicit D0 recommendations are in [the decision record](unresolved_decisions.md)):

- minimum inner training history: 24 complete calendar months;
- inner validation block: 6 complete calendar months;
- outer test block: 6 complete calendar months;
- outer step: 6 complete calendar months, so outer test blocks do not overlap. Require at least six eligible outer test blocks for an executable edge claim;
- at each outer origin, inner rolling-origin folds use only data before that outer test. Candidate selection is completed using inner validation folds; the sealed selection is then scored once on the next outer block;
- no outer-block result may be used to edit candidates and then be reported as their unbiased result. All historical outer results remain development evidence because the period is known to have been examined.

An outer test starts only after its training/selection history is available. Inner validation folds must themselves be chronological and nonoverlapping. All intervals are half-open UTC intervals `[start, end)`. A fold manifest records actual bars/timestamps as well as calendar bounds; missing-candle gaps do not get silently filled.

### Purge, embargo, warm-up, and state

- Maximum frozen signal-label horizon is **96 15-minute bars (24 hours)**. For a training observation to be eligible before boundary `B`, its maximum label timestamp must be strictly earlier than `B`; otherwise purge it and record the reason.
- Completed-trade records are assigned by signal availability time. A trade whose modeled exit, funding accrual, or other scored outcome crosses the next boundary is purged from the earlier score. It is not reassigned by exit time.
- For every six-calendar-month scored validation or outer-test interval `[S,E)`, prohibit new entries during the final 96 expected 15-minute bars. Existing positions may survive that entry blackout, use unchanged Phase 4 exit rules, and are force-closed at `E` using its existing end-of-run policy, with forced-close costs reported. Define `H_position = N_interval`, where `N_interval` is the count of expected 15-minute intervals in `[S,E)`; a maximum 184-day interval gives a 17,664-bar cap. This follows from fold duration and does not add a strategy time stop.
- Embargo is 96 expected 15-minute bars (24 elapsed hours) after the last training/selection outcome before the next scored region. Calendar time is used so data gaps do not shorten it. Purge any label, position, funding accrual, or other scored outcome crossing a boundary. Record counts and reasons per fold.
- Earlier candles may seed deterministic feature and regime warm-up only: exactly 816 prior contiguous 15-minute bars, corresponding to 51 higher-timeframe 4-hour regime candles. Reset after a data gap. Warm-up rows emit no scored signal, trade, equity change, account-control observation, or metric.
- Preprocessing is either none or an explicit allowlist. Any estimated transform is fit using the training portion only, serialized with fit-window and hash, and applied unchanged to validation and outer test. Full-series scaling, imputation, threshold estimation, feature selection, or regime fitting is prohibited.
- Account state, pending orders, and safety state reset at each independent fold. No state carries between folds. For executable scoring, the exact frozen Phase 4 end-of-run policy applies within each isolated fold and any resulting forced-close costs are reported separately. Fold endpoints and the exact close policy must be frozen before candidate enumeration.

### Historical versus prospective claims

The period through 2026-08-19 is permanently classified as previously observed research data. It can support chronological historical diagnostics and nested research development but can never become an untouched holdout by renaming or repartitioning it. Genuine prospective evidence begins only after protocol and candidate hashes are locked and new, unavailable-at-lock-time observations accrue.

## 7. Pre-registration, multiplicity, and seeds

Proposed cumulative ceiling: **12 attempts per family; at most 3 families; 36 attempts per persistent search family**, plus the incumbent control. Each generated/submitted candidate is counted even if malformed, invalid, skipped, or unevaluated. No unregistered variants, dropped losing trials, or post-result candidate regeneration is allowed. Apply Holm step-down family-wise error control at alpha 0.05 across the persistent search family ID, all linked protocols and revisions, all families and attempts, and four confirmatory hypotheses per attempt (at most 144 tests total). Invalid, failed, skipped, unevaluated, insufficient-evidence, or noncomputable attempts receive p=1 and remain in the denominator.

Use a paired, non-circular moving-block bootstrap over UTC calendar weeks (four-week blocks, 100,000 replicates, two-sided 95% percentile intervals and one-sided lower bounds for gates). For each estimate, center the bootstrap errors under the zero-edge null and calculate the one-sided tail p-value; Holm-adjust across the persistent family. Use the same sampled week blocks for the paired control and all cost scenarios. Report raw/adjusted p-values and intervals, Monte Carlo tail-count uncertainty, attempt count, and every invalid, failed, skipped, rejected, insufficient, unevaluated, or missing run. The denominator is sealed from the pre-evaluation attempt manifests; no post-hoc exclusions or protocol reset. Secondary metrics and subgroup scans are exploratory and cannot independently qualify a candidate.

Proposed reproducibility seed: master seed `20261118` (consistent with the Phase 5 analysis convention). Derive each resampling seed from SHA-256 of UTF-8 `protocol_id|attempt_id|fold_id|metric_id|20261118`, interpreting the first eight digest bytes as an unsigned big-endian integer. Candidate enumeration is deterministic and the complete attempt-manifest hash is sealed before validation or evaluation. Randomness is not permitted in strategy decisions.

If any registered result is inspected and the protocol, family, candidate set, primary metric, or fold rules change, close that protocol. The revision gets a new protocol ID and links the entire prior trial family; correction denominators cannot be reset to erase earlier attempts.

## 8. Fitness, uncertainty, sample warnings, and rejection

### Metrics by lane

- **Predictive:** for fixed 1/4/16/96-candle labels, directional accuracy, mean direction-adjusted return, and predeclared calibration metrics where confidence is meaningful. Signals with overlapping forward horizons are clustered by calendar week. Label metrics never enter executable P&L.
- **Independent counterfactual:** raw-reference gross, execution gross, net P&L after per-side fees, adverse slippage and signed funding; expectancy, win rate, profit factor, winner/loser means, duration, MFE/MAE, regime/time slices, and exclusions. This lane is never an account curve.
- **Executable portfolio:** final equity/return, net expectancy, drawdown, completed positions, fees, slippage embedded in fills, signed funding, open/forced-close counts, rejected signals, exposure, and exact Decimal reconciliation under the fixed config. Compare against the incumbent control on the same folds and weeks.

The four confirmatory estimands are: (1) absolute predictive mean weekly score, where each selected direction multiplies its frozen 96-bar forward return and abstention scores zero; (2) paired predictive mean weekly candidate-minus-incumbent score on identical signal opportunities; (3) absolute executable mean weekly portfolio return from a fresh candidate ledger, including zero-trade weeks, with robust absolute value defined as the minimum mean across baseline and three one-factor stress cases; and (4) paired executable mean weekly candidate-minus-incumbent return under baseline costs. Incumbent control is freshly replayed for each comparison. Candidate and control use identical data, fold, warm-up, cost, config, execution, account, and week definitions, but separate ledgers. Candidate advancement requires adjusted lower bounds above zero for both robust absolute executable return and paired executable improvement. Predictive results remain a distinct evidence lane; counterfactual results are descriptive only.

### Uncertainty and proposed minimum evidence gates

Use paired, non-circular moving-block bootstrap over complete UTC calendar-week clusters; block length four consecutive weeks, 100,000 replicates, seed derivation above, two-sided 95% percentile intervals, and one-sided Holm-adjusted lower bounds for gates. Resample candidate and control together for paired differences and use identical week-block draws across candidate and cost-sensitivity rows. Keep overlapping labels/trades within their original weekly cluster. Publish occupied weeks, empty weeks, replicate count, bootstrap block count, trade count, and effective coverage. A confidence interval is not a guarantee of future returns.

Proposed hard minima before any positive edge claim:

| Evidence | Minimum coverage |
|---|---:|
| Predictive label evidence | 200 eligible opportunities and 30 occupied weeks for the registered primary horizon |
| Independent counterfactual trade evidence | 100 completed trades and 30 occupied entry weeks |
| Executable portfolio evidence | 100 completed candidate positions, 30 occupied trade weeks, and at least 6 eligible outer folds |
| A regime-specific edge claim | 30 observations and 12 occupied weeks in that regime, in addition to the overall lane gate |

These are caution gates, not proof of independence or statistical power. Around a 50% Bernoulli rate, independent-sample Wilson half-widths are approximately 6.9 percentage points at n=200 and 9.8 points at n=100; serial dependence makes those illustrative calculations optimistic. Any unmet threshold yields `INSUFFICIENT_EVIDENCE`; do not substitute a less demanding lane. Under the current historical Phase 4 sample (91 positions and sparse occupied weeks), the executable sample gate is not met. Existing fixed counterfactuals have large raw counts but are overlapping and cannot establish portfolio edge by trade count alone.

### Qualification gates and explicit rejection

A candidate is eligible for `RESEARCH_ADVANCED_PENDING_PROSPECTIVE` only if all predeclared gates pass:

1. no lineage, causality, hash, schema, or reconciliation failure;
2. minimum sample gates pass in the lane used for the claim;
3. primary candidate absolute after-cost executable result has an adjusted 95% lower confidence bound above zero **and** paired improvement versus incumbent has adjusted lower bound above zero. Candidate and incumbent control use exactly the same folds, inputs, costs, config, execution assumptions, and calendar-week comparison basis;
4. no registered outer fold has a catastrophic/safety-limit breach; fold stability and regime coverage meet predeclared policy;
5. results survive the fixed adverse-cost sensitivity and no single fold/week dominates the claimed edge;
6. the all-trial multiple-testing adjustment passes;
7. prospective holdout, once available, independently meets the same absolute and comparative gates before any edge claim can be marked confirmed.

**Outperforming losing candidates is insufficient:** a candidate with better return than the incumbent but nonpositive absolute net expectancy or an interval crossing zero fails the absolute gate. The incumbent’s loss does not lower the candidate’s profitability threshold. No single metric, best regime, zero-cost row, or post-hoc subgroup can override a failed primary gate.

Disposition reasons are partitioned with the lifecycle outcome: `INVALID_SPEC`/`INVALID_LINEAGE` => `INVALID_CANDIDATE`; `LEAKAGE`, input/source hash mismatch, `RECONCILIATION_FAILURE`, evaluator `RESOURCE_LIMIT`, or `SAFETY_INVARIANT_FAILURE` => `TECHNICAL_EVALUATION_FAILURE`; minimum sample or regime coverage shortfall => `INSUFFICIENT_EVIDENCE`; and a fully evaluated candidate failing scientific gates such as `NEGATIVE_AFTER_COST_EDGE`, `ABSOLUTE_EDGE_UNPROVEN`, `NO_INCREMENTAL_EDGE`, `MULTIPLE_TESTING_ADJUSTED_FAILURE`, `REGIME_INSTABILITY`, `COST_FRAGILITY`, `FOLD_CONCENTRATION`, or candidate-caused `SAFETY_LIMIT_BREACH` => `REJECTED`. Preserve each reason and its evidence in the immutable log. These categories cannot be relabeled to improve a candidate’s result.

The engine can legitimately conclude: **“No existing strategy or registered candidate has demonstrated a robust predictive and executable edge on the available, previously observed data under this protocol.”** This is an evidence-bound conclusion, not proof that no strategy can ever have an edge. Given Phase 5’s negative after-cost counterfactual expectancies and sparse executable evidence, `NO_QUALIFYING_CANDIDATE` is a valid and likely outcome. A later prospective holdout could change the evidence but cannot rewrite this conclusion’s historical scope.

## 9. Future holdout governance

- No current data through 2026-08-19 are eligible as untouched holdout. Any later-added data must have immutable source hashes and evidence that it was unavailable to candidate authors at protocol lock.
- Before access, freeze source commit, protocol hash, full candidate-attempt manifest hash (including every attempt), fold schema, preprocessing, regime mapping, costs, primary metrics, multiplicity method, seeds, and pass/reject criteria. Store holdout data/outcomes under a separate custody boundary. Generator and candidate-selection processes have no holdout read permission at any time. The ordinary evaluator also has no access; only a one-shot, sealed confirmatory evaluator may receive read-only access after lock verification by the custodian. It can score only the already-frozen candidate set and cannot return data or intermediate outcomes to generation/selection. Development tools must never mount the holdout.
- Proposed holdout: lock exact UTC start/end before accrual, for at least 52 complete future UTC weeks plus the 96-bar label tail; require at least 200 predictive opportunities/30 occupied weeks for a predictive claim, at least 100 executable positions/30 occupied weeks for an executable claim, and 30 observations/12 occupied weeks for a regime claim. The six-fold gate applies to historical walk-forward analysis, not this single fixed holdout block. Do not extend based on sample count or interim outcomes; a miss is `INSUFFICIENT_EVIDENCE`. No shorter observed period is relabeled. A named independent custodian must be appointed before holdout staging.
- Permit one confirmatory opening for the locked candidate family. Any inspection consumes the holdout. If changes follow, that period joins the research history and a new future holdout is required.
- Release includes the committed protocol, data hashes, access log, evaluator hashes, every candidate result, and adjusted comparisons. No automatic promotion follows release.

## 10. Fail-closed and immutable artifact rules

- Reject unknown schema versions, strategy names, regime names, metrics, policies, enum values, invalid hashes, non-UTC or duplicate boundaries, invalid parent links, nonfinite numbers, unsealed attempt manifests, and an attempt count above protocol limits.
- Refuse any write path under Phase 3 fixtures, Phase 4 audit/replay directories, Phase 5 historical reports/runs, frozen input directories, or the approved config/schema paths. New experiment outputs use a new `reports/evolution_research/<UTC-date>/<protocol-id>/` directory created exclusively; no overwrite/resume-in-place.
- Hash every input before/after evaluation. Any change aborts the run and marks it failed; never repair or normalize source bytes in place.
- Record exact source commit and require clean tracked worktree for a confirmatory run. If dirty local prototyping is later allowed for development, mark nonconfirmatory and include diffs and file hashes; never qualify on that run.
- No arbitrary code execution, strategy imports from candidate payloads, dynamic package installs, network data downloads, or side effects. Enforce CPU/time/memory/candidate/trade/output-size caps in an isolated offline process; exceeding a cap produces `TECHNICAL_EVALUATION_FAILURE` for the affected attempt and campaign `TECHNICAL_FAILURE` if the run is aborted. No partial metrics are published as complete; unreached attempts become `UNEVALUATED` and remain in the sealed denominator.
- Candidate records, run manifests, stdout/stderr, metrics, fold artifacts, failures, and reviewer decisions are write-once. Hash-chain registry events; periodic registry snapshots may be signed/exported, but never replace the event log.
- Approval statuses are research-only: `RESEARCH_ADVANCED_PENDING_PROSPECTIVE` and `APPROVED_FOR_FURTHER_RESEARCH`. `APPROVED_FOR_PAPER`, `APPROVED_FOR_LIVE`, and automatic promotion are invalid schema states.

## 11. Test plan and later implementation order

Milestone 0 acceptance is document/schema review only. No code or evaluation run belongs to this milestone. After approval, implementation should be separately authorized and split into testable milestones: schema/contract validation; append-only registry; deterministic bounded candidate-set generation; pure candidate decision adapters; lane-separated fold evaluation; uncertainty/multiple-testing; isolated executable replay; and full cross-platform reproducibility. The detailed future test matrix is in [acceptance_checklist.md](acceptance_checklist.md).

No strategy optimization, strategy modification, Phase 6 work, paper/live trading, or automatic promotion is included here.
