# Business Logic Correctness Audit

> **Internal validation artifact - not approved for public release.** Scope:
> SQL UC scoring primitives (`fn_lead_score`, `fn_rate_spread`,
> `fn_in_the_money`, `fn_next_best_offer`), the Python parity layer
> (`backend/services/scoring.py`), sub-score derivation in
> `gold_lead_scores.sql` and `gold_borrower_360.sql`, golden-fixture coverage,
> fair-lending posture, deploy wiring for UC function DDL, and the Python
> service-layer call sites that build the runtime dossier and offer proof.

**Date:** 2026-05-18
**App:** `https://mip-app-2543889327043640.aws.databricksapps.com`
**Active deployment after remediation:** `01f1530971261d09af6eefef69992d05`

## Headline Result

The original audit findings were valid, and one additional release-blocking
issue surfaced during live validation: `mip_refresh_scores` refreshed gold
tables but did not apply the `sql/uc_functions/*.sql` files first. Local Python
and source-level SQL tests passed, but the deployed `fn_next_best_offer` body
still had old null-threshold behavior. That is now fixed and guarded.

Current state: **0 open P0, P1, HIGH, MEDIUM, or LOW findings from this audit.**

Business logic now validates at three layers:

- Python scoring fixtures: all primitive cases pass locally.
- Deployed UC UDF parity: live warehouse parity passes for all golden cases,
  including the new null-threshold fail-closed case.
- Gold-table materialization: live `borrower_360` and `lead_scores` carry
  refresh-applied thresholds with no nulls; `evidence_events` emits
  compliance-visible `loan_type_fit` rows and no blocked `permit`/`listing`
  rows.

## Remediations

### 1. Threshold Tunability Is Now Realized In Gold

Closed original MEDIUM 1.

`gold_borrower_360.sql` now reads all five governed offer thresholds from
`mip.ref.offer_rules_config` once per refresh, with data-contract defaults only
as missing-row fallbacks:

- `mip_min_spread_bps`
- `mip_min_equity_pct`
- `mip_heloc_equity_min_pct`
- `mip_cashout_equity_min_pct`
- `mip_retention_min_spread_bps`

`gold_borrower_360` materializes:

- `min_spread_bps_applied`
- `min_equity_pct_applied`
- `heloc_equity_min_applied`
- `cashout_equity_min_applied`
- `retention_min_spread_applied`

`gold_lead_scores.sql` consumes those applied columns instead of SQL literals,
so `recommended_offer_code`, `in_the_money`, and the exposed threshold proof
all reflect the same refresh-run rule values.

The Offer Orchestrator now reads and displays the applied thresholds from
`gold.borrower_360` via `DatabricksOfferRepository`, not process-level settings.

### 2. Dead ITM Drift Call Replaced With A Real Guard

Closed original MEDIUM 2.

`databricks_borrowers.py` no longer discards the result of
`in_the_money(...)`. It recomputes the Python primitive against the row's
applied thresholds and emits a structured `borrower_itm_parity_drift` warning
if gold's materialized flag diverges. Borrower reads remain available in
production; the guard surfaces drift without taking down the dossier route.

### 3. `fn_next_best_offer` Fails Closed On Missing Thresholds

Closed original LOW 1.

Both Python and SQL now return `nurture` when any `fn_next_best_offer` threshold
argument is null. Borrower signal nulls still coerce to `0` / `FALSE`, but a
missing threshold row is treated as configuration failure, not as permission for
`0 >= 0` to qualify a positive offer.

Coverage added:

- `tests/fixtures/next_best_offer_golden.json`
  - `case_16_null_thresholds_fail_closed_to_nurture`
- `tests/unit/test_next_best_offer.py`
  - explicit null-threshold unit test
- `sql/fixtures/next_best_offer_validation.sql`
  - SQL-side validation row for the same case
- `tests/integration/test_sql_python_parity.py`
  - now covers the case against deployed UC

### 4. Loan-Type Fit Is Explainable And Guarded

Closed original LOW 2.

The CONV/FHA/VA fit branch remains symmetric:

```sql
WHEN is_owner_occupied AND first_pos_loan_type IN ('CONV','FHA','VA') THEN 70
```

`gold_evidence_events.sql` now emits a `loan_type_fit` evidence row when that
branch applies, making the scoring rationale visible to compliance reviewers.
That row is excluded from the evidence sub-score so explainability does not
retune scoring.

