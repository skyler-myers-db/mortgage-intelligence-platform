# Failed Readiness Audit — ChatGPT external-audit remediation re-verification

> **Internal validation artifact — not approved for public release.**
> **This is not a signoff. Do not cite it as approval to ship, demo, or claim readiness.**

**Date:** 2026-05-21  
**Branch:** `hardening/module0-release-readiness`  
**Hardening commit:** `9bb5cf7 fix: harden module0 release readiness`  
**Live app:** `https://mip-app-2543889327043640.aws.databricksapps.com`  
**Deployment ID:** `01f1553bcc5b1be4a6a4ee8804c93190`  
**Verdict:** fixes are committed and deployed, but readiness is **failed/blocked** by live Playwright failures and pending MLS/permit data shares.

## Headline

The remediation is real and now deployed. The release package hygiene gate, `/leads`
fail-closed marketing eligibility fix, SQL identifier validation/allowlist, CSS
literal lint, release-readiness artifact, and EvidenceDrawer focus trap are all
present in the committed tree and deployed to the Databricks App.

However, the required live browser walkthrough is **not green**. The deployed API
smoke and live integration checks passed, but the deployed Chromium Playwright
matrix failed 15 tests. This audit is a failed readiness record, not a signoff.

## Deployment Evidence

`./scripts/deploy.sh -t dev --no-confirm` completed successfully.

Key deploy output:

- Databricks App snapshot deployed and started successfully.
- App deployment ID: `01f1553bcc5b1be4a6a4ee8804c93190`.
- FRED refresh: succeeded, latest FRED observation `2026-05-18`.
- Cotality silver refresh: succeeded.
- Lakebase migration: succeeded.
- Gold/scoring refresh: succeeded.
- Lifecycle sync and daily funnel snapshot: succeeded.
- Genie space rebound: `01f13d4968af1b249dc388fd5b18b195`.
- `scripts/smoke_live.sh`: PASS against the deployed app.

Smoke coverage included health, portfolio preview, ranked leads, borrower dossier,
evidence timeline, data estate proof, source readiness, geo state/county/ZIP
rollups, outreach draft, outreach approval audit write, and Genie message.

## Live API Evidence

Authenticated probes against the deployed app showed:

- `/api/v1/health`: `status=ok`; warehouse, Lakebase, and Genie all `up`; all circuit breakers `closed`.
- `/api/v1/leads?state=CA&segment=itm&limit=50`: 50 rows, 0 rows with `marketing_eligible != true`.
- `/api/v1/leads?state=CA&include_suppressed_for_analytics=true&limit=50`: 50 rows, 47 suppressed rows returned under the admin analytics override.
- Unauthenticated `/api/v1/leads?include_suppressed_for_analytics=true&limit=1`: HTTP 401.
- `/api/v1/admin/sources`: MLS and Building Permits remain `roadmap`, `rows=null`, `synthetic_demo=false`, `last_updated=null`.

This proves the default actionable lead surface is fail-closed in the live app,
and that the suppressed analytics view opens only through an authenticated path.
It does not prove a non-admin authenticated user is blocked; that still needs a
non-admin Databricks identity or token.

## Live Integration Evidence

The live Databricks integration batch exited 0 with a fresh CLI token, warehouse
ID, and Genie space ID:

```bash
.venv/bin/python -m pytest -q -ra \
  tests/integration/test_sql_python_parity.py \
  tests/integration/test_segment_count_parity.py \
  tests/integration/test_gold_data_truth.py \
  tests/integration/test_source_readiness_live.py \
  tests/integration/test_genie_live.py
```

Lakebase's standalone `test_lakebase_round_trip.py` was not run because local DB
credentials were not present. The deploy smoke still proved the live app could
write an outreach approval audit row through Lakebase.

## Live Browser Walkthrough

The deployed app was opened through headless Chromium at the required
`1440x900` viewport with a Databricks bearer header. A direct page-load probe
rendered the live Home route and returned 200s for the app shell APIs:
health, audit events, data estate, state rollups, config options, footprint,
workspace, and portfolio preview.

The full deployed Chromium matrix was then run:

