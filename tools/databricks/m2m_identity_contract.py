"""Identity roles and structured results for M2M provisioning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DEFAULT_ADMIN_GROUP = "mip-admin"
DEFAULT_LAKEBASE_INSTANCE = "mip-app-state"

IdentityRole = Literal["normal", "admin", "verifier"]


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
