"""``python -m tools.clients.github_environment`` against a fake gh on PATH.

* The plan is read-only and public-safe; ``--apply`` needs its exact digest
  and refuses any other with zero mutations; a second apply makes none.
* Both environments end with exactly the review mode's protections:
  reviewers, ``prevent_self_review``, ``can_admins_bypass=false`` and a
  branch policy of exactly ``{main}``; drift converges.
* Every gh call names ``--env`` and never ``--repo``, runs in a fresh
  private ``GH_CONFIG_DIR`` with no ambient token, and gets every value on
  stdin. A canary identifying value, a canary local secret and the generated
  signing keys never reach argv, stdout, stderr or a file left behind.
* Identifying values are secrets, never variables; the profile digest
  variable equals the digest the deploy resolves from the environment.
* Human presence: refused under Actions, under CI and without a terminal,
  before any session opens.
"""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.client_profiles import demo_profile, split_contexts
from tests.fixtures.fake_gh import (
    MAINTAINER,
    REPOSITORY,
    SESSION_TOKEN,
    FakeGitHub,
    install_fake_gh,
)
from tests.fixtures.github_environment_harness import plan_and_apply, run_env_tool, write_profile
from tools.clients import profile as views
from tools.clients.environment import from_github, from_local, resolve
from tools.clients.github_environment import ed25519_signing_key
from tools.clients.github_environment_plan import (
    MODEL_SIGNING_KEY,
    PINS_FIELD,
    PROFILE_DIGEST_VARIABLE,
    PROOF_SIGNING_KEY,
)
from tools.clients.schema import FIELD_CLASS, FieldClass

CLIENT = "fictional-demo"
ENVIRONMENTS = (CLIENT, f"{CLIENT}-validate")
CANARY_OWNER = "canary-owner-5e81d2"
CANARY_SECRET = "canary-m2m-secret-77aa0c"
EXPECTED_RULES = [
    {
        "type": "required_reviewers",
        "prevent_self_review": False,
        "reviewers": [{"type": "User", "reviewer": {"id": MAINTAINER["id"], "login": MAINTAINER["login"]}}],
    }
]


@pytest.fixture
def fake(tmp_path: Path) -> Iterator[FakeGitHub]:
    found = install_fake_gh(tmp_path / "github")
    yield found
    assert found.state()["violations"] == []
    sessions = found.root / "sessions"
    assert not sessions.exists() or list(sessions.iterdir()) == []


def _values(**overrides: str | None) -> dict[str, str]:
    return demo_profile(MIP_UC_METASTORE_OWNER=CANARY_OWNER, DATABRICKS_CLIENT_SECRET=CANARY_SECRET, **overrides)


@pytest.fixture
def env(tmp_path: Path) -> Path:
    return write_profile(tmp_path / "input" / "acme.env", _values())


def _local(env_file: Path) -> dict[str, str]:
    return views.canonical_env(resolve(from_local({}, env_file)))


def _sha(value: str | bytes) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def _files_under(root: Path, *, skip: Path) -> Iterator[Path]:
    for path in root.rglob("*"):
        if path.is_file() and skip not in path.parents:
            yield path


# --- plan, apply, idempotence ------------------------------------------------------------


def test_the_plan_is_read_only_and_names_every_step(fake: FakeGitHub, env: Path) -> None:
    planned = run_env_tool(fake, "--plan", env_file=env)
    assert planned.code == 0, planned.err
    assert fake.mutations() == []
    ops = [line.split()[0] for line in planned.steps()]
    assert ops.count("put_environment") == 2 and ops.count("add_branch_policy") == 2
    assert ops[-1] == "set_variable" and planned.steps()[-1].split()[2] == f"name={PROFILE_DIGEST_VARIABLE}"
    document = planned.document()
    assert document["tool"] == "tools.clients.github_environment"
    assert document["client"] == CLIENT and document["review_mode"] == "single_maintainer_audited"
    assert document["repository"] == REPOSITORY and document["rotate"] is False
    keys = [step["name"] for step in document["steps"] if step["op"] == "generate_signing_key"]
    assert keys == [PROOF_SIGNING_KEY, MODEL_SIGNING_KEY]  # MIP_AGENTIC_TIER=on
    secrets = {step["name"] for step in document["steps"] if step["op"] == "set_secret"}
    assert PINS_FIELD not in secrets and secrets
    assert all(FIELD_CLASS[name] is FieldClass.IDENTIFYING for name in secrets)
    # Public-safe: no identifying or secret value, only names and public values.
    for name, value in _local(env).items():
        if FIELD_CLASS[name] is FieldClass.IDENTIFYING:
            assert value not in planned.out + planned.err, name
    assert CANARY_OWNER not in planned.out and CANARY_SECRET not in planned.out
    assert str(MAINTAINER["id"]) not in planned.out


