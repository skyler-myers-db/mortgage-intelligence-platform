"""The profile's class maps, pinned to a hand-written copy of plan §5.

``FIELD_CLASS`` and ``JSON_LEAF_CLASS`` decide which values the resolver
refuses from ``vars`` and which leaves ``masks()`` registers (plan threat
T2). The other profile tests read their oracles from those same maps, so a
reclassification would pass them. This file holds an independent literal
copy: reclassifying a field or leaf needs a deliberate edit here too.

The literals are the §5 table plus the PR's declared deviations
(``MIP_DASHBOARD_PARENT_PATH``, ``MIP_APPROVER_EMAILS`` and the
``MIP_*_IDENTITIES`` lists are identifying; the demo flags and
``MIP_LAKEBASE_READABLE_SECONDARIES`` are public).
"""

from __future__ import annotations

import re
from pathlib import Path

from tools.clients.schema import (
    ALIASES,
    FIELD_CLASS,
    IDENTITY_ALIASES,
    JSON_LEAF_CLASS,
    FieldClass,
    LeafClass,
    name_class,
)

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "deploy" / "clients" / "example.env"

EXPECTED_PUBLIC = frozenset(
    {
        "MIP_CLIENT",
        "MIP_PROFILE_VERSION",
        "MIP_CLOUD",
        "MIP_IDENTITY_PREFIX",
        "MIP_INSTALL_CLASS",
        "MIP_APP_ENV",
        "MIP_AGENTIC_TIER",
        "MIP_SCHEDULED_REFRESH",
        "MIP_REVIEW_MODE",
        "MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS",
        "MIP_PRESENTER_MODE",
        "MIP_LAKEBASE_READABLE_SECONDARIES",
        "MIP_DEFAULT_CATALOG",
        "MIP_LAKEBASE_SYNC_CATALOG",
        "MIP_LAKEBASE_SYNC_SCHEMA",
        "MIP_APP_NAME",
        "MIP_LAKEBASE_INSTANCE",
        "LAKEBASE_DATABASE",
        "MIP_RUNTIME_SECRET_SCOPE",
        "MIP_APP_ROLLBACK_SECRET_SCOPE",
        "MIP_AGENT_PROXY_SECRET_SCOPE",
        "MIP_PII_SALT_SCOPE",
        "MIP_GENIE_SPACE_NAME",
        "MIP_AGENT_SUPERVISOR_NAME",
        "MIP_AI_GATEWAY_TABLE_PREFIX",
        "MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE",
        "MIP_COTALITY_SOURCE_CATALOG",
        "MIP_COTALITY_SOURCE_SCHEMA",
        "MIP_COTALITY_SOURCE_TABLE_PREFIX",
    }
)
EXPECTED_IDENTIFYING = frozenset(
    {
        "DATABRICKS_HOST",
        "MIP_EXPECT_WORKSPACE_ID",
        "MIP_EXPECT_METASTORE_ID",
        "DATABRICKS_ACCOUNT_ID",
        "DATABRICKS_ACCOUNT_HOST",
        "MIP_IDENTITY_IDS_JSON",
        "MIP_UC_METASTORE_OWNER",
        "MIP_UC_APPROVED_OWNER_PRINCIPALS",
        "MIP_GENIE_MANAGER_PRINCIPALS",
        "MIP_SCHEDULED_REFRESH_RECIPIENTS",
        "MIP_ADMIN_EMAILS",
        "MIP_APPROVER_EMAILS",
        "MIP_AUDITOR_EMAILS",
        "MIP_ADMIN_IDENTITIES",
        "MIP_APPROVER_IDENTITIES",
        "MIP_AUDITOR_IDENTITIES",
        "MIP_ADMIN_GROUP_NAME",
        "MIP_APPROVER_GROUP_NAME",
        "MIP_COTALITY_SHARE_PROVIDER",
        "MIP_COTALITY_SHARE_NAME",
        "MIP_UC_CATALOG_STORAGE_ROOT",
        "MIP_DASHBOARD_PARENT_PATH",
        "MIP_LENDER_IDENTITY_JSON",
        "MIP_ACCEPTED_EXPOSURE_JSON",
        "MIP_SEED_OPERATOR_EMAIL",
    }
)
EXPECTED_SECRET = frozenset(
    {
        "DATABRICKS_CLIENT_SECRET",
        "DATABRICKS_OPERATOR2_CLIENT_SECRET",
        "DATABRICKS_ADMIN_CLIENT_SECRET",
        "DATABRICKS_RELEASE_PROBE_CLIENT_SECRET",
        "DATABRICKS_ACCOUNT_CLIENT_SECRET",
        "MIP_AI_GATEWAY_PROOF_SIGNING_KEY",
        "DATABRICKS_VERIFIER_CLIENT_SECRET",
        "DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET",
        "DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE",
        "MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY",
    }
)

