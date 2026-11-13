"""deploy-dev.yml's freeze and thaw phases: the freeze marker, signed in CI (wave 7c).

A client's proof-signing key lives only in its GitHub Environment, so no shell
can sign the marker the emergency publisher obeys (M2-PR7).
tools/clients/publisher_freeze.py signs it in the deploy job: the key is read
in-process from the job's ``.env.local``, never placed in ``os.environ`` and
never printed; the freeze is one create-only marker for 30 days; a thaw needs
the exact ``freeze_thaw`` ack of the one marker it thaws. Every test runs
against the in-memory workspace: no Databricks call is made.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.services.ai_gateway_proof_attestation import derive_gateway_proof_verify_key
from tests.fixtures.client_profiles import core_tier_profile, split_contexts
from tests.fixtures.deploy_script import deploy_contract_text
from tests.unit.test_app_deployment_freeze import (
    RELEASE,
    _AclTree,
    _entry,
    _publish_argv,
    _stage,
)
from tools.clients import ci_profile, dispatch_acks
from tools.clients import publisher_freeze as tool
from tools.clients.environment import from_github, resolve
from tools.clients.errors import ProfileRefusal
from tools.databricks import app_deployment_freeze as freezer
from tools.databricks import app_deployment_lease as lease
from tools.databricks import app_release_source as source
from tools.databricks.operation_ack import ack_digest

REPO = Path(__file__).resolve().parents[2]
NOW = 1_800_000_000
THIRTY_DAYS = 30 * 24 * 3600
APP = "mip-app"
DEPLOYER = "deployer-app-id"  # a service principal reports its application id as /Me userName
# A planted key no other fixture holds, so finding it anywhere is this tool's doing.
PLANTED = base64.urlsafe_b64encode(bytes(range(71, 103))).decode().rstrip("=")
PLANTED_VERIFY = derive_gateway_proof_verify_key(PLANTED)
OTHER_KEY = base64.urlsafe_b64encode(bytes(range(140, 172))).decode().rstrip("=")
KEY_ENV = (
    "MIP_AI_GATEWAY_PROOF_SIGNING_KEY",
    "MIP_AI_GATEWAY_PROOF_VERIFY_KEY",
    "MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY",
    "MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS",
)
FROZEN_LINE = re.compile(r"^\[mip-publisher-freeze\] frozen: marker ([0-9a-f-]{36}) until (\d+)$", re.M)
THAW_LINE = re.compile(
    r'^\[mip-publisher-freeze\] thaw with -f phase=thaw -f operation_acks="(freeze_thaw=([0-9a-f]{64}))"$', re.M
)


@pytest.fixture(autouse=True)
def _the_freeze_steps_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The step's own env: GitHub Actions, the DEFAULT profile, and no key of any kind."""

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    for name in (*KEY_ENV, tool.ACK_ENV):
        # setenv first: it records the name's state even when absent, so the public
        # keys the tool writes to os.environ are undone and never reach another test.
        monkeypatch.setenv(name, "recorded-for-undo")
        monkeypatch.delenv(name)


def _env_file(tmp_path: Path, **overrides: str | None) -> Path:
    """An ``.env.local`` as tools/clients/ci_profile.py writes one: ``NAME='value'`` lines."""

    values: dict[str, str | None] = {"MIP_APP_NAME": APP, tool.SIGNING_KEY: PLANTED, **overrides}
    path = tmp_path / ".env.local"
    path.write_text("".join(f"{name}='{value}'\n" for name, value in values.items() if value is not None), encoding="utf-8")
    return path


def _workspace(caller: str = DEPLOYER) -> SimpleNamespace:
    return SimpleNamespace(
        workspace=_AclTree(),
        current_user=SimpleNamespace(me=lambda: SimpleNamespace(user_name=caller)),
        service_principals=SimpleNamespace(list=lambda **_kw: [SimpleNamespace(application_id=DEPLOYER)]),
    )


def _run(
    phase: str, env_file: Path, workspace: Any, *, now: float = NOW, ack: str | None = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> int:
    if ack is not None:
        assert monkeypatch is not None
        monkeypatch.setenv(tool.ACK_ENV, ack)
    return tool.main(
        ["--phase", phase, "--env-file", str(env_file)], workspace_factory=lambda: workspace, clock=lambda: now
    )


def _document(workspace: Any, marker_id: str) -> dict[str, Any]:
    return json.loads(workspace.workspace.download(freezer.freeze_path(APP, marker_id)).read())


def _expected_ack(workspace: Any, marker_id: str, *, app: str = APP) -> str:
    """The thaw ack, recomputed here from the marker's own bytes (never through the tool)."""

    document = json.loads(workspace.workspace.download(freezer.freeze_path(app, marker_id)).read())
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    plan = {"app_name": app, "marker_id": marker_id, "freeze_sha256": hashlib.sha256(canonical).hexdigest()}
    return ack_digest("freeze_thaw", plan)


def _frozen(tmp_path: Path, capsys: pytest.CaptureFixture[str], workspace: Any | None = None) -> tuple[Any, Path, str, str]:
    """A CI freeze of APP at NOW: (workspace, env file, marker id, the printed thaw ack)."""

    workspace = workspace or _workspace()
    env_file = _env_file(tmp_path)
    assert _run("freeze", env_file, workspace) == 0
    out = capsys.readouterr().out
    marker = FROZEN_LINE.search(out)
    thaw = THAW_LINE.search(out)
    assert marker and thaw, out
    return workspace, env_file, marker.group(1), thaw.group(2)


def _no_workspace() -> Any:
    raise AssertionError("refused before any workspace client is built")


# ------------------------------------------------------------------ CI only


@pytest.mark.parametrize("actions", (None, "false", "1", "TRUE"))
@pytest.mark.parametrize("phase", ("freeze", "thaw"))
def test_outside_github_actions_it_refuses_before_reading_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], phase: str, actions: str | None
) -> None:
    if actions is None:
        monkeypatch.delenv("GITHUB_ACTIONS")
    else:
        monkeypatch.setenv("GITHUB_ACTIONS", actions)
    env_file = _env_file(tmp_path)

    assert tool.main(["--phase", phase, "--env-file", str(env_file)], workspace_factory=_no_workspace) == 2
    err = capsys.readouterr().err
    assert err.startswith("[mip-publisher-freeze] REFUSED ci_only: ")
    assert "python -m tools.databricks.app_deployment_lease freeze|thaw" in err, "the operator's route"
    assert not [name for name in KEY_ENV if name in os.environ], "not even a public key was derived"


