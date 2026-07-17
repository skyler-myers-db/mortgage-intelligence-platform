from __future__ import annotations

import base64
import io
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from databricks.sdk.errors import ResourceAlreadyExists, ResourceDoesNotExist

from backend.services.ai_gateway_proof_attestation import derive_gateway_proof_verify_key
from tools.databricks import app_deployment_lease as lease

SIGNING_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")
NOW = datetime(2026, 7, 16, 12, tzinfo=UTC)


class _Files:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.set_permissions_calls = 0

    def mkdirs(self, _path: str) -> None:
        return None

    def get_status(self, _path: str) -> object:
        return SimpleNamespace(object_id="lease-root-id")

    def set_permissions(
        self,
        _object_type: str,
        _object_id: str,
        *,
        access_control_list: list[object],
    ) -> object:
        self.set_permissions_calls += 1
        self.access_control_list = [
            SimpleNamespace(
                user_name=item.user_name,
                group_name=None,
                all_permissions=[SimpleNamespace(permission_level="CAN_MANAGE")],
            )
            for item in access_control_list
        ]
        return SimpleNamespace(access_control_list=self.access_control_list)

    def get_permissions(self, _object_type: str, _object_id: str) -> object:
        return SimpleNamespace(access_control_list=self.access_control_list)

    def upload(self, path: str, content: io.BytesIO, *, overwrite: bool) -> None:
        if path in self.data and not overwrite:
            raise ResourceAlreadyExists("exists")
        self.data[path] = content.read()

    def download(self, path: str) -> io.BytesIO:
        if path not in self.data:
            raise ResourceDoesNotExist("missing")
        return io.BytesIO(self.data[path])

    def delete(self, path: str) -> None:
        if path not in self.data:
            raise ResourceDoesNotExist("missing")
        del self.data[path]


def _workspace(holder: str = "deployer@example.com") -> object:
    return SimpleNamespace(
        workspace=_Files(),
        current_user=SimpleNamespace(me=lambda: SimpleNamespace(user_name=holder)),
    )


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv(
        "MIP_AI_GATEWAY_PROOF_VERIFY_KEY",
        derive_gateway_proof_verify_key(SIGNING_KEY),
    )


def test_workspace_lease_is_exclusive_and_owner_releasable() -> None:
    workspace = _workspace()
    lease_id = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)

    with pytest.raises(RuntimeError, match="already held by deployer@example.com"):
        lease.acquire(workspace, app_name="mip-app", source_git_sha="b" * 40, now=NOW)

    lease.release(workspace, app_name="mip-app", lease_id=lease_id)
    assert workspace.workspace.data == {}


def test_release_rejects_silent_delete_noop() -> None:
    workspace = _workspace()
    lease_id = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)
    workspace.workspace.delete = lambda _path: None

    with pytest.raises(RuntimeError, match="remained after exact deletion"):
        lease.release(workspace, app_name="mip-app", lease_id=lease_id)

    assert lease._path("mip-app") in workspace.workspace.data


def test_release_accepts_delete_that_commits_then_times_out() -> None:
    workspace = _workspace()
    lease_id = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)
    real_delete = workspace.workspace.delete
    calls = 0

    def commit_then_timeout(path: str) -> None:
        nonlocal calls
        calls += 1
        real_delete(path)
        raise TimeoutError("injected timeout after delete commit")

    workspace.workspace.delete = commit_then_timeout

    lease.release(workspace, app_name="mip-app", lease_id=lease_id)

    assert calls == 1
    assert workspace.workspace.data == {}


def test_release_refuses_retry_when_same_exact_record_remains() -> None:
    workspace = _workspace()
    lease_id = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)
    calls = 0

    def timeout_before_commit(_path: str) -> None:
        nonlocal calls
        calls += 1
        raise TimeoutError("injected timeout before delete commit")

    workspace.workspace.delete = timeout_before_commit

    with pytest.raises(RuntimeError, match="remained after ambiguous exact deletion"):
        lease.release(workspace, app_name="mip-app", lease_id=lease_id)

    assert calls == 1
    assert lease._path("mip-app") in workspace.workspace.data


