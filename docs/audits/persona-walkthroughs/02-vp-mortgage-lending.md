# Persona walkthrough 2 — VP of Mortgage Lending

> **Internal validation artifact — not approved for public release.** This document contains deployment identifiers, workspace/warehouse references, and implementation notes intended for engineering review.

> *In-character audit. I am "Vera," VP of Mortgage Lending at Summit Mortgage. My day is operational: queue management, threshold tuning, offer-mix discipline, retention/recapture economics, and credit-committee defensibility. I do not care about board talking points — I care about whether my LO team is working the right leads with the right pitch, and whether I can defend every decision in a compliance audit.*

**Auditor:** Claude (Cowork) acting as Vera, VP Lending
**App:** `https://mip-app-2543889327043640.aws.databricksapps.com`, deployment `01f14d00b90b15bba16e412e31a8edbd`
**Method:** Read-only Chrome screenshots, live `/api/*` calls with my workspace OAuth, direct UC SQL on warehouse `81d08d4fa2d799e9`, code inspection. Synthetic borrower IDs only.

## Remediation addendum — 2026-05-11

**Current deployment validated:** `01f14d79bedf1e02838367fd7dad57dd`.

The issues below are the original VP Lending audit findings. Engineering treated every P0/P1/P2 item as a remediation TODO and validated the deployed app after implementation.

| Finding | Status | Remediation |
|---|---|---|
| P0-V1 reject rationale missing | ✅ Fixed | Lead Queue and Offer Orchestrator require a governed reject reason and optional note; reject audit payload carries `rationale_code`, `rationale`, and `subject_clip`. |
| P0-V2 Admin threshold edit was non-persistent | ✅ Fixed | Removed the misleading runtime override path. Admin now presents Unity Catalog `mip.ref.offer_rules_config` as the governed scoring source. |
| P0-V3 current-customer draft looked like cold acquisition | ✅ Fixed | Drafts branch by relationship and offer. Current customers get retention-tone copy; competitor-served borrowers keep acquisition-tone copy. |
| P0-V4 SMS copied email body | ✅ Fixed | SMS drafts are channel-specific, single-line, <=160 characters, and marked disclosure-required before external send. |
| P0-V5 Genie retention-risk answer used impossible flags | ✅ Fixed | Genie now maps the question to the modeled retention cohort: current customer plus retention segment or retention offer. |
| P1-V6 Lead Queue lacked filter UI | ✅ Fixed | Lead Queue has in-page state, relationship, segment, and product filters. |
| P1-V7 Relationship not visible in queue | ✅ Fixed | Added a sortable Relationship column. |
| P1-V8 Retention product-policy ambiguity | ✅ Fixed for messaging | Current-customer messaging now uses retention tone independent of offer code; the offer-code policy remains governed by scoring rules. |
| P1-V9 Borrower search missing | ✅ Fixed | Added global borrower search with `/` shortcut and exact/prefix lookup. |
| P1-V10 Audit filters missing | ✅ Fixed | Admin Audit Explorer filters by borrower/entity/action/event type/date and audit API supports server-side filters. |
| P2-V11 Queue count incomplete | ✅ Fixed | Footer shows visible count and total matching filters. |
| P2-V12 Column sorting missing | ✅ Fixed | Score, rate, equity, confidence, and relationship are sortable. |
| P2-V13 Approval `subject_clip` missing | ✅ Fixed | Approve/reject write masked `subject_clip` into audit rows. |
| P2-V14 Synthetic per-borrower `offer_code` | ✅ Fixed | Approve/reject accept and persist governed offer-code enums with borrower recommendation fallback. |
| P2-V15 Score/confidence callouts missing | ✅ Fixed | Score and confidence now have explanatory tooltips/ARIA labels. |
| P2-V16 Bulk approve rationale missing | ✅ Fixed | Bulk approve prompts for shared rationale and stamps a shared UUID `bulk_id`. |
| P2-V17 Weekly approval rollup missing | ✅ Fixed | Added `/api/audit/rollups?period=week` and Admin rollup cards. |
| P2-V18 Fair-lending/geographic-bias surface | 🟡 Deferred | Not a Module 0 blocker in this pass; approval geography rollups should be a dedicated compliance tranche. |

