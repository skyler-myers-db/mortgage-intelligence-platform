# Module 0 — Business-Persona UX Review

- **Date:** 2026-04-22
- **Branch:** `fix/copilot-batch-post-merge` (post copy-scrub commit `b8b4d19`)
- **Viewport:** 1440×900 (design target)
- **Stack state:** Real Unity Catalog end-to-end (`5,156,184` marketable, `147,742` high-intent leads). Lakebase locally down (circuit breaker open — genuine degraded state, not a bug).
- **Personas audited:** Head of Growth, VP of Mortgage Lending, Marketing Leader, Sales Manager, plus a technical-buyer pass.

## Summary

- **31 findings total** — **9 blockers**, **15 polish**, **7 nits**.
- The copy scrub fixed surface-level language but several **systemic** issues remain, any one of which would kill a live demo for a specific persona.
- The most damaging finding is not copy at all: **every lead in the top-500 queue carries the same opportunity score (66-68)** — the product's core pitch ("explainable rank") collapses on inspection.
- The second most damaging finding is the **borrower identity surface** — `display_name = "Owner d1a3a065"` appears as a row label, dossier H1, and email salutation, making the app look like a stats tool with synthetic data rather than a production lead system.
- Enterprise-polish basics are missing: **no favicon, no OG/Twitter meta, no page description**. Shows up the moment anyone Slacks a link.

---

## Top 10 fix list (prioritized for pre-demo)

| # | Severity | Finding | Effort | Owner hint |
|---|----------|---------|--------|------------|
| 1 | BLOCKER | Top-500 leads all score 66-68 — only 3 unique values across 500 rows | M | `sql/uc_functions/fn_lead_score.sql` + `backend/services/scoring.py` — check score distribution, not just top band |
| 2 | BLOCKER | Borrower identity surface (`Owner d1a3a065`) appears as list label, dossier H1, and outreach email salutation | S-M | Policy decision (Option A-D below) + `backend/services/scoring.py` projection + `components/mortgage/LeadTable.tsx` + borrower-360 header + outreach template |
| 3 | BLOCKER | "Synthetic property · CHICAGO, IL 60609" label on Borrower 360 Customer 360 panel — leaks demo vocabulary into a compliance-defensible dossier | XS | `frontend/src/routes/BorrowerThreeSixty.tsx` Customer 360 card |
| 4 | BLOCKER | Outreach draft email reads "Hi Owner d1a3a065 … (>= 75) and equity 79% (>= 35% HELOC-grade)" — hash salutation + rule-engine syntax | S | Template in `backend/services/` (search for "help you evaluate") |
| 5 | BLOCKER | Rail icons M1/M2/M3/M4 all link to `/admin-config` — dead navigation that advertises features the product doesn't ship | XS | `frontend/src/app.tsx` rail — either disable until built or route to `/planned/m1` placeholder |
| 6 | BLOCKER | Geography drill only supports IL/CA/TX ("Click IL, CA, or TX to drill") — CLAUDE.md mandates full 6-state footprint (IL/CA/FL/TX/WA/CO) | M | Map component + `sql/transformations/gold_*` state coverage |
| 7 | BLOCKER | 503s on `/api/audit/events` when Lakebase is down propagate to console errors on every page; banner copy "Reconnecting to lakebase" leaks infra vocabulary to business buyer | S | Banner copy → "Reconnecting to campaign store"; audit endpoint should return `200 + empty` with status flag, not `503` (degraded UI is already in place) |
| 8 | BLOCKER | Evidence chips across the app are raw function names (`fn_rate_spread`, `mlflow.mtg_nbo_v3`, `rules.itm_v3`, `permits.building`) — engineer-speak on the compliance surface a VP of Lending reviews | M | Chip registry → human labels with hover-to-reveal technical name |
| 9 | BLOCKER | CLIP inconsistency: segment-preview row shows `clip_b0stszho4o5j04` (lowercased borrower_id tokenization) while Borrower 360 shows `4707924298` (realistic Cotality 10-digit) — two different CLIP surfaces for the same borrower | S | Trace CLIP projection in `backend/services/*` between leads list and borrower detail |
| 10 | POLISH (near-blocker) | No favicon, no `<meta name="description">`, no OG/Twitter tags — internal Slack preview of the app URL will render as `Mortgage Intelligence Platform` + a blank box | XS | `frontend/index.html` + add SVG favicon in `frontend/public/` |