### 5. Retention Boost Is Documented For Fair-Lending Review

Closed original LOW 3.

`docs/data-contract-module0.md` now has an explicit fair-lending posture section:

- no protected-class, FICO, or credit-bureau inputs;
- CONV/FHA/VA parity is a contract;
- `loan_type_fit` evidence is explainability-only;
- the `is_current_customer THEN 70` relationship boost must be reviewed by the
  deploying lender's compliance team before production use.

### 6. UC Function Deploy Wiring Was Added

New finding discovered during validation; now closed.

Live SQL parity initially failed after a successful deploy because
`mip_refresh_scores` did not apply `sql/uc_functions/fn_next_best_offer.sql`.
The gold CTAS chain used the old deployed function body. The job now runs these
tasks before gold DDL and CTAS:

- `init_fn_rate_spread`
- `init_fn_in_the_money`
- `init_fn_lead_score`
- `init_fn_next_best_offer`

`init_gold_ddl` depends on all four. This is defined in both `databricks.yml`
and `resources/jobs.yml`, and is pinned by
`tests/unit/test_gold_ddl_contract.py::test_uc_functions_are_wired_before_gold_ctas`.

The integration test fixture was also tightened so a failed live parity test
does not print bearer tokens in pytest parameter reprs.

## Live Evidence

Full deploy completed on 2026-05-18:

- Active deployment: `01f1530971261d09af6eefef69992d05`
- App state: `RUNNING`
- Compute state: `ACTIVE`
- App URL: `https://mip-app-2543889327043640.aws.databricksapps.com`

Second deploy output confirmed the corrected gold job executed function DDL:

- `Task init_fn_lead_score`: success
- `Task init_fn_rate_spread`: success
- `Task init_fn_in_the_money`: success
- `Task init_fn_next_best_offer`: success
- `Task ctas_borrower_360`: success
- `Task ctas_lead_scores`: success
- `Task refresh_semantics_views`: success

Live smoke passed after the corrected deploy:

- `GET /api/v1/health`
- portfolio preview
- ranked leads
- borrower dossier
- evidence timeline
- data estate proof
- source readiness
- geo state/county/ZIP rollups
- outreach draft
- outreach approval audit write
- Genie message

Live warehouse probes:

| Probe | Result |
|---|---|
| `fn_next_best_offer(NULL thresholds)` | `nurture` |
| `mip.gold.borrower_360` rows | `5,156,184` |
| `borrower_360` null applied-threshold rows | `0` |
| `borrower_360` min/max spread threshold | `75 / 75` |
| `borrower_360` min/max HELOC threshold | `35 / 35` |
| `mip.gold.lead_scores` rows | `5,156,184` |
| `lead_scores` null applied-threshold rows | `0` |
| `mip.gold.evidence_events` rows | `18,583,467` |
| `loan_type_fit` evidence rows | `335,194` |
| blocked `permit` / `listing` evidence rows | `0` |

Live SQL-Python parity:

- `tests/integration/test_sql_python_parity.py`: pass against deployed UC UDFs.
- Includes all four primitives and the new null-threshold `fn_next_best_offer`
  case.

## Regression Gates Added Or Updated

| Gate | Contract |
|---|---|
| `tests/unit/test_next_best_offer.py` | Python null-threshold fail-closed behavior. |
| `tests/fixtures/next_best_offer_golden.json` | Golden fixture case 16. |
| `sql/fixtures/next_best_offer_validation.sql` | SQL validation case 16. |
| `tests/integration/test_sql_python_parity.py` | Live UC parity includes case 16 and masks token reprs. |
| `tests/unit/test_gold_ddl_contract.py::test_offer_thresholds_are_ref_table_sourced_and_materialized` | Gold thresholds come from `mip.ref.offer_rules_config`, are materialized, and no literal 75/15/35/25/50 call-site drift returns. |
| `tests/unit/test_gold_ddl_contract.py::test_uc_functions_are_wired_before_gold_ctas` | UDF files are applied before gold CTAS in deploy jobs. |
| `tests/unit/test_gold_ddl_contract.py::test_fit_loan_type_parity_and_explainability_contract` | CONV/FHA/VA branch symmetry and `loan_type_fit` evidence contract. |
| `tests/unit/test_gold_ddl_contract.py::test_borrower_360_and_lead_scores_subscore_terms_stay_aligned` | Key mirrored sub-score terms stay aligned across `borrower_360` and `lead_scores`. |
| `tests/unit/test_offers_router.py::test_offers_router_uses_refresh_applied_thresholds_from_offer_inputs` | Offer proof displays refresh-applied thresholds from gold, not process settings. |