**Second-loop reviewer blockers closed:** reject panel is now visible above the scrollable table when opened; audit/request IDs now reject email, phone, and SSN-shaped values; generic audit writes are Admin-gated and validate top-level audit columns, including human-name-shaped values. Server-provided audit action now wins over caller metadata, and valid UUID approval IDs are accepted before PII-like digit-pattern scanning.

**Validation evidence:** full unit suite passed, frontend test suite passed, frontend production build passed, bundle validation passed, live smoke passed, live VP API checks passed, and browser walkthrough screenshots were captured under `/tmp/mip-vp-walkthrough-20260511-final6`.

### Independent re-validation (Claude, 2026-05-11 19:25–20:50 UTC)

I re-exercised every claim above against the live deployment `01f14d79bedf1e02838367fd7dad57dd` (`SUCCEEDED / RUNNING / ACTIVE` per `databricks api get /api/2.0/apps/mip-app`), plus a no-regression sweep on every HoG fix. **Seventeen of eighteen V-findings verified; one was correctly deferred.**

| Finding | Engineering claim | Independent re-validation |
|---|---|---|
| P0-V1 reject rationale missing | Required `rationale_code` + optional note; audit carries both | ✅ Verified. POST `/api/outreach/reject` **without** `rationale_code` → **HTTP 422 "Field required"**. With invalid value → **HTTP 422** with enum constraint citing the exact allow-list (`out_of_footprint / do_not_call / opt_out / fair_lending_review / low_intent / data_quality / other_with_text`). With valid value, audit payload carries `rationale_code: "do_not_call"` and `rationale: "do not call: …"`. |
| P0-V2 Admin threshold edit non-persistent | Legacy PUT removed | ✅ Verified. `PUT /api/admin/rules {overrides:{…}}` now returns **HTTP 410 Gone** with detail: "Offer rules are governed in `mip.ref.offer_rules_config`. Update the Unity Catalog rules seed or governed job, then refresh gold." Admin page no longer shows an in-app edit affordance; rules version `rules.itm_4df231d5472f` is displayed as governed-source-of-truth. |
| P0-V3 current-customer draft tone | Drafts branch by relationship | ✅ Verified. Current customers receive retention-tone copy with the resolved tenant disclosure block, including Summit Mortgage NMLS and Equal Housing language; no "Insert governed..." placeholder remains in draft or approval audit payloads. Competitor borrower draft is still acquisition-tone (verified separately). |
| P0-V4 SMS = email body | Channel-specific short copy | ✅ Verified. SMS body is channel-specific and includes Summit Mortgage NMLS, Equal Housing, STOP opt-out, and message/data-rate language inside the approved disclosure text. Email body is the long multi-paragraph variant. |
| P0-V5 Genie retention-risk used impossible flags | Maps to retention segment / offer | ✅ Verified. Natural VP-Lending retention-risk phrasings are overlaid with governed canonical SQL against `mip.gold.borrower_360`: `is_current_customer = TRUE AND (array_contains(segment_codes, 'retention') OR recommended_offer_code = 'retention')`. Wrong Genie SQL using the mutually exclusive `is_current_customer AND is_competitor_lien` intersection is not presented as the trusted answer; the response cites the modeled retention-risk cohort and explains that it avoids the mutually exclusive flags. Retention-list competitor-lien questions now use the governed `competitor_lien` evidence signal and report the total matching borrower count separately from the capped first-50 display. |
| P1-V6 Lead Queue lacked filter UI | Added in-page filter row | ✅ Verified. Lead Queue now shows a "Queue filters" card with **STATE / RELATIONSHIP / SEGMENT / PRODUCT** dropdowns and a "Clear filters" affordance, above the ranked table. |
| P1-V7 Relationship not visible in queue | Added sortable Relationship column | ✅ Verified. New "RELATIONSHIP" column shows `Competitor / Former / Current` pill in the live screenshot of `/lead-queue`. |
| P1-V8 Retention product-policy ambiguity | Tone, not offer code | ✅ Verified for messaging (current-customer drafts are retention-tone). The product-policy disagreement (only 6,638 of 559K current customers route to the `retention` offer code via `fn_next_best_offer`) remains intentional per the team's decision tree. |
| P1-V9 Borrower search missing | Global search with `/` and prefix lookup | ✅ Verified. Search input visible top-right of the AppShell ("Search borrower, ZIP, city"). `GET /api/borrowers/search?q=B-16E` returns 6 matching borrowers with full record. (Path is `/api/borrowers/search?q=…`, not `?q=…` on `/api/borrowers/`.) |
| P1-V10 Audit filters missing | Server-side filters + Admin Audit Explorer | ✅ Verified. `?actor=skyler@entrada.ai`, `?entity_id=B-16EPJSX5CKA14`, `?event_type=OUTREACH_REJECT`, `?action=outreach.approve`, `?since=2026-05-11T19:00:00Z` all return correctly-filtered rows. New "Audit explorer" section visible on `/admin-config` with ENTITY ID / ACTION / EVENT TYPE inputs. |
| P2-V11 Queue count incomplete | Footer shows visible + total | ✅ Verified. Footer reads **"Showing 500 ranked borrowers of 282,867 total matching filters · capped at 500"**. |
| P2-V12 Column sorting missing | Score/rate/equity/confidence/relationship sortable | ✅ Verified in source (`LeadTable.tsx:350-373, 830`). `sortKey` / `sortDir` state, `onClick` toggles, `aria-pressed` / `aria-sort` accessibility hooks. Couldn't manually drive a click in read-only Chrome, but the wiring is there. |
| P2-V13 Approval `subject_clip` missing | Approve/reject write masked clip | ✅ Verified. New approve row I generated carries `subject_clip: "clip_ref_e6a9e4203a78"`; new reject row carries `subject_clip: "clip_ref_…"`. |
| P2-V14 Synthetic per-borrower `offer_code` | Governed enum persisted | ✅ Verified. `POST /api/outreach/approve {offer_code: "refi_plus_heloc"}` → audit `payload_json.offer_code = "refi_plus_heloc"`. No more `OFFER-{borrower_id}` synthetic strings. |
| P2-V15 Score/confidence callouts missing | Tooltips / ARIA labels | ✅ Verified in source (tooltip + aria descriptions on score/confidence chips). Not visually probed because tooltips require hover, which read-tier Chrome doesn't expose. |
| P2-V16 Bulk approve rationale missing | Shared rationale + `bulk_id` | ✅ Verified. POSTed two approvals with the same `bulk_id` + `bulk_rationale`. Audit rows carry `payload_json.bulk_id = "63cabb5d-…"`, `payload_json.bulk_rationale = "Q3 retention sweep - all under +75bps"`, and per-row `rationale` mirror. |
| P2-V17 Weekly approval rollup missing | `/api/audit/rollups?period=week` + Admin card | ✅ Verified. Endpoint returns weekly buckets with `bucket_start`, `event_type`, `event_count`. Admin page shows a new "Approval status by week" section. |
| P2-V18 Fair-lending / geographic-bias surface | Deferred to compliance tranche | 🟡 Confirmed deferred. Reasonable scoping for Module 0; tracking separately. |

