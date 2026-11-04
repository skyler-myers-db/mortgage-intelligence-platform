"""The signed freeze marker and the emergency publisher that obeys it (M2-PR7, attack O8)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures import rollback_records as rr
from tests.fixtures.fake_workspace import FakeWorkspaceClient
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

    assert _allowed(client, held_lease=lambda: None)[0].startswith(
        "the publish runs under the held signed lease"
    )

    def lost() -> None:
        raise RuntimeError("App deployment lease was released")

    assert _code(client, held_lease=lost) == "held_lease_unproven"


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
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
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
    recipe = recipe[: recipe.index("\n## ")]

    for phrase in (
        "freeze marker",
        "--override-freeze",
        "override_freeze",
        "MIP_AI_GATEWAY_PROOF_VERIFY_KEY",
    ):
        assert phrase in recipe, phrase
