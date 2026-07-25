from __future__ import annotations

import base64
import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from databricks.sdk.errors import ResourceAlreadyExists, ResourceDoesNotExist
from databricks.sdk.service.workspace import ImportFormat

from backend.services.ai_gateway_proof_attestation import derive_gateway_proof_verify_key
from tools.databricks import app_deployment_lease as lease
from tools.databricks import app_deployment_lease_cli as lease_cli
from tools.databricks import app_first_install_journal as first_install
from tools.databricks import app_first_install_recovery as first_install_recovery
from tools.databricks.app_first_install_audit import AppCreateAuditProof

SIGNING_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")
NOW = datetime(2026, 7, 16, 12, tzinfo=UTC)
WRITER_ID = "agent-runtime-application-id"


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
                user_name=getattr(item, "user_name", None),
                service_principal_name=getattr(item, "service_principal_name", None),
                group_name=None,
                all_permissions=[
                    SimpleNamespace(permission_level=str(item.permission_level).split(".")[-1])
                ],
            )
            for item in access_control_list
        ]
        return SimpleNamespace(access_control_list=self.access_control_list)

    def get_permissions(self, _object_type: str, _object_id: str) -> object:
        return SimpleNamespace(access_control_list=self.access_control_list)

    def upload(
        self,
        path: str,
        content: io.BytesIO,
        *,
        format: ImportFormat,
        overwrite: bool,
    ) -> None:
        assert format is ImportFormat.AUTO
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


class _Apps:
    def __init__(self) -> None:
        self.app: object | None = None
        self.delete_calls = 0

    def get(self, _app_name: str) -> object:
        if self.app is None:
            raise ResourceDoesNotExist("missing")
        return self.app

    def delete(self, _app_name: str) -> object:
        if self.app is None:
            raise ResourceDoesNotExist("missing")
        deleted = self.app
        self.app = None
        self.delete_calls += 1
        return deleted


def _workspace(holder: str = "deployer@example.com") -> object:
    return SimpleNamespace(
        config=SimpleNamespace(workspace_id="123456789"),
        workspace=_Files(),
        apps=_Apps(),
        current_user=SimpleNamespace(me=lambda: SimpleNamespace(user_name=holder)),
    )


def _first_install_payload() -> dict[str, object]:
    return {
        "name": "mip-app",
        "description": "Mortgage Intelligence Platform",
        "resources": [
            {
                "name": "sql_warehouse",
                "sql_warehouse": {"id": "warehouse-id", "permission": "CAN_USE"},
            }
        ],
    }


def _journaled_app(marked: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        id="app-object-id",
        name=marked["name"],
        description=marked["description"],
        creator="deployer@example.com",
        resources=marked["resources"],
        compute_status=SimpleNamespace(state="STOPPED"),
        active_deployment=None,
        pending_deployment=None,
        service_principal_client_id="app-client-id",
        service_principal_id="app-scim-id",
    )


def _claim_created_app(
    workspace: object,
    *,
    lease_id: str,
    source_git_sha: str = "a" * 40,
) -> None:
    first_install.claim_created_app(
        workspace,
        app_name="mip-app",
        lease_id=lease_id,
        source_git_sha=source_git_sha,
        created_app={
            "id": "app-object-id",
            "name": "mip-app",
            "service_principal_client_id": "app-client-id",
            "service_principal_id": "app-scim-id",
        },
    )


def _missing_rollback(*_args: object, **_kwargs: object) -> object:
    raise RuntimeError("no server-owned last-good App rollback contract exists for mip-app")


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv(
        "MIP_AI_GATEWAY_PROOF_VERIFY_KEY",
        derive_gateway_proof_verify_key(SIGNING_KEY),
    )
    monkeypatch.setenv("DATABRICKS_AGENT_RUNTIME_CLIENT_ID", WRITER_ID)


def test_workspace_lease_is_exclusive_and_owner_releasable() -> None:
    workspace = _workspace()
    lease_id = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)

    with pytest.raises(RuntimeError, match="already held by deployer@example.com"):
        lease.acquire(workspace, app_name="mip-app", source_git_sha="b" * 40, now=NOW)

    lease.release(workspace, app_name="mip-app", lease_id=lease_id)
    released = lease._download(workspace, app_name="mip-app")
    assert released is not None
    assert released["state"] == "released"
    assert released["lease_id"] == lease_id


def test_later_lease_recovers_exact_journaled_app_after_creator_process_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    first_lease = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40)
    marked = first_install.prepare(
        workspace,
        app_name="mip-app",
        lease_id=first_lease,
        source_git_sha="a" * 40,
        payload=_first_install_payload(),
    )
    workspace.apps.app = _journaled_app(marked)
    _claim_created_app(workspace, lease_id=first_lease)
    monkeypatch.setattr(first_install, "_load_record", _missing_rollback)

    lease.release(workspace, app_name="mip-app", lease_id=first_lease)
    retry_lease = lease.acquire(workspace, app_name="mip-app", source_git_sha="b" * 40)

    assert first_install.status(
        workspace,
        app_name="mip-app",
        lease_id=retry_lease,
        source_git_sha="b" * 40,
        rollback_scope="mip-app-rollback",
        expected_lakebase_instance="mip-lakebase",
    ) == "recover"
    first_install.delete_recoverable(
        workspace,
        app_name="mip-app",
        lease_id=retry_lease,
        source_git_sha="b" * 40,
        rollback_scope="mip-app-rollback",
        expected_lakebase_instance="mip-lakebase",
    )

    assert workspace.apps.app is None
    assert workspace.apps.delete_calls == 1
    assert first_install._path("mip-app") not in workspace.workspace.data
    assert lease._path("mip-app") in workspace.workspace.data


def test_process_loss_before_app_identity_claim_recovers_from_server_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    creation_lease = lease.acquire(
        workspace, app_name="mip-app", source_git_sha="a" * 40
    )
    marked = first_install.prepare(
        workspace,
        app_name="mip-app",
        lease_id=creation_lease,
        source_git_sha="a" * 40,
        payload=_first_install_payload(),
    )
    workspace.apps.app = _journaled_app(marked)
    monkeypatch.setattr(first_install, "_load_record", _missing_rollback)
    lease.release(workspace, app_name="mip-app", lease_id=creation_lease)
    retry_lease = lease.acquire(workspace, app_name="mip-app", source_git_sha="b" * 40)

    assert first_install.status(
        workspace,
        app_name="mip-app",
        lease_id=retry_lease,
        source_git_sha="b" * 40,
        rollback_scope="mip-app-rollback",
        expected_lakebase_instance="mip-lakebase",
    ) == "unclaimed"
    monkeypatch.setattr(
        first_install_recovery,
        "find_app_create_proof",
        lambda *_args, **_kwargs: AppCreateAuditProof(
            event_time="2026-07-16T12:01:00+00:00",
            event_id="audit-event-id",
            request_id="audit-request-id",
            app_id="app-object-id",
        ),
    )
    first_install_recovery.recover_unclaimed_from_audit(
        workspace,
        app_name="mip-app",
        lease_id=retry_lease,
        source_git_sha="b" * 40,
        warehouse_id="warehouse-id",
    )
    record = first_install._download(workspace, app_name="mip-app")
    assert record is not None
    assert record["claim_proof_kind"] == "system_access_audit"
    assert record["create_audit_event_id"] == "audit-event-id"
    assert record["create_audit_request_id"] == "audit-request-id"
    assert first_install.status(
        workspace,
        app_name="mip-app",
        lease_id=retry_lease,
        source_git_sha="b" * 40,
        rollback_scope="mip-app-rollback",
        expected_lakebase_instance="mip-lakebase",
    ) == "recover"
    first_install.delete_recoverable(
        workspace,
        app_name="mip-app",
        lease_id=retry_lease,
        source_git_sha="b" * 40,
        rollback_scope="mip-app-rollback",
        expected_lakebase_instance="mip-lakebase",
    )

    assert workspace.apps.app is None
    assert workspace.apps.delete_calls == 1
    assert first_install._path("mip-app") not in workspace.workspace.data


def test_present_unclaimed_app_polls_until_audit_evidence_arrives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    creation_lease = lease.acquire(
        workspace, app_name="mip-app", source_git_sha="a" * 40
    )
    marked = first_install.prepare(
        workspace,
        app_name="mip-app",
        lease_id=creation_lease,
        source_git_sha="a" * 40,
        payload=_first_install_payload(),
    )
    workspace.apps.app = _journaled_app(marked)
    lease.release(workspace, app_name="mip-app", lease_id=creation_lease)
    retry_lease = lease.acquire(
        workspace, app_name="mip-app", source_git_sha="b" * 40
    )
    record = first_install._download(workspace, app_name="mip-app")
    assert record is not None
    visible = False

    def delayed_proof(*_args: object, **_kwargs: object) -> AppCreateAuditProof:
        if not visible:
            raise RuntimeError("first-install create audit proof is not available yet")
        return AppCreateAuditProof(
            event_time=record["create_authorized_until"],
            event_id="delayed-event-id",
            request_id="delayed-request-id",
            app_id="app-object-id",
        )

    def reveal(_seconds: float) -> None:
        nonlocal visible
        visible = True

    monkeypatch.setattr(first_install_recovery, "find_app_create_proof", delayed_proof)
    monkeypatch.setattr(first_install_recovery, "_sleep", reveal)
    first_install_recovery.recover_unclaimed_from_audit(
        workspace,
        app_name="mip-app",
        lease_id=retry_lease,
        source_git_sha="b" * 40,
        warehouse_id="warehouse-id",
    )

    claimed = first_install._download(workspace, app_name="mip-app")
    assert claimed is not None
    assert claimed["claim_proof_kind"] == "system_access_audit"