## Validation Run

Local validation:

- `.venv/bin/python -m ruff check backend tests tools`: pass
- `.venv/bin/python -m pytest -q tests/unit/test_next_best_offer.py tests/unit/test_in_the_money.py tests/unit/test_scoring.py tests/unit/test_admin_rules.py tests/unit/test_gold_ddl_contract.py tests/unit/test_offers_router.py tests/unit/test_public_api_schema_guards.py`: pass
- `.venv/bin/python -m pytest -q`: pass, with expected credential-gated skips
- `.venv/bin/python tools/render_sql.py --catalog mip --dest /tmp/mip_rendered_sql_check2`: pass
- `npm --prefix frontend run lint`: pass
- `npm --prefix frontend test`: 34 files / 200 tests pass
- `npm --prefix frontend run build`: pass
- `npm --prefix frontend run budget`: pass
- `git diff --check`: pass

Deployment validation:

- `./scripts/deploy.sh -t dev --no-confirm`: pass after adding UDF deploy tasks
- `scripts/smoke_live.sh`: pass as part of deploy
- `databricks apps get mip-app`: active deployment `01f1530971261d09af6eefef69992d05`,
  `RUNNING` / `ACTIVE`
- live `tests/integration/test_sql_python_parity.py`: pass against deployed UC
- live warehouse probes listed above: pass

## Residual Risk

No open correctness findings remain from this audit. Remaining risk is ordinary
change risk: future edits to scoring formulas, UDF deploy wiring, or fair-lending
branches must update the new contract tests and golden fixtures deliberately.

---

## v2 independent verification — 2026-05-18

Re-audited the remediation against the working tree to confirm every
claim is correctly landed and no prior-audit invariant regressed.

### What I verified directly

