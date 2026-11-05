"""The deploy job's dispatch preflight (M1-PR7): exact digest acks, protections (S8), tree scan.

tools/clients/dispatch_preflight.py decides, before scripts/deploy.sh, what a
deploy-dev.yml dispatch may do. The owner attestation layer is deferred
(M1-PR5), so each attestation is an exact digest ack over a purpose-named
body; spike S8 is owed, so an unreadable environment is recorded by a plan
and refused by a mutating phase unless the exact ``protections_unverified``
ack is given. Both ways of S8 are pinned here.
"""

from __future__ import annotations

import io
import json
import subprocess
import tempfile
import urllib.error
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.client_profiles import core_tier_profile, split_contexts
from tools.clients import dispatch_acks as acks
from tools.clients import dispatch_preflight as preflight
from tools.clients import dispatch_protections as protections
from tools.clients import profile as views
from tools.clients import tree_scan
from tools.clients.environment import from_github, resolve
from tools.clients.errors import ProfileRefusal

CLIENT = "client-one"
# The real session factory, before any test patches it with a GitHub API double.
_FROM_ENVIRON = protections.from_environ
REPOSITORY = "example-org/mortgage-intelligence-platform"
SHA = "a" * 40
MAINTAINER = 4242
ENVIRON = {
    "GITHUB_EVENT_NAME": "workflow_dispatch",
    "GITHUB_REPOSITORY": REPOSITORY,
    "GITHUB_REF": "refs/heads/main",
    "GITHUB_WORKFLOW_REF": f"{REPOSITORY}/.github/workflows/deploy-dev.yml@refs/heads/main",
    "GITHUB_SHA": SHA,
    "GITHUB_ACTOR_ID": str(MAINTAINER),
    "GITHUB_TOKEN": "ghs_EXAMPLE_PLACEHOLDER_TOKEN",
    "MIP_DISPATCH_BREAK_GLASS": "true",
}


@pytest.fixture(autouse=True)
def _outside_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    """CI runs with GITHUB_ACTIONS=true, where emit_mask writes ::add-mask:: lines; the masked mode has its own test."""

    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


def _profile_values() -> dict[str, str]:
    return core_tier_profile()


def _contexts(tmp_path: Path, values: dict[str, str] | None = None) -> tuple[str, str]:
    variables, secrets = split_contexts(values or _profile_values())
    (tmp_path / "vars.json").write_text(json.dumps(variables), encoding="utf-8")
    (tmp_path / "secrets.json").write_text(json.dumps(secrets), encoding="utf-8")
    return str(tmp_path / "vars.json"), str(tmp_path / "secrets.json")


def _digest(values: dict[str, str] | None = None) -> str:
    variables, secrets = split_contexts(values or _profile_values())
    return views.digest(resolve(from_github(variables, secrets, environment=CLIENT)))


def _repo(tmp_path: Path, files: dict[str, str] | None = None) -> Path:
    repo = tmp_path / "tree"
    repo.mkdir()
    for rel, text in (files or {"README.md": "placeholders only: <workspace-id>\n"}).items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "--all"], cwd=repo, check=True)
    return repo


def _environment_listing() -> dict[str, Any]:
    return {
        "total_count": 1,
        "environments": [
            {
                "name": CLIENT,
                "can_admins_bypass": False,
                "protection_rules": [
                    {
                        "type": "required_reviewers",
                        "prevent_self_review": False,
                        "reviewers": [{"type": "User", "reviewer": {"id": MAINTAINER}}],
                    }
                ],
                "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True},
            }
        ],
    }


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _opener(responses: dict[str, Any] | None) -> Callable[..., Any]:
    """A GitHub API double: canned JSON by path suffix, or a 403 for every read (S8's worst case)."""

    def opener(request: Any, _timeout: int) -> Any:
        assert request.get_method() == "GET", "the preflight only reads GitHub"
        if responses is None:
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)  # type: ignore[arg-type]
        for suffix, body in responses.items():
            if request.full_url.split("/repos/", 1)[1].split("?", 1)[0].endswith(suffix):
                return _Response(json.dumps(body).encode())
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)  # type: ignore[arg-type]

    return opener


