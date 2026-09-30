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
