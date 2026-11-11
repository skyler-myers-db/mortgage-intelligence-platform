"""Configured lender disclosure seed rendering and schema/seed parity preflight."""

from __future__ import annotations

import hashlib
import re

from backend.schemas.lender_identity_record import (
    LENDER_IDENTITY_JSON_ENV,
    LENDER_IDENTITY_SHA256_ENV,
    LENDER_NAME_ENV,
    LENDER_NMLS_ENV,
    TENANT_ID_ENV,
    reviewed_lender_identity_from_env,
)

_ACTIVE_REQUIRES_READY_CONSTRAINT = "campaigns_active_requires_ready_treatment_chk"
_CAMPAIGN_SEED_INSERT_RE = re.compile(r"INSERT INTO mip_app\.campaigns\b.*?;", re.DOTALL)

# lakebase/seed_campaigns.sql names its demo operator (the sales_team admin
# row, the archived campaigns' owner and the sample approvals' actor) by this
# token, never by an address: a committed address would name one install's
# operator in every install (M1-PR4).
SEED_OPERATOR_TOKEN = "{{mip_seed_operator_email}}"
SEED_OPERATOR_EMAIL_ENV = "MIP_SEED_OPERATOR_EMAIL"
# The operator a seed renders when the install configures none: a reserved
# example domain (RFC 2606), so it names nobody.
SYNTHETIC_SEED_OPERATOR = "operator@summit.example"
# One address and nothing else; no quote, backslash or space can reach the
# single-quoted literal the token sits in.
_SEED_OPERATOR_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


def render_seed_sql(seed_sql: str, *, seed_operator_email: str = "") -> str:
    """The seed with its operator token rendered: the install's operator, else the synthetic one.

    The operator is the profile's ``MIP_SEED_OPERATOR_EMAIL``, a demo-only
    field (tools/clients/rules.py refuses it for a customer install), which
    the bundle passes to the migrate job as ``--seed-operator-email``. A
    customer install, and a demo that sets none, seed the synthetic operator.
    The rendered text is what apply executes and what the schema-input digest
    covers, so an install renders the same seed, and the same digest, on
    every run with the same value.
    """

    operator = seed_operator_email.strip() or SYNTHETIC_SEED_OPERATOR
    if _SEED_OPERATOR_RE.fullmatch(operator) is None:
        raise RuntimeError(f"{SEED_OPERATOR_EMAIL_ENV} must be exactly one email address")
    rendered = seed_sql.replace(SEED_OPERATOR_TOKEN, operator)
    if "{{" in rendered:
        raise RuntimeError("lakebase/seed_campaigns.sql holds an unknown template token")
    return rendered


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
    lender_identity_json: object = "",
    lender_identity_sha256: object = "",
) -> str:
    """Render idempotent reviewed generic disclosures for this deployment.

    The identity is the code registry's, or a digest-pinned customer record
    re-verified here (backend/schemas/lender_identity_record.py).
    """

    identity = reviewed_lender_identity_from_env(
        {
            LENDER_NAME_ENV: lender_name,
            LENDER_NMLS_ENV: lender_nmls_id,
            TENANT_ID_ENV: tenant_id,
            LENDER_IDENTITY_JSON_ENV: lender_identity_json,
            LENDER_IDENTITY_SHA256_ENV: lender_identity_sha256,
        }
    )
    lender, nmls_id, tenant = identity.lender_name, identity.nmls_id, identity.tenant_id
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