def test_the_phase_is_one_of_exactly_two(tmp_path: Path) -> None:
    for phase in ("plan", "release", "Freeze", ""):
        with pytest.raises(SystemExit) as refused:
            tool.main(["--phase", phase, "--env-file", str(_env_file(tmp_path))], workspace_factory=_no_workspace)
        assert refused.value.code == 2


# ------------------------------------------------------------------ freeze


def test_freeze_writes_one_marker_that_verifies_under_the_derived_key_for_30_days(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace, _env, marker_id, printed_ack = _frozen(tmp_path, capsys)
    tree = workspace.workspace

    assert tree.ops("mkdirs") == [freezer.LEASE_ROOT], "a missing root is created bare"
    assert tree.set_permissions_calls == 0 and tree.acl == [], "it never sets a permission"
    assert tree.ops("upload") == [freezer.freeze_path(APP, marker_id)], "exactly one create-only file"
    assert tree.ops("delete") == []
    document = _document(workspace, marker_id)
    assert document["until"] == NOW + THIRTY_DAYS == NOW + freezer.MAX_FREEZE_SECONDS
    assert document["attestation_verify_key"] == PLANTED_VERIFY, "derived from the env file's key"
    assert (document["app_name"], document["kind"], document["frozen_by"]) == (APP, "freeze", DEPLOYER)
    (marker,) = freezer.judge_markers(workspace, now=NOW)
    assert (marker.marker_id, marker.frozen, marker.reason) == (marker_id, True, f"frozen until {NOW + THIRTY_DAYS}")
    assert freezer.judge_markers(workspace, now=NOW + THIRTY_DAYS)[0].reason == "expired"
    # The printed ack is the thaw plan's exact digest, and the pair is one the thaw dispatch carries.
    assert printed_ack == _expected_ack(workspace, marker_id)
    assert dispatch_acks.operation_exports(f"freeze_thaw={printed_ack}", "thaw") == {tool.ACK_ENV: printed_ack}


def test_freeze_prints_exactly_the_marker_and_the_thaw_dispatch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    workspace = _workspace()
    assert _run("freeze", _env_file(tmp_path), workspace) == 0
    out, err = capsys.readouterr()
    (marker,) = freezer.judge_markers(workspace, now=NOW)

    assert err == ""
    assert out.splitlines() == [
        f"[mip-publisher-freeze] frozen: marker {marker.marker_id} until {NOW + THIRTY_DAYS}",
        f'[mip-publisher-freeze] thaw with -f phase=thaw -f operation_acks="freeze_thaw={_expected_ack(workspace, marker.marker_id)}"',
    ]
    assert DEPLOYER not in out and APP not in out, "never the freezing identity; marker ids and digests only"


def test_the_env_file_is_the_one_the_deploy_job_writes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """tools/clients/ci_profile.py's own .env.local carries both names the tool reads."""

    values = core_tier_profile()
    profile = resolve(from_github(*split_contexts(values), environment="client-one"))
    env_file = tmp_path / ".env.local"
    env_file.write_text(ci_profile.env_local_text(profile), encoding="utf-8")
    workspace = _workspace()

    assert _run("freeze", env_file, workspace) == 0
    (marker,) = freezer.judge_markers(workspace, now=NOW)
    assert marker.app_name == values["MIP_APP_NAME"] and marker.reason.startswith("frozen until ")
    document = json.loads(workspace.workspace.download(f"{freezer.LEASE_ROOT}/{marker.name}").read())
    assert document["attestation_verify_key"] == derive_gateway_proof_verify_key(values[tool.SIGNING_KEY])
    assert values[tool.SIGNING_KEY] not in "".join(capsys.readouterr())


@pytest.mark.parametrize("phase", ("freeze", "thaw"))
def test_the_signing_key_reaches_no_output_no_environ_and_no_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], phase: str
) -> None:
    seen: list[dict[str, str]] = []

    def no_child(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("the tool starts no process")

    def watched(workspace: Any) -> Any:
        """Record os.environ at every workspace call the tool makes from here on."""

        for op in ("mkdirs", "get_status", "list", "download", "upload"):
            workspace.workspace.hooks[op] = lambda _path: seen.append(dict(os.environ))
        return workspace

    if phase == "freeze":
        workspace, env_file, ack = watched(_workspace()), _env_file(tmp_path), None
    else:
        workspace, env_file, _marker, ack = _frozen(tmp_path, capsys)
        watched(workspace)
    monkeypatch.setattr(subprocess, "Popen", no_child)
    monkeypatch.setattr(os, "system", no_child)
    assert _run(phase, env_file, workspace, ack=ack, monkeypatch=monkeypatch) == 0
    out, err = capsys.readouterr()

    assert len(seen) >= 3, "the workspace was reached"
    for environ in (*seen, dict(os.environ)):
        assert tool.SIGNING_KEY not in environ, "the private key is never placed in os.environ"
        assert PLANTED not in environ.values()
        assert environ[tool.VERIFY_KEY] == PLANTED_VERIFY, "only the public key, for the lease's key registry"
    assert PLANTED not in out + err
    assert not [i for i in range(len(PLANTED) - 7) if PLANTED[i : i + 8] in out + err], "nor any 8-character run of it"
    stored = b"".join(node.data for node in workspace.workspace.nodes.values())
    assert PLANTED.encode() not in stored, "a marker holds the public key and a signature"


@pytest.mark.parametrize(
    "signing",
    (None, "", "not base64 !!", base64.urlsafe_b64encode(bytes(31)).decode().rstrip("="), PLANTED + "AAAA"),
    ids=("absent", "empty", "garbage", "short", "long"),
)
@pytest.mark.parametrize("phase", ("freeze", "thaw"))
def test_a_missing_or_invalid_key_refuses_with_nothing_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], phase: str, signing: str | None
) -> None:
    env_file = _env_file(tmp_path, **{tool.SIGNING_KEY: signing})

    assert tool.main(["--phase", phase, "--env-file", str(env_file)], workspace_factory=_no_workspace) == 2
    out, err = capsys.readouterr()
    assert out == "" and err.startswith("[mip-publisher-freeze] REFUSED freeze_unsigned: ")
    assert "nothing was written" in err
    if signing:
        assert signing not in err, "a malformed key is still never printed"
        assert not [i for i in range(len(signing) - 7) if signing[i : i + 8] in err], "nor any 8-character run of it"
    assert tool.VERIFY_KEY not in os.environ