def test_apply_converges_and_a_second_apply_makes_zero_mutations(fake: FakeGitHub, env: Path) -> None:
    _planned, applied = plan_and_apply(fake, env)
    assert applied.code == 0, applied.err
    assert "converged; a second run makes 0 mutations" in applied.out
    again = run_env_tool(fake, "--plan", env_file=env)
    assert again.code == 0 and again.steps() == []
    fake.clear_log()
    second = run_env_tool(fake, "--apply", "--ack-plan", again.digest, env_file=env)
    assert second.code == 0, second.err
    assert fake.mutations() == []
    assert "0 step(s), 0 mutation(s)" in second.out


def test_both_environments_hold_exactly_the_review_modes_protections(fake: FakeGitHub, env: Path) -> None:
    _planned, applied = plan_and_apply(fake, env)
    assert applied.code == 0, applied.err
    for name in ENVIRONMENTS:
        found = fake.environment(name)
        assert found["protection_rules"] == EXPECTED_RULES, name
        assert found["can_admins_bypass"] is False
        assert found["deployment_branch_policy"] == {"protected_branches": False, "custom_branch_policies": True}
        assert [(p["name"], p["type"]) for p in found["branch_policies"]] == [("main", "branch")]
    # The validate environment carries protections only, no profile values.
    assert fake.environment(f"{CLIENT}-validate")["secrets"] == {}
    assert fake.environment(f"{CLIENT}-validate")["variables"] == {}


def test_drifted_protections_and_extra_branch_policies_converge_exactly(fake: FakeGitHub, env: Path) -> None:
    fake.seed_environment(
        CLIENT,
        can_admins_bypass=True,
        protection_rules=[
            {"type": "required_reviewers", "prevent_self_review": True,
             "reviewers": [{"type": "User", "reviewer": {"id": 7, "login": "someone"}}]}
        ],
        deployment_branch_policy={"protected_branches": False, "custom_branch_policies": True},
        branch_policies=[
            {"id": 1, "name": "main", "type": "branch"},
            {"id": 2, "name": "feature/unreviewed", "type": "branch"},
            {"id": 3, "name": "main", "type": "tag"},
        ],
    )
    planned = run_env_tool(fake, "--plan", env_file=env)
    deploy_steps = [line for line in planned.steps() if f"env={CLIENT} " in line + " "]
    assert any(line.startswith("put_environment env=fictional-demo change=update") for line in deploy_steps)
    assert sorted(line for line in deploy_steps if line.startswith("delete_branch_policy")) == [
        "delete_branch_policy env=fictional-demo policy_id=2",
        "delete_branch_policy env=fictional-demo policy_id=3",
    ]
    assert not any(line.startswith(f"add_branch_policy env={CLIENT} ") for line in deploy_steps)
    applied = run_env_tool(fake, "--apply", "--ack-plan", planned.digest, env_file=env)
    assert applied.code == 0, applied.err
    found = fake.environment(CLIENT)
    assert found["protection_rules"] == EXPECTED_RULES and found["can_admins_bypass"] is False
    assert [(p["id"], p["name"], p["type"]) for p in found["branch_policies"]] == [(1, "main", "branch")]


def test_an_api_that_ignores_a_protection_is_reported_unconverged(fake: FakeGitHub, env: Path) -> None:
    state = fake.state()
    state["ignore_can_admins_bypass"] = True
    fake.write(state)
    _planned, applied = plan_and_apply(fake, env)
    assert applied.code == 1
    assert "not converged; 2 step(s) remain" in applied.out


# --- values: secrets, variables, keys, digest ----------------------------------------------


def test_identifying_values_are_secrets_and_public_values_variables(fake: FakeGitHub, env: Path) -> None:
    _planned, applied = plan_and_apply(fake, env)
    assert applied.code == 0, applied.err
    local = _local(env)
    deploy = fake.environment(CLIENT)
    variables = {name: item["value"] for name, item in deploy["variables"].items()}
    public = {name: value for name, value in local.items() if FIELD_CLASS[name] is FieldClass.PUBLIC}
    assert variables == {**public, PROFILE_DIGEST_VARIABLE: views.digest(resolve(from_local({}, env)))}
    identifying = {name: value for name, value in local.items() if FIELD_CLASS[name] is FieldClass.IDENTIFYING}
    expected_secrets = set(identifying) - {PINS_FIELD} | {PROOF_SIGNING_KEY, MODEL_SIGNING_KEY}
    assert set(deploy["secrets"]) == expected_secrets
    for name in set(identifying) - {PINS_FIELD}:
        assert deploy["secrets"][name]["sha256"] == _sha(identifying[name]), name
        assert identifying[name] not in variables.values()
    # No M2M or account-principal secret is copied from the local file.
    assert not set(deploy["secrets"]) & {n for n, k in FIELD_CLASS.items() if k is FieldClass.SECRET} - {
        PROOF_SIGNING_KEY,
        MODEL_SIGNING_KEY,
    }
    for key in (PROOF_SIGNING_KEY, MODEL_SIGNING_KEY):
        assert deploy["secrets"][key]["ed25519"] is True


