"""Configured lender disclosure seed rendering and schema/seed parity preflight."""

from __future__ import annotations

import hashlib
import re

from backend.schemas.lender_identity import (
    effective_public_tenant_id,
    validate_public_lender_identity,
)

_ACTIVE_REQUIRES_READY_CONSTRAINT = "campaigns_active_requires_ready_treatment_chk"
_CAMPAIGN_SEED_INSERT_RE = re.compile(r"INSERT INTO mip_app\.campaigns\b.*?;", re.DOTALL)


def _preflight_seed_schema_parity(schema_sql: str, seed_sql: str) -> None:
    """Reject schema/seed texts that cannot replay together, before any mutation.

    File-level delta ships can leave the deployed tree mixed: a schema.sql that
    enforces treatment readiness next to an older seed_campaigns.sql that still
    inserts pre-treatment campaigns as ``'active'``. Postgres validates the
    proposed insert tuple against table CHECK constraints before ON CONFLICT
    arbitration, so that mix aborts mid-transaction with a bare constraint
    violation minutes into the run. Fail here instead with a remediation path.
    """

    if _ACTIVE_REQUIRES_READY_CONSTRAINT not in schema_sql:
        return
    campaign_inserts = _CAMPAIGN_SEED_INSERT_RE.findall(seed_sql)
    if not campaign_inserts:
        raise RuntimeError(
            "lakebase/seed_campaigns.sql contains no mip_app.campaigns seed; "
            "re-ship lakebase/schema.sql and lakebase/seed_campaigns.sql from "
            "the same revision, then re-run the migration"
        )
    for statement in campaign_inserts:
        if "'active'" in statement:
            raise RuntimeError(
                "lakebase/seed_campaigns.sql still seeds pre-treatment campaigns "
                f"as active, but lakebase/schema.sql enforces "
                f"{_ACTIVE_REQUIRES_READY_CONSTRAINT}; the deployed tree mixes "
                "revisions -- re-ship lakebase/schema.sql and "
                "lakebase/seed_campaigns.sql from the same commit, then re-run "
                "the migration"
            )


def _sql_literal(value: str) -> str:
    """Quote a validated public configuration value for static migration SQL."""

    return "'" + value.replace("'", "''") + "'"


def _tenant_disclosure_seed_sql(
    *,
    lender_name: object,
    lender_nmls_id: object,
    tenant_id: object,
) -> str:
    """Render idempotent reviewed generic disclosures for this deployment."""

    lender, nmls_id = validate_public_lender_identity(lender_name, lender_nmls_id)
    tenant = effective_public_tenant_id(tenant_id, lender_name=lender)
    identity_digest = hashlib.sha256(f"generic-v1\0{lender}\0{nmls_id}".encode()).hexdigest()[:16]
    version = f"{tenant}-reviewed-generic-v1-{identity_digest}"
    rows = (
        (
            "_ALL",
            "email",
            f"{lender}, NMLS #{nmls_id}. Equal Housing Lender. This is not a commitment "
            "to lend. Terms subject to credit, collateral, and underwriting approval. To "
            f"opt out of marketing, reply unsubscribe or contact {lender} at its governed "
            "compliance address.",
        ),
        (
            "_ALL",
            "direct_mail",
            f"{lender}, NMLS #{nmls_id}. Equal Housing Lender. This is not a commitment "
            "to lend. Terms subject to credit, collateral, and underwriting approval. To "
            f"opt out of marketing, contact {lender} at its governed compliance address.",
        ),
        (
            "_ALL",
            "sms",
            f"{lender} NMLS #{nmls_id}. Equal Housing Lender. Reply STOP to opt out. Msg "
            "and data rates may apply.",
        ),
    )
    values = ",\n".join(
        "    ("
        + ", ".join(
            (
                _sql_literal(tenant),
                _sql_literal(state),
                _sql_literal(channel),
                _sql_literal(version),
                _sql_literal(body),
                "TRUE",
                "now()",
            )
        )
        + ")"
        for state, channel, body in rows
    )
    expected_values = ",\n".join(
        "    ("
        + ", ".join(
            (
                _sql_literal(channel),
                _sql_literal(version),
                _sql_literal(body),
            )
        )
        + ")"
        for _state, channel, body in rows
    )
    return f"""
UPDATE mip_app.tenant_disclosures
SET active = FALSE,
    updated_at = now()
WHERE tenant_id = {_sql_literal(tenant)}
  AND state = '_ALL'
  AND channel IN ('email', 'direct_mail', 'sms')
  AND active = TRUE;

INSERT INTO mip_app.tenant_disclosures AS target (
    tenant_id, state, channel, disclosure_version, body, active, updated_at
)
VALUES
{values}
ON CONFLICT (tenant_id, state, channel, disclosure_version)
DO UPDATE SET
    active = EXCLUDED.active,
    updated_at = EXCLUDED.updated_at
WHERE target.body = EXCLUDED.body;

DO $mip_tenant_disclosure_postflight$
BEGIN
    IF (
        SELECT count(*)
        FROM mip_app.tenant_disclosures
        WHERE tenant_id = {_sql_literal(tenant)}
          AND state = '_ALL'
          AND channel IN ('email', 'direct_mail', 'sms')
          AND active = TRUE
    ) <> 3 OR (
        SELECT count(*)
        FROM mip_app.tenant_disclosures AS actual
        JOIN (VALUES
{expected_values}
        ) AS expected(channel, disclosure_version, body)
          ON actual.channel = expected.channel
         AND actual.disclosure_version = expected.disclosure_version
         AND actual.body = expected.body
        WHERE actual.tenant_id = {_sql_literal(tenant)}
          AND actual.state = '_ALL'
          AND actual.active = TRUE
    ) <> 3 THEN
        RAISE EXCEPTION
            'configured tenant disclosure postflight failed for tenant %',
            {_sql_literal(tenant)};
    END IF;
END
$mip_tenant_disclosure_postflight$;
"""