def test_an_ambient_signing_key_is_never_the_one_it_signs_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Only the env file's key signs: an exported one is neither read nor a fallback."""

    monkeypatch.setenv(tool.SIGNING_KEY, OTHER_KEY)
    monkeypatch.setenv(tool.VERIFY_KEY, derive_gateway_proof_verify_key(OTHER_KEY))
    absent = _env_file(tmp_path, **{tool.SIGNING_KEY: None})
    assert tool.main(["--phase", "freeze", "--env-file", str(absent)], workspace_factory=_no_workspace) == 2
    assert "REFUSED freeze_unsigned" in capsys.readouterr().err
    workspace = _workspace()
    assert _run("freeze", _env_file(tmp_path), workspace) == 0
    (marker,) = freezer.judge_markers(workspace, now=NOW)
    assert _document(workspace, marker.marker_id)["attestation_verify_key"] == PLANTED_VERIFY


@pytest.mark.parametrize(
    ("overrides", "code"),
    (
        ({"MIP_APP_NAME": None}, "freeze_app_invalid"),
        ({"MIP_APP_NAME": ""}, "freeze_app_invalid"),
        ({"MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY": "not-a-key"}, "freeze_unsigned"),
        ({"MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS": "short,keys"}, "freeze_unsigned"),
    ),
)
def test_an_env_file_without_an_app_or_with_a_bad_registry_refuses_before_the_workspace(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], overrides: dict[str, str | None], code: str
) -> None:
    env_file = _env_file(tmp_path, **overrides)

    assert tool.main(["--phase", "freeze", "--env-file", str(env_file)], workspace_factory=_no_workspace) == 2
    assert capsys.readouterr().err.startswith(f"[mip-publisher-freeze] REFUSED {code}: ")
    missing = tmp_path / "no-such.env"
    assert tool.main(["--phase", "freeze", "--env-file", str(missing)], workspace_factory=_no_workspace) == 2
    assert "REFUSED freeze_app_invalid" in capsys.readouterr().err


def test_an_invalid_app_name_refuses_before_the_root_is_touched(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    workspace = _workspace()
    env_file = _env_file(tmp_path, MIP_APP_NAME="Not A Valid App")

    for phase in ("freeze", "thaw"):
        assert _run(phase, env_file, workspace) == 2
        assert "REFUSED freeze_app_invalid" in capsys.readouterr().err
    assert workspace.workspace.ops("mkdirs") == [] and workspace.workspace.ops("upload") == []


def test_the_key_registry_is_the_env_files_and_an_ambient_older_key_never_widens_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    other_verify = derive_gateway_proof_verify_key(OTHER_KEY)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY", other_verify)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS", other_verify)
    workspace = _workspace()
    assert _run("freeze", _env_file(tmp_path), workspace) == 0
    assert "MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY" not in os.environ
    assert "MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS" not in os.environ
    assert os.environ[tool.VERIFY_KEY] == PLANTED_VERIFY
    capsys.readouterr()
    # The env file's own previous key is a registry member: a marker it signed still verifies.
    rotated = _workspace()
    assert _run("freeze", _env_file(tmp_path, **{tool.SIGNING_KEY: OTHER_KEY}), rotated) == 0
    current = _env_file(tmp_path, MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY=other_verify)
    assert _run("freeze", current, rotated) == 2
    assert "REFUSED already_frozen" in capsys.readouterr().err, "the older key's marker verifies and reads frozen"
    assert os.environ["MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY"] == other_verify


def test_a_second_freeze_refuses_already_frozen_and_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    workspace, env_file, marker_id, _ack = _frozen(tmp_path, capsys)

    assert _run("freeze", env_file, workspace, now=NOW + 60) == 2
    out, err = capsys.readouterr()
    assert out == "" and err.startswith(f"[mip-publisher-freeze] REFUSED already_frozen: the App is already frozen by marker {marker_id}")
    assert workspace.workspace.ops("upload") == [freezer.freeze_path(APP, marker_id)]
    # Once it has expired, a new freeze is a new marker (the runbook's "freeze again").
    assert _run("freeze", env_file, workspace, now=NOW + THIRTY_DAYS) == 0
    assert len(workspace.workspace.ops("upload")) == 2


_ADMINS = _entry("CAN_MANAGE", group="admins")


def _sp_entry(level: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(
        user_name=None, service_principal_name=name, group_name=None,
        all_permissions=[SimpleNamespace(permission_level=level)],
    )


@pytest.mark.parametrize(
    ("acl", "allowed"),
    (
        ([], True),
        ([_ADMINS, _sp_entry("CAN_MANAGE", DEPLOYER)], True),
        ([_ADMINS, _entry("CAN_READ", group="users")], False),
        ([_ADMINS, _entry("CAN_MANAGE", user="peer@lender.example")], False),
        ([_sp_entry("CAN_EDIT", DEPLOYER)], False),
    ),
)
def test_a_root_the_ci_freeze_creates_is_judged_before_its_first_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], acl: list[Any], allowed: bool
) -> None:
    """The inherited ACL of a new lease root is not yet probed live: anything but admins and the deployer refuses."""

    monkeypatch.setattr(lease, "_download", lambda *_a, **_kw: None)
    workspace = _workspace()
    workspace.workspace.acl = list(acl)  # what the root reads once mkdirs creates it

    code = _run("freeze", _env_file(tmp_path), workspace)
    tree = workspace.workspace
    assert tree.ops("mkdirs") == [freezer.LEASE_ROOT] and tree.set_permissions_calls == 0 and tree.acl == acl
    if allowed:
        assert code == 0 and [m.frozen for m in freezer.judge_markers(workspace, now=NOW)] == [True]
        return
    err = capsys.readouterr().err
    assert code == 2 and err.startswith("[mip-publisher-freeze] REFUSED freeze_root_unprotected: ")
    assert "created is left bare and empty, no marker was written" in err
    assert tree.ops("upload") == [] and freezer.judge_markers(workspace, now=NOW) == []


