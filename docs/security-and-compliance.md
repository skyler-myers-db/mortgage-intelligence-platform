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
| Lead Queue list (`leads.list_leads`) | `VIEW_LEADS` | once per served `GET /leads` | background, fail-open (`audit.dropped`) |
| Borrower 360 open (`borrowers.get_borrower`) | `VIEW_BORROWER` | once per served `GET /borrowers/{id}` | background, fail-open |
| Proof drawer (`borrowers.get_borrower_proof`) | `VIEW_BORROWER_PROOF` | once per served `GET /borrowers/{id}/proof` | background, fail-open |
| Offer open (the approval surface) | `VIEW_BORROWER` | its own `GET /borrowers/{id}` (the Offer never reuses the Borrower 360 cache entry) | background, fail-open |
| Offer open (the approval surface) | `RECOMMEND_OFFER` | once per `POST /offers/recommend` (`offers.recommend_offer`): the approval-surface open record, carrying offer code, confidence, thresholds, `decision_inputs`, source freshness, evidence ids and subject CLIP | synchronous, fail-closed (503) |
| Offer open (the approval surface) | `DRAFT_OUTREACH` | once per `POST /outreach/draft` (`outreach.draft_outreach`) | committed in the same transaction as the draft row, fail-closed |
| Approve / Reject (`outreach.approve_outreach` / `outreach.reject_outreach`) | `APPROVE` / `OUTREACH_REJECT` | once per decision | committed with the approvals row, fail-closed; carries `decision_inputs` and the draft proof |
| Queue-version poll (`workspace.read_queue_version`) | none | every poll | audit-free |
| Console "My recent activity" (`audit.list_my_events`) | none | every read | audit-free |
| Own decision receipts (`audit_receipt.read_decision_receipt`) | none | every read | audit-free |
| Admin ledger explorer (`audit.list_events`, `audit.list_event_page`, `audit.audit_rollups`) | none | every read | audit-free until D-audit-reads-c3 ships `VIEW_AUDIT_LEDGER` |
| Admin ledger explorer filters (`audit.audit_facets`, `audit.count_events`) | none | on an explicit filter-menu open / filter change | audit-free (admin-gated) until D-audit-reads-c3 |
| Lead Queue filter counts (`leads.count_leads`, `leads.lead_facets`) | none | on an explicit menu open or omnibox count; never with `borrower_ids` (422) | audit-free |
| Saved queue views list (`GET /workspace/saved-views`) | none | when the Saved views panel opens | audit-free (the actor's own views) |
| Saved queue view save / delete (`/workspace/saved-views`) | `SAVE_QUEUE_VIEW` / `DELETE_QUEUE_VIEW` | once per save or soft delete | same Lakebase statement as the change, fail-closed |
| Home Delta Explainer (`home.home_summary_attribution`) | none | only while an evidence drawer for a supported "since your last login" measure is open on Overview; never on hover, prefetch or poll | audit-free (gold and ref aggregates) |
| Home watchlist briefings (`growth_agent_compose_routes.growth_agent_watchlist_summary`) | none | once per Home load (the card never POSTs, so it starts no run) | audit-free |
| Home WHY NOW rate move (`analytics_rate_window.rate_window`) | none | on Home only when the summary is a delta with a previous visit (and on Analytics as before) | audit-free |

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