def test_expired_lease_takeover_recovers_unclaimed_app_from_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    prepared_at = datetime.now(UTC) - lease.LEASE_TTL - timedelta(minutes=1)
    creation_lease = lease.acquire(
        workspace,
        app_name="mip-app",
        source_git_sha="a" * 40,
        now=prepared_at,
    )
    marked = first_install.prepare(
        workspace,
        app_name="mip-app",
        lease_id=creation_lease,
        source_git_sha="a" * 40,
        payload=_first_install_payload(),
        now=prepared_at,
    )
    workspace.apps.app = _journaled_app(marked)
    retry_lease = lease.acquire(
        workspace,
        app_name="mip-app",
        source_git_sha="b" * 40,
        expired_recovery_lease_id=lease.lease_support.recovery_root(
            lease, workspace, app_name="mip-app"
        ),
    )
    monkeypatch.setattr(
        first_install_recovery,
        "find_app_create_proof",
        lambda *_args, **_kwargs: AppCreateAuditProof(
            event_time=(prepared_at + timedelta(minutes=1)).isoformat(),
            event_id="audit-event-id",
            request_id="audit-request-id",
            app_id="app-object-id",
        ),
    )

    first_install_recovery.recover_unclaimed_from_audit(
        workspace,
        app_name="mip-app",
        lease_id=retry_lease,
        source_git_sha="b" * 40,
        warehouse_id="warehouse-id",
    )

    record = first_install._download(workspace, app_name="mip-app")
    assert record is not None
    assert record["claim_proof_kind"] == "system_access_audit"


def test_stale_lease_cannot_commit_audit_identity_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    creation_lease = lease.acquire(
        workspace, app_name="mip-app", source_git_sha="a" * 40
    )
    marked = first_install.prepare(
        workspace,
        app_name="mip-app",
        lease_id=creation_lease,
        source_git_sha="a" * 40,
        payload=_first_install_payload(),
    )
    workspace.apps.app = _journaled_app(marked)
    lease.release(workspace, app_name="mip-app", lease_id=creation_lease)
    retry_lease = lease.acquire(
        workspace, app_name="mip-app", source_git_sha="b" * 40
    )

    def lose_lease_during_audit(*_args: object, **_kwargs: object) -> AppCreateAuditProof:
        lease.release(workspace, app_name="mip-app", lease_id=retry_lease)
        lease.acquire(workspace, app_name="mip-app", source_git_sha="c" * 40)
        return AppCreateAuditProof(
            event_time=datetime.now(UTC).isoformat(),
            event_id="audit-event-id",
            request_id="audit-request-id",
            app_id="app-object-id",
        )

    monkeypatch.setattr(
        first_install_recovery, "find_app_create_proof", lose_lease_during_audit
    )
    with pytest.raises(RuntimeError, match="lease (?:was released|ownership or source changed)"):
        first_install_recovery.recover_unclaimed_from_audit(
            workspace,
            app_name="mip-app",
            lease_id=retry_lease,
            source_git_sha="b" * 40,
            warehouse_id="warehouse-id",
        )

    record = first_install._download(workspace, app_name="mip-app")
    assert record is not None
    assert record["claim_proof_kind"] == ""
    assert workspace.apps.app is not None


def test_stale_lease_cannot_delete_claimed_first_install_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    deployment_lease = lease.acquire(
        workspace, app_name="mip-app", source_git_sha="a" * 40
    )
    marked = first_install.prepare(
        workspace,
        app_name="mip-app",
        lease_id=deployment_lease,
        source_git_sha="a" * 40,
        payload=_first_install_payload(),
    )
    workspace.apps.app = _journaled_app(marked)
    _claim_created_app(workspace, lease_id=deployment_lease)
    monkeypatch.setattr(first_install, "_load_record", _missing_rollback)
    original_assert_owned = first_install._assert_owned_app
    calls = 0

    def lose_lease_before_delete(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        app = original_assert_owned(*args, **kwargs)
        if calls == 2:
            lease.release(workspace, app_name="mip-app", lease_id=deployment_lease)
            lease.acquire(workspace, app_name="mip-app", source_git_sha="b" * 40)
        return app

    monkeypatch.setattr(first_install, "_assert_owned_app", lose_lease_before_delete)
    with pytest.raises(RuntimeError, match="lease (?:was released|ownership or source changed)"):
        first_install.delete_recoverable(
            workspace,
            app_name="mip-app",
            lease_id=deployment_lease,
            source_git_sha="a" * 40,
            rollback_scope="mip-app-rollback",
            expected_lakebase_instance="mip-lakebase",
        )

    assert workspace.apps.app is not None
    assert first_install._download(workspace, app_name="mip-app") is not None


def test_same_metadata_replacement_before_identity_claim_is_never_claimed_or_deleted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    creation_lease = lease.acquire(
        workspace, app_name="mip-app", source_git_sha="a" * 40
    )
    marked = first_install.prepare(
        workspace,
        app_name="mip-app",
        lease_id=creation_lease,
        source_git_sha="a" * 40,
        payload=_first_install_payload(),
    )
    replacement = _journaled_app(marked)
    replacement.id = "replacement-app-object-id"
    replacement.service_principal_client_id = "replacement-client-id"
    replacement.service_principal_id = "replacement-scim-id"
    workspace.apps.app = replacement

    with pytest.raises(RuntimeError, match="differs from the create response"):
        _claim_created_app(workspace, lease_id=creation_lease)
    monkeypatch.setattr(
        first_install_recovery,
        "find_app_create_proof",
        lambda *_args, **_kwargs: AppCreateAuditProof(
            event_time="2026-07-16T12:01:00+00:00",
            event_id="audit-event-id",
            request_id="audit-request-id",
            app_id="app-object-id",
        ),
    )
    with pytest.raises(RuntimeError, match="does not match its audited"):
        first_install_recovery.recover_unclaimed_from_audit(
            workspace,
            app_name="mip-app",
            lease_id=creation_lease,
            source_git_sha="a" * 40,
            warehouse_id="warehouse-id",
        )
    monkeypatch.setattr(first_install, "_load_record", _missing_rollback)
    assert first_install.status(
        workspace,
        app_name="mip-app",
        lease_id=creation_lease,
        source_git_sha="a" * 40,
        rollback_scope="mip-app-rollback",
        expected_lakebase_instance="mip-lakebase",
    ) == "unclaimed"
    with pytest.raises(RuntimeError, match="not eligible for unsigned recovery"):
        first_install.delete_recoverable(
            workspace,
            app_name="mip-app",
            lease_id=creation_lease,
            source_git_sha="a" * 40,
            rollback_scope="mip-app-rollback",
            expected_lakebase_instance="mip-lakebase",
        )

    assert workspace.apps.app is replacement
    assert workspace.apps.delete_calls == 0


def test_identity_claim_accepts_workspace_upload_that_commits_then_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    creation_lease = lease.acquire(
        workspace, app_name="mip-app", source_git_sha="a" * 40
    )
    marked = first_install.prepare(
        workspace,
        app_name="mip-app",
        lease_id=creation_lease,
        source_git_sha="a" * 40,
        payload=_first_install_payload(),
    )
    workspace.apps.app = _journaled_app(marked)
    real_upload = workspace.workspace.upload

    def commit_then_timeout(*args: object, **kwargs: object) -> None:
        real_upload(*args, **kwargs)
        raise TimeoutError("injected timeout after identity claim commit")

    workspace.workspace.upload = commit_then_timeout
    _claim_created_app(workspace, lease_id=creation_lease)
    monkeypatch.setattr(first_install, "_load_record", _missing_rollback)

    assert first_install.status(
        workspace,
        app_name="mip-app",
        lease_id=creation_lease,
        source_git_sha="a" * 40,
        rollback_scope="mip-app-rollback",
        expected_lakebase_instance="mip-lakebase",
    ) == "recover"


def test_identity_claim_timeout_before_commit_remains_unclaimed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    creation_lease = lease.acquire(
        workspace, app_name="mip-app", source_git_sha="a" * 40
    )
    marked = first_install.prepare(
        workspace,
        app_name="mip-app",
        lease_id=creation_lease,
        source_git_sha="a" * 40,
        payload=_first_install_payload(),
    )
    workspace.apps.app = _journaled_app(marked)

    def timeout_before_commit(*_args: object, **_kwargs: object) -> None:
        raise TimeoutError("injected timeout before identity claim commit")

    workspace.workspace.upload = timeout_before_commit
    with pytest.raises(RuntimeError, match="identity claim did not commit"):
        _claim_created_app(workspace, lease_id=creation_lease)
    monkeypatch.setattr(first_install, "_load_record", _missing_rollback)

    assert first_install.status(
        workspace,
        app_name="mip-app",
        lease_id=creation_lease,
        source_git_sha="a" * 40,
        rollback_scope="mip-app-rollback",
        expected_lakebase_instance="mip-lakebase",
    ) == "unclaimed"


def test_recovery_accepts_app_delete_that_commits_then_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    deployment_lease = lease.acquire(
        workspace, app_name="mip-app", source_git_sha="a" * 40
    )
    marked = first_install.prepare(
        workspace,
        app_name="mip-app",
        lease_id=deployment_lease,
        source_git_sha="a" * 40,
        payload=_first_install_payload(),
    )
    workspace.apps.app = _journaled_app(marked)
    _claim_created_app(workspace, lease_id=deployment_lease)
    monkeypatch.setattr(first_install, "_load_record", _missing_rollback)
    real_delete = workspace.apps.delete

    def commit_then_timeout(app_name: str) -> object:
        real_delete(app_name)
        raise TimeoutError("injected timeout after App delete commit")

    workspace.apps.delete = commit_then_timeout

    first_install.delete_recoverable(
        workspace,
        app_name="mip-app",
        lease_id=deployment_lease,
        source_git_sha="a" * 40,
        rollback_scope="mip-app-rollback",
        expected_lakebase_instance="mip-lakebase",
    )

    assert workspace.apps.app is None
    assert first_install._path("mip-app") not in workspace.workspace.data


def test_recovery_refuses_app_delete_timeout_before_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    deployment_lease = lease.acquire(
        workspace, app_name="mip-app", source_git_sha="a" * 40
    )
    marked = first_install.prepare(
        workspace,
        app_name="mip-app",
        lease_id=deployment_lease,
        source_git_sha="a" * 40,
        payload=_first_install_payload(),
    )
    workspace.apps.app = _journaled_app(marked)
    _claim_created_app(workspace, lease_id=deployment_lease)
    monkeypatch.setattr(first_install, "_load_record", _missing_rollback)

    def timeout_before_commit(_app_name: str) -> object:
        raise TimeoutError("injected timeout before App delete commit")

    workspace.apps.delete = timeout_before_commit

    with pytest.raises(RuntimeError, match="App deletion was ambiguous"):
        first_install.delete_recoverable(
            workspace,
            app_name="mip-app",
            lease_id=deployment_lease,
            source_git_sha="a" * 40,
            rollback_scope="mip-app-rollback",
            expected_lakebase_instance="mip-lakebase",
        )

    assert workspace.apps.app is not None
    assert first_install._path("mip-app") in workspace.workspace.data


def test_arbitrary_unsigned_app_is_never_deleted_without_matching_signed_journal() -> None:
    workspace = _workspace("retry@example.com")
    workspace.apps.app = SimpleNamespace(
        name="mip-app",
        description="untrusted legacy App",
        creator="unknown@example.com",
        resources=_first_install_payload()["resources"],
        compute_status=SimpleNamespace(state="STOPPED"),
        pending_deployment=None,
    )
    retry_lease = lease.acquire(workspace, app_name="mip-app", source_git_sha="b" * 40)

    with pytest.raises(RuntimeError, match="not eligible for unsigned recovery"):
        first_install.delete_recoverable(
            workspace,
            app_name="mip-app",
            lease_id=retry_lease,
            source_git_sha="b" * 40,
            rollback_scope="mip-app-rollback",
            expected_lakebase_instance="mip-lakebase",
        )

    assert workspace.apps.app is not None
    assert workspace.apps.delete_calls == 0


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("description", "replacement App"),
        ("creator", "other@example.com"),
        (
            "resources",
            [
                {
                    "name": "sql_warehouse",
                    "sql_warehouse": {"id": "other-warehouse", "permission": "CAN_USE"},
                }
            ],
        ),
        ("service_principal_client_id", "replacement-client-id"),
        ("service_principal_id", "replacement-scim-id"),
        ("id", "replacement-app-object-id"),
    ),
)
def test_signed_journal_never_authorizes_a_mismatched_live_app_deletion(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    replacement: object,
) -> None:
    workspace = _workspace()
    deployment_lease = lease.acquire(
        workspace,
        app_name="mip-app",
        source_git_sha="a" * 40,
    )
    marked = first_install.prepare(
        workspace,
        app_name="mip-app",
        lease_id=deployment_lease,
        source_git_sha="a" * 40,
        payload=_first_install_payload(),
    )
    workspace.apps.app = _journaled_app(marked)
    _claim_created_app(workspace, lease_id=deployment_lease)
    setattr(workspace.apps.app, field, replacement)
    monkeypatch.setattr(first_install, "_load_record", _missing_rollback)

    with pytest.raises(RuntimeError, match="does not match the signed"):
        first_install.delete_recoverable(
            workspace,
            app_name="mip-app",
            lease_id=deployment_lease,
            source_git_sha="a" * 40,
            rollback_scope="mip-app-rollback",
            expected_lakebase_instance="mip-lakebase",
        )

    assert workspace.apps.app is not None
    assert workspace.apps.delete_calls == 0