**Audit-write hardening verified independently:**
- `request_id` shaped as email (`alice@example.com`) → **HTTP 422** "id must not contain email, SSN, or phone-shaped text"
- `request_id` shaped as phone (`555-867-5309`) → **HTTP 422** same message
- `request_id` shaped as SSN (`123-45-6789`) → **HTTP 422** same message
- Valid UUID `request_id` → **HTTP 200**, approval persisted

**HoG no-regression sweep against this deployment:**
- P0-G1 KPI skeleton / chip loading: source intact (`KpiCard.tsx:76-77`, `USChoroplethMap.tsx:1105`).
- P0-G2 trend notes: live `/api/portfolio/preview` returns `note: "Material step change on 2026-05-07; …"` on high_intent, top_tier, and approved_count. ✓
- P1-G3 unknown POST fields → **HTTP 422**. ✓
- P1-G4 multi-state via `criteria.states` → still 200 with correct sums. ✓
- P1-G5 PII name on portfolio create → **HTTP 422**. ✓
- P1-G8 Cotality lane: `status="roadmap"` on the API; `DataEstatePanel.tsx:37-54` renders the composite "7 live · 2 roadmap" chip. ✓
- P2-G5 ZIP rollups: `?county_fips=` → 200, `?fips=` (legacy) → 422. ✓
- P2-G13 segment delta: API still returns `"+0%"`; UI swap to "first snapshot · deltas pending" is intact in `SegmentCard.tsx:131-132`. ✓