READABLE = {
    "environments": _environment_listing(),
    "deployment-branch-policies": {"total_count": 1, "branch_policies": [{"id": 7, "name": "main", "type": "branch"}]},
}


def _mkdir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _decide(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    phase: str,
    responses: dict[str, Any] | None = READABLE,
    environ: dict[str, str] | None = None,
    repo: Path | None = None,
    values: dict[str, str] | None = None,
) -> preflight.Verdict:
    monkeypatch.setattr(protections, "from_environ", lambda env: _FROM_ENVIRON(env, opener=_opener(responses)))
    work = Path(tempfile.mkdtemp(dir=_mkdir(tmp_path)))
    vars_source, secrets_source = _contexts(work, values)
    return preflight.decide(
        phase=phase,
        environment=CLIENT,
        vars_source=vars_source,
        secrets_source=secrets_source,
        environ={**ENVIRON, **(environ or {})},
        repo=repo or _repo(work),
    )


def _profile_ack(values: dict[str, str] | None = None) -> dict[str, str]:
    return {"MIP_DISPATCH_PROFILE_ACK": acks.profile_ack(CLIENT, _digest(values))}


# ------------------------------------------------------------------ the acks


def test_each_ack_is_purpose_named_and_never_satisfies_another() -> None:
    digest = "b" * 64
    profile = acks.profile_ack(CLIENT, digest)
    unverified = acks.protections_ack(CLIENT, CLIENT, SHA)

    assert len({profile, unverified}) == 2
    assert profile == acks.profile_ack(CLIENT, digest), "deterministic"
    assert profile != acks.profile_ack("client-two", digest) != acks.profile_ack(CLIENT, "c" * 64)
    assert unverified != acks.protections_ack(CLIENT, CLIENT, "b" * 40), "this commit only"
    assert acks.profile_body(CLIENT, digest)["purpose"] == "profile"
    assert acks.protections_body(CLIENT, CLIENT, SHA)["purpose"] == "protections_unverified"
    assert not acks.same_ack(profile, unverified)
    assert not acks.same_ack("", profile) and not acks.same_ack(profile.upper(), profile)


def test_the_operation_acks_are_a_closed_map_each_read_by_its_own_phases() -> None:
    hexes = {name: f"{index:x}" * 64 for index, name in enumerate(sorted(acks.OPERATION_ACKS), 1)}
    assert acks.operation_exports(f"adopt_plan={hexes['adopt_plan']}", "adopt") == {"MIP_ADOPT_PLAN_ACK": hexes["adopt_plan"]}
    assert acks.operation_exports(
        f"quiesce_window={hexes['quiesce_window']}, genie_pin={hexes['genie_pin']}", "release"
    ) == {"MIP_ACK_QUIESCE_WINDOW": hexes["quiesce_window"], "MIP_GENIE_PIN_ACK": hexes["genie_pin"]}
    assert acks.operation_exports("", "plan") == {}
    for text, phase, message in (
        (f"adopt_plan={hexes['adopt_plan']}", "release", "never read by --phase release"),
        (f"rotate_everything={'d' * 64}", "release", "not a reviewed purpose"),
        (f"genie_pin={'e' * 64} genie_pin={'f' * 64}", "release", "given twice"),
        ("genie_pin=short", "release", "each entry is purpose=<64 lowercase hex>"),
        (f"MIP_GENIE_PIN_ACK={'e' * 64}", "release", "each entry is purpose=<64 lowercase hex>"),
    ):
        with pytest.raises(ProfileRefusal) as caught:
            acks.operation_exports(text, phase)
        assert any(message in line for line in caught.value.lines), (text, caught.value.lines)
    # Every env an operation ack exports is one an existing tool recomputes.
    assert {ack.env for ack in acks.OPERATION_ACKS.values()} <= {
        "MIP_ADOPT_PLAN_ACK",
        "MIP_ACK_QUIESCE_WINDOW",
        "MIP_ACK_DATABASE_INSTANCE_UPDATE",
        "MIP_GENIE_PIN_ACK",
        "MIP_ACK_ACCEPTED_EXPOSURE_PLAN",
        "MIP_ACK_DATA_LEDGER_SUPERSEDE",
        "MIP_ACK_DATA_LEDGER_RERUN",
    }


