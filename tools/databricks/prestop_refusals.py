"""Coded refusals for the pre-stop deploy probes (``deploy_prestop_probes``).

Every refusal the pre-stop probes print carries one reviewed code, so the
deploy's refusal matrix (tests/unit/test_prestop_refusal_matrix.py) can prove
each one exits 2 before the signed lease and before any App stop. A message
names the object and the remedy; the code alone is what a public summary may
carry.
"""

from __future__ import annotations

from dataclasses import dataclass

# The Supervisor and Genie-grant clauses that predate the codes.
SUPERVISOR_RETIREMENT = "supervisor_retirement"
RESERVED_REPLACEMENT_NAME = "reserved_replacement_name"
AUDITED_PRINCIPAL_UNRESOLVED = "audited_principal_unresolved"
GENIE_GROUP_GRANT = "genie_group_grant"
VERIFIER_GENIE_GRANT = "verifier_genie_grant"
# The signed last-good rollback record (prestop_rollback_record).
ROLLBACK_RECORD_ABSENT = "rollback_record_absent"
ROLLBACK_RECORD_PRESENT = "rollback_record_present"
ROLLBACK_RECORD_TAMPERED = "rollback_record_tampered"
ROLLBACK_RECORD_DRIFT = "rollback_record_drift"
# Governed Unity Catalog owners (prestop_owner_policy).
OWNER_POLICY = "owner_policy"
# Owner-equivalent grantees on the governed catalog, schemas and space (attack G12).
OWNER_EQUIVALENT = "owner_equivalent"
# App serving-endpoint access outside the record-pinned endpoints.
SERVING_ENDPOINT = "serving_endpoint"
# One real Genie turn on the governed space.
GENIE_BUDGET = "genie_budget"
# A probe that could not complete its reads refuses rather than pass.
PROBE_ERROR = "probe_error"

REFUSAL_CODES = frozenset(
    {
        SUPERVISOR_RETIREMENT,
        RESERVED_REPLACEMENT_NAME,
        AUDITED_PRINCIPAL_UNRESOLVED,
        GENIE_GROUP_GRANT,
        VERIFIER_GENIE_GRANT,
        ROLLBACK_RECORD_ABSENT,
        ROLLBACK_RECORD_PRESENT,
        ROLLBACK_RECORD_TAMPERED,
        ROLLBACK_RECORD_DRIFT,
        OWNER_POLICY,
        OWNER_EQUIVALENT,
        SERVING_ENDPOINT,
        GENIE_BUDGET,
        PROBE_ERROR,
    }
)


@dataclass(frozen=True)
class PrestopRefusal:
    """One pre-stop refusal: a reviewed code and the message that names the remedy."""

    code: str
    message: str

    def __post_init__(self) -> None:
        if self.code not in REFUSAL_CODES:
            raise ValueError(f"unreviewed pre-stop refusal code {self.code!r}")
        if not self.message.strip():
            raise ValueError("a pre-stop refusal needs a message")

    def line(self) -> str:
        """The stderr line: ``REFUSED: <code>: <message>`` after the probe prefix."""

        return f"{self.code}: {self.message}"