def test_matching_signed_app_state_cannot_be_deleted_as_first_install_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    deployment_lease = lease.acquire(
        workspace,
        app_name="mip-app",
        source_git_sha="a" * 40,
    )
    marked = first_install.prepare(
        workspace,
        app_name="mip-app",
        lease_id=deployment_lease,
        source_git_sha="a" * 40,
        payload=_first_install_payload(),
    )
    workspace.apps.app = _journaled_app(marked)
    _claim_created_app(workspace, lease_id=deployment_lease)
    monkeypatch.setattr(
        first_install,
        "_load_record",
        lambda *_args, **_kwargs: {
            "app_service_principal_client_id": "app-client-id",
            "app_service_principal_scim_id": "app-scim-id",
            "app_resources": marked["resources"],
        },
    )

    assert first_install.status(
        workspace,
        app_name="mip-app",
        lease_id=deployment_lease,
        source_git_sha="a" * 40,
        rollback_scope="mip-app-rollback",
        expected_lakebase_instance="mip-lakebase",
    ) == "signed"
    with pytest.raises(RuntimeError, match="not eligible for unsigned recovery"):
        first_install.delete_recoverable(
            workspace,
            app_name="mip-app",
            lease_id=deployment_lease,
            source_git_sha="a" * 40,
            rollback_scope="mip-app-rollback",
            expected_lakebase_instance="mip-lakebase",
        )

    assert workspace.apps.app is not None
    assert workspace.apps.delete_calls == 0

    first_install.complete(
        workspace,
        app_name="mip-app",
        lease_id=deployment_lease,
        source_git_sha="a" * 40,
        rollback_scope="mip-app-rollback",
        expected_lakebase_instance="mip-lakebase",
    )
    assert first_install._path("mip-app") not in workspace.workspace.data
    assert lease._path("mip-app") in workspace.workspace.data


def test_orphaned_first_install_intent_is_cleared_only_after_window_and_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    deployment_lease = lease.acquire(
        workspace,
        app_name="mip-app",
        source_git_sha="a" * 40,
        now=NOW,
    )
    first_install.prepare(
        workspace,
        app_name="mip-app",
        lease_id=deployment_lease,
        source_git_sha="a" * 40,
        payload=_first_install_payload(),
        now=NOW,
    )
    record = first_install._download(workspace, app_name="mip-app")
    assert record is not None
    assert record["create_authorized_until"] == (
        NOW + first_install.CREATE_AUTHORIZATION_WINDOW
    ).isoformat()
    assert timedelta(minutes=120) > (
        first_install.CREATE_AUTHORIZATION_WINDOW
        + first_install.AUDIT_SETTLEMENT_DELAY
    )
    monkeypatch.setattr(lease, "_now", lambda: NOW)
    assert first_install.status(
        workspace,
        app_name="mip-app",
        lease_id=deployment_lease,
        source_git_sha="a" * 40,
        rollback_scope="mip-app-rollback",
        expected_lakebase_instance="mip-lakebase",
    ) == "orphan_unclaimed"
    monkeypatch.setattr(
        first_install_recovery,
        "find_app_create_proof",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("first-install create audit proof is not available yet")
        ),
    )

    with pytest.raises(RuntimeError, match="audit settlement remains open"):
        first_install_recovery.clear_absent(
            workspace,
            app_name="mip-app",
            lease_id=deployment_lease,
            source_git_sha="a" * 40,
            warehouse_id="warehouse-id",
            now=NOW + timedelta(hours=1),
        )

    lease.release(workspace, app_name="mip-app", lease_id=deployment_lease)
    retry_now = (
        NOW
        + first_install.CREATE_AUTHORIZATION_WINDOW
        + first_install.AUDIT_SETTLEMENT_DELAY
        + timedelta(seconds=1)
    )
    retry_lease = lease.acquire(
        workspace,
        app_name="mip-app",
        source_git_sha="b" * 40,
        now=retry_now,
    )
    first_install_recovery.clear_absent(
        workspace,
        app_name="mip-app",
        lease_id=retry_lease,
        source_git_sha="b" * 40,
        warehouse_id="warehouse-id",
        now=retry_now,
    )

    assert first_install._path("mip-app") not in workspace.workspace.data
    assert lease._path("mip-app") in workspace.workspace.data