def test_an_existing_root_is_checked_read_only_against_the_apps_own_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    record = {"holder": DEPLOYER, "writer_application_id": "writer-id"}
    monkeypatch.setattr(lease, "_download", lambda *_a, **_kw: record)
    governed = _workspace()
    governed.workspace.mkdirs(freezer.LEASE_ROOT)
    lease._ensure_protected_root(governed, holder=DEPLOYER, writer_application_id="writer-id")
    acl = list(governed.workspace.acl)

    assert _run("freeze", _env_file(tmp_path), governed) == 0
    assert governed.workspace.set_permissions_calls == 1 and governed.workspace.acl == acl, "only the lease's own"
    capsys.readouterr()

    drifted = _workspace()
    drifted.workspace.mkdirs(freezer.LEASE_ROOT)
    lease._ensure_protected_root(drifted, holder="owner@lender.example", writer_application_id="writer-id")
    assert _run("freeze", tmp_path / ".env.local", drifted) == 2
    err = capsys.readouterr().err
    assert "REFUSED freeze_root_unprotected" in err and "nothing was written and no permission was changed" in err
    assert drifted.workspace.ops("upload") == []


# ------------------------------------------------------------------ thaw


def test_a_thaw_without_an_ack_prints_each_frozen_markers_digest_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace, env_file, marker_id, ack = _frozen(tmp_path, capsys)
    before = list(workspace.workspace.ops("upload"))

    assert _run("thaw", env_file, workspace) == 2
    out, err = capsys.readouterr()
    assert out == "" and err.startswith("[mip-publisher-freeze] REFUSED thaw_unacked: ")
    assert f'marker {marker_id}: operation_acks="freeze_thaw={ack}"' in err, "the exact pair to dispatch"
    assert "nothing was written" in err
    assert workspace.workspace.ops("upload") == before
    assert [m.reason for m in freezer.judge_markers(workspace, now=NOW)] == [f"frozen until {NOW + THIRTY_DAYS}"]


def _other_plans(marker_id: str, document: dict[str, Any]) -> dict[str, str]:
    """Digests that are not this marker's thaw ack, by what each gets wrong."""

    plan = tool.thaw_plan(APP, marker_id, document)
    return {
        "malformed": "A" * 64,
        "short": "0" * 63,
        "another-digest": "0" * 64,
        # The same plan under another purpose: domain separation.
        "another-purpose": ack_digest("override_freeze", plan),
        "another-app": ack_digest("freeze_thaw", {**plan, "app_name": "other-app"}),
        "another-marker": ack_digest("freeze_thaw", {**plan, "marker_id": "00000000-0000-4000-8000-000000000000"}),
        "another-freeze": ack_digest("freeze_thaw", {**plan, "freeze_sha256": "f" * 64}),
        # What an override of this marker would be: never a thaw.
        "the-override": ack_digest("override_freeze", freezer.override_plan(marker_id=marker_id, release_path=RELEASE)),
    }


@pytest.mark.parametrize(
    "wrong",
    ("malformed", "short", "another-digest", "another-purpose", "another-app", "another-marker", "another-freeze", "the-override"),
)
def test_a_thaw_refuses_every_ack_but_the_markers_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], wrong: str
) -> None:
    workspace, env_file, marker_id, ack = _frozen(tmp_path, capsys)
    given = _other_plans(marker_id, _document(workspace, marker_id))[wrong]
    assert given != ack
    before = list(workspace.workspace.ops("upload"))

    assert _run("thaw", env_file, workspace, ack=given, monkeypatch=monkeypatch) == 2
    err = capsys.readouterr().err
    assert "REFUSED thaw_unacked" in err and f"freeze_thaw={ack}" in err
    assert workspace.workspace.ops("upload") == before and freezer.judge_markers(workspace, now=NOW)[0].frozen


def test_a_thaw_with_the_exact_ack_writes_the_thaw_and_reads_thawed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace, env_file, marker_id, ack = _frozen(tmp_path, capsys)

    assert _run("thaw", env_file, workspace, now=NOW + 3600, ack=ack, monkeypatch=monkeypatch) == 0
    out, err = capsys.readouterr()
    assert err == "" and out.splitlines() == [f"[mip-publisher-freeze] thawed: marker {marker_id} (nothing was deleted)"]
    tree = workspace.workspace
    assert tree.ops("upload") == [freezer.freeze_path(APP, marker_id), freezer.thaw_path(APP, marker_id)]
    assert tree.ops("delete") == [] and tree.set_permissions_calls == 0
    (marker,) = freezer.judge_markers(workspace, now=NOW + 3600)
    assert (marker.frozen, marker.reason) == (False, "thawed")
    thaw = json.loads(tree.download(freezer.thaw_path(APP, marker_id)).read())
    assert thaw["freeze_sha256"] == freezer.freeze_document_sha256(_document(workspace, marker_id))
    assert thaw["attestation_verify_key"] == PLANTED_VERIFY
    # The same ack again: nothing is frozen any more, and nothing is written.
    assert _run("thaw", env_file, workspace, now=NOW + 3600, ack=ack, monkeypatch=monkeypatch) == 2
    assert "REFUSED nothing_frozen" in capsys.readouterr().err
    assert len(tree.ops("upload")) == 2