| Claim | Verification method | Result |
|---|---|---|
| `fn_next_best_offer` fails closed to `'nurture'` on any NULL threshold (Python) | Read `backend/services/scoring.py:217-224` | PASS — explicit guard returns `'nurture'` before the decision tree runs, with comment "A missing threshold is a configuration failure, not permission to treat 0 >= 0 as a positive eligibility signal" |
| `fn_next_best_offer` fails closed to `'nurture'` on any NULL threshold (SQL) | Read `sql/uc_functions/fn_next_best_offer.sql:152-159` | PASS — top of CASE checks all five thresholds for NULL and returns `'nurture'`; previous `COALESCE(min_*, 0)` permissive fallback is replaced with explicit NULL checks |
| Golden `case_16_null_thresholds_fail_closed_to_nurture` added | Read `tests/fixtures/next_best_offer_golden.json` + executed Python parity | PASS — 16 NBO cases now, all 16 match Python (was 15/15, now 16/16) |
| All four golden suites still pass | Re-ran Python parity for lead_score (12), rate_spread (11), in_the_money (11), next_best_offer (16) | PASS — **50/50** cases match Python (up from 49/49) |
| Gold thresholds come from `mip.ref.offer_rules_config` | Read `sql/transformations/gold_borrower_360.sql:86-94` — `rules` CTE reads 5 keys from `mip.ref.offer_rules_config` with data-contract defaults as `COALESCE` fallback | PASS — all five thresholds (min_spread_bps / min_equity_pct / heloc_equity_min_pct / cashout_equity_min_pct / retention_min_spread_bps) sourced from the ref table |
| `gold.borrower_360` materializes `*_applied` threshold columns | Read `sql/transformations/gold_borrower_360.sql:399-426` (CROSS JOIN rules; 5 applied columns projected; both UDF calls use `r.*` not literals) | PASS |
| `gold.lead_scores` consumes the applied columns from `borrower_360` | Read `sql/transformations/gold_lead_scores.sql:103-108` (selects `b.*_applied` columns from `mip.gold.borrower_360`) and lines 198-213 (UDF call uses `s.*_applied`, no `35, 25, 50` literals) | PASS |
| Offer Orchestrator reads applied thresholds from gold | Read `backend/api/offers.py:280-286` — `thresholds_applied` dict pulls from `inputs["min_spread_bps"]`, `inputs["heloc_equity_min_pct"]`, etc. (returned by `DatabricksOfferRepository.get_offer_inputs`), not from process-level `settings.*` | PASS |
| Vestigial `_ = in_the_money(...)` replaced with real drift guard | Read `backend/services/repositories/databricks_borrowers.py:223-246` — captures `expected_itm`, compares to `borrower.why_panel.in_the_money`, emits structured `borrower_itm_parity_drift` WARNING with all relevant inputs (rate_spread_bps, equity_pct, both applied thresholds, both ITM values); does NOT raise so a borrower read stays serviceable | PASS |
| `loan_type_fit` evidence row emitted | Read `sql/transformations/gold_evidence_events.sql:158-177` — emits one row per owner-occupied CONV/FHA/VA borrower with `signal_type = 'loan_type_fit'`, `signal_rank = 4`, deterministic `display_text` | PASS |
| `loan_type_fit` excluded from evidence sub-score | `grep "loan_type_fit" sql/transformations/gold_*.sql` — line 40 of gold_lead_scores.sql and line 456 of gold_borrower_360.sql both have `WHERE signal_type NOT IN ('permit', 'listing', 'loan_type_fit')` for the evidence counter; line 482 (top-3 trigger timeline) intentionally includes it so dossier renders the explanation | PASS — explainability doesn't retune scoring |
| Fair-lending posture documented | Read `docs/data-contract-module0.md:585-610` — explicit "5.1 Fair-Lending Posture For Scoring Inputs" section locks no-protected-class + no-FICO inputs; CONV/FHA/VA parity as a contract; current-customer retention boost as compliance-review-required | PASS |
| Bundle wires UC function DDL before CTAS | Read `databricks.yml:390-432` — `mip_refresh_scores` job has 4 `init_fn_*` tasks; `init_gold_ddl` has explicit `depends_on` listing all four | PASS |
| Mirror wiring in `resources/jobs.yml` | `test_uc_functions_are_wired_before_gold_ctas` asserts both files | PASS |
| Regression gate locks the deploy wiring | Executed `tests/unit/test_gold_ddl_contract.py::test_uc_functions_are_wired_before_gold_ctas` | PASS |
| Sub-score parity gate between `borrower_360` and `lead_scores` | Executed `test_borrower_360_and_lead_scores_subscore_terms_stay_aligned` (this is the exact gate I recommended in the v1 audit) | PASS |
| FHA/VA fit parity gate | Executed `test_fit_loan_type_parity_and_explainability_contract` | PASS |
| NBO null-threshold UDF gate | Executed `test_next_best_offer_udf_fails_closed_on_null_thresholds` | PASS |
| Offer-thresholds materialization gate | Executed `test_offer_thresholds_are_ref_table_sourced_and_materialized` | PASS |

### Original audit findings → status

| Original finding | Severity | Status |
|---|---|---|
| Three NBO thresholds hardcoded in gold transformations | MEDIUM 1 | Closed — both gold transformations now read all five thresholds from `mip.ref.offer_rules_config` and materialize `*_applied` columns; Offer Orchestrator also reads applied thresholds from gold |
| Vestigial defence-in-depth call discarding `in_the_money()` result | MEDIUM 2 | Closed — replaced with real comparison + structured `borrower_itm_parity_drift` WARNING emit |
| Threshold-NULL fallback returns `refi_plus_heloc` instead of `nurture` | LOW 1 | Closed — both Python and SQL now fail closed to `nurture`; locked by `case_16` golden + `test_next_best_offer_udf_fails_closed_on_null_thresholds` |
| FHA/VA fit parity not test-locked, no compliance evidence | LOW 2 | Closed — `loan_type_fit` evidence row added (excluded from scoring), parity test `test_fit_loan_type_parity_and_explainability_contract` |
| Current-customer retention boost not flagged for fair-lending review | LOW 3 | Closed — explicit data-contract §5.1 section requires per-lender compliance sign-off |

### New finding surfaced and closed in this tranche

| Finding | Severity | Status |
|---|---|---|
| `mip_refresh_scores` deploy job refreshed gold without applying UC function DDL — golden parity passed locally while deployed UDF body was stale | HIGH (release-blocker) | Closed — 4 `init_fn_*` tasks added with `init_gold_ddl` depending on all four; locked by `test_uc_functions_are_wired_before_gold_ctas` in both `databricks.yml` and `resources/jobs.yml` |

