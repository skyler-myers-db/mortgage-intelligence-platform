"""The signed freeze marker and the emergency publisher that obeys it (M2-PR7, attack O8)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.fixtures import rollback_records as rr
from tests.fixtures.fake_workspace import FakeTree, FakeWorkspaceClient
from tools.databricks import app_deployment_freeze as freezer
from tools.databricks import app_release_contract as contract
from tools.databricks import app_release_source as source
from tools.databricks.operation_ack import ack_digest

NOW = 1_800_000_000.0
RELEASE = "/Workspace/Users/owner@lender.example/.bundle/mortgage-intelligence-platform/dev/app-src/0123456789ab-fedcba987654"
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    rr.signing_env(monkeypatch)


def _env() -> dict[str, str]:
    return {
        "MIP_AI_GATEWAY_PROOF_SIGNING_KEY": rr.SIGNING_KEY,
        "MIP_AI_GATEWAY_PROOF_VERIFY_KEY": rr.VERIFY_KEY,
    }


def _freeze(
    client: FakeWorkspaceClient, *, until: float = NOW + 3600, app: str = rr.APP_NAME
) -> str:
    return freezer.freeze(
        client,
        app_name=app,
        until=int(until),
        holder="deployer",
        now=NOW,
        environ=_env(),
        ensure_root=lambda: client.workspace.mkdirs(freezer.LEASE_ROOT),
    )


def _allowed(client: FakeWorkspaceClient, **kwargs: Any) -> list[str]:
    return freezer.assert_publish_allowed(client, release_path=RELEASE, now=NOW, **kwargs)


def _code(client: FakeWorkspaceClient, **kwargs: Any) -> str:
    with pytest.raises(freezer.FreezeRefused) as refused:
        _allowed(client, **kwargs)
    return refused.value.code


def test_no_lease_root_and_no_marker_change_nothing() -> None:
    client = FakeWorkspaceClient()
    assert _allowed(client) == []
    client.workspace.mkdirs(freezer.LEASE_ROOT)
    client.workspace.add_file(f"{freezer.LEASE_ROOT}/mip-app.json", b"{}")  # the lease itself
    assert _allowed(client) == []


def test_freeze_blocks_publish_until_a_signed_thaw() -> None:
    client = FakeWorkspaceClient()
    marker = _freeze(client)

    assert _code(client) == "frozen"
    freezer.thaw(client, app_name=rr.APP_NAME, marker_id=marker, holder="deployer", environ=_env())
    assert _allowed(client) == [f"freeze marker mip-app.freeze-{marker}.json: thawed"]
    assert client.workspace.ops("delete") == [], "nothing is deleted"


def test_a_marker_expires_at_its_until() -> None:
    client = FakeWorkspaceClient()
    _freeze(client, until=NOW + 60)

    assert freezer.assert_publish_allowed(client, release_path=RELEASE, now=NOW + 61)[0].endswith(
        "expired"
    )


def test_a_shell_without_the_verify_key_reads_every_marker_as_frozen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeWorkspaceClient()
    marker = _freeze(client, until=NOW + 60)
    freezer.thaw(client, app_name=rr.APP_NAME, marker_id=marker, holder="deployer", environ=_env())
    monkeypatch.delenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY")

    assert freezer.assert_publish_allowed is not None
    with pytest.raises(freezer.FreezeRefused, match="unverifiable"):
        freezer.assert_publish_allowed(client, release_path=RELEASE, now=NOW + 61)


@pytest.mark.parametrize("tamper", ("until", "unsigned", "garbage"))
def test_a_tampered_marker_stays_frozen_and_a_forged_thaw_thaws_nothing(tamper: str) -> None:
    client = FakeWorkspaceClient()
    marker = _freeze(client)
    path = freezer.freeze_path(rr.APP_NAME, marker)
    document = json.loads(client.workspace.download(path).read())
    if tamper == "until":
        document["until"] = 0
    elif tamper == "unsigned":
        document.pop("attestation_signature")
    client.workspace.upload(
        path, json.dumps(document).encode() if tamper != "garbage" else b"{", overwrite=True
    )

    assert _code(client) == "frozen"
    forged = {"version": 1, "kind": "thaw", "marker_id": marker, "attestation_signature": "x"}
    client.workspace.upload(freezer.thaw_path(rr.APP_NAME, marker), json.dumps(forged).encode())
    assert _code(client) == "frozen"


def test_override_needs_the_exact_digest_for_this_marker_and_this_folder() -> None:
    client = FakeWorkspaceClient()
    marker = _freeze(client)
    plan = freezer.override_plan(marker_id=marker, release_path=RELEASE)
    other_folder = freezer.override_plan(
        marker_id=marker, release_path=RELEASE.replace("0123", "4567")
    )

    with pytest.raises(freezer.FreezeRefused) as refused:
        _allowed(client)
    assert ack_digest("override_freeze", plan) in str(
        refused.value
    ), "the refusal prints the ack to give"
    for ack in (
        None,
        ack_digest("no_rollback_for", plan),
        ack_digest("override_freeze", other_folder),
    ):
        assert _code(client, override_marker_id=marker, override_ack=ack) in {
            "ack_missing",
            "ack_mismatch",
        }
    assert (
        _code(client, override_marker_id="0" * 8, override_ack=ack_digest("override_freeze", plan))
        == "override_unknown"
    )
    notes = _allowed(
        client, override_marker_id=marker, override_ack=ack_digest("override_freeze", plan)
    )
    assert notes[-1] == f"freeze marker {marker} overridden by its exact override_freeze ack"


def test_an_override_lifts_one_marker_only() -> None:
    client = FakeWorkspaceClient()
    first = _freeze(client)
    _freeze(client, app="mip-app-two")
    ack = ack_digest(
        "override_freeze", freezer.override_plan(marker_id=first, release_path=RELEASE)
    )

    assert _code(client, override_marker_id=first, override_ack=ack) == "frozen"


def test_the_held_signed_lease_is_the_coordinated_path_and_is_proven() -> None:
    client = FakeWorkspaceClient()
    _freeze(client)

    notes = _allowed(client, held_lease=lambda: None, held_lease_app_name=rr.APP_NAME)
    assert notes[-1].startswith(f"the publish runs under {rr.APP_NAME}'s held signed lease")

    def lost() -> None:
        raise RuntimeError("App deployment lease was released")

    assert _code(client, held_lease=lost, held_lease_app_name=rr.APP_NAME) == "held_lease_unproven"
    assert _code(client, held_lease=lambda: None) == "held_lease_unproven", "it names its App"


def test_a_held_lease_exempts_its_own_apps_markers_only() -> None:
    """A lease taken on a decoy App lifts no freeze, with no ack and no audited thaw."""

    client = FakeWorkspaceClient()
    _freeze(client)

    assert _code(client, held_lease=lambda: None, held_lease_app_name="decoy-app") == "frozen"
    other = _freeze(client, app="other-app")
    assert _code(client, held_lease=lambda: None, held_lease_app_name=rr.APP_NAME) == "frozen"
    with pytest.raises(freezer.FreezeRefused, match=f"frozen by marker {other} "):
        _allowed(client, held_lease=lambda: None, held_lease_app_name=rr.APP_NAME)


# --- freeze never converges the lease root's ACL -----------------------------------------


class _AclTree(FakeTree):
    """The workspace tree plus the directory ACL API, exactly as the lease uses it."""

    def __init__(self) -> None:
        super().__init__()
        self.acl: list[SimpleNamespace] = []
        self.set_permissions_calls = 0

    def get_status(self, path: str) -> SimpleNamespace:
        status = super().get_status(path)
        status.object_id = "lease-root-object"
        return status

    def set_permissions(
        self, _kind: str, _object_id: str, *, access_control_list: list[Any]
    ) -> None:
        self.set_permissions_calls += 1
        self.acl = [
            SimpleNamespace(
                user_name=getattr(item, "user_name", None),
                service_principal_name=getattr(item, "service_principal_name", None),
                group_name=None,
                all_permissions=[
                    SimpleNamespace(permission_level=str(item.permission_level).split(".")[-1])
                ],
            )
            for item in access_control_list
        ]

    def get_permissions(self, _kind: str, _object_id: str) -> SimpleNamespace:
        return SimpleNamespace(access_control_list=list(self.acl))


def _acl_workspace(caller: str) -> SimpleNamespace:
    return SimpleNamespace(
        workspace=_AclTree(),
        current_user=SimpleNamespace(me=lambda: SimpleNamespace(user_name=caller)),
        service_principals=SimpleNamespace(
            list=lambda **_kw: [SimpleNamespace(application_id="deployer-app-id")]
        ),
    )


_LEASE_RECORD = {"holder": "deployer-app-id", "writer_application_id": "writer-id"}


def _freeze_cli(
    workspace: Any, monkeypatch: pytest.MonkeyPatch, record: dict[str, str] | None = _LEASE_RECORD
) -> int:
    import argparse
    import time

    from tools.databricks import app_deployment_lease as lease
    from tools.databricks import app_deployment_lease_cli as lease_cli

    monkeypatch.setattr(lease, "_download", lambda *_a, **_kw: record)
    args = argparse.Namespace(
        action="freeze",
        app_name=rr.APP_NAME,
        until=int(time.time()) + 3600,
        writer_application_id="writer-id",
        marker_id=None,
    )
    return lease_cli.freeze_action(lease, workspace, args, argparse.ArgumentParser())


def test_freeze_from_the_owners_shell_never_rewrites_the_lease_roots_acl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools.databricks import app_deployment_lease as lease

    workspace = _acl_workspace("owner@lender.example")
    workspace.workspace.mkdirs(freezer.LEASE_ROOT)
    lease._ensure_protected_root(
        workspace, holder="deployer-app-id", writer_application_id="writer-id"
    )
    governed = list(workspace.workspace.acl)

    assert _freeze_cli(workspace, monkeypatch) == 0
    assert workspace.workspace.set_permissions_calls == 1, "only the governed lease's own"
    assert workspace.workspace.acl == governed
    lease._assert_protected_root(
        workspace,
        holder="deployer-app-id",
        writer_application_id="writer-id",
        object_id="lease-root-object",
    )
    assert [m.app_name for m in freezer.judge_markers(workspace)] == [rr.APP_NAME]


def test_freeze_creates_a_missing_root_bare_and_refuses_an_unprotected_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tools.databricks import app_deployment_lease as lease

    bare = _acl_workspace("owner@lender.example")
    assert _freeze_cli(bare, monkeypatch) == 0
    assert bare.workspace.set_permissions_calls == 0 and bare.workspace.acl == []

    drifted = _acl_workspace("owner@lender.example")
    drifted.workspace.mkdirs(freezer.LEASE_ROOT)
    lease._ensure_protected_root(drifted, holder="owner@lender.example", writer_application_id="w")
    assert _freeze_cli(drifted, monkeypatch) == 2
    assert "REFUSED freeze_root_unprotected" in capsys.readouterr().err
    assert freezer.judge_markers(drifted) == [], "nothing was written"


def _entry(level: str, *, user: str = "", group: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        user_name=user or None,
        service_principal_name=None,
        group_name=group or None,
        all_permissions=[SimpleNamespace(permission_level=level)],
    )


def test_an_unsigned_freeze_refuses_before_it_creates_the_root(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = _acl_workspace("owner@lender.example")
    monkeypatch.delenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY")

    assert _freeze_cli(workspace, monkeypatch) == 2
    assert "REFUSED freeze_unsigned" in capsys.readouterr().err
    assert not workspace.workspace.exists(freezer.LEASE_ROOT), "a refused freeze changes nothing"
    assert workspace.workspace.ops("mkdirs") == [] and workspace.workspace.ops("upload") == []


_ADMINS = _entry("CAN_MANAGE", group="admins")
_NO_RECORD_ACLS = (
    ([_entry("CAN_MANAGE", group="users")], False),
    ([_ADMINS, _entry("CAN_EDIT", user="peer@lender.example")], False),
    ([_entry("CAN_READ", user="owner@lender.example")], False),
    ([_ADMINS, _entry("CAN_MANAGE", user="owner@lender.example")], True),
)


@pytest.mark.parametrize(("acl", "allowed"), _NO_RECORD_ACLS)
def test_an_existing_root_with_no_lease_record_is_still_judged_read_only(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    acl: list[SimpleNamespace],
    allowed: bool,
) -> None:
    """A root a freeze created bare (or another App's): only admins and the freezer hold it."""

    workspace = _acl_workspace("owner@lender.example")
    workspace.workspace.mkdirs(freezer.LEASE_ROOT)
    workspace.workspace.acl = list(acl)

    code = _freeze_cli(workspace, monkeypatch, record=None)
    assert workspace.workspace.set_permissions_calls == 0 and workspace.workspace.acl == acl
    if allowed:
        assert code == 0 and [m.app_name for m in freezer.judge_markers(workspace)] == [rr.APP_NAME]
        return
    assert code == 2 and "REFUSED freeze_root_unprotected" in capsys.readouterr().err
    assert freezer.judge_markers(workspace) == [], "nothing was written"


@pytest.mark.parametrize(("acl", "allowed"), _NO_RECORD_ACLS)
def test_a_root_the_freeze_creates_is_judged_before_its_first_marker(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    acl: list[SimpleNamespace],
    allowed: bool,
) -> None:
    """The creating freeze judges the new root exactly as the next freeze over it would."""

    workspace = _acl_workspace("owner@lender.example")
    workspace.workspace.acl = list(acl)  # what the root reads once mkdirs creates it

    code = _freeze_cli(workspace, monkeypatch, record=None)
    assert workspace.workspace.ops("mkdirs") == [freezer.LEASE_ROOT]
    assert workspace.workspace.set_permissions_calls == 0 and workspace.workspace.acl == acl
    if allowed:
        assert code == 0 and [m.app_name for m in freezer.judge_markers(workspace)] == [rr.APP_NAME]
        return
    err = capsys.readouterr().err
    assert code == 2 and "REFUSED freeze_root_unprotected" in err
    assert "created is left bare and empty" in err, "the refusal names what it left"
    assert workspace.workspace.ops("upload") == [] and freezer.judge_markers(workspace) == []


def test_a_lease_root_that_cannot_be_listed_refuses() -> None:
    client = FakeWorkspaceClient()
    client.workspace.mkdirs(freezer.LEASE_ROOT)
    client.workspace.fail_before[("list", freezer.LEASE_ROOT)] = RuntimeError("permission denied")

    assert _code(client) == "freeze_unreadable"


def test_freeze_is_create_only_bounded_and_refuses_a_second_freeze() -> None:
    client = FakeWorkspaceClient()
    with pytest.raises(freezer.FreezeRefused) as refused:
        _freeze(client, until=NOW - 1)
    assert refused.value.code == "freeze_until_invalid"
    with pytest.raises(freezer.FreezeRefused):
        _freeze(client, until=NOW + freezer.MAX_FREEZE_SECONDS + 1)
    _freeze(client)
    with pytest.raises(freezer.FreezeRefused) as refused:
        _freeze(client)
    assert refused.value.code == "already_frozen"
    unsigned = {**_env(), "MIP_AI_GATEWAY_PROOF_SIGNING_KEY": ""}
    with pytest.raises(freezer.FreezeRefused):
        freezer.freeze(
            client,
            app_name="other",
            until=int(NOW + 60),
            holder="d",
            now=NOW,
            environ=unsigned,
            ensure_root=lambda: None,
        )


# --- the emergency publisher ----------------------------------------------------------


def _stage(tmp_path: Path) -> tuple[Path, Path, str]:
    stage = tmp_path / "stage"
    files = {
        "app.yaml": b"command: []\n",
        "requirements.txt": b"x\n",
        "frontend/dist/index.html": b"<html></html>\n",
    }
    for relpath, data in files.items():
        (stage / relpath).parent.mkdir(parents=True, exist_ok=True)
        (stage / relpath).write_bytes(data)
    manifest = tmp_path / "manifest.json"
    written = contract.write_manifest(
        manifest, commit="0123456789ab" + "c" * 28, entries=contract.manifest_entries(stage)
    )
    return stage, manifest, written.release_id


def _publish_argv(stage: Path, manifest: Path, release_id: str, *extra: str) -> list[str]:
    owner = "owner@lender.example"
    files_root = f"/Workspace/Users/{owner}/.bundle/mortgage-intelligence-platform/dev/files"
    return [
        "publish",
        "--release-id",
        release_id,
        "--stage-dir",
        str(stage),
        "--manifest",
        str(manifest),
        "--files-root",
        files_root,
        "--expected-commit",
        "0123456789ab" + "c" * 28,
        "--expected-principal",
        owner,
        "--expected-target",
        "dev",
        "--expected-root-leaf",
        "dev",
        *extra,
    ]


def test_freeze_blocks_the_emergency_publish_before_any_upload(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # main() passes the publisher no clock, so the judge reads time.time():
    # pin it to the NOW the marker is written at. Against the real clock the
    # marker (frozen until NOW + 3600, a fixed 2027 instant) reads as expired
    # from 2027-01-15T09:00:00Z on, and the publish is no longer refused.
    monkeypatch.setattr(freezer, "time", SimpleNamespace(time=lambda: NOW))
    client = FakeWorkspaceClient()
    marker = _freeze(client)
    stage, manifest, release_id = _stage(tmp_path)

    assert source.main(_publish_argv(stage, manifest, release_id), client=lambda: client) == 2
    err = capsys.readouterr().err
    assert f"refused: frozen: the emergency publisher is frozen by marker {marker}" in err
    assert client.workspace.ops("upload") == [
        p for p in client.workspace.ops("upload") if freezer.LEASE_ROOT in p
    ]

    ack = re.search(r"--override-freeze-ack ([0-9a-f]{64})", err)
    assert ack is not None
    argv = _publish_argv(
        stage,
        manifest,
        release_id,
        "--override-freeze",
        marker,
        "--override-freeze-ack",
        ack.group(1),
    )
    assert source.main(argv, client=lambda: client) == 0
    assert "overridden by its exact override_freeze ack" in capsys.readouterr().out


def test_the_lease_cli_offers_freeze_and_thaw() -> None:
    text = (REPO / "tools" / "databricks" / "app_deployment_lease_cli.py").read_text(
        encoding="utf-8"
    )

    assert '"freeze", "thaw"' in text and "--until" in text and "--marker-id" in text


def test_the_emergency_recipe_says_the_publisher_obeys_the_freeze() -> None:
    docs = (REPO / "docs" / "deployment.md").read_text(encoding="utf-8")
    recipe = docs[docs.index("### The emergency recipe, command by command") :]
    recipe = " ".join(recipe[: recipe.index("\n## ")].split())

    for phrase in (
        "freeze marker",
        "--override-freeze",
        "override_freeze",
        "MIP_AI_GATEWAY_PROOF_VERIFY_KEY",
        # The full live effect: the root must be listable once it exists, and the
        # verify key is needed after any freeze, even with no marker frozen.
        "the publish refuses (`freeze_unreadable`) even with no marker",
        "after any freeze every later publish needs `MIP_AI_GATEWAY_PROOF_VERIFY_KEY`",
        "exempts that App's own markers only",
        "Neither ever sets a permission",
    ):
        assert phrase in recipe, phrase