def test_authenticated_delete_converges_an_orphaned_app_delete() -> None:
    workspace = _workspace()
    deployment_lease = lease.acquire(
        workspace,
        app_name="mip-app",
        source_git_sha="a" * 40,
    )
    marked = first_install.prepare(
        workspace,
        app_name="mip-app",
        lease_id=deployment_lease,
        source_git_sha="a" * 40,
        payload=_first_install_payload(),
    )
    workspace.apps.app = _journaled_app(marked)
    _claim_created_app(workspace, lease_id=deployment_lease)
    workspace.apps.app = None

    assert first_install.status(
        workspace,
        app_name="mip-app",
        lease_id=deployment_lease,
        source_git_sha="a" * 40,
        rollback_scope="mip-app-rollback",
        expected_lakebase_instance="mip-lakebase",
    ) == "orphan_claimed"

    first_install.delete_recoverable(
        workspace,
        app_name="mip-app",
        lease_id=deployment_lease,
        source_git_sha="a" * 40,
        rollback_scope="mip-app-rollback",
        expected_lakebase_instance="mip-lakebase",
    )

    assert first_install._path("mip-app") not in workspace.workspace.data
    assert lease._path("mip-app") in workspace.workspace.data


def test_workspace_lease_rejects_signed_record_copied_to_another_app_path() -> None:
    workspace = _workspace()
    lease_id = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)
    workspace.workspace.data[lease._path("other-app")] = workspace.workspace.data[
        lease._path("mip-app")
    ]

    with pytest.raises(RuntimeError, match="path binding is invalid"):
        lease.assert_held(
            workspace,
            app_name="other-app",
            lease_id=lease_id,
            source_git_sha="a" * 40,
            now=NOW,
        )


@pytest.mark.parametrize("source", ("g" * 40, "A" * 40, "a" * 39, "a" * 41))
def test_workspace_lease_rejects_noncanonical_source_sha(source: str) -> None:
    with pytest.raises(ValueError, match="exact source SHA"):
        lease.acquire(_workspace(), app_name="mip-app", source_git_sha=source, now=NOW)


def test_delegated_writer_can_use_bound_lease_assertion() -> None:
    workspace = _workspace()
    lease_id = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40)
    workspace.current_user.me = lambda: SimpleNamespace(user_name=WRITER_ID)
    workspace.workspace.get_permissions = lambda *_args, **_kwargs: pytest.fail(
        "CAN_READ writer must not call the manager-only ACL API"
    )

    check = lease.held_assertion(
        workspace,
        app_name="mip-app",
        lease_id=lease_id,
        source_git_sha="a" * 40,
    )

    check()


def test_delegated_writer_can_assert_but_cannot_release_lease() -> None:
    workspace = _workspace()
    lease_id = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)
    workspace.current_user.me = lambda: SimpleNamespace(user_name=WRITER_ID)

    record = lease.assert_held(
        workspace,
        app_name="mip-app",
        lease_id=lease_id,
        source_git_sha="a" * 40,
        now=NOW,
    )

    assert record["writer_application_id"] == WRITER_ID
    with pytest.raises(RuntimeError, match="ownership changed before release"):
        lease.release(workspace, app_name="mip-app", lease_id=lease_id)


def test_assertion_rejects_lease_replacement_during_acl_validation() -> None:
    workspace = _workspace()
    lease_id = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)
    original_get_permissions = workspace.workspace.get_permissions
    released = False

    def replace_during_acl_check(*args: object, **kwargs: object) -> object:
        nonlocal released
        if not released:
            record = lease._download(workspace, app_name="mip-app")
            assert record is not None
            lease._release_successor(
                workspace,
                app_name="mip-app",
                record=record,
                now=NOW,
            )
            released = True
        return original_get_permissions(*args, **kwargs)

    workspace.workspace.get_permissions = replace_during_acl_check

    with pytest.raises(RuntimeError, match="changed during validation"):
        lease.assert_held(
            workspace,
            app_name="mip-app",
            lease_id=lease_id,
            source_git_sha="a" * 40,
            now=NOW,
        )

    assert lease._download(workspace, app_name="mip-app")["state"] == "released"


def test_assertion_accepts_same_lease_renewal_during_acl_validation() -> None:
    workspace = _workspace()
    lease_id = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)
    original_get_permissions = workspace.workspace.get_permissions
    renewed = False

    def renew_during_acl_check(*args: object, **kwargs: object) -> object:
        nonlocal renewed
        if not renewed:
            record = lease._download(workspace, app_name="mip-app")
            assert record is not None
            lease._create_generation(
                workspace,
                app_name="mip-app",
                record=lease._next_transition(
                    record,
                    operation="renew",
                    changes={
                        "expires_at": (
                            NOW + lease.LEASE_TTL + timedelta(minutes=1)
                        ).isoformat()
                    },
                ),
            )
            renewed = True
        return original_get_permissions(*args, **kwargs)

    workspace.workspace.get_permissions = renew_during_acl_check

    record = lease.assert_held(
        workspace,
        app_name="mip-app",
        lease_id=lease_id,
        source_git_sha="a" * 40,
        now=NOW,
    )

    assert record["lease_id"] == lease_id
    assert record["expires_at"] == (NOW + lease.LEASE_TTL + timedelta(minutes=1)).isoformat()


def test_unrelated_actor_cannot_assert_deployment_lease() -> None:
    workspace = _workspace()
    lease_id = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)
    workspace.current_user.me = lambda: SimpleNamespace(user_name="other-application-id")

    with pytest.raises(RuntimeError, match="not its holder or delegated writer"):
        lease.assert_held(
            workspace,
            app_name="mip-app",
            lease_id=lease_id,
            source_git_sha="a" * 40,
            now=NOW,
        )


def test_lease_root_avoids_shared_users_management_inheritance() -> None:
    assert lease.LEASE_ROOT == "/.mip-deployment-leases"


def test_release_appends_signed_terminal_and_never_deletes_history() -> None:
    workspace = _workspace()
    lease_id = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)
    workspace.workspace.delete = lambda _path: pytest.fail("lease history must be append-only")

    lease.release(workspace, app_name="mip-app", lease_id=lease_id)

    record = lease._download(workspace, app_name="mip-app")
    assert record is not None
    assert record["state"] == "released"
    assert record["operation"] == "release"


def test_release_preserves_only_holder_and_reserved_writer_root_acl() -> None:
    workspace = _workspace()
    lease_id = lease.acquire(
        workspace,
        app_name="mip-app",
        source_git_sha="a" * 40,
        writer_application_id=WRITER_ID,
        now=NOW,
    )
    calls_before = workspace.workspace.set_permissions_calls
    acl_before = list(workspace.workspace.access_control_list)

    lease.release(workspace, app_name="mip-app", lease_id=lease_id)

    assert workspace.workspace.set_permissions_calls == calls_before
    assert workspace.workspace.access_control_list == acl_before
    assert {
        (
            entry.user_name,
            entry.service_principal_name,
            entry.all_permissions[0].permission_level,
        )
        for entry in acl_before
    } == {
        ("deployer@example.com", None, "CAN_MANAGE"),
        (None, WRITER_ID, "CAN_READ"),
    }


def test_release_accepts_successor_create_that_commits_then_times_out() -> None:
    workspace = _workspace()
    lease_id = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)
    real_upload = workspace.workspace.upload
    calls = 0

    def commit_then_timeout(
        path: str,
        content: io.BytesIO,
        *,
        format: ImportFormat,
        overwrite: bool,
    ) -> None:
        nonlocal calls
        payload = content.getvalue()
        body = json.loads(payload)
        real_upload(path, io.BytesIO(payload), format=format, overwrite=overwrite)
        if body.get("operation") == "release" and not overwrite:
            calls += 1
            raise TimeoutError("injected timeout after release commit")

    workspace.workspace.upload = commit_then_timeout

    lease.release(workspace, app_name="mip-app", lease_id=lease_id)

    assert calls == 1
    assert lease._download(workspace, app_name="mip-app")["state"] == "released"


def test_release_refuses_timeout_before_successor_commit() -> None:
    workspace = _workspace()
    lease_id = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)
    real_upload = workspace.workspace.upload
    calls = 0

    def timeout_before_commit(
        path: str,
        content: io.BytesIO,
        *,
        format: ImportFormat,
        overwrite: bool,
    ) -> None:
        nonlocal calls
        payload = content.getvalue()
        if json.loads(payload).get("operation") == "release" and not overwrite:
            calls += 1
            raise TimeoutError("injected timeout before release commit")
        real_upload(path, io.BytesIO(payload), format=format, overwrite=overwrite)

    workspace.workspace.upload = timeout_before_commit

    with pytest.raises(RuntimeError, match="without an exact commit"):
        lease.release(workspace, app_name="mip-app", lease_id=lease_id)

    assert calls == 1
    assert lease._download(workspace, app_name="mip-app")["state"] == "active"


