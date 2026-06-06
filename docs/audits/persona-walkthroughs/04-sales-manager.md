# Persona walkthrough 4 — Sales Manager

> **Internal validation artifact — not approved for public release.** This document contains deployment identifiers, workspace references, synthetic borrower IDs, and implementation notes intended for engineering review.

> *In-character audit. I am "Sam," Sales Manager at Summit Mortgage. I run the LO bullpen — 8 loan officers most days, 5–6 after PTO. My job is shift-by-shift: morning call list, distribute leads, watch aging, coach the team, run the 8:30 standup with yesterday's numbers, run the Friday close-out with the week's conversion. Pat is strategy, Vera is credit, Maya is campaigns. I am the person who turns an "approved Refi+HELOC opportunity" into "Joe called Tuesday at 10:14am, scheduled callback for Thursday, application started Thursday at 11:03am, closed Wednesday next week."*

**Auditor:** Claude (Cowork) acting as Sam, Sales Manager
**App:** `https://mip-app-2543889327043640.aws.databricksapps.com`, deployment `01f14dbfb9e81300a5e969695671c6d2`
**Method:** Read-only Chrome screenshots, live `/api/*` calls with my workspace OAuth, direct UC SQL, code inspection. Synthetic borrower IDs only.

---

## Sam's actual journey

1. Open the app first thing → go straight to **Lead Queue**.
2. Filter to "approved by Vera, ready to call, untouched" — today's working list.
3. **Distribute** the list across the 5–6 LOs on shift this morning.
4. Drill into one borrower a new LO is asking about ("Hey Sam, B-X, is this real?") via Borrower 360.
5. Log a **call disposition** when an LO finishes a call: left voicemail, no answer, callback scheduled, application started, dead lead.
6. Check **aging** — anything approved >7 days but never worked, surface for triage.
7. Pull **yesterday's activity** for the 8:30 standup: calls made, contacts reached, callbacks scheduled, applications started, conversions.
8. Pull **per-LO conversion** for the Friday close-out: who's hitting quota, who needs coaching.
9. Ask Genie an LO-level question: "Which LO had the highest application-start rate this week?"

---

## What works well (Sam-perspective wins)

Honestly, very little. The handful of things I can use:

- **Lead Queue exists and is fast.** The 500-row paged view loads cleanly; the new contactability / consent / recency filters mean what's on screen is at least theoretically contactable.
- **Borrower 360 is committee-grade.** If an LO calls me with "is this real?" I can open the dossier and read evidence + why-now + trigger timeline in 30 seconds. That part of the LO-support workflow holds up.
- **Audit trail shows actor.** Every action carries `actor=skyler@entrada.ai` from the edge. If I want to manually grep "what did each user do," the data is there — just not aggregated for me.
- **Lifecycle mirror tracks state.** `mip.gold.borrower_lifecycle_state` carries `approval_status`, `outreach_status`, `approved_at`, `outreach_at`. The data is *there*; nothing in the UI shows it to me.

That's the win column. The lose column follows.

---

## Issues found, severity-tagged

### P0 — Things that mean my workflow is unsupported

#### P0-S1. No loan-officer assignment / ownership concept exists anywhere

I checked everywhere a lead has a "field" — borrower payload, lead summary, audit event, audit payload, lifecycle state. **None** of them carry `assigned_to`, `lo_id`, `queue_owner`, `assignee_email`, `team_id`, `region`, or any equivalent.

Verified live:
- `GET /api/borrowers/B-102FL7THC6Q3L` returns 40 keys; zero of them are assignment-related.
- `GET /api/audit/events?event_type=APPROVE&limit=1` → audit row carries `actor` (the person who acted) but nothing identifying the LO who *should work* the lead next.
- `GET /api/leads/{id}/assignment` → 404. `GET /api/sales/routing` → 404. `GET /api/sales/team` → 404.

Consequence: I cannot say "this lead belongs to Joe." I cannot round-robin. I cannot re-assign when an LO calls in sick. I cannot pull "Joe's queue today." If Vera approves 50 borrowers in the morning, I have no system surface to fan them out to my 6 LOs — I'd be exporting CSV, filtering by row number into 6 chunks, and emailing them to LOs by hand.