### Cross-audit no-regression sweep

Spot-checked 19 invariants from prior audits and the BL primitives themselves. All 19 still hold.

| Audit | Invariant | Status |
|---|---|---|
| Critical v3 | `COMPAT_API_PREFIX = "/api"` still in `backend/main.py` | OK |
| API v2 | `X-API-Version: v1` emitted | OK |
| Obs v3 | correlation-id middleware present | OK |
| Arch v2 | Never-mock invariant policed by `test_architecture_boundaries.py` | OK |
| DR v2 | RTO/RPO + HMAC `kid` rotation present in DR doc | OK |
| SC v2 | `us-atlas` pinned, `@svg-maps/usa` absent | OK |
| MT v2 | `mip_lender_name` lender-identity binding | OK |
| AI v2 | Genie services intact | OK |
| Load v2 | `tools/load_test/baseline.json` present | OK |
| CB v2 | `frontend/src/design-system/tokens.css` present | OK |
| PERF v3 v2 | `configOptionsQuery` shared hook | OK |
| DOC v2 | 87/87 backend module docstrings (recomputed live) | OK |
| BL | `lead_score(50,50,60,50,50) == 52` (banker's rounding lock) | OK |
| BL | `rate_spread(0.0575, 0.04875) == 88` bps | OK |
| BL | `in_the_money(75, 15, 75, 15) is True` (inclusive boundary) | OK |
| BL | `next_best_offer(... NULL thresholds ...) == 'nurture'` | OK (new) |
| BL | `next_best_offer(listed=True) == 'purchase'` | OK |
| Doc-contract gate | All 8 functions PASS | OK |
| Supply-chain gate | All 4 functions PASS | OK |

### Gates exercised live

| Gate | Method | Result |
|---|---|---|
| `tests/unit/test_documentation_contract.py` | Manual exec of all 8 functions | 8/8 PASS |
| `tests/unit/test_supply_chain_licenses.py` | Manual exec of all 4 functions | 4/4 PASS |
| `tests/unit/test_scoring.py` (non-parametrized) | Manual exec | 4/4 PASS |
| `tests/unit/test_next_best_offer.py` (non-parametrized) | Manual exec | 6/6 PASS |
| `tests/unit/test_in_the_money.py` (non-parametrized) | Manual exec | 2/2 PASS |
| `tests/unit/test_rate_spread.py` (non-parametrized) | Manual exec | 1/1 PASS |
| `tests/unit/test_gold_ddl_contract.py` (non-parametrized) | Manual exec | 21/21 PASS (6 parametrized tests skipped due to sandbox lacking pytest fixture injection — not regressions) |
| All four golden-fixture parity suites | Manual Python recomputation against fixtures | **50/50 PASS** (12 lead_score + 11 rate_spread + 11 in_the_money + 16 next_best_offer) |

### Operating notes

- The release-blocker finding (deploy job didn't refresh UDFs) is the kind of bug that golden-fixture parity tests structurally cannot catch — local Python and source-level SQL both passed, but the deployed function body was stale. The new `test_uc_functions_are_wired_before_gold_ctas` regression gate is the right pattern: it asserts on the *deploy artifact*, not the source. Worth using this pattern in other tranches where there's a similar source-vs-deployed gap.
- The worktree carries 25 modified files plus the new audit doc. Scope matches the engineering signoff: scoring primitives + their UDF, two gold transformations, two evidence-events files, the deploy YAML pair (`databricks.yml` + `resources/jobs.yml`), the offers/borrower repository wiring, fair-lending docs, and tests. No commit/push performed; no `.git/index.lock` present.

### v2 verdict

**Findings after independent verification: 0 P0, 0 P1, 0 HIGH, 0 MEDIUM, 0 LOW.**

Every original finding (1 HIGH-equivalent + 2 MEDIUM + 3 LOW) is genuinely
closed in the working tree, locked by a real regression gate, and verified
against the post-deployment active app `01f1530971261d09af6eefef69992d05`.
The release-blocking deploy-wiring bug surfaced during engineering's
validation is also closed and gated. 50/50 golden cases pass Python parity.
No prior-audit invariant was broken in the process.

Sign-off: ready to commit.
