from __future__ import annotations

import base64
import io
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from databricks.sdk.errors import PermissionDenied, ResourceAlreadyExists

from tools.databricks import converge_foreign_catalog_workspace_bindings as converger
from tools.databricks import foreign_catalog_binding_catalog as catalog_state
from tools.databricks import foreign_catalog_binding_journal as journal
from tools.databricks import foreign_catalog_binding_manifest as manifest_plan
from tools.databricks.audit_agent_runtime_foreign_uc_access import (
    parse_foreign_catalog_binding_policy,
)

MIP_WORKSPACE_ID = "7474645995341779"
TESTING_WORKSPACE_ID = "2478181912221244"
PAYCHEX_WORKSPACE_ID = "2543889327043640"
SOURCE_SHA = "a" * 40
LEASE_ID = "11111111-1111-4111-8111-111111111111"
OPERATION_ID = "22222222-2222-4222-8222-222222222222"


@pytest.fixture(autouse=True)
def _signing_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    private = Ed25519PrivateKey.generate()

    def encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    monkeypatch.setenv(
        "MIP_AI_GATEWAY_PROOF_SIGNING_KEY",
        encode(private.private_bytes_raw()),
    )
    monkeypatch.setenv(
        "MIP_AI_GATEWAY_PROOF_VERIFY_KEY",
        encode(private.public_key().public_bytes_raw()),
    )
    monkeypatch.delenv(
        "MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY",
        raising=False,
    )
    monkeypatch.delenv(
        "MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS",
        raising=False,
    )


def _binding(workspace_id: str) -> dict[str, str]:
    return {
        "workspace_id": workspace_id,
        "binding_type": "BINDING_TYPE_READ_WRITE",
    }


