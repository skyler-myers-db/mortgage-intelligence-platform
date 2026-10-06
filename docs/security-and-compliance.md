# Security and Compliance

Module 0 is designed to fail closed and remain public-demo safe.

## Public Demo Masking

The application always emits masked output on app, CSV, and audit surfaces.
Raw CLIP and Owner Link values remain in governed Unity Catalog tables for
authorized joins and debugging, but there is no runtime flag that exposes them
through the product. Masking covers raw CLIP, Owner Link, addresses, names,
phones, emails, and competitor servicer names.

## Governed State Changes

State-changing workflows write through Lakebase-backed APIs and audit tables.
Approvals, rejections, saved leads, saved drafts, and Genie-materialized cohorts
must carry actor, action, entity, payload, timestamp, and request identifiers.
If Lakebase is unavailable, the API must return an error and leave the UI in a
pending or failed state; it must not claim success.

### Lead Queue filter reads

- `GET /api/leads` still writes exactly one `VIEW_LEADS` row per served read.
  The row now also records the public score and spread bounds: the effective
  `min_opportunity_score` / `min_rate_spread_bps` floors (a Genie cohort's or
  the URL's) and, when set, `max_opportunity_score` / `max_rate_spread_bps`.
  A bound beside a Genie cohort or a Growth Agent handoff is refused (422).
- `GET /api/leads/count`, `GET /api/leads/facets` and
  `GET /api/workspace/saved-views` are audit-free: they run the same
  authorization as the ranked list (admin gate, assignee visibility, cohort
  replay) but return totals or the actor's own views only, never resolve the
  audit store, and write no row. The Lead Queue reads facet counts only when a
  person opens a filter menu, and the saved views only when the panel opens.
  Count and facets refuse `borrower_ids` (422): an aggregate over named
  borrowers would read their attributes with no `VIEW_LEADS` row, so a read of
  named borrowers is the audited list only, and the Lead Queue shows a borrower
  list's menus without counts.
- Saving and deleting a view write `SAVE_QUEUE_VIEW` / `DELETE_QUEUE_VIEW` in
  the same Lakebase statement as the change (deletes are soft). The audit
  metadata carries the view id, the SHA-256 of its canonical params and the
  request id, never the view name or the params. The name passes the same
  public-text policy as a campaign name and a refusal never echoes it.

## Source Truth

Numbers shown to reviewers must trace to Unity Catalog tables, functions, or
metric views. MLS/listing activity is connected through Unity Catalog and must
remain evidence-backed. Pending Cotality feeds, currently filed Building
Permits, remain visible as data gaps until the corresponding Delta Shares are
connected and refreshed.

## Genie Controls

Genie answers are allowed to drive app actions only when the action payload is
derived from trusted answer rows or source filters and the user confirms the
action. The destination route must preserve those filters, and the action must
be audited.

## Roles and access

Four tiers are decided server-side by `backend/services/rbac.py` and
mirrored to the UI by `/api/v1/session` (`can_access_admin`, `can_approve`,
`can_read_audit`, from the same decisions the routes enforce); the fifth
column, the sales team, is the Lakebase roster described below. The three
privileged tiers are exact identities in their allowlists (`MIP_ADMIN_*`,
`MIP_APPROVER_*`, `MIP_AUDITOR_*`). Empty lists admit nobody, and a deployed
`X-Forwarded-Groups` header grants nothing (group names are a local/test
compatibility path only). An auditor is also a workspace user: they may do
what any workspace user may, including creating approval requests, but never
approve, reject or revoke.

The sales team is the Lakebase roster (`mip_app.sales_team`), and its roster
role and manager scope are what authorize the sales-state writes; no
`rbac.py` tier grants them. An identity that is not an active roster member
is refused lead assign, disposition and outcome whatever its tier
(`backend/services/sales_state_core.py`):

- **Assign** (single and distribute): a roster `sales_manager` for their own
  loan officers, or a roster `admin` for any loan officer
  (`require_manager_actor`, `require_assignee_in_scope`).
- **Disposition**: the loan officer themself, their roster `sales_manager`,
  or a roster `admin` (`require_disposition_scope`), and only against an
  active assignment to that loan officer
  (`sales_state_writes.log_disposition`).
- **Outcome**: a roster `sales_manager` against an active assignment in
  their scope, or a roster `admin` (`require_manager_actor`,
  `require_outcome_scope`).

A roster `admin` is a sales-roster role, not the `MIP_ADMIN_*` Administrator
tier; neither implies the other.

| Surface | Workspace user | Sales team | Approver | Auditor | Administrator |
| --- | --- | --- | --- | --- | --- |
| Ranked leads, Borrower 360, Offer reads | yes | yes | yes | yes | yes |
| Approve / reject outreach | no | no | yes | no | yes |
| Bulk approve / reject | no | no | yes | no | yes |
| Request approval (`POST /outreach/approval-requests`) | yes | yes | no (409: approvers decide directly) | yes | no (an administrator is an approver: 409) |
| Approval requests list (`GET /outreach/approval-requests`) | own (`scope=mine`) | own (`scope=mine`) | the open queue and own | own (`scope=mine`) | the open queue and own |
| Withdraw an approval request (`POST /outreach/approval-requests/{id}/withdraw`) | own requests only | own requests only | own requests only | own requests only | own requests only |
| Revoke an approval (`POST /outreach/revoke`) | no | no | yes | no | yes |
| Lead assign, disposition, outcome | only if also on the sales roster | by roster role and scope: assign and outcome a `sales_manager` (own loan officers) or roster `admin`; disposition the loan officer, their manager or a roster `admin` | only if also on the sales roster | only if also on the sales roster | only if also on the sales roster |
| Own activity (`/audit/my-events`) and own decision receipts | yes | yes | yes | yes | yes |
| Another actor's decision receipt | no | no | no | yes | yes |
| Borrower decision history (GET /borrowers/{id}/decisions) | no | yes (active roster member) | yes | yes | yes |
| Full audit ledger (`/audit/events`, `/events/page`, `/rollups`, `/facets`, `/count`) | no | no | no | yes | yes |
| Ledger CSV receipt (`POST /audit/export-receipt`) | no | no | no | no | yes |
| Refusal reports list and question (from W5c) | no | no | no | yes | yes |
| `POST /audit/event` | no | no | no | no | yes |
| `/admin/*` (rules 410, operations run, force-degraded, settings, asset metadata) | no | no | no | no | yes |
| Lead Queue marketing override and `include_suppressed_for_analytics` | no | no | no | no | yes |

**Segregation of duties (D-audit-reads-c3).** The Auditor role exists so that
the people who read the ledger are not the people whose actions it records.
An administrator is an approver by construction (`rbac._approver_access`
admits every admin), can write `POST /audit/event`, can start data jobs and
force degraded mode (`POST /admin/operations/run`, `POST
/admin/force-degraded`) and can widen the Lead Queue past marketing
suppression; an approver makes the decisions the ledger holds. `PUT
/admin/rules` answers 410 for everyone, so it is not part of this basis. An
identity configured as auditor AND administrator or approver is therefore
flagged, never refused: `./scripts/deploy.sh` step 0 prints the auditor count
and a warning with the overlap count (never an identity), and the admin
health body (`GET /api/v1/admin/health`) carries `auditor_role_overlap`.

## Read-audit semantics by surface

Which read writes which audit event, and how the write behaves when Lakebase
is unavailable. "Background, fail-open" means the row is written after the
response by a FastAPI background task and a failure is logged as
`audit.dropped` without failing the read; "fail-closed" means the endpoint
returns 503 when the audit write fails. This table lists only what is on
main; each later item (D-audit-reads-a, c1, c2, c3, d) edits its own row in
the pull request that ships the behaviour, never earlier.

| Surface | Event | When | Write mode |
| --- | --- | --- | --- |
| Lead Queue list (`leads.list_leads`) | `VIEW_LEADS` | once per served `GET /leads`; carries `approval_request_batch_id` when the list is scoped to an approval request (a request with no open borrower answers an empty list and writes none) | background, fail-open (`audit.dropped`) |
| Borrower 360 open (`borrowers.get_borrower`) | `VIEW_BORROWER` | once per served `GET /borrowers/{id}` | background, fail-open |
| Proof drawer (`borrowers.get_borrower_proof`) | `VIEW_BORROWER_PROOF` | once per served `GET /borrowers/{id}/proof` | background, fail-open |
| Offer open (the approval surface) | `VIEW_BORROWER` | its own `GET /borrowers/{id}` (the Offer never reuses the Borrower 360 cache entry) | background, fail-open |
| Offer open (the approval surface) | `RECOMMEND_OFFER` | once per `POST /offers/recommend` (`offers.recommend_offer`): the approval-surface open record, carrying offer code, confidence, thresholds, `decision_inputs`, source freshness, evidence ids and subject CLIP | synchronous, fail-closed (503) |
| Offer open (the approval surface) | `DRAFT_OUTREACH` | once per `POST /outreach/draft` (`outreach.draft_outreach`) | committed in the same transaction as the draft row, fail-closed |
| Approve / Reject (`outreach.approve_outreach` / `outreach.reject_outreach`) | `APPROVE` / `OUTREACH_REJECT` | once per decision | committed with the approvals row, fail-closed; carries `decision_inputs` and the draft proof, and `approval_request_batch_id` when the decision answers an approval request |
| Revoke (`outreach_revoke.revoke_outreach`) | `OUTREACH_REVOKE` | once per revoke | committed with the revoke's approvals row, fail-closed; the approve row is never changed |
| Queue-version poll (`workspace.read_queue_version`) | none | every poll | audit-free |
| Console "My recent activity" (`audit.list_my_events`) | none | every read | audit-free |
| Own decision receipts (`audit_receipt.read_decision_receipt`) | none | every read | audit-free |
| Borrower decision history (`borrower_decisions.list_borrower_decisions`) | none | on a Borrower 360 or Offer mount; never polled or prefetched | audit-free |
| Audit ledger (`audit.list_events`, `list_event_page`, `audit_rollups`, `audit_facets`, `count_events`) | `VIEW_AUDIT_LEDGER` (`ledger_surface` events / events_page / rollups / facets / count) | once per served read by an admin or auditor | background, fail-open (`audit.dropped`) |
| Another actor's decision receipt (`audit_receipt.read_decision_receipt`) | `VIEW_AUDIT_LEDGER` (`receipt`, `read_audit_event_id`) | once per served cross-actor read | background, fail-open |
| Lead Queue filter counts (`leads.count_leads`, `leads.lead_facets`) | none | on an explicit menu open or omnibox count; never with `borrower_ids` or `approval_request_batch` (422) | audit-free |
| Saved queue views list (`GET /workspace/saved-views`) | none | when the Saved views panel opens | audit-free (the actor's own views) |
| Saved queue view save / delete (`/workspace/saved-views`) | `SAVE_QUEUE_VIEW` / `DELETE_QUEUE_VIEW` | once per save or soft delete | same Lakebase statement as the change, fail-closed |
| Approval request list (`approval_requests.list_outreach_approval_requests`) | none | when a request panel opens | audit-free (Lakebase workflow state, no borrower attribute) |
| Approval request create / withdraw (`/outreach/approval-requests`) | `APPROVAL_REQUESTED` / `APPROVAL_REQUEST_REFUSED` / `APPROVAL_REQUEST_WITHDRAWN` | every create attempt that reaches classification (a zero-eligible one writes `APPROVAL_REQUEST_REFUSED` with each id's reason and answers counts only); a withdraw that closed at least one borrower | same Lakebase transaction as the batch (or its own, for a refusal), fail-closed |
| Triage deck (`/lead-queue?mode=triage`) | none on entry, card show, J / K / Skip, Back or Esc (the cards are the loaded rows); `DRAFT_OUTREACH` only on A; `APPROVE` with `review_mode` `triage` per Confirm; `OUTREACH_REJECT` per card rejected | per explicit action | as the draft / approve / reject rows above |
| Lead Queue CSV export (`leads_export.create_lead_export_receipt`) | `LEAD_EXPORT` | once per download, before it starts; carries `exported_row_count` and, when the client knew it, `matching_row_count` (how many borrowers matched: a loaded-rows export states it is partial; a count below the file's row count is refused with 422 and nothing written) | synchronous, fail-closed (no download without the row) |
| Home Delta Explainer (`home.home_summary_attribution`) | none | only while an evidence drawer for a supported "since your last login" measure is open on Overview; never on hover, prefetch or poll | audit-free (gold and ref aggregates) |
| Home watchlist briefings (`growth_agent_compose_routes.growth_agent_watchlist_summary`) | none | once per Home load (the card never POSTs, so it starts no run) | audit-free |
| Home WHY NOW rate move (`analytics_rate_window.rate_window`) | none | on Home only when the summary is a delta with a previous visit (and on Analytics as before) | audit-free |
| Genie completion-job status poll (`genie.genie_message_status`) | none | every ~1.5 s poll of the caller's own job; while a deep job runs it may carry verified sections as Partial research | audit-free; `genie.run_query` RUN_GENIE stays at the job's single `recorded_at` commit point and no action token is issued for a revealed section |
| Genie verified section revealed (the job's sections writer, `genie_completion_sections`) | `GENIE_SECTION_REVEALED` | once per section, before it can be served (ruling R1): job and turn ids, plan index, row count, SQL hash, verification verdict; never question text or a row value | same Lakebase transaction as the `sections_json` write, fail-closed: a failed audit write withholds the section until the final answer; a turn that later fails, stops or expires keeps the rows |
| Admin SSE ingress probe (`admin_sse_probe.sse_probe`, `admin_sse_probe.sse_probe_outcome`) | none | admin-only transport diagnostic (delivery-04) | audit-free; reads no UC or Lakebase data |

A `VIEW_AUDIT_LEDGER` row (`backend/services/audit_ledger_reads.py`) carries
only the closed `ledger_surface`, `has_cursor`, `returned_row_count`, the
SHA-256 `filter_fingerprint` and, for a receipt, `read_audit_event_id`: never
ledger row contents, an actor filter in clear, or an email. Opening
Administration reads nothing from the ledger (its old "last event" probe
went with the explorer, which now lives on `/audit-ledger`), and no ledger
read is polled, prefetched or refetched on window focus. `tools/verify_live.py`'s
ledger probes now write attributable `VIEW_AUDIT_LEDGER` rows; that is
expected.

Residuals (recorded 2026-10-01, owners in the 2026-09-21 UI/UX audit report,
12.3). The `filter_fingerprint` is an unkeyed SHA-256 of the filter set
(`audit_pagination.audit_filter_fingerprint`, also the cursor binding), so an
actor-only filter can be recovered by hashing candidate emails. Only ledger
readers can see these rows, and they already see actor emails, so the added
exposure is small. Keying the stored copy with an HMAC (the cursor already
derives one from the action secret) goes to W5c `w5-lead-queue-paging`
before `VIEW_LEADS` adds a fingerprint of the same shape. The explorer's
'Page CSV' downloads the loaded page without `POST /audit/export-receipt`, so
no `AUDIT_EXPORT` row precedes it (the read that loaded that page wrote its
own `VIEW_AUDIT_LEDGER` row); W5d `w5-print-glossary-sales-manager` wires the receipt and
settles whether auditors may export.

**Ruling (wave 5, D-audit-reads-b, audit delivery-08):** the Offer Orchestrator
keeps its own audited reads and no `VIEW_OFFER` event exists. `RECOMMEND_OFFER`
is the approval-surface open record: it is written only by
`backend/api/offers.py`, synchronously and fail-closed, so the ledger records
from the server's own read what the approver saw. Sharing the Borrower 360
cache would make that a client claim the server cannot verify and give one
user action two ledger shapes. Pins: `tests/unit/test_audit_event_label_parity.py`
(no `VIEW_OFFER` in the server-owned set or the explorer labels),
`tests/unit/test_offers_router.py` (a single `RECOMMEND_OFFER` emitter) and
`frontend/src/routes/offer-orchestrator.recommendCaller.test.ts` (a single
`recommendOffer` caller).
