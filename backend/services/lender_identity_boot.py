"""One ``LENDER_IDENTITY_VERIFIED`` audit row per App boot on a pinned lender record.

``backend/runtime.py`` verifies a digest-pinned customer lender record
(``backend/schemas/lender_identity_record.py``) before anything else loads
and refuses the boot when it does not verify. Once the App is serving
dependencies, the lifespan calls ``record_lender_identity_boot`` exactly once:
it appends one ledger row through the existing audit store, carrying the
record's public sha256 and nothing else (never the lender's name). The
code-registry identity (the Summit demo) writes nothing, and no read of the
identity anywhere writes a row.

Degraded-state choice: a failed write never fails or restarts the boot. It
emits one ERROR ``lender_identity_audit_write_failed`` line (outcome
``degraded``, with the digest), and the boot is not retried: the INSERT may
have committed before the error surfaced, and a retry could then write a
second row for the same boot. The next boot writes its own row.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from threading import Lock
from typing import Literal

from backend.schemas.lender_identity_record import ReviewedLenderIdentity
from backend.services.audit_entity_ids import audit_safe_letters
from backend.services.audit_store import AuditStore
from backend.services.observability import emit

log = logging.getLogger(__name__)

LENDER_IDENTITY_AUDIT_ACTION = "lender_identity_verified"
LENDER_IDENTITY_AUDIT_EVENT_TYPE = "LENDER_IDENTITY_VERIFIED"
LENDER_IDENTITY_AUDIT_ENTITY_TYPE = "lender_identity"

BootAuditOutcome = Literal["not_pinned", "recorded", "write_failed", "already_attempted"]

_attempt_lock = Lock()
_attempted = False


def lender_identity_entity_id(digest: str) -> str:
    """The row's entity id: the digest's letter image, never raw hex (phone-shape guard)."""

    return f"lenderidentity-{audit_safe_letters(digest)}"


def record_lender_identity_boot(
    identity: ReviewedLenderIdentity,
    *,
    store_factory: Callable[[], AuditStore],
    actor: str,
) -> BootAuditOutcome:
    """Write this process's one boot row for a pinned identity; never raises.

    The store is built only for a pinned identity, so the code-registry boot
    does no audit work at all.
    """

    global _attempted
    if identity.digest is None:
        return "not_pinned"
    digest = identity.digest
    with _attempt_lock:
        if _attempted:
            return "already_attempted"
        _attempted = True
    try:
        store_factory().write(
            actor=actor,
            action=LENDER_IDENTITY_AUDIT_ACTION,
            event_type=LENDER_IDENTITY_AUDIT_EVENT_TYPE,
            entity_type=LENDER_IDENTITY_AUDIT_ENTITY_TYPE,
            entity_id=lender_identity_entity_id(digest),
            payload_json={"lender_identity_sha256": digest},
        )
    except Exception as exc:  # noqa: BLE001 -- the boot degrades, it never crash-loops
        emit(
            log,
            "lender_identity_audit_write_failed",
            level=logging.ERROR,
            dependency="lakebase",
            outcome="degraded",
            exc_type=type(exc).__name__,
            lender_identity_sha256=digest,
            recommended_action=(
                "the App serves the verified lender identity, but this boot has no "
                "LENDER_IDENTITY_VERIFIED ledger row; check Lakebase health, then "
                "restart the App to record its next boot"
            ),
        )
        return "write_failed"
    emit(log, "lender_identity_verified", level=logging.INFO, outcome="ok", lender_identity_sha256=digest)
    return "recorded"


def _reset_boot_audit_for_tests() -> None:
    global _attempted
    with _attempt_lock:
        _attempted = False