# Pinned literally: the module's own set never parametrizes a test of itself.
MUTATING = ("adopt", "data", "release", "unadopt")


def test_exactly_these_phases_mutate_and_need_the_acks() -> None:
    assert frozenset(MUTATING) == acks.MUTATING_PHASES
    assert frozenset({"plan", *MUTATING}) == acks.PHASES


@pytest.mark.parametrize("mode", ("independent", "", "single_maintainer"))
def test_approval_provenance_refuses_every_review_mode_but_single_maintainer_audited(mode: str) -> None:
    """The schema refuses independent today; the preflight fails closed on its own too."""

    verdict = preflight.Verdict("plan")
    preflight._review_mode(verdict, mode, {"MIP_DISPATCH_BREAK_GLASS": "true"})
    assert verdict.refusals == [
        "approval_provenance: independent review needs approval provenance, deferred with the attestation layer (M1-PR5)"
    ]
    assert not [fact for fact in verdict.facts if fact.startswith("approval provenance")]
    single = preflight.Verdict("plan")
    preflight._review_mode(single, "single_maintainer_audited", {})
    assert [refusal.split(":", 1)[0] for refusal in single.refusals] == ["break_glass"]


# ---------------------------------------------------------------- the verdict


def test_a_plan_passes_without_any_ack_and_prints_the_profile_ack_a_release_needs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    verdict = _decide(tmp_path, monkeypatch, phase="plan")

    assert verdict.refusals == []
    expected = acks.profile_ack(CLIENT, _digest())
    assert f"profile ack (purpose profile) a mutating phase needs: owner_attestation={expected}" in verdict.facts
    assert "approval provenance: not applicable (single_maintainer_audited)" in verdict.facts
    assert "trust-root chain: deferred (M1-PR5)" in verdict.facts
    assert "environment protections: environment_protections_verified (single_maintainer_audited)" in verdict.facts
    assert verdict.exports == {}, "only what a later step reads is exported"


@pytest.mark.parametrize("phase", MUTATING)
def test_a_mutating_phase_refuses_without_the_exact_profile_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    other = acks.profile_ack(CLIENT, "0" * 64)
    for given in ("", other, acks.protections_ack(CLIENT, CLIENT, SHA)):
        verdict = _decide(tmp_path / phase / str(len(given)), monkeypatch, phase=phase, environ={"MIP_DISPATCH_PROFILE_ACK": given})
        assert any(refusal.startswith("profile_ack: dispatch with owner_attestation=") for refusal in verdict.refusals)
    (tmp_path / "ok").mkdir()
    assert _decide(tmp_path / "ok", monkeypatch, phase=phase, environ=_profile_ack()).refusals == []


def test_s8_readable_protections_that_match_let_a_release_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    verdict = _decide(_mkdir(tmp_path / "a"), monkeypatch, phase="release", environ=_profile_ack())

    assert verdict.refusals == []
    assert "environment protections: environment_protections_verified (single_maintainer_audited)" in verdict.facts


@pytest.mark.parametrize("phase", MUTATING)
def test_s8_drifted_protections_are_recorded_by_a_plan_and_refuse_a_mutating_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    drifted = json.loads(json.dumps(READABLE))
    drifted["environments"]["environments"][0]["can_admins_bypass"] = True
    plan = _decide(_mkdir(tmp_path / "plan"), monkeypatch, phase="plan", responses=drifted)
    release = _decide(_mkdir(tmp_path / phase), monkeypatch, phase=phase, responses=drifted, environ=_profile_ack())

    assert plan.refusals == []
    assert any("environment_protections_drift" in fact and "can_admins_bypass" in fact for fact in plan.facts)
    assert release.refusals == [f"environment_protections_drift: github: {CLIENT} can_admins_bypass differs from single_maintainer_audited"]