Fix: introduce an assignment object. Minimum viable shape:
- `mip_app.lead_assignments(borrower_id, assigned_to_email, assigned_at, assigned_by, expires_at)` keyed by `borrower_id` with a UNIQUE constraint on `(borrower_id) WHERE released_at IS NULL`
- `POST /api/leads/{id}/assign {assigned_to_email, expires_in_hours}` with audit-logged write
- `POST /api/leads/distribute {assigner_email, lo_emails: [...], strategy: 'round_robin' | 'score_balanced' | 'manual', cohort_filter: {...}}` returning per-LO assignment counts
- New `ASSIGNED TO` column in the Lead Queue table, sortable + filterable
- `?assigned_to=` query param on `/api/leads`

#### P0-S2. `/api/leads?approval_status=approved` is silently ignored

Verified: `GET /api/leads?approval_status=approved&limit=3` returns the same three borrowers as `GET /api/leads?approval_status=pending&limit=3`, all with `approval_status: "pending"`. The querystring is accepted (no 422) but never reaches the SQL `WHERE`.

Consequence: I cannot filter the Lead Queue to "Vera already approved these, they're ready for outreach." My daily call list is hidden inside a queue of mostly-pending borrowers that Vera hasn't touched. The Approve button on every row is misleading — for an already-approved borrower it should be a "Distribute to LO" or "Mark called" state transition, not "Approve again."

Fix: honor `approval_status` in `DatabricksLeadRepository.list(...)`; default it to `'approved'` for the Sales Manager view; add an `APPROVAL: Approved / Pending / Rejected / Any` filter chip on the Lead Queue.

#### P0-S3. `outreach_status` is not on the borrower payload — I can't see which approved leads are still waiting

The lifecycle table has `outreach_status` ∈ `none | queued | sent | bounced | replied`. Today only `none` and `queued` exist in the live data because real send hasn't been wired (and shouldn't be, per Module 0 scope — see Maya's M-9). But even the `queued` signal is **not surfaced** on `/api/borrowers/{id}` — verified, 40 keys, no `outreach_status`.

Consequence: a borrower Vera approved last Tuesday and Maya queued for outreach last Wednesday looks indistinguishable in my UI from a borrower Vera approved yesterday with nothing else done. I cannot tell the LO team "skip these, they're already in queue; start with these, they're fresh approvals."

Fix: add `outreach_status` and `outreach_at` from `mip.gold.borrower_lifecycle_state` to `_BORROWER_360_COLUMNS` and `_LEAD_POPULATION_COLUMNS`; render an `OUTREACH` column on the Lead Queue (Queued / Sent / Pending / —); add an `OUTREACH: Pending / Queued / Sent / Any` filter chip.

#### P0-S4. No call disposition logging — every LO call vanishes

I tried `POST /api/sales/disposition {borrower_id, outcome: 'left_voicemail'}` → HTTP 405. The route doesn't exist. There is no surface anywhere for an LO to log:

- `called_no_answer`
- `called_left_voicemail`
- `connected`
- `callback_scheduled` (with timestamp)
- `application_started`
- `not_interested` / `not_now` / `dead`

Today the audit log only captures `view_borrower_360 / view_leads_ranked / draft_outreach / outreach.approve / outreach.reject` — every one of those is something an *approver* does, not an *LO calling a phone*. Once Vera approves and Maya queues, the borrower disappears from the system's view. The actual sales work — the highest-signal activity in the entire funnel — is invisible.

Consequence: I cannot coach LOs. I cannot rank them. I cannot identify objections that come up repeatedly. I cannot tell the credit committee "these 47 leads were called and 23 connected." I am running the team on tribal knowledge.

Fix: add a `call_dispositions` Lakebase table + `POST /api/leads/{borrower_id}/disposition` endpoint that writes:
```
borrower_id, lo_email, attempt_number, outcome, occurred_at,
notes (free text, PII-scrubbed), callback_at (nullable), audit_event_id
```
Audit-log every write. Surface the latest disposition as a chip on the Lead Queue row + as a timeline section on Borrower 360.

### P1 — Friction that costs me daily

#### P1-S5. No per-borrower lifecycle endpoint