def test_nothing_frozen_refuses_for_no_root_another_apps_marker_and_an_expired_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env_file = _env_file(tmp_path)
    assert _run("thaw", env_file, _workspace()) == 2
    assert "REFUSED nothing_frozen" in capsys.readouterr().err
    # Another App's frozen marker is never this App's to thaw, with or without that marker's ack.
    workspace = _workspace()
    assert _run("freeze", _env_file(tmp_path, MIP_APP_NAME="other-app"), workspace) == 0
    other_ack = THAW_LINE.search(capsys.readouterr().out)
    assert other_ack
    env_file = _env_file(tmp_path)
    for ack in ("", other_ack.group(2)):
        monkeypatch.setenv(tool.ACK_ENV, ack)
        assert _run("thaw", env_file, workspace) == 2
        assert "REFUSED nothing_frozen" in capsys.readouterr().err
    assert len(workspace.workspace.ops("upload")) == 1 and freezer.judge_markers(workspace, now=NOW)[0].frozen
    # An expired marker needs no thaw.
    monkeypatch.delenv(tool.ACK_ENV)
    expired, env_file, _marker, ack = _frozen(tmp_path, capsys)
    assert _run("thaw", env_file, expired, now=NOW + THIRTY_DAYS, ack=ack, monkeypatch=monkeypatch) == 2
    assert "REFUSED nothing_frozen" in capsys.readouterr().err


@pytest.mark.parametrize("tamper", ("until", "unsigned", "garbage", "another-key"))
def test_an_unverifiable_marker_is_never_thawed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tamper: str
) -> None:
    workspace, env_file, marker_id, ack = _frozen(tmp_path, capsys)
    path = freezer.freeze_path(APP, marker_id)
    document = _document(workspace, marker_id)
    if tamper == "until":
        document["until"] = NOW + 1
    elif tamper == "unsigned":
        document.pop("attestation_signature")
    elif tamper == "another-key":
        document = freezer._sign(
            {k: v for k, v in document.items() if not k.startswith("attestation_")},
            environ={tool.SIGNING_KEY: OTHER_KEY, tool.VERIFY_KEY: derive_gateway_proof_verify_key(OTHER_KEY)},
        )
    workspace.workspace.upload(path, b"{" if tamper == "garbage" else json.dumps(document).encode(), overwrite=True)
    before = list(workspace.workspace.ops("upload"))

    for given in (ack, ""):
        monkeypatch.setenv(tool.ACK_ENV, given)
        assert _run("thaw", env_file, workspace) == 2
        err = capsys.readouterr().err
        assert err.startswith("[mip-publisher-freeze] REFUSED thaw_unverified: 1 freeze marker(s) of this App read frozen")
        assert "nothing was written" in err
    assert workspace.workspace.ops("upload") == before
    assert freezer.judge_markers(workspace, now=NOW)[0].frozen, "it stays frozen: fail closed"
    assert freezer.frozen_documents(workspace, app_name=APP, now=NOW) == []


def test_two_frozen_markers_are_listed_and_each_needs_its_own_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = _workspace()
    signing = {tool.SIGNING_KEY: PLANTED, tool.VERIFY_KEY: PLANTED_VERIFY}
    monkeypatch.setenv(tool.VERIFY_KEY, PLANTED_VERIFY)
    first = freezer.freeze(
        workspace, app_name=APP, until=NOW + 600, holder="owner@lender.example", now=float(NOW),
        environ=signing, ensure_root=lambda: workspace.workspace.mkdirs(freezer.LEASE_ROOT),
    )
    monkeypatch.delenv(tool.VERIFY_KEY)
    env_file = _env_file(tmp_path)
    assert _run("freeze", env_file, workspace, now=NOW + 900) == 0, "the first had expired"
    second = FROZEN_LINE.search(capsys.readouterr().out)
    assert second and second.group(1) != first
    acks = {marker: _expected_ack(workspace, marker) for marker in (first, second.group(1))}
    assert len(set(acks.values())) == 2

    at = NOW + 300  # both read frozen at this instant
    assert _run("thaw", env_file, workspace, now=at) == 2
    err = capsys.readouterr().err
    for marker, ack in acks.items():
        assert f'marker {marker}: operation_acks="freeze_thaw={ack}"' in err
    assert _run("thaw", env_file, workspace, now=at, ack=acks[first], monkeypatch=monkeypatch) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"[mip-publisher-freeze] thawed: marker {first} (nothing was deleted)"
    assert out[1].startswith("[mip-publisher-freeze] 1 other freeze marker(s) of this App still read frozen")
    reasons = {m.marker_id: m.reason for m in freezer.judge_markers(workspace, now=at)}
    assert reasons[first] == "thawed" and reasons[second.group(1)].startswith("frozen until ")
    # The thawed marker's ack thaws nothing else.
    assert _run("thaw", env_file, workspace, now=at, ack=acks[first], monkeypatch=monkeypatch) == 2
    assert "REFUSED thaw_unacked" in capsys.readouterr().err


def test_a_thaw_that_does_not_read_back_as_thawed_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace, env_file, marker_id, ack = _frozen(tmp_path, capsys)
    monkeypatch.setattr(freezer, "thaw", lambda *_a, **_kw: None)  # a thaw that wrote nothing

    assert _run("thaw", env_file, workspace, ack=ack, monkeypatch=monkeypatch) == 2
    err = capsys.readouterr().err
    assert err.startswith(f"[mip-publisher-freeze] REFUSED thaw_unverified: the thaw of marker {marker_id} was written")


def test_a_freeze_that_does_not_read_back_as_frozen_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = _workspace()
    monkeypatch.setattr(freezer, "frozen_documents", lambda *_a, **_kw: [])

    assert _run("freeze", _env_file(tmp_path), workspace) == 2
    out, err = capsys.readouterr()
    assert out == "" and "REFUSED freeze_unverified: marker " in err, "no thaw ack is printed for a marker that does not verify"