@pytest.mark.parametrize("phase", MUTATING)
def test_s8_worst_case_unreadable_protections_a_plan_records_and_a_mutating_phase_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """GITHUB_TOKEN cannot read the rules: never 'verified', never 'unprotected'."""

    expected = acks.protections_ack(CLIENT, CLIENT, SHA)
    plan = _decide(_mkdir(tmp_path / "plan"), monkeypatch, phase="plan", responses=None)
    release = _decide(_mkdir(tmp_path / phase), monkeypatch, phase=phase, responses=None, environ=_profile_ack())

    assert plan.refusals == []
    assert protections.UNREADABLE == "environment_protections_unreadable" and plan.exports == {}
    assert "environment protections: environment_protections_unreadable (HTTP 403; spike S8 owed)" in plan.facts
    assert any(fact.startswith("recorded for the plan: environment_protections_unreadable") and expected in fact for fact in plan.facts)
    (refusal,) = release.refusals
    assert refusal.startswith("environment_protections_unreadable: GITHUB_TOKEN could not read")
    assert f"protections_unverified_ack={expected}" in refusal


def test_s8_worst_case_the_exact_protections_unverified_ack_lets_a_release_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exact = acks.protections_ack(CLIENT, CLIENT, SHA)
    ok = _decide(
        _mkdir(tmp_path / "ok"),
        monkeypatch,
        phase="release",
        responses=None,
        environ={**_profile_ack(), "MIP_DISPATCH_PROTECTIONS_ACK": exact},
    )
    assert ok.refusals == []
    assert "protections_unverified ack: exact for this client, environment and commit" in ok.facts
    for name, given in (
        ("another-commit", acks.protections_ack(CLIENT, CLIENT, "b" * 40)),
        ("another-purpose", acks.profile_ack(CLIENT, _digest())),
        ("another-client", acks.protections_ack("client-two", "client-two", SHA)),
    ):
        verdict = _decide(
            _mkdir(tmp_path / name),
            monkeypatch,
            phase="release",
            responses=None,
            environ={**_profile_ack(), "MIP_DISPATCH_PROTECTIONS_ACK": given},
        )
        assert [refusal.split(":", 1)[0] for refusal in verdict.refusals] == ["environment_protections_unreadable"], name


@pytest.mark.parametrize(
    ("environ", "code"),
    (
        ({"GITHUB_EVENT_NAME": "push"}, "workflow_event"),
        ({"GITHUB_EVENT_NAME": "pull_request"}, "workflow_event"),
        ({"GITHUB_REF": "refs/heads/feature"}, "workflow_ref"),
        ({"GITHUB_WORKFLOW_REF": f"{REPOSITORY}/.github/workflows/nightly.yml@refs/heads/main"}, "workflow_ref"),
        ({"MIP_DISPATCH_BREAK_GLASS": "false"}, "break_glass"),
        ({"MIP_DISPATCH_EXPECTED_ACTIVE_DEPLOYMENT_SHA256": "not-a-digest"}, "expected_active_deployment_sha256"),
    ),
)
def test_the_workflow_ref_the_break_glass_and_the_inputs_refuse_every_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environ: dict[str, str], code: str
) -> None:
    verdict = _decide(tmp_path, monkeypatch, phase="plan", environ=environ)
    assert code in [refusal.split(":", 1)[0] for refusal in verdict.refusals]


def test_the_expected_active_deployment_reaches_a_release_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pinned = {**_profile_ack(), "MIP_DISPATCH_EXPECTED_ACTIVE_DEPLOYMENT_SHA256": "c" * 64}
    release = _decide(_mkdir(tmp_path / "release"), monkeypatch, phase="release", environ=pinned)
    data = _decide(_mkdir(tmp_path / "data"), monkeypatch, phase="data", environ=pinned)

    assert release.exports[preflight.EXPECTED_DEPLOYMENT_ENV] == "c" * 64 and release.refusals == []
    assert [refusal.split(":", 1)[0] for refusal in data.refusals] == ["expected_active_deployment_sha256"]