_ID, _VOCAB, _DATE = LeafClass.IDENTIFYING, LeafClass.VOCABULARY, LeafClass.DATE
EXPECTED_LEAF_CLASS = {
    "MIP_IDENTITY_IDS_JSON": {
        role: _ID
        for role in (
            "deployer",
            "jobs",
            "validator",
            "normal",
            "operator2",
            "admin",
            "release_probe",
            "verifier",
            "runtime",
            "proxy",
            "account_principal",
        )
    },
    "MIP_LENDER_IDENTITY_JSON": {
        "version": LeafClass.PUBLIC,
        "legal_name": _ID,
        "nmls_id": _ID,
        "tenant_id": _ID,
        "servicer_aliases[]": _ID,
        "reviewed_by": _ID,
        "reviewed_at": _DATE,
    },
    "MIP_ACCEPTED_EXPOSURE_JSON": {
        "[].kind": _VOCAB,
        "[].securable.type": _VOCAB,
        "[].securable.id": _ID,
        "[].securable.name": _ID,
        "[].grantee": _ID,
        "[].privileges[]": _VOCAB,
        "[].source": _ID,
        "[].binding.isolation_mode": _VOCAB,
        "[].binding.workspace_ids[]": _ID,
        "[].identities[]": _VOCAB,
        "[].privilege_class": _VOCAB,
        "[].reason": _ID,
        "[].expires_at": _DATE,
    },
}

# Every name the deploy tooling reads for one identity role; each is
# identifying, so ``vars`` refuses it like the JSON field it mirrors.
EXPECTED_IDENTITY_ALIASES = {
    "MIP_CI_DEPLOYER_CLIENT_ID": "deployer",
    "DATABRICKS_CLIENT_ID": "normal",
    "DATABRICKS_OPERATOR2_CLIENT_ID": "operator2",
    "DATABRICKS_ADMIN_CLIENT_ID": "admin",
    "DATABRICKS_RELEASE_PROBE_CLIENT_ID": "release_probe",
    "DATABRICKS_VERIFIER_CLIENT_ID": "verifier",
    "MIP_AI_GATEWAY_VERIFIER_CLIENT_ID": "verifier",
    "DATABRICKS_AGENT_RUNTIME_CLIENT_ID": "runtime",
    "MIP_AGENT_RUNTIME_CLIENT_ID": "runtime",
    "DATABRICKS_AGENT_PROXY_CLIENT_ID": "proxy",
    "MIP_AGENT_PROXY_CLIENT_ID": "proxy",
    "DATABRICKS_ACCOUNT_CLIENT_ID": "account_principal",
}
EXPECTED_ALIASES = {
    "MIP_APP_ENV": ("APP_ENV",),
    "MIP_LAKEBASE_INSTANCE": ("LAKEBASE_INSTANCE_NAME",),
    "LAKEBASE_DATABASE": ("MIP_LAKEBASE_DATABASE_NAME",),
}


def _expected_field_class() -> dict[str, FieldClass]:
    expected: dict[str, FieldClass] = {}
    for names, kind in (
        (EXPECTED_PUBLIC, FieldClass.PUBLIC),
        (EXPECTED_IDENTIFYING, FieldClass.IDENTIFYING),
        (EXPECTED_SECRET, FieldClass.SECRET),
    ):
        for name in names:
            assert name not in expected, f"{name} is listed in two classes"
            expected[name] = kind
    return expected


def test_every_field_has_its_hand_written_class() -> None:
    assert _expected_field_class() == FIELD_CLASS


def test_every_json_leaf_has_its_hand_written_class() -> None:
    assert JSON_LEAF_CLASS == EXPECTED_LEAF_CLASS


def test_alias_names_keep_their_classes() -> None:
    assert IDENTITY_ALIASES == EXPECTED_IDENTITY_ALIASES
    assert ALIASES == EXPECTED_ALIASES
    for name in EXPECTED_IDENTITY_ALIASES:
        assert name_class(name) is FieldClass.IDENTIFYING, name
    for canonical, aliases in EXPECTED_ALIASES.items():
        for alias in aliases:
            assert name_class(alias) is _expected_field_class()[canonical], alias


def test_the_template_files_each_field_under_its_class_heading() -> None:
    headings = {"public": FieldClass.PUBLIC, "identifying": FieldClass.IDENTIFYING, "secret": FieldClass.SECRET}
    section: FieldClass | None = None
    filed: dict[str, FieldClass] = {}
    for line in TEMPLATE.read_text(encoding="utf-8").splitlines():
        heading = re.match(r"# ---- (\w+):", line)
        if heading:
            section = headings[heading.group(1)]
            continue
        field = re.match(r"([A-Z][A-Z0-9_]*)=", line)
        if field:
            assert section is not None, f"{field.group(1)} precedes every class heading"
            filed[field.group(1)] = section
    assert filed == _expected_field_class()