def _policy(*, alpha_testing: bool = True, beta_testing: bool = False) -> str:
    alpha = [_binding(PAYCHEX_WORKSPACE_ID)]
    beta = [_binding(PAYCHEX_WORKSPACE_ID)]
    if alpha_testing:
        alpha.append(_binding(TESTING_WORKSPACE_ID))
    if beta_testing:
        beta.append(_binding(TESTING_WORKSPACE_ID))
    return json.dumps(
        {
            "version": 1,
            "catalogs": {
                "alpha": {
                    "owner": "alpha-owner",
                    "catalog_type": "MANAGED_CATALOG",
                    "bindings": alpha,
                },
                "beta": {
                    "owner": "beta-owner",
                    "catalog_type": "MANAGED_CATALOG",
                    "bindings": beta,
                },
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _lease(
    *,
    lease_id: str = LEASE_ID,
    expires_at: datetime | None = None,
    generation_id: str = "44444444-4444-4444-8444-444444444444",
    generation_seq: int = 3,
    chain_id: str = "33333333-3333-4333-8333-333333333333",
    recovery_root_lease_id: str = LEASE_ID,
    writer_application_id: str = "runtime-client",
) -> dict[str, str | int]:
    now = datetime.now(UTC)
    return {
        "lease_id": lease_id,
        "recovery_root_lease_id": recovery_root_lease_id,
        "chain_id": chain_id,
        "generation_id": generation_id,
        "generation_seq": generation_seq,
        "holder": "deployer@example.com",
        "writer_application_id": writer_application_id,
        "acquired_at": (now - timedelta(minutes=1)).isoformat(),
        "expires_at": (expires_at or now + timedelta(hours=1)).isoformat(),
    }


class _WorkspaceFiles:
    def __init__(self, owner: _Workspace) -> None:
        self.owner = owner
        self.records: dict[str, bytes] = {}

    def mkdirs(self, path: str) -> None:
        self.owner.operations.append(("mkdirs", path))

    def upload(
        self,
        path: str,
        stream: io.BytesIO,
        *,
        format: object,
        overwrite: bool,
    ) -> None:
        del format
        payload = stream.read()
        if path in self.records and not overwrite:
            raise ResourceAlreadyExists(path)
        self.records[path] = payload
        self.owner.operations.append(("upload", path))

    def download(self, path: str) -> io.BytesIO:
        if path not in self.records:
            from databricks.sdk.errors import ResourceDoesNotExist

            raise ResourceDoesNotExist(path)
        return io.BytesIO(self.records[path])


class _Workspace:
    def __init__(self) -> None:
        self.metadata = {
            "alpha": {
                "owner": "alpha-owner",
                "catalog_type": "MANAGED_CATALOG",
                "isolation_mode": "OPEN",
            },
            "beta": {
                "owner": "beta-owner",
                "catalog_type": "MANAGED_CATALOG",
                "isolation_mode": "ISOLATED",
            },
        }
        self.bindings = {
            "alpha": [],
            "beta": [_binding(PAYCHEX_WORKSPACE_ID)],
        }
        self.operations: list[tuple[str, str]] = []
        self.fail_bindings: set[str] = set()
        self.force_grant_denial: set[str] = set()
        self.grant_privileges = {
            "alpha": {"account users": ["BROWSE"]},
            "beta": {"account users": ["USE_CATALOG"]},
        }
        self.catalogs = SimpleNamespace(
            list=self._list_catalogs,
            update=self._update_catalog,
        )
        self.workspace_bindings = SimpleNamespace(
            get_bindings=self._get_bindings,
            update_bindings=self._update_bindings,
        )
        self.grants = SimpleNamespace(get=self._get_grants)
        self.workspace = _WorkspaceFiles(self)

    def _catalog(self, name: str) -> object:
        return SimpleNamespace(name=name, **self.metadata[name])

    def _list_catalogs(
        self,
        *,
        include_browse: bool,
        include_unbound: bool,
    ) -> object:
        assert include_browse is True
        assert include_unbound is True
        return iter(self._catalog(name) for name in sorted(self.metadata))

    def _update_catalog(self, name: str, *, isolation_mode: object) -> object:
        mode = str(getattr(isolation_mode, "value", isolation_mode)).upper()
        self.metadata[name]["isolation_mode"] = mode
        self.operations.append(("catalog", f"{name}:{mode}"))
        return self._catalog(name)

    def _get_bindings(self, _type: str, name: str) -> object:
        return iter(
            [
                SimpleNamespace(
                    workspace_id=item["workspace_id"],
                    binding_type=item["binding_type"],
                )
                for item in self.bindings[name]
            ]
        )

    def _update_bindings(
        self,
        _type: str,
        name: str,
        *,
        add: list[object],
        remove: list[object],
    ) -> object:
        if name in self.fail_bindings:
            raise RuntimeError("injected binding failure")
        current = {item["workspace_id"]: item for item in self.bindings[name]}
        for item in remove:
            current.pop(str(item.workspace_id), None)
        for item in add:
            current[str(item.workspace_id)] = {
                "workspace_id": str(item.workspace_id),
                "binding_type": str(getattr(item.binding_type, "value", item.binding_type)).upper(),
            }
        self.bindings[name] = sorted(
            current.values(),
            key=lambda item: (item["workspace_id"], item["binding_type"]),
        )
        self.operations.append(("bindings", name))
        return SimpleNamespace()

    def _get_grants(
        self,
        _type: str,
        name: str,
        *,
        max_results: int,
        page_token: str | None,
    ) -> object:
        assert max_results == 1000
        assert page_token is None
        if name in self.force_grant_denial or (
            self.metadata[name]["isolation_mode"] == "ISOLATED"
            and MIP_WORKSPACE_ID not in {item["workspace_id"] for item in self.bindings[name]}
        ):
            raise PermissionDenied("catalog is not accessible in current workspace")
        return SimpleNamespace(
            privilege_assignments=[
                SimpleNamespace(
                    principal=principal,
                    privileges=privileges,
                )
                for principal, privileges in self.grant_privileges[name].items()
            ],
            next_page_token=None,
        )


def _manifest(workspace: _Workspace, policy_json: str = "") -> dict[str, Any]:
    policy_json = policy_json or _policy()
    policy = parse_foreign_catalog_binding_policy(policy_json)
    reviewed_policy = catalog_state.policy_payload(policy)
    now = datetime.now(UTC)
    body: dict[str, object] = {
        "version": manifest_plan.MANIFEST_VERSION,
        "kind": "foreign-catalog-binding-manifest",
        "operation_id": OPERATION_ID,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=20)).isoformat(),
        "source_git_sha": SOURCE_SHA,
        "parent_manifest_sha256": "",
        "actor": "deployer@example.com",
        "account_identity": {
            "account_id": "account-id",
            "application_id": "account-client",
            "scim_id": "account-scim",
            "display_name": "account-admin",
        },
        "runtime_identity": {
            "application_id": "runtime-client",
            "account_scim_id": "runtime-account-scim",
            "account_display_name": "runtime",
            "workspace_scim_id": "runtime-workspace-scim",
            "workspace_display_name": "runtime workspace",
        },
        "app_name": "mip-staging",
        "mip_catalog": "mip",
        "app_identity": {
            "name": "mip-staging",
            "app_id": "app-id",
            "service_principal_client_id": "app-client",
            "service_principal_scim_id": "app-scim",
        },
        "metastore_id": "metastore-id",
        "mip_workspace_id": MIP_WORKSPACE_ID,
        "metastore_workspace_ids": [
            TESTING_WORKSPACE_ID,
            PAYCHEX_WORKSPACE_ID,
            MIP_WORKSPACE_ID,
        ],
        "lease": manifest_plan.lease_evidence(_lease()),
        "policy_sha256": journal.digest(reviewed_policy),
        "policy": reviewed_policy,
        "prestate": [
            catalog_state.snapshot(
                workspace,
                name,
                mip_workspace_id=MIP_WORKSPACE_ID,
            )
            for name in sorted(policy)
        ],
    }
    manifest = manifest_plan.validated_manifest(manifest_plan._seal_manifest(body))
    journal.persist_operation(
        workspace,
        manifest=manifest,
        app_name="mip-staging",
        lease_id=LEASE_ID,
    )
    return manifest


def _legacy_manifest(workspace: _Workspace) -> dict[str, Any]:
    current = _manifest(workspace)
    unsigned = {
        key: value
        for key, value in current.items()
        if key
        not in {
            "manifest_sha256",
            "attestation_alg",
            "attestation_verify_key",
            "attestation_signature",
        }
    }
    unsigned["version"] = manifest_plan.LEGACY_MANIFEST_VERSION
    unsigned["runtime_identity"] = {
        "application_id": "runtime-client",
        "scim_id": "runtime-account-scim",
        "display_name": "runtime",
    }
    legacy = manifest_plan.validated_manifest(manifest_plan._seal_manifest(unsigned))
    workspace.workspace.records.clear()
    journal.persist_operation(
        workspace,
        manifest=legacy,
        app_name="mip-staging",
        lease_id=LEASE_ID,
    )
    return legacy


def _common(policy_json: str = "") -> dict[str, object]:
    return {
        "lease_id": LEASE_ID,
        "policy_json": policy_json or _policy(),
        "app_name": "mip-staging",
        "application_id": "runtime-client",
        "expected_inventory_principal": "deployer@example.com",
        "expected_account_id": "account-id",
        "expected_account_client_id": "account-client",
        "mip_catalog": "mip",
    }


def _patch_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(converger, "_guard", lambda *_args, **_kwargs: _lease())


def test_apply_preserves_grants_and_preexisting_isolation_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _Workspace()
    manifest = _manifest(workspace)
    _patch_guard(monkeypatch)

    converger.apply_manifest(
        workspace,
        object(),
        manifest=manifest,
        action="apply",
        **_common(),
    )

    assert workspace.metadata["alpha"]["isolation_mode"] == "ISOLATED"
    assert workspace.bindings["alpha"] == sorted(
        [_binding(TESTING_WORKSPACE_ID), _binding(PAYCHEX_WORKSPACE_ID)],
        key=lambda item: item["workspace_id"],
    )
    assert workspace.bindings["beta"] == [_binding(PAYCHEX_WORKSPACE_ID)]
    assert manifest["prestate"][0]["direct_grants"] == [
        {"principal": "account users", "privileges": ["BROWSE"]}
    ]
    assert manifest["prestate"][1]["direct_grants"] is None
    for pre in manifest["prestate"]:
        post = catalog_state.snapshot(
            workspace,
            str(pre["catalog"]),
            mip_workspace_id=MIP_WORKSPACE_ID,
        )
        assert post["direct_grants"] is None


def test_snapshot_reads_preexisting_unbound_catalog_from_full_inventory() -> None:
    workspace = _Workspace()

    observed = catalog_state.snapshot(
        workspace,
        "beta",
        mip_workspace_id=MIP_WORKSPACE_ID,
    )

    assert observed == {
        "catalog": "beta",
        "owner": "beta-owner",
        "catalog_type": "MANAGED_CATALOG",
        "isolation_mode": "ISOLATED",
        "bindings": [_binding(PAYCHEX_WORKSPACE_ID)],
        "direct_grants": None,
    }


@pytest.mark.parametrize(
    ("catalog", "bindings"),
    [
        ("alpha", []),
        ("beta", [_binding(MIP_WORKSPACE_ID)]),
    ],
)
def test_snapshot_rejects_unexpected_direct_grant_denial(
    catalog: str,
    bindings: list[dict[str, str]],
) -> None:
    workspace = _Workspace()
    workspace.bindings[catalog] = bindings
    workspace.force_grant_denial.add(catalog)

    with pytest.raises(RuntimeError, match="unexpectedly inaccessible"):
        catalog_state.snapshot(
            workspace,
            catalog,
            mip_workspace_id=MIP_WORKSPACE_ID,
        )


def test_snapshot_rejects_permission_denial_after_partial_grant_page() -> None:
    workspace = _Workspace()

    def paginated_grants(
        _type: str,
        _name: str,
        *,
        max_results: int,
        page_token: str | None,
    ) -> object:
        assert max_results == 1000
        if page_token is None:
            return SimpleNamespace(
                privilege_assignments=[
                    SimpleNamespace(principal="account users", privileges=["BROWSE"])
                ],
                next_page_token="next",
            )
        raise PermissionDenied("catalog became inaccessible")

    workspace.grants.get = paginated_grants

    with pytest.raises(RuntimeError, match="during pagination"):
        catalog_state.snapshot(
            workspace,
            "beta",
            mip_workspace_id=MIP_WORKSPACE_ID,
        )


def test_intent_is_durable_before_first_catalog_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _Workspace()
    manifest = _manifest(workspace)
    _patch_guard(monkeypatch)

    converger.apply_manifest(
        workspace,
        object(),
        manifest=manifest,
        action="apply",
        **_common(),
    )

    intent = journal.event_path(
        "mip-staging",
        OPERATION_ID,
        index=0,
        direction="apply",
        phase="intent",
        catalog="alpha",
    )
    intent_index = workspace.operations.index(("upload", intent))
    mutation_index = next(
        index
        for index, operation in enumerate(workspace.operations)
        if operation[0] in {"catalog", "bindings"}
    )
    assert intent_index < mutation_index


def test_resume_reconciles_interrupted_safe_transitional_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _Workspace()
    manifest = _manifest(workspace)
    _patch_guard(monkeypatch)
    policy = parse_foreign_catalog_binding_policy(_policy())
    pre = manifest["prestate"][0]
    desired = catalog_state.desired_snapshot(pre, policy["alpha"])
    converger._load_or_write_event(
        workspace,
        manifest=manifest,
        lease=_lease(),
        index=0,
        catalog="alpha",
        direction="apply",
        phase="intent",
        observed=catalog_state.snapshot(
            workspace,
            "alpha",
            mip_workspace_id=MIP_WORKSPACE_ID,
        ),
        target=desired,
        prior_event_sha256="",
    )
    workspace.metadata["alpha"]["isolation_mode"] = "ISOLATED"

    converger.apply_manifest(
        workspace,
        object(),
        manifest=manifest,
        action="resume",
        **_common(),
    )

    assert (
        catalog_state.snapshot(
            workspace,
            "alpha",
            mip_workspace_id=MIP_WORKSPACE_ID,
        )
        == desired
    )


def test_resume_rejects_unjournaled_transitional_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _Workspace()
    manifest = _manifest(workspace)
    _patch_guard(monkeypatch)
    workspace.metadata["alpha"]["isolation_mode"] = "ISOLATED"

    with pytest.raises(RuntimeError, match="unjournaled non-prestate"):
        converger.apply_manifest(
            workspace,
            object(),
            manifest=manifest,
            action="resume",
            **_common(),
        )

    assert workspace.bindings["alpha"] == []


def test_partial_failure_stays_fail_closed_until_explicit_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _Workspace()
    manifest = _manifest(workspace)
    _patch_guard(monkeypatch)
    workspace.fail_bindings.add("alpha")

    with pytest.raises(RuntimeError, match="injected binding failure"):
        converger.apply_manifest(
            workspace,
            object(),
            manifest=manifest,
            action="apply",
            **_common(),
        )

    assert workspace.metadata["alpha"]["isolation_mode"] == "ISOLATED"
    assert workspace.bindings["alpha"] == []
    assert any("apply-failure" in path for path in workspace.workspace.records)
    workspace.fail_bindings.clear()
    converger.apply_manifest(
        workspace,
        object(),
        manifest=manifest,
        action="resume",
        **_common(),
    )
    assert workspace.bindings["alpha"]


def test_cli_never_exposes_catalog_reopening_rollback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        converger.main(["rollback"])

    assert "invalid choice: 'rollback'" in capsys.readouterr().err


def test_final_whole_policy_sweep_detects_concurrent_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _Workspace()
    manifest = _manifest(workspace)

    def guard(*_args: object, **_kwargs: object) -> dict[str, str | int]:
        converged = [path for path in workspace.workspace.records if "-apply-converged-" in path]
        if len(converged) == 2:
            workspace.metadata["alpha"]["owner"] = "concurrent-owner"
        return _lease()

    monkeypatch.setattr(converger, "_guard", guard)

    with pytest.raises(RuntimeError, match="final whole-policy sweep"):
        converger.apply_manifest(
            workspace,
            object(),
            manifest=manifest,
            action="apply",
            **_common(),
        )

    assert any(
        json.loads(payload)["catalog"] == "__whole_policy__"
        for path, payload in workspace.workspace.records.items()
        if "apply-failure" in path
    )


def test_create_manifest_rejects_omitted_open_workspace_and_broadened_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _Workspace()
    monkeypatch.setattr(
        manifest_plan.deployment_lease,
        "assert_held",
        lambda *_args, **_kwargs: _lease(),
    )
    monkeypatch.setattr(
        manifest_plan,
        "boundary_evidence",
        lambda *_args, **_kwargs: {
            "app_identity": {"name": "mip-staging"},
            "metastore_id": "metastore-id",
            "mip_workspace_id": MIP_WORKSPACE_ID,
            "metastore_workspace_ids": [
                TESTING_WORKSPACE_ID,
                PAYCHEX_WORKSPACE_ID,
                MIP_WORKSPACE_ID,
            ],
            "account_identity": {"application_id": "account-client"},
            "runtime_identity": {"application_id": "runtime-client"},
        },
    )
    kwargs = {
        "workspace": workspace,
        "account": object(),
        "app_name": "mip-staging",
        "application_id": "runtime-client",
        "expected_inventory_principal": "deployer@example.com",
        "expected_account_id": "account-id",
        "expected_account_client_id": "account-client",
        "mip_catalog": "mip",
        "lease_id": LEASE_ID,
        "source_git_sha": SOURCE_SHA,
    }
    with pytest.raises(RuntimeError, match="preserve every non-MIP"):
        manifest_plan.create_manifest(
            **kwargs,
            policy_json=_policy(alpha_testing=False),
        )
    with pytest.raises(RuntimeError, match="preserve exact bindings"):
        manifest_plan.create_manifest(
            **kwargs,
            policy_json=_policy(beta_testing=True),
        )


@pytest.mark.parametrize(
    "catalog",
    ["mip", "MIP", "system", "System", "samples", "__databricks_internal"],
)
def test_create_manifest_rejects_protected_catalog_targets(catalog: str) -> None:
    policy = json.dumps(
        {
            "version": 1,
            "catalogs": {
                catalog: {
                    "owner": "protected-owner",
                    "catalog_type": "MANAGED_CATALOG",
                    "bindings": [_binding(PAYCHEX_WORKSPACE_ID)],
                }
            },
        }
    )

    with pytest.raises(RuntimeError, match="protected catalogs"):
        manifest_plan.create_manifest(
            object(),
            object(),
            policy_json=policy,
            app_name="mip-staging",
            application_id="runtime-client",
            expected_inventory_principal="deployer@example.com",
            expected_account_id="account-id",
            expected_account_client_id="account-client",
            mip_catalog="mip",
            lease_id=LEASE_ID,
            source_git_sha=SOURCE_SHA,
        )


def test_manifest_seals_app_runtime_account_and_lease_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _Workspace()
    monkeypatch.setattr(
        manifest_plan.deployment_lease,
        "assert_held",
        lambda *_args, **_kwargs: _lease(),
    )
    expected_boundary = {
        "app_identity": {
            "name": "mip-staging",
            "app_id": "app-id",
            "service_principal_client_id": "app-client",
            "service_principal_scim_id": "app-scim",
        },
        "metastore_id": "metastore-id",
        "mip_workspace_id": MIP_WORKSPACE_ID,
        "metastore_workspace_ids": [
            TESTING_WORKSPACE_ID,
            PAYCHEX_WORKSPACE_ID,
            MIP_WORKSPACE_ID,
        ],
        "account_identity": {
            "account_id": "account-id",
            "application_id": "account-client",
            "scim_id": "account-scim",
            "display_name": "account-admin",
        },
        "runtime_identity": {
            "application_id": "runtime-client",
            "account_scim_id": "runtime-account-scim",
            "account_display_name": "runtime",
            "workspace_scim_id": "runtime-workspace-scim",
            "workspace_display_name": "runtime workspace",
        },
    }
    monkeypatch.setattr(
        manifest_plan,
        "boundary_evidence",
        lambda *_args, **_kwargs: expected_boundary,
    )

    manifest = manifest_plan.create_manifest(
        workspace,
        object(),
        policy_json=_policy(),
        app_name="mip-staging",
        application_id="runtime-client",
        expected_inventory_principal="deployer@example.com",
        expected_account_id="account-id",
        expected_account_client_id="account-client",
        mip_catalog="mip",
        lease_id=LEASE_ID,
        source_git_sha=SOURCE_SHA,
    )

    assert manifest["app_identity"] == expected_boundary["app_identity"]
    assert manifest["runtime_identity"] == expected_boundary["runtime_identity"]
    assert manifest["account_identity"] == expected_boundary["account_identity"]
    assert manifest["lease"]["record_sha256"]


def test_manifest_signature_tamper_is_rejected() -> None:
    workspace = _Workspace()
    manifest = _manifest(workspace)
    tampered = {**manifest, "actor": "attacker@example.com"}

    with pytest.raises(RuntimeError, match="signature"):
        manifest_plan.validated_manifest(tampered)


def test_manifest_requires_distinct_account_and_workspace_runtime_identity() -> None:
    manifest = _manifest(_Workspace())
    unsigned = {
        key: value
        for key, value in manifest.items()
        if key
        not in {
            "manifest_sha256",
            "attestation_alg",
            "attestation_verify_key",
            "attestation_signature",
        }
    }
    unsigned["runtime_identity"] = {
        "application_id": "runtime-client",
        "scim_id": "ambiguous-runtime-scim",
        "display_name": "runtime",
    }

    with pytest.raises(RuntimeError, match="runtime identity is incomplete"):
        manifest_plan.validated_manifest(manifest_plan._seal_manifest(unsigned))


def test_signed_legacy_manifest_remains_readable_but_cannot_mutate() -> None:
    workspace = _Workspace()
    legacy = _legacy_manifest(workspace)

    assert legacy["version"] == manifest_plan.LEGACY_MANIFEST_VERSION
    assert manifest_plan.validated_manifest(legacy) == legacy
    with pytest.raises(RuntimeError, match="legacy.*must be reauthorized"):
        converger.apply_manifest(
            workspace,
            object(),
            manifest=legacy,
            action="resume",
            **_common(),
        )
    with pytest.raises(RuntimeError, match="legacy.*must be reauthorized"):
        manifest_plan.persist_manifest(
            workspace,
            manifest=legacy,
            lease_id=LEASE_ID,
        )


@pytest.mark.parametrize(
    ("legacy", "field", "value"),
    [
        (True, "application_id", " runtime-client "),
        (True, "scim_id", " runtime-account-scim "),
        (True, "display_name", "runtime "),
        (True, "application_id", 123),
        (False, "workspace_scim_id", " runtime-workspace-scim "),
    ],
)
def test_signed_runtime_identity_must_be_canonical(
    legacy: bool,
    field: str,
    value: object,
) -> None:
    workspace = _Workspace()
    manifest = _legacy_manifest(workspace) if legacy else _manifest(workspace)
    unsigned = {
        key: item
        for key, item in manifest.items()
        if key
        not in {
            "manifest_sha256",
            "attestation_alg",
            "attestation_verify_key",
            "attestation_signature",
        }
    }
    runtime_identity = dict(unsigned["runtime_identity"])
    runtime_identity[field] = value
    unsigned["runtime_identity"] = runtime_identity

    with pytest.raises(RuntimeError, match="runtime identity is not canonical"):
        manifest_plan.validated_manifest(manifest_plan._seal_manifest(unsigned))


def test_guard_rejects_expired_signed_manifest_before_any_mutation() -> None:
    workspace = _Workspace()
    current = _manifest(workspace)
    unsigned = {
        key: value
        for key, value in current.items()
        if key
        not in {
            "manifest_sha256",
            "attestation_alg",
            "attestation_verify_key",
            "attestation_signature",
        }
    }
    now = datetime.now(UTC)
    unsigned["created_at"] = (now - timedelta(minutes=25)).isoformat()
    unsigned["expires_at"] = (now - timedelta(minutes=5)).isoformat()
    sealed_lease = dict(unsigned["lease"])
    sealed_lease["acquired_at"] = (now - timedelta(minutes=30)).isoformat()
    sealed_lease["expires_at"] = (now + timedelta(minutes=30)).isoformat()
    sealed_lease["record_sha256"] = journal.digest(sealed_lease)
    unsigned["lease"] = sealed_lease
    expired = manifest_plan.validated_manifest(manifest_plan._seal_manifest(unsigned))

    with pytest.raises(RuntimeError, match="change window expired"):
        converger._guard(
            workspace,
            object(),
            manifest=expired,
            **_common(),
        )


def test_workspace_fence_recovers_manifest_after_local_write_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _Workspace()
    manifest = _manifest(workspace)
    monkeypatch.setattr(
        manifest_plan.deployment_lease,
        "assert_held",
        lambda *_args, **_kwargs: _lease(),
    )
    monkeypatch.setattr(
        manifest_plan,
        "stopped_app_identity",
        lambda *_args, **_kwargs: manifest["app_identity"],
    )

    recovered = manifest_plan.recover_persisted_manifest(
        workspace,
        app_name="mip-staging",
        lease_id=LEASE_ID,
        source_git_sha=SOURCE_SHA,
    )

    assert recovered == manifest


def test_missing_fence_is_distinct_from_invalid_fence() -> None:
    workspace = _Workspace()

    with pytest.raises(journal.ForeignCatalogOperationNotFound):
        journal.recover_operation(
            workspace,
            app_name="mip-staging",
            lease_id=LEASE_ID,
        )

    path = journal.fence_path("mip-staging", LEASE_ID)
    workspace.workspace.records[path] = b"not-json"
    with pytest.raises(RuntimeError, match="not valid JSON"):
        journal.recover_operation(
            workspace,
            app_name="mip-staging",
            lease_id=LEASE_ID,
        )


def test_signed_completion_is_bound_to_exact_manifest() -> None:
    workspace = _Workspace()
    manifest = _manifest(workspace)

    assert not journal.operation_completed(
        workspace,
        manifest=manifest,
        app_name="mip-staging",
    )
    journal.complete_operation(
        workspace,
        manifest=manifest,
        app_name="mip-staging",
        lease_id=LEASE_ID,
    )
    assert journal.operation_completed(
        workspace,
        manifest=manifest,
        app_name="mip-staging",
    )

    path = journal.completion_path("mip-staging", str(manifest["operation_id"]))
    completion = json.loads(workspace.workspace.records[path])
    completion["manifest_sha256"] = "0" * 64
    workspace.workspace.records[path] = json.dumps(completion).encode()
    with pytest.raises(RuntimeError, match="signature is invalid"):
        journal.operation_completed(
            workspace,
            manifest=manifest,
            app_name="mip-staging",
        )


def test_completed_legacy_fence_remains_recoverable() -> None:
    workspace = _Workspace()
    legacy = _legacy_manifest(workspace)
    journal.complete_operation(
        workspace,
        manifest=legacy,
        app_name="mip-staging",
        lease_id=LEASE_ID,
    )

    recovered = manifest_plan.validated_manifest(
        journal.recover_operation(
            workspace,
            app_name="mip-staging",
            lease_id=LEASE_ID,
        )
    )

    assert recovered == legacy
    assert journal.operation_completed(
        workspace,
        manifest=recovered,
        app_name="mip-staging",
    )


def test_previous_signing_key_fence_supports_signed_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _Workspace()
    manifest = _manifest(workspace)
    _patch_guard(monkeypatch)
    converger.apply_manifest(
        workspace,
        object(),
        manifest=manifest,
        action="apply",
        **_common(),
    )
    old_verify = os.environ["MIP_AI_GATEWAY_PROOF_VERIFY_KEY"]
    replacement = Ed25519PrivateKey.generate()

    def encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    monkeypatch.setenv(
        "MIP_AI_GATEWAY_PROOF_SIGNING_KEY",
        encode(replacement.private_bytes_raw()),
    )
    monkeypatch.setenv(
        "MIP_AI_GATEWAY_PROOF_VERIFY_KEY",
        encode(replacement.public_key().public_bytes_raw()),
    )
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY", old_verify)

    def rotating_guard(*_args: object, **_kwargs: object) -> dict[str, str | int]:
        journal.assert_operation(
            workspace,
            manifest=manifest,
            app_name="mip-staging",
            lease_id=LEASE_ID,
        )
        return _lease()

    monkeypatch.setattr(converger, "_guard", rotating_guard)
    converger.verify_manifest_state(
        workspace,
        object(),
        manifest=manifest,
        **_common(),
    )

    assert workspace.metadata["alpha"]["isolation_mode"] == "ISOLATED"


def test_expired_interrupted_manifest_reauthorizes_under_fresh_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _Workspace()
    current = _manifest(workspace)
    unsigned = {
        key: value
        for key, value in current.items()
        if key
        not in {
            "manifest_sha256",
            "attestation_alg",
            "attestation_verify_key",
            "attestation_signature",
        }
    }
    now = datetime.now(UTC)
    unsigned["created_at"] = (now - timedelta(minutes=25)).isoformat()
    unsigned["expires_at"] = (now - timedelta(minutes=5)).isoformat()
    old_lease = dict(unsigned["lease"])
    old_lease["acquired_at"] = (now - timedelta(minutes=30)).isoformat()
    old_lease["expires_at"] = (now - timedelta(minutes=1)).isoformat()
    old_lease["record_sha256"] = journal.digest(old_lease)
    unsigned["lease"] = old_lease
    expired = manifest_plan.validated_manifest(manifest_plan._seal_manifest(unsigned))
    workspace.workspace.records.clear()
    journal.persist_operation(
        workspace,
        manifest=expired,
        app_name="mip-staging",
        lease_id=LEASE_ID,
    )
    workspace.metadata["alpha"]["isolation_mode"] = "ISOLATED"
    new_lease_id = "55555555-5555-4555-8555-555555555555"
    monkeypatch.setattr(
        manifest_plan.deployment_lease,
        "assert_held",
        lambda *_args, **_kwargs: _lease(
            lease_id=new_lease_id,
            generation_id="66666666-6666-4666-8666-666666666666",
            generation_seq=4,
        ),
    )
    expected_boundary = {
        key: expired[key]
        for key in (
            "app_identity",
            "metastore_id",
            "mip_workspace_id",
            "metastore_workspace_ids",
            "account_identity",
            "runtime_identity",
        )
    }
    monkeypatch.setattr(
        manifest_plan,
        "boundary_evidence",
        lambda *_args, **_kwargs: expected_boundary,
    )

    replacement = manifest_plan.reauthorize_manifest(
        workspace,
        object(),
        original_manifest=expired,
        policy_json=_policy(),
        app_name="mip-staging",
        application_id="runtime-client",
        expected_inventory_principal="deployer@example.com",
        expected_account_id="account-id",
        expected_account_client_id="account-client",
        mip_catalog="mip",
        lease_id=new_lease_id,
        source_git_sha=SOURCE_SHA,
    )
    assert replacement["parent_manifest_sha256"] == journal.digest(expired)
    assert replacement["lease"]["lease_id"] == new_lease_id
    journal.persist_operation(
        workspace,
        manifest=replacement,
        app_name="mip-staging",
        lease_id=new_lease_id,
    )
    monkeypatch.setattr(
        converger,
        "_guard",
        lambda *_args, **_kwargs: _lease(
            lease_id=new_lease_id,
            generation_id="66666666-6666-4666-8666-666666666666",
            generation_seq=4,
        ),
    )
    recovery_inputs = _common()
    recovery_inputs["lease_id"] = new_lease_id
    converger.apply_manifest(
        workspace,
        object(),
        manifest=replacement,
        action="resume",
        **recovery_inputs,
    )

    assert workspace.bindings["alpha"]


def test_incomplete_operation_reauthorizes_across_runtime_writer_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _Workspace()
    current = _manifest(workspace)
    unsigned = {
        key: value
        for key, value in current.items()
        if key
        not in {
            "manifest_sha256",
            "attestation_alg",
            "attestation_verify_key",
            "attestation_signature",
        }
    }
    old_runtime = {
        "application_id": "old-runtime-client",
        "account_scim_id": "old-runtime-account-scim",
        "account_display_name": "old runtime",
        "workspace_scim_id": "old-runtime-workspace-scim",
        "workspace_display_name": "old runtime workspace",
    }
    unsigned["runtime_identity"] = old_runtime
    old_lease = dict(unsigned["lease"])
    old_lease["writer_application_id"] = old_runtime["application_id"]
    old_lease["record_sha256"] = journal.digest(old_lease)
    unsigned["lease"] = old_lease
    interrupted = manifest_plan.validated_manifest(
        manifest_plan._seal_manifest(unsigned)
    )
    workspace.workspace.records.clear()
    journal.persist_operation(
        workspace,
        manifest=interrupted,
        app_name="mip-staging",
        lease_id=LEASE_ID,
    )
    new_lease_id = "55555555-5555-4555-8555-555555555555"
    current_runtime = {
        "application_id": "new-runtime-client",
        "account_scim_id": "new-runtime-account-scim",
        "account_display_name": "new runtime",
        "workspace_scim_id": "new-runtime-workspace-scim",
        "workspace_display_name": "new runtime workspace",
    }
    monkeypatch.setattr(
        manifest_plan.deployment_lease,
        "assert_held",
        lambda *_args, **_kwargs: _lease(
            lease_id=new_lease_id,
            generation_id="66666666-6666-4666-8666-666666666666",
            generation_seq=4,
            writer_application_id=current_runtime["application_id"],
        ),
    )
    expected_boundary = {
        key: interrupted[key]
        for key in (
            "app_identity",
            "metastore_id",
            "mip_workspace_id",
            "metastore_workspace_ids",
            "account_identity",
        )
    }
    expected_boundary["runtime_identity"] = current_runtime
    monkeypatch.setattr(
        manifest_plan,
        "boundary_evidence",
        lambda *_args, **_kwargs: expected_boundary,
    )

    replacement = manifest_plan.reauthorize_manifest(
        workspace,
        object(),
        original_manifest=interrupted,
        policy_json=_policy(),
        app_name="mip-staging",
        application_id=current_runtime["application_id"],
        expected_inventory_principal="deployer@example.com",
        expected_account_id="account-id",
        expected_account_client_id="account-client",
        mip_catalog="mip",
        lease_id=new_lease_id,
        source_git_sha=SOURCE_SHA,
    )

    assert replacement["runtime_identity"] == current_runtime
    assert replacement["lease"]["writer_application_id"] == current_runtime[
        "application_id"
    ]
    assert replacement["parent_manifest_sha256"] == journal.digest(interrupted)


def test_incomplete_legacy_operation_reauthorizes_to_v4(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _Workspace()
    legacy = _legacy_manifest(workspace)
    current_source_sha = "b" * 40
    new_lease_id = "55555555-5555-4555-8555-555555555555"
    monkeypatch.setattr(
        manifest_plan.deployment_lease,
        "assert_held",
        lambda *_args, **_kwargs: _lease(
            lease_id=new_lease_id,
            generation_id="66666666-6666-4666-8666-666666666666",
            generation_seq=4,
        ),
    )
    expected_boundary = {
        key: legacy[key]
        for key in (
            "app_identity",
            "metastore_id",
            "mip_workspace_id",
            "metastore_workspace_ids",
            "account_identity",
        )
    }
    expected_boundary["runtime_identity"] = {
        "application_id": "runtime-client",
        "account_scim_id": "runtime-account-scim",
        "account_display_name": "runtime",
        "workspace_scim_id": "runtime-workspace-scim",
        "workspace_display_name": "runtime workspace",
    }
    monkeypatch.setattr(
        manifest_plan,
        "stopped_app_identity",
        lambda *_args, **_kwargs: legacy["app_identity"],
    )
    recovered = manifest_plan.recover_persisted_manifest(
        workspace,
        app_name="mip-staging",
        lease_id=new_lease_id,
        parent_lease_id=LEASE_ID,
        source_git_sha=current_source_sha,
    )
    assert recovered == legacy

    monkeypatch.setattr(
        manifest_plan,
        "boundary_evidence",
        lambda *_args, **_kwargs: expected_boundary,
    )

    replacement = manifest_plan.reauthorize_manifest(
        workspace,
        object(),
        original_manifest=recovered,
        policy_json=_policy(),
        app_name="mip-staging",
        application_id="runtime-client",
        expected_inventory_principal="deployer@example.com",
        expected_account_id="account-id",
        expected_account_client_id="account-client",
        mip_catalog="mip",
        lease_id=new_lease_id,
        source_git_sha=current_source_sha,
    )

    assert replacement["version"] == manifest_plan.MANIFEST_VERSION
    assert replacement["source_git_sha"] == current_source_sha
    assert replacement["runtime_identity"] == expected_boundary["runtime_identity"]
    assert replacement["parent_manifest_sha256"] == journal.digest(legacy)


def test_cli_recovers_old_source_v3_and_reauthorizes_v4(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = _Workspace()
    legacy = _legacy_manifest(workspace)
    current_source_sha = "b" * 40
    new_lease_id = "55555555-5555-4555-8555-555555555555"
    fresh_lease = _lease(
        lease_id=new_lease_id,
        generation_id="66666666-6666-4666-8666-666666666666",
        generation_seq=4,
    )
    expected_boundary = {
        key: legacy[key]
        for key in (
            "app_identity",
            "metastore_id",
            "mip_workspace_id",
            "metastore_workspace_ids",
            "account_identity",
        )
    }
    expected_boundary["runtime_identity"] = {
        "application_id": "runtime-client",
        "account_scim_id": "runtime-account-scim",
        "account_display_name": "runtime",
        "workspace_scim_id": "runtime-workspace-scim",
        "workspace_display_name": "runtime workspace",
    }
    monkeypatch.setattr(converger, "WorkspaceClient", lambda: workspace)
    monkeypatch.setattr(converger, "account_client_from_env", lambda: object())
    monkeypatch.setattr(manifest_plan, "source_sha", lambda _repo: current_source_sha)
    monkeypatch.setattr(
        manifest_plan.deployment_lease,
        "assert_held",
        lambda *_args, **_kwargs: fresh_lease,
    )
    monkeypatch.setattr(
        manifest_plan,
        "stopped_app_identity",
        lambda *_args, **_kwargs: legacy["app_identity"],
    )
    monkeypatch.setattr(
        manifest_plan,
        "boundary_evidence",
        lambda *_args, **_kwargs: expected_boundary,
    )
    recovered_path = tmp_path / "legacy.json"
    replacement_path = tmp_path / "v4.json"
    common = [
        "--app-name",
        "mip-staging",
        "--application-id",
        "runtime-client",
        "--expected-inventory-principal",
        "deployer@example.com",
        "--expected-account-id",
        "account-id",
        "--expected-account-client-id",
        "account-client",
        "--mip-catalog",
        "mip",
        "--lease-id",
        new_lease_id,
        "--policy-json",
        _policy(),
    ]

    assert (
        converger.main(
            [
                "recover-local",
                *common,
                "--parent-lease-id",
                LEASE_ID,
                "--manifest",
                str(recovered_path),
            ]
        )
        == 0
    )
    assert json.loads(recovered_path.read_text()) == legacy
    assert (
        converger.main(
            [
                "reauthorize",
                *common,
                "--manifest",
                str(recovered_path),
                "--out-manifest",
                str(replacement_path),
            ]
        )
        == 0
    )
    replacement = manifest_plan.validated_manifest(
        json.loads(replacement_path.read_text())
    )
    assert replacement["version"] == manifest_plan.MANIFEST_VERSION
    assert replacement["source_git_sha"] == current_source_sha
    assert replacement["parent_manifest_sha256"] == journal.digest(legacy)


@pytest.mark.parametrize(
    "fresh_lease",
    [
        _lease(
            lease_id="55555555-5555-4555-8555-555555555555",
            generation_id="66666666-6666-4666-8666-666666666666",
            generation_seq=3,
        ),
        _lease(
            lease_id="55555555-5555-4555-8555-555555555555",
            generation_id="66666666-6666-4666-8666-666666666666",
            generation_seq=4,
            chain_id="77777777-7777-4777-8777-777777777777",
        ),
        _lease(
            lease_id="55555555-5555-4555-8555-555555555555",
            generation_id="66666666-6666-4666-8666-666666666666",
            generation_seq=4,
            recovery_root_lease_id="88888888-8888-4888-8888-888888888888",
        ),
    ],
    ids=("non-advancing-generation", "foreign-chain", "foreign-recovery-root"),
)
def test_reauthorization_rejects_lease_outside_parent_lineage(
    monkeypatch: pytest.MonkeyPatch,
    fresh_lease: dict[str, str | int],
) -> None:
    workspace = _Workspace()
    original = _manifest(workspace)
    monkeypatch.setattr(
        manifest_plan.deployment_lease,
        "assert_held",
        lambda *_args, **_kwargs: fresh_lease,
    )

    with pytest.raises(RuntimeError, match="does not descend"):
        manifest_plan.reauthorize_manifest(
            workspace,
            object(),
            original_manifest=original,
            policy_json=_policy(),
            app_name="mip-staging",
            application_id="runtime-client",
            expected_inventory_principal="deployer@example.com",
            expected_account_id="account-id",
            expected_account_client_id="account-client",
            mip_catalog="mip",
            lease_id=str(fresh_lease["lease_id"]),
            source_git_sha=SOURCE_SHA,
        )


def test_source_sha_rejects_dirty_tree(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        check=True,
    )
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("clean\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    tracked.write_text("dirty\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="clean source tree"):
        manifest_plan.source_sha(tmp_path)
