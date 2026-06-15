# Security and Compliance

Module 0 is designed to fail closed and remain public-demo safe.

## Public Demo Masking

The application defaults to masked output unless `MIP_EXPOSE_RAW_COTALITY_IDS`
is explicitly enabled in a governed, non-public environment. Public recordings
must keep masking on. Masking covers raw CLIP, Owner Link, addresses, names,
phones, emails, and competitor servicer names.

## Governed State Changes

State-changing workflows write through Lakebase-backed APIs and audit tables.
Approvals, rejections, saved leads, saved drafts, and Genie-materialized cohorts
must carry actor, action, entity, payload, timestamp, and request identifiers.
If Lakebase is unavailable, the API must return an error and leave the UI in a
pending or failed state; it must not claim success.

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