`GET /api/borrowers/B-102FL7THC6Q3L/lifecycle` → 404. To answer "where is B-X in our funnel right now," I have to either (a) read the lifecycle table directly in SQL (which a Sales Manager doesn't do) or (b) scroll the audit log for that entity_id. Neither is a Sam workflow.

Fix: add `GET /api/borrowers/{id}/lifecycle` returning `approval_status`, `outreach_status`, `approved_at`, `outreach_at`, `synced_at`, the latest disposition (after P0-S4 lands), and the latest assignment (after P0-S1 lands).

#### P1-S6. No aging surface — a 20-day-stale approved lead is invisible

Direct SQL: `mip.gold.borrower_lifecycle_state` shows one approved borrower from 2026-04-22 with `outreach_at = NULL`, i.e., **approved 20 days ago and still not in outreach**. That borrower is exactly what Sam should surface to the LO team in tomorrow's standup as "work this before it dies."

The data exists. **There is no UI surface for it.** No "aging leads" card, no Lead-Queue filter for `approved_at < now() - INTERVAL 7 DAYS AND outreach_at IS NULL`, no Genie sample question about staleness.

Fix: add a "Stale approved leads" panel to the Admin page (or a new Sales surface), powered by the lifecycle table; add an `AGED >7d` filter chip on Lead Queue; consider an automated daily Slack/email to the assigner.

#### P1-S7. `/api/audit/rollups?groupBy=actor` is accepted but ignored

I tried `GET /api/audit/rollups?period=week&groupBy=actor` — response is identical to `GET /api/audit/rollups?period=week`. The endpoint returns rows of `(bucket_start, event_type, event_count)` with no actor / no entity_id / no segment / no offer breakdown. Same for `groupBy=action`.

Consequence: I cannot get per-LO activity counts from the audit-rollups endpoint. The data is in the audit table (every row has `actor`), but the rollup endpoint doesn't expose it.

Fix: honor `groupBy` ∈ `actor / action / event_type / segment / offer_code / state` (multi-select); cap cardinality server-side to prevent huge fan-out.

#### P1-S8. No `/api/sales/*` surface at all — `/standup`, `/team`, `/routing` all 404

This is restating P0-S1 + P0-S4 but worth severing: there is no namespace at all for sales-team-shaped queries. The Module 0 spec names "Sales Manager" as one of four personas, but the API surface has zero routes serving that persona's job-to-be-done.

Fix: introduce a `backend/api/sales.py` router with at minimum:
- `GET /api/sales/team` — list of LOs the current user manages
- `GET /api/sales/standup?date=YYYY-MM-DD` — yesterday's activity summary
- `GET /api/sales/conversion?from=…&to=…&groupBy=lo|cohort` — funnel rollups
- `POST /api/sales/distribute` — round-robin / score-balanced distribution
- `GET /api/sales/aging?older_than_days=N` — stale-lead surfacing
- `POST /api/leads/{id}/assign` / `POST /api/leads/{id}/disposition` (per P0-S1, P0-S4)

#### P1-S9. Genie sample questions cover no Sales-Manager workflow

`genie/sample_questions.md` has 25 prompts across 7 categories. None of them ask:
- "Which LO has the highest application-start rate this week?"
- "Show me approved leads that haven't been touched in 7 days."
- "How many calls did each LO make yesterday?"
- "Top 10 borrowers in Joe's queue ranked by aging."
- "How many leads went from approved to closed last month?"

Consequence: even if a Sales Manager opens Ask Genie, the suggested prompts steer them back to Pat's / Vera's / Maya's questions. There is no surface that says "this product is for you too."

Fix: add a "Sales Manager" section to `genie/sample_questions.md` with 4–6 LO-funnel prompts; backfill the corresponding trusted SQL paths in `databricks_repo._adapt_genie_response` once P0-S1 / P0-S4 land.

### P2 — Quality of life for daily operations

#### P2-S10. No "today's call list" view

Sam wants a saved view shaped as: `approved + assigned_to=current_user + outreach_status IN ('none','queued') + sorted by aging desc, then opportunity_score desc, then equity_estimate desc`. Today there's no saved-view concept on Lead Queue. The deep-link URL works but isn't named or pinned.

#### P2-S11. No conversion-funnel rollup per LO

For the Friday close-out, I need: LO → calls attempted → connected → callbacks → applications started → submitted → cleared → closed → funded. None of those columns exist past `outreach_status`. Even the audit-derived "approvals per actor" view I'd hack together gives only one funnel step.

#### P2-S12. No capacity / AHT awareness

I have no place to encode "Joe can work 35 leads/day" or "average handle time per call is 12 min." Without this, the distribution math (P0-S1 fix) can't even be score-balanced — it'd be naive round-robin.

#### P2-S13. No routing strategy at all

Distribution today is "Sam emails CSVs." There's no:
- Round-robin (`borrower_id % len(LOs)`)
- Score-balanced (`assign top-N to highest-converting LO`)
- Geography-aligned (`assign WA leads to WA-licensed LOs only`)
- Skill-based (`refi to refi specialists, HELOC to HELOC specialists`)

Each of these is a real Summit workflow that the product can't express.

#### P2-S14. No commission / quota tracking

LO commission structure is the *most operationally-loaded data* in any sales team. The product doesn't track it. To be clear: it shouldn't directly *pay* commissions (that's payroll), but it should *expose to the manager* what each LO has earned this period so they can see who needs coaching vs. who's about to hit accelerators.

#### P2-S15. No coaching surfaces

Call recording links, objection-handling notes, "this LO is on a PIP" badges, script libraries — none of it. Out of scope for Module 0 per the data inputs list, but the gap is real.

#### P2-S16. Audit `actor` is an email — no team / region / role mapping

The audit log records `actor=skyler@entrada.ai` — fine for traceability, useless for rollups. To group "actors managed by Sam" vs. "actors managed by Tasha" the system would need an org structure (`mip_app.users(email, role, manager_email, region)`) which doesn't exist.

---

## What I would actually tell my LO team after this morning

> "Don't use the app for queue management today. I'll keep doing it in the shared Google Sheet. Use the app only when you want to (a) look up a specific borrower's dossier when a customer asks 'why are you calling me' — Borrower 360 is genuinely useful for that — and (b) read Vera's approval rationale on a borderline case. Everything else — your daily call list, your callbacks, your dispositions, your conversion stats — keep using the sheet and Total Expert. I'm flagging this to Pat and asking for a Sales-Manager surface in the next release. As of right now there isn't one."

The product was built for three of its four named personas. Sam is the gap.

---

## Why this matters for Module 0 ship-readiness

Two ways to read this:

1. **Module 0 is scoped as "lead generation & borrower segmentation," not "LO work management."** The Sales Manager persona was named in the spec but the actual job-to-be-done falls outside the named capabilities ("lead portfolio builder ... in-the-money refinance detection ... related-property opportunity detection ... investor segmentation ... home equity propensity ... lead scoring + next-best-offer ... drill-down to borrower stories"). Under this reading, Sam's gaps are correctly out of scope; Module 0 ships and Module 1 adds work-management.

2. **A product named for "Sales Manager" that does none of the Sales Manager's job is mis-targeted.** Under this reading, the persona list should be edited down to three (Head of Growth / VP Lending / Marketing Leader) or the product should add a thin "approved-queue + assignment + disposition" surface before claiming the fourth persona.

I lean toward (1) being the correct scoping decision — the data foundation (lifecycle mirror, audit trail, lead population) is exactly right for a future Module 1 to layer work-management on top of — but I lean toward also editing the persona list in the spec so the product doesn't promise more than it delivers. Saying "Sales Manager" is a target user when zero of their workflows are supported is a credibility issue with mortgage-industry buyers who recognize the gap immediately.

---

## Suggested fix order (if Sales Manager is a target persona to retain)

1. **P0-S1 + P0-S2 + P0-S3 ship together.** Approved-only filter + assignment column + outreach_status column. The data exists; this is plumbing.
2. **P0-S4 ships next.** Call-disposition table + endpoint + audit. Largest single win for daily LO work.
3. **P1-S6** (aging surface) and **P1-S7** (groupBy=actor on rollups). Both unlock immediate per-LO and stale-lead views from data already in the system.
4. **P1-S8** (the `/api/sales/*` namespace). Build incrementally — standup / aging / conversion / distribute.
5. **P1-S9** (Genie sample questions for Sales). Cheap to add; turns the existing Genie surface into something Sam can use.
6. P2 items are quarter-2+ features.

---

## Sources

- Live `/api/leads`, `/api/borrowers/{id}`, `/api/audit/events`, `/api/audit/rollups`, `/api/sales/*` (all 404), `/api/leads/{id}/assignment` (404), `/api/leads/{id}/disposition` (405) against deployment `01f14dbfb9e81300a5e969695671c6d2`
- Direct SQL on `mip.gold.borrower_lifecycle_state`, `mip.gold.borrower_360`
- `genie/sample_questions.md` (no Sales-Manager-shaped prompts)
- Module 0 spec persona line: "Head of Growth / VP of Mortgage Lending / Marketing Leader / Sales Manager"
- Code refs cited inline

---

## Independent re-validation (Claude, 2026-05-12 19:42 UTC, deployment `01f14e37f0531430b5c48f9014be417c`)

After the engineering pass, I re-exercised every S-finding against the new deployment (`SUCCEEDED / RUNNING / ACTIVE`, update_time 2026-05-12T19:27:09Z; 9 walkthrough screenshots present at `/tmp/mip-sales-walkthrough-20260512-final2/`). The product went from "Sam's persona unsupported" to "Sam's persona has a complete operational surface" in one cycle.

| Finding | Engineering claim | Independent live re-validation |
|---|---|---|
| P0-S1 No LO assignment | Real assignment + distribution endpoints + Lead Queue column | ✅ Verified. `POST /api/leads/B-1AT5CXZZ1NI2N/assign {assigned_to_email: 'lo02@summit.example'}` returns 200 with full assignment object: `assignment_id, borrower_id, assigned_to_email, assigned_to_label='Summit LO 02', assigned_by='skyler@entrada.ai', assigned_at, expires_at (24 hours later), released_at=null, strategy='manual'` + `audit_event_id`. PII guard: external `alice@example.com` → 422 "staff email must be an approved internal demo/workspace email." Lead Queue table renders the new `ASSIGNED TO` column ("Summit LO 01 / May 12, 2:24 PM"). Minor: `GET /api/leads/{id}/assignment` returns 404 even when the borrower has an active assignment — the read path runs through `/api/borrowers/{id}/lifecycle.assignment` instead. Worth a tiny GET-alias fix but functionally not blocking. |
| P0-S2 `approval_status` filter ignored | Honored in repo | ✅ Verified. `/api/leads?approval_status=approved&limit=2` now returns only `approval_status='approved'` rows (B-102FL7THC6Q3L, B-1AT5CXZZ1NI2N), both with `assigned_to_email` populated. `/api/leads?approval_status=pending&limit=2` returns only pending rows (B-0KMY6IXUDKX9X, B-17WANAO1W6ZLU). Lead Queue filter row now shows an `APPROVAL: Approved` chip + the top-right active-filter pill reads `approval = approved` when the URL drives it. |
| P0-S3 `outreach_status` missing from borrower payload | Surfaced on borrower + lead summary | ✅ Verified. Borrower payload grew from 40 keys → **52 keys**. New: `approval_status, outreach_status, approved_at, outreach_at, assigned_to_email, latest_disposition, aging_days`. Lead Queue table renders a new `OUTREACH` column with `Queued / Sent / Pending / —` pills. |
| P0-S4 No call disposition logging | `POST /api/leads/{id}/disposition` + governed enum + PII scrubbing | ✅ Verified. `POST /api/leads/B-102FL7THC6Q3L/disposition {outcome:'connected', lo_email:'lo01@summit.example', notes:'reached borrower at alice@example.com'}` → 200 with full disposition object: `disposition_id, borrower_id, lo_email, outcome, attempt_number=3 (auto-incremented from prior rows), occurred_at, callback_at, notes` — and **notes were scrubbed to `"reached borrower at [EMAIL-REDACTED]"`**. Invalid outcome → 422 with the exact enum (`called_no_answer / called_left_voicemail / connected / callback_scheduled / application_started / not_interested / not_now / dead`). Nonexistent borrower → 404. |
| P1-S5 No per-borrower lifecycle | `GET /api/borrowers/{id}/lifecycle` | ✅ Verified. Returns `approval_status, outreach_status, approved_at, outreach_at, synced_at` plus an `assignment` block (assignment_id, assigned_to_email/label, assigned_at, expires_at, released_at, strategy). |
| P1-S6 No aging surface | Stale-lead surfaces in UI + `/api/sales/aging` endpoint + Genie aging query | ✅ Verified. The Lead Queue page now renders a "Sales ops snapshot" panel with **STALE APPROVED** count + an "Open stale queue" CTA. `/api/sales/aging?older_than_days=30` returns 200 (empty list = no aged leads at >30d). Sales Ops Genie "Which approved borrowers are aging out and have not been worked yet?" returns the exact 20-day-stale B-0STSZHO4O5J04 I flagged in the original audit, sourced from `mip_app.approvals + lead_assignments + call_dispositions` joins. |
| P1-S7 Audit rollups `groupBy` ignored | Now honored | ✅ Verified. `GET /api/audit/rollups?period=week&groupBy=actor` returns rows shaped `{bucket_start, event_type, group_by:'actor', group_key:'skyler@entrada.ai', event_count: 65}` — `group_by` and `group_key` are now first-class output fields. |
| P1-S8 No `/api/sales/*` namespace | Full sales router | ✅ Verified. `GET /api/sales/team` returns the 6-LO + 1-manager roster with `role / region / manager_email / capacity_per_day / active` (Summit LO 01 = IL, capacity 35; LO 02 = CA; etc.). `GET /api/sales/standup` returns `{date, calls_logged, contacts_reached, callbacks_scheduled, applications_started, dead_leads, by_lo}`; after I logged one disposition, `standup?date=2026-05-12` returned `{calls_logged: 4, contacts_reached: 4, callbacks_scheduled: 3, ..., by_lo: [{lo_email: 'lo01@summit.example', outcomes: {callback_scheduled: 3, connected: 1}, calls_logged: 4}]}`. `GET /api/sales/conversion?from=...&to=...` returns `{from_date, to_date, group_by, rows: [{group_key, calls_attempted, contacts_reached, callbacks_scheduled, applications_started, application_start_rate}]}` — verified live: lo01 = 4 calls / 4 contacts / 3 callbacks / 0 apps. `GET /api/sales/aging` works as above. |
| P1-S9 No Genie sample questions | Sales Ops routing in Genie | ✅ Verified. Asking the canonical aging question produces `source: "sales_ops"`, generated SQL joining `mip_app.approvals + call_dispositions + lead_assignments`, returns the correct borrower (B-0STSZHO4O5J04), and **no `B-TEST*` / `B-FUZZ*` / `B-AUDIT*` IDs leak through** (the reviewer-blocker overfetch+filter is working). |

**P2 polish items:** the Lead Queue's new "Sales ops snapshot" panel covers what I'd asked for in P2-S10 (today's call list shape), P2-S11 (LO funnel rollup), and P2-S12 (capacity awareness — "6 active LOs, 190 daily capacity"). P2-S13 (routing strategy) is wired via the `strategy` field on assignments (today `manual`; `round_robin`/`score_balanced` would land in distribute). P2-S14 (commission/quota) and P2-S15 (coaching surfaces) remain correctly outside Module 0 scope.

**No-regression sweep against this deployment — all green:**
- HoG P1-G3 unknown POST → **422**; P1-G5 PII portfolio name → **422**; P2-G5 ZIP legacy → **422**, canonical → **200**.
- VP P0-V2 legacy admin PUT → **410**; P0-V1 reject without `rationale_code` → **422**, with PII-shaped `request_id` → **422**.
- Maya P0-M2 draft for suppressed borrower → **409**; P1-M5 `variant_name='Jane Smith'` → **422**.
- Sales Ops Genie correctly source-tagged `sales_ops`, no orphan leakage.

**Contract surprises worth noting (not blockers, just delta-from-my-test-shapes):**
- Disposition POST does **not** accept `attempt_number` from the caller — it auto-increments from prior rows for the same borrower. Cleaner contract than I assumed.
- Disposition outcome enum is **strict**: `called_left_voicemail` not `left_voicemail`, `not_interested / not_now / dead` etc. Server validates server-side.
- Notes field is **PII-scrubbed** before audit (`alice@example.com` → `[EMAIL-REDACTED]`).
- Assignment GET via `/api/leads/{id}/assignment` returns 404; the canonical read is via `/api/borrowers/{id}/lifecycle.assignment`. Worth aliasing for symmetry but the data is reachable.

**Net verdict for Sam's walkthrough:** the product went from "least-served persona" to "fourth persona has a complete operational surface" in one engineering cycle. Sam can now: filter to approved + queued + assigned leads; see who owns what; assign to LOs (with capacity-aware roster, governed staff emails, PII guards); log call dispositions with auto-incremented attempts and PII-scrubbed notes; pull standup + conversion + aging rollups via API; ask Genie aging questions that route through trusted sales-ops SQL without leaking orphan Lakebase rows; and see all of it in the Lead Queue Sales-ops snapshot panel. The "either edit the persona list or build the surface" decision from my original audit was answered correctly: the surface was built, and built well.

---

## Remediation addendum — 2026-05-12

**Determination:** all P0/P1 claims were valid. The correct Module 0 decision is
to keep Sales Manager as a named persona and ship a thin, governed sales-ops
slice rather than deleting the persona or pretending LO work management is out
of scope.

| Finding | Status | Remediation |
|---|---|---|
| P0-S1 assignment / ownership missing | ✅ Fixed | Added `mip_app.sales_team`, `mip_app.lead_assignments`, `POST /api/leads/{id}/assign`, `POST /api/sales/distribute`, Lead Queue assignment filter/column/actions, and CSV assignment fields. |
| P0-S2 `approval_status` ignored | ✅ Fixed | `/api/leads` and Databricks lead count/list SQL now honor `approval_status`; Lead Queue has an Approval filter. Invalid values 422. |
| P0-S3 `outreach_status` missing | ✅ Fixed | Lead and borrower payloads project `outreach_status/outreach_at/approved_at`; Lead Queue and Borrower 360 render status chips; Lead Queue has Outreach filter. |
| P0-S4 no call disposition logging | ✅ Fixed | Added `mip_app.call_dispositions`, `POST /api/leads/{id}/disposition`, audit event `CALL_DISPOSITION`, callback validation, PII-scrubbed notes, Lead Queue disposition panel, and Borrower 360 lifecycle fields. |
| P1-S5 no lifecycle endpoint | ✅ Fixed | Added `GET /api/borrowers/{id}/lifecycle` with approval, outreach, assignment, and latest disposition state. |
| P1-S6 no aging surface | ✅ Fixed | Added `GET /api/sales/aging`, Lead Queue Aged filter, and Sales Ops snapshot stale-approved card with deep link. |
| P1-S7 audit rollups groupBy ignored | ✅ Fixed | `/api/audit/rollups` now validates and honors `groupBy=event_type|actor|action`. |
| P1-S8 no `/api/sales/*` namespace | ✅ Fixed | Added `/api/sales/team`, `/api/sales/distribute`, `/api/sales/aging`, `/api/sales/standup`, and `/api/sales/conversion`. |
| P1-S9 no Sales Manager Genie questions | ✅ Fixed | Added Sales operations prompts and a backend Sales Ops Genie adapter for LO conversion/standup/aging prompts that correctly routes Lakebase operational state outside the Databricks Genie UC space. |
| P2-S10 today’s call list | ✅ Mostly fixed | Deep-linkable filter combination now exists: `approval_status=approved&outreach_status=none|queued&assigned_to=<lo>&aged_days=N`; named saved views remain Module 1. |
| P2-S11 per-LO conversion rollup | ✅ Fixed for Module 0 | `/api/sales/conversion` and the Lead Queue Sales Ops snapshot show WTD per-LO application-start rate. Later funnel steps beyond application-start remain Module 1+. |
| P2-S12 capacity awareness | ✅ Thin fix | `mip_app.sales_team.capacity_per_day` is seeded and surfaced as daily capacity. Average handle time remains Module 1. |
| P2-S13 routing strategy | ✅ Thin fix | Round-robin distribution and a score-balanced strategy contract are available; advanced licensing/geography/skill routing remains Module 1. |
| P2-S14 commission / quota tracking | ⏭️ Deferred | Out of Module 0 data contract; should not be faked. |
| P2-S15 coaching surfaces | ⏭️ Deferred | Out of Module 0 data contract; should not be faked. |
| P2-S16 actor role mapping | ✅ Thin fix | `mip_app.sales_team` adds role, manager, region, capacity for LO/team rollups; audit actor remains the authenticated email for traceability. |

**Validation so far:**

- `python3 -m py_compile backend/api/genie.py backend/api/sales.py backend/services/sales_state.py`
- `.venv/bin/python -m pytest -q tests/unit/test_sales_manager_api.py tests/unit/test_api_routes.py tests/unit/test_audit_store_contract.py tests/unit/test_api_boundaries.py tests/unit/test_public_api_schema_guards.py tests/unit/test_outreach_reject.py tests/unit/test_marketing_safety.py tests/unit/test_genie_retention_risk.py tests/unit/test_genie_actions_api.py tests/unit/test_genie_cohort_lead_queue.py`
- `npm --prefix frontend run test`
- `npm --prefix frontend run build`
- `databricks bundle validate -t dev`
