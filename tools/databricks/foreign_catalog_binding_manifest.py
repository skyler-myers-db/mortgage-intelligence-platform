"""Signed change-window manifest for foreign-catalog binding remediation."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from tools.databricks import app_deployment_lease as deployment_lease
from tools.databricks import foreign_catalog_binding_journal as journal
from tools.databricks.agent_runtime_uc_inventory import _text
from tools.databricks.audit_agent_runtime_foreign_uc_access import (
    _PLATFORM_CATALOGS,
    _account_group_evidence,
    _assert_metastore_owner_inventory_identity,
    _assert_runtime_workspace_assignment_boundary,
    _normalized_target_groups,
    parse_foreign_catalog_binding_policy,
)
from tools.databricks.converge_campaign_treatment_access import (
    target_identity_groups_probe,
)
from tools.databricks.foreign_catalog_binding_catalog import (
    desired_bindings,
    desired_snapshot,
    policy_payload,
    snapshot,
    state_kind,
)
from tools.databricks.oauth_credential_boundary import (
    held_deployment_credential_assertion,
)
from tools.databricks.uc_target_identity import (
    account_target_identity,
    workspace_target_identity,
)
from tools.databricks.workspace_system_group_evidence import (
    workspace_users_group_evidence,
)

MANIFEST_VERSION = 4
LEGACY_MANIFEST_VERSION = 3
MANIFEST_TTL = timedelta(minutes=30)
_DEFAULT_TARGET_GROUPS_PROBE = target_identity_groups_probe
MINIMUM_CHANGE_WINDOW = timedelta(minutes=5)
ATTESTATION_FIELDS = {
    "attestation_alg",
    "attestation_verify_key",
    "attestation_signature",
}


def parse_timestamp(value: object, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise RuntimeError(f"UC remediation {label} is invalid") from exc
    if parsed.tzinfo is None:
        raise RuntimeError(f"UC remediation {label} is invalid")
    return parsed.astimezone(UTC)


def stopped_app_identity(workspace: Any, app_name: str) -> dict[str, str]:
    app = workspace.apps.get(app_name)
    state = _text(getattr(getattr(app, "compute_status", None), "state", None)).upper()
    if state != "STOPPED" or getattr(app, "pending_deployment", None) is not None:
        raise RuntimeError(f"App {app_name} must remain STOPPED without a pending deployment")
    identity = {
        "name": _text(getattr(app, "name", None)),
        "app_id": _text(getattr(app, "id", None)),
        "service_principal_client_id": _text(getattr(app, "service_principal_client_id", None)),
        "service_principal_scim_id": _text(getattr(app, "service_principal_id", None)),
    }
    if identity["name"] != app_name or not all(identity.values()):
        raise RuntimeError(f"App {app_name} returned incomplete immutable identity")
    return identity


def source_sha(repo: Path) -> str:
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if dirty:
        raise RuntimeError("UC remediation requires a clean source tree")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha):
        raise RuntimeError("UC remediation source SHA is invalid")
    return sha


def write_exclusive(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(value, sort_keys=True, indent=2))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def account_actor_identity(
    account: Any,
    *,
    expected_account_id: str,
    expected_account_client_id: str,
) -> dict[str, str]:
    config = getattr(account, "config", None)
    account_id = _text(getattr(config, "account_id", None))
    client_id = _text(getattr(config, "client_id", None))
    if (
        account_id != expected_account_id
        or client_id.casefold() != expected_account_client_id.casefold()
    ):
        raise RuntimeError("UC remediation account authority identity drifted")
    scim_id, display_name = account_target_identity(
        account,
        application_id=client_id,
    )
    if not display_name:
        raise RuntimeError("UC remediation account authority has no display name")
    return {
        "account_id": account_id,
        "application_id": client_id,
        "scim_id": scim_id,
        "display_name": display_name,
    }


def lease_evidence(record: dict[str, str | int]) -> dict[str, object]:
    return {
        "lease_id": record["lease_id"],
        "recovery_root_lease_id": record["recovery_root_lease_id"],
        "chain_id": record["chain_id"],
        "generation_id": record["generation_id"],
        "generation_seq": record["generation_seq"],
        "record_sha256": deployment_lease._record_digest(record),
        "holder": record["holder"],
        "writer_application_id": record["writer_application_id"],
        "acquired_at": record["acquired_at"],
        "expires_at": record["expires_at"],
    }


def boundary_evidence(
    workspace: Any,
    account: Any,
    *,
    app_name: str,
    application_id: str,
    expected_inventory_principal: str,
    expected_account_id: str,
    expected_account_client_id: str,
    approved_workspace_ids: set[str],
    target_groups_probe: Callable[..., dict[str, str]] = target_identity_groups_probe,
    assert_single_writer: Callable[[], None] | None = None,
) -> dict[str, object]:
    app_identity = stopped_app_identity(workspace, app_name)
    metastore_id, workspace_id = _assert_metastore_owner_inventory_identity(
        workspace,
        expected_principal=expected_inventory_principal,
    )
    account_identity = account_actor_identity(
        account,
        expected_account_id=expected_account_id,
        expected_account_client_id=expected_account_client_id,
    )
    runtime_scim_id, runtime_display_name = account_target_identity(
        account,
        application_id=application_id,
    )
    workspace_runtime = workspace_target_identity(
        workspace,
        application_id=application_id,
    )
    workspace_host = _text(getattr(getattr(workspace, "config", None), "host", None))
    if not workspace_host:
        raise RuntimeError("UC remediation found no workspace host")
    probe_kwargs: dict[str, object] = {
        "expected_workspace_scim_id": workspace_runtime.scim_id,
        "workspace_host": workspace_host,
    }
    if target_groups_probe is _DEFAULT_TARGET_GROUPS_PROBE:
        credential_lease = (
            assert_single_writer
            or held_deployment_credential_assertion(
                workspace,
                app_name=app_name,
            )
        )
        probe_kwargs["assert_single_writer"] = credential_lease
    effective_target_groups = _normalized_target_groups(
        target_groups_probe(
            account,
            runtime_scim_id,
            application_id,
            **probe_kwargs,
        )
    )
    account_effective_groups, implicit_system_groups = _account_group_evidence(
        account,
        target_scim_id=runtime_scim_id,
    )
    workspace_system_groups = workspace_users_group_evidence(workspace)
    metastore_workspace_ids = _assert_runtime_workspace_assignment_boundary(
        account,
        application_id=application_id,
        target_scim_ids={runtime_scim_id, workspace_runtime.scim_id},
        account_target_scim_id=runtime_scim_id,
        account_effective_groups=account_effective_groups,
        effective_target_groups=effective_target_groups,
        implicit_system_groups=implicit_system_groups,
        workspace_system_groups=workspace_system_groups,
        metastore_id=metastore_id,
        workspace_id=workspace_id,
        approved_foreign_workspace_ids=approved_workspace_ids,
    )
    return {
        "app_identity": app_identity,
        "metastore_id": metastore_id,
        "mip_workspace_id": workspace_id,
        "metastore_workspace_ids": sorted(metastore_workspace_ids),
        "account_identity": account_identity,
        "runtime_identity": {
            "application_id": application_id,
            "account_scim_id": runtime_scim_id,
            "account_display_name": runtime_display_name,
            "workspace_scim_id": workspace_runtime.scim_id,
            "workspace_display_name": workspace_runtime.display_name,
        },
    }


def _seal_manifest(body: dict[str, object]) -> dict[str, Any]:
    return journal.sign({**body, "manifest_sha256": journal.digest(body)})


def _string_record(
    value: object,
    *,
    keys: set[str],
    label: str,
) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != keys:
        raise RuntimeError(f"UC remediation {label} identity is incomplete")
    if any(
        not isinstance(item, str) or item != item.strip()
        for item in value.values()
    ):
        raise RuntimeError(f"UC remediation {label} identity is not canonical")
    normalized = {str(key): item for key, item in value.items()}
    if not all(normalized.values()):
        raise RuntimeError(f"UC remediation {label} identity is incomplete")
    return normalized


def _runtime_identity_record(value: object) -> dict[str, str]:
    keys = {
        "application_id",
        "account_scim_id",
        "account_display_name",
        "workspace_scim_id",
        "workspace_display_name",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise RuntimeError("UC remediation runtime identity is incomplete")
    if any(
        not isinstance(item, str) or item != item.strip()
        for item in value.values()
    ):
        raise RuntimeError("UC remediation runtime identity is not canonical")
    normalized = {str(key): item for key, item in value.items()}
    required = keys - {"workspace_display_name"}
    if any(not normalized[key] for key in required):
        raise RuntimeError("UC remediation runtime identity is incomplete")
    return normalized


def _legacy_runtime_identity_record(value: object) -> dict[str, str]:
    return _string_record(
        value,
        keys={"application_id", "scim_id", "display_name"},
        label="legacy runtime",
    )


def _validate_lease(value: object, *, manifest_expiry: datetime) -> None:
    required = {
        "lease_id",
        "recovery_root_lease_id",
        "chain_id",
        "generation_id",
        "generation_seq",
        "record_sha256",
        "holder",
        "writer_application_id",
        "acquired_at",
        "expires_at",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise RuntimeError("UC remediation lease evidence is incomplete")
    try:
        UUID(str(value["lease_id"]))
        UUID(str(value["recovery_root_lease_id"]))
        UUID(str(value["chain_id"]))
        UUID(str(value["generation_id"]))
    except ValueError as exc:
        raise RuntimeError("UC remediation lease identity is invalid") from exc
    sequence = value["generation_seq"]
    digest = str(value["record_sha256"])
    if (
        not isinstance(sequence, int)
        or isinstance(sequence, bool)
        or sequence < 0
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
        or not str(value["holder"]).strip()
        or not str(value["writer_application_id"]).strip()
    ):
        raise RuntimeError("UC remediation lease evidence is invalid")
    acquired = parse_timestamp(value["acquired_at"], "lease acquisition")
    expires = parse_timestamp(value["expires_at"], "lease expiration")
    if not acquired < manifest_expiry <= expires:
        raise RuntimeError("UC remediation manifest exceeds its sealed lease")


def _validate_catalog_evidence(manifest: dict[str, Any]) -> None:
    policy = manifest["policy"]
    prestate = manifest["prestate"]
    if not isinstance(policy, list) or not isinstance(prestate, list) or not policy:
        raise RuntimeError("UC remediation manifest catalog evidence is invalid")
    try:
        parsed = parse_foreign_catalog_binding_policy(
            json.dumps(
                {
                    "version": 1,
                    "catalogs": {
                        item["catalog"]: {
                            "owner": item["owner"],
                            "catalog_type": item["catalog_type"],
                            "bindings": item["bindings"],
                        }
                        for item in policy
                    },
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("UC remediation manifest policy is invalid") from exc
    if policy_payload(parsed) != policy:
        raise RuntimeError("UC remediation manifest policy is not canonical")
    policy_names = [str(item["catalog"]) for item in policy]
    prestate_names: list[str] = []
    required = {
        "catalog",
        "owner",
        "catalog_type",
        "isolation_mode",
        "bindings",
        "direct_grants",
    }
    for item in prestate:
        if not isinstance(item, dict) or set(item) != required:
            raise RuntimeError("UC remediation pre-state is incomplete")
        name = str(item["catalog"]).strip()
        owner = str(item["owner"]).strip()
        catalog_type = str(item["catalog_type"]).strip()
        mode = str(item["isolation_mode"])
        bindings = item["bindings"]
        grants = item["direct_grants"]
        if (
            not name
            or not owner
            or not catalog_type
            or mode not in {"OPEN", "ISOLATED"}
            or not isinstance(bindings, list)
            or (grants is not None and not isinstance(grants, list))
            or (mode == "OPEN" and not isinstance(grants, list))
            or (mode == "ISOLATED" and grants is not None)
        ):
            raise RuntimeError("UC remediation pre-state is invalid")
        binding_keys = [
            (entry.get("workspace_id"), entry.get("binding_type"))
            for entry in bindings
            if isinstance(entry, dict) and set(entry) == {"workspace_id", "binding_type"}
        ]
        grant_keys = (
            [
                (entry.get("principal"), entry.get("privileges"))
                for entry in grants
                if isinstance(entry, dict) and set(entry) == {"principal", "privileges"}
            ]
            if isinstance(grants, list)
            else []
        )
        if (
            len(binding_keys) != len(bindings)
            or (isinstance(grants, list) and len(grant_keys) != len(grants))
            or binding_keys != sorted(binding_keys)
            or grant_keys != sorted(grant_keys)
            or len({workspace_id for workspace_id, _kind in binding_keys}) != len(binding_keys)
            or any(
                not str(workspace_id).isdecimal()
                or kind
                not in {
                    "BINDING_TYPE_READ_ONLY",
                    "BINDING_TYPE_READ_WRITE",
                }
                for workspace_id, kind in binding_keys
            )
            or any(
                not str(principal).strip()
                or not isinstance(privileges, list)
                or not privileges
                or privileges != sorted(set(privileges))
                for principal, privileges in grant_keys
            )
        ):
            raise RuntimeError("UC remediation pre-state evidence is not canonical")
        prestate_names.append(name)
    if policy_names != sorted(set(policy_names)) or prestate_names != policy_names:
        raise RuntimeError("UC remediation catalog identity sets differ")


def validated_manifest(value: object) -> dict[str, Any]:
    manifest = journal.verify(value)
    required = {
        "version",
        "kind",
        "operation_id",
        "created_at",
        "expires_at",
        "source_git_sha",
        "parent_manifest_sha256",
        "actor",
        "account_identity",
        "runtime_identity",
        "app_name",
        "mip_catalog",
        "app_identity",
        "metastore_id",
        "mip_workspace_id",
        "metastore_workspace_ids",
        "lease",
        "policy_sha256",
        "policy",
        "prestate",
        "manifest_sha256",
        *ATTESTATION_FIELDS,
    }
    if set(manifest) != required:
        raise RuntimeError("UC remediation manifest is incomplete")
    unsigned = {
        key: value
        for key, value in manifest.items()
        if key not in ATTESTATION_FIELDS | {"manifest_sha256"}
    }
    version = manifest["version"]
    if (
        type(version) is not int
        or version not in {LEGACY_MANIFEST_VERSION, MANIFEST_VERSION}
        or manifest["kind"] != "foreign-catalog-binding-manifest"
        or manifest["manifest_sha256"] != journal.digest(unsigned)
        or manifest["policy_sha256"] != journal.digest(manifest["policy"])
    ):
        raise RuntimeError("UC remediation manifest digest contract is invalid")
    try:
        manifest["operation_id"] = str(UUID(str(manifest["operation_id"])))
    except ValueError as exc:
        raise RuntimeError("UC remediation operation identity is invalid") from exc
    created = parse_timestamp(manifest["created_at"], "creation timestamp")
    expires = parse_timestamp(manifest["expires_at"], "expiration timestamp")
    if expires <= created or expires - created > MANIFEST_TTL:
        raise RuntimeError("UC remediation manifest change window is invalid")
    sha = str(manifest["source_git_sha"])
    if len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha):
        raise RuntimeError("UC remediation manifest source SHA is invalid")
    parent_digest = str(manifest["parent_manifest_sha256"])
    if parent_digest and (
        len(parent_digest) != 64 or any(char not in "0123456789abcdef" for char in parent_digest)
    ):
        raise RuntimeError("UC remediation parent manifest digest is invalid")
    account_identity = _string_record(
        manifest["account_identity"],
        keys={"account_id", "application_id", "scim_id", "display_name"},
        label="account authority",
    )
    runtime_identity = (
        _legacy_runtime_identity_record(manifest["runtime_identity"])
        if version == LEGACY_MANIFEST_VERSION
        else _runtime_identity_record(manifest["runtime_identity"])
    )
    app_identity = _string_record(
        manifest["app_identity"],
        keys={
            "name",
            "app_id",
            "service_principal_client_id",
            "service_principal_scim_id",
        },
        label="App",
    )
    _validate_lease(manifest["lease"], manifest_expiry=expires)
    workspace_id = str(manifest["mip_workspace_id"])
    workspace_ids = manifest["metastore_workspace_ids"]
    if (
        not str(manifest["actor"]).strip()
        or not str(manifest["metastore_id"]).strip()
        or not str(manifest["mip_catalog"]).strip()
        or not workspace_id.isdecimal()
        or not isinstance(workspace_ids, list)
        or any(not isinstance(item, str) or not item.isdecimal() for item in workspace_ids)
        or workspace_ids != sorted(set(workspace_ids))
        or workspace_id not in workspace_ids
        or app_identity["name"] != manifest["app_name"]
        or runtime_identity["application_id"] != manifest["lease"]["writer_application_id"]
        or account_identity["account_id"] == ""
    ):
        raise RuntimeError("UC remediation immutable identity contract is invalid")
    _validate_catalog_evidence(manifest)
    return manifest


def create_manifest(
    workspace: Any,
    account: Any,
    *,
    policy_json: str,
    app_name: str,
    application_id: str,
    expected_inventory_principal: str,
    expected_account_id: str,
    expected_account_client_id: str,
    mip_catalog: str,
    lease_id: str,
    source_git_sha: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    policy = parse_foreign_catalog_binding_policy(policy_json)
    if not policy:
        raise RuntimeError("foreign catalog binding policy must not be empty")
    if not mip_catalog.strip() or mip_catalog != mip_catalog.strip():
        raise RuntimeError("configured MIP catalog identity is invalid")
    protected_catalogs = {item.casefold() for item in {*_PLATFORM_CATALOGS, mip_catalog}}
    forbidden = sorted(name for name in policy if name.casefold() in protected_catalogs)
    if forbidden:
        raise RuntimeError(
            "foreign catalog binding policy names protected catalogs: " + ", ".join(forbidden)
        )
    if len(source_git_sha) != 40 or any(char not in "0123456789abcdef" for char in source_git_sha):
        raise RuntimeError("UC remediation source SHA is invalid")
    current = now or datetime.now(UTC)
    lease = deployment_lease.assert_held(
        workspace,
        app_name=app_name,
        lease_id=lease_id,
        source_git_sha=source_git_sha,
        now=current,
    )
    if str(lease["writer_application_id"]).casefold() != application_id.casefold():
        raise RuntimeError("UC remediation lease writer is not the reviewed runtime")
    approved_ids = {
        workspace_id for item in policy.values() for workspace_id, _binding_type in item.bindings
    }
    boundary = boundary_evidence(
        workspace,
        account,
        app_name=app_name,
        application_id=application_id,
        expected_inventory_principal=expected_inventory_principal,
        expected_account_id=expected_account_id,
        expected_account_client_id=expected_account_client_id,
        approved_workspace_ids=approved_ids,
    )
    metastore_workspace_ids = cast(list[str], boundary["metastore_workspace_ids"])
    mip_workspace_id = str(boundary["mip_workspace_id"])
    preserved_workspace_ids = set(metastore_workspace_ids) - {mip_workspace_id}
    snapshots = [
        snapshot(workspace, name, mip_workspace_id=mip_workspace_id) for name in sorted(policy)
    ]
    for current_snapshot in snapshots:
        expected = policy[str(current_snapshot["catalog"])]
        if (
            current_snapshot["owner"] != expected.owner
            or current_snapshot["catalog_type"] != expected.catalog_type
        ):
            raise RuntimeError(
                f"catalog {current_snapshot['catalog']} does not match the " "reviewed owner/type"
            )
        current_bindings = cast(
            list[dict[str, str]],
            current_snapshot["bindings"],
        )
        desired = desired_bindings(expected)
        desired_ids = {item["workspace_id"] for item in desired}
        if mip_workspace_id in desired_ids:
            raise RuntimeError(
                f"catalog {current_snapshot['catalog']} policy includes the MIP workspace"
            )
        if current_snapshot["isolation_mode"] == "OPEN":
            if current_bindings:
                raise RuntimeError(
                    f"OPEN catalog {current_snapshot['catalog']} has latent bindings"
                )
            if desired_ids != preserved_workspace_ids or any(
                item["binding_type"] != "BINDING_TYPE_READ_WRITE" for item in desired
            ):
                raise RuntimeError(
                    f"OPEN catalog {current_snapshot['catalog']} must preserve every "
                    "non-MIP metastore workspace as READ_WRITE"
                )
        elif current_bindings != desired:
            raise RuntimeError(
                f"ISOLATED catalog {current_snapshot['catalog']} policy must "
                "preserve exact bindings"
            )
    lease_expiry = parse_timestamp(lease["expires_at"], "lease expiration")
    expires = min(lease_expiry, current + MANIFEST_TTL)
    if expires - current < MINIMUM_CHANGE_WINDOW:
        raise RuntimeError("UC remediation lease has insufficient remaining change window")
    reviewed_policy = policy_payload(policy)
    body: dict[str, object] = {
        "version": MANIFEST_VERSION,
        "kind": "foreign-catalog-binding-manifest",
        "operation_id": str(uuid4()),
        "created_at": current.isoformat(),
        "expires_at": expires.isoformat(),
        "source_git_sha": source_git_sha,
        "parent_manifest_sha256": "",
        "actor": expected_inventory_principal,
        "account_identity": boundary["account_identity"],
        "runtime_identity": boundary["runtime_identity"],
        "app_name": app_name,
        "mip_catalog": mip_catalog,
        "app_identity": boundary["app_identity"],
        "metastore_id": boundary["metastore_id"],
        "mip_workspace_id": boundary["mip_workspace_id"],
        "metastore_workspace_ids": boundary["metastore_workspace_ids"],
        "lease": lease_evidence(lease),
        "policy_sha256": journal.digest(reviewed_policy),
        "policy": reviewed_policy,
        "prestate": snapshots,
    }
    return validated_manifest(_seal_manifest(body))


def persist_manifest(
    workspace: Any,
    *,
    manifest: dict[str, Any],
    lease_id: str,
    now: datetime | None = None,
) -> None:
    manifest = validated_manifest(manifest)
    if manifest["version"] != MANIFEST_VERSION:
        raise RuntimeError("legacy UC remediation manifest must be reauthorized")
    source_git_sha = str(manifest["source_git_sha"])
    deployment_lease.assert_held(
        workspace,
        app_name=str(manifest["app_name"]),
        lease_id=lease_id,
        source_git_sha=source_git_sha,
        now=now,
    )
    if stopped_app_identity(workspace, str(manifest["app_name"])) != manifest["app_identity"]:
        raise RuntimeError("UC remediation App identity drifted before manifest commit")
    journal.persist_operation(
        workspace,
        manifest=manifest,
        app_name=str(manifest["app_name"]),
        lease_id=lease_id,
    )
    deployment_lease.assert_held(
        workspace,
        app_name=str(manifest["app_name"]),
        lease_id=lease_id,
        source_git_sha=source_git_sha,
        now=now,
    )
    journal.assert_operation(
        workspace,
        manifest=manifest,
        app_name=str(manifest["app_name"]),
        lease_id=lease_id,
    )


def recover_persisted_manifest(
    workspace: Any,
    *,
    app_name: str,
    lease_id: str,
    source_git_sha: str,
    parent_lease_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    deployment_lease.assert_held(
        workspace,
        app_name=app_name,
        lease_id=lease_id,
        source_git_sha=source_git_sha,
        now=now,
    )
    fenced_lease_id = (parent_lease_id or lease_id).strip()
    if parent_lease_id and fenced_lease_id == lease_id:
        raise RuntimeError("UC remediation parent lease must differ from the active lease")
    manifest = validated_manifest(
        journal.recover_operation(
            workspace,
            app_name=app_name,
            lease_id=fenced_lease_id,
        )
    )
    legacy_source_migration = (
        manifest["version"] == LEGACY_MANIFEST_VERSION
        and parent_lease_id is not None
        and fenced_lease_id != lease_id
    )
    if (
        manifest["app_name"] != app_name
        or (
            manifest["source_git_sha"] != source_git_sha
            and not legacy_source_migration
        )
        or manifest["lease"]["lease_id"] != fenced_lease_id
        or stopped_app_identity(workspace, app_name) != manifest["app_identity"]
    ):
        raise RuntimeError("UC remediation recovered manifest identity drifted")
    return manifest


def reauthorize_manifest(
    workspace: Any,
    account: Any,
    *,
    original_manifest: dict[str, Any],
    policy_json: str,
    app_name: str,
    application_id: str,
    expected_inventory_principal: str,
    expected_account_id: str,
    expected_account_client_id: str,
    mip_catalog: str,
    lease_id: str,
    source_git_sha: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    original = validated_manifest(original_manifest)
    policy = parse_foreign_catalog_binding_policy(policy_json)
    if (
        (
            original["source_git_sha"] != source_git_sha
            and original["version"] != LEGACY_MANIFEST_VERSION
        )
        or original["policy"] != policy_payload(policy)
        or original["app_name"] != app_name
        or original["mip_catalog"] != mip_catalog
        or original["actor"] != expected_inventory_principal
        or original["account_identity"]["account_id"] != expected_account_id
        or original["account_identity"]["application_id"].casefold()
        != expected_account_client_id.casefold()
    ):
        raise RuntimeError("UC remediation recovery inputs differ from the signed parent")
    journal.assert_operation(
        workspace,
        manifest=original,
        app_name=app_name,
        lease_id=str(original["lease"]["lease_id"]),
    )
    current = now or datetime.now(UTC)
    lease = deployment_lease.assert_held(
        workspace,
        app_name=app_name,
        lease_id=lease_id,
        source_git_sha=source_git_sha,
        now=current,
    )
    if str(lease["writer_application_id"]).casefold() != application_id.casefold():
        raise RuntimeError("UC remediation recovery lease writer is not the runtime")
    parent_lease = original["lease"]
    if (
        lease["lease_id"] == parent_lease["lease_id"]
        or lease["chain_id"] != parent_lease["chain_id"]
        or lease["recovery_root_lease_id"] != parent_lease["recovery_root_lease_id"]
        or lease["generation_id"] == parent_lease["generation_id"]
        or int(lease["generation_seq"]) <= int(parent_lease["generation_seq"])
    ):
        raise RuntimeError("UC remediation recovery lease does not descend from the signed parent")
    approved_ids = {
        workspace_id for item in policy.values() for workspace_id, _binding_type in item.bindings
    }
    boundary = boundary_evidence(
        workspace,
        account,
        app_name=app_name,
        application_id=application_id,
        expected_inventory_principal=expected_inventory_principal,
        expected_account_id=expected_account_id,
        expected_account_client_id=expected_account_client_id,
        approved_workspace_ids=approved_ids,
    )
    expected_static_boundary = {
        key: original[key]
        for key in (
            "app_identity",
            "metastore_id",
            "mip_workspace_id",
            "metastore_workspace_ids",
            "account_identity",
        )
    }
    current_static_boundary = {
        key: boundary[key] for key in expected_static_boundary
    }
    same_runtime = (
        str(original["runtime_identity"]["application_id"]).casefold()
        == application_id.casefold()
    )
    original_runtime = cast(
        dict[str, object],
        original["runtime_identity"],
    )
    current_runtime = cast(
        dict[str, object],
        boundary["runtime_identity"],
    )
    same_runtime_boundary = (
        current_runtime["account_scim_id"] == original_runtime["scim_id"]
        and current_runtime["account_display_name"] == original_runtime["display_name"]
        if original["version"] == LEGACY_MANIFEST_VERSION
        else current_runtime == original_runtime
    )
    if (
        current_static_boundary != expected_static_boundary
        or (same_runtime and not same_runtime_boundary)
    ):
        raise RuntimeError("UC remediation recovery identity boundary drifted")
    for prestate in original["prestate"]:
        name = str(prestate["catalog"])
        desired = desired_snapshot(prestate, policy[name])
        state_kind(
            snapshot(
                workspace,
                name,
                mip_workspace_id=str(original["mip_workspace_id"]),
            ),
            pre=prestate,
            desired=desired,
        )
    lease_expiry = parse_timestamp(lease["expires_at"], "lease expiration")
    expires = min(lease_expiry, current + MANIFEST_TTL)
    if expires - current < MINIMUM_CHANGE_WINDOW:
        raise RuntimeError("UC remediation recovery lease change window is too short")
    unsigned = {
        key: value
        for key, value in original.items()
        if key not in ATTESTATION_FIELDS | {"manifest_sha256"}
    }
    unsigned.update(
        {
            "version": MANIFEST_VERSION,
            "operation_id": str(uuid4()),
            "created_at": current.isoformat(),
            "expires_at": expires.isoformat(),
            "source_git_sha": source_git_sha,
            "parent_manifest_sha256": journal.digest(original),
            "lease": lease_evidence(lease),
            "runtime_identity": boundary["runtime_identity"],
        }
    )
    return validated_manifest(_seal_manifest(unsigned))
