"""B4(c): the adopt phases' bundle calls bind the one Genie title match an exact ack names.

Before an adoption nothing pins the governed Genie space (no pin record, and
the signed v7 record that will name it is adopt-live's last write), so
``bundle_env`` refused the adopt phases' plan, binds, unbinds and summary
``no pin names the governed Genie space yet`` whatever ``genie_pin`` said.
``--adopt-acked-genie-pin`` (bundle_env_adopt.ADOPT_GENIE_FLAG) lets those
calls, and only those, bind the one title match whose read-only pin plan's
sha256 is the shell's exact ``MIP_GENIE_PIN_ACK``, through the provisioner's
own ``genie_space_pin.resolve``. A pin still wins, a pin refusal still
refuses, and everything else refuses before the CLI runs. Every call names
``-t client`` with the paychex-shaped profile in ``.env.local``; the Genie
reads are the in-memory PinWorkspace (tests/fixtures/genie_pin_workspace.py).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.fixtures.client_profiles import env_file_text, paychex_shaped_profile
from tests.fixtures.genie_pin_workspace import (
    APP,
    DEPLOYER,
    TITLE,
    PinWorkspace,
    Space,
    patch_rollback_scope,
)
from tools.clients.environment import KNOWN_NAMES
from tools.databricks import bundle_env, bundle_env_adopt
from tools.databricks import genie_space_pin as pin

FLAG = bundle_env_adopt.ADOPT_GENIE_FLAG
ACK_ENV = bundle_env_adopt.GENIE_PIN_ACK_ENV
REPO = Path(__file__).resolve().parents[2]
CANDIDATE = "acked-candidate-space-0001"
MANAGERS = ("creator@lender.example", "genie-workbench-sp")
AUTHORITY = (
    "--adopt-plan", "/private/plan.json", "--adopt-plan-sha256", "a" * 64,
    "--adopt-lease-id", "lease-1", "--adopt-source-git-sha", "0" * 40,
)


def digest(*, space_id: str = CANDIDATE, managers: tuple[str, ...] = MANAGERS, deployer: str = DEPLOYER) -> str:
    """The sha256 of the read-only pin plan the step-0 pin check prints for that space."""

    plan = pin.pin_plan(app_name=APP, title=TITLE, space_id=space_id, managers=tuple(sorted(managers)), deployer=deployer)
    return pin.plan_sha256(plan)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bundle_env, "BUNDLE_ROOT", tmp_path / "bundle-root")
    monkeypatch.delenv("DATABRICKS_BUNDLE_ROOT", raising=False)
    for name in (*KNOWN_NAMES, ACK_ENV, "GENIE_SPACE_ID", "DATABRICKS_WAREHOUSE_ID"):
        monkeypatch.delenv(name, raising=False)


def public(err: str) -> str:
    """``err`` without its runner-mask registrations (``::add-mask::``, under GitHub Actions only)."""

    return "\n".join(line for line in err.splitlines() if not line.startswith("::add-mask::"))


def world(*titles: str) -> PinWorkspace:
    """Nothing pinned; one space per title (the governed one first), each with MANAGERS."""

    ids = [CANDIDATE, *(f"other-space-{n:04d}" for n in range(1, len(titles)))]
    return PinWorkspace(spaces=[Space(space_id, title=title, managers=MANAGERS) for space_id, title in zip(ids, titles, strict=True)])


def run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    argv: list[str],
    *,
    workspace: PinWorkspace,
    env_local_extra: str = "",
) -> tuple[int, dict[str, Any]]:
    env_local = tmp_path / ".env.local"
    env_local.write_text(env_file_text(paychex_shaped_profile()) + env_local_extra, encoding="utf-8")
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    monkeypatch.setattr(bundle_env, "REPO", tmp_path)
    patch_rollback_scope(monkeypatch)
    monkeypatch.setattr(bundle_env, "_genie_workspace_client", lambda _env: workspace)
    monkeypatch.setattr(bundle_env.render_sql, "render", lambda **_kwargs: (1, 0, 0))
    monkeypatch.setattr(bundle_env.scheduled_refresh_posture, "apply", lambda env, **_kw: "refresh: test")
    # An adopt plan's state edit: its own check (lease, plan, ack) is bundle_env_adopt's tests'.
    monkeypatch.setattr(bundle_env.bundle_env_adopt, "edit_verifier", lambda *_a, **_k: lambda *_e: None)
    captured: dict[str, Any] = {}

    def cli(command: list[str], *, env: dict[str, str], check: bool) -> Any:
        captured.update(command=command, env=env)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(bundle_env.subprocess, "run", cli)
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", *argv])
    return bundle_env.main(), captured


ADOPT_CALLS = {
    "plan": ["plan", "-t", "client", "-o", "json", FLAG],
    "summary": ["summary", "-t", "client", "-o", "json", FLAG],
    "bind": ["deployment", "bind", "mip_ref_seed", "1001", "-t", "client", "--auto-approve", *AUTHORITY, FLAG],
    "unbind": ["deployment", "unbind", "mip_ref_seed", "-t", "client", *AUTHORITY, FLAG],
}


# --- the acked title match binds --------------------------------------------------------


@pytest.mark.parametrize("call", sorted(ADOPT_CALLS))
def test_with_nothing_pinned_the_flag_and_the_exact_ack_bind_the_title_match(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str], call: str
) -> None:
    monkeypatch.setenv(ACK_ENV, digest())
    workspace = world(TITLE, "Another Team Space")

    rc, captured = run(monkeypatch, tmp_path, ADOPT_CALLS[call], workspace=workspace)

    assert rc == 0, capsys.readouterr().err
    assert captured["env"]["BUNDLE_VAR_genie_space_id"] == CANDIDATE
    assert FLAG not in captured["command"], "the flag is wrapper-only and never reaches the CLI"
    assert captured["command"][:3] == ["databricks", "bundle", ADOPT_CALLS[call][0]]
    assert workspace.mutations == [], "nothing writes a pin; adopt-live's signed v7 record pins the space"
    assert CANDIDATE not in public(capsys.readouterr().err), "the id is never printed"


# --- every other shape refuses before the CLI -------------------------------------------


REFUSALS = {
    "no ack": (lambda mp: None, world(TITLE), "no ack names it"),
    "another plan's ack (another holder)": (
        lambda mp: mp.setenv(ACK_ENV, digest(managers=(*MANAGERS, "stranger@other.example"))),
        world(TITLE),
        "the ack names another plan",
    ),
    "another plan's ack (another deployer)": (
        lambda mp: mp.setenv(ACK_ENV, digest(deployer="00000000-0000-4000-8000-000000000004")),
        world(TITLE),
        "the ack names another plan",
    ),
    "an uppercase ack": (
        lambda mp: mp.setenv(ACK_ENV, digest().upper()),
        world(TITLE),
        f"{ACK_ENV} must be the 64-hex sha256",
    ),
    "a 63-hex ack": (lambda mp: mp.setenv(ACK_ENV, digest()[:-1]), world(TITLE), f"{ACK_ENV} must be the 64-hex sha256"),
    "a padded ack": (lambda mp: mp.setenv(ACK_ENV, f" {digest()}"), world(TITLE), f"{ACK_ENV} must be the 64-hex sha256"),
    "two title matches": (lambda mp: mp.setenv(ACK_ENV, digest()), world(TITLE, TITLE), "2 Genie spaces carry the governed title"),
    "no title match": (
        lambda mp: mp.setenv(ACK_ENV, digest()),
        world("Another Team Space"),
        "no Genie space carries the governed title; an adoption binds the live App's space",
    ),
}


@pytest.mark.parametrize("call", sorted(ADOPT_CALLS))
@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_the_flag_refuses_without_the_exact_shell_ack_for_exactly_one_title_match(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str], case: str, call: str
) -> None:
    set_ack, workspace, message = REFUSALS[case]
    set_ack(monkeypatch)

    rc, captured = run(monkeypatch, tmp_path, ADOPT_CALLS[call], workspace=workspace)

    err = public(capsys.readouterr().err)
    assert rc == 2 and captured == {}, "the CLI never runs"
    assert "governed Genie binding verification failed" in err and message in err
    assert CANDIDATE not in err and digest() not in err, "a refusal names rules, never an id or a digest"


def test_an_ack_only_in_dotenv_is_no_ack(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The ack is a one-run shell export (the dispatch carrier), never an .env.local line."""

    rc, captured = run(
        monkeypatch, tmp_path, ADOPT_CALLS["plan"], workspace=world(TITLE), env_local_extra=f"{ACK_ENV}={digest()}\n"
    )

    assert rc == 2 and captured == {}
    assert "no ack names it" in capsys.readouterr().err