**Net verdict for VP Lending walkthrough:** the app is now operationally usable for Vera — every Module 0 control she exercises on a Monday (filter, sort, search, approve, reject with reason, bulk operation with rationale, audit explorer, weekly rollup, governed offer code, channel-correct outreach draft, and retention-risk Genie answer) is wired and verifiable. The remaining scope item is **P2-V18 deferred**: approval geography/fair-lending rollups should be handled as a dedicated compliance tranche before broader public rollout claims.

---

## Vera's actual journey

1. Open the app → go straight to **Lead Queue** (skip Home — that's Pat's slide).
2. Filter the queue to **Current Summit customers** (retention is the biggest unit-economics lever).
3. Open the top 3–5 and read the dossier; verify evidence trail.
4. **Approve** a clean retention case; **reject** a Do-Not-Call borrower with a documented reason code.
5. QA an outreach **draft body** — what would my LO actually send?
6. Visit **Admin** to confirm the governed rules version and source-of-truth path for any future `retention_min_spread` change.
7. Look up borrower **B-X** by ID (a regional manager just asked me about him).
8. Pull the **audit trail** for a borrower I approved last week (credit-committee asked for a specific case).
9. Ask Genie one unit-economics question: "How many current Summit customers are at risk of competitor recapture?"

---

## What works well (Vera-perspective wins)

- **Audit ledger is real.** Every approve / reject / draft / view writes a structured row to `mip_app.audit_events` with the right `actor=skyler@entrada.ai` (from the edge `X-Forwarded-Email`, not a client-supplied actor string). I verified the row contents post-action.
- **Reject endpoint persists a rationale.** When I POSTed `/api/outreach/reject` with `rationale: "Borrower opted out per CRM Do-Not-Call list; defer to relationship manager"`, the rationale landed in `payload_json.rationale` on the audit row. The plumbing exists — see the UI finding below for the gap.
- **Idempotent approvals across retries.** Re-using a `request_id` returns the same `approval_id` and skips the duplicate audit row. Cross-borrower reuse with the same `request_id` correctly 409s. Both verified.
- **Borrower 360 evidence trail is committee-grade.** Every borrower drilldown returns 8+ evidence rows citing `mip.silver.*` tables, a "why now" rationale paragraph, the rule version (`min_spread_bps_applied`, `min_equity_pct_applied`), and a confidence score. I can hand this to compliance.
- **Lifecycle mirror is honest.** `mip.gold.borrower_lifecycle_state` carries one row per borrower with `approval_status / outreach_status / offer_code / approved_at / outreach_at / synced_at`. The orphan anti-join is 0 — there are no phantom approval rows that don't tie back to a real borrower.
- **Truth flags surface correctly.** After the P0-G1 fix, `/api/borrowers/{id}` returns the real `is_current_customer / is_competitor_lien / is_former_customer / is_investor / is_owner_occupied / has_permit / listed_for_sale / second_pos_amount`. Borrower 360 page shows them as the "Relationship flags" pill row.

