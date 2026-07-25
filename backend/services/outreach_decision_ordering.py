"""Shared Lakebase ordering contract for borrower outreach decisions."""

BORROWER_DECISION_LOCK = """
SELECT pg_advisory_xact_lock(
    hashtext('mip_outreach_decision:' || %(borrower_id)s)
)
"""

LATEST_BORROWER_DECISION = """
SELECT approval_id::text, action
FROM mip_app.approvals
WHERE borrower_id = %(borrower_id)s
ORDER BY decided_at DESC, approval_id::text DESC
LIMIT 1
"""


def is_current_approval(row: object, *, approval_id: str) -> bool:
    if not isinstance(row, dict):
        return False
    return (
        str(row.get("approval_id") or "") == approval_id
        and row.get("action") == "approve"
    )
