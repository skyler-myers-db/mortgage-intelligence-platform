"""A fictional, fully populated client profile for the tools/clients tests.

Every value is synthetic: example-domain emails, ``dbc-unit`` hosts, short
workspace ids and all-zero UUIDs, so none of it trips the committed-identifier
ratchet (tests/unit/test_no_client_identifiers_committed.py).
"""

from __future__ import annotations

import json

from backend.schemas.lender_identity_record import lender_identity_digest
from tools.clients.schema import FIELD_CLASS, FieldClass


def app_id(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


IDENTITY_IDS = {
    "deployer": app_id(1),
    "jobs": app_id(2),
    "validator": app_id(3),
    "normal": app_id(4),
    "operator2": app_id(5),
    "admin": app_id(6),
    "release_probe": app_id(7),
    "verifier": app_id(8),
    "runtime": app_id(9),
    "proxy": app_id(10),
    "account_principal": app_id(11),
}
LENDER_IDENTITY = {
    "version": 1,
    "legal_name": "Fictional Harbor Lending",
    "nmls_id": "424242",
    "tenant_id": "fictional_harbor",
    "servicer_aliases": ["Fictional Harbor Servicing"],
    "reviewed_by": "reviewer@lender.example",
    "reviewed_at": "2026-10-01",
}
# The optional co-branding mark a record may carry (its facts, never the image).
LENDER_MARK = {
    "sha256": "ab" * 32,
    "media_type": "image/png",
    "width": 64,
    "height": 32,
    "authorization_ref": "BRAND-AUTH/2026-0042",
    "reviewed_on": "2026-09-30",
}
# The record's reviewed pin, computed (a committed 64-hex literal would only
# restate it).
LENDER_IDENTITY_SHA256 = lender_identity_digest(json.dumps(LENDER_IDENTITY))
EXPOSURE = [
    {
        "kind": "workspace_permission",
        "securable": {
            "type": "GENIE_SPACE",
            "id": "space-alpha-0001",
            "name": "Another Team Space",
        },
        "grantee": "account users",
        "privileges": ["CAN_VIEW"],
        "source": "direct",
        "binding": {"isolation_mode": "OPEN", "workspace_ids": ["4200000001"]},
        "identities": ["app"],
        "privilege_class": "read",
        "reason": "Shared with every account user by its owner",
        "expires_at": "2026-12-01",
    },
    {
        "kind": "uc_grant",
        "securable": {"type": "CATALOG", "id": "foreign_catalog_alpha", "name": "foreign_catalog_alpha"},
        "grantee": "other-team-readers",
        "privileges": ["BROWSE", "USE_CATALOG"],
        "source": "inherited:metastore_parent_alpha",
        "binding": {"isolation_mode": "ISOLATED", "workspace_ids": ["4200000002"]},
        "identities": ["app", "runtime"],
        "privilege_class": "read",
        "reason": "Inherited browse on a shared catalog",
        "expires_at": "2026-12-01T00:00:00Z",
    },
]

DEMO_PROFILE: dict[str, str] = {
    "MIP_CLIENT": "fictional-demo",
    "MIP_PROFILE_VERSION": "1",
    "MIP_CLOUD": "aws",
    "MIP_IDENTITY_PREFIX": "mip",
    "MIP_INSTALL_CLASS": "demo",
    "MIP_APP_ENV": "sandbox",
    "MIP_AGENTIC_TIER": "on",
    "MIP_SCHEDULED_REFRESH": "on",
    "MIP_REVIEW_MODE": "single_maintainer_audited",
    "MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS": "1",
    "MIP_PRESENTER_MODE": "0",
    "MIP_LAKEBASE_READABLE_SECONDARIES": "false",
    "MIP_DEFAULT_CATALOG": "mip",
    "MIP_LAKEBASE_SYNC_CATALOG": "mip_app_state",
    "MIP_LAKEBASE_SYNC_SCHEMA": "mip_sync",
    "MIP_APP_NAME": "mip-app",
    "MIP_LAKEBASE_INSTANCE": "mip-app-state",
    "LAKEBASE_DATABASE": "mip_app_state",
    "MIP_RUNTIME_SECRET_SCOPE": "mip-runtime",
    "MIP_APP_ROLLBACK_SECRET_SCOPE": "mip-app-rollback",
    "MIP_AGENT_PROXY_SECRET_SCOPE": "mip-agent-proxy",
    "MIP_PII_SALT_SCOPE": "mip",
    "MIP_GENIE_SPACE_NAME": "Mortgage Lead Intelligence",
    "MIP_AGENT_SUPERVISOR_NAME": "Mortgage Growth Agent",
    "MIP_AI_GATEWAY_TABLE_PREFIX": "mip_agent_gateway_growth_agent",
    "MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE": "mip-agent-runtime-gateway-proxy",
    "MIP_COTALITY_SOURCE_CATALOG": "cotality_source_alpha",
    "MIP_COTALITY_SOURCE_SCHEMA": "property_alpha",
    "MIP_COTALITY_SOURCE_TABLE_PREFIX": "alpha_",
    "DATABRICKS_HOST": "https://dbc-unit.cloud.databricks.com",
    "MIP_EXPECT_WORKSPACE_ID": "4200000001",
    "MIP_EXPECT_METASTORE_ID": "00000000-0000-4000-8000-00000000a001",
    "DATABRICKS_ACCOUNT_ID": "00000000-0000-4000-8000-00000000b001",
    "DATABRICKS_ACCOUNT_HOST": "https://accounts.cloud.databricks.com",
    "MIP_IDENTITY_IDS_JSON": json.dumps(IDENTITY_IDS),
    "MIP_UC_METASTORE_OWNER": "metastore-owners-alpha",
    "MIP_UC_APPROVED_OWNER_PRINCIPALS": "mip-data-owners,owner@summit.example",
    "MIP_GENIE_MANAGER_PRINCIPALS": "genie-managers-alpha",
    "MIP_SCHEDULED_REFRESH_RECIPIENTS": "refresh-alerts@summit.example",
    "MIP_ADMIN_EMAILS": "admin@summit.example",
    "MIP_APPROVER_EMAILS": "approver@summit.example",
    "MIP_AUDITOR_EMAILS": "auditor@summit.example",
    "MIP_ADMIN_IDENTITIES": app_id(21),
    "MIP_APPROVER_IDENTITIES": app_id(22),
    "MIP_AUDITOR_IDENTITIES": app_id(23),
    "MIP_ADMIN_GROUP_NAME": "mip-admin",
    "MIP_APPROVER_GROUP_NAME": "mip-approver",
    "MIP_COTALITY_SHARE_PROVIDER": "provider_alpha",
    "MIP_COTALITY_SHARE_NAME": "share_alpha",
    "MIP_UC_CATALOG_STORAGE_ROOT": "s3://bucket-alpha/mip",
    "MIP_DASHBOARD_PARENT_PATH": "/Workspace/Shared/dashboards-alpha",
    "MIP_ACCEPTED_EXPOSURE_JSON": json.dumps(EXPOSURE),
    "MIP_SEED_OPERATOR_EMAIL": "operator@summit.example",
    "DATABRICKS_CLIENT_SECRET": "secret-normal-alpha",
    "DATABRICKS_OPERATOR2_CLIENT_SECRET": "secret-operator2-alpha",
    "DATABRICKS_ADMIN_CLIENT_SECRET": "secret-admin-alpha",
    "DATABRICKS_RELEASE_PROBE_CLIENT_SECRET": "secret-release-probe-alpha",
    "DATABRICKS_ACCOUNT_CLIENT_SECRET": "secret-account-alpha",
    "MIP_AI_GATEWAY_PROOF_SIGNING_KEY": "secret-proof-key-alpha",
    "DATABRICKS_VERIFIER_CLIENT_SECRET": "secret-verifier-alpha",
    "DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET": "secret-runtime-alpha",
    "DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE": "secret-proxy-bundle-alpha",
    "MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY": "secret-attestation-key-alpha",
}

_DEMO_ONLY = ("MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS", "MIP_PRESENTER_MODE", "MIP_SEED_OPERATOR_EMAIL")


def _apply(base: dict[str, str], overrides: dict[str, str | None]) -> dict[str, str]:
    values = dict(base)
    for name, value in overrides.items():
        if value is None:
            values.pop(name, None)
        else:
            values[name] = value
    return values


def demo_profile(**overrides: str | None) -> dict[str, str]:
    """The demo profile; an override of ``None`` removes that field."""

    return _apply(DEMO_PROFILE, overrides)


def customer_profile(**overrides: str | None) -> dict[str, str]:
    """The same install as a customer: no demo-only fields, a lender identity."""

    customer: dict[str, str | None] = {
        **dict.fromkeys(_DEMO_ONLY),
        "MIP_CLIENT": "fictional-customer",
        "MIP_INSTALL_CLASS": "customer",
        "MIP_APP_ENV": "prod",
        "MIP_LENDER_IDENTITY_JSON": json.dumps(LENDER_IDENTITY),
        "MIP_LENDER_IDENTITY_SHA256": LENDER_IDENTITY_SHA256,
    }
    return _apply(_apply(DEMO_PROFILE, customer), overrides)


def split_contexts(values: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """(vars, secrets) as a GitHub Environment holds them: by field class."""

    variables = {name: v for name, v in values.items() if FIELD_CLASS[name] is FieldClass.PUBLIC}
    secrets = {name: v for name, v in values.items() if FIELD_CLASS[name] is not FieldClass.PUBLIC}
    return variables, secrets