@pytest.mark.parametrize("phase", MUTATING)
def test_a_tracked_identifying_value_is_recorded_by_a_plan_and_refuses_a_mutating_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    host = "dbc-unit.cloud.databricks.com"
    leaked = _repo(_mkdir(tmp_path / "leak"), {"docs/notes.md": f"the workspace is https://{host}/\n", "ok.txt": "fine\n"})
    plan = _decide(_mkdir(tmp_path / "plan"), monkeypatch, phase="plan", repo=leaked)
    release = _decide(_mkdir(tmp_path / phase), monkeypatch, phase=phase, repo=leaked, environ=_profile_ack())

    assert plan.refusals == []
    assert release.refusals == ["tree_scan: 1 tracked files hold a resolved identifying value (DATABRICKS_HOST in docs/notes.md)"]
    assert any("tree_scan" in fact for fact in plan.facts)
    assert all(host not in line for line in [*plan.facts, *release.refusals]), "the field and the file, never the value"


def test_the_cli_writes_exports_only_on_a_pass_and_never_prints_a_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(protections, "from_environ", lambda env: _FROM_ENVIRON(env, opener=_opener(READABLE)))
    vars_source, secrets_source = _contexts(tmp_path)
    github_env, summary = tmp_path / "github.env", tmp_path / "summary.md"
    argv = [
        "--phase", "release", "--environment", CLIENT, "--vars-json", vars_source, "--secrets-json", secrets_source,
        "--repo", str(_repo(tmp_path)), "--github-env", str(github_env), "--step-summary", str(summary),
    ]
    environ = {**ENVIRON, **_profile_ack(), "MIP_DISPATCH_OPERATION_ACKS": f"quiesce_window={'d' * 64}"}

    assert preflight.main(argv, environ=environ) == 0
    exported = github_env.read_text(encoding="utf-8").splitlines()
    assert exported == [f"MIP_ACK_QUIESCE_WINDOW={'d' * 64}"]
    out = capsys.readouterr()
    text = out.out + out.err + summary.read_text(encoding="utf-8")
    profile = resolve(from_github(*split_contexts(_profile_values()), environment=CLIENT))
    for value in (*views.masks(profile), *views.derived_masks(profile), *(v for k, v in _profile_values().items() if "SECRET" in k)):
        assert value not in text, "the preflight prints codes, digests, field names and paths only"
    assert "- verdict: passed" in text
    github_env.unlink()
    # data never reads the quiesce-window ack: refused, nothing exported.
    assert preflight.main(["--phase", "data", *argv[2:]], environ=environ) == 2
    assert not github_env.exists(), "a refused dispatch exports nothing"


def test_inside_actions_the_only_lines_that_hold_a_value_are_runner_mask_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(protections, "from_environ", lambda env: _FROM_ENVIRON(env, opener=_opener(READABLE)))
    vars_source, secrets_source = _contexts(tmp_path)
    argv = ["--phase", "plan", "--environment", CLIENT, "--vars-json", vars_source, "--secrets-json", secrets_source, "--repo", str(_repo(tmp_path))]

    assert preflight.main(argv, environ=ENVIRON) == 0
    out = capsys.readouterr()
    profile = resolve(from_github(*split_contexts(_profile_values()), environment=CLIENT))
    registered = {line.removeprefix("::add-mask::") for line in out.err.splitlines() if line.startswith("::add-mask::")}
    assert registered == {*views.masks(profile), *views.derived_masks(profile)}
    printed = "\n".join(line for line in (out.out + out.err).splitlines() if not line.startswith("::add-mask::"))
    assert not [value for value in registered if value in printed]


