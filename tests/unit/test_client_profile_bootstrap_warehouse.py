"""The profile's MIP_BOOTSTRAP_WAREHOUSE_ID: an optional identifying field, a first install's only (B1).

It names the SQL warehouse a new client's first install borrows until its
bundle apply creates the bundle's own (tools/databricks/bundle_state.py
``bootstrap-warehouse-id`` proves it read-only). What this file pins:

* its shape (16 lowercase hex characters, refused by name, never by value);
* its class: identifying, so a GitHub Environment secret, refused from
  ``vars``, masked, digested; unset, it leaves every existing profile's
  canonical env and digest exactly as they were (client #1's ack included);
* CI carries it: ``ci_profile.env_local_text`` writes it into ``.env.local``,
  and ``github_environment`` stores it as an environment secret;
* it reaches nothing else: no App env, no bundle variable, no client-target
  export;
* it never stands beside ``MIP_LEGACY_BUNDLE_ROOT``: an adopted install keeps
  the warehouse its legacy state binds.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.client_profiles import DEMO_PROFILE, demo_profile, split_contexts
from tools.clients import ci_profile
from tools.clients import profile as views
from tools.clients.environment import RawProfile, from_github, from_local, resolve
from tools.clients.errors import ProfileRefusal
from tools.clients.schema import FIELD_CLASS, ClientProfile, FieldClass

FIELD = "MIP_BOOTSTRAP_WAREHOUSE_ID"
BOOT = "b0b1b2b3b4b5b6b7"
LEGACY_ROOT = "/Workspace/Users/legacy-owner@lender.example/.bundle/mortgage-intelligence-platform/legacy"
TEMPLATE = Path(__file__).resolve().parents[2] / "deploy" / "clients" / "example.env"


def _profile(**overrides: str | None) -> ClientProfile:
    return resolve(RawProfile(values=demo_profile(**overrides)))


def _refusals(**overrides: str | None) -> list[str]:
    with pytest.raises(ProfileRefusal) as caught:
        _profile(**overrides)
    return list(caught.value.lines)


# --- shape and class ----------------------------------------------------------------


@pytest.mark.parametrize("value", [BOOT, "0123456789abcdef", "0000000000000000"])
def test_a_warehouse_id_of_16_lowercase_hex_characters_is_accepted(value: str) -> None:
    assert _profile(MIP_BOOTSTRAP_WAREHOUSE_ID=value).bootstrap_warehouse_id == value


@pytest.mark.parametrize(
    "value",
    [BOOT.upper(), BOOT[:15], f"{BOOT}0", "g0b1b2b3b4b5b6b7", "b0b1b2b3 b4b5b6b", "b0b1-b2b3-b4b5-b6", "warehouse-1"],
    ids=["uppercase", "15", "17", "non-hex", "inner-space", "dashes", "a-name"],
)
def test_any_other_shape_refuses_by_name_never_by_value(value: str) -> None:
    lines = _refusals(MIP_BOOTSTRAP_WAREHOUSE_ID=value)

    assert lines == [f"{FIELD}: must be a SQL warehouse id (16 lowercase hex characters)"]
    assert value not in "\n".join(lines)


def test_the_field_is_identifying_optional_and_unset_by_default() -> None:
    assert FIELD_CLASS[FIELD] is FieldClass.IDENTIFYING
    assert ClientProfile.env_name("bootstrap_warehouse_id") == FIELD
    assert FIELD not in DEMO_PROFILE and _profile().bootstrap_warehouse_id is None


def test_github_refuses_it_from_vars_and_takes_it_from_secrets() -> None:
    variables, secrets = split_contexts(demo_profile(MIP_BOOTSTRAP_WAREHOUSE_ID=BOOT))
    assert FIELD in secrets and FIELD not in variables

    assert resolve(from_github(variables, secrets, environment="fictional-demo")).bootstrap_warehouse_id == BOOT
    misfiled = {**variables, FIELD: BOOT}
    secrets.pop(FIELD)
    with pytest.raises(ProfileRefusal) as caught:
        resolve(from_github(misfiled, secrets, environment="fictional-demo"))
    assert f"{FIELD}: is identifying; store it as an environment secret, never a variable" in caught.value.lines
    assert BOOT not in "\n".join(caught.value.lines)


def test_it_is_masked_and_digested_and_unset_it_leaves_the_canonical_env_as_it_was() -> None:
    unset, configured = _profile(), _profile(MIP_BOOTSTRAP_WAREHOUSE_ID=BOOT)

    assert BOOT in views.masks(configured) and BOOT not in views.masks(unset)
    assert views.canonical_env(configured) == {**views.canonical_env(unset), FIELD: BOOT}
    assert views.digest(configured) != views.digest(unset)
    # Unset, the field is no key at all: every existing profile digests as before.
    assert FIELD not in views.canonical_env(unset) and FIELD not in views.deploy_controls(unset)


# --- what carries it, and what never does ------------------------------------------------


def test_ci_writes_it_into_env_local_and_never_a_databricks_warehouse_id() -> None:
    text = ci_profile.env_local_text(_profile(MIP_BOOTSTRAP_WAREHOUSE_ID=BOOT))

    assert f"{FIELD}='{BOOT}'\n" in text
    assert "DATABRICKS_WAREHOUSE_ID" not in text
    assert FIELD not in ci_profile.env_local_text(_profile())


def test_it_reaches_no_app_env_bundle_variable_or_client_target_export() -> None:
    profile = _profile(MIP_BOOTSTRAP_WAREHOUSE_ID=BOOT)

    for view in (views.app_payload_env, views.bundle_vars, views.client_target, views.client_target_vars):
        values = view(profile)
        assert not any("BOOTSTRAP_WAREHOUSE" in name.upper() for name in values), view.__name__
        assert BOOT not in values.values(), view.__name__
        assert values == view(_profile()), f"{view.__name__} is the same with the field unset"


def test_github_environment_stores_it_as_an_environment_secret(tmp_path: Path) -> None:
    from tests.fixtures.fake_gh import install_fake_gh
    from tests.fixtures.github_environment_harness import plan_and_apply, write_profile
    from tools.clients.github_environment_plan import PROFILE_DIGEST_VARIABLE

    fake = install_fake_gh(tmp_path / "github")
    env = write_profile(tmp_path / "input" / "client.env", demo_profile(MIP_BOOTSTRAP_WAREHOUSE_ID=BOOT))

    planned, applied = plan_and_apply(fake, env)

    assert applied.code == 0, applied.err
    deploy = fake.environment("fictional-demo")
    assert FIELD in deploy["secrets"] and FIELD not in deploy["variables"]
    assert BOOT not in planned.out + planned.err + applied.out + applied.err
    assert fake.state()["violations"] == []
    assert views.digest(resolve(from_local({}, env))) == deploy["variables"][PROFILE_DIGEST_VARIABLE]["value"]


# --- never beside an adopted install's legacy root ------------------------------------------


def test_it_refuses_beside_a_real_legacy_bundle_root() -> None:
    lines = _refusals(
        MIP_BOOTSTRAP_WAREHOUSE_ID=BOOT,
        MIP_LEGACY_BUNDLE_ROOT=LEGACY_ROOT,
        MIP_DASHBOARD_PARENT_PATH=f"{LEGACY_ROOT}/dashboards",
    )

    assert lines == [
        f"{FIELD}: must be unset when MIP_LEGACY_BUNDLE_ROOT is set: an adopted install keeps the SQL "
        "warehouse its legacy bundle state binds"
    ]
    # Positive control: the same adopted profile without the field resolves.
    adopted = _profile(MIP_LEGACY_BUNDLE_ROOT=LEGACY_ROOT, MIP_DASHBOARD_PARENT_PATH=f"{LEGACY_ROOT}/dashboards")
    assert adopted.legacy_bundle_root == LEGACY_ROOT


def test_the_template_holds_both_placeholders_and_still_validates_as_a_template() -> None:
    raw = from_local({}, TEMPLATE)
    assert raw.values[FIELD] == "<bootstrap-warehouse-id>"
    assert raw.values["MIP_LEGACY_BUNDLE_ROOT"] == "<legacy-bundle-root>"

    template = resolve(raw, allow_placeholders=True)

    assert template.bootstrap_warehouse_id == "<bootstrap-warehouse-id>"
    with pytest.raises(ProfileRefusal) as caught:
        resolve(raw)
    assert f"{FIELD}: holds a <placeholder>; set a real value" in caught.value.lines
    assert not [line for line in caught.value.lines if "must be unset when" in line], "a placeholder never fires"
