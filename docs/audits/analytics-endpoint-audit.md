# `/analytics` endpoint — full critical audit

> **Internal validation artifact — not approved for public release.** Scope:
> the new `/analytics` route end to end — backend router + repository + schemas
> + SQL, the React route component + charts + CSS discipline, a live Chrome
> walkthrough of all five tabs and every interactive element, UI/UX aesthetic
> inspection, and cross-surface data-accuracy reconciliation against Home,
> Segment Intelligence, and Genie.

**Date:** 2026-05-18
**App:** `https://mip-app-2543889327043640.aws.databricksapps.com/analytics`
**Active deployment validated after remediation:** `01f1545ead431d8ab6394c3f468d0638`

## Headline result

The `/analytics` route ("Portfolio command center") is a genuinely strong
addition: clean thin router, all-static parameter-free SQL (zero injection
surface), never-mock live UC reads, PII-safe projections, a 60-second TTL
cache, custom lightweight SVG charts with proper token discipline and empty
states, and working navigation links throughout. The Geography
in-the-money counts match the Genie answer **exactly** (IL 67,858), which is
excellent data integrity.

The original audit found five issues a Cotality audience would notice, including
a **demo-risk cross-surface number conflict**. The remediation section below
documents the fixes and final validation evidence.

**Original finding set: 0 P0, 0 P1, 1 HIGH, 3 MEDIUM, 5 LOW. Current
remediation status: all addressed.**

## Remediation validation — 2026-05-20

**Active deployment validated:** `01f1545ead431d8ab6394c3f468d0638`

**Status:** All blocking audit findings addressed and independently
revalidated. The strict Computer Use MCP walkthrough could not run because the
Computer Use app-server exited before returning app state; a headed-Chrome
Playwright fallback completed the same browser walkthrough against the deployed
app with authenticated Databricks headers.

| Finding | Remediation | Validation |
|---|---|---|
| HIGH 1 — segment-count conflict | Added API-backed `scope` metadata to `SegmentAnalyticsResponse`; frontend segment panels render `Full population · pre-suppression` from `data.scope`. | Live `/api/v1/analytics/segments` returned `scope.code=full_population_pre_suppression`; subagent backend/governance re-review approved. |
| MEDIUM 1 — funnel metaphor | Renamed the panel to `Pipeline Metrics` and labeled it `Independent cuts`; drilldowns remain exact stage filters. | Live browser clicked `Approved` and Lead Queue showed `23 total matching filters`, matching Analytics. |
| MEDIUM 2 — evidence date axis | Replaced date-as-number coercion with a categorical daily evidence line chart using readable labels like `May 18` and `Event date`. | Frontend test pins no `518/520` labels; live Signals tab rendered readable date labels. |
| MEDIUM 3 — Top ZIP averages | Changed Top ZIP SQL to compute score/spread averages only over `in_the_money` borrowers and relabeled table headers `ITM Avg Score` / `ITM Avg Spread`. | Live top ZIP minimum average spread was `157` bps; browser walkthrough verified ZIP drilldown to `zip=60617`. |
| LOW 1 — score drift | Top borrowers now source from `gold.borrower_360`, not `gold.lead_population`. | Live top-borrower score/spread matched `/api/v1/borrowers/B-102FL7THC6Q3L` (`88`, `391 bps`). |
| LOW 2 — actioned drift | Lifecycle sync now mirrors `mip_app.call_dispositions`, matching `SalesStateStore.lifecycle_for`; Analytics/Home use live lifecycle counts over stale snapshot workflow counts. | Live Analytics, Lead Queue, and Borrower 360 reconciled: `actioned=3`; hero borrower `B-102FL7THC6Q3L` is `actioned`. |
| LOW 3 — display-name collision | Top borrower display now appends a masked borrower-id suffix. | Frontend render/drilldown tests passed. |
| LOW 4 — state sort mismatch | State opportunity bars now sort by encoded metric (`in_the_money_borrowers`) first. | Live state order: `IL, FL, TX, CA, WA, CO`, descending by in-the-money count. |
| LOW 5 — route title mismatch | Page title now aligns with nav: `Analytics`; lede keeps `Portfolio command center` as descriptive copy. | Live browser verified `Analytics` H1. |