def test_release_is_idempotent_after_signed_terminal_commit() -> None:
    workspace = _workspace()
    lease_id = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)
    lease.release(workspace, app_name="mip-app", lease_id=lease_id)

    lease.release(workspace, app_name="mip-app", lease_id=lease_id)
    assert lease._download(workspace, app_name="mip-app")["state"] == "released"


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


def test_winner_postflight_failure_appends_signed_release_for_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    original = lease._ensure_protected_root
    attempts = 0

    def fail_once(client: object, *, holder: str, writer_application_id: str) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("injected ACL postflight failure")
        original(client, holder=holder, writer_application_id=writer_application_id)

    monkeypatch.setattr(lease, "_ensure_protected_root", fail_once)

    with pytest.raises(RuntimeError, match="injected ACL postflight failure"):
        lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)

    failed = lease._download(workspace, app_name="mip-app")
    assert failed is not None
    assert failed["state"] == "released"
    lease_id = lease.acquire(
        workspace,
        app_name="mip-app",
        source_git_sha="a" * 40,
        now=NOW,
    )
    assert lease_id


def test_process_death_after_first_generation_commit_repairs_exact_acl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    original = lease._ensure_protected_root

    def process_killed(*_args: object, **_kwargs: object) -> None:
        raise SystemExit("injected process death before ACL postflight")

    monkeypatch.setattr(lease, "_ensure_protected_root", process_killed)
    with pytest.raises(SystemExit, match="process death"):
        lease.acquire(
            workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW
        )

    committed = lease._download(workspace, app_name="mip-app")
    assert committed is not None
    assert committed["state"] == "active"
    monkeypatch.setattr(lease, "_ensure_protected_root", original)
    recovery = lease.lease_support.recovery_root(
        lease, workspace, app_name="mip-app"
    )
    assert recovery == committed["recovery_root_lease_id"]
    retry = lease.acquire(
        workspace,
        app_name="mip-app",
        source_git_sha="b" * 40,
        expired_recovery_lease_id=recovery,
        now=NOW + lease.LEASE_TTL + timedelta(seconds=1),
    )
    assert lease._download(workspace, app_name="mip-app")["lease_id"] == retry


def test_post_commit_upload_timeout_authenticates_exact_candidate() -> None:
    workspace = _workspace()
    original_upload = workspace.workspace.upload

    def commit_then_timeout(
        path: str,
        content: io.BytesIO,
        *,
        format: ImportFormat,
        overwrite: bool,
    ) -> None:
        original_upload(path, content, format=format, overwrite=overwrite)
        raise TimeoutError("injected timeout after commit")

    workspace.workspace.upload = commit_then_timeout

    lease_id = lease.acquire(
        workspace,
        app_name="mip-app",
        source_git_sha="a" * 40,
        now=NOW,
    )

    assert lease._download(workspace, app_name="mip-app")["lease_id"] == lease_id


def test_head_hint_failure_never_rolls_back_authoritative_generation() -> None:
    workspace = _workspace()
    original_upload = workspace.workspace.upload

    def commit_then_timeout(
        path: str,
        content: io.BytesIO,
        *,
        format: ImportFormat,
        overwrite: bool,
    ) -> None:
        if path == lease._head_path("mip-app"):
            raise OSError("injected non-authoritative hint failure")
        original_upload(path, content, format=format, overwrite=overwrite)

    workspace.workspace.upload = commit_then_timeout

    lease_id = lease.acquire(
        workspace,
        app_name="mip-app",
        source_git_sha="a" * 40,
        now=NOW,
    )

    assert lease._head_path("mip-app") not in workspace.workspace.data
    assert lease._download(workspace, app_name="mip-app")["lease_id"] == lease_id


def test_ambiguous_upload_never_deletes_a_different_persisted_record() -> None:
    workspace = _workspace()

    def replace_then_timeout(
        path: str,
        content: io.BytesIO,
        *,
        format: ImportFormat,
        overwrite: bool,
    ) -> None:
        assert format is ImportFormat.AUTO
        assert overwrite is False
        candidate = json.loads(content.read())
        replacement_id = "11111111-1111-4111-8111-111111111111"
        replacement = lease._sign(
            {
                key: value
                for key, value in candidate.items()
                if key not in {"attestation_verify_key", "attestation_signature"}
            }
            | {
                "lease_id": replacement_id,
                "recovery_root_lease_id": replacement_id,
            }
        )
        workspace.workspace.data[path] = json.dumps(replacement, sort_keys=True).encode()
        raise TimeoutError("injected timeout with replacement")

    workspace.workspace.upload = replace_then_timeout

    with pytest.raises(RuntimeError, match="without an exact commit"):
        lease.acquire(
            workspace,
            app_name="mip-app",
            source_git_sha="a" * 40,
            now=NOW,
        )

    persisted = lease._download(workspace, app_name="mip-app")
    assert persisted is not None
    assert persisted["lease_id"] == "11111111-1111-4111-8111-111111111111"


def test_expired_signed_lease_is_replaced_exactly_for_automatic_retry() -> None:
    workspace = _workspace()
    first = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)

    replacement = lease.acquire(
        workspace,
        app_name="mip-app",
        source_git_sha="b" * 40,
        expired_recovery_lease_id=first,
        now=NOW + lease.LEASE_TTL + timedelta(seconds=1),
    )

    persisted = lease._download(workspace, app_name="mip-app")
    assert persisted is not None
    assert replacement != first
    assert persisted["lease_id"] == replacement
    assert persisted["source_git_sha"] == "b" * 40


def test_expired_signed_lease_without_exact_recovery_authority_is_not_replaced() -> None:
    workspace = _workspace()
    first = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)

    with pytest.raises(RuntimeError, match="not authorized by its durable recovery root"):
        lease.acquire(
            workspace,
            app_name="mip-app",
            source_git_sha="b" * 40,
            writer_application_id="other-writer-application-id",
            expired_recovery_lease_id=first,
            now=NOW + lease.LEASE_TTL + timedelta(seconds=1),
        )

    assert lease._download(workspace, app_name="mip-app")["lease_id"] == first


def test_expired_signed_lease_rejects_wrong_id_for_same_writer() -> None:
    workspace = _workspace()
    first = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)

    with pytest.raises(RuntimeError, match="not authorized by its durable recovery root"):
        lease.acquire(
            workspace,
            app_name="mip-app",
            source_git_sha="b" * 40,
            expired_recovery_lease_id="different-expired-lease-id",
            now=NOW + lease.LEASE_TTL + timedelta(seconds=1),
        )

    assert lease._download(workspace, app_name="mip-app")["lease_id"] == first


def test_only_first_contender_can_replace_exact_expired_lease() -> None:
    workspace = _workspace()
    first = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)
    retry_now = NOW + lease.LEASE_TTL + timedelta(seconds=1)

    winner = lease.acquire(
        workspace,
        app_name="mip-app",
        source_git_sha="b" * 40,
        expired_recovery_lease_id=first,
        now=retry_now,
    )
    with pytest.raises(RuntimeError, match="already held"):
        lease.acquire(
            workspace,
            app_name="mip-app",
            source_git_sha="c" * 40,
            expired_recovery_lease_id=first,
            now=retry_now,
        )

    assert lease._download(workspace, app_name="mip-app")["lease_id"] == winner


def test_signed_lease_keeps_exact_recovery_root_without_first_install_journal() -> None:
    workspace = _workspace()
    creation_lease = lease.acquire(
        workspace,
        app_name="mip-app",
        source_git_sha="a" * 40,
        now=NOW,
    )
    recovery = lease.lease_support.recovery_root(
        lease, workspace, app_name="mip-app"
    )
    assert recovery == creation_lease

    retry = lease.acquire(
        workspace,
        app_name="mip-app",
        source_git_sha="b" * 40,
        expired_recovery_lease_id=recovery,
        now=NOW + lease.LEASE_TTL + timedelta(seconds=1),
    )
    assert lease._download(workspace, app_name="mip-app")["lease_id"] == retry


def test_released_lease_allows_clean_handoff_to_a_new_deployer_identity() -> None:
    workspace = _workspace()
    first = lease.acquire(
        workspace, app_name="mip-app", source_git_sha="a" * 40
    )
    lease.release(workspace, app_name="mip-app", lease_id=first)
    workspace.current_user.me = lambda: SimpleNamespace(
        user_name="replacement-deployer@example.com"
    )

    assert lease.lease_support.recovery_root(
        lease, workspace, app_name="mip-app"
    ) == ""
    second = lease.acquire(
        workspace, app_name="mip-app", source_git_sha="b" * 40
    )
    assert lease._download(workspace, app_name="mip-app")["lease_id"] == second


def test_released_lease_preserves_same_deployer_recovery_lineage() -> None:
    workspace = _workspace()
    first = lease.acquire(
        workspace, app_name="mip-app", source_git_sha="a" * 40
    )
    lease.release(workspace, app_name="mip-app", lease_id=first)

    recovery, candidates = lease.lease_support.recovery_context(
        lease, workspace, app_name="mip-app"
    )
    assert recovery == first
    assert candidates == [first]

    second = lease.acquire(
        workspace,
        app_name="mip-app",
        source_git_sha="b" * 40,
        expired_recovery_lease_id=recovery,
    )
    lease.release(workspace, app_name="mip-app", lease_id=second)

    recovery, candidates = lease.lease_support.recovery_context(
        lease, workspace, app_name="mip-app"
    )
    assert recovery == first
    assert candidates == [second, first]


