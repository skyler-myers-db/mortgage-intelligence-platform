"""Select the control plane that mints temporary target-identity probe secrets.

``MIP_TEMP_CREDENTIAL_PLANE`` unset, empty or ``account`` keeps the original
path: the account client's ``service_principal_secrets`` keyed by the account
SCIM id. ``workspace`` lists, creates and deletes through the deployer
workspace client's ``service_principal_secrets_proxy`` keyed by the workspace
SCIM id, with a matching ``workspace`` mutation context so quarantine recovery
(``oauth_credential_recovery_cli``) resolves the same provider. Every other
value fails closed.

Why a workspace mode exists: on the 2026-08-03/04 CI deploys, 300-second probe
secrets created on the account plane were returned by the create call yet
never appeared in the account listing, so every exact-inventory mint failed
as "incomplete or ambiguous". Workspace-plane secrets minted from runners
persisted. The exact before/after inventory contract is identical on both
planes; only the provider changes.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

TEMP_CREDENTIAL_PLANE_ENV = "MIP_TEMP_CREDENTIAL_PLANE"
ACCOUNT_PLANE = "account"
WORKSPACE_PLANE = "workspace"
_PROVIDER_APIS = {
    ACCOUNT_PLANE: "account.service_principal_secrets",
    WORKSPACE_PLANE: "workspace.service_principal_secrets_proxy",
}


@dataclass(frozen=True)
class TemporaryCredentialApi:
    """One plane's exact list/create/delete calls for a single principal."""

    authority_scope: str
    provider_api: str
    principal_id: str
    list_credentials: Callable[[], Iterable[object]]
    create_credential: Callable[[], object]
    delete_credential: Callable[[str], None]


def temporary_credential_plane(environ: Mapping[str, str] | None = None) -> str:
    """Return ``account`` (the default) or ``workspace``; anything else fails closed."""

    env = os.environ if environ is None else environ
    value = str(env.get(TEMP_CREDENTIAL_PLANE_ENV) or "").strip()
    if not value:
        return ACCOUNT_PLANE
    if value not in _PROVIDER_APIS:
        raise RuntimeError(
            f"{TEMP_CREDENTIAL_PLANE_ENV} must be '{ACCOUNT_PLANE}' or '{WORKSPACE_PLANE}'"
        )
    return value


def temporary_credential_api(
    *,
    account: Any,
    deployer_workspace: Any | None,
    account_principal_id: str,
    workspace_principal_id: str,
    lifetime: str,
) -> TemporaryCredentialApi:
    """Bind the selected plane's secret calls to the target principal."""

    plane = temporary_credential_plane()
    if plane == ACCOUNT_PLANE:
        principal = account_principal_id

        def account_delete(credential_id: str) -> None:
            account.service_principal_secrets.delete(principal, credential_id)

        return TemporaryCredentialApi(
            authority_scope=ACCOUNT_PLANE,
            provider_api=_PROVIDER_APIS[ACCOUNT_PLANE],
            principal_id=principal,
            list_credentials=lambda: account.service_principal_secrets.list(principal),
            create_credential=lambda: account.service_principal_secrets.create(
                principal,
                lifetime=lifetime,
            ),
            delete_credential=account_delete,
        )
    if deployer_workspace is None:
        raise RuntimeError(
            "the workspace temporary-credential plane requires the deployer workspace client"
        )
    principal = workspace_principal_id

    def workspace_delete(credential_id: str) -> None:
        deployer_workspace.service_principal_secrets_proxy.delete(principal, credential_id)

    return TemporaryCredentialApi(
        authority_scope=WORKSPACE_PLANE,
        provider_api=_PROVIDER_APIS[WORKSPACE_PLANE],
        principal_id=principal,
        list_credentials=lambda: deployer_workspace.service_principal_secrets_proxy.list(
            principal
        ),
        create_credential=lambda: deployer_workspace.service_principal_secrets_proxy.create(
            principal,
            lifetime=lifetime,
        ),
        delete_credential=workspace_delete,
    )