FLAG_MISUSE = {
    "validate": (["validate", "-t", "client", FLAG], "accepted once, by plan, summary or deployment bind|unbind only"),
    "validate ci": (["validate", "-t", "ci", FLAG], "accepted once, by plan, summary or deployment bind|unbind only"),
    "deploy": (
        ["deploy", "-t", "client", "--select", "jobs.mip_ref_seed", FLAG],
        "accepted once, by plan, summary or deployment bind|unbind only",
    ),
    "twice": (["plan", "-t", "client", "-o", "json", FLAG, FLAG], "accepted once"),
    "a value": (["plan", "-t", "client", "-o", "json", f"{FLAG}=1"], "accepted once"),
    "beside the unprovisioned flag": (
        ["plan", "-t", "client", "-o", "json", FLAG, bundle_env.UNPROVISIONED_GENIE_FLAG],
        "never with --allow-unprovisioned-genie",
    ),
    "the unprovisioned flag on summary": (
        ["summary", "-t", "client", "-o", "json", FLAG, bundle_env.UNPROVISIONED_GENIE_FLAG],
        f"{bundle_env.UNPROVISIONED_GENIE_FLAG} is accepted once, by plan only",
    ),
    "the governed App bind": (
        ["deployment", "bind", "mip_app", "mip-app", "-t", "client", FLAG],
        f"{FLAG} goes with an adopt plan's state edit only",
    ),
    "the governed App unbind": (
        ["deployment", "unbind", "mip_app", "-t", "client", FLAG],
        f"{FLAG} goes with an adopt plan's state edit only",
    ),
}


