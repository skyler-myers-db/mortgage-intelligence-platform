from __future__ import annotations

import base64
import io
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from databricks.sdk.errors import (
    ResourceAlreadyExists,
    ResourceConflict,
    ResourceDoesNotExist,
)

import tools.databricks.serving_query_group_access as access
import tools.databricks.serving_query_group_provenance as provenance
from backend.services.ai_gateway_proof_attestation import (
    derive_gateway_proof_verify_key,
)

_SIGNING_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")
_VERIFY_KEY = derive_gateway_proof_verify_key(_SIGNING_KEY)
_ROTATED_SIGNING_KEY = base64.urlsafe_b64encode(bytes(reversed(range(32)))).decode().rstrip("=")
_ROTATED_VERIFY_KEY = derive_gateway_proof_verify_key(_ROTATED_SIGNING_KEY)
_APP = "mip-app"
_LEASE = str(uuid4())
_SOURCE = "a" * 40
_NOW = datetime(2026, 7, 28, 12, tzinfo=UTC)
_ENDPOINT = "endpoint-id"
_APPLICATION = "app-client"
_PRINCIPAL = "app-scim"
_GROUP_NAME = access.managed_query_group_name(
    endpoint_id=_ENDPOINT,
    application_id=_APPLICATION,
)
class _Files:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    def upload(
        self,
        path: str,
        content: io.BytesIO,
        *,
        format: object,
        overwrite: bool,
    ) -> None:
        del format
        if path in self.data and not overwrite:
            raise ResourceAlreadyExists("exists")
        self.data[path] = content.read()

    def download(self, path: str) -> io.BytesIO:
        if path not in self.data:
            raise ResourceDoesNotExist("missing")
        return io.BytesIO(self.data[path])


class _Groups:
    def __init__(self) -> None:
        self.group: SimpleNamespace | None = None
        self.list_hidden = 0
        self.create_calls = 0
        self.create_error: Exception | None = None
        self.patch_calls: list[object] = []

    def list(self, **_kwargs: object) -> list[SimpleNamespace]:
        if self.list_hidden:
            self.list_hidden -= 1
            return []
        return [] if self.group is None else [self.group]

    def create(self, *, display_name: str, external_id: str) -> SimpleNamespace:
        self.create_calls += 1
        if self.create_error is not None:
            raise self.create_error
        self.group = SimpleNamespace(
            id="managed-group-id",
            display_name=display_name,
            external_id=external_id,
            members=[],
            meta=SimpleNamespace(resource_type="WorkspaceGroup"),
        )
        self.list_hidden = 1
        return self.group

    def get(self, group_id: str) -> SimpleNamespace:
        if self.group is None or self.group.id != group_id:
            raise ResourceDoesNotExist("missing")
        return self.group

    def patch(self, **kwargs: object) -> None:
        self.patch_calls.append(kwargs)


class _Workspace:
    def __init__(self) -> None:
        self.config = SimpleNamespace(host="https://workspace.cloud.databricks.com")
        self.workspace = _Files()
        self.groups = _Groups()
        self.workspace_id = 123456789

    def get_workspace_id(self) -> int:
        return self.workspace_id


@pytest.fixture(autouse=True)
def _deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", _SIGNING_KEY)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", _VERIFY_KEY)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS", "")

    def assert_expected_lease(
        _workspace: object,
        *,
        app_name: str,
        lease_id: str,
        source_git_sha: str,
        **_kwargs: object,
    ) -> dict[str, str]:
        assert app_name == _APP
        assert lease_id
        assert source_git_sha
        return {
            "lease_id": lease_id,
            "source_git_sha": source_git_sha,
        }

    monkeypatch.setattr(
        provenance,
        "assert_held",
        assert_expected_lease,
    )


def _prepare(
    workspace: _Workspace,
    *,
    lease_id: str = _LEASE,
    source_git_sha: str = _SOURCE,
) -> dict[str, object]:
    return provenance.prepare(
        workspace,
        app_name=_APP,
        deployment_lease_id=lease_id,
        deployment_source_git_sha=source_git_sha,
        endpoint_id=_ENDPOINT,
        application_id=_APPLICATION,
        service_principal_id=_PRINCIPAL,
        group_name=_GROUP_NAME,
        assert_single_writer=lambda: None,
        now=_NOW,
    )


def _claim(
    workspace: _Workspace,
    *,
    record: dict[str, object],
    group_id: str,
    lease_id: str = _LEASE,
    source_git_sha: str = _SOURCE,
    proof_kind: str = "create_response",
) -> dict[str, object]:
    return provenance.claim(
        workspace,
        app_name=_APP,
        deployment_lease_id=lease_id,
        deployment_source_git_sha=source_git_sha,
        record=record,
        group_id=group_id,
        proof_kind=proof_kind,
        assert_single_writer=lambda: None,
        now=_NOW,
    )