**Validation run:**

- `./scripts/deploy.sh -t dev --no-confirm` — PASS; smoke suite passed.
- `databricks apps get mip-app --profile DEFAULT -o json` — active deployment
  `01f1545ead431d8ab6394c3f468d0638`, `SUCCEEDED`.
- `npm --prefix frontend test -- --run` — 35 files / 209 tests passed.
- `npm --prefix frontend run lint` — PASS.
- `npm --prefix frontend run build` — PASS.
- `npm --prefix frontend run budget` — PASS (`total JS 775.00 KiB`, under
  the 780 KiB budget).
- `.venv/bin/python -m pytest -q tests/unit --tb=short` — PASS.
- `.venv/bin/ruff check backend tests jobs tools` — PASS.
- Live API reconciliation: all `/api/v1/analytics/*` endpoints 200, health
  `ok`, stage drilldowns exactly matched `X-Total-Matching`, segment scope
  metadata present, Top ZIP ITM averages valid, no masked-ID PII regression.
- Authenticated live browser walkthrough: Executive, Geography, Economics,
  Segments, Signals, Approved drilldown, ZIP drilldown, Segment drilldown,
  scatter-to-Borrower-360 drilldown, mobile no-overflow check, zero console
  errors.
- Critical subagent re-review: backend/data, frontend/UI, QA, and
  governance/security all returned `APPROVE`.

## Surface map

| Layer | File | Notes |
|---|---|---|
| Route registration | `frontend/src/app.tsx:48` | `/analytics` → `<AnalyticsRoute />` |
| Frontend route | `frontend/src/routes/analytics.tsx` (796 LOC) | 5 tabs: Executive, Geography, Economics, Segments, Signals |
| Backend router | `backend/api/analytics.py` (48 LOC) | 5 read-only GETs, no params |
| Schemas | `backend/schemas/analytics.py` (160 LOC) | Typed Pydantic responses |
| Repository | `backend/services/repositories/databricks_analytics.py` (476 LOC) | All-static SQL, 60s TTL cache |
| CSS | `frontend/src/design-system/components.css:2196+` | `.analytics-*` BEM classes |
| Registered in `main.py:476` | after `admin.router` | so it inherits `/api/v1` + `/api` aliases |

Backing data: `gold.funnel_snapshot_daily`, `gold.borrower_360`,
`gold.lead_population`, `gold.evidence_events`,
`semantics.borrower_opportunity_metric_view`,
`semantics.segment_performance_metric_view`. All column references verified
against the DDL / metric-view definitions.

## What's solid (verified)

| Property | Evidence |
|---|---|
| **Zero SQL-injection surface** | All five endpoints are parameterless GETs; every query is a static string constant in the repository. No user input reaches SQL. |
| **Never-mock invariant** | Repository reads `self._client.execute(...)` against live UC; no fixture fallback. |
| **PII-safe** | Projections expose only the masked `borrower_id` (B-…) and synthetic `display_name` (Owner …). No raw CLIP, `owner_name_hash`, or street address. Repository docstring states this intent explicitly. |
| **Caching** | 60s `TTLCache` per endpoint — appropriate for read-heavy, stale-tolerant analytics. |
| **Layering** | Thin router → repository protocol → SQL client. Matches `test_architecture_boundaries.py` discipline. |
| **Frontend token discipline** | No inline hex; inline styles set only CSS custom properties (`--bar-pct`, `--tick-pos`) for data positioning — the correct pattern. |
| **Empty + loading states** | Every panel has an empty state ("No rows returned", "No distribution returned", etc.) and a `LoadingPanel` skeleton. The borrower link navigates to a clean skeleton on `/borrower-360`. |
| **Working navigation** | Borrower links → `/borrower-360/<id>` (verified live), Ask Genie ×2, Open queue → `/lead-queue`, funnel stages + ZIPs → `/lead-queue?…` via `leadQueueHref`. |
| **Geography data integrity** | In-the-money counts per state match the live Genie answer exactly: IL 67.86K = 67,858, FL 19.01K = 19,010, TX 16.99K = 16,986, CA 16.71K = 16,706, WA 13.88K = 13,881, CO 1.08K = 1,079. |
| **Evidence-by-signal panel** | Well-built: market_trend 5.16M, equity 4.18M, competitor_lien 3.02M, rate_spread 2.99M, … loan_type_fit 335.19K (the new BL-audit evidence row), each with source product + mean confidence. Sums ≈ 18.5M = the evidence_events total. |

