#!/usr/bin/env python3
"""Read-only check of the App's campaign-treatment grant boundary.

``converge_campaign_treatment_access --check`` lands here. It reads the same
authoritative Unity Catalog state the converger reads (identity, metastore,
object presence, direct and effective grants) and reports the diff the
converger would apply, without issuing any SQL statement or grants update.

The report is symbolic: securables are named by role (catalog, audit schema,
treatment table) and the principal is "the App", so the output carries no
host, catalog, principal or other per-client identifier.

The approved-owner policy is not evaluated here. Its group-exclusion proof
mints a short-lived OAuth credential for the App identity, which a read-only
mode must not do; the converger and the UC owner-policy probe keep that proof.
"""

from __future__ import annotations

from databricks.sdk import WorkspaceClient
from tools.databricks.converge_campaign_treatment_access import (
    _ALL_PRIVILEGES,
    _SAFE_METASTORE_PRIVILEGES,
    Mode,
    _effective_privileges,
    _identity_context,
    _object_presence,
    direct_privileges,
    governed_securables,
    plan_securable,
    reviewed_privileges,
)
from tools.databricks.workspace_auth import deployment_workspace_client

_PREFIX = "[mip-treatment-access]"


def _names(privileges: list[str] | set[str]) -> str:
    return ", ".join(sorted(privileges))


def check_campaign_treatment_access(
    *,
    catalog: str,
    principal: str,
    mode: Mode,
    workspace: WorkspaceClient | None = None,
) -> list[str]:
    """Return symbolic drift lines; an empty list means the boundary is converged."""

    if mode not in {"quiesce", "runtime"}:
        raise ValueError(f"Unsupported access convergence mode: {mode!r}")
    principal_name = principal.strip()
    if not principal_name or principal_name != principal or "`" in principal_name:
        raise ValueError("principal must be canonical and contain no backticks")
    securables = governed_securables(catalog)
    client = workspace or deployment_workspace_client()
    _identity_context(client, principal_name)

    drift: list[str] = []
    assignment = client.metastores.current()
    metastore_id = str(getattr(assignment, "metastore_id", "") or "").strip()
    if not metastore_id:
        raise RuntimeError("Current workspace has no authoritative metastore identifier")
    forbidden = (
        _effective_privileges(
            client,
            securable_type="metastore",
            full_name=metastore_id,
            principal=principal_name,
        )
        - _SAFE_METASTORE_PRIVILEGES
    )
    if forbidden:
        drift.append(f"metastore: forbidden privileges {_names(forbidden)} (convergence refuses)")

    presence = _object_presence(client, catalog=securables[0].full_name)
    for securable, present in zip(securables, presence, strict=True):
        if present is None:
            if mode == "runtime":
                drift.append(f"{securable.label}: absent (runtime convergence refuses)")
            # Absent parents imply absent children; quiesce has nothing to remove.
            break
        reviewed = reviewed_privileges(securable.securable_type, mode)
        direct = direct_privileges(
            client,
            securable_type=securable.securable_type,
            full_name=securable.full_name,
            principal=principal_name,
        )
        grant, revoke, retained = plan_securable(direct, reviewed)
        if _ALL_PRIVILEGES in revoke:
            drift.append(
                f"{securable.label}: revoke ALL_PRIVILEGES first "
                "(it also drops the reviewed privileges it implies, which are re-granted)"
            )
            revoke = [name for name in revoke if name != _ALL_PRIVILEGES]
            grant = sorted(set(grant) | (set(reviewed) - {_ALL_PRIVILEGES}))
        if grant:
            drift.append(f"{securable.label}: grant {_names(grant)}")
        if revoke:
            drift.append(f"{securable.label}: revoke {_names(revoke)}")
        if retained:
            drift.append(
                f"{securable.label}: direct {_names(retained)} is outside ALL PRIVILEGES "
                "(convergence leaves it and refuses)"
            )
        effective = _effective_privileges(
            client,
            securable_type=securable.securable_type,
            full_name=securable.full_name,
            principal=principal_name,
        )
        inherited = effective - direct - reviewed
        if inherited:
            drift.append(
                f"{securable.label}: inherited {_names(inherited)} "
                "(convergence cannot remove it and refuses)"
            )
    return drift


def run_check(*, catalog: str, principal: str, mode: Mode) -> int:
    drift = check_campaign_treatment_access(catalog=catalog, principal=principal, mode=mode)
    print(
        f"{_PREFIX} owner policy is not evaluated read-only "
        "(its group-exclusion proof mints a credential)"
    )
    if not drift:
        print(f"{_PREFIX} check passed mode={mode}: App grants equal the reviewed set")
        return 0
    for line in drift:
        print(f"{_PREFIX} drift mode={mode}: {line}")
    return 1