def _ensure(
    workspace: _Workspace,
    **kwargs: object,
) -> access.ManagedQueryGroupState:
    return access.ensure_managed_query_group(
        workspace,
        app_name=_APP,
        deployment_lease_id=_LEASE,
        deployment_source_git_sha=_SOURCE,
        endpoint_id=_ENDPOINT,
        application_id=_APPLICATION,
        service_principal_id=_PRINCIPAL,
        assert_single_writer=lambda: None,
        **kwargs,
    )


def _recover(
    workspace: _Workspace,
    *,
    expected_intent: dict[str, object],
    lease_id: str = _LEASE,
    source_git_sha: str = _SOURCE,
) -> access.ManagedQueryGroupState | None:
    return access.recover_existing_managed_query_group(
        workspace,
        app_name=_APP,
        deployment_lease_id=lease_id,
        deployment_source_git_sha=source_git_sha,
        endpoint_id=_ENDPOINT,
        application_id=_APPLICATION,
        service_principal_id=_PRINCIPAL,
        expected_intent=expected_intent,
        assert_single_writer=lambda: None,
    )


def test_prepare_and_claim_persist_exact_signed_immutable_id() -> None:
    workspace = _Workspace()
    prepared = _prepare(workspace)

    assert prepared["group_id"] == ""
    assert prepared["external_id"] == provenance.intent_external_id(
        endpoint_id=_ENDPOINT,
        application_id=_APPLICATION,
        creation_nonce=str(prepared["creation_nonce"]),
    )
    claimed = _claim(
        workspace,
        record=prepared,
        group_id="managed-group-id",
    )

    assert claimed["group_id"] == "managed-group-id"
    assert claimed["claimed_at"] == _NOW.isoformat()
    assert claimed["claim_proof_kind"] == "create_response"
    assert _prepare(workspace) == claimed


def test_signed_provenance_rejects_tampering_and_workspace_drift() -> None:
    workspace = _Workspace()
    prepared = _prepare(workspace)
    path = provenance._path(  # noqa: SLF001 - exact persistence contract
        app_name=_APP,
        endpoint_id=_ENDPOINT,
        application_id=_APPLICATION,
    )
    value = json.loads(workspace.workspace.data[path])
    value["service_principal_id"] = "other-principal"
    workspace.workspace.data[path] = json.dumps(value).encode()

    with pytest.raises(RuntimeError, match="signature is invalid"):
        _prepare(workspace)

    workspace.workspace.data[path] = json.dumps(prepared).encode()
    workspace.config.host = "https://other.cloud.databricks.com"
    with pytest.raises(RuntimeError, match="provenance scope or claim is invalid"):
        _prepare(workspace)


def test_signed_provenance_rejects_workspace_id_and_principal_scope_drift() -> None:
    workspace = _Workspace()
    _prepare(workspace)

    workspace.workspace_id = 987654321
    with pytest.raises(RuntimeError, match="provenance scope or claim is invalid"):
        _prepare(workspace)

    workspace.workspace_id = 123456789
    with pytest.raises(RuntimeError, match="provenance scope or claim is invalid"):
        provenance.prepare(
            workspace,
            app_name=_APP,
            deployment_lease_id=_LEASE,
            deployment_source_git_sha=_SOURCE,
            endpoint_id=_ENDPOINT,
            application_id=_APPLICATION,
            service_principal_id="replacement-principal",
            group_name=_GROUP_NAME,
            assert_single_writer=lambda: None,
            now=_NOW,
        )


def test_unclaimed_intent_is_readmitted_by_a_later_held_deployment() -> None:
    workspace = _Workspace()
    original = _prepare(workspace)
    assert provenance.read_existing(
        workspace,
        app_name=_APP,
        endpoint_id=_ENDPOINT,
        application_id=_APPLICATION,
        service_principal_id=_PRINCIPAL,
        group_name=_GROUP_NAME,
    ) == original
    replacement_lease = str(uuid4())

    admitted = _prepare(
        workspace,
        lease_id=replacement_lease,
        source_git_sha="b" * 40,
    )

    assert admitted["creation_nonce"] == original["creation_nonce"]
    assert admitted["origin_lease_id"] == _LEASE
    assert admitted["origin_source_git_sha"] == _SOURCE
    assert admitted["admitted_lease_id"] == replacement_lease
    assert admitted["admitted_source_git_sha"] == "b" * 40


