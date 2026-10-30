"""The GitHub-environment credential sink, alone and under the real bootstrap.

* ``bootstrap_account --sink github-environment`` runs end to end over the
  fake gh (tests/fixtures/fake_gh.py) and the fake account: ``--apply`` pins
  every identity into ``MIP_IDENTITY_IDS_JSON``, ``--rotate-adopted`` hands
  every minted secret to the environment, and the canary secret never
  reaches argv, stdout, stderr or a file left behind.
* ``ready`` refuses, before the first account mutation, an environment that
  is missing or not protected exactly as its review mode requires.
* ``put_secret`` takes only a field the bootstrap mints; ``put_identity_ids``
  only a valid pin map; ``confirm_secret`` only what this run wrote.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from tests.fixtures.bootstrap_harness import CORE_ROLES, RESERVED_CORE, run_tool, write_env
from tests.fixtures.fake_databricks_account import REPOSITORY, FakeEstate, assert_safe
from tests.fixtures.fake_gh import FakeGitHub, install_fake_gh
from tests.fixtures.github_environment_harness import plan_and_apply, session_opener, write_profile
from tools.clients.bootstrap_plan import canonical_json
from tools.clients.bootstrap_roster import spec_for
from tools.clients.errors import BootstrapRefusal
from tools.clients.github_session import GhFailure, fresh_gh_session
from tools.clients.github_sink import MINTED_FIELDS, GitHubEnvironmentSink, github_environment_sink

CANARY = "canary-3b7e19-minted-secret-never-printed"
CLIENT = "acme"


@pytest.fixture
def estate() -> Iterator[FakeEstate]:
    found = FakeEstate(minted_value=CANARY)
    yield found
    assert_safe(found)


@pytest.fixture
def fake(tmp_path: Path) -> Iterator[FakeGitHub]:
    found = install_fake_gh(tmp_path / "github")
    yield found
    assert found.state()["violations"] == []
    sessions = found.root / "sessions"
    assert not sessions.exists() or list(sessions.iterdir()) == []


@pytest.fixture
def protected(fake: FakeGitHub, tmp_path: Path) -> FakeGitHub:
    """``acme`` and ``acme-validate``, set up by the real environment tool (skeleton profile)."""

    skeleton = write_profile(
        tmp_path / "input" / "skeleton.env",
        {"MIP_CLIENT": CLIENT, "MIP_REVIEW_MODE": "single_maintainer_audited", "MIP_AGENTIC_TIER": "off"},
    )
    _planned, applied = plan_and_apply(fake, skeleton)
    assert applied.code == 0, applied.err
    fake.clear_log()
    return fake


def _sink_factory(fake: FakeGitHub) -> Any:
    def factory(client: str, repository: str) -> Any:
        return github_environment_sink(
            client, repository, open_session=session_opener(fake, fake.root / "sessions"), sleep=lambda _s: None
        )

    return factory


@contextmanager
def _sink(fake: FakeGitHub) -> Iterator[GitHubEnvironmentSink]:
    with _sink_factory(fake)(CLIENT, REPOSITORY) as sink:
        yield sink


def _pins(estate: FakeEstate) -> dict[str, str]:
    pins = {}
    for role in CORE_ROLES:
        sp = estate.by_name(spec_for(role).display_name(CLIENT))
        assert sp is not None, role
        pins[role] = sp.application_id
    return pins


def _sha(value: str | bytes) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


# --- the bootstrap end to end -----------------------------------------------------------------


def test_the_bootstrap_pins_and_rotates_through_the_github_environment(
    estate: FakeEstate, protected: FakeGitHub, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    fake = protected
    env = write_env(tmp_path / "acme.env")
    planned = run_tool(estate, "--plan", env_file=env)
    applied = run_tool(
        estate, "--apply", "--sink", "github-environment", "--ack-plan", planned.digest,
        env_file=env, github_sink=_sink_factory(fake),
    )
    assert applied.code == 0, applied.err
    assert "converged" in applied.out
    pins = _pins(estate)
    stored = fake.environment(CLIENT)["secrets"]
    assert stored["MIP_IDENTITY_IDS_JSON"]["sha256"] == _sha(canonical_json(pins))

    pinned = write_env(tmp_path / "pinned.env", pins=pins)
    rotation = run_tool(estate, "--plan", "--for", "rotate-adopted", env_file=pinned)
    assert rotation.code == 0, rotation.err
    rotated = run_tool(
        estate, "--rotate-adopted", "--sink", "github-environment", "--ack-plan", rotation.digest,
        env_file=pinned, github_sink=_sink_factory(fake),
    )
    assert rotated.code == 0, rotated.err
    stored = fake.environment(CLIENT)["secrets"]
    for role in (*RESERVED_CORE, "account_principal"):
        field = spec_for(role).secret_field
        assert field is not None and stored[field]["sha256"] == _sha(CANARY), role

    calls = fake.calls()
    assert calls and all("--repo" not in call["argv"] and call["tokens"] == [] for call in calls)
    writes = [call for call in calls if call["argv"][:2] == ["secret", "set"]]
    assert writes and all(call["argv"][3:] == ["--env", CLIENT] and len(call["argv"]) == 5 for call in writes)
    assert all(CANARY not in arg for call in calls for arg in call["argv"])
    captured = capfd.readouterr()
    printed = applied.out + applied.err + rotated.out + rotated.err + captured.out + captured.err
    assert CANARY not in printed
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert CANARY not in path.read_text(encoding="utf-8", errors="replace"), path


def test_a_missing_environment_refuses_before_the_first_account_mutation(
    estate: FakeEstate, fake: FakeGitHub, tmp_path: Path
) -> None:
    env = write_env(tmp_path / "acme.env")
    planned = run_tool(estate, "--plan", env_file=env)
    result = run_tool(
        estate, "--apply", "--sink", "github-environment", "--ack-plan", planned.digest,
        env_file=env, github_sink=_sink_factory(fake),
    )
    assert result.code == 2
    assert "credential sink: the acme GitHub environment does not exist" in result.err
    assert estate.mutations() == [] and fake.mutations() == []


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"can_admins_bypass": True}, "acme can_admins_bypass differs from single_maintainer_audited"),
        ({"protection_rules": []}, "acme reviewers differs from single_maintainer_audited"),
        ({"branch_policies": [{"id": 1, "name": "main", "type": "branch"}, {"id": 2, "name": "dev", "type": "branch"}]},
         "acme deployment branch policy is not exactly {main}"),
    ],
)
def test_protection_drift_refuses_before_the_first_account_mutation(
    estate: FakeEstate, protected: FakeGitHub, tmp_path: Path, change: dict[str, Any], expected: str
) -> None:
    state = protected.state()
    state["environments"][CLIENT].update(change)
    protected.write(state)
    env = write_env(tmp_path / "acme.env")
    planned = run_tool(estate, "--plan", env_file=env)
    result = run_tool(
        estate, "--apply", "--sink", "github-environment", "--ack-plan", planned.digest,
        env_file=env, github_sink=_sink_factory(protected),
    )
    assert result.code == 2 and f"credential sink: github: {expected}" in result.err
    assert estate.mutations() == [] and protected.mutations() == []


def test_a_review_mode_variable_that_is_missing_or_unbuilt_refuses(protected: FakeGitHub) -> None:
    for value in (None, "independent"):
        state = protected.state()
        variables = state["environments"][CLIENT]["variables"]
        if value is None:
            variables.pop("MIP_REVIEW_MODE")
        else:
            variables["MIP_REVIEW_MODE"] = {"value": value, "updatedAt": "2026-10-30T00:00:01Z"}
        protected.write(state)
        with _sink(protected) as sink, pytest.raises(BootstrapRefusal) as refused:
            sink.ready()
        assert any(line.startswith("MIP_REVIEW_MODE: ") for line in refused.value.lines)
    assert protected.mutations() == []


def test_the_bootstrap_refuses_a_sink_on_a_read_only_action(estate: FakeEstate, tmp_path: Path) -> None:
    env = write_env(tmp_path / "acme.env")
    for action in ("--plan", "--verify"):
        result = run_tool(estate, action, "--sink", "github-environment", env_file=env)
        assert result.code == 2 and "--sink: applies to --apply" in result.err
    assert estate.calls == []


# --- the sink's own contract --------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["MIP_COTALITY_ID_MASK_SECRET", "MIP_GENIE_ACTION_SECRET_CURRENT", "MIP_AI_GATEWAY_PROOF_SIGNING_KEY",
     "DATABRICKS_HOST", "MIP_IDENTITY_IDS_JSON", "GITHUB_TOKEN"],
)
def test_put_secret_takes_only_a_field_the_bootstrap_mints(protected: FakeGitHub, field: str) -> None:
    assert field not in MINTED_FIELDS
    with _sink(protected) as sink, pytest.raises(BootstrapRefusal, match="is not a secret the bootstrap mints"):
        sink.put_secret(field, "00000000-0000-4000-8000-000000000001", SecretStr(CANARY))
    assert protected.calls() == []


def test_the_minted_fields_are_exactly_the_rosters_secret_fields() -> None:
    assert {
        "DATABRICKS_CLIENT_SECRET",
        "DATABRICKS_OPERATOR2_CLIENT_SECRET",
        "DATABRICKS_ADMIN_CLIENT_SECRET",
        "DATABRICKS_RELEASE_PROBE_CLIENT_SECRET",
        "DATABRICKS_VERIFIER_CLIENT_SECRET",
        "DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET",
        "DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE",
        "DATABRICKS_ACCOUNT_CLIENT_SECRET",
    } == MINTED_FIELDS


def test_confirm_is_true_only_for_what_this_run_wrote(protected: FakeGitHub) -> None:
    with _sink(protected) as sink:
        assert sink.confirm_secret("DATABRICKS_CLIENT_SECRET") is False
        sink.put_secret("DATABRICKS_CLIENT_SECRET", "00000000-0000-4000-8000-000000000004", SecretStr(CANARY))
        assert sink.confirm_secret("DATABRICKS_CLIENT_SECRET") is True
        assert sink.confirm_secret("DATABRICKS_ADMIN_CLIENT_SECRET") is False
    with _sink(protected) as later:
        # A later run did not write it, whatever the environment holds.
        later.ready()
        assert later.confirm_secret("DATABRICKS_CLIENT_SECRET") is False
    stored = protected.environment(CLIENT)["secrets"]["DATABRICKS_CLIENT_SECRET"]
    assert stored["sha256"] == _sha(CANARY)


def test_a_write_the_environment_did_not_record_is_not_confirmed(protected: FakeGitHub) -> None:
    with _sink(protected) as sink:
        sink.ready()
        state = protected.state()
        state["fail"].append({"argv": ["secret", "set", "DATABRICKS_CLIENT_SECRET"]})
        protected.write(state)
        with pytest.raises(GhFailure, match=r"^gh secret set failed \(exit 1\)$"):
            sink.put_secret("DATABRICKS_CLIENT_SECRET", "00000000-0000-4000-8000-000000000004", SecretStr(CANARY))
        assert sink.confirm_secret("DATABRICKS_CLIENT_SECRET") is False


@pytest.mark.parametrize(
    ("pins", "expected"),
    [
        ({}, "the pin map is empty"),
        ({"deployer": "<application-id>"}, "<placeholder>"),
        ({"deployer": "not-a-uuid"}, "MIP_IDENTITY_IDS_JSON.deployer"),
        ({"stranger": "00000000-0000-4000-8000-000000000001"}, "MIP_IDENTITY_IDS_JSON."),
    ],
)
def test_put_identity_ids_refuses_an_invalid_pin_map_without_writing(
    protected: FakeGitHub, pins: dict[str, str], expected: str
) -> None:
    with _sink(protected) as sink, pytest.raises(BootstrapRefusal) as refused:
        sink.put_identity_ids(pins)
    assert any(expected in line for line in refused.value.lines), refused.value.lines
    assert protected.mutations() == []


def test_the_sink_writes_the_pin_map_whole_as_canonical_json(protected: FakeGitHub) -> None:
    pins = {"jobs": "00000000-0000-4000-8000-000000000002", "deployer": "00000000-0000-4000-8000-000000000001"}
    with _sink(protected) as sink:
        sink.put_identity_ids(pins)
    stored = protected.environment(CLIENT)["secrets"]["MIP_IDENTITY_IDS_JSON"]
    assert stored["sha256"] == _sha(json.dumps(dict(sorted(pins.items())), separators=(",", ":")))


def test_the_sink_logs_in_lazily_and_once(protected: FakeGitHub) -> None:
    logins = protected.state()["logins"]
    with _sink(protected) as sink:
        assert protected.state()["logins"] == logins  # nothing yet: a converged run needs no login
        sink.ready()
        sink.ready()
    assert protected.state()["logins"] == logins + 1


def test_the_real_session_factory_is_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    import inspect

    from tools.clients import bootstrap_account, github_sink

    assert inspect.signature(github_sink.github_environment_sink).parameters["open_session"].default is fresh_gh_session
    assert bootstrap_account.Dependencies().open_github_sink is github_environment_sink