def test_the_digest_variable_equals_what_the_deploy_resolves_from_the_environment(fake: FakeGitHub, env: Path) -> None:
    _planned, applied = plan_and_apply(fake, env)
    assert applied.code == 0, applied.err
    deploy = fake.environment(CLIENT)
    variables = {name: item["value"] for name, item in deploy["variables"].items()}
    # GitHub cannot return secrets: rebuild them from the local values whose
    # sha256 the fake holds, plus what the bootstrap's sink would add.
    _vars, local_secrets = split_contexts(_values())
    secrets = {name: value for name, value in local_secrets.items() if name in deploy["secrets"] or name == PINS_FIELD}
    secrets.update({name: value for name, value in local_secrets.items() if FIELD_CLASS[name] is FieldClass.SECRET})
    for name in deploy["secrets"]:
        if name in local_secrets and FIELD_CLASS[name] is FieldClass.IDENTIFYING:
            assert deploy["secrets"][name]["sha256"] == _sha(_local(env)[name])
    resolved = resolve(from_github(variables, secrets, environment=CLIENT))
    assert views.digest(resolved) == variables[PROFILE_DIGEST_VARIABLE]


def test_secrets_are_set_only_where_missing_unless_rotate_and_keys_never_rotate(fake: FakeGitHub, env: Path) -> None:
    _planned, applied = plan_and_apply(fake, env)
    assert applied.code == 0, applied.err
    before = {name: dict(item) for name, item in fake.environment(CLIENT)["secrets"].items()}
    rotated_plan = run_env_tool(fake, "--plan", "--rotate", env_file=env)
    ops = {line.split()[0] for line in rotated_plan.steps()}
    assert ops == {"set_secret"}
    assert all("change=replace" in line for line in rotated_plan.steps())
    assert any("signing keys are generated once" in line for line in rotated_plan.out.splitlines())
    applied = run_env_tool(fake, "--apply", "--rotate", "--ack-plan", rotated_plan.digest, env_file=env)
    assert applied.code == 0, applied.err
    after = fake.environment(CLIENT)["secrets"]
    for key in (PROOF_SIGNING_KEY, MODEL_SIGNING_KEY):
        assert after[key] == before[key]
    rewritten = [name for name in after if after[name]["updatedAt"] != before[name]["updatedAt"]]
    assert sorted(rewritten) == sorted(set(after) - {PROOF_SIGNING_KEY, MODEL_SIGNING_KEY})
    # A rotated plan is not the plain plan: its ack does not carry over.
    plain = run_env_tool(fake, "--apply", "--ack-plan", rotated_plan.digest, env_file=env)
    assert plain.code == 2 and "does not match the plan computed now" in plain.err


def test_the_core_tier_generates_only_the_proof_signing_key(fake: FakeGitHub, tmp_path: Path) -> None:
    core = write_profile(
        tmp_path / "input" / "core.env",
        _values(
            MIP_AGENTIC_TIER="off",
            DATABRICKS_VERIFIER_CLIENT_SECRET=None,
            DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET=None,
            DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE=None,
            MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY=None,
        ),
    )
    planned = run_env_tool(fake, "--plan", env_file=core)
    assert [step["name"] for step in planned.document()["steps"] if step["op"] == "generate_signing_key"] == [
        PROOF_SIGNING_KEY
    ]


def test_an_incomplete_profile_plans_only_the_skeleton(fake: FakeGitHub, tmp_path: Path) -> None:
    partial = write_profile(tmp_path / "input" / "partial.env", _values(MIP_IDENTITY_IDS_JSON=None))
    planned = run_env_tool(fake, "--plan", env_file=partial)
    assert planned.code == 0, planned.err
    document = planned.document()
    variables = {step["name"]: step["value"] for step in document["steps"] if step["op"] == "set_variable"}
    assert variables == {"MIP_CLIENT": CLIENT, "MIP_REVIEW_MODE": "single_maintainer_audited"}
    assert not [step for step in document["steps"] if step["op"] == "set_secret"]
    assert {step["op"] for step in document["steps"]} >= {"put_environment", "generate_signing_key"}
    assert any("MIP_IDENTITY_IDS_JSON" in note for note in document["notes"])
    assert CANARY_OWNER not in planned.out


