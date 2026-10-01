"""SQL for the maker-checker approval requests (Lakebase ``mip_app`` only).

Statements only; the logic lives in the approval-request service. Every read
of a request's state goes through ``ITEMS_WITH_DECISIONS``: it returns each
item with the latest finalized approve/reject linked to the request and the
latest finalized approve/reject for the borrower after the request, and the
service derives the row state from those two facts (hold and revoke rows are
ignored). The link is matched on the canonical decision intent text, which
``_canonical_intent`` always writes as ``"approval_request_batch_id":"<id>"``
(sorted keys, no whitespace); a text match never fails on a legacy row the
way a ``::jsonb`` cast of every decision intent could.
"""

from __future__ import annotations

REQUEST_EXPIRY_DAYS = 30

BATCH_BY_KEY = """
SELECT batch_id::text AS batch_id, request_intent_hash, response,
       audit_event_id::text AS audit_event_id
FROM mip_app.approval_request_batches
WHERE requested_by = %(requested_by)s
  AND request_key = %(request_key)s
"""

LATEST_FINALIZED_DECISIONS = """
SELECT DISTINCT ON (borrower_id) borrower_id, action
FROM mip_app.approvals
WHERE borrower_id = ANY(%(borrower_ids)s)
  AND audit_event_id IS NOT NULL
ORDER BY borrower_id, decided_at DESC, approval_id::text DESC
"""

EXPIRE_STALE_OPEN_ITEMS = """
UPDATE mip_app.approval_request_items AS item
SET status = 'expired', closed_at = now()
FROM mip_app.approval_request_batches AS batch
WHERE item.batch_id = batch.batch_id
  AND item.borrower_id = ANY(%(borrower_ids)s)
  AND item.status = 'open'
  AND batch.created_at < now() - interval '30 days'
"""

INSERT_BATCH = """
INSERT INTO mip_app.approval_request_batches (
    batch_id, requested_by, request_key, request_intent_hash, note
) VALUES (
    %(batch_id)s, %(requested_by)s, %(request_key)s, %(request_intent_hash)s, %(note)s
)
ON CONFLICT ON CONSTRAINT uq_approval_request_batches_key DO NOTHING
RETURNING batch_id::text AS batch_id
"""

INSERT_OPEN_ITEMS = """
INSERT INTO mip_app.approval_request_items (batch_id, borrower_id)
SELECT %(batch_id)s, candidate.borrower_id
FROM unnest(%(borrower_ids)s::text[]) AS candidate(borrower_id)
ON CONFLICT (borrower_id) WHERE status = 'open' DO NOTHING
RETURNING borrower_id
"""

FINALIZE_BATCH = """
UPDATE mip_app.approval_request_batches
SET response = %(response)s::jsonb,
    audit_event_id = %(audit_event_id)s
WHERE batch_id = %(batch_id)s
  AND response IS NULL
  AND audit_event_id IS NULL
RETURNING batch_id::text AS batch_id
"""

BATCH_BY_ID = """
SELECT batch_id::text AS batch_id, requested_by, note, created_at
FROM mip_app.approval_request_batches
WHERE batch_id = %(batch_id)s
  AND audit_event_id IS NOT NULL
"""

BATCH_BY_ID_FOR_UPDATE = BATCH_BY_ID.rstrip() + "\nFOR UPDATE\n"

OPEN_SCOPE_BATCHES = """
SELECT batch.batch_id::text AS batch_id, batch.requested_by, batch.note, batch.created_at
FROM mip_app.approval_request_batches AS batch
WHERE batch.audit_event_id IS NOT NULL
  AND batch.created_at >= now() - interval '30 days'
  AND EXISTS (
      SELECT 1
      FROM mip_app.approval_request_items AS item
      WHERE item.batch_id = batch.batch_id
        AND item.status = 'open'
  )
ORDER BY batch.created_at ASC, batch.batch_id ASC
LIMIT 200
"""

MINE_SCOPE_BATCHES = """
SELECT batch.batch_id::text AS batch_id, batch.requested_by, batch.note, batch.created_at
FROM mip_app.approval_request_batches AS batch
WHERE batch.audit_event_id IS NOT NULL
  AND batch.requested_by = %(requested_by)s
  AND batch.created_at >= now() - interval '30 days'
ORDER BY batch.created_at DESC, batch.batch_id DESC
LIMIT 50
"""

ITEMS_WITH_DECISIONS = """
SELECT item.batch_id::text AS batch_id,
       item.borrower_id,
       item.status,
       batch.created_at AS batch_created_at,
       linked.action AS linked_action,
       linked.approval_id AS linked_approval_id,
       outside.action AS outside_action,
       now() AS read_at
FROM mip_app.approval_request_items AS item
JOIN mip_app.approval_request_batches AS batch ON batch.batch_id = item.batch_id
LEFT JOIN LATERAL (
    SELECT decision.action, decision.approval_id::text AS approval_id
    FROM mip_app.approvals AS decision
    WHERE decision.borrower_id = item.borrower_id
      AND decision.audit_event_id IS NOT NULL
      AND decision.action IN ('approve', 'reject')
      AND decision.decided_at >= batch.created_at
      AND strpos(
          decision.decision_intent,
          '"approval_request_batch_id":"' || item.batch_id::text || '"'
      ) > 0
    ORDER BY decision.decided_at DESC, decision.approval_id::text DESC
    LIMIT 1
) AS linked ON true
LEFT JOIN LATERAL (
    SELECT decision.action
    FROM mip_app.approvals AS decision
    WHERE decision.borrower_id = item.borrower_id
      AND decision.audit_event_id IS NOT NULL
      AND decision.action IN ('approve', 'reject')
      AND decision.decided_at > batch.created_at
    ORDER BY decision.decided_at DESC, decision.approval_id::text DESC
    LIMIT 1
) AS outside ON true
WHERE item.batch_id = ANY(%(batch_ids)s::uuid[])
  AND (%(borrower_id)s::text IS NULL OR item.borrower_id = %(borrower_id)s::text)
ORDER BY batch.created_at, item.batch_id, item.borrower_id
"""

WITHDRAW_OPEN_ITEMS = """
UPDATE mip_app.approval_request_items
SET status = 'withdrawn', closed_at = now()
WHERE batch_id = %(batch_id)s
  AND borrower_id = ANY(%(borrower_ids)s)
  AND status = 'open'
RETURNING borrower_id
"""

EXPIRE_OPEN_ITEMS_FOR_BORROWER = """
UPDATE mip_app.approval_request_items
SET status = 'expired', closed_at = now()
WHERE borrower_id = %(borrower_id)s
  AND status = 'open'
RETURNING batch_id::text AS batch_id
"""