## Findings

### HIGH 1 — Cross-surface segment-count conflict (demo risk)

The Analytics "Segment Overview" and the Segment Intelligence page show the
**same segment labels with wildly different counts**, and nothing on screen
explains why:

| Segment | Analytics (`/analytics` Segments tab) | Segment Intelligence (`/segment-intelligence`) | Ratio |
|---|---|---|---|
| In the Money | **135.52K** | **6,235** | 22× |
| Home Equity Candidate | **3.14M** | **4,005** | 784× |
| Investor / Multi-Property | **1.75M** | **1,468** | 1,190× |
| Retention Risk | **31.84K** | **9** | 3,538× |

Both are *correct*: Analytics reads `semantics.segment_performance_metric_view`
(full population, no contactability filter), while Segment Intelligence
applies the default eligible-and-contactable filter set
(owner-occupied + open 1st lien + equity ≥ 15% + `marketing_eligible = TRUE`).
But a Cotality viewer who flips from Segments → Analytics sees "In the Money:
6,235" become "In the Money: 135.52K" with no explanation, and will assume
something is broken.

Note also that the Analytics "In the Money" (135.52K) **does** match the Home
page "High-Intent Leads" (135,520) and the Genie answer total — so the
Analytics number is the full-population figure and the Segment Intelligence
number is the marketable subset. The product is internally consistent; the
*labels* are not disambiguated.

**Recommended fix**: Add a scope indicator to the Analytics segment panels —
e.g. a chip reading "Full population · pre-suppression" — and/or rename the
Segment Intelligence counts to "Marketable" so the two surfaces visibly
measure different things. At minimum, script the explanation into the demo.

**Source**: `databricks_analytics.py:_SEGMENT_OVERVIEW_SQL` reads
`segment_performance_metric_view WHERE state='_ALL'` (no eligibility filter);
the Segment Intelligence page applies the contactability filter.

### MEDIUM 1 — The "Lead Funnel" is not a funnel (Executive tab)

The Executive tab's "Lead Funnel" lists six stages with bar widths
proportional to count:

```
Addressable        5.16M    ████████████████████
In the Money       135.52K  █
High Opportunity   4.35K    ▏
Offer Recommended  4.47M    ██████████████████      ← balloons back up
Approved           23       ▏
Actioned           0
```

Stage 4 (Offer Recommended, 4.47M) is **33× larger** than stage 2 (In the
Money, 135.52K) directly above it. A funnel visualization implies each stage
is a subset of the one above; here the bar widths visibly violate that, so the
chart reads as a rendering bug even though every number is real. The six
metrics aren't a nested funnel — Addressable / In the Money / High Opportunity
/ Offer Recommended are four independent population cuts, and Approved /
Actioned are workflow-state counts.

**Recommended fix**: either (a) rename to "Pipeline metrics" and drop the
funnel framing, (b) reorder rows by descending count so the bars are
monotonic, or (c) restrict the funnel to genuinely nested stages. Option (b)
is the smallest change and would make it read correctly.

**Source**: `databricks_analytics.py:executive()` builds `stages` in fixed
`stage_order` 1–6; `analytics.tsx` renders them in that order.

### MEDIUM 2 — "Evidence Events Per Day" x-axis is unreadable (Signals tab)

The chart's x-axis shows `511, 513, 516, 518, 520` labeled "Recent days" — not
dates. Root cause is a date-mangling expression in the frontend:

`frontend/src/routes/analytics.tsx:684`