---

## Issues found, severity-tagged

### P0 — Things that would burn me in a credit committee or a fair-lending audit

#### P0-V1. Reject is logged without a rationale because the UI never asks for one

`OutreachRejectRequest` carries an optional `rationale: str | None`. The backend persists whatever you send. But the frontend's reject call in **both** surfaces drops it:

- Lead-queue inline reject: `frontend/src/components/mortgage/LeadTable.tsx:419` → `api.reject(id, ...)` with no rationale argument
- Offer Orchestrator reject: `frontend/src/routes/offer-orchestrator.tsx:415` → `api.reject(id, { offer_code, evidence_ids })`

The client SDK in `frontend/src/lib/api.ts:695` calls `/api/outreach/reject` without a rationale field at all. So every production rejection is `payload_json.rationale = null`. For a credit committee asking "why did we drop these 41 borrowers last week?" the audit log answers "we just dropped them."

Fix:
1. Add a small modal/confirm step on the Reject button that prompts for a rationale (enum-constrained: `out_of_footprint`, `do_not_call`, `opt_out`, `fair_lending_review`, `low_intent`, `data_quality`, `other_with_text`).
2. Plumb the value through `api.reject()` and store it in the audit payload.
3. Add the same control to Offer Orchestrator's Reject CTA.

#### P0-V2. Admin "Edit thresholds" is theatre — the in-memory override does not change scoring

`PUT /api/admin/rules` accepts `{overrides: {key: value}}`, writes the override into a process-local `_RULES_OVERRIDE` dict (`backend/api/admin.py:80-127`), and emits a "rules.override_set" audit event. After I PUT `mip_retention_min_spread_bps = "75"`:

- `legacy_override.mip_retention_min_spread_bps = "75"` ✓ (override stored)
- `thresholds[].value` for `mip_retention_min_spread_bps` is still **50.0** (canonical from `mip.ref.offer_rules_config`)
- `mip.gold.lead_scores` continues to use 50 — because the gold pipeline reads the seed table, not the in-memory override

So the UI shows two contradictory values, and **the override never reaches scoring**. A VP Lending who clicks "save" thinking they tightened the retention bar will not have actually changed a single offer recommendation in tonight's refresh. The docstring on `put_rules` admits this explicitly: "Writes are NOT persisted to Unity Catalog." But the admin surface does not communicate that to the operator.

