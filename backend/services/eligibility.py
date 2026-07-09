"""Contact-eligibility enforcement -- the ONE interface for every path.

S1.4 contract: every server-side campaign / lead-queue / export decision
about whether a borrower may be contacted reads through
``EligibilityService``. The consent source is swappable (synthetic seed
today, a CRM/CDP connector id once S4.1 lands) without touching
enforcement code or audit code: enforcement branches only on
``EligibilityDecision`` fields, and suppression audit rows are written
from the decision alone.

The gold columns are the transport (``marketing_eligible``,
``consent_status``, ``suppression_reason``, ``dnc``,
``eligibility_source``, ``last_touch_at``, ``eligible_recontact_at``);
this module is the only place enforcement semantics live:

* Row-level: ``EligibilityService.evaluate(borrower)`` for outreach
  draft/approve and activation-export staging.
* Set-level: ``eligible_sql_predicate()`` / ``suppressed_sql_predicate()``
  for warehouse WHERE clauses (lead queue, campaign preview, growth-agent
  workflow counts).

Consent fields are synthetic-by-design (see
``sql/transformations/demo_first_party_feeds.sql``); the decision carries
``source`` so every audit row records that provenance.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from backend.services.audit_store import AuditEvent, AuditStore
from backend.services.observability import emit

log = logging.getLogger(__name__)

# Provenance value for the governed demo seed; a connected CRM/CDP
# connector writes its own id into gold.eligibility_source (S4.1 seam).
DEFAULT_ELIGIBILITY_SOURCE = "synthetic_seed"

# Audit vocabulary for enforcement suppressions.
SUPPRESS_EVENT_TYPE = "SUPPRESS_CONTACT"
SUPPRESS_ACTION = "suppress_contact"

FREQUENCY_CAP_DAYS = 30

# Machine reason codes; enforcement code branches on these, never on raw
# borrower fields, so a source swap cannot change enforcement behavior.
REASON_NOT_CONFIGURED = "contactability_not_configured"
REASON_CONSENT_NOT_OPT_IN = "consent_not_opt_in"
REASON_FREQUENCY_CAP = "frequency_cap"
REASON_SUPPRESSED = "suppressed"
REASON_NOT_PROVEN = "eligibility_not_proven"

_REQUIRED_CONTACTABILITY_FIELDS = frozenset(
    {"marketing_eligible", "consent_status", "suppression_reason"}
)


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


@dataclass(frozen=True)
class EligibilityDecision:
    """Point-in-time contactability facts for one borrower.

    ``eligible`` is fail-closed: it is True only when consent is opt-in,
    no suppression (including do-not-contact) exists, the frequency cap
    is clear, and the gold ``marketing_eligible`` gate agrees.
    """

    eligible: bool
    reason_code: str | None
    marketing_eligible: bool
    consent_status: str
    suppression_reason: str | None
    dnc: bool
    source: str
    configured: bool
    last_touch_at: datetime | None = None
    eligible_recontact_at: datetime | None = None
    earliest_recontact_at: datetime | None = field(default=None)

    def audit_payload(self) -> dict[str, Any]:
        """Allowlist-safe metadata for the suppression audit row."""
        return {
            "marketing_eligible": self.marketing_eligible,
            "consent_status": self.consent_status,
            "suppression_reason": self.suppression_reason,
            "dnc": self.dnc,
            "eligibility_source": self.source,
            "reason": self.reason_code,
            "last_touch_at": self.last_touch_at.isoformat() if self.last_touch_at else None,
            "eligible_recontact_at": (
                self.eligible_recontact_at.isoformat() if self.eligible_recontact_at else None
            ),
        }


class EligibilityService(Protocol):
    """Single enforcement read-path for contact eligibility.

    Implementations differ only in where the consent facts come from;
    callers depend on this Protocol and on ``EligibilityDecision``.
    """

    def evaluate(self, borrower: Any) -> EligibilityDecision: ...


class GoldEligibilityService:
    """Reads the gold-layer consent fields carried on a borrower/lead row.

    The row shape is duck-typed (Pydantic model or plain object) because
    outreach, activation, and lead rows share the same gold field names.
    A future CRM/CDP connector implementation replaces this class behind
    ``get_eligibility_service`` -- enforcement and audit code stay put.
    """

    def evaluate(self, borrower: Any) -> EligibilityDecision:
        fields_set = set(getattr(borrower, "model_fields_set", set()) or set())
        configured = _REQUIRED_CONTACTABILITY_FIELDS.issubset(fields_set)

        marketing_eligible = getattr(borrower, "marketing_eligible", False) is True
        consent_status = str(getattr(borrower, "consent_status", "unknown") or "unknown")
        suppression_reason = getattr(borrower, "suppression_reason", None)
        dnc = bool(getattr(borrower, "dnc", False)) or suppression_reason == "do_not_contact"
        source = str(
            getattr(borrower, "eligibility_source", None) or DEFAULT_ELIGIBILITY_SOURCE
        )
        last_touch_at = _coerce_datetime(getattr(borrower, "last_touch_at", None))
        recontact_at = _coerce_datetime(getattr(borrower, "eligible_recontact_at", None))
        now = datetime.now(UTC)

        earliest_recontact: datetime | None = None
        if recontact_at and recontact_at > now:
            earliest_recontact = recontact_at
        elif last_touch_at and last_touch_at > now - timedelta(days=FREQUENCY_CAP_DAYS):
            earliest_recontact = last_touch_at + timedelta(days=FREQUENCY_CAP_DAYS)

        reason_code: str | None = None
        if not configured:
            reason_code = REASON_NOT_CONFIGURED
        elif consent_status != "opt_in":
            reason_code = REASON_CONSENT_NOT_OPT_IN
        elif earliest_recontact is not None:
            reason_code = REASON_FREQUENCY_CAP
        elif suppression_reason or dnc:
            reason_code = REASON_SUPPRESSED
        elif not marketing_eligible:
            reason_code = REASON_NOT_PROVEN

        return EligibilityDecision(
            eligible=reason_code is None,
            reason_code=reason_code,
            marketing_eligible=marketing_eligible,
            consent_status=consent_status,
            suppression_reason=suppression_reason,
            dnc=dnc,
            source=source,
            configured=configured,
            last_touch_at=last_touch_at,
            eligible_recontact_at=recontact_at,
            earliest_recontact_at=earliest_recontact,
        )


def eligible_sql_predicate(alias: str = "") -> str:
    """Canonical fail-closed warehouse predicate for eligible-only reads.

    The FULL set-based equivalent of ``EligibilityDecision``: a row where
    ``marketing_eligible`` is stale-true but consent, suppression, dnc, or
    the frequency cap disagree must be excluded exactly as ``evaluate``
    would block it. ``tests/unit/test_contact_eligibility.py`` pins every
    decision field into this text and keeps the deployed
    ``fn_segment_counts`` UC function in lockstep -- change all three
    together.
    """
    p = f"{alias}." if alias else ""
    return (
        f"({p}marketing_eligible = TRUE"
        f" AND {p}consent_status = 'opt_in'"
        f" AND {p}suppression_reason IS NULL"
        f" AND COALESCE({p}dnc, FALSE) = FALSE"
        f" AND ({p}eligible_recontact_at IS NULL"
        f" OR {p}eligible_recontact_at <= CURRENT_TIMESTAMP())"
        f" AND ({p}last_touch_at IS NULL"
        # ANSI quoted-interval form: the Genie trusted-SQL policy lexer
        # treats a bare digit between two string literals as a potential
        # raw-identifier literal, so the cap value must stay quoted.
        f" OR {p}last_touch_at < CURRENT_TIMESTAMP() - INTERVAL '{FREQUENCY_CAP_DAYS}' DAYS))"
    )


def suppressed_sql_predicate(alias: str = "") -> str:
    """Warehouse predicate for admin-gated suppressed-only analytics.

    Defined as the exact negation of ``eligible_sql_predicate`` so the
    two set-based views partition the population without drift.
    """
    return f"NOT {eligible_sql_predicate(alias)}"


def write_suppression_audit(
    store: AuditStore,
    *,
    actor: str,
    borrower_id: str,
    decision: EligibilityDecision,
    surface: str,
    request_id: str | None = None,
) -> AuditEvent:
    """Record one enforcement suppression as a Lakebase audit row.

    ``surface`` names the blocked path (``outreach_draft``,
    ``outreach_approve``, ``activation_stage``) and lands in the
    allowlisted ``route`` metadata key. This function only consumes the
    decision, so a consent-source swap never touches audit code.
    """
    payload = {
        "borrower_id": borrower_id,
        "route": surface,
        **decision.audit_payload(),
    }
    if request_id:
        payload["request_id"] = request_id
    return store.write(
        actor=actor,
        action=SUPPRESS_ACTION,
        entity_type="borrower",
        entity_id=borrower_id,
        payload_json=payload,
        event_type=SUPPRESS_EVENT_TYPE,
        request_id=request_id,
    )


def safe_write_suppression_audit(store: AuditStore, **kwargs: Any) -> None:
    """Suppression-audit writer for paths that must not let audit raise.

    Mirrors the routers' ``_safe_audit_write`` posture: the borrower is
    already blocked (the enforcement outcome stands); a failed audit
    write is surfaced to operators as a structured ``audit.dropped``
    event, never as a 500 to the caller.
    """
    try:
        write_suppression_audit(store, **kwargs)
    except Exception as exc:  # noqa: BLE001 -- audit must not mask the block
        emit(
            log,
            "audit.dropped",
            dependency="lakebase",
            exc_type=type(exc).__name__,
            outcome="error",
        )


_service: EligibilityService | None = None


def get_eligibility_service() -> EligibilityService:
    """FastAPI dependency provider (process-wide singleton).

    S4.1 seam: when a CRM/CDP connector lands, select the implementation
    here (e.g. from settings) -- callers never change.
    """
    global _service
    if _service is None:
        _service = GoldEligibilityService()
    return _service