# --- the gh boundary ------------------------------------------------------------------------


def test_every_gh_call_names_env_never_repo_and_gets_values_only_on_stdin(
    fake: FakeGitHub, env: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    generated: list[bytes] = []

    def generate() -> bytes:
        key = ed25519_signing_key()
        generated.append(key)
        return key

    _planned, applied = plan_and_apply(fake, env, generate_key=generate)
    assert applied.code == 0, applied.err
    calls = fake.calls()
    local = _local(env)
    identifying = [value for name, value in local.items() if FIELD_CLASS[name] is FieldClass.IDENTIFYING]
    values = [*identifying, CANARY_SECRET, SESSION_TOKEN, *(key.decode() for key in generated)]
    stdin_digests = [call["stdin_sha256"] for call in calls if call["stdin_sha256"]]
    for call in calls:
        argv = call["argv"]
        assert "--repo" not in argv and "-R" not in argv
        assert call["tokens"] == [] and "GH_DEBUG" not in call["gh_names"]
        if argv[0] in {"secret", "variable"}:
            assert "--env" in argv and call["env"]["GH_REPO"] == REPOSITORY
        if argv[:2] in (["secret", "set"], ["variable", "set"]):
            # Exactly the name and the environment: the value is on stdin.
            assert len(argv) == 5 and argv[3:] == ["--env", CLIENT] and call["stdin_len"] > 0
        for value in values:
            if len(value) >= 8:
                assert all(value not in arg for arg in argv), argv
    # Each identifying value and each generated key went through stdin.
    for name, value in local.items():
        if FIELD_CLASS[name] is FieldClass.IDENTIFYING and name != PINS_FIELD:
            assert _sha(value) in stdin_digests
    assert [_sha(key) for key in generated] == [
        fake.environment(CLIENT)["secrets"][name]["sha256"] for name in (PROOF_SIGNING_KEY, MODEL_SIGNING_KEY)
    ]
    assert _sha(CANARY_SECRET) not in stdin_digests  # a local secret is never sent
    # Nothing printed or left on disk holds a value.
    captured = capfd.readouterr()
    printed = applied.out + applied.err + captured.out + captured.err
    for value in (CANARY_OWNER, CANARY_SECRET, SESSION_TOKEN, *(key.decode() for key in generated)):
        assert value not in printed
        for path in _files_under(fake.root.parent, skip=env.parent):
            # The fake server's own state holds the token it issued; nothing else may.
            if value != SESSION_TOKEN or path != fake.state_path:
                assert value not in path.read_text(encoding="utf-8", errors="replace"), path


def test_each_run_logs_in_fresh_in_a_private_directory_that_is_then_deleted(fake: FakeGitHub, env: Path) -> None:
    _planned, applied = plan_and_apply(fake, env)
    assert applied.code == 0, applied.err
    calls = fake.calls()
    assert fake.state()["logins"] == 2  # one per run, never reused
    roots = {call["env"]["GH_CONFIG_DIR"] for call in calls}
    assert len(roots) == 2
    for root in roots:
        assert root.startswith(str(fake.root / "sessions")) and not Path(root).exists()
    assert {call["config_mode"] for call in calls} == {oct(0o700)}
    login = [call for call in calls if call["argv"][:2] == ["auth", "login"]]
    assert all("--insecure-storage" in call["argv"] and "--web" in call["argv"] for call in login)
    assert all(call["env"]["GH_PROMPT_DISABLED"] is None for call in login)
    assert all(call["env"]["GH_PROMPT_DISABLED"] == "1" for call in calls if call not in login)


def test_a_failed_step_names_the_step_and_a_rerun_finishes(fake: FakeGitHub, env: Path) -> None:
    planned = run_env_tool(fake, "--plan", env_file=env)
    fake.fail_next("secret", "set")
    failed = run_env_tool(fake, "--apply", "--ack-plan", planned.digest, env_file=env)
    assert failed.code == 2
    assert "(set_secret) failed: GhFailure" in failed.err and "mutation(s) applied before it" in failed.err
    assert CANARY_OWNER not in failed.err
    _replanned, finished = plan_and_apply(fake, env)
    assert finished.code == 0 and "converged" in finished.out


# --- refusals --------------------------------------------------------------------------------


def _never_opened(repository: str) -> Any:
    raise AssertionError("a refused run must not open a gh session")


@pytest.mark.parametrize("action", [("--plan",), ("--apply", "--ack-plan", "a" * 64)])
@pytest.mark.parametrize(
    ("environ", "stdin", "expected"),
    [
        ({"GITHUB_ACTIONS": "true"}, None, "GITHUB_ACTIONS: is set"),
        ({"CI": "1"}, None, "CI: is set"),
        ({}, io.StringIO(), "stdin: is not a terminal"),
    ],
)
def test_every_action_refuses_without_a_human_before_any_session(
    fake: FakeGitHub, env: Path, action: tuple[str, ...], environ: dict[str, str], stdin: Any, expected: str
) -> None:
    result = run_env_tool(fake, *action, env_file=env, environ=environ, stdin=stdin, open_session=_never_opened)
    assert result.code == 2 and expected in result.err
    assert fake.calls() == []


def test_apply_without_a_matching_ack_changes_nothing(fake: FakeGitHub, env: Path) -> None:
    missing = run_env_tool(fake, "--apply", env_file=env, open_session=_never_opened)
    assert missing.code == 2 and "--ack-plan: --apply needs the 64-hex sha256" in missing.err
    stray = run_env_tool(fake, "--plan", "--ack-plan", "0" * 64, env_file=env, open_session=_never_opened)
    assert stray.code == 2 and "--ack-plan: applies to --apply only" in stray.err
    planned = run_env_tool(fake, "--plan", env_file=env)
    fake.seed_environment(CLIENT)  # the repository moved between plan and apply
    fake.clear_log()
    moved = run_env_tool(fake, "--apply", "--ack-plan", planned.digest, env_file=env)
    assert moved.code == 2 and "nothing was changed" in moved.err
    assert fake.mutations() == []
    wrong = run_env_tool(fake, "--apply", "--ack-plan", "f" * 64, env_file=env)
    assert wrong.code == 2 and fake.mutations() == []


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("independent", "independent needs a second, human-only GitHub identity"), ("anything", "must be one of")],
)
def test_an_unbuilt_or_unknown_review_mode_is_refused(
    fake: FakeGitHub, tmp_path: Path, mode: str, expected: str
) -> None:
    refused = write_profile(tmp_path / "input" / "mode.env", _values(MIP_REVIEW_MODE=mode))
    result = run_env_tool(fake, "--plan", env_file=refused, open_session=_never_opened)
    assert result.code == 2 and f"MIP_REVIEW_MODE: {expected}" in result.err