---

## Per-route findings

### `/` — Landing / Home

Screenshots: `screenshots/persona-review/01-landing-vp-lending.png`, `01b-landing-belowfold.png`

#### Head of Growth
- **Expected:** Addressable-market TAM, funnel, projected ROI by segment.
- **Actual:** KPIs shown: Marketable Population (5.16M), High-Intent Leads (147k), Cost per contact ($2.18), Projected Contact→App (9.7%). Below the fold: geography drill + audit log + "Future modules" row.
- **Gap (polish):** Cost per contact at $2.18 is precise-looking but unsourced on-tile — a Head of Growth wants "why $2.18?" a hover away. The `config` evidence chip tells them nothing.
- **Gap (polish):** "Projected Contact → App 9.7%" — no baseline comparison. Is 9.7% good? What's industry benchmark? A Head of Growth reads 9.7% as "could be 30%, could be 3% — I have no reference".
- **Gap (nit):** `mlflow · mtg_nbo_v3` on the contact-to-app tile is engineer-speak; a Head of Growth doesn't know what `mtg_nbo_v3` means.

#### VP of Mortgage Lending
- **Actual:** "Review approval required before outreach" banner — clear human-gate message. Good.
- **Strength:** Data-source breadcrumbs (`cotality.public_records`, `mip.gold.lead_population`) — a compliance officer likes seeing provenance. Keep; label them "Source" not just `<chip>`.
- **Gap (polish):** "Refreshed 06:12 UTC" — no date, so at a demo at 10 AM PT a buyer will say "was that today or yesterday?".

#### Marketing Leader
- **Actual:** Hero H1 "Who should we contact, why now, and with what offer?" — perfect.
- **Gap (nit):** "Grounded on Cotality public records, liens, listings, permits, AVM, and mortgage market data" — keep, but the next sentence "Every recommendation is traceable, every score has a rationale, and nothing is sent without human approval" is talking past them. A Marketing Leader wants "show me who I can activate in Q3."

