"""The per-release App source path and manifest contract.

tools/databricks/app_release_contract.py names where a release folder lives
(``<bundle root>/app-src/<id>``, a sibling of the bundle's ``files/``) and
what its manifest proves. Every refusal here is a ReleaseContractError, which
the release-id CLI turns into exit 2.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.databricks import app_release_contract as contract
from tools.databricks import converge_static_app_source as converge
from tools.databricks.app_deployment_state import immutable_source
from tools.databricks.app_rollback_record_contract import _validated_payload

PRINCIPAL = "operator@example.com"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
OTHER_COMMIT = "fedcba9876543210fedcba9876543210fedcba98"
BUNDLE = f"/Workspace/Users/{PRINCIPAL}/.bundle/mortgage-intelligence-platform"
RELEASE_ID = f"{COMMIT[:12]}-0123456789ab"


def _release(leaf: str = "dev", release_id: str = RELEASE_ID) -> str:
    return contract.release_path(
        principal=PRINCIPAL, target="dev", root_leaf=leaf, release_id=release_id, commit=COMMIT
    )


def _folder(tmp_path: Path, files: dict[str, bytes]) -> Path:
    folder = tmp_path / "stage"
    for relpath, data in files.items():
        path = folder / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return folder


FILES = {"app.yaml": b"command: []\n", "backend/main.py": b"X = 1\n", "frontend/dist/index.html": b"<html/>"}


def test_the_files_root_is_unchanged_and_the_release_root_is_its_sibling() -> None:
    files = contract.files_root(principal=PRINCIPAL, target="dev")

    assert files == f"{BUNDLE}/dev/files"
    assert files == converge._expected_source_path(principal=PRINCIPAL, target="dev")
    assert contract.release_root(principal=PRINCIPAL, target="dev") == f"{BUNDLE}/dev/app-src"
    assert _release() == f"{BUNDLE}/dev/app-src/{RELEASE_ID}"


def test_the_ci_install_release_root_renders_under_its_own_leaf() -> None:
    path = _release(leaf="dev-ci")

    assert path == f"{BUNDLE}/dev-ci/app-src/{RELEASE_ID}"
    assert contract.release_id_of(path, principal=PRINCIPAL, target="dev", root_leaf="dev-ci") == (
        RELEASE_ID
    )
    # A release folder of the CI install is never one of the live install's.
    assert contract.release_id_of(path, principal=PRINCIPAL, target="dev") is None
    assert contract.release_id_of(_release(), principal=PRINCIPAL, target="dev", root_leaf="dev-ci") is None


def test_a_release_path_has_nine_segments_and_no_snapshot_marker() -> None:
    path = _release()

    assert len(Path(path).parts) == contract.RELEASE_PATH_SEGMENTS == 9
    assert "/src/" not in path
    assert contract.api_path(path) == path.removeprefix("/Workspace")


@pytest.mark.parametrize(
    "release_id",
    [
        "",
        COMMIT[:12],
        f"{COMMIT[:12]}-0123456789AB",
        f"{COMMIT[:12]}-0123456789a",
        f"{COMMIT[:12]}-0123456789abc",
        f"{COMMIT[:12]}_0123456789ab",
        f"{COMMIT[:12]}-0123456789ab/x",
        f"../{COMMIT[:11]}-0123456789ab",
        f" {RELEASE_ID}",
        f"{RELEASE_ID}\n",
    ],
)
def test_a_release_id_that_is_not_two_12_hex_halves_is_refused(release_id: str) -> None:
    with pytest.raises(contract.ReleaseContractError, match="release id"):
        _release(release_id=release_id)


def test_a_release_id_must_carry_its_commit_prefix() -> None:
    with pytest.raises(contract.ReleaseContractError, match="commit's prefix"):
        contract.release_path(
            principal=PRINCIPAL, target="dev", release_id=RELEASE_ID, commit=OTHER_COMMIT
        )
    with pytest.raises(contract.ReleaseContractError, match="40-hex"):
        contract.release_path(
            principal=PRINCIPAL, target="dev", release_id=RELEASE_ID, commit=COMMIT[:12]
        )


@pytest.mark.parametrize(
    ("principal", "target", "leaf", "message"),
    [
        ("src", "dev", None, "governed per-release folder"),
        ("a/b", "dev", None, "principal"),
        (" op", "dev", None, "principal"),
        ("..", "dev", None, "principal"),
        (PRINCIPAL, "dev.prod", None, "target"),
        (PRINCIPAL, "dev", ".hidden", "root leaf"),
        (PRINCIPAL, "dev", "a/b", "root leaf"),
    ],
)
def test_a_release_path_refuses_a_bad_root_or_a_snapshot_marker(
    principal: str, target: str, leaf: str | None, message: str
) -> None:
    with pytest.raises(contract.ReleaseContractError, match=message):
        contract.release_path(principal=principal, target=target, root_leaf=leaf, release_id=RELEASE_ID)


@pytest.mark.parametrize(
    "path",
    [
        f"{BUNDLE}/dev/files",
        f"{BUNDLE}/dev/app-src",
        f"{BUNDLE}/dev/app-src/",
        f"{BUNDLE}/dev/app-src/{RELEASE_ID}/",
        f"{BUNDLE}/dev/app-src/{RELEASE_ID}/src/abc",
        f"{BUNDLE}/dev/app-src/0123abc",
        f"{BUNDLE}/dev/app-src/./{RELEASE_ID}",
        f"{BUNDLE}/dev/files/app-src/{RELEASE_ID}",
        f"/Workspace/Users/other@example.com/.bundle/mortgage-intelligence-platform/dev/app-src/{RELEASE_ID}",
        f"/Users/{PRINCIPAL}/.bundle/mortgage-intelligence-platform/dev/app-src/{RELEASE_ID}",
        f"/Workspace/Users/app-sp/src/{RELEASE_ID}",
        None,
    ],
)
def test_only_an_exact_release_folder_of_this_bundle_root_has_an_id(path: str | None) -> None:
    assert contract.release_id_of(path, principal=PRINCIPAL, target="dev") is None


def test_a_release_path_is_never_an_immutable_snapshot_for_rollback() -> None:
    # A rollback record must name the platform's /src/ snapshot, never a
    # release folder; capture still accepts a release folder as its candidate.
    path = _release()
    payload = {
        "source_code_path": path,
        "mode": "SNAPSHOT",
        "env_vars": [
            {"name": "MIP_LAKEBASE_INSTANCE", "value": "mip-app-state"},
            {"name": "LAKEBASE_INSTANCE_NAME", "value": "mip-app-state"},
        ],
    }

    with pytest.raises(RuntimeError, match="immutable source artifact"):
        immutable_source(SimpleNamespace(deployment_artifacts=SimpleNamespace(source_code_path=path)))
    with pytest.raises(RuntimeError, match="not an immutable deployment artifact"):
        _validated_payload(payload, require_immutable_source=True)
    assert _validated_payload(payload, require_immutable_source=False)["source_code_path"] == path
    snapshot = path.replace("/app-src/", "/src/")
    assert _validated_payload({**payload, "source_code_path": snapshot})["source_code_path"] == snapshot


def test_api_path_refuses_a_path_outside_the_workspace_tree() -> None:
    with pytest.raises(contract.ReleaseContractError, match="/Workspace/"):
        contract.api_path("/Users/x/app-src")


@pytest.mark.parametrize(
    "relpath",
    ["", "/abs", "a//b", "a/./b", "a/../b", "a/", ".env", "backend/.cache/x", "a\\b", "a\tb", "a\nb", "a\x7fb"],
)
def test_a_manifest_path_must_be_canonical_relative_and_visible(relpath: str) -> None:
    with pytest.raises(contract.ReleaseContractError, match="not canonical"):
        contract.check_relpath(relpath)


def test_manifest_entries_list_every_regular_file_sorted_with_size_and_hash(tmp_path: Path) -> None:
    folder = _folder(tmp_path, FILES)

    entries = contract.manifest_entries(folder)

    assert [entry.path for entry in entries] == sorted(FILES)
    assert entries[0] == contract.ManifestEntry(
        "app.yaml", len(FILES["app.yaml"]), contract.hashlib.sha256(FILES["app.yaml"]).hexdigest()
    )


@pytest.mark.parametrize("kind", ["file_symlink", "dir_symlink", "fifo", "hidden", "root_symlink"])
def test_manifest_entries_refuse_anything_but_visible_regular_files(tmp_path: Path, kind: str) -> None:
    folder = _folder(tmp_path, FILES)
    if kind == "file_symlink":
        (folder / "link.py").symlink_to(folder / "app.yaml")
    elif kind == "dir_symlink":
        (folder / "linked").symlink_to(folder / "backend", target_is_directory=True)
    elif kind == "fifo":
        os.mkfifo(folder / "pipe")
    elif kind == "hidden":
        (folder / "backend" / ".env").write_text("X=1\n", encoding="utf-8")
    else:
        alias = tmp_path / "alias"
        alias.symlink_to(folder, target_is_directory=True)
        folder = alias

    with pytest.raises(contract.ReleaseContractError):
        contract.manifest_entries(folder)


def test_the_release_id_is_deterministic_and_moves_with_one_byte_or_one_path(tmp_path: Path) -> None:
    entries = contract.manifest_entries(_folder(tmp_path / "a", FILES))
    same = contract.manifest_entries(_folder(tmp_path / "b", FILES))
    byte = contract.manifest_entries(_folder(tmp_path / "c", {**FILES, "app.yaml": b"command: [] \n"}))
    moved = dict(FILES)
    moved["backend/app.py"] = moved.pop("backend/main.py")
    path = contract.manifest_entries(_folder(tmp_path / "d", moved))

    rid = contract.release_id(COMMIT, entries)

    assert contract.RELEASE_ID_RE.fullmatch(rid) and rid.startswith(COMMIT[:12])
    assert contract.release_id(COMMIT, same) == rid
    assert contract.release_id(COMMIT, byte) != rid
    assert contract.release_id(COMMIT, path) != rid
    assert contract.release_id(OTHER_COMMIT, entries)[13:] == rid[13:]


def test_file_modes_are_not_part_of_the_release_id(tmp_path: Path) -> None:
    folder = _folder(tmp_path, FILES)
    before = contract.release_id(COMMIT, contract.manifest_entries(folder))
    (folder / "app.yaml").chmod(0o755)

    assert contract.release_id(COMMIT, contract.manifest_entries(folder)) == before


def _written(tmp_path: Path) -> tuple[Path, contract.Manifest]:
    path = tmp_path / "manifest.json"
    manifest = contract.write_manifest(
        path, commit=COMMIT, entries=contract.manifest_entries(_folder(tmp_path, FILES))
    )
    return path, manifest


def test_a_written_manifest_reads_back_and_recomputes(tmp_path: Path) -> None:
    path, manifest = _written(tmp_path)

    assert contract.read_manifest(path, expected_commit=COMMIT) == manifest
    assert contract.read_manifest(path, expected_commit=None) == manifest
    assert manifest.paths == tuple(sorted(FILES))


def _mutate(document: dict[str, object], change: str) -> None:
    files = document["files"]
    assert isinstance(files, list)
    if change == "version":
        document["version"] = 2
    elif change == "bool_version":
        document["version"] = True
    elif change == "extra_key":
        document["note"] = "x"
    elif change == "short_commit":
        document["commit"] = COMMIT[:12]
    elif change == "unsorted":
        files.reverse()
    elif change == "duplicate":
        files.append(dict(files[0]))
    elif change == "id":
        document["release_id"] = f"{COMMIT[:12]}-000000000000"
    elif change == "size":
        files[0]["size"] += 1
    elif change == "bool_size":
        files[0]["size"] = True
    elif change == "negative_size":
        files[0]["size"] = -1
    elif change == "sha":
        files[0]["sha256"] = files[0]["sha256"].upper()
    elif change == "path":
        files[0]["path"] = "./app.yaml"
    elif change == "entry_key":
        files[0]["mode"] = "0644"
    elif change == "empty":
        files.clear()
    else:
        document["files"] = {}


@pytest.mark.parametrize(
    "change",
    ["version", "bool_version", "extra_key", "short_commit", "unsorted", "duplicate", "id", "size",
     "bool_size", "negative_size", "sha", "path", "entry_key", "empty", "files_not_list"],
)
def test_read_manifest_refuses_anything_but_an_exact_version_1_manifest(
    tmp_path: Path, change: str
) -> None:
    path, _ = _written(tmp_path)
    document = json.loads(path.read_text(encoding="utf-8"))
    _mutate(document, change)
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(contract.ReleaseContractError):
        contract.read_manifest(path, expected_commit=None)


def test_read_manifest_refuses_another_commit_and_unreadable_input(tmp_path: Path) -> None:
    path, _ = _written(tmp_path)

    with pytest.raises(contract.ReleaseContractError, match="another commit"):
        contract.read_manifest(path, expected_commit=OTHER_COMMIT)
    with pytest.raises(contract.ReleaseContractError, match="cannot read"):
        contract.read_manifest(tmp_path / "absent.json", expected_commit=COMMIT)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(contract.ReleaseContractError, match="cannot read"):
        contract.read_manifest(path, expected_commit=COMMIT)


def test_the_cli_prints_the_id_and_exits_2_on_any_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path, manifest = _written(tmp_path)
    args = ["release-id", "--manifest", str(path), "--expected-commit"]

    assert contract.main([*args, COMMIT]) == 0
    assert capsys.readouterr().out == f"{manifest.release_id}\n"
    for expected in (OTHER_COMMIT, COMMIT[:12], COMMIT.upper()):
        assert contract.main([*args, expected]) == 2
        captured = capsys.readouterr()
        assert captured.out == "" and captured.err.startswith("refused: ")
    path.write_text(path.read_text(encoding="utf-8").replace('"size": 6', '"size": 7'), encoding="utf-8")
    assert contract.main([*args, COMMIT]) == 2
    assert "does not recompute" in capsys.readouterr().err
