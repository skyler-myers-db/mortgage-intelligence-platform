"""Identity roles and structured results for M2M provisioning."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

DEFAULT_ADMIN_GROUP = "mip-admin"
DEFAULT_LAKEBASE_INSTANCE = "mip-app-state"

IdentityRole = Literal["normal", "operator2", "admin", "verifier"]


@dataclass(frozen=True)
class IdentityDefaults:
    sp_name: str
    client_id_secret_name: str
    client_secret_secret_name: str
    app_url_secret_name: str | None
    group_name: str | None
    grant_can_use: bool
    lakebase_instance: str | None


IDENTITY_DEFAULTS: dict[IdentityRole, IdentityDefaults] = {
    "normal": IdentityDefaults(
        sp_name="mip-nightly-ci-sp",
        client_id_secret_name="DATABRICKS_CLIENT_ID",
        client_secret_secret_name="DATABRICKS_CLIENT_SECRET",
        app_url_secret_name="MIP_APP_URL",
        group_name=None,
        grant_can_use=True,
        lakebase_instance=None,
    ),
    "operator2": IdentityDefaults(
        sp_name="mip-nightly-operator2-ci-sp",
        client_id_secret_name="DATABRICKS_OPERATOR2_CLIENT_ID",
        client_secret_secret_name="DATABRICKS_OPERATOR2_CLIENT_SECRET",
        app_url_secret_name=None,
        group_name=None,
        grant_can_use=True,
        lakebase_instance=None,
    ),
    "admin": IdentityDefaults(
        sp_name="mip-nightly-admin-ci-sp",
        client_id_secret_name="DATABRICKS_ADMIN_CLIENT_ID",
        client_secret_secret_name="DATABRICKS_ADMIN_CLIENT_SECRET",
        app_url_secret_name=None,
        group_name=DEFAULT_ADMIN_GROUP,
        grant_can_use=True,
        lakebase_instance=None,
    ),
    "verifier": IdentityDefaults(
        sp_name="mip-ai-gateway-verifier-ci-sp",
        client_id_secret_name="DATABRICKS_VERIFIER_CLIENT_ID",
        client_secret_secret_name="DATABRICKS_VERIFIER_CLIENT_SECRET",
        app_url_secret_name=None,
        group_name=None,
        grant_can_use=False,
        lakebase_instance=DEFAULT_LAKEBASE_INSTANCE,
    ),
}


def configured_identity_client_ids() -> dict[IdentityRole, str]:
    """Return non-empty role-owned client IDs from the provisioning environment."""
    configured: dict[IdentityRole, str] = {}
    for role, defaults in IDENTITY_DEFAULTS.items():
        client_id = os.environ.get(defaults.client_id_secret_name, "").strip()
        if client_id:
            configured[role] = client_id
    return configured


def validate_identity_role_binding(
    *,
    identity_role: IdentityRole,
    sp_name: str,
    expected_application_id: str | None,
    client_id_secret_name: str,
    client_secret_secret_name: str,
    app_url_secret_name: str | None,
    configured_client_ids: Mapping[IdentityRole, str] | None = None,
) -> str | None:
    """Bind one role to its reserved principal, client ID, and secret sinks."""
    defaults = IDENTITY_DEFAULTS[identity_role]
    if sp_name != defaults.sp_name:
        raise ValueError(
            f"--identity-role {identity_role} is bound to reserved service principal "
            f"{defaults.sp_name!r}; --sp-name may not select another identity"
        )
    expected_sinks = (
        defaults.client_id_secret_name,
        defaults.client_secret_secret_name,
        defaults.app_url_secret_name,
    )
    actual_sinks = (
        client_id_secret_name,
        client_secret_secret_name,
        app_url_secret_name,
    )
    if actual_sinks != expected_sinks:
        raise ValueError(
            f"--identity-role {identity_role} may write only its role-owned GitHub secret sinks"
        )

    configured_source = (
        configured_identity_client_ids()
        if configured_client_ids is None
        else configured_client_ids
    )
    configured = {
        role: str(client_id).strip()
        for role, client_id in configured_source.items()
        if str(client_id).strip()
    }
    owners_by_client_id: dict[str, list[IdentityRole]] = {}
    for role, client_id in configured.items():
        owners_by_client_id.setdefault(client_id, []).append(role)
    if any(len(owners) > 1 for owners in owners_by_client_id.values()):
        raise ValueError("Configured M2M role client IDs must be distinct")

    supplied = (expected_application_id or "").strip() or None
    authoritative = configured.get(identity_role)
    if supplied and authoritative and supplied != authoritative:
        raise ValueError(
            f"--expected-application-id does not match the configured {identity_role} client ID"
        )
    resolved = supplied or authoritative
    if resolved is not None:
        for role, client_id in configured.items():
            if role != identity_role and client_id == resolved:
                raise ValueError(
                    f"--expected-application-id is reserved for the {role} identity role"
                )
    return resolved


@dataclass
class ProvisionResult:
    """Provisioning result without the one-shot client secret."""

    sp_id: str
    sp_application_id: str
    sp_display_name: str
    created_sp: bool
    granted_can_use: bool
    group_name: str | None
    added_to_group: bool
    lakebase_instance: str | None
    created_lakebase_role: bool
    gateway_endpoint: str | None
    granted_can_query: bool
    warehouse_id: str | None
    granted_warehouse_can_use: bool
    client_id: str
    secret_minted: bool
    secret_written_to_gh: bool
    gh_repo: str | None