def test_release_never_retries_after_record_changes_during_ambiguous_delete() -> None:
    workspace = _workspace()
    lease_id = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)
    path = lease._path("mip-app")
    calls = 0

    def replace_then_timeout(_path: str) -> None:
        nonlocal calls
        calls += 1
        current = json.loads(workspace.workspace.data[path])
        replacement = lease._sign(
            {
                key: value
                for key, value in current.items()
                if key not in {"attestation_verify_key", "attestation_signature"}
            }
            | {"lease_id": "replacement-lease-id"}
        )
        workspace.workspace.data[path] = json.dumps(replacement, sort_keys=True).encode()
        raise TimeoutError("injected timeout with changed record")

    workspace.workspace.delete = replace_then_timeout

    with pytest.raises(RuntimeError, match="changed during ambiguous exact deletion"):
        lease.release(workspace, app_name="mip-app", lease_id=lease_id)

    assert calls == 1
    assert json.loads(workspace.workspace.data[path])["lease_id"] == "replacement-lease-id"


def test_losing_different_holder_does_not_mutate_active_holder_acl() -> None:
    workspace = _workspace("first@example.com")
    lease_id = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)
    acl_before = list(workspace.workspace.access_control_list)
    calls_before = workspace.workspace.set_permissions_calls
    workspace.current_user.me = lambda: SimpleNamespace(user_name="loser@example.com")

    with pytest.raises(RuntimeError, match="already held by first@example.com"):
        lease.acquire(workspace, app_name="mip-app", source_git_sha="b" * 40, now=NOW)

    assert workspace.workspace.set_permissions_calls == calls_before
    assert workspace.workspace.access_control_list == acl_before
    workspace.current_user.me = lambda: SimpleNamespace(user_name="first@example.com")
    assert (
        lease.assert_held(
            workspace,
            app_name="mip-app",
            lease_id=lease_id,
            source_git_sha="a" * 40,
            now=NOW,
        )["holder"]
        == "first@example.com"
    )


def test_winner_postflight_failure_removes_only_its_exact_record_for_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    original = lease._ensure_protected_root
    attempts = 0

    def fail_once(client: object, *, holder: str) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("injected ACL postflight failure")
        original(client, holder=holder)

    monkeypatch.setattr(lease, "_ensure_protected_root", fail_once)

    with pytest.raises(RuntimeError, match="injected ACL postflight failure"):
        lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)

    assert workspace.workspace.data == {}
    lease_id = lease.acquire(
        workspace,
        app_name="mip-app",
        source_git_sha="a" * 40,
        now=NOW,
    )
    assert lease_id


def test_post_commit_upload_timeout_removes_only_the_exact_candidate() -> None:
    workspace = _workspace()
    original_upload = workspace.workspace.upload

    def commit_then_timeout(path: str, content: io.BytesIO, *, overwrite: bool) -> None:
        original_upload(path, content, overwrite=overwrite)
        raise TimeoutError("injected timeout after commit")

    workspace.workspace.upload = commit_then_timeout

    with pytest.raises(TimeoutError, match="timeout after commit"):
        lease.acquire(
            workspace,
            app_name="mip-app",
            source_git_sha="a" * 40,
            now=NOW,
        )

    assert workspace.workspace.data == {}


def test_post_commit_upload_timeout_reports_exact_cleanup_failure() -> None:
    workspace = _workspace()
    original_upload = workspace.workspace.upload

    def commit_then_timeout(path: str, content: io.BytesIO, *, overwrite: bool) -> None:
        original_upload(path, content, overwrite=overwrite)
        raise TimeoutError("injected timeout after commit")

    def fail_delete(_path: str) -> None:
        raise OSError("injected exact cleanup failure")

    workspace.workspace.upload = commit_then_timeout
    workspace.workspace.delete = fail_delete

    with pytest.raises(
        RuntimeError,
        match="upload failed after commit and exact compensation did not complete",
    ) as exc_info:
        lease.acquire(
            workspace,
            app_name="mip-app",
            source_git_sha="a" * 40,
            now=NOW,
        )

    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert "remained after ambiguous exact deletion" in str(exc_info.value.__cause__)
    assert isinstance(exc_info.value.__cause__.__cause__, OSError)
    assert "exact cleanup failure" in str(exc_info.value.__cause__.__cause__)
    assert lease._path("mip-app") in workspace.workspace.data


