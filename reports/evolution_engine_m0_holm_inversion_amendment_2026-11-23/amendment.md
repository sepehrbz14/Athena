# Proposed M0 Amendment — Holm Inversion v1

**Status:** proposed for protocol-owner approval; not yet authoritative  
**Amendment schema:** `athena.evolution.m0-holm-inversion-amendment.v1`  
**Base protocol:** `athena.evolution.m0-frozen.v1`  
**Machine-readable record:** [amendment.json](amendment.json)  
**Strict schema:** [athena_m0_holm_inversion_amendment_v1.schema.json](../../../schemas/athena_m0_holm_inversion_amendment_v1.schema.json)

This is an additive, separately versioned amendment. The frozen M0 v1 document and schema remain unchanged. The amendment fills only the missing arbitrary-threshold p-value and Holm-compatible simultaneous-bound procedure. Estimands, sample floors, attempt limits, bootstrap settings, seed derivation, costs, alpha, and family accounting remain exactly as M0 v1.

## 1. Marginal test at an arbitrary effect threshold

For family hypothesis (i), let \hat\theta_i be its unchanged M0 weekly estimand and let \hat\theta^*_{ib}, (b=1,\dots,B), be the already specified fold-stratified, four-week moving-block bootstrap draws. Preserve the frozen centered errors

\[
e_{ib}=\hat\theta^*_{ib}-\hat\theta_i.
\]

For a finite candidate threshold (\delta\), test the one-sided null (H_i(\delta):\theta_i\le\delta) using

\[
p_i(\delta)=\frac{1+\sum_{b=1}^{B}\mathbf 1\{e_{ib}\ge\hat\theta_i-\delta\}}{B+1}.
\]

This is the same centered-bootstrap upper-tail test already frozen in M0 when (\delta=0); it changes only the threshold from zero. The `>=` comparison, add-one correction, draw order, M1-derived seeds, and (B=100{,}000) remain fixed. (p_i(\delta)) is a nondecreasing right-continuous step function. Its marginal lower endpoint at local level (\gamma\) is

\[
q_i(\gamma)=\sup\{\delta\in\mathbb R:p_i(\delta)\le\gamma\}.
\]

If the set is empty, the endpoint is null/undefined; it is never replaced by zero. An unresolved, invalid, failed, skipped, insufficient, or missing statistic has the frozen placeholder (p_i(\delta)=1) for all finite (\delta), stays in the family, and has no finite marginal bound.

## 2. Holm step-down at a threshold vector

Let (m=4N\le144) include every registered attempt, including all unresolved and unsuccessful attempts. For any threshold vector (\boldsymbol\delta=(\delta_1,\ldots,\delta_m)), evaluate all raw (p_i(\delta_i)) first, then apply ordinary Holm step-down to that complete vector. Sort ascending by raw p-value; break exact ties by persistent global attempt ordinal and then frozen M0 hypothesis order. At one-based rank (r), compare

\[
p_{(r)}(\delta_{(r)})\le\frac{\alpha}{m-r+1},\qquad \alpha=0.05.
\]

Reject through the last consecutive success and stop at the first failure. Equivalently report

\[
\tilde p_{(r)}(\boldsymbol\delta)=\min\left(1,\max_{s\le r}\{(m-s+1)p_{(s)}(\delta_{(s)})\}\right).
\]

The full family size and ordinal order do not change at different thresholds or across protocol revisions. Holm is applied to threshold-specific raw p-values, not to a selected subset of candidates or only to hypotheses with available trades/evaluations.

## 3. Simultaneous lower bounds compatible with Holm

Use the equal-weight Holm-compatible closed-testing confidence construction. First compute the Holm rejection set at the frozen edge boundary:

\[
R=\operatorname{HolmReject}\bigl(p_1(0),\ldots,p_m(0);\alpha\bigr),\qquad A=\{1,\ldots,m\}\setminus R.
\]

Then produce simultaneous lower bounds:

\[
L_i=
\begin{cases}
\max(0,q_i(\alpha/m)), & R=\{1,\ldots,m\},\\
0, & R\ne\{1,\ldots,m\}\text{ and }i\in R,\\
q_i(\alpha/|A|), & R\ne\{1,\ldots,m\}\text{ and }i\in A.
\end{cases}
\]

This is the unweighted Holm-compatible lower-bound construction based on closed testing, not a replacement of the Holm family test by a standalone Bonferroni qualification rule. Holm determines (R); local inversion levels depend on the observed rejection partition. The all-rejected case reduces to α/m marginal inversions; that is an intrinsic conservative branch of this confidence construction. The confidence guarantee is conditional on the marginal p-value functions being valid. M0's bootstrap remains an approximation; neither this amendment nor the software claims exact finite-sample coverage.

## 4. Qualification and an important consequence

The existing M0 executable criteria remain unchanged: both executable absolute-after-cost and paired executable lower bounds must be strictly positive, with all other frozen gates met. Predictive hypotheses remain separately reported and never substitute for executable evidence.

Because every invalid/failed/skipped/insufficient/missing attempt contributes four p=1 placeholders, any such placeholder is never rejected. Thus (R) is not the full persistent family, and this compatible construction assigns (L_i=0) to every rejected hypothesis, including a candidate's significant executable hypotheses. In that family, the frozen strict-positive-bound qualification criterion cannot be met. More generally, qualification requires every hypothesis in the persistent family to be rejected at zero before any rejected hypothesis can receive a strictly positive bound.

This conservative consequence is recorded rather than worked around: no attempt is removed, no alpha is reallocated outside the stated construction, and the qualification gate remains disabled pending owner approval. The owner may approve this exact consequence or request a new, versioned protocol decision. M4 must not substitute pointwise intervals or a custom coordinate-wise adjusted-p function.

## 5. Immutable identity and authority

The canonical JSON amendment identity is SHA-256 over the canonical amendment object with only `amendment_id` omitted. The amendment is explicitly `PROPOSED_FOR_PROTOCOL_OWNER_APPROVAL`; hashing it does not approve it. M4 verifies this identity and can calculate bounds for synthetic evidence, but emits only nonauthoritative proposed bounds. Qualification, `NO_QUALIFYING_CANDIDATE`, historical evaluation, and holdout access remain disabled until explicit owner approval is recorded and all separate infrastructure/authorization gates are met.

## 6. Numerical invariants

Unchanged from M0 v1: four hypotheses per attempt; at most 36 cumulative attempts and 144 hypotheses; alpha 0.05; 100,000 four-week moving-block bootstrap replicates; 95% raw percentile intervals; master seed 20261118; all sample floors and estimands; p=1 treatment; and all chronological, execution-cost, fold-concentration, ledger-separation, and holdout rules.

## References

- Holm, S. (1979), “A Simple Sequentially Rejective Multiple Test Procedure,” *Scandinavian Journal of Statistics* 6, 65–70. [Paper](https://www.ime.usp.br/~abe/lista/pdf4R8xPVzCnX.pdf).
- Strassburger, K. & Bretz, F. (2008), “Compatible simultaneous lower confidence bounds for the Holm procedure and other Bonferroni-based closed tests,” *Statistics in Medicine* 27, 4914–4927. [DOI/PubMed record](https://pubmed.ncbi.nlm.nih.gov/18618415/).
- Guilbaud, O. (2008), “Simultaneous Confidence Regions Corresponding to Holm's Step-Down Procedure and Other Closed-Testing Procedures,” *Biometrical Journal* 50, 678–692. [DOI/abstract](https://onlinelibrary.wiley.com/doi/10.1002/bimj.200710449).