def test_bind_only_recovery_claims_existing_exact_intent_group() -> None:
    workspace = _Workspace()
    prepared = _prepare(workspace)
    replacement_lease = str(uuid4())
    workspace.groups.group = SimpleNamespace(
        id="recovered-group-id",
        display_name=_GROUP_NAME,
        external_id=prepared["external_id"],
        members=[],
        meta=SimpleNamespace(resource_type="WorkspaceGroup"),
    )

    state = _recover(
        workspace,
        expected_intent=prepared,
        lease_id=replacement_lease,
        source_git_sha="b" * 40,
    )

    assert state is not None
    assert state.contract.id == "recovered-group-id"
    assert workspace.groups.create_calls == 0
    claimed = provenance.require_claimed(
        workspace,
        app_name=_APP,
        endpoint_id=_ENDPOINT,
        application_id=_APPLICATION,
        service_principal_id=_PRINCIPAL,
        group_name=_GROUP_NAME,
    )
    assert claimed["creation_nonce"] == prepared["creation_nonce"]
    assert claimed["admitted_lease_id"] == replacement_lease
    assert claimed["group_id"] == "recovered-group-id"
    assert claimed["claim_proof_kind"] == "signed_intent_projection"


def test_bind_only_recovery_never_creates_when_exact_group_is_absent() -> None:
    workspace = _Workspace()
    prepared = _prepare(workspace)

    assert _recover(
        workspace,
        expected_intent=prepared,
        lease_id=str(uuid4()),
        source_git_sha="b" * 40,
    ) is None

    assert workspace.groups.create_calls == 0
    assert provenance.read_existing(
        workspace,
        app_name=_APP,
        endpoint_id=_ENDPOINT,
        application_id=_APPLICATION,
        service_principal_id=_PRINCIPAL,
        group_name=_GROUP_NAME,
    ) == prepared


def test_bind_only_recovery_aborts_after_observed_intent_disappears() -> None:
    workspace = _Workspace()
    prepared = _prepare(workspace)
    workspace.groups.group = SimpleNamespace(
        id="unclaimed-group-id",
        display_name=_GROUP_NAME,
        external_id=prepared["external_id"],
        members=[],
        meta=SimpleNamespace(resource_type="WorkspaceGroup"),
    )
    workspace.workspace.data.clear()

    with pytest.raises(RuntimeError, match="provenance disappeared during recovery"):
        _recover(
            workspace,
            expected_intent=prepared,
            lease_id=str(uuid4()),
            source_git_sha="b" * 40,
        )

    assert workspace.groups.create_calls == 0
    assert workspace.workspace.data == {}
    assert workspace.groups.group.id == "unclaimed-group-id"


def test_bind_only_recovery_aborts_after_observed_group_disappears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _Workspace()
    prepared = _prepare(workspace)
    workspace.groups.group = SimpleNamespace(
        id="unclaimed-group-id",
        display_name=_GROUP_NAME,
        external_id=prepared["external_id"],
        members=[],
        meta=SimpleNamespace(resource_type="WorkspaceGroup"),
    )
    original_get = workspace.groups.get
    get_calls = 0

    def transient_get(group_id: str) -> SimpleNamespace:
        nonlocal get_calls
        get_calls += 1
        if get_calls == 2:
            workspace.groups.list_hidden = 1
            raise ResourceDoesNotExist("transiently hidden")
        return original_get(group_id)

    monkeypatch.setattr(workspace.groups, "get", transient_get)

    with pytest.raises(RuntimeError, match="group disappeared during recovery"):
        _recover(workspace, expected_intent=prepared)

    assert workspace.groups.create_calls == 0
    assert provenance.read_existing(
        workspace,
        app_name=_APP,
        endpoint_id=_ENDPOINT,
        application_id=_APPLICATION,
        service_principal_id=_PRINCIPAL,
        group_name=_GROUP_NAME,
    )["group_id"] == ""
    assert workspace.groups.group.id == "unclaimed-group-id"


def test_bind_only_recovery_rejects_a_changed_durable_intent() -> None:
    workspace = _Workspace()
    prepared = _prepare(workspace)
    workspace.groups.group = SimpleNamespace(
        id="unclaimed-group-id",
        display_name=_GROUP_NAME,
        external_id=prepared["external_id"],
        members=[],
        meta=SimpleNamespace(resource_type="WorkspaceGroup"),
    )
    _prepare(
        workspace,
        lease_id=str(uuid4()),
        source_git_sha="b" * 40,
    )

    with pytest.raises(RuntimeError, match="changed before readmission"):
        _recover(
            workspace,
            expected_intent=prepared,
            lease_id=str(uuid4()),
            source_git_sha="c" * 40,
        )

    assert workspace.groups.create_calls == 0
    assert provenance.read_existing(
        workspace,
        app_name=_APP,
        endpoint_id=_ENDPOINT,
        application_id=_APPLICATION,
        service_principal_id=_PRINCIPAL,
        group_name=_GROUP_NAME,
    )["group_id"] == ""