#### Sales Manager
- **Gap (blocker #5):** The rail shows M0/M1/M2/M3/M4 icons. M1-M4 all link to `/admin-config`. A Sales Manager clicking "M2" lands on Admin Config and concludes the app is broken. Resolution below-the-fold "Future modules · PLANNED" section explains the intent, but the rail link is ahead of the explanation.

#### Cross-cutting
- **Blocker #7:** `Reconnecting to lakebase` banner is on every page on local. Copy should be "Reconnecting to campaign store" or "Reconnecting to audit store" — "lakebase" is a Databricks product name and business personas don't know it.
- **Blocker #6:** Map on landing shades only IL/CA/TX; FL/WA/CO (three of the six promised states) appear empty/dark. A buyer in Florida asks "so Florida isn't ready?"

### `/portfolio-builder`

Screenshot: `02-portfolio-head-of-growth.png`

- **BLOCKER #1 surfaces here too:** "AVG. BORROWER SCORE 42" — a Head of Growth reads 42 as F-grade. Either relabel ("avg score, 0-100 scale") or rescale to something that doesn't trigger school-grade pattern-matching (percentile?).
- **Polish:** Filter chip `GEO Chicago MSA` — pre-selected in a 6-state product. Creates the same "is Florida supported?" fear as the map.
- **Polish:** Builder filters (`OCCUPANCY Owner-occupied`, `LIEN STATUS Open 1st lien`, `RELATIONSHIP All`, `PRODUCT All products`, `EQUITY ≥ 15%`) look right for the persona. Good.
- **Nit:** `mip.gold.lead_population` chip in the top-right is engineer-speak; rename to "Source: Lead population (Unity Catalog)".

### `/segment-intelligence`

Screenshots: `03-segments-marketing-leader.png`, `03b-segments-belowfold.png`, `03c-segments-row-expanded.png`

- **Polish:** Every segment card shows "+0%" growth indicator. Zero uniformly suggests the WoW calculation is broken or unwired. Either wire it or hide until the second weekly snapshot exists.
- **Polish:** Each segment card shows "avg 44 / avg 40 / avg 60 / avg 47" — what's it averaging? A Marketing Leader reads "avg" and doesn't know if it's score, age, tenure, ticket size.
- **Polish:** "Retention Risk 749" is 4 orders of magnitude smaller than peers (3.1M / 1.7M / 147k / 749). Looks broken next to the others. If the segment is genuinely small, add a "why small?" chip.
- **BLOCKER #9:** Row-expand preview shows CLIP as `clip_b0stszho4o5j04` (lowercased borrower_id with `clip_` prefix). Borrower 360 for the same borrower shows CLIP `4707924298`. Two different identifiers presented as "CLIP" in the same session breaks the evidence trail a VP of Lending depends on.
- **Polish:** Footer `SELECT * FROM mip.gold.lead_scores WHERE segment IN (…)` — raw SQL is useful *audit* evidence but shouldn't be a visible footer. Move behind an "Audit evidence" expander.

### `/lead-queue`

Screenshot: `04-leads-sales-manager.png`

- **BLOCKER #1 (most damaging):** Programmatic inspection of 500 rendered rows: only **3 unique score values (66, 67, 68)**. First 6 rows all 68; next several all 67. The explainability pitch ("every row carries an opportunity score, confidence meter, evidence chip") fails because the scores don't differentiate anything. Likely causes: score is sorting to a top band; `fn_lead_score` clamps to a narrow range; or the endpoint already filters to `score >= 65`. Whatever the cause, this is the single most important fix.
- **BLOCKER #2:** Every row's BORROWER column is `Owner <hash>` on top and `B-<alphanum>` underneath. No human signal. A Sales Manager handing this to a LO can't say "call Mr. Chen" because there is no name.
- **Polish:** Every row shows CONFIDENCE as 4 solid bars. Same uniformity problem as score.
- **Polish:** `NEXT-BEST-OFFER` column shows `Refinance + HELOC · nbo_v3` on every visible row. `nbo_v3` is a model-version tag — belongs behind a hover chip, not a prominent column value.
- **Good:** `PII suppressed · compliance` chip header is excellent compliance signaling.
- **Nit:** `RATE Δ (BPS)` column header — use a word, not a Greek character, for this audience ("Rate spread (bps)"). A Sales Manager reads Δ as "uncertainty" or just skips it.

### `/borrower-360/<id>`

Screenshot: `05-borrower360-vp-lending.png`

- **BLOCKER #2:** Page H1 is `Owner d1a3a065`. A dossier that opens with a hash name fails every persona.
- **BLOCKER #3:** Customer 360 card: `Subject property · Synthetic property · CHICAGO, IL 60609`. "Synthetic" leaks demo vocabulary into the same dossier a VP of Lending would screenshot for a compliance audit.
- **BLOCKER #8:** Evidence chip row: `fn_rate_spread`, `fn_in_the_money`, `mlflow.mtg_nbo_v3`, `borrower_dossier`, `permits.building`. A compliance officer reviewing an outreach decision needs to read "Rate-spread rule (v3)", "In-the-money threshold", "Next-best-offer model (mtg_nbo_v3)", "Borrower dossier snapshot", "Property permit record" — not function names.
- **Good:** "Rationale: +246 bps spread (>= 75) AND 79% equity (>= 15%)" — spelled out, human-readable, sourced. Keep.
- **Good:** "Approval pending" chip + Next-best-offer panel + "Build outreach draft" button — the gate is clearly human-driven.
- **Polish:** "Trigger timeline" items say "1D AGO" and "2D AGO" without a date. On a Monday-morning demo, "2D ago" means Saturday, which reads as "we're working over weekends on this borrower?" Use ISO date or "Apr 20".
- **Polish:** Related properties "2 (via Owner Link)" is a great signal but Owner Link (`1100000134187756`) is displayed as a raw 16-digit number. Label: "Owner Link ID (Cotality master)".

### `/offer-orchestrator/<id>`

Screenshot: `06-offer-marketing-leader.png`

- **BLOCKER #4 (demo-killer):** Outreach draft copy reads:
  > "Hi Owner d1a3a065 — based on recent public-record signals in CHICAGO, IL, Summit Mortgage may be able to help you evaluate refinance + heloc options. Rate spread +246 bps (>= 75) and equity 79% (>= 35% HELOC-grade) — refi + HELOC cross-sell. Reply if you'd like a licensed officer to follow up."

  Problems: (a) hash salutation, (b) rule-engine syntax ("(>= 75)", "(>= 35% HELOC-grade)") leaks internals, (c) lowercase "refinance + heloc" looks unproofed, (d) "refi + HELOC cross-sell" is internal vocabulary that shouldn't appear in a customer-facing email.
- **Good:** "Draft outreach · review only, never auto-sent" label is excellent human-in-the-loop signaling. Keep.
- **Polish:** "Sources" chips under Primary Offer are raw function names again. Same fix as Borrower 360.
- **Polish:** `Considered alternatives` + `Thresholds applied · Admin config at decision time` — this is a great compliance artifact, should be called out visually (a "Decision-trace" headline badge). Currently it reads as two small cards below the primary offer.

### `/ask-genie`

Screenshot: `07-genie-head-of-growth.png`

- **Polish:** "Trusted assets" lists `mip.gold.lead_population`, `mip.gold.lead_segment_membership`, `mip.gold.lead_scores`, `mip.gold.evidence_events`, `mip.semantics.lead_generation_metric_view` — raw UC FQNs. Head of Growth needs human names: "Marketable population (Cotality)", "Segment membership map", "Lead opportunity scores", "Evidence events stream", "Metric view — lead generation".
- **Good:** Suggested questions are persona-appropriate ("Which zips have the most in-the-money refi candidates?", "Show HELOC candidates with recent permits and strong equity").
- **Good:** `Production: Databricks Genie API` chip says it's the real thing, not a mock.
- **Nit:** No conversation history visible on arrival — for a "conversational analytics" surface, no example prior exchanges shown. Suggest a sample Q&A or recent-questions section so buyers see what a good answer looks like.

### `/admin-config`

Screenshot: `08-admin-vp-lending.png`

- **Good:** Admin page is now well-scoped — presentation controls, offer rules, audit settings, data-source readiness. Reads like a thoughtful ops console.
- **Good:** `Lakebase schema mip_app.audit_events · append-only · exported nightly to UC for compliance review` — exactly the kind of sentence a technical buyer wants to read.
- **Nit:** "Data source readiness · 8 sources · Delta Share" list runs as a comma-separated paragraph ("Public Records · Voluntary Lien · MMA · CLIP · Owner Link · MLS · Building Permits · AVM"). A bulleted list with a green/amber dot per source would make this a compliance exhibit instead of a sentence.

---

## Cross-cutting: Borrower identity treatment

The hash-gibberish identity field is the persona-agnostic blocker. Where it appears:

1. **Leads table** — row label (`Owner d1a3a065`) + subtitle (`B-0STSZHO4O5J04`)
2. **Segment Intelligence row-preview** — same
3. **Borrower 360 H1** — dossier opens with `Owner d1a3a065`
4. **Offer Orchestrator** — score/dossier badge (inherits from Borrower 360)
5. **Outreach draft email salutation** — `Hi Owner d1a3a065`

### Options (ranked for this product)

**Option D — Remove `display_name` entirely; lead with property signals. (Recommended)**
Lead with CLIP + property (`CHICAGO, IL · 606094708 · Owner-occupied · 2 props via Owner Link`). A Sales Manager handing this to a LO gets "call the owner of 606094708 Chicago — 2 properties, 79% equity, in-the-money 246 bps". That's a better brief than a fake name. Outreach is always the LO's — the app doesn't need to fabricate a salutation, and the email template should leave the greeting to the officer ("[Officer personalization]" placeholder).

**Option C — Synthesize deterministic placeholder names ("Household #48291", "[Redacted — revealed on approval]")**
Second-best. Makes it clear no PII is fabricated, still gives the list something to scan. Harder to maintain and easy to misread as "actual data we're hiding".

**Option B — Replace `display_name` with `Lead #12345`**
Workable but loses the "dossier" feel — a loan officer reviewing Lead #12345 won't feel like they're reading a person's story.

**Option A — Keep `display_name` but demote; raise CLIP + location**
Don't recommend. The "Owner d1a3a065" string still appears and still looks like a hash.

**Recommendation:** Adopt Option D. Treat `display_name` as internal metadata only (not rendered). Lead-table primary column becomes `CLIP · CITY, ST` with the property address-ish composite; Borrower 360 H1 becomes `Household in CHICAGO, IL · CLIP 4707924298`; outreach email body uses `[Officer personalization]` as the salutation placeholder and the LO fills it in at send time. This is also the most legally-defensible posture (no fabricated names render anywhere in the outreach channel).

---

## Cross-cutting: Enterprise aesthetic / meta

| Finding | Severity | Fix |
|---------|----------|-----|
| No `<link rel="icon">` (serves 404 on `/favicon.ico`) | polish | Add SVG favicon to `frontend/public/`, reference in `frontend/index.html` |
| No `<meta name="description">` | polish | Single line, ~155 chars, ships with index.html |
| No `og:*` tags | polish | Add `og:title`, `og:description`, `og:image` (1200×630 snapshot of landing) |
| No `twitter:card` | polish | Add `twitter:card=summary_large_image` |
| Console errors on every page: 503 for `/api/audit/events`, 404 for favicon | polish | Audit endpoint should return degraded-200 with empty list + status flag rather than 503 (the degraded-UI exists already in the page) |
| Raw UC FQNs exposed as evidence labels | blocker #8 | Human labels with hover tooltip for technical name |
| "Synthetic property" label | blocker #3 | Remove or replace with "Property address withheld (PII)" |
| "lakebase" / "warehouse" vocabulary in Reconnecting banner | blocker #7 | "campaign store" / "data warehouse" |
| M1-M4 rail icons link to `/admin-config` | blocker #5 | Disable or route to `/future/m1` placeholder |
| Light-theme segment pills on Leads (e.g. `Home Equity Candidate`) are near-white-on-cyan | polish | Bump chip contrast in `design-system/tokens.css` for `data-theme=light` |
| Light-theme rail M0/M1/M2/M3/M4 labels are near-invisible | polish | Same |
| `PII suppressed · compliance` chip low-contrast in light theme | nit | Chip token polish |
| Light-theme "Borrowers in selection 17,261" map legend low-contrast | nit | Same |

---

## Cross-cutting: Data quality issues (surfaces through UX)

| Finding | Severity | Evidence |
|---------|----------|----------|
| Top-500 leads score distribution is 3 unique values (66/67/68) | **blocker #1** | Programmatic DOM scan of `/lead-queue` — 500 samples, `[...new Set(vals)] === ['68','67','66']` |
| All confidence meters show 4 bars | polish | Visual across Leads route |
| Every segment card shows "+0%" WoW | polish | Visual across Segments route |
| Map coverage is 3/6 states on landing, more (but not all 6) on Segments | **blocker #6** | Landing shades IL/CA/TX; Segments map shades more but still partial |
| CLIP shown differently in list vs dossier | **blocker #9** | `clip_b0stszho4o5j04` in segment row-expand vs `4707924298` in Borrower 360 |

None of these are "the copy is off". They are data-quality issues that the UI is honestly exposing. Fixing them is 60% SQL / scoring work, not frontend work.

---

## Observations worth keeping

A few things this review confirmed are **working well** and should not be regressed:

- Approval-gate language is clear on every decision surface ("Approval pending", "Review only, never auto-sent", "Review approval required before outreach"). This is the compliance story working.
- Evidence chips (even when mis-labeled) are present on every KPI — the traceability pattern is baked in.
- The degraded-state UI on the landing audit panel ("Audit feed is briefly unavailable. This page will retry on the next refresh; live dependency state is shown below.") is excellent — it tells the user exactly what happened and what's still working. This is the resilience posture paying off.
- Ask Genie's `Production: Databricks Genie API` chip is the right badge to show when a buyer asks "is this real or a demo?".
- The 8-route spine is navigable at 1440×900 with no horizontal scroll or layout break.

---

## Next recommended action

Triage this list with the user. In priority order:

1. Fix #1 (score distribution) — it's the single finding that would be most damaging in a live demo.
2. Decide on borrower-identity Option (A/B/C/D above) — then fix #2, #3, #4 together as a single "identity + dossier hygiene" slice.
3. Fix #5 (rail dead links) — 20-minute frontend fix, eliminates a "is this broken?" moment.
4. Fix #6 (6-state map coverage) — probably a SQL/gold-table coverage issue; spec it.
5. Batch #7 + banner copy + audit 503 → 200 fix as one "degraded-state copy" commit.
6. Batch #8 + human-labeled evidence chips + Ask Genie asset labels as one "evidence vocabulary" commit.
7. Polish pass (#10 favicon/meta, light-theme contrast, "+0%" WoW, uniform confidence, etc.) as a single low-risk commit.
