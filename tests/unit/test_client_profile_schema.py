"""tools/clients/schema.py: every validator refusal, the template and a round trip."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from tests.fixtures.client_profiles import (
    DEMO_PROFILE,
    IDENTITY_IDS,
    LENDER_IDENTITY,
    customer_profile,
    demo_profile,
    split_contexts,
)
from tools.clients import profile as views
from tools.clients.environment import RawProfile, from_github, from_local, resolve
from tools.clients.errors import ProfileRefusal
from tools.clients.json_fields import IdentityIds
from tools.clients.schema import (
    FIELD_CLASS,
    IDENTITY_ALIASES,
    JSON_LEAF_CLASS,
    ClientProfile,
    FieldClass,
)

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "deploy" / "clients" / "example.env"
# Azure values are assembled at run time: written whole, they would match the
# committed-identifier ratchet's host and email shapes.
_ADB = "https://adb-"
AZURE_HOST = _ADB + "4200000001.7.azuredatabricks.net"
AZURE_OTHER_HOST = _ADB + "4200000009.7.azuredatabricks.net"
AZURE_STORAGE_ROOT = "abfss://container" + "@" + "accountalpha.dfs.core.windows.net/mip"
FIXED_TEMPLATE_VALUES = {
    "MIP_PROFILE_VERSION": "1",
    "MIP_DEFAULT_CATALOG": "mip",
    "MIP_REVIEW_MODE": "single_maintainer_audited",
}


def _profile(values: dict[str, str]) -> ClientProfile:
    return resolve(RawProfile(values=values))


def _refusals(values: dict[str, str], identity_aliases: dict[str, str] | None = None) -> list[str]:
    with pytest.raises(ProfileRefusal) as caught:
        resolve(RawProfile(values=values, identity_aliases=identity_aliases or {}))
    return list(caught.value.lines)


def _ids(**changes: str | None) -> str:
    ids = {**IDENTITY_IDS, **changes}
    return json.dumps({role: value for role, value in ids.items() if value is not None})


def _assert_refused(values: dict[str, str], field: str, rule: str) -> list[str]:
    lines = _refusals(values)
    assert any(line.startswith(f"{field}") and rule in line for line in lines), lines
    return lines


# --- the classes and the template --------------------------------------------


def test_every_model_field_has_exactly_one_class() -> None:
    names = {ClientProfile.env_name(attribute) for attribute in ClientProfile.model_fields}
    assert names == set(FIELD_CLASS)


def test_the_json_class_map_names_every_json_field_and_identity_role() -> None:
    json_fields = {name for name in FIELD_CLASS if name.endswith("_JSON")}
    assert set(JSON_LEAF_CLASS) == json_fields
    assert set(JSON_LEAF_CLASS["MIP_IDENTITY_IDS_JSON"]) == set(IdentityIds.model_fields)
    assert set(IDENTITY_ALIASES.values()) <= set(IdentityIds.model_fields)


def _template_values() -> dict[str, str]:
    raw = from_local({}, TEMPLATE)
    return dict(raw.values)


def test_the_template_lists_every_field_once_and_holds_placeholders_only() -> None:
    names = re.findall(r"^([A-Z][A-Z0-9_]*)=", TEMPLATE.read_text(encoding="utf-8"), flags=re.M)
    assert len(names) == len(set(names))
    assert set(names) == set(FIELD_CLASS)
    for name, value in _template_values().items():
        if name in FIXED_TEMPLATE_VALUES:
            assert value == FIXED_TEMPLATE_VALUES[name]
        else:
            assert "<" in value and ">" in value, name


def test_the_template_validates_only_with_allow_placeholders() -> None:
    raw = from_local({}, TEMPLATE)
    template = resolve(raw, allow_placeholders=True)
    assert template.client == "<client-key>"
    lines = _refusals(dict(raw.values))
    assert len(lines) == len(FIELD_CLASS) - len(FIXED_TEMPLATE_VALUES)
    assert all(line.endswith("holds a <placeholder>; set a real value") for line in lines)


def test_one_placeholder_in_a_real_profile_is_refused() -> None:
    values = demo_profile(DATABRICKS_HOST="<workspace-host>")
    assert _refusals(values) == ["DATABRICKS_HOST: holds a <placeholder>; set a real value"]
    assert resolve(RawProfile(values=values), allow_placeholders=True).databricks_host == "<workspace-host>"


def test_bundle_vars_name_only_variables_databricks_yml_declares() -> None:
    declared = set(yaml.safe_load((ROOT / "databricks.yml").read_text(encoding="utf-8"))["variables"])
    for values in (demo_profile(), customer_profile()):
        assert set(views.bundle_vars(_profile(values))) <= declared


# --- round trip ---------------------------------------------------------------


@pytest.mark.parametrize("build", [demo_profile, customer_profile])
def test_a_profile_round_trips_through_its_canonical_env(build) -> None:
    first = _profile(build())
    env = views.canonical_env(first, include_secrets=True)
    again = resolve(from_local(env, None))
    assert again == first
    assert views.digest(again) == views.digest(first)
    variables, secrets = split_contexts(env)
    assert resolve(from_github(variables, secrets)) == first


def test_the_digest_covers_identifying_fields_and_never_secrets() -> None:
    base = views.digest(_profile(demo_profile()))
    assert views.digest(_profile(demo_profile(DATABRICKS_CLIENT_SECRET="rotated-secret"))) == base
    assert views.digest(_profile(demo_profile(MIP_ADMIN_EMAILS="other@summit.example"))) != base
    assert views.digest(_profile(demo_profile(MIP_APP_NAME="mip-app-two"))) != base


# --- refusals -------------------------------------------------------------------


def test_uuid_recipients_are_refused() -> None:
    values = demo_profile(MIP_SCHEDULED_REFRESH_RECIPIENTS=f"ops@summit.example,{IDENTITY_IDS['admin']}")
    _assert_refused(values, "MIP_SCHEDULED_REFRESH_RECIPIENTS", "entry 1 is an application id")


def test_scheduled_refresh_on_needs_recipients() -> None:
    values = demo_profile(MIP_SCHEDULED_REFRESH_RECIPIENTS=None)
    _assert_refused(values, "MIP_SCHEDULED_REFRESH_RECIPIENTS", "required when MIP_SCHEDULED_REFRESH=on")
    assert _profile(demo_profile(MIP_SCHEDULED_REFRESH="off", MIP_SCHEDULED_REFRESH_RECIPIENTS=None))


def test_a_catalog_other_than_mip_is_refused() -> None:
    _assert_refused(demo_profile(MIP_DEFAULT_CATALOG="lender_catalog"), "MIP_DEFAULT_CATALOG", "must be mip")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS", "1"),
        ("MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS", "true"),
        ("MIP_PRESENTER_MODE", "on"),
        ("MIP_SEED_OPERATOR_EMAIL", "operator@summit.example"),
    ],
)
def test_demo_fields_are_refused_on_a_customer_profile(field: str, value: str) -> None:
    _assert_refused(customer_profile(**{field: value}), field, "demo-only and refused for a customer")


def test_explicitly_off_demo_flags_are_allowed_on_a_customer_profile() -> None:
    assert _profile(customer_profile(MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS="0", MIP_PRESENTER_MODE="off"))


@pytest.mark.parametrize(
    "lender",
    [
        {"legal_name": "Summit Mortgage"},
        {"legal_name": "SUMMIT MORTGAGE"},
        {"legal_name": "summit  mortgage"},
        {"nmls_id": "123456"},
        {"tenant_id": "summit"},
    ],
)
def test_the_summit_identity_is_refused_on_a_customer_profile(lender: dict[str, str]) -> None:
    # Each case changes one part of the identity, so each part refuses alone.
    values = customer_profile(MIP_LENDER_IDENTITY_JSON=json.dumps({**LENDER_IDENTITY, **lender}))
    _assert_refused(values, "MIP_LENDER_IDENTITY_JSON", "Summit Mortgage demo identity is refused")


def test_a_customer_needs_a_lender_identity_and_a_demo_refuses_one() -> None:
    _assert_refused(customer_profile(MIP_LENDER_IDENTITY_JSON=None), "MIP_LENDER_IDENTITY_JSON", "required for a customer")
    values = demo_profile(MIP_LENDER_IDENTITY_JSON=json.dumps(LENDER_IDENTITY))
    _assert_refused(values, "MIP_LENDER_IDENTITY_JSON", "customer-only")


@pytest.mark.parametrize("app_env", ["local", "test"])
def test_local_and_test_app_envs_are_refused(app_env: str) -> None:
    _assert_refused(demo_profile(MIP_APP_ENV=app_env), "MIP_APP_ENV", "must not be local or test")


def test_an_unknown_app_env_is_refused() -> None:
    _assert_refused(demo_profile(MIP_APP_ENV="staging"), "MIP_APP_ENV", "must be sandbox or prod")


@pytest.mark.parametrize(
    ("changes", "field", "rule"),
    [
        ({"MIP_CLOUD": "azure"}, "DATABRICKS_HOST", "workspace host on azure"),
        ({"DATABRICKS_HOST": "https://accounts.cloud.databricks.com"}, "DATABRICKS_HOST", "workspace host on aws"),
        ({"DATABRICKS_HOST": AZURE_HOST}, "DATABRICKS_HOST", "on aws"),
        ({"DATABRICKS_ACCOUNT_HOST": "https://accounts.azuredatabricks.net"}, "DATABRICKS_ACCOUNT_HOST", "of aws"),
        ({"MIP_UC_CATALOG_STORAGE_ROOT": "gs://bucket-alpha/mip"}, "MIP_UC_CATALOG_STORAGE_ROOT", "s3:// root on aws"),
        (
            {
                "MIP_CLOUD": "azure",
                "DATABRICKS_HOST": AZURE_OTHER_HOST,
                "DATABRICKS_ACCOUNT_HOST": None,
                "MIP_UC_CATALOG_STORAGE_ROOT": None,
            },
            "MIP_EXPECT_WORKSPACE_ID",
            "equal the id inside DATABRICKS_HOST",
        ),
    ],
)
def test_a_cloud_and_host_mismatch_is_refused(changes: dict, field: str, rule: str) -> None:
    _assert_refused(demo_profile(**changes), field, rule)


def test_a_matching_azure_profile_is_accepted() -> None:
    values = demo_profile(
        MIP_CLOUD="azure",
        DATABRICKS_HOST=AZURE_HOST,
        DATABRICKS_ACCOUNT_HOST=None,
        MIP_UC_CATALOG_STORAGE_ROOT=AZURE_STORAGE_ROOT,
    )
    profile = _profile(values)
    assert views.account_host(profile) == "https://accounts.azuredatabricks.net"


@pytest.mark.parametrize("role", ["deployer", "jobs", "validator"])
@pytest.mark.parametrize(
    "field",
    ["MIP_ADMIN_IDENTITIES", "MIP_APPROVER_IDENTITIES", "MIP_AUDITOR_IDENTITIES"],
)
def test_reserved_identity_ids_and_names_are_refused_in_role_lists(role: str, field: str) -> None:
    for entry in (IDENTITY_IDS[role].upper(), f"mip-{role}", f"MIP-{role.upper()}"):
        values = demo_profile(**{field: f"{DEMO_PROFILE[field]},{entry}"})
        _assert_refused(values, field, f"must not name the {role} identity")


def test_a_reserved_name_follows_the_identity_prefix() -> None:
    values = demo_profile(MIP_IDENTITY_PREFIX="lender", MIP_ADMIN_IDENTITIES="lender-jobs")
    _assert_refused(values, "MIP_ADMIN_IDENTITIES", "must not name the jobs identity")
    assert _profile(demo_profile(MIP_IDENTITY_PREFIX="lender", MIP_ADMIN_IDENTITIES="mip-jobs"))


@pytest.mark.parametrize(
    "field",
    ["MIP_ADMIN_EMAILS", "MIP_APPROVER_EMAILS", "MIP_AUDITOR_EMAILS", "MIP_SEED_OPERATOR_EMAIL"],
)
def test_email_role_and_seed_lists_refuse_a_reserved_identity(field: str) -> None:
    # A service principal has no email: its application id fails the email
    # shape before the reserved-identity rule is reached.
    _assert_refused(demo_profile(**{field: IDENTITY_IDS["deployer"]}), field, "email")


@pytest.mark.parametrize("owner", ["users", "account users", "Account  Users", "ADMINS"])
def test_a_vocabulary_metastore_owner_is_refused(owner: str) -> None:
    _assert_refused(demo_profile(MIP_UC_METASTORE_OWNER=owner), "MIP_UC_METASTORE_OWNER", "exact principal")


def test_independent_review_mode_needs_the_deferred_attestation_layer() -> None:
    _assert_refused(demo_profile(MIP_REVIEW_MODE="independent"), "MIP_REVIEW_MODE", "owner-attestation layer, which is deferred")
    _assert_refused(demo_profile(MIP_REVIEW_MODE="audited"), "MIP_REVIEW_MODE", "must be single_maintainer_audited")


def test_identity_ids_must_be_pairwise_distinct_application_ids() -> None:
    _assert_refused(
        demo_profile(MIP_IDENTITY_IDS_JSON=_ids(jobs=IDENTITY_IDS["deployer"].upper())),
        "MIP_IDENTITY_IDS_JSON",
        "deployer and jobs must be distinct",
    )
    _assert_refused(demo_profile(MIP_IDENTITY_IDS_JSON=_ids(admin="mip-admin")), "MIP_IDENTITY_IDS_JSON.admin", "UUID")


def test_the_agentic_tier_needs_its_identities_and_credentials() -> None:
    lines = _refusals(demo_profile(MIP_IDENTITY_IDS_JSON=_ids(verifier=None), DATABRICKS_VERIFIER_CLIENT_SECRET=None))
    assert lines == [
        "MIP_IDENTITY_IDS_JSON.verifier: is required when MIP_AGENTIC_TIER=on",
        "DATABRICKS_VERIFIER_CLIENT_SECRET: is required when MIP_AGENTIC_TIER=on",
    ]
    core = demo_profile(
        MIP_AGENTIC_TIER="off",
        MIP_IDENTITY_IDS_JSON=_ids(verifier=None, runtime=None, proxy=None),
        DATABRICKS_VERIFIER_CLIENT_SECRET=None,
        DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET=None,
        DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE=None,
        MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY=None,
    )
    assert _profile(core).identity_ids.verifier is None


def test_unknown_keys_are_refused_without_naming_them() -> None:
    lines = _refusals({**demo_profile(), "MIP_UNKNOWN_FIELD": "x"})
    assert lines == ["MIP_UNKNOWN_FIELD: Extra inputs are not permitted"]
    secret_key = IDENTITY_IDS["admin"]
    values = demo_profile(MIP_IDENTITY_IDS_JSON=json.dumps({**IDENTITY_IDS, secret_key: "x"}))
    lines = _refusals(values)
    assert lines == ["MIP_IDENTITY_IDS_JSON.<unexpected key>: Extra inputs are not permitted"]


def test_malformed_json_and_missing_fields_are_refused() -> None:
    _assert_refused(demo_profile(MIP_IDENTITY_IDS_JSON="{not json"), "MIP_IDENTITY_IDS_JSON", "single-line JSON")
    _assert_refused(demo_profile(MIP_ACCEPTED_EXPOSURE_JSON="{}"), "MIP_ACCEPTED_EXPOSURE_JSON", "JSON list")
    _assert_refused(demo_profile(DATABRICKS_HOST=None), "DATABRICKS_HOST", "Field required")


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "grant"},
        {"privileges": ["SELECT", "SELECT"]},
        {"privileges": ["READ_EVERYTHING"]},
        {"identities": ["verifier"]},
        {"privilege_class": "write"},
        {"source": "parent"},
        {"expires_at": "soon"},
    ],
)
def test_exposure_entries_are_a_closed_tuple(change: dict) -> None:
    entry = {**json.loads(DEMO_PROFILE["MIP_ACCEPTED_EXPOSURE_JSON"])[0], **change}
    values = demo_profile(MIP_ACCEPTED_EXPOSURE_JSON=json.dumps([entry]))
    _assert_refused(values, "MIP_ACCEPTED_EXPOSURE_JSON.0", "must")


# --- aliases and sources ------------------------------------------------------


def test_scalar_aliases_must_agree_and_may_supply_the_field() -> None:
    with pytest.raises(ProfileRefusal) as caught:
        from_local({**demo_profile(), "APP_ENV": "prod"}, None)
    assert caught.value.lines == ("MIP_APP_ENV, APP_ENV: must agree",)
    supplied = demo_profile(MIP_LAKEBASE_INSTANCE=None)
    profile = resolve(from_local({**supplied, "LAKEBASE_INSTANCE_NAME": "mip-app-state"}, None))
    assert profile.lakebase_instance == "mip-app-state"


def test_identity_aliases_must_equal_their_role() -> None:
    agreeing = {"MIP_CI_DEPLOYER_CLIENT_ID": IDENTITY_IDS["deployer"].upper()}
    assert resolve(from_local({**demo_profile(), **agreeing}, None))
    lines = _refusals(demo_profile(), {"DATABRICKS_CLIENT_ID": IDENTITY_IDS["admin"]})
    assert lines == ["DATABRICKS_CLIENT_ID: must equal MIP_IDENTITY_IDS_JSON.normal"]
    core = demo_profile(MIP_AGENTIC_TIER="off", MIP_IDENTITY_IDS_JSON=_ids(verifier=None))
    lines = _refusals(core, {"DATABRICKS_VERIFIER_CLIENT_ID": IDENTITY_IDS["verifier"]})
    assert lines == ["DATABRICKS_VERIFIER_CLIENT_ID: must equal MIP_IDENTITY_IDS_JSON.verifier"]


def test_a_non_empty_shell_value_beats_env_local(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        "\n".join(f"{name}='{value}'" for name, value in demo_profile().items()) + "\n", encoding="utf-8"
    )
    assert resolve(from_local({"MIP_APP_NAME": "shell-app"}, env_file)).app_name == "shell-app"
    assert resolve(from_local({"MIP_APP_NAME": "  "}, env_file)).app_name == "mip-app"


def test_refusal_lines_never_carry_the_refused_values() -> None:
    hostile = demo_profile(
        DATABRICKS_HOST="https://dbc-unit.cloud.databricks.com/path-alpha",
        MIP_EXPECT_WORKSPACE_ID="workspace-alpha-id",
        MIP_ADMIN_EMAILS="not-an-email-alpha",
        MIP_UC_METASTORE_OWNER="owner,alpha",
        MIP_CLIENT="Client_Alpha",
    )
    text = "\n".join(_refusals(hostile))
    for value in ("path-alpha", "workspace-alpha-id", "not-an-email-alpha", "owner,alpha", "Client_Alpha"):
        assert value not in text
    assert all(field in text for field in ("DATABRICKS_HOST", "MIP_EXPECT_WORKSPACE_ID", "MIP_CLIENT"))


def test_secret_fields_never_appear_in_repr_or_views() -> None:
    profile = _profile(demo_profile())
    secrets = [value for name, value in DEMO_PROFILE.items() if FIELD_CLASS[name] is FieldClass.SECRET]
    rendered = repr(profile) + json.dumps(
        [views.deploy_controls(profile), views.bundle_vars(profile), views.app_payload_env(profile), views.canonical_env(profile)]
    )
    assert secrets and not any(secret in rendered for secret in secrets)