def test_ambiguous_upload_never_deletes_a_different_persisted_record() -> None:
    workspace = _workspace()

    def replace_then_timeout(path: str, content: io.BytesIO, *, overwrite: bool) -> None:
        assert overwrite is False
        candidate = json.loads(content.read())
        replacement = lease._sign(
            {
                key: value
                for key, value in candidate.items()
                if key not in {"attestation_verify_key", "attestation_signature"}
            }
            | {"lease_id": "replacement-lease-id"}
        )
        workspace.workspace.data[path] = json.dumps(replacement, sort_keys=True).encode()
        raise TimeoutError("injected timeout with replacement")

    workspace.workspace.upload = replace_then_timeout

    with pytest.raises(RuntimeError, match="different record present; refusing compensation"):
        lease.acquire(
            workspace,
            app_name="mip-app",
            source_git_sha="a" * 40,
            now=NOW,
        )

    persisted = json.loads(workspace.workspace.data[lease._path("mip-app")])
    assert persisted["lease_id"] == "replacement-lease-id"


def test_expired_signed_lease_cannot_be_replaced_automatically() -> None:
    workspace = _workspace()
    first = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)

    with pytest.raises(RuntimeError, match="expired but never auto-replaced"):
        lease.acquire(
            workspace,
            app_name="mip-app",
            source_git_sha="b" * 40,
            now=NOW + lease.LEASE_TTL + timedelta(seconds=1),
        )

    assert json.loads(workspace.workspace.data[lease._path("mip-app")])["lease_id"] == first


def test_lease_renewal_extends_exact_owner_fence() -> None:
    workspace = _workspace()
    lease_id = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)

    lease.renew(
        workspace,
        app_name="mip-app",
        lease_id=lease_id,
        source_git_sha="a" * 40,
        now=NOW + timedelta(hours=1),
    )

    record = lease.assert_held(
        workspace,
        app_name="mip-app",
        lease_id=lease_id,
        source_git_sha="a" * 40,
        now=NOW + timedelta(hours=4, minutes=30),
    )
    assert record["expires_at"] == (NOW + timedelta(hours=5)).isoformat()


def test_renewal_rejects_exact_record_replacement_between_read_and_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    lease_id = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)
    path = lease._path("mip-app")
    original_sign = lease._sign

    def replace_during_refresh(record: dict[str, str | int]) -> dict[str, str | int]:
        refreshed = original_sign(record)
        persisted = json.loads(workspace.workspace.data[path])
        replacement = original_sign(
            {
                key: value
                for key, value in persisted.items()
                if key not in {"attestation_verify_key", "attestation_signature"}
            }
            | {"expires_at": (NOW + timedelta(hours=2)).isoformat()}
        )
        workspace.workspace.data[path] = json.dumps(replacement, sort_keys=True).encode()
        return refreshed

    monkeypatch.setattr(lease, "_sign", replace_during_refresh)

    with pytest.raises(RuntimeError, match="changed immediately before renewal"):
        lease.renew(
            workspace,
            app_name="mip-app",
            lease_id=lease_id,
            source_git_sha="a" * 40,
            now=NOW + timedelta(hours=1),
        )

    assert json.loads(workspace.workspace.data[path])["expires_at"] == (
        NOW + timedelta(hours=2)
    ).isoformat()


def test_renewal_rejects_wrong_source_or_lease() -> None:
    workspace = _workspace()
    lease_id = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)

    with pytest.raises(RuntimeError, match="ownership or source changed"):
        lease.renew(
            workspace,
            app_name="mip-app",
            lease_id=lease_id,
            source_git_sha="b" * 40,
            now=NOW + timedelta(minutes=1),
        )