```bash
E2E_LIVE=1 \
MIP_APP_URL=https://mip-app-2543889327043640.aws.databricksapps.com \
MIP_API_URL=https://mip-app-2543889327043640.aws.databricksapps.com \
npx playwright test \
  tests/e2e/real_data.spec.ts \
  tests/e2e/demo_visual.spec.ts \
  tests/e2e/accessibility.spec.ts \
  tests/e2e/responsive.spec.ts \
  tests/e2e/genie_proof_layout.spec.ts \
  tests/e2e/route_performance.spec.ts \
  --project=chromium --workers=1 --reporter=list
```

Result: **81 passed, 15 failed, 1 skipped**.

### Playwright Blockers

Accessibility:

- Dark Home route has serious `color-contrast` failures for KPI negative deltas
  and data-estate muted copy.
- Light Home, Portfolio Builder, Segment Intelligence, and deep-linked Offer
  Orchestrator have serious `color-contrast` failures.

Visual/demo layout:

- Segment card grid clips dynamic copy.
- Segment geography drill did not reach/render `.zip-tiles` in the ZIP-layer
  breadcrumb test.
- Genie proof layout test timed out waiting for the `Show proof` button.

Evidence/source drawer contract:

- Genie and several evidence chips opened a drawer whose subtitle was
  `mip.gold.borrower_360` or `CLIP + Owner Link` where the live test expects the
  governed product label, such as `Borrower 360 feature set`, `Next-Best-Offer
  logic`, or `Property + owner graph`.
- Borrower 360 specific-drawer test did not find the expected `Customer 360`
  surface.

Passing browser evidence is still meaningful: responsive route-performance
canaries passed across desktop/mobile/ultrawide widths; core real-data golden
path tests passed for masked Cotality identifiers, non-zero segment counts,
multi-select segment filtering, map drill filters, evidence drawer with at least
two rows, audit write on approval, portfolio-builder live KPIs, analytics URL
state, lead-row preview, inline approval evidence IDs, governed progress during
Genie request, cohort action to Lead Queue, and admin presentation controls.

## Per-Finding Verdict

| External finding | Verdict after deploy |
| --- | --- |
| Exported package contained local artifacts | **Closed.** The repo never tracked `.env.local`/`.databricks`; the new git-archive package gate blocks the manual-zip failure mode. |
| `/leads` dropped marketing eligibility on drilldowns | **Closed in code and deployed.** Live default lead query returned 0 ineligible rows; admin analytics override returned suppressed rows; unauthenticated override returned 401. Non-admin authenticated proof remains needed. |
| MLS/Listings + Building Permits required | **Open.** Still `roadmap`/pending; no fabricated live rows. |
| Source/package workflow not enforced | **Closed.** `make zip` + `release_hygiene.py` passed and CI has a release-hygiene job. |
| Live completion unverifiable | **Partially closed.** Deploy, smoke, and live integration now ran; Playwright is red and Genie benchmark remains not run. |
| SQL identifier validation/allowlist | **Closed.** All current call sites are covered. |
| Scoring fixtures only pin lead score | **False finding.** Per-primitive tests already exist. |
| EvidenceDrawer focus trap | **Closed in code and deployed.** Full browser accessibility is still blocked by contrast failures. |
| Hard-coded design colors | **Closed.** CSS literal lint is wired into frontend lint and passed locally before deploy. |
| Monolithic files | **Open/deferred.** Manual file-size gate exists; CI wiring and splits remain. |
| Raw exception strings in audit logs | **Open/unverified.** Not part of this hardening pass. |

## Release-Readiness Artifact

`tools/release_readiness.py` wrote:

- `dist/release-readiness.json`
- `dist/release-readiness.md`

The artifact records package hygiene, bundle validation, SQL/live integration,
Lakebase smoke, Genie live, and source readiness as passed; Genie eval as not
run; Playwright live as failed; MLS/Listings and Building Permits as pending.

## Final Readiness Result

The original process violation is partially corrected for this branch: the
hardening work is committed, deployed, smoke-tested, integration-tested, and
browser-tested against the live Databricks App.

The product receives **no signoff**. The blockers are specific and live-proven:
Playwright failures, pending MLS/permit data shares, missing non-admin
authenticated eligibility proof, missing standalone Genie eval, unwired
file-size gate, and low-risk audit exception-string cleanup.

Future signoff requires all blockers closed, full live browser walkthroughs
green, and independent critical review lanes converging on a readiness judgment.
