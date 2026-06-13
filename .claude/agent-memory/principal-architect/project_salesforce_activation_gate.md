---
name: salesforce-activation-gate
description: Real Salesforce delivery is dead-code-safe by default — gated on a destination row that no app code ever flips to 'connected'.
metadata:
  type: project
---

The `feat/salesforce-delivery` branch adds a real (stdlib urllib) Salesforce REST delivery adapter that fires synchronously inside `POST /api/.../stage` (`backend/api/activation.py::_maybe_deliver_salesforce`).

It is gated three ways: `destination_type=='salesforce'` AND `destination.status=='connected'` AND `settings.salesforce_configured`. Delivery failure is non-fatal to /stage (row is already durably staged + audited); calls are circuit-breakered via `Resilient` keyed `"salesforce"`.

**Why this is booth-safe:** The `salesforce_crm` destination is seeded `status='not_configured'` in `lakebase/schema.sql`, and **no application code path flips a destination to 'connected'** — the only writes to `activation_destinations.status` are the seed INSERT. So at the booth the inline delivery is inert (gate never passes) unless someone manually `UPDATE`s the Lakebase row AND fills the `SALESFORCE_*` env. Honest degraded posture: unconfigured = staged-only no-op, never claims a delivery without a real 201.

**How to apply:** When asked to arm a live Salesforce demo, the missing step is (1) a manual/admin `UPDATE activation_destinations SET status='connected'` for `salesforce_crm` and (2) `SALESFORCE_*` creds. There is no admin endpoint for the flip today. Schema (`delivery_metadata` jsonb col, `delivered`/`failed` outbox statuses, `salesforce` destination_type) already exists in `main` — the branch is pure backend code, no migration. Idempotency on re-stage is enforced by a UNIQUE index on `activation_outbox.request_id`, but Salesforce itself is NOT deduped by this code (in-create retry budget capped at 2 to bound duplicate-Task risk).