@pytest.mark.parametrize("case", sorted(FLAG_MISUSE))
def test_the_flag_is_refused_outside_the_adopt_phases_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    argv, message = FLAG_MISUSE[case]
    monkeypatch.setenv(ACK_ENV, digest())

    rc, captured = run(monkeypatch, tmp_path, argv, workspace=world(TITLE))

    assert rc == 2 and captured == {}
    assert message in capsys.readouterr().err


# --- a pin always wins ---------------------------------------------------------------------


def test_a_pin_wins_and_the_ack_is_never_read(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(ACK_ENV, digest(managers=("stranger@other.example",)))

    def never(*_a: Any, **_k: Any) -> str:
        raise AssertionError("the acked title path runs only when nothing pins a space")

    monkeypatch.setattr(bundle_env_adopt, "acked_title_space_id", never)
    for call in ADOPT_CALLS.values():
        monkeypatch.setattr(bundle_env, "_pinned_genie_space_id", lambda _env, _profile: "pinned-space-0001")
        rc, captured = run(monkeypatch, tmp_path, call, workspace=world(TITLE))
        assert rc == 0 and captured["env"]["BUNDLE_VAR_genie_space_id"] == "pinned-space-0001"


def test_a_pin_refusal_still_refuses_with_the_flag_and_the_exact_ack(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(ACK_ENV, digest())

    def refused(_env: dict[str, str], _profile: Any) -> str:
        raise pin.GeniePinRefusal("the Genie pin record and the signed rollback record name different spaces")

    rc, captured = run(monkeypatch, tmp_path, ADOPT_CALLS["plan"], workspace=world(TITLE))
    assert rc == 0, "the control: the same call binds while nothing pins a space"
    monkeypatch.setattr(bundle_env, "_pinned_genie_space_id", refused)
    captured.clear()

    assert bundle_env.main() == 2 and captured == {}
    assert "name different spaces" in capsys.readouterr().err


def test_a_pin_that_appears_after_the_pin_check_refuses_the_acked_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the provisioner's ``adopt`` binds: a ``use`` (a pin written between bundle_env's two reads) refuses."""

    import json

    from tests.fixtures.client_profiles import paychex_shaped_profile as shaped
    from tests.fixtures.genie_pin_workspace import signing_env
    from tools.clients.environment import RawProfile, resolve

    for name, value in signing_env().items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY", raising=False)
    patch_rollback_scope(monkeypatch)
    workspace = world(TITLE)
    record = {
        "version": 1, "app_name": APP, "title": TITLE, "space_id": CANDIDATE, "provenance": "acked",
        "plan_sha256": digest(), "pinned_at": "2026-11-11T00:00:00+00:00", "retired_space_ids": [],
    }
    workspace.secrets_by_key[pin.pin_key(APP)] = json.dumps(pin.sign_pin(record), sort_keys=True, separators=(",", ":"))
    profile = resolve(RawProfile(values=shaped()))

    with pytest.raises(pin.GeniePinRefusal, match="a pin names the governed Genie space now"):
        bundle_env_adopt.acked_title_space_id(workspace, profile, environ={ACK_ENV: digest()})
    del workspace.secrets_by_key[pin.pin_key(APP)]
    assert bundle_env_adopt.acked_title_space_id(workspace, profile, environ={ACK_ENV: digest()}) == CANDIDATE


@pytest.mark.parametrize("subcmd", ("plan", "summary"))
def test_without_the_flag_the_exact_ack_binds_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str], subcmd: str
) -> None:
    """Only the adopt phases' own calls pass the flag: every other plan or summary still refuses."""

    monkeypatch.setenv(ACK_ENV, digest())

    rc, captured = run(monkeypatch, tmp_path, [subcmd, "-t", "client", "-o", "json"], workspace=world(TITLE))

    assert rc == 2 and captured == {}
    assert "no pin names the governed Genie space yet" in capsys.readouterr().err


# --- who passes it -------------------------------------------------------------------------


def _files_naming(pattern: str) -> set[str]:
    result = subprocess.run(
        ["git", "grep", "-lP", "-e", pattern, "--", "tools", "scripts", ".github", "Makefile", "app.yaml", "databricks.yml"],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode in (0, 1), result.stderr
    return set(result.stdout.split())


def test_only_the_adopt_phases_two_callers_pass_the_flag() -> None:
    # The positive control: the same search finds the unprovisioned flag's one caller.
    assert "scripts/lib/deploy_bundle_plan_gate_lifecycle.sh" in _files_naming(r"--allow-unprovisioned-genie\b")

    named = _files_naming(r"--adopt-acked-genie-pin\b|\bADOPT_GENIE_FLAG\b")

    assert named == {
        "tools/databricks/bundle_env_adopt.py",  # the definition and its parser
        "tools/databricks/bundle_env.py",  # the wrapper that strips it
        "tools/databricks/install_state_adopt.py",  # plan, bind and unbind
        "tools/databricks/adopt_live_facts.py",  # the post-bind summary
    }
    assert not any(path.startswith("scripts/") for path in named), "no shell call binds a title match"


def test_the_callers_argv_carry_the_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from tools.databricks import adopt_live_facts
    from tools.databricks import install_state_adopt as adopt

    calls: list[list[str]] = []
    children: list[dict[str, str]] = []
    signing_keys = ("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", "MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY")

    def record(argv: list[str], **kwargs: Any) -> Any:
        calls.append(list(argv))
        children.append(dict(kwargs["env"]))
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setenv(ACK_ENV, digest())
    for name in signing_keys:
        monkeypatch.setenv(name, "PLACEHOLDER-not-a-key")
    monkeypatch.setattr(adopt.subprocess, "run", record)
    monkeypatch.setattr(adopt, "parse_plan", lambda text: text)
    cli = adopt.BundleEnvCli(python="python")
    authority = adopt.Authority(tmp_path / "plan.json", "a" * 64, "lease-1", "0" * 40)
    cli.plan()
    cli.bind("mip_ref_seed", "1001", authority)
    cli.unbind("mip_ref_seed", authority)
    monkeypatch.setattr(adopt_live_facts.subprocess, "run", record)
    monkeypatch.setattr(
        "tools.databricks.app_rollback_resource_contract.reviewed_app_resource_contract", lambda summary: []
    )
    adopt_live_facts.reviewed_bindings()

    authority_flags = [
        "--adopt-plan", str(tmp_path / "plan.json"), "--adopt-plan-sha256", "a" * 64,
        "--adopt-lease-id", "lease-1", "--adopt-source-git-sha", "0" * 40,
    ]
    assert [call[1:] for call in calls] == [
        ["-m", "tools.databricks.bundle_env", "plan", "-t", "client", "-o", "json", FLAG],
        ["-m", "tools.databricks.bundle_env", "deployment", "bind", "mip_ref_seed", "1001", "-t", "client",
         "--auto-approve", *authority_flags, FLAG],
        ["-m", "tools.databricks.bundle_env", "deployment", "unbind", "mip_ref_seed", "-t", "client",
         *authority_flags, FLAG],
        ["-m", "tools.databricks.bundle_env", "summary", "-t", "client", "-o", "json", FLAG],
    ]
    # The flag binds nothing without the ack: each child's own environment carries it, and no signing key.
    assert [child.get(ACK_ENV) for child in children] == [digest()] * 4
    assert not any(name in child for child in children for name in signing_keys)


def test_the_docs_say_how_a_mid_run_holder_change_ends_and_recovers() -> None:
    """The judge's required note: the undo's unbinds re-resolve the match; exit 3, half-bound; unadopt with a fresh ack.

    Also what a CI job log shows at each point (bundle_env's own line never
    reaches it) and that a refusal at adopt-live's summary needs 10B-0 and
    10B-1 again, not 10B-2 alone: the binds changed the adopt plan's digest.
    """

    from tools.databricks import adopt_live
    from tools.databricks import install_state_adopt as adopt

    def flat(path: str) -> str:
        return " ".join((REPO / path).read_text(encoding="utf-8").split())

    runbook = flat("docs/clients/first-client-cutover.md")
    for anchor in (
        "**A Genie holder that changes mid-run.**",
        "and the undo's unbinds",
        "exit 3, **half-bound**",
        "`phase=unadopt` and a **fresh** `genie_pin`",
        "| B4 | fixed |",
        "**10B walked with #344, #345, #346 and B4(c) all on `main`**",
        "**No known code gap is left on 10B's path:**",
        # What the job log shows, by the tools' own prefixes; the cause is in the private report.
        "The job log never prints that text",
        f"`{adopt._PREFIX} REFUSED: bundle plan failed (exit 2)`",
        f"`{adopt._PREFIX} REFUSED bind_failed: BundleCliError during the binds`",
        f"`{adopt_live._PREFIX} REFUSED bindings_differ: the bundle summary failed (exit 2)`",
        "written only to the run's private report (`cli_errors`",
        # Wave 7f: the adopt phases publish that report; the log still never carries it.
        "never to the log; the deploy publishes that report to the deployer's plan-reports folder",
        "A read-only dispatch without `genie_pin` reveals it",
        # A summary-time refusal: the binds stay, so the adopt plan digest moved too.
        "re-running 10B-2 with only a fresh `genie_pin` cannot pass",
        "refuses `ack_mismatch` before the lease",
        "10B-0 (review, a fresh `genie_pin`), 10B-1 (a fresh `adopt_plan` and `adopt_live`), then 10B-2",
    ):
        assert anchor in runbook, anchor
    assert "so re-run 10B-2, or unadopt, each with a fresh `genie_pin`" not in runbook
    assert "bind plan: `governed Genie binding verification failed` with" not in runbook
    assert "B4(c), until it lands" not in runbook and "Until B4(c) lands" not in runbook
    deployment = flat("docs/deployment.md")
    assert "**The adopt phases (B4(c)).**" in deployment
    assert "the undo's unbinds included" in deployment and "with a fresh ack" in deployment
    assert "a job log names only the call that failed" in deployment
    assert "a re-run needs a fresh adopt plan digest as well as a fresh ack" in deployment
    assert "the cutover runbook's B4(c), a follow-up" not in deployment
    assert "B4(c), the next follow-up" not in flat("docs/clients/onboarding.md")