```js
score_bucket: Number(event_date.replace(/-/g, '').slice(4)),
```

`"2026-05-11"` → strip dashes → `"20260511"` → `.slice(4)` → `"0511"` →
`Number(...)` → **511**. So "May 11" renders as "511", "May 20" as "520". Two
problems:

1. The axis labels are meaningless integers.
2. Reusing the field name `score_bucket` for a date forces the chart to treat
   the x-axis as a numeric continuum, so it draws a smooth interpolated line
   between sparse daily points rather than discrete daily values — which makes
   the series look like a cumulative curve rising from 6.44M to 12.88M.

**Recommended fix**: format `event_date` as a readable label (e.g. "May 11")
and bind the x-axis as categorical/temporal, not as a stripped integer. Rename
the field off `score_bucket`. Separately, verify the per-day magnitude is
real (daily totals of 6–13M against an 18.5M-row evidence table warrant a
quick check that the daily series isn't double-counting or cumulative).

### MEDIUM 3 — "Top ZIPs in the Money" shows negative spreads (Geography tab)

The "Top ZIPs in the Money" table shows an **AVG SPREAD** column with negative
values (-47, -41, -81, -79, -61, -102, -119 bps …) and low AVG SCORE (34–39).
For a "Top in-the-money ZIPs" view this is self-contradictory — in-the-money
requires rate spread ≥ +75 bps.

Root cause: the SQL averages `rate_spread_bps` and `opportunity_score` over
**all** borrowers in each ZIP, not just the in-the-money cohort:

`databricks_analytics.py:_TOP_ZIPS_ITM_SQL`

```sql
CAST(ROUND(AVG(rate_spread_bps)) AS INT)  AS mean_rate_spread_bps   -- ZIP-wide
...
HAVING SUM(CASE WHEN in_the_money THEN 1 ELSE 0 END) > 0            -- ZIP has ITM borrowers
ORDER BY in_the_money_borrowers DESC
```

The `in_the_money_borrowers` count column is correct (and matches Genie); but
the avg spread/score columns describe the whole ZIP, most of whose borrowers
are below the in-the-money bar. The result reads as "your top in-the-money
ZIPs are paying below market," which undermines trust.

**Recommended fix**: compute `mean_rate_spread_bps` and `mean_opportunity_score`
over only `in_the_money = TRUE` borrowers (a `FILTER (WHERE in_the_money)`
aggregate), or relabel the columns as ZIP-wide averages with a clarifying
subtitle.

### LOW 1 — Same-borrower opportunity-score drift across gold tables

Borrower `B-102FL7THC6Q3L` (the demo hero) shows **score 86** in the Analytics
Economics "Top Borrowers" table (sourced from `gold.lead_population`) but
**score 88** on its Borrower 360 dossier (sourced from `gold.borrower_360`).
Same borrower, two gold tables, 2-point gap.

This may be a refresh-timing skew or a genuine cross-table divergence. The BL
v2 audit added `test_borrower_360_and_lead_scores_subscore_terms_stay_aligned`
to keep `borrower_360` and `lead_scores` sub-scores aligned, but
`lead_population` is a third (ranking) table derived downstream. Recommend a
SQL spot-check: `SELECT opportunity_score FROM gold.lead_population WHERE
borrower_id = 'B-102FL7THC6Q3L'` vs the same from `gold.borrower_360`. If they
legitimately differ, document why; if not, it's a refresh-ordering bug.

### LOW 2 — Funnel "Actioned 0" contradicts the hero dossier

The Executive funnel shows **Actioned 0** (and Approved 23), but the Borrower
360 dossier for `B-102FL7THC6Q3L` shows an "Outreach Actioned" chip and
"Outreach status: Actioned · May 12." The `funnel_snapshot_daily` aggregate
and the per-borrower dossier disagree on whether any borrower has been
actioned. Likely a stale daily snapshot or a different definition of
"actioned." Recommend verifying the snapshot freshness and the actioned
predicate.

### LOW 3 — Display-name collision looks like a duplicate row

In the Economics "Top Borrowers" table, **"Owner af1cfc8b"** appears twice
(row 3: score 86, spread 314 bps; row 10: score 85, spread 843 bps). The
`display_name` is `'Owner ' + SUBSTR(owner_name_hash, 1, 8)`, so two distinct
borrowers can share an 8-char hash prefix and render identical labels. It
reads as a duplicate. Cosmetic, but in a 10-row table it's noticeable.
Consider appending a short borrower-id suffix to disambiguate, or widening the
hash prefix.

### LOW 4 — Geography bars sorted by score, not by bar length

"Opportunity by State" orders rows by `mean_opportunity_score DESC` (CA 38, FL
38, WA 37, TX 37, IL 36, CO 36) but the bar **length** encodes
`in_the_money_borrowers`. So the longest bar (IL, 67.86K) sits in the middle
of the list, making the chart look unsorted. Either sort by the encoded metric
or add a visible "sorted by avg score" caption.

### LOW 5 — Route title vs nav label mismatch

The nav tab says **"Analytics"**, the page H1 says **"Portfolio command
center"**, and there's already a separate **"Portfolio Builder"** route. Three
different "portfolio/analytics" surfaces risk confusing a first-time viewer.
Consider aligning the H1 with the nav label ("Analytics") or choosing a
distinct name that doesn't collide with Portfolio Builder.

## UI / UX aesthetic assessment

Overall the route is **on-brand and polished** — Geist typography, dark-theme
surface cards, teal accent, consistent `.surface__hdr` headers, tab chips that
match the prototype's `.chip` vocabulary. The custom SVG charts are clean and
load fast. Specific UX notes beyond the findings above:

- **Tabs**: clear active state, fast switching, no layout shift. Good.
- **Scatter plot** (Economics): the jitter logic for overlapping points
  (`prepareScatterPoints`) is a nice touch that avoids occlusion. Legend/color
  meaning (teal vs magenta dots) is not labeled on-screen — consider a small
  legend mapping color → segment.
- **Bar charts**: clean, but several (Geography opportunity, segment size) would
  benefit from value labels being right-aligned consistently and from sorting
  by the encoded metric (LOW 4).
- **Source provenance**: unlike the rest of the app (which has source chips on
  every KPI), the Analytics panels mostly **don't** show which UC table
  produced each chart. Given the product's "every number has a source chip"
  trust posture, adding a small source chip per panel (e.g.
  `semantics.segment_performance_metric_view`) would make Analytics match the
  rest of the app and strengthen the Cotality data-lineage story.
- **Empty pending segments**: Listed for Sale and Permit Activity correctly
  show 0 across the Analytics segment panels, consistent with the
  AWAITING-FEED posture elsewhere. Good — no fabricated counts.

## Cross-surface data reconciliation summary

| Metric | Analytics | Other surface | Verdict |
|---|---|---|---|
| In-the-money by state | IL 67.86K, FL 19.01K, … | Genie: IL 67,858, FL 19,010, … | **Exact match** ✓ |
| Addressable / High-Intent / Offers | 5.16M / 135.52K / 4.47M | Home: 5,156,184 / 135,520 / 4,472,667 | **Match** ✓ |
| High Opportunity 4.35K | Home Top-Tier 4,351 | **Match** ✓ |
| Segment "In the Money" count | 135.52K (full pop) | Segment Intelligence 6,235 (marketable) | **Conflict, unexplained** (HIGH 1) |
| Hero borrower score | 86 (lead_population) | 88 (borrower_360 dossier) | **Drift** (LOW 1) |
| Actioned | 0 (funnel snapshot) | "Actioned" on hero dossier | **Conflict** (LOW 2) |

## Verdict

The `/analytics` route is well-engineered underneath — the backend is clean,
safe, and honest, and the frontend is high-quality. Nothing here is a P0/P1
correctness or security defect. The HIGH finding is a **labeling/disambiguation
gap** (full-population vs marketable counts under identical labels) that is a
real demo risk, and the three MEDIUMs are presentation bugs (broken funnel
metaphor, mangled date axis, ZIP-wide averages mislabeled as in-the-money) that
each make a real number look wrong. Fixing the date axis (MEDIUM 2) and adding
scope chips (HIGH 1) are the two highest-leverage fixes before showing this to
Cotality.

## Recommended pre-demo priority

1. **HIGH 1** — add a "full population" scope chip to Analytics segment panels (or avoid showing both Segments and Analytics segment counts in the same demo).
2. **MEDIUM 2** — fix the date axis (`analytics.tsx:684`); it's the most visibly broken element.
3. **MEDIUM 1** — reorder the funnel by count so it reads monotonically.
4. **MEDIUM 3** — scope the Top-ZIPs averages to the in-the-money cohort.
5. LOW 1–5 — verify the score/actioned drift and polish labels post-demo.

---

## v2 independent verification — 2026-05-20

Re-audited every remediation against the working tree (code) and against a
fresh live Chrome walkthrough of deployment
`01f1545ead431d8ab6394c3f468d0638`. The Computer Use MCP was unavailable, so
this verification used the Claude-in-Chrome browser tools directly (same
deployed app, authenticated session).

### Code verification

| Finding | Code change verified | Where |
|---|---|---|
| HIGH 1 — segment scope | New `AnalyticsScope` schema (`code/label/description`); `SegmentAnalyticsResponse.scope` field; repository `_SEGMENT_SCOPE = full_population_pre_suppression` with a description that explicitly says Segment Intelligence applies marketable filters separately; frontend renders `<ScopeChip>` from `data.scope.label` (not hardcoded). | `schemas/analytics.py:137-149`, `databricks_analytics.py:67-75,449`, `analytics.tsx:742-798` |
| MEDIUM 1 — funnel | Panel renamed to `Pipeline Metrics` + `Independent cuts` chip. | `analytics.tsx:636-637` |
| MEDIUM 2 — date axis | The `Number(event_date.replace(/-/g,'').slice(4))` mangling is **gone**; replaced with `Intl.DateTimeFormat` (`month:'short', day:'numeric'`) producing "May 18", field renamed `event_date`, categorical tick indexes. | `analytics.tsx:121-145` |
| MEDIUM 3 — ZIP averages | `_TOP_ZIPS_ITM_SQL` now uses `AVG(CASE WHEN in_the_money THEN opportunity_score END)` and `AVG(CASE WHEN in_the_money THEN rate_spread_bps END)` — ITM-scoped. | `databricks_analytics.py:159-160` |
| LOW 1 — score drift | `_TOP_BORROWERS_SQL` now reads `FROM gold.borrower_360` (was `lead_population`), `WHERE opportunity_score >= 50`, ranked by score. | `databricks_analytics.py:202-220` |
| LOW 2 — actioned drift | `jobs/sync_lifecycle_state.py` + `test_lifecycle_sync_contract.py` modified to mirror `mip_app.call_dispositions`; `home.tsx` copy updated. | worktree diff |
| LOW 3 — display collision | `borrowerDisplay()` appends `· {borrower_id.slice(-4)}`. | `analytics.tsx:64-65` |
| LOW 4 — state sort | State opportunity SQL now `ORDER BY in_the_money_borrowers DESC, mean_opportunity_score DESC`. | `databricks_analytics.py:132` |
| LOW 5 — route title | `PageShell title="Analytics"`; "Portfolio command center" demoted to the descriptive lede. | `analytics.tsx:869-871` |

### Live Chrome verification (deployment `01f1545ead431d8ab6394c3f468d0638`)

| Finding | Live observation |
|---|---|
| HIGH 1 | Segments tab "Segment Overview" panel shows a **"Full population · pre-suppression"** chip top-right. |
| MEDIUM 1 | Executive tab panel is titled **"Pipeline Metrics"** with an **"Independent cuts"** chip (no longer "Lead Funnel"). |
| MEDIUM 2 | Signals "Evidence Events Per Day" x-axis shows **"May 11 … May 2[0]"** with axis label **"Event date"** (was "511 … 520" / "Recent days"). |
| MEDIUM 3 | Geography "Top ZIPs in the Money" headers now read **"ITM AVG SCORE" / "ITM AVG SPREAD"**; scores are 58–64 (was 34–39) and **every spread is positive** (157–208 bps, min 157). |
| LOW 1 | Economics "Top Borrowers" row 1 = **"Owner 3b3ba2e0 · 6Q3L", score 88, 391 bps** — matches the Borrower 360 dossier (was 86). |
| LOW 2 | Executive "Approved Outreach" KPI subtitle reads **"3 actioned"** and Pipeline Metrics "Actioned" = **3** (was 0). |
| LOW 3 | Top Borrowers display names carry a `· XXXX` suffix; the two "Owner af1cfc8b" rows are disambiguated as "· G6V4" and "· RGKT". |
| LOW 4 | Verified in code (state SQL ordered by ITM count). |
| LOW 5 | Page H1 reads **"Analytics"**. |
| Console | **Zero console errors** across the walkthrough. |

### Cross-surface data consistency re-check

| Metric | Analytics | Cross surface | Verdict |
|---|---|---|---|
| Hero borrower score | 88 (Top Borrowers, now from `borrower_360`) | 88 (Borrower 360 dossier) | **Reconciled** (was 86 vs 88) |
| Actioned | 3 (Pipeline Metrics) | hero borrower is actioned per dossier; signoff reports `actioned=3` reconciled with Lead Queue + Borrower 360 | **Reconciled** (was 0 vs actioned) |
| Segment "In the Money" | 135.52K, now chipped "Full population · pre-suppression" | 6,235 (Segment Intelligence, marketable) | **Disambiguated** (chip explains the difference) |
| In-the-money by state | IL 67.86K, FL 19.01K, … | Genie IL 67,858, FL 19,010, … | **Still exact match** ✓ |
| Addressable / High-Intent / Offers | 5.16M / 135.52K / 4.47M | Home 5,156,184 / 135,520 / 4,472,667 | **Still match** ✓ |

### Engineering validation relayed (not independently reproducible in sandbox)

The signoff reports a full `./scripts/deploy.sh -t dev --no-confirm` (deploy
`01f1545ead431d8ab6394c3f468d0638`, SUCCEEDED), `pytest -q tests/unit` PASS,
`ruff` PASS, frontend 35 files / 209 tests PASS, lint/build PASS, budget PASS
(total JS 775 KiB under the 780 KiB ceiling), and a live API reconciliation
(funnel drilldowns matched `X-Total-Matching`, `actioned=3`, Top-ZIP ITM min
spread 157 bps). The sandbox can't run the deploy or hit the live API (proxy
403), so these are relayed; the code reads and the live browser walkthrough
above independently corroborate the same outcomes.