@pytest.mark.parametrize(
    ("kind", "name", "expected"),
    [
        ("variables", "DATABRICKS_HOST", "DATABRICKS_HOST: is identifying but is a variable of fictional-demo"),
        ("secrets", "MIP_APP_NAME", "MIP_APP_NAME: is public but is a secret of fictional-demo"),
    ],
)
def test_a_profile_field_stored_in_the_wrong_place_refuses(
    fake: FakeGitHub, env: Path, kind: str, name: str, expected: str
) -> None:
    item = {"value": "x", "updatedAt": "2026-10-30T00:00:01Z"} if kind == "variables" else {
        "sha256": _sha("x"), "len": 1, "ed25519": False, "updatedAt": "2026-10-30T00:00:01Z"
    }
    fake.seed_environment(CLIENT, **{kind: {name: item}})
    result = run_env_tool(fake, "--plan", env_file=env)
    assert result.code == 2 and expected in result.err
    assert fake.mutations() == []


def test_a_stale_profile_variable_is_named_not_deleted(fake: FakeGitHub, env: Path) -> None:
    _planned, applied = plan_and_apply(fake, env)
    assert applied.code == 0
    trimmed = write_profile(env.parent / "trimmed.env", _values(MIP_PRESENTER_MODE=None))
    planned = run_env_tool(fake, "--plan", env_file=trimmed)
    assert any(note.startswith("stale: MIP_PRESENTER_MODE is a variable") for note in planned.document()["notes"])
    assert not [line for line in planned.steps() if "MIP_PRESENTER_MODE" in line]


def test_the_plan_document_round_trips_as_canonical_json(fake: FakeGitHub, env: Path) -> None:
    planned = run_env_tool(fake, "--plan", env_file=env)
    line = next(line for line in planned.out.splitlines() if line.startswith("canonical plan: "))
    raw = line.removeprefix("canonical plan: ").encode("ascii")
    assert hashlib.sha256(raw).hexdigest() == planned.digest
    assert json.dumps(json.loads(raw), sort_keys=True, separators=(",", ":")).encode() == raw