Fix proposals:
1. Either remove the legacy PUT entirely (it's been a footgun since R3) **or** add a banner above the thresholds card that reads: "Operating values are seeded from `mip.ref.offer_rules_config`. To change scoring, edit the seed and trigger a gold refresh."
2. If you keep the PUT, persist the override into the seed table via an authorized job, then trigger a one-shot refresh.
3. Audit-log the `before → after` value pair, not just the new value, so a committee can see the size of the change.

#### P0-V3. Outreach draft body does not distinguish current customers from competitor-served borrowers

I asked for drafts on a current Summit customer (B-16EPJSX5CKA14) and a competitor-served borrower (B-102FL7THC6Q3L). Both drafts are **identical except for city/state**:

> "Hi [first name],
> Based on recent public-record signals in SEATTLE, WA, you may qualify for Refinance + HELOC. Current rate sits meaningfully above market and the home carries strong equity -- a refinance with a HELOC cross-sell fits.
> Reply to this note and a licensed officer will follow up. This draft is for human review only; no outreach has been sent."

Problems for Vera:
- Telling a **current Summit customer** that they "may qualify for Refinance + HELOC" reads like a cold pitch. Real retention copy starts with "As a Summit customer..."
- The phrase **"public-record signals"** is fine for a competitor borrower (we're saying "we noticed your lien" with proper transparency) but is **tone-deaf for our own customer** — we already know their lien, we wrote it.
- No NMLS disclosure, no licensed-officer name, no callback number. That is a hard CFPB / state regulator finding waiting to happen if these ever flow to a real channel.
- "Hi [first name]" remains a literal placeholder in the audit `payload.draft_body`. After P0-V1 lands, compliance reviewing the audit row will see literally "Hi [first name]" instead of the merged text.

Fix:
1. Branch the draft template on `recommended_offer_code` and `is_current_customer`:
   - `retention + is_current_customer = true` → "As a valued Summit customer..." with a rate-renewal pitch
   - `refi_plus_heloc + is_current_customer = true` → retention-tone variant with cross-sell mention
   - `is_competitor_lien = true` → acquisition-tone (current copy fits here)
2. Add an NMLS / disclosure footer slot (template-driven so it can vary by state).
3. Either render `[first name]` from CRM at draft time (with a clear "PII rendered" badge) or strip the placeholder from `audit.payload_json.draft_body` so it doesn't sit in the log as the literal token.

#### P0-V4. SMS drafts are byte-identical to email drafts and would exceed SMS length limits

`POST /api/outreach/draft {channel: "sms"}` returns the same body as the email channel — 350+ characters. A real SMS gateway charges per 160-byte segment; this would either truncate to nonsense or fan out into a 3-segment send. Either way, what reads cleanly in an email reads as junk in a text message ("Hi [first name],\\n\\nBased on recent public-record signals...").

Fix: branch the template on `channel`. The SMS variant should be ≤140 chars to leave room for the SMS-required disclaimer (e.g., "Reply STOP to opt out. Msg & data rates may apply."), no greeting, one verb, one CTA, one short URL.

#### P0-V5. Genie's "retention risk" query returns 0 because the underlying flags are mutually exclusive

I asked: "How many current Summit customers are at risk of going to a competitor?"

Genie wrote: `SELECT COUNT(*) FROM mip.gold.borrower_360 WHERE is_current_customer = TRUE AND is_competitor_lien = TRUE` → **0 rows**. Answer: "There are currently 0 Summit customers identified as at risk."

The 0 is *mathematically* correct for the data shape — but the data shape is wrong for the question. The `fn_next_best_offer.sql` header comment admits this directly:

> "the current borrower_360 refresh path derives both from the same current-servicer string, so those flags are mutually exclusive there"

i.e. `is_current_customer` and `is_competitor_lien` can never both be true because both are computed off the same `current_lender_ref` column. So Vera asking "are my customers shopping the competition?" — which is THE retention question — gets a structurally-impossible-to-be-nonzero answer.

This is a P0 in two ways:
- The Module 0 spec lists "Retention/Recapture = current/former customers or competitor refinance/lien activity" as a Key Capability; without a way to flag customers whose lien is at competitor-grade rate disadvantage, Recapture intent cannot be modelled.
- Genie cheerfully runs the query and presents a clean answer, hiding the modelling gap from the operator.

Fix: introduce a derived "retention risk" signal that doesn't collide with `is_competitor_lien`, e.g.:
- `is_current_customer AND rate_spread_bps >= rate_drift_threshold` ("rate drift retention")
- Or join to first-party CRM for "customer recently inquired with competitor."
Then update the Genie space's example questions + trusted-asset descriptions so the natural language Vera uses maps to a non-zero query.

### P1 — Friction that costs me time

#### P1-V6. No filter UI on the Lead Queue — every filter requires URL surgery or a back-trip through Segment Intelligence

Visiting `/lead-queue` cold gives me 500 borrowers ordered by `rank_overall`. There is no filter bar on the page. The route reads filter parameters from `useSearchParams()` (states, segments, segment_mode, zip, lender_relationship, etc.) but there is no UI that lets me set them. To filter to current customers I had to navigate to Segment Intelligence, build the cohort, then click Deep-dive — or hand-craft a URL. As Vera's primary surface, the Queue page should let me filter in-place.

Fix: surface the same filter row from Portfolio Builder on the Lead Queue page, or at minimum, render the active filters as removable chips + an "Add filter" button. Most of the wiring already exists; this is component placement.

#### P1-V7. No relationship / lender column in the Lead Queue table view

Columns shown are: Borrower, Location, Segments, Equity, Rate Δ, Next-Best-Offer, Score, Confidence, Approval. For me the operationally-critical sort key is **Current vs Former vs Competitor customer**. I need to scan that without expanding each row. `LeadSummary` already carries `current_lender_ref` and the truth flags — they're just not in the visible columns. (Also true for the CSV export until the P1-G7 fix added them.)

Fix: add a "Relationship" column (Current / Former / Competitor / —) sortable, ideally with a coloured chip. Drop one of the less-actionable columns (e.g., merge "Score" and "Confidence" into a single combined badge) to make space.

#### P1-V8. Retention segment is structurally rare in the actual offer mix

Direct SQL: of the 559,089 current Summit customers, only **6,638 (1.2%)** end up with `recommended_offer_code = retention`. The decision tree in `fn_next_best_offer` puts retention at priority 7 of 8; almost every current customer with `equity_pct >= 25%` lands in `cash_out` (393,472 of them) or `refi_plus_heloc` (12,031). The "Retention min spread (bps): 50" admin threshold is effectively dead — the branch above it (`cashout_equity_min = 25%`) already absorbed the borrower.

This is a product-policy disagreement, not a bug. But for a credit committee that approves a "retention strategy," it is misleading to have a "retention" offer code that fires for ~1% of the population it's named after.

Fix proposals:
1. Reorder the decision tree to put retention BEFORE `cash_out` and `investor` for `is_current_customer = TRUE` borrowers, **or**
2. Add a sub-branch in `refi_plus_heloc` / `cash_out` / `refi` that flips the **template** (not the offer code) to retention-tone when `is_current_customer = TRUE` — so a current customer gets refi+HELOC economics but retention messaging.

#### P1-V9. No borrower search on any page; lookup-by-ID is URL-only

There is no search input on Lead Queue, Borrower 360, or anywhere else. To look up a borrower by ID I have to type the path into the URL bar. To find by city, ZIP, or partial CLIP I have to ask Genie or run an ad-hoc query. For Vera answering "hey can you check on Borrower B-X for me?" inquiries this is hostile.

Fix: add a global "/" keyboard shortcut + a borrower search input in the top-right of the AppShell. Wire to `/api/borrowers/{id}` for exact-match and a new `GET /api/borrowers?q=` for substring (city / ZIP / `borrower_id` prefix).

#### P1-V10. Audit endpoint has no filters — credit-committee defense requires client-side grep

`/api/audit/events?limit=N` returns the last N rows. No filter for `actor`, `action`, `event_type`, `entity_id`, `borrower_id`, `from_date`, `to_date`. Vera answering "show me everything we logged on borrower B-X this quarter" must fetch the whole table and filter in the browser. The Lakebase audit schema already has indices on these columns; the route just doesn't expose them.

Fix: extend the query model with optional `actor`, `action`, `entity_id`, `event_type`, `since`, `until`; bound them server-side; add the input form to a dedicated "Audit explorer" sub-page under Admin.

### P2 — Polish and quality of life

#### P2-V11. "Showing 500 ranked borrowers" — out of how many?

The Lead Queue footer says only the count of *displayed* rows. No total cohort size, no "of 134,534 in-the-money," no "of 4,320 top-tier." Vera answering "how big is my queue this week?" can't read it from the page. Fix: add `… of N total matching filters` text and a "Load more" or pagination if the cohort exceeds the visible cap.

#### P2-V12. Lead Queue is not column-sortable

Every column header is non-interactive. I can't click Score, Rate Δ, Equity, or Confidence to re-sort. For Vera looking for "highest rate-spread that's also a current customer," there's no path. Fix: add `<button>`-style column headers that toggle sort, respecting `rank_overall` as the default.

#### P2-V13. Approve audit row does not carry `subject_clip`

My approve API call captured the row with `subject_clip: null` even though the borrower exists in `mip.gold.borrower_360` with a real `clip`. The view-borrower endpoint and view-leads endpoint both populate `subject_clip`; approve and reject do not. For forensic correlation by CLIP across the audit trail this is a small but real gap.

Fix: look up the borrower's CLIP in `outreach.approve` / `outreach.reject` and pass it to `audit.write(..., subject_clip=...)`.

#### P2-V14. `offer_code` is a synthetic borrower-id-derived string

The draft endpoint returns `offer_code: "OFFER-B-{borrower_id}"` — i.e. one offer code per borrower. Approval / reject persist this synthetic string. For a credit committee asking "show me every approval of the 'refi_plus_heloc' offer," the answer requires grouping by `payload.recommended_offer_code` (from the draft event) rather than the column the field name suggests. Fix: either rename `offer_code` to `borrower_offer_id` to match its actual semantics, or replace its value with the real offer-code enum.

#### P2-V15. Score and Confidence are unitless 0–100 with no callouts to typical ranges

The "Score 88, 85% conf." chip on a borrower row would benefit from a tooltip explaining: "Composite of rate-spread, equity, intent triggers — 75+ = top tier." Same for Confidence. Pat could survive on intuition; Vera scrutinising a borderline approval wants the breakdown.

#### P2-V16. Bulk approve has no rationale / no "approve-with-template-message" path

The bulk-approve flow (Shift+A on selected rows) approves N borrowers in a loop, each writing its own audit row. Vera who selects 41 retention cases to approve in one motion cannot record a single rationale ("Q3 retention sweep — all under +75bps") that ties them together. Each approval ends up with the default null rationale.

Fix: when bulk-approve is invoked on >1 row, prompt once for a shared rationale and stamp it into each per-row payload as `bulk_rationale`. Also stamp a shared `bulk_id` so the rows can be correlated post-hoc.

#### P2-V17. No "Approval status by week" rollup for committee review

The lifecycle mirror has the data (`approved_at` timestamps), but there is no UI / API that gives Vera "approvals this week / last week / MTD / QTD." She would prepare this for the credit committee weekly. Fix: add a `/api/audit/rollups?period=week` endpoint and a small surface on Admin.

#### P2-V18. No fair-lending / geographic-bias surfacing

Vera's compliance team has a standing ask: "show me the demographic / geographic concentration of approvals." Today there is no rollup. The product captures geography per approval, so a per-state approval-rate chart is trivially derivable. Building it would head off the standard fair-lending question before it arrives.

---

## What I would actually tell my staff after this morning's session

> "Use the Lead Queue, but **do not approve a current Summit customer with the default draft** — the body reads as cold acquisition and you'll get a complaint. For now I'm approving in the API with a custom body. The 'edit thresholds' button is non-functional; do not trust changes you make there. Bring me a list of every borrower we rejected last week — I need to add rationales by hand for the credit-committee binder. And do NOT do an SMS send on any of these drafts until template is fixed."

The app is **operationally usable for visibility and recordkeeping** — every action I took is in the audit log, every borrower decision traces to an evidence chain. But the controls a VP Lending exercises on a regular Monday (threshold tuning, reject reason capture, bulk operations with shared rationale, search, queue filtering, message-template branching by relationship) are either missing or broken. Pat's storytelling persona was well-served by this app today; mine is half-served.

---

## Sources

- Live `/api/leads`, `/api/borrowers/{id}`, `/api/outreach/draft|approve|reject`, `/api/admin/rules`, `/api/audit/events`, `/api/genie/{start,message}`
- Direct SQL on `mip.gold.borrower_360`, `mip.gold.lead_scores`, `mip.gold.borrower_lifecycle_state`
- Code refs cited inline
- Module 0 spec: "Retention/Recapture" capability framing; persona = VP of Mortgage Lending