def test_lease_fence_rejects_storage_acl_drift() -> None:
    workspace = _workspace()
    lease_id = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)
    workspace.workspace.access_control_list.append(
        SimpleNamespace(
            user_name="other@example.com",
            group_name=None,
            all_permissions=[SimpleNamespace(permission_level="CAN_MANAGE")],
        )
    )

    with pytest.raises(RuntimeError, match="unexpected manager"):
        lease.assert_held(
            workspace,
            app_name="mip-app",
            lease_id=lease_id,
            source_git_sha="a" * 40,
            now=NOW + timedelta(minutes=1),
        )


def test_tampered_lease_cannot_be_replaced_or_released() -> None:
    workspace = _workspace()
    lease_id = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)
    path = lease._path("mip-app")
    payload = json.loads(workspace.workspace.data[path])
    payload["holder"] = "attacker@example.com"
    workspace.workspace.data[path] = json.dumps(payload).encode()

    with pytest.raises(RuntimeError, match="signature is invalid"):
        lease.acquire(
            workspace,
            app_name="mip-app",
            source_git_sha="b" * 40,
            now=NOW + lease.LEASE_TTL + timedelta(seconds=1),
        )
    with pytest.raises(RuntimeError, match="signature is invalid"):
        lease.release(workspace, app_name="mip-app", lease_id=lease_id)


def test_heartbeat_exits_before_renewal_when_original_parent_dies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    renewed: list[bool] = []
    killed: list[tuple[int, int]] = []
    observed_parents = iter((4242, 1))
    monkeypatch.setattr(lease.os, "getppid", lambda: next(observed_parents))
    monkeypatch.setattr(lease.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(lease, "renew", lambda *_args, **_kwargs: renewed.append(True))
    monkeypatch.setattr(lease.os, "kill", lambda pid, sig: killed.append((pid, sig)))

    lease._heartbeat(
        workspace,
        app_name="mip-app",
        lease_id="lease-id",
        source_git_sha="a" * 40,
        parent_pid=4242,
    )

    assert renewed == []
    assert killed == []


def test_acquire_cli_releases_exact_lease_when_environment_handoff_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    released: list[str] = []
    real_release = lease.release
    monkeypatch.setattr(lease, "WorkspaceClient", lambda: workspace)

    def fail_write(*_args: object, **_kwargs: object) -> int:
        raise OSError("injected environment write failure")

    def release_exact(
        client: object,
        *,
        app_name: str,
        lease_id: str,
    ) -> None:
        persisted = json.loads(workspace.workspace.data[lease._path(app_name)])
        assert persisted["lease_id"] == lease_id
        released.append(lease_id)
        real_release(client, app_name=app_name, lease_id=lease_id)

    monkeypatch.setattr(lease.Path, "write_text", fail_write)
    monkeypatch.setattr(lease, "release", release_exact)

    with pytest.raises(OSError, match="environment write failure"):
        lease.main(
            [
                "acquire",
                "--app-name",
                "mip-app",
                "--source-git-sha",
                "a" * 40,
                "--out-env",
                "/tmp/mip-lease.env",
            ]
        )

    assert len(released) == 1
    assert workspace.workspace.data == {}


def test_acquire_cli_reports_environment_handoff_compensation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    monkeypatch.setattr(lease, "WorkspaceClient", lambda: workspace)

    def fail_write(*_args: object, **_kwargs: object) -> int:
        raise OSError("injected environment write failure")

    def fail_release(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected exact release failure")

    monkeypatch.setattr(lease.Path, "write_text", fail_write)
    monkeypatch.setattr(lease, "release", fail_release)

    with pytest.raises(
        RuntimeError,
        match="environment handoff failed and exact compensation did not complete",
    ) as exc_info:
        lease.main(
            [
                "acquire",
                "--app-name",
                "mip-app",
                "--source-git-sha",
                "a" * 40,
                "--out-env",
                "/tmp/mip-lease.env",
            ]
        )

    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert "exact release failure" in str(exc_info.value.__cause__)
    assert lease._path("mip-app") in workspace.workspace.data