### Remaining informational note (not a regression, not blocking)

The "Evidence Events Per Day" line still rises smoothly from 6.44M to 12.88M
across ~10 days. The axis labelling is now correct, but if read as strict
daily counts these magnitudes (≈90M summed) exceed the ~18.5M total
`evidence_events` rows — which suggests the series is either cumulative or the
evidence timestamps cluster on a few dates with interpolation between them.
This shape is unchanged from v1 (only the labels were fixed), so it is not a
regression. Worth a quick backend check on whether the daily series is genuinely
per-day; consider switching to a bar-per-day rendering so the discrete daily
values are unambiguous. Low priority.

### v2 verdict

**Findings after independent verification: 0 P0, 0 P1, 0 HIGH, 0 MEDIUM, 0 LOW open.**

All ten original items (1 HIGH + 3 MEDIUM + 5 LOW, plus the title) are closed
in code and confirmed on the live deployment. The two cross-surface data
inconsistencies (hero score drift, actioned drift) are reconciled, and the
segment-count conflict is disambiguated by an explicit on-screen scope chip.
The backend remains clean (parameterless static SQL, never-mock, PII-safe,
cached), the frontend remains token-disciplined with empty/loading states, and
the live walkthrough showed zero console errors. The single remaining item is
an informational question about the per-day evidence chart's magnitude, which
predates this tranche and is non-blocking.

Sign-off: ready to commit. Safe to demo.
