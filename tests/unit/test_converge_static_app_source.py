from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest
from databricks.sdk.errors import PermissionDenied, ResourceDoesNotExist

from tools.databricks.converge_static_app_source import converge_static_app_source

PRINCIPAL = "operator@example.com"
TARGET = "dev"
SOURCE = f"/Workspace/Users/{PRINCIPAL}/.bundle/mortgage-intelligence-platform/{TARGET}/files"
API_ROOT = SOURCE.removeprefix("/Workspace")


def _file(path: str, *, size: object = 1) -> SimpleNamespace:
    return SimpleNamespace(path=path, object_type="FILE", size=size)


def _workspace(*, manifests: set[str] | None = None) -> MagicMock:
    present = set(manifests or set())
    index = f"{API_ROOT}/frontend/dist/index.html"
    client = MagicMock()

    def get_status(path: str) -> SimpleNamespace:
        if path == index:
            return _file(path, size=42)
        if path in present:
            return _file(path)
        raise ResourceDoesNotExist(path)

    def delete(path: str, *, recursive: bool) -> None:
        assert recursive is False
        present.remove(path)

    client.workspace.get_status.side_effect = get_status
    client.workspace.delete.side_effect = delete
    return client


def _converge(
    client: MagicMock,
    *,
    source: str = SOURCE,
    expected_principal: str = PRINCIPAL,
    expected_target: str = TARGET,
) -> None:
    converge_static_app_source(
        client,
        source_code_path=source,
        expected_principal=expected_principal,
        expected_target=expected_target,
    )


def test_converges_prebuilt_source_and_removes_exact_root_manifests() -> None:
    manifests = {
        f"{API_ROOT}/package.json",
        f"{API_ROOT}/package-lock.json",
    }
    client = _workspace(manifests=manifests)

    _converge(client)

    assert client.workspace.delete.call_args_list == [
        call(f"{API_ROOT}/package.json", recursive=False),
        call(f"{API_ROOT}/package-lock.json", recursive=False),
    ]


@pytest.mark.parametrize(
    "source",
    [
        "/Workspace/Users/operator@example.com/project/files",
        "/Workspace/Users/operator@example.com/.bundle/mortgage-intelligence-platform/dev",
        "/Workspace/Users/operator@example.com/.bundle/mortgage-intelligence-platform/files",
        "/Users/operator@example.com/.bundle/mortgage-intelligence-platform/dev/files",
        "/Workspace/Shared/.bundle/mortgage-intelligence-platform/dev/files",
        "/Workspace/Users/operator@example.com/team/.bundle/mortgage-intelligence-platform/dev/files",
        f"{SOURCE}/",
        f"{SOURCE}/../files",
        f"{SOURCE}/nested/files",
        SOURCE.replace("/dev/files", "/dev.prod/files"),
        SOURCE.replace("/dev/files", "/.hidden/files"),
        f" {SOURCE}",
        f"{SOURCE} ",
    ],
)
def test_rejects_noncanonical_or_unbounded_source_root(source: str) -> None:
    with pytest.raises(RuntimeError, match="App source path"):
        _converge(_workspace(), source=source)


@pytest.mark.parametrize(
    ("principal", "target"),
    [
        ("other@example.com", TARGET),
        (PRINCIPAL, "prod"),
        ("operator/team", TARGET),
        (" operator@example.com", TARGET),
        (".", TARGET),
        (PRINCIPAL, "dev.prod"),
    ],
)
def test_rejects_source_not_bound_to_expected_principal_and_target(
    principal: str,
    target: str,
) -> None:
    with pytest.raises(RuntimeError, match="App source|expected App source"):
        _converge(
            _workspace(),
            expected_principal=principal,
            expected_target=target,
        )


def test_requires_non_empty_prebuilt_index() -> None:
    client = _workspace()
    client.workspace.get_status.side_effect = lambda path: _file(path, size=0)

    with pytest.raises(RuntimeError, match="non-empty frontend/dist/index.html"):
        _converge(client)


