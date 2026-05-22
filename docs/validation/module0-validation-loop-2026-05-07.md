> **Internal implementation artifact. Not approved for public release.**

# Module 0 Validation Loop — 2026-05-07

Purpose: track the current full-validation loop for the Mortgage Intelligence
Platform Module 0 demo/product surface. A topic is not closed by a single
passing test. Closure requires independent audit, master-agent reproduction or
review, fixes when needed, re-audit, and deployed/live validation when the
surface is user-visible.

Current live app:

- URL: `https://mip-app-2543889327043640.aws.databricksapps.com`
- Active deployment after loop fixes: `01f14a5a205d17a9b27ade3d2e6e4a18`
- Databricks app update time after loop fixes: `2026-05-07T21:21:29Z`
- Compute status after loop fixes: `ACTIVE`

## Closure Rule

Each outer-loop topic must end in one of these states:

- `clean`: independent audits found no confirmed issue, and relevant tests/live
  proof passed.
- `fixed-clean`: at least one issue was confirmed, fixed, redeployed if needed,
  and independently re-audited clean.
- `blocked-external`: implementation is truthful but waiting on an external
  dependency such as Cotality MLS/Listings or Building Permits Delta Shares.
- `not-shippable`: confirmed issue remains open.

No topic may be marked `clean` or `fixed-clean` from source inspection alone
when the behavior is visible in the app.

## Outer-Loop Topics

| Topic | Scope | Initial status | Final status |
|---|---|---:|---:|
| Evidence chips and lineage drawers | All Lead Queue, Borrower 360, Offer, Genie, trusted-asset, proof, timeline, and support chips open accurate, distinct source drawers. | auditing | fixed-clean |
| Geography drilldowns | Home and Segment maps preserve filters through state/county/ZIP, no overlay collisions, breadcrumbs remain clickable, counts match filtered population. | auditing | fixed-clean |
| Segment and Lead Queue workflows | Multi-select AND semantics, secondary filters, ZIP/county/state deep links, export, approve/reject, Borrower 360 and Offer navigation. | auditing | fixed-clean |
| Genie answers | Stateful conversation, no broad deterministic answer masking, identifier-aware chart planning, proof/source/freshness display, governed refusal/degraded states. | auditing | fixed-clean |
| Genie actions | Open cohort in Lead Queue, save borrowers, draft campaign, compare offer strategies, and route persistence all use Lakebase/audit-backed state and preserve exact filters. Generic demo-export actions are intentionally unsupported until a real artifact writer exists. | auditing | fixed-clean |
| Data truth and source readiness | Cotality live sources, pending MLS/permit truth, first-party synthetic feed disclosure, FRED freshness, no mock fallback in runtime. | auditing | fixed-clean |
| PII and public-demo safety | Raw CLIP/Owner Link/property identifiers masked by default in API/UI/CSV/audit; public-demo mode is easy to verify and disable only intentionally. | auditing | fixed-clean |
| Audit and Lakebase state | Approvals, rejections, Genie actions, saved cohorts/drafts/inbox state write safe audit rows with evidence IDs and no PII. | auditing | fixed-clean |
| Visual polish and accessibility | Prototype-aligned layout, no overlapping text, polished empty/loading/degraded states, responsive demo viewport coverage. | auditing | fixed-clean |
| Deployment/release gate | DAB/direct deployment path, deploy freshness, smoke, unit/integration/e2e/live Playwright, and reviewer consensus. | auditing | fixed-clean |

## Independent Audit Lanes

| Lane | Focus | Agent | Status |
|---|---|---|---:|
| UI workflow audit | Visible workflows, evidence chips, maps, visual polish, prototype contract | `Pasteur` | PASS |
| Data/source truth audit | SQL/API/docs source semantics, rollup parity, pending feed truth | `Rawls` | PASS |
| Genie/action audit | Genie answers, chart planning, proof, actions, persistence | `Mill` | PASS |
| Governance/security audit | PII masking, audit metadata, public-demo safety, no fake runtime data | `Curie` | PASS after stale workspace-store test fix |
| QA coverage audit | Test matrix, live-vs-local gaps, flaky assumptions, release gates | `McClintock` | PASS |

## Baseline Validation Commands

Baseline commands should be run before fixes and repeated after any confirmed
fix that affects the relevant surface.

```bash
databricks apps get mip-app --profile DEFAULT -o json

TOKEN=$(databricks auth token --profile DEFAULT -o json | jq -r .access_token)
E2E_LIVE=1 \
MIP_APP_URL="https://mip-app-2543889327043640.aws.databricksapps.com" \
MIP_API_URL="https://mip-app-2543889327043640.aws.databricksapps.com" \
MIP_BEARER_TOKEN="$TOKEN" \
npm --prefix frontend run e2e:ci -- \
  tests/e2e/real_data.spec.ts \
  tests/e2e/demo_visual.spec.ts \
  tests/e2e/accessibility.spec.ts \
  tests/e2e/responsive.spec.ts
```

## Findings Log

Accepted findings were reproduced, fixed, and independently re-audited:

- ZIP rollup schema/CTAS order drift fixed in DDL + CTAS.
- Evidence contract removed non-emitted REO/equity_delta/mock-mode claims and
  corrected clustering documentation.
- Genie cohort actions now preserve county route filters and do not rehydrate
  non-persistable degraded/policy-blocked sessions.
- Segment map and deep-dive Lead Queue now preserve secondary Portfolio
  criteria (`occupancy`, `lien_status`, `owner_link`, `purchase_intent`,
  `min_equity_pct_label`) through state/county/ZIP and queue links.
- Audit metadata now recursively rejects PII-adjacent keys, validates nested
  `result_filters`, and allows reviewed `county` route filters only.
- Workspace saved-lead SQL now filters public borrower ids with a regex instead
  of prefix-only checks; the contract test was updated accordingly.
- Deploy now fails if the app URL cannot be resolved instead of falling back to
  localhost; nightly Lakebase validation is release-blocking.
- Segment-count parity now reads the current live FRED `MORTGAGE30US` row and
  fails if it is not `source='fred'` or is stale, avoiding weekly hard-coded
  rate drift.

Validation evidence:

- Local: `git diff --check`; frontend `build`, `lint`, `test`, and `tsc`;
  focused and broader pytest suites including Genie actions, geo rollups,
  audit/PII, Lakebase, SQL queries, and segment-count parity.
- Deploy: direct DAB deploy + Databricks App snapshot
  `01f14a5a205d17a9b27ade3d2e6e4a18`; Lakebase migrate; gold refresh; lifecycle
  sync; Genie rebind; deployed smoke PASS.
- Live Playwright: deployed URL run passed `79` tests with `1` intentional
  fault-injection skip across real-data, demo visual, accessibility, and
  responsive suites.
- Targeted map reconciliation: with `itm + investor + equity + retention` in
  AND mode, a sampled state/county/ZIP drilldown reconciled exactly, and sampled
  Lead Queue rows stayed within the selected geography with all selected segment
  codes.
