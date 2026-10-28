"""converge_static_app_source release mode: an activation sees exactly its release.

Before every App activation, deploy.sh converges the source it is about to
promote. In release mode the source must be exactly
``<bundle root>/app-src/<id>`` for the deploy commit, the manifest must be
that release's, and the remote folder must list exactly the manifest's
files with their sizes. Nothing is ever deleted in release mode. The folder
is published with tools/databricks/app_release_source.py into
tests/fixtures/fake_workspace.py.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from databricks.sdk.service.workspace import ObjectType

from tests.fixtures.fake_workspace import FakeWorkspaceClient
from tools.databricks import app_release_contract as contract
from tools.databricks import app_release_source as source
from tools.databricks import converge_static_app_source as converge

PRINCIPAL = "deployer@example.com"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
FILES = {
    "app.yaml": b"command: ['python', '-m', 'backend.runtime']\n",
    "requirements.txt": b"-c uv.lock\n",
    "backend/main.py": b"APP = 1\n",
    "frontend/dist/index.html": b"<html></html>\n",
}


def _published(
    tmp_path: Path, files: dict[str, bytes] = FILES, leaf: str | None = None
) -> tuple[FakeWorkspaceClient, Path, contract.Manifest, str]:
    stage = tmp_path / "stage"
    for relpath, data in files.items():
        (stage / relpath).parent.mkdir(parents=True, exist_ok=True)
        (stage / relpath).write_bytes(data)
    manifest_path = tmp_path / "manifest.json"
    manifest = contract.write_manifest(
        manifest_path, commit=COMMIT, entries=contract.manifest_entries(stage)
    )
    client = FakeWorkspaceClient()
    report = source.publish(
        client, release_id=manifest.release_id, stage_dir=stage, manifest_path=manifest_path,
        files_root=contract.files_root(principal=PRINCIPAL, target="dev", root_leaf=leaf),
        expected_commit=COMMIT, principal=PRINCIPAL, target="dev", root_leaf=leaf,
    )
    return client, manifest_path, manifest, report.release_path


def _converge(
    client: FakeWorkspaceClient, manifest_path: Path, release_id: str, path: str, **overrides: object
) -> contract.Manifest:
    kwargs: dict[str, object] = {
        "source_code_path": path,
        "release_id": release_id,
        "manifest_path": manifest_path,
        "expected_git_sha": COMMIT,
        "expected_principal": PRINCIPAL,
        "expected_target": "dev",
        **overrides,
    }
    return converge.converge_release_source(client, **kwargs)  # type: ignore[arg-type]


def test_a_published_release_matches_its_manifest_and_nothing_is_deleted(tmp_path: Path) -> None:
    client, manifest_path, manifest, path = _published(tmp_path)

    assert _converge(client, manifest_path, manifest.release_id, path) == manifest
    assert client.workspace.ops("delete") == client.workspace.ops("upload")[len(FILES):] == []
    assert client.workspace.ops("download")[len(FILES):] == []  # sizes listed: no hashing


def test_the_ci_install_release_folder_converges_only_with_its_leaf(tmp_path: Path) -> None:
    client, manifest_path, manifest, path = _published(tmp_path, leaf="dev-ci")

    _converge(client, manifest_path, manifest.release_id, path, expected_root_leaf="dev-ci")
    with pytest.raises(contract.ReleaseContractError, match="exact release folder"):
        _converge(client, manifest_path, manifest.release_id, path)


@pytest.mark.parametrize(
    "path_case",
    ["files_root", "trailing_slash", "other_id", "snapshot", "other_principal"],
)
def test_any_path_but_the_exact_release_folder_is_refused(tmp_path: Path, path_case: str) -> None:
    client, manifest_path, manifest, path = _published(tmp_path)
    other = {
        "files_root": contract.files_root(principal=PRINCIPAL, target="dev"),
        "trailing_slash": f"{path}/",
        "other_id": path.replace(manifest.release_id, f"{COMMIT[:12]}-000000000000"),
        "snapshot": path.replace("/app-src/", "/src/"),
        "other_principal": path.replace(PRINCIPAL, "other@example.com"),
    }[path_case]

    with pytest.raises(contract.ReleaseContractError, match="exact release folder"):
        _converge(client, manifest_path, manifest.release_id, other)


def test_a_manifest_or_sha_of_another_release_is_refused(tmp_path: Path) -> None:
    client, manifest_path, manifest, path = _published(tmp_path)
    other_id = f"{COMMIT[:12]}-000000000000"

    with pytest.raises(contract.ReleaseContractError, match="manifest's release id"):
        _converge(client, manifest_path, other_id, path.replace(manifest.release_id, other_id))
    with pytest.raises(contract.ReleaseContractError, match="commit's prefix"):
        _converge(client, manifest_path, manifest.release_id, path, expected_git_sha="f" * 40)


@pytest.mark.parametrize("change", ["extra", "missing", "size", "notebook", "package_json"])
def test_a_release_folder_that_drifted_from_its_manifest_is_refused_untouched(
    tmp_path: Path, change: str
) -> None:
    client, manifest_path, manifest, path = _published(tmp_path)
    root = contract.api_path(path)
    tree = client.workspace
    if change == "extra":
        tree.add_file(f"{root}/backend/extra.py", b"x\n")
    elif change == "missing":
        del tree.nodes[f"{root}/backend/main.py"]
    elif change == "size":
        tree.nodes[f"{root}/backend/main.py"].data = b"APP = 10\n"
    elif change == "notebook":
        tree.nodes[f"{root}/backend/main.py"].kind = ObjectType.NOTEBOOK
    else:
        tree.add_file(f"{root}/package.json", b"{}")
    before = dict(tree.nodes)

    with pytest.raises(contract.ReleaseContractError):
        _converge(client, manifest_path, manifest.release_id, path)
    assert tree.nodes == before
    assert tree.ops("delete") == []


def test_a_root_node_manifest_is_named_and_never_deleted(tmp_path: Path) -> None:
    client, manifest_path, manifest, path = _published(tmp_path)
    client.workspace.add_file(f"{contract.api_path(path)}/package-lock.json", b"{}")

    with pytest.raises(contract.ReleaseContractError, match="root Node manifests"):
        _converge(client, manifest_path, manifest.release_id, path)
    assert client.workspace.exists(f"{contract.api_path(path)}/package-lock.json")


@pytest.mark.parametrize(
    "files",
    [
        {**FILES, "frontend/dist/index.html": b""},
        {key: value for key, value in FILES.items() if key != "frontend/dist/index.html"},
        {**FILES, "package.json": b"{}"},
    ],
)
def test_a_manifest_without_a_built_index_or_with_a_node_manifest_is_refused(
    tmp_path: Path, files: dict[str, bytes]
) -> None:
    stage = tmp_path / "stage"
    for relpath, data in files.items():
        (stage / relpath).parent.mkdir(parents=True, exist_ok=True)
        (stage / relpath).write_bytes(data)
    manifest_path = tmp_path / "manifest.json"
    manifest = contract.write_manifest(
        manifest_path, commit=COMMIT, entries=contract.manifest_entries(stage)
    )
    path = contract.release_path(principal=PRINCIPAL, target="dev", release_id=manifest.release_id)

    with pytest.raises(contract.ReleaseContractError, match="index.html and no root"):
        _converge(FakeWorkspaceClient(), manifest_path, manifest.release_id, path)


@pytest.mark.parametrize(("data", "ok"), [(FILES["backend/main.py"], True), (b"APP = 2\n", False)])
def test_a_file_listed_without_a_size_is_hashed(tmp_path: Path, data: bytes, ok: bool) -> None:
    client, manifest_path, manifest, path = _published(tmp_path)
    root = contract.api_path(path)
    target = f"{root}/backend/main.py"
    client.workspace.nodes[target].data = data
    real_list = client.workspace.list

    def list_without_size(api: str, **kwargs: object) -> object:
        return iter(
            SimpleNamespace(path=item.path, object_type=item.object_type,
                            size=None if item.path == target else item.size)
            for item in real_list(api, **kwargs)  # type: ignore[arg-type]
        )

    client.workspace.list = list_without_size  # type: ignore[method-assign]
    downloads = len(client.workspace.ops("download"))

    if ok:
        _converge(client, manifest_path, manifest.release_id, path)
    else:
        with pytest.raises(contract.ReleaseContractError, match="remote bytes differ"):
            _converge(client, manifest_path, manifest.release_id, path)
    assert client.workspace.ops("download")[downloads:] == [target]


def test_the_cli_requires_the_three_release_flags_together_and_exits_2_on_a_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    client, manifest_path, manifest, path = _published(tmp_path)
    base = ["--source-code-path", path, "--expected-principal", PRINCIPAL, "--expected-target", "dev"]
    release = ["--release-id", manifest.release_id, "--release-manifest", str(manifest_path),
               "--expected-git-sha", COMMIT]

    for partial in (release[:2], release[2:4], release[:4], release[2:]):
        with pytest.raises(SystemExit) as exit_info:
            converge.main([*base, *partial], client=lambda: client)
        assert exit_info.value.code == 2
    assert converge.main([*base, *release], client=lambda: client) == 0
    assert f"release {manifest.release_id} matches its manifest" in capsys.readouterr().out
    client.workspace.add_file(f"{contract.api_path(path)}/package.json", b"{}")
    assert converge.main([*base, *release], client=lambda: client) == 2
    assert "root Node manifests" in capsys.readouterr().err
    assert client.workspace.ops("delete") == []
