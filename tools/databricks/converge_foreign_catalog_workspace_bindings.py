#!/usr/bin/env python3
"""Safely isolate reviewed foreign catalogs from the MIP staging workspace."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.catalog import CatalogIsolationMode
from tools.databricks import app_deployment_lease as deployment_lease
from tools.databricks import foreign_catalog_binding_catalog as catalog_state
from tools.databricks import foreign_catalog_binding_journal as journal
from tools.databricks import foreign_catalog_binding_manifest as manifest_plan
from tools.databricks.audit_agent_runtime_foreign_uc_access import (
    parse_foreign_catalog_binding_policy,
)
from tools.databricks.uc_owner_policy import account_client_from_env

_ATTESTATION_FIELDS = {
    "attestation_alg",
    "attestation_verify_key",
    "attestation_signature",
}


def _snapshot(
    workspace: Any,
    manifest: dict[str, Any],
    catalog: str,
) -> dict[str, object]:
    return catalog_state.snapshot(
        workspace,
        catalog,
        mip_workspace_id=str(manifest["mip_workspace_id"]),
    )


def _guard(
    workspace: Any,
    account: Any,
    *,
    manifest: dict[str, Any],
    lease_id: str,
    policy_json: str,
    app_name: str,
    application_id: str,
    expected_inventory_principal: str,
    expected_account_id: str,
    expected_account_client_id: str,
    mip_catalog: str,
    now: datetime | None = None,
) -> dict[str, str | int]:
    if manifest["version"] != manifest_plan.MANIFEST_VERSION:
        raise RuntimeError("legacy UC remediation manifest must be reauthorized")
    current = now or datetime.now(UTC)
    if current >= manifest_plan.parse_timestamp(manifest["expires_at"], "manifest expiration"):
        raise RuntimeError("UC remediation signed change window expired")
    if manifest_plan.source_sha(Path.cwd()) != manifest["source_git_sha"]:
        raise RuntimeError("UC remediation source changed")
    policy = parse_foreign_catalog_binding_policy(policy_json)
    if manifest["policy"] != catalog_state.policy_payload(policy):
        raise RuntimeError("UC remediation manifest policy does not match current policy")
    if (
        manifest["app_name"] != app_name
        or manifest["actor"] != expected_inventory_principal
        or manifest["mip_catalog"] != mip_catalog
        or manifest["runtime_identity"]["application_id"].casefold() != application_id.casefold()
        or manifest["account_identity"]["account_id"] != expected_account_id
        or manifest["account_identity"]["application_id"].casefold()
        != expected_account_client_id.casefold()
    ):
        raise RuntimeError("UC remediation immutable input identity drifted")
    lease = deployment_lease.assert_held(
        workspace,
        app_name=app_name,
        lease_id=lease_id,
        source_git_sha=str(manifest["source_git_sha"]),
        now=current,
    )
    sealed_lease = manifest["lease"]
    stable_lease_fields = {
        "lease_id",
        "recovery_root_lease_id",
        "chain_id",
        "holder",
        "writer_application_id",
        "acquired_at",
    }
    if (
        any(lease[field] != sealed_lease[field] for field in stable_lease_fields)
        or int(lease["generation_seq"]) < int(sealed_lease["generation_seq"])
        or str(lease["writer_application_id"]).casefold() != application_id.casefold()
    ):
        raise RuntimeError("UC remediation deployment lease lineage drifted")
    approved_ids = {
        workspace_id for item in policy.values() for workspace_id, _binding_type in item.bindings
    }
    boundary = manifest_plan.boundary_evidence(
        workspace,
        account,
        app_name=app_name,
        application_id=application_id,
        expected_inventory_principal=expected_inventory_principal,
        expected_account_id=expected_account_id,
        expected_account_client_id=expected_account_client_id,
        approved_workspace_ids=approved_ids,
    )
    expected_boundary = {
        key: manifest[key]
        for key in (
            "app_identity",
            "metastore_id",
            "mip_workspace_id",
            "metastore_workspace_ids",
            "account_identity",
            "runtime_identity",
        )
    }
    if boundary != expected_boundary:
        raise RuntimeError("UC remediation live identity boundary drifted")
    journal.assert_operation(
        workspace,
        manifest=manifest,
        app_name=app_name,
        lease_id=lease_id,
    )
    return lease


def _event_common(
    manifest: dict[str, Any],
    *,
    lease: dict[str, str | int],
    index: int,
    catalog: str,
    direction: str,
    phase: str,
    observed: dict[str, object],
    target: dict[str, object],
    prior_event_sha256: str,
) -> dict[str, object]:
    return {
        "version": 1,
        "kind": "foreign-catalog-binding-event",
        "operation_id": manifest["operation_id"],
        "manifest_sha256": journal.digest(manifest),
        "index": index,
        "catalog": catalog,
        "direction": direction,
        "phase": phase,
        "observed_sha256": journal.digest(observed),
        "target_sha256": journal.digest(target),
        "prior_event_sha256": prior_event_sha256,
        "lease": manifest_plan.lease_evidence(lease),
        "at": datetime.now(UTC).isoformat(),
    }


def _validate_event(
    value: object,
    *,
    manifest: dict[str, Any],
    index: int,
    catalog: str,
    direction: str,
    phase: str,
    target: dict[str, object],
    prior_event_sha256: str,
) -> dict[str, Any]:
    event = journal.verify(value)
    required = {
        "version",
        "kind",
        "operation_id",
        "manifest_sha256",
        "index",
        "catalog",
        "direction",
        "phase",
        "observed_sha256",
        "target_sha256",
        "prior_event_sha256",
        "lease",
        "at",
        *_ATTESTATION_FIELDS,
    }
    if (
        set(event) != required
        or event["version"] != 1
        or event["kind"] != "foreign-catalog-binding-event"
        or event["operation_id"] != manifest["operation_id"]
        or event["manifest_sha256"] != journal.digest(manifest)
        or event["index"] != index
        or event["catalog"] != catalog
        or event["direction"] != direction
        or event["phase"] != phase
        or event["target_sha256"] != journal.digest(target)
        or event["prior_event_sha256"] != prior_event_sha256
    ):
        raise RuntimeError("UC remediation durable event identity is invalid")
    manifest_plan.parse_timestamp(event["at"], "event timestamp")
    return event


def _load_or_write_event(
    workspace: Any,
    *,
    manifest: dict[str, Any],
    lease: dict[str, str | int],
    index: int,
    catalog: str,
    direction: str,
    phase: str,
    observed: dict[str, object],
    target: dict[str, object],
    prior_event_sha256: str,
) -> tuple[dict[str, Any], bool]:
    path = journal.event_path(
        str(manifest["app_name"]),
        str(manifest["operation_id"]),
        index=index,
        direction=direction,
        phase=phase,
        catalog=catalog,
    )
    existing = journal.load_event(workspace, path)
    if existing is not None:
        return (
            _validate_event(
                existing,
                manifest=manifest,
                index=index,
                catalog=catalog,
                direction=direction,
                phase=phase,
                target=target,
                prior_event_sha256=prior_event_sha256,
            ),
            True,
        )
    signed = journal.sign(
        _event_common(
            manifest,
            lease=lease,
            index=index,
            catalog=catalog,
            direction=direction,
            phase=phase,
            observed=observed,
            target=target,
            prior_event_sha256=prior_event_sha256,
        )
    )
    journal.upload_once(workspace, path, signed)
    return signed, False


def _record_failure(
    workspace: Any,
    *,
    manifest: dict[str, Any],
    lease: dict[str, str | int],
    index: int,
    catalog: str,
    direction: str,
    error: BaseException,
) -> None:
    attempt_id = str(uuid4())
    record = journal.sign(
        {
            "version": 1,
            "kind": "foreign-catalog-binding-failure",
            "operation_id": manifest["operation_id"],
            "manifest_sha256": journal.digest(manifest),
            "attempt_id": attempt_id,
            "index": index,
            "catalog": catalog,
            "direction": direction,
            "error_type": type(error).__name__,
            "error_sha256": journal.digest(str(error)),
            "lease": manifest_plan.lease_evidence(lease),
            "at": datetime.now(UTC).isoformat(),
        }
    )
    journal.upload_once(
        workspace,
        journal.failure_path(
            str(manifest["app_name"]),
            str(manifest["operation_id"]),
            attempt_id=attempt_id,
            index=index,
            direction=direction,
        ),
        record,
    )


def _apply_catalog(
    workspace: Any,
    account: Any,
    *,
    manifest: dict[str, Any],
    pre: dict[str, object],
    desired: dict[str, object],
    guard_args: dict[str, Any],
) -> None:
    name = str(pre["catalog"])
    current = _snapshot(workspace, manifest, name)
    if catalog_state.state_kind(current, pre=pre, desired=desired) == "desired":
        return
    if current["isolation_mode"] == "OPEN":
        _guard(workspace, account, manifest=manifest, **guard_args)
        if _snapshot(workspace, manifest, name) != current:
            raise RuntimeError(f"catalog {name} changed immediately before isolation")
        workspace.catalogs.update(name, isolation_mode=CatalogIsolationMode.ISOLATED)
        _guard(workspace, account, manifest=manifest, **guard_args)
        current = _snapshot(workspace, manifest, name)
        catalog_state.state_kind(current, pre=pre, desired=desired)
    if current["bindings"] != desired["bindings"]:
        _guard(workspace, account, manifest=manifest, **guard_args)
        if _snapshot(workspace, manifest, name) != current:
            raise RuntimeError(f"catalog {name} changed immediately before binding update")
        catalog_state.converge_bindings(
            workspace,
            catalog=name,
            current=cast(list[dict[str, str]], current["bindings"]),
            desired=cast(list[dict[str, str]], desired["bindings"]),
        )
        _guard(workspace, account, manifest=manifest, **guard_args)
    if _snapshot(workspace, manifest, name) != desired:
        raise RuntimeError(f"catalog {name} failed exact post-state verification")


def _guard_args(
    *,
    lease_id: str,
    policy_json: str,
    app_name: str,
    application_id: str,
    expected_inventory_principal: str,
    expected_account_id: str,
    expected_account_client_id: str,
    mip_catalog: str,
) -> dict[str, Any]:
    return {
        "lease_id": lease_id,
        "policy_json": policy_json,
        "app_name": app_name,
        "application_id": application_id,
        "expected_inventory_principal": expected_inventory_principal,
        "expected_account_id": expected_account_id,
        "expected_account_client_id": expected_account_client_id,
        "mip_catalog": mip_catalog,
    }


def apply_manifest(
    workspace: Any,
    account: Any,
    *,
    manifest: dict[str, Any],
    action: str,
    lease_id: str,
    policy_json: str,
    app_name: str,
    application_id: str,
    expected_inventory_principal: str,
    expected_account_id: str,
    expected_account_client_id: str,
    mip_catalog: str,
) -> None:
    if action not in {"apply", "resume"}:
        raise ValueError("UC remediation apply action is invalid")
    manifest = manifest_plan.validated_manifest(manifest)
    guard_args = _guard_args(
        lease_id=lease_id,
        policy_json=policy_json,
        app_name=app_name,
        application_id=application_id,
        expected_inventory_principal=expected_inventory_principal,
        expected_account_id=expected_account_id,
        expected_account_client_id=expected_account_client_id,
        mip_catalog=mip_catalog,
    )
    lease = _guard(workspace, account, manifest=manifest, **guard_args)
    policy = parse_foreign_catalog_binding_policy(policy_json)
    prestate = list(manifest["prestate"])
    for index, pre in enumerate(prestate):
        name = str(pre["catalog"])
        desired = catalog_state.desired_snapshot(pre, policy[name])
        try:
            current = _snapshot(workspace, manifest, name)
            state = catalog_state.state_kind(current, pre=pre, desired=desired)
            intent_path = journal.event_path(
                app_name,
                str(manifest["operation_id"]),
                index=index,
                direction="apply",
                phase="intent",
                catalog=name,
            )
            existing_intent = journal.load_event(workspace, intent_path)
            if action == "apply" and (existing_intent is not None or state != "prestate"):
                raise RuntimeError("UC remediation apply requires untouched signed pre-state")
            if (
                action == "resume"
                and state != "prestate"
                and existing_intent is None
                and not manifest["parent_manifest_sha256"]
            ):
                raise RuntimeError(f"catalog {name} has unjournaled non-prestate remediation state")
            intent, existed = _load_or_write_event(
                workspace,
                manifest=manifest,
                lease=lease,
                index=index,
                catalog=name,
                direction="apply",
                phase="intent",
                observed=current,
                target=desired,
                prior_event_sha256="",
            )
            if existed != (existing_intent is not None):
                raise RuntimeError("UC remediation intent journal changed concurrently")
            lease = _guard(workspace, account, manifest=manifest, **guard_args)
            converged_path = journal.event_path(
                app_name,
                str(manifest["operation_id"]),
                index=index,
                direction="apply",
                phase="converged",
                catalog=name,
            )
            converged = journal.load_event(workspace, converged_path)
            if converged is not None:
                _validate_event(
                    converged,
                    manifest=manifest,
                    index=index,
                    catalog=name,
                    direction="apply",
                    phase="converged",
                    target=desired,
                    prior_event_sha256=journal.digest(intent),
                )
                if _snapshot(workspace, manifest, name) != desired:
                    raise RuntimeError(f"catalog {name} drifted after signed convergence")
                continue
            _apply_catalog(
                workspace,
                account,
                manifest=manifest,
                pre=pre,
                desired=desired,
                guard_args=guard_args,
            )
            lease = _guard(workspace, account, manifest=manifest, **guard_args)
            post = _snapshot(workspace, manifest, name)
            _load_or_write_event(
                workspace,
                manifest=manifest,
                lease=lease,
                index=index,
                catalog=name,
                direction="apply",
                phase="converged",
                observed=post,
                target=desired,
                prior_event_sha256=journal.digest(intent),
            )
        except BaseException as exc:
            try:
                lease = _guard(
                    workspace,
                    account,
                    manifest=manifest,
                    **guard_args,
                )
                _record_failure(
                    workspace,
                    manifest=manifest,
                    lease=lease,
                    index=index,
                    catalog=name,
                    direction="apply",
                    error=exc,
                )
            except BaseException as journal_exc:
                raise RuntimeError(
                    f"catalog {name} failed and its durable failure record also failed"
                ) from journal_exc
            raise
    _guard(workspace, account, manifest=manifest, **guard_args)
    failures = [
        str(pre["catalog"])
        for pre in prestate
        if _snapshot(workspace, manifest, str(pre["catalog"]))
        != catalog_state.desired_snapshot(pre, policy[str(pre["catalog"])])
    ]
    if failures:
        error = RuntimeError(
            "UC remediation final whole-policy sweep failed: " + ", ".join(failures)
        )
        lease = _guard(workspace, account, manifest=manifest, **guard_args)
        _record_failure(
            workspace,
            manifest=manifest,
            lease=lease,
            index=len(prestate),
            catalog="__whole_policy__",
            direction="apply",
            error=error,
        )
        raise error


def verify_manifest_state(
    workspace: Any,
    account: Any,
    *,
    manifest: dict[str, Any],
    lease_id: str,
    policy_json: str,
    app_name: str,
    application_id: str,
    expected_inventory_principal: str,
    expected_account_id: str,
    expected_account_client_id: str,
    mip_catalog: str,
) -> None:
    manifest = manifest_plan.validated_manifest(manifest)
    guard_args = _guard_args(
        lease_id=lease_id,
        policy_json=policy_json,
        app_name=app_name,
        application_id=application_id,
        expected_inventory_principal=expected_inventory_principal,
        expected_account_id=expected_account_id,
        expected_account_client_id=expected_account_client_id,
        mip_catalog=mip_catalog,
    )
    _guard(workspace, account, manifest=manifest, **guard_args)
    policy = parse_foreign_catalog_binding_policy(policy_json)
    failures = []
    for pre in manifest["prestate"]:
        desired = catalog_state.desired_snapshot(pre, policy[str(pre["catalog"])])
        if _snapshot(workspace, manifest, str(pre["catalog"])) != desired:
            failures.append(str(pre["catalog"]))
    if failures:
        raise RuntimeError("UC remediation desired state is incomplete: " + ", ".join(failures))


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("UC remediation manifest file is invalid") from exc
    return manifest_plan.validated_manifest(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "snapshot",
            "recover-local",
            "reauthorize",
            "apply",
            "resume",
            "verify",
        ),
    )
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--application-id", required=True)
    parser.add_argument("--expected-inventory-principal", required=True)
    parser.add_argument("--expected-account-id", required=True)
    parser.add_argument("--expected-account-client-id", required=True)
    parser.add_argument(
        "--mip-catalog",
        default=os.environ.get("MIP_DEFAULT_CATALOG", "mip"),
    )
    parser.add_argument("--lease-id", required=True)
    parser.add_argument("--parent-lease-id")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out-manifest", type=Path)
    parser.add_argument(
        "--policy-json",
        default=os.environ.get("MIP_UC_FOREIGN_CATALOG_BINDING_POLICY", ""),
    )
    args = parser.parse_args(argv)
    workspace = WorkspaceClient()
    account = account_client_from_env()
    common = {
        "workspace": workspace,
        "account": account,
        "lease_id": args.lease_id,
        "policy_json": args.policy_json,
        "app_name": args.app_name,
        "application_id": args.application_id,
        "expected_inventory_principal": args.expected_inventory_principal,
        "expected_account_id": args.expected_account_id,
        "expected_account_client_id": args.expected_account_client_id,
        "mip_catalog": args.mip_catalog,
    }
    if args.action == "recover-local":
        if args.out_manifest:
            raise RuntimeError("recover-local accepts only the manifest destination")
        sha = manifest_plan.source_sha(Path.cwd())
        try:
            fenced_lease_id = (args.parent_lease_id or args.lease_id).strip()
            fenced = manifest_plan.validated_manifest(
                journal.recover_operation(
                    workspace,
                    app_name=args.app_name,
                    lease_id=fenced_lease_id,
                )
            )
            if journal.operation_completed(
                workspace,
                manifest=fenced,
                app_name=args.app_name,
            ):
                current_policy = manifest_plan.policy_payload(
                    manifest_plan.parse_foreign_catalog_binding_policy(args.policy_json)
                )
                exact_inputs = (
                    fenced["source_git_sha"] == sha
                    and fenced["policy"] == current_policy
                    and fenced["app_name"] == args.app_name
                    and fenced["mip_catalog"] == args.mip_catalog
                    and fenced["actor"] == args.expected_inventory_principal
                    and str(fenced["runtime_identity"]["application_id"]).casefold()
                    == args.application_id.casefold()
                    and fenced["account_identity"]["account_id"]
                    == args.expected_account_id
                    and str(fenced["account_identity"]["application_id"]).casefold()
                    == args.expected_account_client_id.casefold()
                    and manifest_plan.stopped_app_identity(workspace, args.app_name)
                    == fenced["app_identity"]
                )
                if not exact_inputs:
                    print("foreign catalog recovery fence: COMPLETED_STALE")
                    return 5
                print("foreign catalog recovery fence: COMPLETED_EXACT")
            recovered = manifest_plan.recover_persisted_manifest(
                workspace,
                app_name=args.app_name,
                lease_id=args.lease_id,
                source_git_sha=sha,
                parent_lease_id=args.parent_lease_id,
            )
        except journal.ForeignCatalogOperationNotFound:
            print("foreign catalog recovery fence: ABSENT")
            return 3
        manifest_plan.write_exclusive(args.manifest, recovered)
        print(recovered["manifest_sha256"])
        return 0
    if args.parent_lease_id:
        raise RuntimeError(f"{args.action} does not accept --parent-lease-id")
    if args.action == "snapshot":
        if args.out_manifest:
            raise RuntimeError("snapshot does not accept --out-manifest")
        manifest = manifest_plan.create_manifest(
            **common,
            source_git_sha=manifest_plan.source_sha(Path.cwd()),
        )
        manifest_plan.persist_manifest(
            workspace,
            manifest=manifest,
            lease_id=args.lease_id,
        )
        manifest_plan.write_exclusive(args.manifest, manifest)
        print(manifest["manifest_sha256"])
        return 0
    manifest = _load_manifest(args.manifest)
    if args.action == "reauthorize":
        if args.out_manifest is None:
            raise RuntimeError("reauthorize requires --out-manifest")
        replacement = manifest_plan.reauthorize_manifest(
            **common,
            original_manifest=manifest,
            source_git_sha=manifest_plan.source_sha(Path.cwd()),
        )
        manifest_plan.persist_manifest(
            workspace,
            manifest=replacement,
            lease_id=args.lease_id,
        )
        manifest_plan.write_exclusive(args.out_manifest, replacement)
        print(replacement["manifest_sha256"])
        return 0
    if args.out_manifest:
        raise RuntimeError(f"{args.action} does not accept --out-manifest")
    if args.action in {"apply", "resume"}:
        apply_manifest(**common, manifest=manifest, action=args.action)
    else:
        verify_manifest_state(**common, manifest=manifest)
        journal.complete_operation(
            workspace,
            manifest=manifest,
            app_name=args.app_name,
            lease_id=args.lease_id,
        )
    print(f"foreign catalog workspace isolation {args.action}: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