def test_bind_only_recovery_rejects_wrong_intent_group_without_mutation() -> None:
    workspace = _Workspace()
    prepared = _prepare(workspace)
    workspace.groups.group = SimpleNamespace(
        id="wrong-intent-group-id",
        display_name=_GROUP_NAME,
        external_id=f"{provenance.INTENT_EXTERNAL_ID_PREFIX}{'x' * 43}",
        members=[],
        meta=SimpleNamespace(resource_type="WorkspaceGroup"),
    )

    with pytest.raises(RuntimeError, match="contract drifted"):
        _recover(workspace, expected_intent=prepared)

    assert workspace.groups.create_calls == 0
    assert provenance.read_existing(
        workspace,
        app_name=_APP,
        endpoint_id=_ENDPOINT,
        application_id=_APPLICATION,
        service_principal_id=_PRINCIPAL,
        group_name=_GROUP_NAME,
    ) == prepared


def test_bind_only_recovery_rejects_unrelated_member_before_claim() -> None:
    workspace = _Workspace()
    prepared = _prepare(workspace)
    workspace.groups.group = SimpleNamespace(
        id="unsafe-intent-group-id",
        display_name=_GROUP_NAME,
        external_id=prepared["external_id"],
        members=[SimpleNamespace(value="unrelated-scim")],
        meta=SimpleNamespace(resource_type="WorkspaceGroup"),
    )

    with pytest.raises(RuntimeError, match="unrelated member"):
        _recover(workspace, expected_intent=prepared)

    assert workspace.groups.create_calls == 0
    assert provenance.read_existing(
        workspace,
        app_name=_APP,
        endpoint_id=_ENDPOINT,
        application_id=_APPLICATION,
        service_principal_id=_PRINCIPAL,
        group_name=_GROUP_NAME,
    ) == prepared


def test_claimed_immutable_id_cannot_be_replaced() -> None:
    workspace = _Workspace()
    claimed = _claim(
        workspace,
        record=_prepare(workspace),
        group_id="managed-group-id",
    )

    with pytest.raises(RuntimeError, match="claims another immutable ID"):
        _claim(
            workspace,
            record=claimed,
            group_id="replacement-group-id",
        )


def test_access_create_claims_id_before_waiting_for_name_projection() -> None:
    workspace = _Workspace()
    sleeps: list[float] = []

    state = _ensure(
        workspace,
        timeout_s=5,
        sleep=sleeps.append,
        clock=iter((0.0, 0.0)).__next__,
    )

    assert state.contract.id == "managed-group-id"
    assert sleeps == [2]
    assert _prepare(workspace)["group_id"] == "managed-group-id"


def test_claimed_create_recovers_from_delayed_projection_without_recreating() -> None:
    workspace = _Workspace()
    _ensure(
        workspace,
        timeout_s=5,
        sleep=lambda _seconds: None,
        clock=iter((0.0, 0.0)).__next__,
    )
    workspace.groups.list_hidden = 2
    sleeps: list[float] = []

    state = _ensure(
        workspace,
        timeout_s=5,
        sleep=sleeps.append,
        clock=iter((0.0, 0.0)).__next__,
    )

    assert state.contract.id == "managed-group-id"
    assert workspace.groups.create_calls == 1
    assert sleeps == [2]


def test_ambiguous_create_commit_is_claimed_exactly_on_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _Workspace()
    original_create = workspace.groups.create

    def commit_then_timeout(*, display_name: str, external_id: str) -> SimpleNamespace:
        original_create(
            display_name=display_name,
            external_id=external_id,
        )
        workspace.groups.list_hidden = 0
        raise TimeoutError("SCIM create response was lost after commit")

    monkeypatch.setattr(workspace.groups, "create", commit_then_timeout)
    with pytest.raises(TimeoutError, match="response was lost"):
        _ensure(workspace)

    assert workspace.groups.group is not None
    monkeypatch.setattr(
        workspace.groups,
        "create",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("retry must claim the exact committed intent group")
        ),
    )

    state = _ensure(workspace)

    assert state.contract.id == "managed-group-id"
    assert workspace.groups.create_calls == 1
    assert _prepare(workspace)["group_id"] == "managed-group-id"
    assert _prepare(workspace)["claim_proof_kind"] == "signed_intent_projection"


