---
name: growth-agent-idempotency-gap
description: growth_agent.run computes a request_id but no index/ON CONFLICT enforces it — endpoint is non-idempotent despite implying otherwise
metadata:
  type: project
---

`backend/api/growth_agent.py` (commit bbda202) computes a deterministic `request_id` (uuid5 of actor|workflow|criteria, lines ~505-508) and passes it to `write_audit_event_in_transaction`, but nothing enforces it:

- `action_audit` request_id unique indexes (`lakebase/schema.sql:558-563`) are predicated on `event_type LIKE 'GENIE_ACTION_%'`. Growth agent writes `event_type="GROWTH_AGENT_RUN"`, which matches neither — so the audit insert (no `ON CONFLICT`) always inserts.
- `growth_agent_runs` / `growth_agent_monitors` (`schema.sql:687-741`) have no `request_id` column or unique index.

Result: duplicate POSTs double-write the run + audit ledgers. The `request_id` is dead machinery.

**Why:** Sibling write paths DO enforce this correctly — approvals (`schema.sql:227-228`), lead_outcomes (`485-487`), activation_outbox (`387-388`) each have a real unique index + `outreach.py:268-280` does the ON CONFLICT→lookup dance. Growth-agent skipped both halves. Relates to [[r5-governance-fixes]] R5-01 request_id idempotency.

**How to apply:** If asked to harden growth-agent: either add a partial unique index on `action_audit(actor_email, request_id, event_type) WHERE event_type='GROWTH_AGENT_RUN'` + ON CONFLICT in both inserts + a request_id column on growth_agent_runs (mirror outreach), OR delete the `_request_id` plumbing if at-least-once is acceptable for this non-activating read-ledger. The half-wired state is the bug. Tests in `tests/unit/test_growth_agent_api.py` mock lakebase so heavily (fresh uuid4 per insert, no conflict modeling) that idempotency is structurally untestable there — needs a real-Lakebase integration test.
