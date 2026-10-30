"""--rotate-adopted and --post-first-install against a fake account.

Rotation mints one secret per pinned M2M identity, hands it to the sink
only, confirms and probes it, then revokes exactly the secrets its plan
listed. The agent proxy is the exception: its field gets the canonical
credential bundle the deploy parses, and its older credential stays for the
signed deploy's blue/green retirement. A canary secret value must never
reach stdout, stderr, a log record, the plan or an exception message, on
the happy path or any failure path.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.fixtures.bootstrap_harness import (
    RESERVED_CORE,
    FakeSink,
    plan_and_apply,
    run_tool,
    write_env,
)
from tests.fixtures.fake_databricks_account import FakeEstate, assert_safe
from tools.clients.bootstrap_roster import ROSTER
from tools.databricks.agent_proxy_credential_bundle import parse_agent_proxy_credential_bundle
from tools.databricks.m2m_identity_contract import IDENTITY_DEFAULTS
from tools.databricks.m2m_oauth_credential_mutation import credential_retirement_mode

CANARY = "canary-6f1d2c9e-secret-never-printed"
ROTATED = (*RESERVED_CORE, "account_principal")
PROXY_FIELD = "DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE"
AGENTIC = ("--tier", "agentic")


@pytest.fixture
def estate() -> Iterator[FakeEstate]:
    found = FakeEstate(minted_value=CANARY)
    yield found
    assert_safe(found)


@pytest.fixture
def adopted(estate: FakeEstate, tmp_path: Path) -> tuple[Path, FakeSink, dict[str, list[str]]]:
    """The reserved identities exist with older secrets; bootstrap adopts and pins them."""

    names = {
        "normal": "acme-nightly-ci-sp",
        "operator2": "acme-nightly-operator2-ci-sp",
        "admin": "acme-nightly-admin-ci-sp",
        "release_probe": "acme-release-probe-ci-sp",
        "account_principal": "acme-account-scim-ci-sp",
    }
    older: dict[str, list[str]] = {}
    adopt: list[str] = []
    for role, name in names.items():
        sp = estate.add_sp(name)
        older[role] = [estate.add_secret(sp), estate.add_secret(sp)]
        adopt += ["--adopt", f"{role}={sp.application_id}"]
    sink = FakeSink()
    _planned, applied = plan_and_apply(estate, write_env(tmp_path / "first.env"), sink, *adopt)
    assert applied.code == 0, applied.err
    assert sink.pins is not None
    return write_env(tmp_path / "pinned.env", pins=sink.pins), sink, older


def _rotation_plan(estate: FakeEstate, env: Path, sink: FakeSink):  # noqa: ANN202 - Result
    planned = run_tool(estate, "--plan", "--for", "rotate-adopted", env_file=env, sink=sink)
    assert planned.code == 0, planned.err
    return planned


def _secret_ids(estate: FakeEstate, name: str) -> list[str]:
    sp = estate.by_name(name)
    assert sp is not None
    return [secret.secret_id for secret in estate.secrets[sp.scim_id]]


def test_rotation_revokes_exactly_the_older_secrets_of_pinned_identities(
    estate: FakeEstate, adopted: tuple[Path, FakeSink, dict[str, list[str]]]
) -> None:
    env, sink, older = adopted
    foreign = estate.add_sp("other-team-automation", protected=True)
    foreign_secret = estate.add_secret(foreign)
    planned = _rotation_plan(estate, env, sink)
    assert planned.steps() == [
        f"rotate_secret role={role} field={field} revoke=2"
        for role, field in zip(
            ROTATED,
            (
                "DATABRICKS_CLIENT_SECRET",
                "DATABRICKS_OPERATOR2_CLIENT_SECRET",
                "DATABRICKS_ADMIN_CLIENT_SECRET",
                "DATABRICKS_RELEASE_PROBE_CLIENT_SECRET",
                "DATABRICKS_ACCOUNT_CLIENT_SECRET",
            ),
            strict=True,
        )
    ]
    result = run_tool(estate, "--rotate-adopted", "--ack-plan", planned.digest, env_file=env, sink=sink)
    assert result.code == 0, result.err
    deletes = [c for c in estate.calls if c.method == "delete"]
    assert {c.api for c in deletes} == {"service_principal_secrets"}
    assert sorted(c.args[1] for c in deletes) == sorted(i for ids in older.values() for i in ids)
    for role in ROTATED:
        application_id, secret = sink.secrets[{
            "normal": "DATABRICKS_CLIENT_SECRET",
            "operator2": "DATABRICKS_OPERATOR2_CLIENT_SECRET",
            "admin": "DATABRICKS_ADMIN_CLIENT_SECRET",
            "release_probe": "DATABRICKS_RELEASE_PROBE_CLIENT_SECRET",
            "account_principal": "DATABRICKS_ACCOUNT_CLIENT_SECRET",
        }[role]]
        assert application_id == sink.pins[role] and secret.get_secret_value() == CANARY  # type: ignore[index]
    for name in ("acme-nightly-ci-sp", "acme-account-scim-ci-sp"):
        (remaining,) = _secret_ids(estate, name)
        assert remaining not in older["normal"] + older["account_principal"]
    assert _secret_ids(estate, "other-team-automation") == [foreign_secret]
    for role in ("deployer", "jobs", "validator"):
        assert _secret_ids(estate, f"acme-{role}") == []


def test_the_minted_secret_never_reaches_output_logs_or_the_plan(
    estate: FakeEstate, adopted: tuple[Path, FakeSink, dict[str, list[str]]], caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    env, sink, _older = adopted
    caplog.set_level(logging.DEBUG)
    planned = _rotation_plan(estate, env, sink)
    result = run_tool(estate, "--rotate-adopted", "--ack-plan", planned.digest, env_file=env, sink=sink)
    assert result.code == 0
    verify = run_tool(estate, "--verify", env_file=env, sink=sink)
    assert verify.code == 0, verify.out
    captured = capsys.readouterr()
    for text in (planned.out, planned.err, result.out, result.err, verify.out, captured.out, captured.err, caplog.text):
        assert CANARY not in text
    assert all(CANARY not in repr(secret) and CANARY not in str(secret) for _a, secret in sink.secrets.values())


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("sink", "the credential sink failed (RuntimeError); older secrets kept"),
        ("confirm", "the sink did not confirm DATABRICKS_CLIENT_SECRET; older secrets kept"),
        ("probe", "the new credential did not authenticate; older secrets kept"),
    ],
)
def test_a_failed_handoff_keeps_every_older_secret_and_leaks_nothing(
    estate: FakeEstate,
    adopted: tuple[Path, FakeSink, dict[str, list[str]]],
    caplog: pytest.LogCaptureFixture,
    failure: str,
    message: str,
) -> None:
    env, sink, older = adopted
    caplog.set_level(logging.DEBUG)
    if failure == "sink":
        sink.fail_put = RuntimeError(f"gh secret set echoed {CANARY}")
    elif failure == "confirm":
        sink.confirm = False
    planned = _rotation_plan(estate, env, sink)
    result = run_tool(
        estate,
        "--rotate-adopted",
        "--ack-plan",
        planned.digest,
        env_file=env,
        sink=sink,
        probe=(lambda application_id, secret: False) if failure == "probe" else None,
    )
    assert result.code == 2
    assert f"normal: {message}" in result.err
    assert not [c for c in estate.calls if c.method == "delete"]
    assert set(older["normal"]) <= set(_secret_ids(estate, "acme-nightly-ci-sp"))
    for text in (result.out, result.err, caplog.text):
        assert CANARY not in text


def test_a_secret_minted_after_the_plan_refuses_before_any_mint(
    estate: FakeEstate, adopted: tuple[Path, FakeSink, dict[str, list[str]]]
) -> None:
    env, sink, _older = adopted
    planned = _rotation_plan(estate, env, sink)
    sp = estate.by_name("acme-nightly-admin-ci-sp")
    assert sp is not None
    estate.add_secret(sp)
    result = run_tool(estate, "--rotate-adopted", "--ack-plan", planned.digest, env_file=env, sink=sink)
    assert result.code == 2 and "does not match the rotate-adopted plan" in result.err
    assert not [c for c in estate.calls if c.api == "service_principal_secrets" and c.method == "create"]


def test_rotation_refuses_an_identity_not_yet_pinned(estate: FakeEstate, tmp_path: Path) -> None:
    sp = estate.add_sp("acme-nightly-ci-sp")
    result = run_tool(
        estate, "--plan", "--for", "rotate-adopted", "--adopt", f"normal={sp.application_id}", env_file=write_env(tmp_path / "e.env")
    )
    assert result.code == 2
    assert "normal: is not pinned in MIP_IDENTITY_IDS_JSON; run --apply first" in result.err


def test_rotation_never_touches_the_secretless_identities(estate: FakeEstate, tmp_path: Path) -> None:
    sink = FakeSink()
    _planned, applied = plan_and_apply(estate, write_env(tmp_path / "a.env"), sink)
    assert applied.code == 0
    env = write_env(tmp_path / "pinned.env", pins=sink.pins)
    planned = _rotation_plan(estate, env, sink)
    assert not any(role in line for line in planned.steps() for role in ("role=deployer", "role=jobs", "role=validator"))
    result = run_tool(estate, "--rotate-adopted", "--ack-plan", planned.digest, env_file=env, sink=sink)
    assert result.code == 0
    minted_for = {c.args[0] for c in estate.calls if c.api == "service_principal_secrets" and c.method == "create"}
    secretless = {estate.by_name(f"acme-{role}").scim_id for role in ("deployer", "jobs", "validator")}  # type: ignore[union-attr]
    assert minted_for and not minted_for & secretless


# --- the agent proxy -----------------------------------------------------------------


def test_the_roster_follows_the_m2m_contract_for_fields_and_retirement() -> None:
    reserved = [spec for spec in ROSTER if spec.contract_role is not None]
    assert [spec.role for spec in reserved] == [*RESERVED_CORE, "verifier", "runtime", "proxy"]
    for spec in reserved:
        assert spec.contract_role is not None
        assert spec.secret_field == IDENTITY_DEFAULTS[spec.contract_role].client_secret_secret_name
        assert spec.retirement == credential_retirement_mode(spec.contract_role)
    assert [spec.role for spec in ROSTER if spec.bundled] == ["proxy"]
    assert [spec.role for spec in ROSTER if spec.retirement != "immediate"] == ["proxy"]


def _agentic_install(estate: FakeEstate, tmp_path: Path) -> tuple[Path, FakeSink]:
    sink = FakeSink()
    _planned, applied = plan_and_apply(estate, write_env(tmp_path / "a.env", MIP_AGENTIC_TIER="on"), sink, *AGENTIC)
    assert applied.code == 0, applied.err
    return write_env(tmp_path / "pinned.env", pins=sink.pins, MIP_AGENTIC_TIER="on"), sink


def test_the_proxy_gets_its_canonical_bundle_and_keeps_its_older_credential(
    estate: FakeEstate, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    env, sink = _agentic_install(estate, tmp_path)
    caplog.set_level(logging.DEBUG)
    proxy = estate.by_name("acme-agent-supervisor-proxy-ci-sp")
    assert proxy is not None
    active = estate.add_secret(proxy, "the-signed-active-credential")
    planned = run_tool(estate, "--plan", "--for", "rotate-adopted", *AGENTIC, env_file=env, sink=sink)
    assert planned.code == 0, planned.err
    assert f"rotate_secret role=proxy field={PROXY_FIELD} revoke=0 retire=signed_app_cutover" in planned.steps()
    result = run_tool(estate, "--rotate-adopted", "--ack-plan", planned.digest, *AGENTIC, env_file=env, sink=sink)
    assert result.code == 0, result.err
    (minted,) = [s.secret_id for s in estate.secrets[proxy.scim_id] if s.minted_here]
    assert _secret_ids(estate, "acme-agent-supervisor-proxy-ci-sp") == [active, minted]
    assert not [c for c in estate.calls if c.method == "delete" and c.args[0] == proxy.scim_id]
    application_id, value = sink.secrets[PROXY_FIELD]
    bundle = parse_agent_proxy_credential_bundle(value.get_secret_value())
    assert (bundle.client_id, bundle.credential_id, bundle.client_secret) == (sink.pins["proxy"], minted, CANARY)  # type: ignore[index]
    assert application_id == sink.pins["proxy"]  # type: ignore[index]
    for role, field in (("verifier", "DATABRICKS_VERIFIER_CLIENT_SECRET"), ("runtime", "DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET")):
        assert sink.secrets[field] == (sink.pins[role], sink.secrets[field][1])  # type: ignore[index]
        assert sink.secrets[field][1].get_secret_value() == CANARY
    for text in (planned.out, planned.err, result.out, result.err, caplog.text):
        assert CANARY not in text


def test_a_proxy_holding_two_credentials_is_deferred_not_rotated(estate: FakeEstate, tmp_path: Path) -> None:
    env, sink = _agentic_install(estate, tmp_path)
    proxy = estate.by_name("acme-agent-supervisor-proxy-ci-sp")
    assert proxy is not None
    held = [estate.add_secret(proxy), estate.add_secret(proxy)]
    planned = run_tool(estate, "--plan", "--for", "rotate-adopted", *AGENTIC, env_file=env, sink=sink)
    assert planned.code == 0, planned.err
    assert not [line for line in planned.steps() if "role=proxy" in line]
    assert "deferred: proxy: not rotated; it holds 2 credentials" in planned.out
    result = run_tool(estate, "--rotate-adopted", "--ack-plan", planned.digest, *AGENTIC, env_file=env, sink=sink)
    assert result.code == 0, result.err
    assert _secret_ids(estate, "acme-agent-supervisor-proxy-ci-sp") == held
    assert PROXY_FIELD not in sink.secrets
    assert not [c for c in estate.calls if c.args and c.args[0] == proxy.scim_id and c.method in {"create", "delete"}]


# --- post-first-install --------------------------------------------------------------


def test_post_first_install_grants_manager_on_the_app_identity_once(estate: FakeEstate, tmp_path: Path) -> None:
    sink = FakeSink()
    _planned, applied = plan_and_apply(estate, write_env(tmp_path / "a.env"), sink)
    assert applied.code == 0 and sink.pins is not None
    env = write_env(tmp_path / "pinned.env", pins=sink.pins)
    missing = run_tool(estate, "--plan", "--for", "post-first-install", env_file=env, sink=sink)
    assert missing.code == 2 and "the App does not exist yet" in missing.err
    app_sp = estate.add_sp("app-acme-app")
    estate.rule_set(app_sp.application_id)["roles/servicePrincipal.user"] = {"users/owner@owner.example"}
    estate.apps["acme-app"] = app_sp.application_id
    planned = run_tool(estate, "--plan", "--for", "post-first-install", env_file=env, sink=sink)
    assert planned.steps() == ["grant_sp_role target=app grantee=account_principal sp_role=manager"]
    assert app_sp.application_id not in planned.out
    result = run_tool(estate, "--post-first-install", "--ack-plan", planned.digest, env_file=env, sink=sink)
    assert result.code == 0, result.err
    assert estate.rule_set(app_sp.application_id) == {
        "roles/servicePrincipal.user": {"users/owner@owner.example"},
        "roles/servicePrincipal.manager": {f"servicePrincipals/{sink.pins['account_principal']}"},
    }
    again = run_tool(estate, "--plan", "--for", "post-first-install", env_file=env, sink=sink)
    assert again.steps() == []


def test_post_first_install_refuses_when_the_app_identity_is_a_bootstrap_identity(estate: FakeEstate, tmp_path: Path) -> None:
    sink = FakeSink()
    _planned, applied = plan_and_apply(estate, write_env(tmp_path / "a.env"), sink)
    assert applied.code == 0 and sink.pins is not None
    estate.apps["acme-app"] = sink.pins["deployer"]
    result = run_tool(estate, "--plan", "--for", "post-first-install", env_file=write_env(tmp_path / "p.env", pins=sink.pins), sink=sink)
    assert result.code == 2 and "the App's identity is one of the bootstrap identities" in result.err