def test_an_unexpected_error_prints_its_class_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    host = "https://dbc-unit.cloud.databricks.com"

    def failing() -> Any:
        raise ConnectionError(f"cannot reach {host} as {DEPLOYER}")

    assert tool.main(["--phase", "freeze", "--env-file", str(_env_file(tmp_path))], workspace_factory=failing) == 2
    out, err = capsys.readouterr()
    assert out == "" and err.startswith("[mip-publisher-freeze] REFUSED failed: ConnectionError. ")
    assert host not in err and DEPLOYER not in err and PLANTED not in err


# ------------------------------------------------------------------ the two helpers


def test_frozen_documents_returns_only_this_apps_verified_frozen_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace, _env_file_path, marker_id, _ack = _frozen(tmp_path, capsys)

    ((found_id, document),) = freezer.frozen_documents(workspace, app_name=APP, now=NOW)
    assert found_id == marker_id and document == _document(workspace, marker_id)
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    assert freezer.freeze_document_sha256(document) == hashlib.sha256(canonical).hexdigest()
    assert freezer.frozen_documents(workspace, app_name="other-app", now=NOW) == []
    assert freezer.frozen_documents(workspace, app_name=APP, now=NOW + THIRTY_DAYS) == [], "expired"
    with pytest.raises(freezer.FreezeRefused) as invalid:
        freezer.frozen_documents(workspace, app_name="Not Valid", now=NOW)
    assert invalid.value.code == "freeze_app_invalid"
    monkeypatch.delenv(tool.VERIFY_KEY)
    with pytest.raises(freezer.FreezeRefused) as unsigned:
        freezer.frozen_documents(workspace, app_name=APP, now=NOW)
    assert unsigned.value.code == "freeze_unsigned", "no registry: nothing is reported thawable"


# ------------------------------------------------------------------ what honours the marker


def _publisher(workspace: Any) -> Any:
    """The same workspace as the emergency recipe's publisher sees it."""

    return SimpleNamespace(workspace=workspace.workspace)


def test_the_emergency_publisher_obeys_the_ci_marker_until_its_thaw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(freezer, "time", SimpleNamespace(time=lambda: float(NOW)))
    workspace, env_file, marker_id, ack = _frozen(tmp_path, capsys)
    stage, manifest, release_id = _stage(tmp_path)
    argv = _publish_argv(stage, manifest, release_id)
    uploads = list(workspace.workspace.ops("upload"))

    # An operator's shell holds no key at all: the tool's own registry write is undone here.
    monkeypatch.delenv(tool.VERIFY_KEY)
    assert source.main(argv, client=lambda: _publisher(workspace)) == 2
    assert "refused: frozen: the emergency publisher is frozen by marker" in capsys.readouterr().err
    # With the environment's public verify key exported, the refusal names the marker's own state.
    monkeypatch.setenv(tool.VERIFY_KEY, PLANTED_VERIFY)
    assert source.main(argv, client=lambda: _publisher(workspace)) == 2
    err = capsys.readouterr().err
    assert f"frozen by marker {marker_id} (frozen until {NOW + THIRTY_DAYS})" in err
    override = re.search(r"--override-freeze ([0-9a-f-]{36}) --override-freeze-ack ([0-9a-f]{64})", err)
    assert override and override.group(1) == marker_id, "the exact override the runbook names"
    assert workspace.workspace.ops("upload") == uploads, "refused before any upload"
    # The thaw ack is no override, and the override is no thaw.
    assert source.main([*argv, "--override-freeze", marker_id, "--override-freeze-ack", ack], client=lambda: _publisher(workspace)) == 2
    assert "ack_mismatch" in capsys.readouterr().err

    assert _run("thaw", env_file, workspace, ack=ack, monkeypatch=monkeypatch) == 0
    capsys.readouterr()
    # Thawed, but a shell without the public key still reads the marker as frozen (fail closed).
    monkeypatch.delenv(tool.VERIFY_KEY)
    assert source.main(argv, client=lambda: _publisher(workspace)) == 2
    assert "unverifiable" in capsys.readouterr().err
    monkeypatch.setenv(tool.VERIFY_KEY, PLANTED_VERIFY)
    assert source.main(argv, client=lambda: _publisher(workspace)) == 0
    assert f"freeze marker {APP}.freeze-{marker_id}.json: thawed" in capsys.readouterr().out


def test_the_exact_override_publishes_one_folder_while_the_ci_marker_is_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Cutover step 10A's remedy under the freeze: the override the refusal prints, with the verify key exported."""

    monkeypatch.setattr(freezer, "time", SimpleNamespace(time=lambda: float(NOW)))
    workspace, _env, marker_id, _ack = _frozen(tmp_path, capsys)
    stage, manifest, release_id = _stage(tmp_path)
    argv = _publish_argv(stage, manifest, release_id)
    assert source.main(argv, client=lambda: _publisher(workspace)) == 2
    override = re.search(r"--override-freeze-ack ([0-9a-f]{64})", capsys.readouterr().err)
    assert override

    acked = [*argv, "--override-freeze", marker_id, "--override-freeze-ack", override.group(1)]
    assert source.main(acked, client=lambda: _publisher(workspace)) == 0
    assert "overridden by its exact override_freeze ack" in capsys.readouterr().out
    assert freezer.judge_markers(workspace, now=NOW)[0].frozen, "the override lifts one publish, never the marker"


def test_the_governed_runs_held_lease_is_exempt_for_its_own_app_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace, _env, marker_id, _ack = _frozen(tmp_path, capsys)

    notes = freezer.assert_publish_allowed(
        workspace, release_path=RELEASE, now=NOW, held_lease=lambda: None, held_lease_app_name=APP
    )
    assert notes[-1].startswith(f"the publish runs under {APP}'s held signed lease")
    with pytest.raises(freezer.FreezeRefused, match=f"frozen by marker {marker_id} "):
        freezer.assert_publish_allowed(
            workspace, release_path=RELEASE, now=NOW, held_lease=lambda: None, held_lease_app_name="decoy-app"
        )


# ------------------------------------------------------------------ what the tool may reach


