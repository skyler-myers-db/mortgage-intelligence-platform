"""The profile's cross-field refusals (plan §5 and the M1-PR1 validator list).

Each rule returns value-free lines that lead with the field they name. A rule
skips a ``<placeholder>`` operand: the template is checked for structure, and
``placeholder_refusals`` refuses every placeholder outside a template check.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, SecretStr

from backend.schemas.lender_identity import _SUMMIT_DEMO_NMLS_ID, _SUMMIT_LENDER_NAME
from tools.clients.shapes import (
    CLOUDS,
    STORAGE_ROOT,
    host_workspace_id,
    https_host,
    is_placeholder,
    normalize_principal,
)

if TYPE_CHECKING:
    from tools.clients.json_fields import LenderIdentity
    from tools.clients.schema import ClientProfile

CATALOG = "mip"
# The demo identity, from the backend's lender registry so the two cannot drift.
SUMMIT_LENDER_NAME = _SUMMIT_LENDER_NAME
SUMMIT_DEMO_NMLS_ID = _SUMMIT_DEMO_NMLS_ID
SUMMIT_TENANT_ID = "summit"
RESERVED_ROLES = ("deployer", "jobs", "validator")
AGENTIC_ROLES = ("verifier", "runtime", "proxy")
AGENTIC_SECRETS = (
    "verifier_client_secret",
    "agent_runtime_client_secret",
    "agent_proxy_credential_bundle",
    "gateway_model_attestation_signing_key",
)
EMAIL_LISTS = ("admin_emails", "approver_emails", "auditor_emails")
IDENTITY_LISTS = ("admin_identities", "approver_identities", "auditor_identities")
DEMO_FLAGS = ("demo_first_party_feeds", "presenter_mode")


def _real(value: str | None) -> bool:
    return value is not None and not is_placeholder(value)


def _strings(value: object) -> list[str]:
    if isinstance(value, SecretStr):
        return [value.get_secret_value()]
    if isinstance(value, str):
        return [value]
    if isinstance(value, BaseModel):
        return _strings(value.model_dump())
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, list | tuple):
        return [text for item in value for text in _strings(item)]
    return []


def placeholder_refusals(profile: ClientProfile) -> list[str]:
    return [
        f"{profile.env_name(attribute)}: holds a <placeholder>; set a real value"
        for attribute in type(profile).model_fields
        if any(is_placeholder(text) for text in _strings(getattr(profile, attribute)))
    ]


def catalog_refusals(profile: ClientProfile) -> list[str]:
    if not _real(profile.default_catalog) or profile.default_catalog == CATALOG:
        return []
    return [
        "MIP_DEFAULT_CATALOG: must be mip; the SQL still names the catalog "
        "literally, so a rename is refused until the catalog-label slice lands"
    ]


def cloud_refusals(profile: ClientProfile) -> list[str]:
    if profile.cloud not in CLOUDS:
        return []
    suffix, account_host, scheme = CLOUDS[profile.cloud]
    lines = []
    if _real(profile.databricks_host):
        hostname = https_host(profile.databricks_host)
        if not hostname.endswith(suffix) or hostname == account_host:
            lines.append(f"DATABRICKS_HOST: must be a workspace host on {profile.cloud} (*{suffix})")
        embedded = host_workspace_id(hostname) if profile.cloud != "aws" else None
        if embedded and _real(profile.expect_workspace_id) and embedded != profile.expect_workspace_id:
            lines.append("MIP_EXPECT_WORKSPACE_ID: must equal the id inside DATABRICKS_HOST")
    if _real(profile.account_host) and profile.account_host != f"https://{account_host}":
        lines.append(f"DATABRICKS_ACCOUNT_HOST: must be the account console host of {profile.cloud}")
    if _real(profile.catalog_storage_root):
        match = STORAGE_ROOT.fullmatch(profile.catalog_storage_root or "")
        if match and match.group("scheme") != scheme:
            lines.append(f"MIP_UC_CATALOG_STORAGE_ROOT: must be a {scheme}:// root on {profile.cloud}")
    return lines


def _is_summit(identity: LenderIdentity) -> bool:
    """Any one part of the demo identity: its name in any case, NMLS id or tenant."""

    return (
        normalize_principal(identity.legal_name) == normalize_principal(SUMMIT_LENDER_NAME)
        or identity.nmls_id == SUMMIT_DEMO_NMLS_ID
        or identity.tenant_id == SUMMIT_TENANT_ID
    )


def install_class_refusals(profile: ClientProfile) -> list[str]:
    """Demo fields stay off a customer install; a lender identity stays off a demo."""

    lines = []
    identity = profile.lender_identity
    if profile.install_class == "customer":
        lines += [
            f"{profile.env_name(attribute)}: is demo-only and refused for a customer install"
            for attribute in DEMO_FLAGS
            if getattr(profile, attribute) == "1"
        ]
        if profile.seed_operator_email is not None:
            lines.append("MIP_SEED_OPERATOR_EMAIL: is demo-only and refused for a customer install")
        if identity is None:
            lines.append("MIP_LENDER_IDENTITY_JSON: is required for a customer install")
        elif _is_summit(identity):
            lines.append(
                "MIP_LENDER_IDENTITY_JSON: the Summit Mortgage demo identity is refused "
                "for a customer install"
            )
    elif profile.install_class == "demo" and identity is not None:
        lines.append(
            "MIP_LENDER_IDENTITY_JSON: is customer-only; a demo install uses the "
            "Summit Mortgage identity"
        )
    return lines


def tier_refusals(profile: ClientProfile) -> list[str]:
    lines = []
    if profile.agentic_tier == "on":
        lines += [
            f"MIP_IDENTITY_IDS_JSON.{role}: is required when MIP_AGENTIC_TIER=on"
            for role in AGENTIC_ROLES
            if getattr(profile.identity_ids, role) is None
        ]
        lines += [
            f"{profile.env_name(attribute)}: is required when MIP_AGENTIC_TIER=on"
            for attribute in AGENTIC_SECRETS
            if getattr(profile, attribute) is None
        ]
    if profile.scheduled_refresh == "on" and not profile.scheduled_refresh_recipients:
        lines.append("MIP_SCHEDULED_REFRESH_RECIPIENTS: is required when MIP_SCHEDULED_REFRESH=on")
    return lines


def reserved_identity_refusals(profile: ClientProfile) -> list[str]:
    """The deployer, jobs and validator identities hold no App role (plan §4.3).

    Each is matched by application id and by its ``<prefix>-<role>`` name, in
    every admin, approver and auditor list and in the seed operator.
    """

    reserved: dict[str, str] = {}
    for role in RESERVED_ROLES:
        reserved[normalize_principal(getattr(profile.identity_ids, role))] = role
        if _real(profile.identity_prefix):
            reserved[normalize_principal(f"{profile.identity_prefix}-{role}")] = role
    lines = []
    for attribute in (*EMAIL_LISTS, *IDENTITY_LISTS, "seed_operator_email"):
        value = getattr(profile, attribute)
        entries = (value,) if isinstance(value, str) else value or ()
        roles = sorted({reserved[key] for key in map(normalize_principal, entries) if key in reserved})
        lines += [f"{profile.env_name(attribute)}: must not name the {role} identity" for role in roles]
    return lines


def cross_field_refusals(profile: ClientProfile, *, allow_placeholders: bool) -> list[str]:
    lines = [] if allow_placeholders else placeholder_refusals(profile)
    for rule in (
        catalog_refusals,
        cloud_refusals,
        install_class_refusals,
        tier_refusals,
        reserved_identity_refusals,
    ):
        lines += rule(profile)
    return lines
