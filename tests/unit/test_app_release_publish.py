"""``app_release_source publish`` and ``verify-deployment`` against a fake workspace.

Publish uploads a staged release folder create-only to
``<bundle root>/app-src/<id>`` and proves it by listing and sha256; it never
deletes or overwrites. Every local refusal exits 2 before a workspace client
exists. tests/fixtures/fake_workspace.py models the Workspace API, including
AUTO's notebook detection and ResourceAlreadyExists.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from databricks.sdk.errors import (
    InternalError,
    PermissionDenied,
    RequestLimitExceeded,
    ResourceAlreadyExists,
    ResourceExhausted,
    TemporarilyUnavailable,
    TooManyRequests,
)
from databricks.sdk.service.workspace import ImportFormat, ObjectType

from tests.fixtures.fake_workspace import FakeWorkspaceClient, deployment
from tools.databricks import app_release_contract as contract
from tools.databricks import app_release_retry as retry
from tools.databricks import app_release_source as source

PRINCIPAL = "deployer@example.com"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
FILES_ROOT = contract.files_root(principal=PRINCIPAL, target="dev")
FILES = {
    "app.yaml": b"command: ['python', '-m', 'backend.runtime']\n",
    "requirements.txt": b"# deps-digest\n-c uv.lock\n",
    "backend/__init__.py": b"",
    "backend/main.py": b"APP = 1\n",
    "frontend/dist/index.html": b"<html></html>\n",
    "frontend/dist/assets/app-DnQ3_k-1.js": b"app();\n",
}


def _stage(tmp_path: Path, files: dict[str, bytes] = FILES) -> tuple[Path, Path, contract.Manifest]:
    stage_dir = tmp_path / "stage"
    for relpath, data in files.items():
        path = stage_dir / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest_path = tmp_path / "manifest.json"
    manifest = contract.write_manifest(
        manifest_path, commit=COMMIT, entries=contract.manifest_entries(stage_dir)
    )
    return stage_dir, manifest_path, manifest


def _args(stage_dir: Path, manifest_path: Path, release_id: str, **overrides: str) -> list[str]:
    values = {
        "--release-id": release_id,
        "--stage-dir": str(stage_dir),
        "--manifest": str(manifest_path),
        "--files-root": FILES_ROOT,
        "--expected-commit": COMMIT,
        "--expected-principal": PRINCIPAL,
        "--expected-target": "dev",
        **overrides,
    }
    return ["publish", *(item for pair in values.items() for item in pair)]


def _api_root(release_id: str) -> str:
    return contract.api_path(
        contract.release_path(principal=PRINCIPAL, target="dev", release_id=release_id)
    )


def _publish(
    client: FakeWorkspaceClient, stage_dir: Path, manifest_path: Path, rid: str, **extra: Any
) -> Any:
    return source.publish(
        client, release_id=rid, stage_dir=stage_dir, manifest_path=manifest_path,
        files_root=FILES_ROOT, expected_commit=COMMIT, principal=PRINCIPAL, target="dev", **extra,
    )


def test_publish_uploads_every_file_create_only_with_auto_and_verifies_it(tmp_path: Path) -> None:
    stage_dir, manifest_path, manifest = _stage(tmp_path)
    client = FakeWorkspaceClient()
    seen: list[tuple[object, object]] = []
    real_upload = client.workspace.upload

    def spy(path: str, content: Any, **kwargs: Any) -> None:
        seen.append((kwargs.get("format"), kwargs.get("overwrite")))
        real_upload(path, content, **kwargs)

    client.workspace.upload = spy  # type: ignore[method-assign]

    report = _publish(client, stage_dir, manifest_path, manifest.release_id)

    root = _api_root(manifest.release_id)
    assert report.release_path == f"/Workspace{root}"
    assert (report.uploaded, report.reused) == (len(FILES), 0)
    assert set(seen) == {(ImportFormat.AUTO, False)}
    tree = client.workspace
    assert {name.removeprefix(f"{root}/") for name in tree.below(root)
            if tree.nodes[name].kind is ObjectType.FILE} == set(FILES)
    assert all(tree.nodes[f"{root}/{path}"].data == data for path, data in FILES.items())
    assert tree.ops("delete") == []
    assert sorted(tree.ops("download")) == sorted(f"{root}/{path}" for path in FILES)


def test_a_second_publish_of_the_same_release_uploads_nothing(tmp_path: Path) -> None:
    stage_dir, manifest_path, manifest = _stage(tmp_path)
    client = FakeWorkspaceClient()
    _publish(client, stage_dir, manifest_path, manifest.release_id)

    again = _publish(client, stage_dir, manifest_path, manifest.release_id)

    assert (again.uploaded, again.reused) == (0, len(FILES))
    assert client.workspace.ops("delete") == []


def test_a_partial_folder_left_by_a_failed_run_is_completed(tmp_path: Path) -> None:
    stage_dir, manifest_path, manifest = _stage(tmp_path)
    client = FakeWorkspaceClient()
    root = _api_root(manifest.release_id)
    client.workspace.add_file(f"{root}/app.yaml", FILES["app.yaml"])

    report = _publish(client, stage_dir, manifest_path, manifest.release_id)

    assert (report.uploaded, report.reused) == (len(FILES) - 1, 1)


def test_an_existing_file_with_other_bytes_is_refused_and_left_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage_dir, manifest_path, manifest = _stage(tmp_path)
    client = FakeWorkspaceClient()
    root = _api_root(manifest.release_id)
    client.workspace.add_file(f"{root}/backend/main.py", b"APP = 2\n")

    assert source.main(_args(stage_dir, manifest_path, manifest.release_id), client=lambda: client) == 2

    assert "already exists with different bytes" in capsys.readouterr().err
    assert client.workspace.nodes[f"{root}/backend/main.py"].data == b"APP = 2\n"
    assert client.workspace.ops("delete") == []


def test_an_unexpected_remote_file_is_refused_and_never_deleted(tmp_path: Path) -> None:
    stage_dir, manifest_path, manifest = _stage(tmp_path)
    client = FakeWorkspaceClient()
    root = _api_root(manifest.release_id)
    client.workspace.add_file(f"{root}/backend/extra.py", b"X = 1\n")

    with pytest.raises(contract.ReleaseContractError, match="unexpected backend/extra.py"):
        _publish(client, stage_dir, manifest_path, manifest.release_id)
    assert client.workspace.exists(f"{root}/backend/extra.py")
    assert client.workspace.ops("delete") == []


def test_an_object_the_platform_imported_as_a_notebook_is_refused(tmp_path: Path) -> None:
    stage_dir, manifest_path, manifest = _stage(tmp_path)
    client = FakeWorkspaceClient()
    root = _api_root(manifest.release_id)
    target = f"{root}/backend/main.py"

    def as_notebook(path: str) -> None:
        if not client.workspace.exists(f"{root}/backend/main"):
            client.workspace.add_file(f"{root}/backend/main", b"x", kind=ObjectType.NOTEBOOK)

    client.workspace.hooks["list"] = as_notebook

    with pytest.raises(contract.ReleaseContractError, match="NOTEBOOK, not a FILE"):
        _publish(client, stage_dir, manifest_path, manifest.release_id)
    assert client.workspace.exists(target)


def test_an_ambiguous_upload_that_landed_is_reconciled(tmp_path: Path) -> None:
    stage_dir, manifest_path, manifest = _stage(tmp_path)
    client = FakeWorkspaceClient()
    root = _api_root(manifest.release_id)
    client.workspace.fail_after[("upload", f"{root}/app.yaml")] = InternalError("timeout")

    report = _publish(client, stage_dir, manifest_path, manifest.release_id)

    assert report.uploaded == len(FILES)


def test_a_failed_upload_that_did_not_land_fails_the_publish(tmp_path: Path) -> None:
    stage_dir, manifest_path, manifest = _stage(tmp_path)
    client = FakeWorkspaceClient()
    root = _api_root(manifest.release_id)
    client.workspace.fail_before[("upload", f"{root}/app.yaml")] = InternalError("refused")

    with pytest.raises(InternalError):
        _publish(client, stage_dir, manifest_path, manifest.release_id)
    assert client.workspace.ops("delete") == []


def test_an_ambiguous_upload_over_other_bytes_fails_with_the_upload_error(tmp_path: Path) -> None:
    stage_dir, manifest_path, manifest = _stage(tmp_path)
    client = FakeWorkspaceClient()
    target = f"{_api_root(manifest.release_id)}/backend/main.py"
    client.workspace.add_file(target, b"APP = 2\n")  # same size, other bytes
    client.workspace.fail_before[("upload", target)] = InternalError("timeout")

    with pytest.raises(InternalError, match="timeout"):
        _publish(client, stage_dir, manifest_path, manifest.release_id)
    assert client.workspace.nodes[target].data == b"APP = 2\n"
    assert client.workspace.ops("delete") == []


def test_an_existing_object_that_cannot_be_read_back_is_never_reused(tmp_path: Path) -> None:
    stage_dir, manifest_path, manifest = _stage(tmp_path)
    client = FakeWorkspaceClient()
    target = f"{_api_root(manifest.release_id)}/backend/main.py"
    client.workspace.add_file(target, FILES["backend/main.py"])
    client.workspace.fail_before[("download", target)] = InternalError("unreadable")

    with pytest.raises(contract.ReleaseContractError, match="exists and cannot be read back"):
        _publish(client, stage_dir, manifest_path, manifest.release_id)


# -- rate limits (HTTP 429) ---------------------------------------------------


def _no_wait() -> tuple[retry.RateLimitRetry, list[float]]:
    """The default budget with a recording sleep (no test ever waits) and no jitter."""
    waits: list[float] = []
    return retry.RateLimitRetry(sleep=waits.append, jitter=lambda: 1.0), waits


def _throttled(retry_after: int | None = None) -> ResourceExhausted:
    # What SDK 0.103.0 raises for a 429 RESOURCE_EXHAUSTED (the 2026-10-29 error).
    return ResourceExhausted(
        "Too many requests", error_code="RESOURCE_EXHAUSTED", retry_after_secs=retry_after
    )


def test_a_rate_limited_upload_is_retried_create_only_and_lands_once(tmp_path: Path) -> None:
    stage_dir, manifest_path, manifest = _stage(tmp_path)
    client = FakeWorkspaceClient()
    target = f"{_api_root(manifest.release_id)}/app.yaml"
    client.workspace.fail_before[("upload", target)] = [_throttled(retry_after=7), _throttled()]
    overwrite: list[object] = []
    real_upload = client.workspace.upload

    def spy(path: str, content: Any, **kwargs: Any) -> None:
        if path == target:
            overwrite.append(kwargs.get("overwrite"))
        real_upload(path, content, **kwargs)

    client.workspace.upload = spy  # type: ignore[method-assign]
    policy, waits = _no_wait()

    report = _publish(client, stage_dir, manifest_path, manifest.release_id, retry=policy)

    assert (report.uploaded, report.reused) == (len(FILES), 0)
    assert overwrite == [False, False, False]  # two throttled tries, then the one that landed
    assert waits == [7.0, 4.0]  # the Retry-After beats the 2 s backoff; then 4 s
    assert client.workspace.nodes[target].data == FILES["app.yaml"]
    assert client.workspace.ops("download").count(target) == 1  # verify only: no read-back
    assert client.workspace.ops("delete") == []


def test_an_upload_rate_limited_on_every_attempt_is_refused_naming_file_and_budget(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage_dir, manifest_path, manifest = _stage(tmp_path)
    client = FakeWorkspaceClient()
    target = f"{_api_root(manifest.release_id)}/app.yaml"
    client.workspace.fail_before[("upload", target)] = _throttled(retry_after=1)
    policy, waits = _no_wait()

    code = source.main(_args(stage_dir, manifest_path, manifest.release_id),
                       client=lambda: client, retry=policy)

    assert code == 2
    err = capsys.readouterr().err
    assert f"refused: the upload of {target} was rate limited (HTTP 429) on all 6 attempts" in err
    assert "retry budget (at most 30 s per wait)" in err
    assert err.count(f"the upload of {target} was rate limited (HTTP 429); try ") == 5
    assert client.workspace.ops("upload").count(target) == 6
    assert len(waits) == 5 and max(waits) <= 30
    assert target not in client.workspace.ops("download")  # a spent budget is not reconciled
    assert not client.workspace.exists(target)
    assert client.workspace.ops("delete") == []


def test_a_rate_limited_upload_that_landed_meets_already_exists_and_compares_bytes(
    tmp_path: Path,
) -> None:
    stage_dir, manifest_path, manifest = _stage(tmp_path)
    client = FakeWorkspaceClient()
    target = f"{_api_root(manifest.release_id)}/app.yaml"
    client.workspace.fail_after[("upload", target)] = [_throttled()]
    policy, waits = _no_wait()

    report = _publish(client, stage_dir, manifest_path, manifest.release_id, retry=policy)

    assert (report.uploaded, report.reused) == (len(FILES) - 1, 1)
    assert client.workspace.ops("upload").count(target) == 2
    assert client.workspace.ops("download").count(target) == 2  # the byte compare, then verify
    assert waits == [2.0]
    assert client.workspace.nodes[target].data == FILES["app.yaml"]
    assert client.workspace.ops("delete") == []


def test_a_rate_limit_retry_never_takes_other_bytes_for_its_own(tmp_path: Path) -> None:
    stage_dir, manifest_path, manifest = _stage(tmp_path)
    client = FakeWorkspaceClient()
    target = f"{_api_root(manifest.release_id)}/backend/main.py"
    client.workspace.fail_before[("upload", target)] = [_throttled()]

    def someone_else_writes(path: str) -> None:  # between the 429 and the retry
        if path == target and not client.workspace.exists(target):
            client.workspace.add_file(target, b"APP = 2\n")

    client.workspace.hooks["upload"] = someone_else_writes
    policy, _ = _no_wait()

    with pytest.raises(contract.ReleaseContractError, match="already exists with different bytes"):
        _publish(client, stage_dir, manifest_path, manifest.release_id, retry=policy)
    assert client.workspace.nodes[target].data == b"APP = 2\n"
    assert client.workspace.ops("delete") == []


def test_verify_hashes_retries_a_rate_limited_download(tmp_path: Path) -> None:
    _, _, manifest = _stage(tmp_path)
    client = FakeWorkspaceClient()
    root = _api_root(manifest.release_id)
    for relpath, data in FILES.items():
        client.workspace.add_file(f"{root}/{relpath}", data)
    target = f"{root}/backend/main.py"
    client.workspace.fail_before[("download", target)] = [TooManyRequests("429"), _throttled()]
    policy, waits = _no_wait()

    source.verify_hashes(client, root, manifest.entries, workers=2, retry=policy)

    assert client.workspace.ops("download").count(target) == 3
    assert waits == [2.0, 4.0]


def test_the_exact_listing_retries_a_rate_limit_from_the_start(tmp_path: Path) -> None:
    _, _, manifest = _stage(tmp_path)
    client = FakeWorkspaceClient()
    root = _api_root(manifest.release_id)
    for relpath, data in FILES.items():
        client.workspace.add_file(f"{root}/{relpath}", data)
    client.workspace.fail_after[("list", root)] = [_throttled()]
    policy, waits = _no_wait()

    assert set(source.remote_files(client, root, retry=policy)) == set(FILES)
    assert client.workspace.ops("list") == [root, root]
    assert waits == [2.0]


@pytest.mark.parametrize(
    "error",
    [InternalError("timeout"), TemporarilyUnavailable("503", retry_after_secs=1),
     PermissionDenied("no")],
    ids=["500", "503-with-retry-after", "403"],
)
def test_an_upload_error_that_is_not_a_rate_limit_is_never_retried(
    tmp_path: Path, error: Exception
) -> None:
    stage_dir, manifest_path, manifest = _stage(tmp_path)
    client = FakeWorkspaceClient()
    target = f"{_api_root(manifest.release_id)}/app.yaml"
    client.workspace.fail_before[("upload", target)] = error
    policy, waits = _no_wait()

    with pytest.raises(type(error)):
        _publish(client, stage_dir, manifest_path, manifest.release_id, retry=policy)
    assert client.workspace.ops("upload").count(target) == 1
    assert client.workspace.ops("download").count(target) == 1  # today's ambiguous reconcile
    assert waits == []


@pytest.mark.parametrize("error", [TooManyRequests, ResourceExhausted, RequestLimitExceeded])
def test_every_sdk_429_class_is_a_rate_limit(error: type[Exception]) -> None:
    policy, waits = _no_wait()
    failures = [error("429")]

    def call() -> str:
        if failures:
            raise failures.pop()
        return "ok"

    assert policy.call("probe", call) == "ok"
    assert waits == [2.0]


@pytest.mark.parametrize(
    "error", [InternalError, TemporarilyUnavailable, PermissionDenied, ResourceAlreadyExists]
)
def test_no_other_sdk_class_is_a_rate_limit(error: type[Exception]) -> None:
    policy, waits = _no_wait()

    def call() -> str:
        raise error("not a 429")

    with pytest.raises(error):
        policy.call("probe", call)
    assert waits == []


@pytest.mark.parametrize(
    ("attempt", "jitter", "retry_after", "expected"),
    [(1, 0.0, None, 1.0), (1, 1.0, None, 2.0), (5, 1.0, None, 30.0), (9, 0.5, None, 22.5),
     (2, 1.0, 3, 4.0), (2, 1.0, 9, 9.0), (1, 0.0, 120, 30.0)],
)
def test_the_backoff_doubles_with_jitter_honours_retry_after_and_is_capped(
    attempt: int, jitter: float, retry_after: int | None, expected: float
) -> None:
    policy = retry.RateLimitRetry(sleep=lambda _: None, jitter=lambda: jitter)

    assert policy.delay(attempt, _throttled(retry_after)) == expected


def test_a_staged_file_changed_after_the_stage_check_is_never_uploaded(tmp_path: Path) -> None:
    stage_dir, manifest_path, manifest = _stage(tmp_path)
    plan = source.prepare_publish(
        release_id=manifest.release_id, stage_dir=stage_dir, manifest_path=manifest_path,
        files_root=FILES_ROOT, expected_commit=COMMIT, principal=PRINCIPAL, target="dev",
    )
    (stage_dir / "backend/main.py").write_bytes(b"APP = 9\n")  # same size, after the check
    client = FakeWorkspaceClient()

    with pytest.raises(contract.ReleaseContractError, match="backend/main.py changed after the stage check"):
        source.upload_release(client, plan)
    assert not client.workspace.exists(f"{_api_root(manifest.release_id)}/backend/main.py")


def test_a_listing_that_names_a_file_twice_is_refused(tmp_path: Path) -> None:
    stage_dir, manifest_path, manifest = _stage(tmp_path)
    client = FakeWorkspaceClient()
    _publish(client, stage_dir, manifest_path, manifest.release_id)
    root = _api_root(manifest.release_id)
    real_list = client.workspace.list

    def listed_twice(api: str, **kwargs: Any) -> Any:
        listing = list(real_list(api, **kwargs))
        return iter([*listing, listing[0]])

    client.workspace.list = listed_twice  # type: ignore[method-assign]

    with pytest.raises(contract.ReleaseContractError, match="twice"):
        source.remote_files(client, root)


@pytest.mark.parametrize("corruption", ["same_size_bytes", "size"])
def test_remote_bytes_that_differ_after_upload_are_refused(tmp_path: Path, corruption: str) -> None:
    stage_dir, manifest_path, manifest = _stage(tmp_path)
    client = FakeWorkspaceClient()
    target = f"{_api_root(manifest.release_id)}/backend/main.py"

    def corrupt(_path: str) -> None:
        data = b"APP = 9\n" if corruption == "same_size_bytes" else b"APP = 10\n"
        client.workspace.nodes[target].data = data

    client.workspace.hooks["list"] = corrupt

    with pytest.raises(contract.ReleaseContractError, match="backend/main.py"):
        _publish(client, stage_dir, manifest_path, manifest.release_id)


def test_the_sdk_default_format_would_make_notebooks_of_python_files() -> None:
    # Why publish passes ImportFormat.AUTO explicitly (the fake models SOURCE).
    client = FakeWorkspaceClient()
    client.workspace.mkdirs("/Users/x")
    client.workspace.upload("/Users/x/main.py", b"X = 1\n")

    assert client.workspace.nodes["/Users/x/main"].kind is ObjectType.NOTEBOOK


def _no_client() -> Any:
    raise AssertionError("a local refusal must not construct a workspace client")


def _refusal(tmp_path: Path, case: str) -> list[str]:
    files = dict(FILES)
    overrides: dict[str, str] = {}
    if case == "missing_app_yaml":
        files.pop("app.yaml")
    elif case == "missing_requirements":
        files.pop("requirements.txt")
    elif case == "missing_index":
        files.pop("frontend/dist/index.html")
    elif case == "empty_index":
        files["frontend/dist/index.html"] = b""
    elif case in ("package.json", "package-lock.json"):
        files[case] = b"{}"
    elif case == "notebook_header":
        files["backend/job.py"] = b"# Databricks notebook source\n"
    elif case == "ipynb":
        files["backend/analysis.ipynb"] = b"{}"
    elif case == "junk":
        files["backend/__pycache__/main.cpython-311.pyc"] = b"\x00"
    stage_dir, manifest_path, manifest = _stage(tmp_path, files)
    rid = manifest.release_id
    if case == "other_commit":
        overrides["--expected-commit"] = "f" * 40
    elif case == "other_release_id":
        overrides["--release-id"] = f"{COMMIT[:12]}-000000000000"
    elif case == "other_leaf":
        overrides["--files-root"] = contract.files_root(principal=PRINCIPAL, target="dev", root_leaf="dev-ci")
    elif case == "other_principal":
        overrides["--expected-principal"] = "other@example.com"
    elif case == "workers":
        overrides["--workers"] = "0"
    elif case == "extra_local_file":
        (stage_dir / "backend" / "extra.py").write_bytes(b"x\n")
    elif case == "changed_local_byte":
        (stage_dir / "backend" / "main.py").write_bytes(b"APP = 2\n")
    elif case == "hidden_local_file":
        (stage_dir / "backend" / ".env").write_bytes(b"X=1\n")
    elif case == "symlink":
        (stage_dir / "backend" / "link.py").symlink_to(stage_dir / "backend" / "main.py")
    return _args(stage_dir, manifest_path, rid, **overrides)


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("missing_app_yaml", "missing app.yaml"),
        ("missing_requirements", "missing requirements.txt"),
        ("missing_index", "missing frontend/dist/index.html"),
        ("empty_index", "empty frontend/dist/index.html"),
        ("package.json", "root Node manifests"),
        ("package-lock.json", "root Node manifests"),
        ("notebook_header", "notebooks of"),
        ("ipynb", "notebooks of"),
        ("junk", "junk"),
        ("other_commit", "another commit"),
        ("other_release_id", "manifest's release id"),
        ("other_leaf", "expected bundle files root"),
        ("other_principal", "expected bundle files root"),
        ("workers", "--workers"),
        ("extra_local_file", "does not match its manifest"),
        ("changed_local_byte", "does not match its manifest"),
        ("hidden_local_file", "not canonical"),
        ("symlink", "not a regular file"),
    ],
)
def test_every_local_refusal_exits_2_before_any_workspace_client_exists(
    tmp_path: Path, case: str, message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert source.main(_refusal(tmp_path, case), client=_no_client) == 2
    assert message in capsys.readouterr().err


@pytest.mark.parametrize(("limit", "message"), [("MAX_FILES", "file release budget"),
                                                 ("MAX_FILE_BYTES", "files over")])
def test_the_file_count_and_size_budgets_refuse_locally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: str, message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(source, limit, len(FILES) - 1 if limit == "MAX_FILES" else 10)
    stage_dir, manifest_path, manifest = _stage(tmp_path)

    assert source.main(_args(stage_dir, manifest_path, manifest.release_id), client=_no_client) == 2
    assert message in capsys.readouterr().err


def test_the_budgets_are_the_stage_budget_and_the_import_limit() -> None:
    assert source.MAX_FILES == 1_300
    assert source.MAX_FILE_BYTES == 10 * 1024 * 1024
    # 2026-10-29: 8 workers drew a 429 part way through a 1,177-file release.
    assert (source.DEFAULT_WORKERS, source.MAX_WORKERS) == (4, 16)
    budget = retry.RATE_LIMIT_RETRY
    assert (budget.attempts, budget.base_seconds, budget.cap_seconds) == (6, 2.0, 30.0)


# -- verify-deployment ---------------------------------------------------------

SNAPSHOT = "/Workspace/Users/app-sp/src/01f0deployment"


def _deployed(tmp_path: Path, *, files: dict[str, bytes] = FILES) -> tuple[FakeWorkspaceClient, Path, str]:
    _, manifest_path, manifest = _stage(tmp_path)
    client = FakeWorkspaceClient()
    for relpath, data in files.items():
        client.workspace.add_file(f"{contract.api_path(SNAPSHOT)}/{relpath}", data)
    release = contract.release_path(principal=PRINCIPAL, target="dev", release_id=manifest.release_id)
    active = deployment("dep-1", release, create_time="2026-10-28T10:00:00Z", snapshot=SNAPSHOT)
    client.apps.app.active_deployment = active
    client.apps.deployments = [active]
    return client, manifest_path, manifest.release_id


def _verify(client: FakeWorkspaceClient, manifest_path: Path, rid: str, *extra: str) -> int:
    args = ["verify-deployment", "--app-name", "mip-app", "--manifest", str(manifest_path),
            "--release-id", rid, *extra]
    return source.main(args, client=lambda: client)


def test_verify_deployment_proves_the_snapshot_holds_the_release_bytes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    client, manifest_path, rid = _deployed(tmp_path)

    assert _verify(client, manifest_path, rid) == 0
    assert _verify(client, manifest_path, rid, "--deployment-id", "dep-1") == 0
    assert f"holds exactly release {rid}" in capsys.readouterr().out
    assert client.workspace.ops("upload") == client.workspace.ops("delete") == []
    assert client.workspace.ops("mkdirs") == []


@pytest.mark.parametrize("case", ["byte", "extra", "not_release", "not_snapshot", "no_active"])
def test_verify_deployment_refuses_anything_but_the_release_bytes(
    tmp_path: Path, case: str, capsys: pytest.CaptureFixture[str]
) -> None:
    files = dict(FILES)
    if case == "byte":
        files["backend/main.py"] = b"APP = 2\n"
    elif case == "extra":
        files["backend/extra.py"] = b"x\n"
    client, manifest_path, rid = _deployed(tmp_path, files=files)
    active = client.apps.app.active_deployment
    if case == "not_release":
        active.source_code_path = FILES_ROOT
    elif case == "not_snapshot":
        active.deployment_artifacts.source_code_path = FILES_ROOT
    elif case == "no_active":
        client.apps.app.active_deployment = None

    assert _verify(client, manifest_path, rid) == 2
    assert capsys.readouterr().err.startswith("refused: ")