def test_signed_claim_rejects_replacement_group_id_projection() -> None:
    workspace = _Workspace()
    _ensure(
        workspace,
        timeout_s=5,
        sleep=lambda _seconds: None,
        clock=iter((0.0, 0.0)).__next__,
    )
    assert workspace.groups.group is not None
    workspace.groups.group.id = "replacement-group-id"

    with pytest.raises(RuntimeError, match="contract drifted"):
        _ensure(workspace)


def test_create_conflict_recovers_only_the_exact_nonce_bound_intent_group() -> None:
    workspace = _Workspace()
    prepared = _prepare(workspace)
    exact_intent_group = SimpleNamespace(
        id="spoof-group-id",
        display_name=_GROUP_NAME,
        external_id=prepared["external_id"],
        members=[],
        meta=SimpleNamespace(resource_type="WorkspaceGroup"),
    )
    workspace.groups.create_error = ResourceConflict("already exists")
    workspace.groups.list_hidden = 1
    workspace.groups.group = exact_intent_group

    state = _ensure(workspace)

    assert state.contract.id == "spoof-group-id"
    assert _prepare(workspace)["group_id"] == "spoof-group-id"
    assert _prepare(workspace)["claim_proof_kind"] == "signed_intent_projection"
    assert workspace.groups.patch_calls == []


def test_deterministic_name_with_wrong_marker_is_never_adopted() -> None:
    workspace = _Workspace()
    workspace.groups.group = SimpleNamespace(
        id="spoof-group-id",
        display_name=_GROUP_NAME,
        external_id=access.managed_query_group_external_id(
            endpoint_id=_ENDPOINT,
            application_id=_APPLICATION,
        ),
        members=[],
        meta=SimpleNamespace(resource_type="WorkspaceGroup"),
    )

    with pytest.raises(RuntimeError, match="contract drifted"):
        _ensure(workspace)

    assert _prepare(workspace)["group_id"] == ""


def test_claimed_provenance_remains_valid_across_registered_key_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _Workspace()
    claimed = _claim(
        workspace,
        record=_prepare(workspace),
        group_id="managed-group-id",
    )
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", _ROTATED_SIGNING_KEY)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", _ROTATED_VERIFY_KEY)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS", _VERIFY_KEY)

    assert provenance.require_claimed(
        workspace,
        app_name=_APP,
        endpoint_id=_ENDPOINT,
        application_id=_APPLICATION,
        service_principal_id=_PRINCIPAL,
        group_name=_GROUP_NAME,
    ) == claimed


def test_failed_lease_assertion_precedes_provenance_and_scim_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _Workspace()
    monkeypatch.setattr(
        provenance,
        "assert_held",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("deployment lease is stale")
        ),
    )

    with pytest.raises(RuntimeError, match="deployment lease is stale"):
        _ensure(workspace)

    assert workspace.workspace.data == {}
    assert workspace.groups.group is None
    assert workspace.groups.create_calls == 0
    assert workspace.groups.patch_calls == []


def test_final_writer_fence_preserves_prior_intent_during_readmission() -> None:
    workspace = _Workspace()
    prepared = _prepare(workspace)
    new_lease = str(uuid4())

    with pytest.raises(RuntimeError, match="deployment lease lost"):
        provenance.prepare(
            workspace,
            app_name=_APP,
            deployment_lease_id=new_lease,
            deployment_source_git_sha="b" * 40,
            endpoint_id=_ENDPOINT,
            application_id=_APPLICATION,
            service_principal_id=_PRINCIPAL,
            group_name=_GROUP_NAME,
            assert_single_writer=lambda: (_ for _ in ()).throw(
                RuntimeError("deployment lease lost")
            ),
            now=_NOW,
        )

    assert _prepare(workspace) == prepared


def test_final_writer_fence_preserves_unclaimed_intent_during_claim() -> None:
    workspace = _Workspace()
    prepared = _prepare(workspace)

    with pytest.raises(RuntimeError, match="deployment lease lost"):
        provenance.claim(
            workspace,
            app_name=_APP,
            deployment_lease_id=_LEASE,
            deployment_source_git_sha=_SOURCE,
            record=prepared,
            group_id="managed-group-id",
            proof_kind="create_response",
            assert_single_writer=lambda: (_ for _ in ()).throw(
                RuntimeError("deployment lease lost")
            ),
            now=_NOW,
        )

    assert _prepare(workspace) == prepared