def test_recovery_root_cli_exports_every_same_deployer_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = _workspace()
    first = lease.acquire(
        workspace, app_name="mip-app", source_git_sha="a" * 40
    )
    lease.release(workspace, app_name="mip-app", lease_id=first)
    second = lease.acquire(
        workspace,
        app_name="mip-app",
        source_git_sha="b" * 40,
        expired_recovery_lease_id=first,
    )
    lease.release(workspace, app_name="mip-app", lease_id=second)
    monkeypatch.setattr(lease, "WorkspaceClient", lambda: workspace)
    output = tmp_path / "recovery.env"

    assert lease.main(
        [
            "recovery-root",
            "--app-name",
            "mip-app",
            "--out-env",
            str(output),
        ]
    ) == 0

    values = dict(
        line.split("=", 1)
        for line in output.read_text(encoding="utf-8").splitlines()
    )
    assert values["MIP_APP_DEPLOYMENT_RECOVERY_ROOT"] == first
    assert values["MIP_APP_DEPLOYMENT_RECOVERY_LEASE_ID"] == second
    assert values["MIP_APP_DEPLOYMENT_RECOVERY_CANDIDATES"] == f"{second},{first}"


def test_released_lineage_remains_discoverable_after_runtime_writer_rotation() -> None:
    workspace = _workspace()
    first = lease.acquire(
        workspace,
        app_name="mip-app",
        source_git_sha="a" * 40,
        writer_application_id="old-runtime-writer",
    )
    lease.release(workspace, app_name="mip-app", lease_id=first)
    recovery, _candidates = lease.lease_support.recovery_context(
        lease, workspace, app_name="mip-app"
    )
    second = lease.acquire(
        workspace,
        app_name="mip-app",
        source_git_sha="b" * 40,
        writer_application_id="new-runtime-writer",
        expired_recovery_lease_id=recovery,
    )
    lease.release(workspace, app_name="mip-app", lease_id=second)

    recovery, candidates = lease.lease_support.recovery_context(
        lease, workspace, app_name="mip-app"
    )
    assert recovery == first
    assert candidates == [second, first]


def test_original_holder_can_recover_lineage_after_released_actor_handoff() -> None:
    workspace = _workspace()
    first = lease.acquire(
        workspace, app_name="mip-app", source_git_sha="a" * 40
    )
    lease.release(workspace, app_name="mip-app", lease_id=first)
    workspace.current_user.me = lambda: SimpleNamespace(
        user_name="replacement-deployer@example.com"
    )
    assert lease.lease_support.recovery_context(
        lease, workspace, app_name="mip-app"
    ) == ("", [])
    replacement = lease.acquire(
        workspace,
        app_name="mip-app",
        source_git_sha="b" * 40,
        writer_application_id="replacement-runtime-writer",
    )
    lease.release(workspace, app_name="mip-app", lease_id=replacement)
    workspace.current_user.me = lambda: SimpleNamespace(
        user_name="deployer@example.com"
    )

    recovery, candidates = lease.lease_support.recovery_context(
        lease, workspace, app_name="mip-app"
    )
    assert recovery == first
    assert candidates == [first]
    resumed = lease.acquire(
        workspace,
        app_name="mip-app",
        source_git_sha="a" * 40,
        expired_recovery_lease_id=recovery,
    )
    lease.release(workspace, app_name="mip-app", lease_id=resumed)
    recovery, candidates = lease.lease_support.recovery_context(
        lease, workspace, app_name="mip-app"
    )
    assert recovery == first
    assert candidates == [resumed, first]


def test_losing_historical_holder_cannot_overwrite_winning_lease_acl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace("holder-a@example.com")
    first = lease.acquire(
        workspace,
        app_name="mip-app",
        source_git_sha="a" * 40,
        writer_application_id="writer-a",
    )
    lease.release(workspace, app_name="mip-app", lease_id=first)
    workspace.current_user.me = lambda: SimpleNamespace(
        user_name="holder-b@example.com"
    )
    second = lease.acquire(
        workspace,
        app_name="mip-app",
        source_git_sha="b" * 40,
        writer_application_id="writer-b",
    )
    lease.release(workspace, app_name="mip-app", lease_id=second)

    thread_identity = threading.local()
    workspace.current_user.me = lambda: SimpleNamespace(
        user_name=thread_identity.holder
    )
    barrier = threading.Barrier(2)
    real_create = lease._create_generation

    def synchronized_create(
        client: object,
        *,
        app_name: str,
        record: dict[str, str | int],
        publish_hint: bool = True,
    ) -> dict[str, str | int]:
        if record["operation"] == "acquire":
            barrier.wait(timeout=5)
        return real_create(
            client,
            app_name=app_name,
            record=record,
            publish_hint=publish_hint,
        )

    monkeypatch.setattr(lease, "_create_generation", synchronized_create)

    def contend(holder: str, writer: str, source: str) -> tuple[str, str]:
        thread_identity.holder = holder
        try:
            lease_id = lease.acquire(
                workspace,
                app_name="mip-app",
                source_git_sha=source * 40,
                writer_application_id=writer,
            )
            return "won", lease_id
        except RuntimeError as exc:
            return "lost", str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(
                lambda args: contend(*args),
                (
                    ("holder-a@example.com", "writer-a", "c"),
                    ("holder-b@example.com", "writer-b", "d"),
                ),
            )
        )

    assert sum(status == "won" for status, _value in outcomes) == 1
    assert sum(status == "lost" for status, _value in outcomes) == 1
    head = lease._download(workspace, app_name="mip-app")
    assert head is not None and head["state"] == "active"
    thread_identity.holder = str(head["holder"])
    lease._assert_protected_root(
        workspace,
        holder=str(head["holder"]),
        writer_application_id=str(head["writer_application_id"]),
        object_id=lease._root_object_id(workspace),
    )


def test_active_lease_recovery_root_is_not_disclosed_to_another_actor() -> None:
    workspace = _workspace()
    lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40)
    workspace.current_user.me = lambda: SimpleNamespace(
        user_name="replacement-deployer@example.com"
    )

    with pytest.raises(RuntimeError, match="recovery actor is not its holder"):
        lease.lease_support.recovery_root(lease, workspace, app_name="mip-app")


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


def test_renewal_converges_when_same_lease_successor_wins_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    lease_id = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)
    real_create = lease._create_generation
    raced = False

    def competing_renewal(
        client: object,
        *,
        app_name: str,
        record: dict[str, str | int],
    ) -> None:
        nonlocal raced
        if not raced and record["operation"] == "renew":
            raced = True
            real_create(client, app_name=app_name, record=record)
            raise RuntimeError("App deployment lease generation race was lost")
        real_create(client, app_name=app_name, record=record)

    monkeypatch.setattr(lease, "_create_generation", competing_renewal)

    lease.renew(
        workspace,
        app_name="mip-app",
        lease_id=lease_id,
        source_git_sha="a" * 40,
        now=NOW + timedelta(hours=1),
    )

    assert raced is True
    assert lease._download(workspace, app_name="mip-app")["expires_at"] == (
        NOW + timedelta(hours=5)
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

    with pytest.raises(RuntimeError, match="unexpected accessor"):
        lease.assert_held(
            workspace,
            app_name="mip-app",
            lease_id=lease_id,
            source_git_sha="a" * 40,
            now=NOW + timedelta(minutes=1),
        )


def test_lease_fence_rejects_inherited_non_admin_group_management() -> None:
    workspace = _workspace()
    lease_id = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)
    workspace.workspace.access_control_list.append(
        SimpleNamespace(
            user_name=None,
            group_name="users",
            all_permissions=[SimpleNamespace(permission_level="CAN_MANAGE")],
        )
    )

    with pytest.raises(RuntimeError, match="unexpected accessor"):
        lease.assert_held(
            workspace,
            app_name="mip-app",
            lease_id=lease_id,
            source_git_sha="a" * 40,
            now=NOW + timedelta(minutes=1),
        )


def test_deployer_acl_attestation_rejects_extra_reader() -> None:
    workspace = _workspace()
    lease_id = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)
    workspace.workspace.access_control_list.append(
        SimpleNamespace(
            user_name="reader@example.com",
            service_principal_name=None,
            group_name=None,
            all_permissions=[SimpleNamespace(permission_level="CAN_READ")],
        )
    )
    with pytest.raises(RuntimeError, match="unexpected accessor"):
        lease.assert_held(
            workspace,
            app_name="mip-app",
            lease_id=lease_id,
            source_git_sha="a" * 40,
            now=NOW + timedelta(minutes=1),
        )