@pytest.mark.parametrize("size", [None, "42", 1.5, True, 0, -1])
def test_requires_strict_positive_integer_prebuilt_index_size(size: object) -> None:
    client = _workspace()
    client.workspace.get_status.side_effect = lambda path: _file(path, size=size)

    with pytest.raises(RuntimeError, match="non-empty frontend/dist/index.html"):
        _converge(client)


@pytest.mark.parametrize(
    ("actual_path", "object_type"),
    [
        (f"{API_ROOT}/other.html", "FILE"),
        (f"{API_ROOT}/frontend/dist/index.html", "DIRECTORY"),
    ],
)
def test_rejects_wrong_index_identity_or_type(
    actual_path: str,
    object_type: str,
) -> None:
    client = _workspace()
    client.workspace.get_status.side_effect = lambda _path: SimpleNamespace(
        path=actual_path,
        object_type=object_type,
        size=42,
    )

    with pytest.raises(RuntimeError, match="invalid identity"):
        _converge(client)


@pytest.mark.parametrize("actual_path", [f" {API_ROOT}/frontend/dist/index.html ", 42])
def test_rejects_non_exact_or_non_string_status_path(actual_path: object) -> None:
    client = _workspace()
    client.workspace.get_status.side_effect = lambda _path: SimpleNamespace(
        path=actual_path,
        object_type="FILE",
        size=42,
    )

    with pytest.raises(RuntimeError, match="invalid identity"):
        _converge(client)


def test_accepts_already_absent_root_manifests_without_delete() -> None:
    client = _workspace()

    _converge(client)

    client.workspace.delete.assert_not_called()


def test_fails_closed_when_manifest_status_cannot_be_read() -> None:
    client = _workspace()
    index = f"{API_ROOT}/frontend/dist/index.html"

    def get_status(path: str) -> SimpleNamespace:
        if path == index:
            return _file(path, size=42)
        raise PermissionDenied(path)

    client.workspace.get_status.side_effect = get_status

    with pytest.raises(RuntimeError, match="could not read uploaded App source object"):
        _converge(client)


def test_fails_closed_when_successful_delete_leaves_manifest_present() -> None:
    manifest = f"{API_ROOT}/package.json"
    client = _workspace(manifests={manifest})
    client.workspace.delete.side_effect = lambda *_args, **_kwargs: None

    with pytest.raises(RuntimeError, match="manifest remained after deletion"):
        _converge(client)


def test_fails_closed_when_delete_is_denied_and_manifest_remains() -> None:
    manifest = f"{API_ROOT}/package.json"
    client = _workspace(manifests={manifest})
    client.workspace.delete.side_effect = PermissionDenied(manifest)

    with pytest.raises(RuntimeError, match="manifest remained after deletion"):
        _converge(client)


def test_fails_closed_when_post_delete_status_is_unreadable() -> None:
    manifest = f"{API_ROOT}/package.json"
    client = _workspace(manifests={manifest})
    index = f"{API_ROOT}/frontend/dist/index.html"
    calls: dict[str, int] = {}

    def get_status(path: str) -> SimpleNamespace:
        calls[path] = calls.get(path, 0) + 1
        if path == index:
            return _file(path, size=42)
        if path == manifest and calls[path] == 1:
            return _file(path)
        if path == manifest:
            raise PermissionDenied(path)
        raise ResourceDoesNotExist(path)

    client.workspace.get_status.side_effect = get_status
    client.workspace.delete.side_effect = TimeoutError("ambiguous")

    with pytest.raises(RuntimeError, match="ambiguous deletion"):
        _converge(client)


def test_accepts_ambiguous_delete_only_after_proven_absence() -> None:
    manifest = f"{API_ROOT}/package.json"
    client = _workspace(manifests={manifest})

    def delete(path: str, *, recursive: bool) -> None:
        assert path == manifest
        assert recursive is False
        client.workspace.get_status.side_effect = lambda requested: (
            _file(f"{API_ROOT}/frontend/dist/index.html", size=42)
            if requested.endswith("frontend/dist/index.html")
            else (_ for _ in ()).throw(ResourceDoesNotExist(requested))
        )
        raise TimeoutError("ambiguous")

    client.workspace.delete.side_effect = delete

    _converge(client)
