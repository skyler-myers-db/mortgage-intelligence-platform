#!/usr/bin/env python3
"""Inspect or recover one signed interrupted OAuth credential intent."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from typing import Any

from tools.databricks import oauth_credential_records as records
from tools.databricks.m2m_oauth_github import invalidate_gh_secrets
from tools.databricks.oauth_credential_boundary import (
    held_deployment_credential_assertion,
    held_deployment_credential_recovery_assertion,
)
from tools.databricks.oauth_credential_recovery import (
    orphan_credential_mutation_lease_coordinates,
    recover_oauth_credential_mutation,
    recover_orphan_credential_mutation_lease,
)
from tools.databricks.probe_deadlines import install_probe_deadlines
from tools.databricks.uc_owner_policy import account_client_from_env
from tools.databricks.workspace_auth import deployment_workspace_client

_PROVIDERS = {
    "workspace.service_principal_secrets_proxy",
    "account.service_principal_secrets",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    inspect = subcommands.add_parser("inspect")
    inspect.add_argument("--intent-path", required=True)
    subcommands.add_parser("inspect-orphan-lease")
    orphan = subcommands.add_parser("recover-orphan-lease")
    orphan.add_argument("--confirm-lease-id", required=True)
    orphan.add_argument("--confirm-recovery-root-lease-id", required=True)
    recover = subcommands.add_parser("recover")
    recover.add_argument("--intent-path", required=True)
    recover.add_argument("--confirm-principal-id", required=True)
    recover.add_argument("--confirm-authority-identity", required=True)
    recover.add_argument(
        "--confirm-provider-api",
        required=True,
        choices=sorted(_PROVIDERS),
    )
    return parser


def _canonical_identity(principal: object) -> tuple[str, str]:
    principal_id = records.field(principal, "id")
    application_id = records.field(principal, "application_id")
    if not principal_id or not application_id:
        raise RuntimeError(
            "OAuth credential recovery principal identity is incomplete"
        )
    return principal_id, application_id


def _public_intent(intent: dict[str, object]) -> dict[str, object]:
    return {
        name: intent[name]
        for name in (
            "version",
            "outer_app_name",
            "source_git_sha",
            "label",
            "principal_id",
            "authority_scope",
            "authority_identity",
            "provider_api",
            "operation_mode",
            "sink_descriptor",
            "sink_repository",
            "sink_secret_names",
            "sink_atomic_credential_bundle",
            "retirement_mode",
            "credential_lifetime_seconds",
            "before_credential_ids",
        )
    }


def execute(
    argv: Sequence[str] | None = None,
    *,
    workspace_factory: Callable[[], Any] = deployment_workspace_client,
    account_factory: Callable[[], Any] = account_client_from_env,
) -> dict[str, object]:
    args = _parser().parse_args(argv)
    workspace = workspace_factory()
    if args.command == "inspect-orphan-lease":
        coordinate = orphan_credential_mutation_lease_coordinates(workspace)
        return {
            "lease_id": coordinate.lease_id,
            "recovery_root_lease_id": coordinate.recovery_root_lease_id,
            "source_git_sha": coordinate.source_git_sha,
            "expected_intent_path": coordinate.expected_intent_path,
            "intent_present": coordinate.intent_present,
        }
    if args.command == "recover-orphan-lease":
        outer_fence = held_deployment_credential_assertion(workspace)
        orphan_result = recover_orphan_credential_mutation_lease(
            workspace,
            outer_fence=outer_fence,
            expected_lease_id=args.confirm_lease_id,
            expected_recovery_root_lease_id=(
                args.confirm_recovery_root_lease_id
            ),
        )
        return {
            "lease_id": orphan_result.lease_id,
            "recovery_root_lease_id": orphan_result.recovery_root_lease_id,
            "expected_intent_path": orphan_result.expected_intent_path,
            "status": "released_without_intent",
        }
    intent, _encoded = records.read_json(workspace, args.intent_path)
    records.validate_intent(args.intent_path, intent)
    if args.command == "inspect":
        return _public_intent(intent)
    principal_id = args.confirm_principal_id.strip()
    authority_identity = args.confirm_authority_identity.strip()
    provider_api = args.confirm_provider_api.strip()
    if (
        principal_id != records.field(intent, "principal_id")
        or authority_identity != records.field(intent, "authority_identity")
        or provider_api != records.field(intent, "provider_api")
    ):
        raise RuntimeError(
            "OAuth credential recovery confirmations do not match the signed intent"
        )
    if provider_api == "workspace.service_principal_secrets_proxy":
        principal = workspace.service_principals.get(principal_id)
        observed_id, observed_application_id = _canonical_identity(principal)
        credential_api = workspace.service_principal_secrets_proxy
    elif provider_api == "account.service_principal_secrets":
        account = account_factory()
        principal = account.service_principals.get(principal_id)
        observed_id, observed_application_id = _canonical_identity(principal)
        credential_api = account.service_principal_secrets
    else:  # pragma: no cover - argparse and signed-intent validation close it
        raise RuntimeError("OAuth credential recovery provider is unsupported")
    if (observed_id, observed_application_id) != (
        principal_id,
        authority_identity,
    ):
        raise RuntimeError(
            "OAuth credential recovery provider identity does not match the intent"
        )
    outer_fence = held_deployment_credential_recovery_assertion(
        workspace,
        intent_path=args.intent_path,
    )
    result = recover_oauth_credential_mutation(
        workspace,
        intent_path=args.intent_path,
        outer_fence=outer_fence,
        principal_id=principal_id,
        authority_identity=authority_identity,
        provider_api=provider_api,
        list_credentials=lambda: credential_api.list(principal_id),
        delete_credential=lambda credential_id: credential_api.delete(
            principal_id,
            credential_id,
        ),
        invalidate_sink=invalidate_gh_secrets,
    )
    return {
        "intent_path": result.intent_path,
        "principal_id": result.principal_id,
        "revoked_credential_id": result.revoked_credential_id,
        "sink_disposition": result.sink_disposition,
        "status": result.outcome,
    }


def main(argv: Sequence[str] | None = None) -> int:
    # Same wedge class as the step-4 identity probe (2026-08-09): a recovery
    # run stalled 13 minutes in an accounts-API read with no deadline.
    install_probe_deadlines(label="credential-recovery")
    print(json.dumps(execute(argv), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