def test_delegated_writer_rejects_stale_deployer_acl_attestation() -> None:
    workspace = _workspace()
    lease_id = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)
    workspace.current_user.me = lambda: SimpleNamespace(user_name=WRITER_ID)

    with pytest.raises(RuntimeError, match="deployer ACL attestation is stale"):
        lease.assert_held(
            workspace,
            app_name="mip-app",
            lease_id=lease_id,
            source_git_sha="a" * 40,
            now=NOW + lease.WRITER_ACL_ATTESTATION_MAX_AGE + timedelta(seconds=1),
        )


def test_delegated_writer_resamples_time_after_stalled_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    lease_id = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)
    workspace.current_user.me = lambda: SimpleNamespace(user_name=WRITER_ID)
    observations = iter(
        (
            NOW,
            NOW + lease.WRITER_ACL_ATTESTATION_MAX_AGE + timedelta(seconds=1),
        )
    )
    monkeypatch.setattr(lease, "_now", lambda: next(observations))

    with pytest.raises(RuntimeError, match="deployer ACL attestation is stale"):
        lease.assert_held(
            workspace,
            app_name="mip-app",
            lease_id=lease_id,
            source_git_sha="a" * 40,
        )


def test_delegated_writer_cannot_renew_acl_attestation() -> None:
    workspace = _workspace()
    lease_id = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)
    workspace.current_user.me = lambda: SimpleNamespace(user_name=WRITER_ID)

    with pytest.raises(RuntimeError, match="Only the App deployment lease holder"):
        lease.renew(
            workspace,
            app_name="mip-app",
            lease_id=lease_id,
            source_git_sha="a" * 40,
            now=NOW + timedelta(minutes=1),
        )


def test_holder_heartbeat_keeps_writer_attestation_fresh_at_lifetime_cap() -> None:
    workspace = _workspace()
    lease_id = lease.acquire(
        workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW
    )
    lease.renew(
        workspace,
        app_name="mip-app",
        lease_id=lease_id,
        source_git_sha="a" * 40,
        now=NOW + timedelta(hours=2),
    )
    capped = lease._download(workspace, app_name="mip-app")
    assert capped is not None
    assert capped["expires_at"] == (NOW + lease.MAX_ACTIVE_LEASE_LIFETIME).isoformat()
    capped_seq = capped["generation_seq"]

    lease.renew(
        workspace,
        app_name="mip-app",
        lease_id=lease_id,
        source_git_sha="a" * 40,
        now=NOW + timedelta(hours=2, minutes=4),
    )
    refreshed = lease._download(workspace, app_name="mip-app")
    assert refreshed is not None
    assert refreshed["generation_seq"] == capped_seq + 1
    assert refreshed["acl_attested_at"] == (NOW + timedelta(hours=2, minutes=4)).isoformat()
    workspace.current_user.me = lambda: SimpleNamespace(user_name=WRITER_ID)
    lease.assert_held(
        workspace,
        app_name="mip-app",
        lease_id=lease_id,
        source_git_sha="a" * 40,
        now=NOW + timedelta(hours=2, minutes=5),
    )

    workspace.current_user.me = lambda: SimpleNamespace(user_name="deployer@example.com")
    lease.renew(
        workspace,
        app_name="mip-app",
        lease_id=lease_id,
        source_git_sha="a" * 40,
        now=NOW + timedelta(hours=5, minutes=58),
    )
    workspace.current_user.me = lambda: SimpleNamespace(user_name=WRITER_ID)
    lease.assert_held(
        workspace,
        app_name="mip-app",
        lease_id=lease_id,
        source_git_sha="a" * 40,
        now=NOW + timedelta(hours=5, minutes=59),
    )


def test_pre_acl_field_v4_chain_keeps_original_parent_digest_compatible() -> None:
    workspace = _workspace()
    lease.acquire(
        workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW
    )
    base_path = lease._path("mip-app")
    current_base = json.loads(workspace.workspace.data[base_path])
    legacy_base_unsigned = {
        key: value
        for key, value in current_base.items()
        if key not in {
            "acl_attested_at",
            "attestation_verify_key",
            "attestation_signature",
        }
    }
    legacy_base = lease._sign(legacy_base_unsigned)
    workspace.workspace.data[base_path] = json.dumps(legacy_base).encode()
    workspace.workspace.data[lease._head_path("mip-app")] = json.dumps(legacy_base).encode()
    normalized_base = lease._verify(legacy_base)
    legacy_child_unsigned = lease._next_transition(
        normalized_base,
        operation="renew",
        changes={"expires_at": (NOW + timedelta(hours=5)).isoformat()},
    )
    legacy_child_unsigned.pop("acl_attested_at")
    legacy_child = lease._sign(legacy_child_unsigned)
    child_path = lease._successor_path(
        "mip-app", str(normalized_base["generation_id"])
    )
    workspace.workspace.data[child_path] = json.dumps(legacy_child).encode()
    del workspace.workspace.data[lease._head_path("mip-app")]

    head = lease._download(workspace, app_name="mip-app")

    assert head is not None
    assert head["generation_id"] == legacy_child["generation_id"]
    assert head["acl_attested_at"] == (NOW + timedelta(hours=1)).isoformat()


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
    monkeypatch.setattr(lease_cli.time, "sleep", lambda _seconds: None)
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
        persisted = lease._download(workspace, app_name=app_name)
        assert persisted is not None
        assert persisted["lease_id"] == lease_id
        released.append(lease_id)
        real_release(client, app_name=app_name, lease_id=lease_id)

    monkeypatch.setattr(lease_cli.Path, "write_text", fail_write)
    monkeypatch.setattr(lease, "release", release_exact)

    with pytest.raises(OSError, match="environment write failure"):
        lease.main(
            [
                "acquire",
                "--app-name",
                "mip-app",
                "--source-git-sha",
                "a" * 40,
                "--writer-application-id",
                WRITER_ID,
                "--out-env",
                "/tmp/mip-lease.env",
            ]
        )

    assert len(released) == 1
    assert lease._download(workspace, app_name="mip-app")["state"] == "released"


def test_acquire_cli_reports_environment_handoff_compensation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    monkeypatch.setattr(lease, "WorkspaceClient", lambda: workspace)

    def fail_write(*_args: object, **_kwargs: object) -> int:
        raise OSError("injected environment write failure")

    def fail_release(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected exact release failure")

    monkeypatch.setattr(lease_cli.Path, "write_text", fail_write)
    monkeypatch.setattr(lease, "release", fail_release)

    with pytest.raises(
        RuntimeError,
        match="environment handoff failed and signed compensation did not complete",
    ) as exc_info:
        lease.main(
            [
                "acquire",
                "--app-name",
                "mip-app",
                "--source-git-sha",
                "a" * 40,
                "--writer-application-id",
                WRITER_ID,
                "--out-env",
                "/tmp/mip-lease.env",
            ]
        )

    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert "exact release failure" in str(exc_info.value.__cause__)
    assert lease._path("mip-app") in workspace.workspace.data


def test_two_expired_takeover_contenders_have_one_atomic_successor_winner() -> None:
    workspace = _workspace()
    first = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)
    original_upload = workspace.workspace.upload
    barrier = threading.Barrier(2)
    successor_lock = threading.Lock()

    def synchronized_upload(
        path: str,
        content: io.BytesIO,
        *,
        format: ImportFormat,
        overwrite: bool,
    ) -> None:
        payload = content.getvalue()
        if not overwrite and path.endswith(".next"):
            barrier.wait(timeout=5)
            with successor_lock:
                original_upload(
                    path,
                    io.BytesIO(payload),
                    format=format,
                    overwrite=overwrite,
                )
            return
        original_upload(path, io.BytesIO(payload), format=format, overwrite=overwrite)

    workspace.workspace.upload = synchronized_upload
    retry_now = NOW + lease.LEASE_TTL + timedelta(seconds=1)

    def contend(source: str) -> tuple[str, str]:
        try:
            return (
                "won",
                lease.acquire(
                    workspace,
                    app_name="mip-app",
                    source_git_sha=source * 40,
                    expired_recovery_lease_id=first,
                    now=retry_now,
                ),
            )
        except RuntimeError as exc:
            return ("lost", str(exc))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(contend, ("b", "c")))

    winners = [value for status, value in results if status == "won"]
    assert len(winners) == 1
    assert sum(status == "lost" for status, _value in results) == 1
    current = lease._download(workspace, app_name="mip-app")
    assert current is not None
    assert current["lease_id"] == winners[0]
    assert current["operation"] == "takeover"


def test_stale_and_corrupt_head_hints_cannot_override_canonical_successor() -> None:
    workspace = _workspace()
    lease_id = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)
    stale = workspace.workspace.data[lease._head_path("mip-app")]
    lease.renew(
        workspace,
        app_name="mip-app",
        lease_id=lease_id,
        source_git_sha="a" * 40,
        now=NOW + timedelta(hours=1),
    )
    renewed = lease._download(workspace, app_name="mip-app")
    assert renewed is not None

    workspace.workspace.data[lease._head_path("mip-app")] = stale
    assert lease._download(workspace, app_name="mip-app")["generation_id"] == renewed[
        "generation_id"
    ]
    workspace.workspace.data[lease._head_path("mip-app")] = b"not-json"
    assert lease._download(workspace, app_name="mip-app")["generation_id"] == renewed[
        "generation_id"
    ]