def test_the_tool_calls_no_app_no_permission_and_no_delete_api() -> None:
    tree = ast.parse((REPO / "tools" / "clients" / "publisher_freeze.py").read_text(encoding="utf-8"))
    called = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}

    assert not called & {"start", "stop", "deploy", "update", "create_update", "delete", "set_permissions", "run", "Popen", "system"}
    assert "apps" not in attributes and "subprocess" not in {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert called >= {"freeze", "thaw", "frozen_documents", "judge_markers", "prepare_root"}
    # Positive control: the lease's own root converger would be found.
    control = ast.parse("workspace.workspace.set_permissions('directories', object_id)")
    assert {n.func.attr for n in ast.walk(control) if isinstance(n, ast.Call)} == {"set_permissions"}
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not [module for module in imports if module and ("rollback" in module or "quiesce" in module)]


def test_scripts_deploy_sh_never_runs_it_and_the_ack_is_refused_everywhere_but_thaw() -> None:
    assert "publisher_freeze" not in deploy_contract_text(), "deploy.sh is unchanged: the workflow's own step runs it"
    assert "MIP_ACK_FREEZE_THAW" not in deploy_contract_text()
    digest = "a" * 64
    for phase in sorted(dispatch_acks.PHASES - {"thaw"}):
        with pytest.raises(ProfileRefusal) as refused:
            dispatch_acks.operation_exports(f"freeze_thaw={digest}", phase)
        assert list(refused.value.lines) == [f"operation_acks: freeze_thaw is never read by --phase {phase}"]
    assert tool.ACK_ENV == dispatch_acks.OPERATION_ACKS["freeze_thaw"].env == "MIP_ACK_FREEZE_THAW"
    assert tool.PURPOSE == "freeze_thaw"


# ------------------------------------------------------------------ the docs


def _flat(relative: str) -> str:
    return " ".join((REPO / relative).read_text(encoding="utf-8").split())


RUNBOOK = "docs/clients/first-client-cutover.md"
FREEZE_DISPATCH = (
    "gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=freeze "
    "-f owner_attestation=<profile-ack> -f acknowledge_single_maintainer_break_glass=true"
)
THAW_DISPATCH = (
    "gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=thaw -f owner_attestation=<profile-ack> "
    '-f operation_acks="freeze_thaw=<sha256>" -f acknowledge_single_maintainer_break_glass=true'
)
OVERRIDE = "--override-freeze <marker id> --override-freeze-ack <sha256>"


def test_the_runbook_keeps_step_1s_message_freezes_by_marker_at_6b_and_thaws_last_in_13() -> None:
    runbook = _flat(RUNBOOK)
    step_1 = runbook[runbook.index("## Step 1. Freeze and snapshot") : runbook.index("## Step 2.")]
    step_6b = runbook[runbook.index("## Step 6b. Freeze the emergency recipe") : runbook.index("## Step 7.")]
    step_13 = runbook[runbook.index("## Step 13.") : runbook.index("## Step 14.")]

    # Step 1 keeps the message freeze: the environment and its key do not exist until step 4.
    assert "message every peer session that the emergency recipe is frozen for client #1 until step 13" in step_1
    assert "So CI signs the marker, at step 6b" in step_1 and "phase=freeze" not in step_1
    assert runbook.index("## Step 6. Plan #1") < runbook.index("## Step 6b.") < runbook.index("## Step 7.")
    assert "| 6b | Freeze the emergency recipe (the CI-signed marker) | CI |" in runbook
    assert FREEZE_DISPATCH in step_6b and runbook.count(FREEZE_DISPATCH) == 1
    assert "Plan #1's dispatch preflight prints the profile ack before its live checks refuse" in step_6b
    # The log lines are the tool's own, and the pair is the one the thaw carries.
    assert "`[mip-publisher-freeze] frozen: marker <id> until <epoch>`" in step_6b
    assert '`[mip-publisher-freeze] thaw with -f phase=thaw -f operation_acks="freeze_thaw=<sha256>"`' in step_6b
    source_text = (REPO / "tools" / "clients" / "publisher_freeze.py").read_text(encoding="utf-8")
    assert 'f"frozen: marker {marker_id} until {until}"' in source_text
    assert "thaw with -f phase=thaw -f operation_acks=" in source_text
    # The thaw is step 13's last action, after the profile's last change.
    assert THAW_DISPATCH in step_13 and runbook.count(THAW_DISPATCH) == 1
    assert step_13.index("remove the legacy owner's interim membership") < step_13.index(THAW_DISPATCH) < step_13.index("**Proof:**")
    assert "`<profile-ack>` is the one item 5's plan printed (item 4 changed the profile)" in step_13
    assert "`[mip-publisher-freeze] thawed: marker <id> (nothing was deleted)`" in step_13
    assert "**If step 13 is not reached within 30 days,** the marker expires by itself: dispatch this step again." in step_6b
    # Every refusal the two steps name is a code the tool or the marker module raises.
    raised = source_text + (REPO / "tools" / "databricks" / "app_deployment_freeze.py").read_text(encoding="utf-8")
    for code in ("freeze_root_unprotected", "already_frozen", "freeze_unsigned", "thaw_unacked", "nothing_frozen"):
        assert f"`{code}`" in step_6b + step_13 and f'"{code}"' in raised, code
    # What the marker does not stop is said where the owner dispatches it.
    for phrase in (
        "It does not stop the raw CLI either",
        "step 12's publish runs under its held lease",
        "for every App in this workspace",
        "because markers are never deleted",
    ):
        assert phrase in step_6b, phrase


def test_every_emergency_remedy_between_6b_and_13_names_the_exact_override_or_a_thaw() -> None:
    """A CI freeze makes each emergency publish refuse ``frozen``: the runbook says how the owner still ships one."""

    raw = (REPO / RUNBOOK).read_text(encoding="utf-8")
    runbook = _flat(RUNBOOK)
    step_6b = runbook[runbook.index("## Step 6b. Freeze the emergency recipe") : runbook.index("## Step 7.")]
    assert "**An emergency publish between here and step 13.**" in step_6b
    assert f"the exact `{OVERRIDE}` the refusal prints" in step_6b and "or a thaw (step 13's dispatch)" in step_6b
    assert "exports the environment's public variable `MIP_AI_GATEWAY_PROOF_VERIFY_KEY`" in step_6b
    assert "reads each one as frozen, a thawed or expired one included" in step_6b
    decisions = runbook[runbook.index("## Decisions taken") : runbook.index("## Steps at a glance")]
    assert "**From step 6b to step 13 the emergency recipe is frozen by a signed marker**" in decisions
    assert f"`{OVERRIDE}`" in decisions and "or a thaw" in decisions
    ten_a = runbook[runbook.index("### 10A. Start the stopped App (owner)") : runbook.index("### 10B.")]
    stop = ten_a[ten_a.index("**Stop point:**") :]
    assert "a fresh emergency deploy of a current `main` commit is the remedy for `closure_differs`" in stop
    assert f"the exact `{OVERRIDE}` the refusal prints" in stop and "refuses `frozen`" in stop
    assert "`MIP_AI_GATEWAY_PROOF_VERIFY_KEY` exported in that shell, or a thaw followed by a new freeze" in stop
    # Each paragraph from 6b to the end of 13 that names an emergency ship names the route too.
    frozen_span = raw[raw.index("## Step 6b. Freeze the emergency recipe") : raw.index("## Step 14.")]
    named = [" ".join(p.split()) for p in frozen_span.split("\n\n") if re.search(r"emergency\s+(?:ship|redeploy|deploy)", p)]
    assert len(named) >= 3, "non-vacuous: 6b's own rule, 10A's source check and 10A's stop point"
    for paragraph in named:
        assert "override-freeze" in paragraph or "now refuses `frozen`" in paragraph, paragraph[:120]
    # Positive control: the pre-change stop point would be found.
    before = "fix the folder first (a fresh emergency deploy of a current `main` commit is the remedy for `closure_differs`)."
    assert re.search(r"emergency\s+(?:ship|redeploy|deploy)", before) and "override-freeze" not in before


def test_deployment_md_documents_the_ci_freeze_and_keeps_the_verify_key_export_in_prose() -> None:
    from tests.unit.test_docs_commands_parse import ALLOWED_ENV, BREAK_GLASS_HEADING, CLIENT_DOCS

    raw = (REPO / "docs" / "deployment.md").read_text(encoding="utf-8")
    recipe = raw[raw.index("### The emergency recipe, command by command") : raw.index(BREAK_GLASS_HEADING)]
    flat = " ".join(recipe.split())
    for phrase in (
        "**The freeze during the first-client cutover: CI signs it (wave 7c).**",
        "`deploy-dev.yml` has two phases that run no `scripts/deploy.sh`: `freeze` and `thaw`",
        "The step binds no secret and no variable",
        "refuses outside GitHub Actions (`ci_only`",
        "`phase=freeze` writes one marker for the profile's App, valid for the marker's maximum of 30 days",
        "`freeze_thaw=<sha256>`",
        "`already_frozen`",
        "`thaw_unacked`",
        "`nothing_frozen`",
        "`thaw_unverified`",
        "`freeze_root_unprotected`",
        "The raw CLI reads no marker at all",
        f"the exact `{OVERRIDE}` its refusal prints",
        "or a thaw",
        "public variable `MIP_AI_GATEWAY_PROOF_VERIFY_KEY`",
        "`scripts/deploy.sh` has no such phase and refuses the name",
    ):
        assert phrase in flat, phrase
    for stale in ("a CI-run freeze and thaw is a follow-up", "no workflow step runs `freeze`", "not a marker;"):
        assert stale not in " ".join(raw.split()), stale
    # The heading the break-glass doc test anchors on is kept verbatim, and no bash block of the
    # runbooks or this page exports the verify key: the command parser admits one env name only.
    assert raw.count(BREAK_GLASS_HEADING + "\n") == 1
    assert set(ALLOWED_ENV) == {"DATABRICKS_CONFIG_PROFILE"}
    for doc in ("docs/deployment.md", *CLIENT_DOCS):
        for block in re.findall(r"```bash\n(.*?)\n```", (REPO / doc).read_text(encoding="utf-8"), flags=re.DOTALL):
            assert "MIP_AI_GATEWAY_PROOF_VERIFY_KEY" not in block and "MIP_ACK_FREEZE_THAW" not in block, doc
    assert "tools.clients.publisher_freeze" not in "".join((REPO / doc).read_text(encoding="utf-8") for doc in CLIENT_DOCS), (
        "CI only: no runbook shows it as a command an operator runs"
    )


def test_the_phase_lists_and_the_ack_lists_name_the_marker_phases() -> None:
    readme = _flat(".github/workflows/README.md")
    assert "or `freeze` or `thaw`, which run no `scripts/deploy.sh`" in readme
    assert "no secret is bound to the step" in readme
    front = _flat("README.md")
    assert "Two more phases, `freeze` and `thaw`, run no `scripts/deploy.sh`" in front
    assert "(the freeze marker and its thaw, above, are the workflow's only writes outside it)" in front
    onboarding = _flat("docs/clients/onboarding.md")
    assert "| Freezing the emergency recipe: `phase=freeze`, `phase=thaw` | Built, CI only (wave 7c); not run live yet." in onboarding
    assert "A client onboarded from nothing never does, and for it nothing changes." in onboarding
    review = _flat("docs/clients/review-modes.md")
    assert "`freeze_thaw`, which a CI freeze prints and only a thaw reads" in review
    assert "on every mutating phase and on the two marker phases, `freeze` and `thaw`" in review
    deployment = _flat("docs/deployment.md")
    assert "`freeze_thaw` (thaw only: the exact thaw of one signed freeze marker, which the freeze printed" in deployment
    assert "adopt and unadopt 60 minutes, freeze and thaw 15" in deployment
    assert "for every mutating phase and for both marker phases (`freeze`, `thaw`)" in deployment
    workflow = (REPO / ".github" / "workflows" / "deploy-dev.yml").read_text(encoding="utf-8")
    assert "options: [plan, release, data, adopt, unadopt, freeze, thaw]" in workflow