def test_the_tree_scan_never_refuses_a_platform_default_or_the_repositorys_own_group_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default account host and the repository's own group names are not a client's own: a report at most."""

    profile = resolve(from_github(*split_contexts(_profile_values()), environment=CLIENT))
    needles = tree_scan.needles(profile)
    assert profile.account_host == "https://accounts.cloud.databricks.com"
    assert not [value for value in needles if "accounts.cloud.databricks.com" in value], "a platform default"
    assert needles["mip-admin"] == tree_scan.Needle("MIP_ADMIN_GROUP_NAME", True)
    assert needles["mip-approver"] == tree_scan.Needle("MIP_APPROVER_GROUP_NAME", True)
    assert needles["mip-data-owners"] == tree_scan.Needle("MIP_UC_APPROVED_OWNER_PRINCIPALS", True)
    assert needles["metastore-owners-alpha"] == tree_scan.Needle("MIP_UC_METASTORE_OWNER", False)
    tree = _repo(
        _mkdir(tmp_path / "tree"),
        {"docs/groups.md": "the admin group is mip-admin, the approvers mip-approver; the console is https://accounts.cloud.databricks.com\n"},
    )
    release = _decide(_mkdir(tmp_path / "release"), monkeypatch, phase="release", repo=tree, environ=_profile_ack())

    assert release.refusals == []
    assert (
        "tree scan: 1 tracked files name the repository's own convention name of MIP_ADMIN_GROUP_NAME, "
        "MIP_APPROVER_GROUP_NAME (reported, not refused)"
    ) in release.facts, "distinct files, not hits"
    assert "tree scan: no tracked file holds a resolved identifying value" not in release.facts, "only when nothing was found"
    owned = _repo(_mkdir(tmp_path / "owned"), {"docs/owner.md": "the metastore owner is metastore-owners-alpha\n"})
    refused = _decide(_mkdir(tmp_path / "refused"), monkeypatch, phase="release", repo=owned, environ=_profile_ack())
    assert refused.refusals == ["tree_scan: 1 tracked files hold a resolved identifying value (MIP_UC_METASTORE_OWNER in docs/owner.md)"]
    clean = _decide(_mkdir(tmp_path / "clean"), monkeypatch, phase="release", environ=_profile_ack())
    assert "tree scan: no tracked file holds a resolved identifying value" in clean.facts


def test_the_tree_scan_exemption_is_a_closed_set_each_name_in_its_own_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A <prefix>- spelling is not a convention: an email, a client's own group, a name another field also holds."""

    values = core_tier_profile(
        MIP_ADMIN_EMAILS="mip-ops@acme-mortgage-client.example",
        MIP_ADMIN_GROUP_NAME="mip-acme-mortgage-admins",
        MIP_GENIE_MANAGER_PRINCIPALS="mip-approver",
    )
    needles = tree_scan.needles(resolve(from_github(*split_contexts(values), environment=CLIENT)))
    assert needles["mip-ops@acme-mortgage-client.example"] == tree_scan.Needle("MIP_ADMIN_EMAILS", False)
    assert needles["mip-acme-mortgage-admins"] == tree_scan.Needle("MIP_ADMIN_GROUP_NAME", False)
    assert needles["mip-approver"] == tree_scan.Needle("MIP_GENIE_MANAGER_PRINCIPALS", False), "never exempt by field order"
    tree = _repo(
        _mkdir(tmp_path / "tree"),
        {"docs/x.md": "ops: mip-ops@acme-mortgage-client.example, admins: mip-acme-mortgage-admins, managers: mip-approver\n"},
    )
    plan = _decide(_mkdir(tmp_path / "plan"), monkeypatch, phase="plan", repo=tree, values=values)
    release = _decide(
        _mkdir(tmp_path / "release"), monkeypatch, phase="release", repo=tree, values=values, environ=_profile_ack(values)
    )

    assert release.refusals == [
        "tree_scan: 1 tracked files hold a resolved identifying value (MIP_ADMIN_EMAILS in docs/x.md, "
        "MIP_ADMIN_GROUP_NAME in docs/x.md, MIP_GENIE_MANAGER_PRINCIPALS in docs/x.md)"
    ]
    assert plan.refusals == [] and any(fact.startswith("recorded for the plan: tree_scan: 1 tracked files") for fact in plan.facts)
    assert not [fact for fact in [*plan.facts, *release.facts] if fact.startswith("tree scan: ")], "no clean or convention fact"