def test_head_hint_bounds_reads_after_more_than_4096_historical_generations() -> None:
    workspace = _workspace()
    lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)
    for _ in range(4097):
        parent = lease._download(workspace, app_name="mip-app")
        assert parent is not None
        lease._create_generation(
            workspace,
            app_name="mip-app",
            record=lease._next_transition(
                parent,
                operation="renew",
                changes={"expires_at": parent["expires_at"]},
            ),
        )
    calls = 0
    original_download = workspace.workspace.download

    def counted(path: str) -> io.BytesIO:
        nonlocal calls
        calls += 1
        return original_download(path)

    workspace.workspace.download = counted
    current = lease._download(workspace, app_name="mip-app")

    assert current is not None
    assert current["generation_seq"] == 4097
    assert calls <= 3


def test_three_signing_key_epochs_keep_append_only_history_verifiable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    signing_keys = [
        base64.urlsafe_b64encode(bytes((index + offset) % 256 for index in range(32)))
        .decode()
        .rstrip("=")
        for offset in (0, 41, 83)
    ]
    verify_keys = [derive_gateway_proof_verify_key(value) for value in signing_keys]

    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", signing_keys[0])
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", verify_keys[0])
    first = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW)
    lease.release(workspace, app_name="mip-app", lease_id=first)

    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS", verify_keys[0])
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY", verify_keys[0])
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", signing_keys[1])
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", verify_keys[1])
    second = lease.acquire(workspace, app_name="mip-app", source_git_sha="b" * 40)
    lease.release(workspace, app_name="mip-app", lease_id=second)

    monkeypatch.setenv(
        "MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS",
        ",".join(verify_keys[:2]),
    )
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY", verify_keys[1])
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", signing_keys[2])
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", verify_keys[2])
    third = lease.acquire(workspace, app_name="mip-app", source_git_sha="c" * 40)

    current = lease._download(workspace, app_name="mip-app")
    assert current is not None
    assert current["lease_id"] == third
    assert current["key_epoch"] == 2


def test_three_key_epochs_keep_first_install_recovery_journal_verifiable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    signing_keys = [
        base64.urlsafe_b64encode(bytes((index + offset) % 256 for index in range(32)))
        .decode()
        .rstrip("=")
        for offset in (0, 41, 83)
    ]
    verify_keys = [derive_gateway_proof_verify_key(value) for value in signing_keys]

    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", signing_keys[0])
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", verify_keys[0])
    creation_lease = lease.acquire(
        workspace, app_name="mip-app", source_git_sha="a" * 40, now=NOW
    )
    first_install.prepare(
        workspace,
        app_name="mip-app",
        lease_id=creation_lease,
        source_git_sha="a" * 40,
        payload=_first_install_payload(),
        now=NOW,
    )

    monkeypatch.setenv(
        "MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS", ",".join(verify_keys[:2])
    )
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY", verify_keys[1])
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", signing_keys[2])
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", verify_keys[2])

    record = first_install._download(workspace, app_name="mip-app")
    assert record is not None
    assert record["prepared_lease_id"] == creation_lease


def test_expired_legacy_v2_lease_migrates_only_after_grace_and_exact_authority() -> None:
    workspace = _workspace()
    lease._ensure_protected_root(
        workspace,
        holder="deployer@example.com",
        writer_application_id=WRITER_ID,
    )
    legacy_id = "22222222-2222-4222-8222-222222222222"
    raw: dict[str, str | int] = {
        "version": 2,
        "app_name": "mip-app",
        "lease_id": legacy_id,
        "source_git_sha": "a" * 40,
        "holder": "deployer@example.com",
        "writer_application_id": WRITER_ID,
        "acquired_at": NOW.isoformat(),
        "expires_at": (NOW + lease.LEASE_TTL).isoformat(),
    }
    private = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    legacy = {
        **raw,
        "attestation_verify_key": derive_gateway_proof_verify_key(SIGNING_KEY),
        "attestation_signature": lease._encode(private.sign(lease._message(raw))),
    }
    workspace.workspace.data[lease._path("mip-app")] = json.dumps(legacy).encode()

    with pytest.raises(RuntimeError, match="already held"):
        lease.acquire(
            workspace,
            app_name="mip-app",
            source_git_sha="b" * 40,
            expired_recovery_lease_id=legacy_id,
            now=NOW + lease.LEASE_TTL + timedelta(minutes=1),
        )
    migrated = lease.acquire(
        workspace,
        app_name="mip-app",
        source_git_sha="b" * 40,
        expired_recovery_lease_id=legacy_id,
        now=NOW + lease.LEASE_TTL + lease.LEGACY_TAKEOVER_GRACE,
    )

    current = lease._download(workspace, app_name="mip-app")
    assert current is not None
    assert current["lease_id"] == migrated
    assert current["operation"] == "takeover"
    assert json.loads(workspace.workspace.data[lease._path("mip-app")])["version"] == 2

    lease.release(workspace, app_name="mip-app", lease_id=migrated)
    following = lease.acquire(
        workspace,
        app_name="mip-app",
        source_git_sha="c" * 40,
        now=NOW + lease.LEASE_TTL + lease.LEGACY_TAKEOVER_GRACE + timedelta(minutes=1),
    )
    after = lease._download(workspace, app_name="mip-app")
    assert after is not None
    assert after["lease_id"] == following
    assert after["operation"] == "acquire"


def test_delayed_audit_row_during_settlement_preserves_first_install_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    prepared_at = datetime.now(UTC) - lease.LEASE_TTL
    creation_lease = lease.acquire(
        workspace,
        app_name="mip-app",
        source_git_sha="a" * 40,
        now=prepared_at,
    )
    first_install.prepare(
        workspace,
        app_name="mip-app",
        lease_id=creation_lease,
        source_git_sha="a" * 40,
        payload=_first_install_payload(),
        now=prepared_at,
    )
    lease.release(workspace, app_name="mip-app", lease_id=creation_lease)
    retry_lease = lease.acquire(
        workspace,
        app_name="mip-app",
        source_git_sha="b" * 40,
    )
    record = first_install._download(workspace, app_name="mip-app")
    assert record is not None
    settlement = datetime.fromisoformat(record["audit_settlement_until"])
    audit_visible = False

    def delayed_proof(*_args: object, **_kwargs: object) -> AppCreateAuditProof:
        if not audit_visible:
            raise RuntimeError("first-install create audit proof is not available yet")
        return AppCreateAuditProof(
            event_time=record["create_authorized_until"],
            event_id="delayed-event-id",
            request_id="delayed-request-id",
            app_id="delayed-app-id",
        )

    def reveal_during_wait(_seconds: float) -> None:
        nonlocal audit_visible
        audit_visible = True

    monkeypatch.setattr(first_install_recovery, "find_app_create_proof", delayed_proof)
    monkeypatch.setattr(first_install_recovery, "_now", lambda: settlement - timedelta(seconds=1))
    monkeypatch.setattr(first_install_recovery, "_sleep", reveal_during_wait)

    with pytest.raises(RuntimeError, match="with audited App creation"):
        first_install_recovery.clear_absent(
            workspace,
            app_name="mip-app",
            lease_id=retry_lease,
            source_git_sha="b" * 40,
            warehouse_id="warehouse-id",
        )

    assert first_install._download(workspace, app_name="mip-app") == record


def test_absent_clear_requeries_when_audit_query_crosses_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    creation_lease = lease.acquire(
        workspace, app_name="mip-app", source_git_sha="a" * 40
    )
    first_install.prepare(
        workspace,
        app_name="mip-app",
        lease_id=creation_lease,
        source_git_sha="a" * 40,
        payload=_first_install_payload(),
    )
    lease.release(workspace, app_name="mip-app", lease_id=creation_lease)
    retry_lease = lease.acquire(
        workspace, app_name="mip-app", source_git_sha="b" * 40
    )
    record = first_install._download(workspace, app_name="mip-app")
    assert record is not None
    settlement = datetime.fromisoformat(record["audit_settlement_until"])
    observations = iter(
        (
            settlement - timedelta(seconds=1),
            settlement + timedelta(seconds=1),
            settlement + timedelta(seconds=1),
            settlement + timedelta(seconds=2),
        )
    )
    queries = 0

    def no_proof(*_args: object, **_kwargs: object) -> AppCreateAuditProof:
        nonlocal queries
        queries += 1
        raise RuntimeError("first-install create audit proof is not available yet")

    monkeypatch.setattr(first_install_recovery, "find_app_create_proof", no_proof)
    monkeypatch.setattr(first_install_recovery, "_now", lambda: next(observations))
    monkeypatch.setattr(
        first_install_recovery,
        "_sleep",
        lambda _seconds: pytest.fail("a boundary-crossing query must retry immediately"),
    )

    first_install_recovery.clear_absent(
        workspace,
        app_name="mip-app",
        lease_id=retry_lease,
        source_git_sha="b" * 40,
        warehouse_id="warehouse-id",
    )

    assert queries == 2
    assert first_install._download(workspace, app_name="mip-app") is None
