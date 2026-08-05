"""Exact signed SCIM contract for one-use Lakebase bootstrap principals."""

from __future__ import annotations

from typing import Any

from databricks.sdk.errors import NotFound
from tools.databricks.lakebase_oauth_role_account_inventory import (
    assert_exact_account_marker_contract,
    assert_no_workspace_app_binding,
    exact_account_principals_by_display_prefix,
)
from tools.databricks.lakebase_oauth_role_account_principal import (
    assert_account_workspace_assignment_boundary,
)
from tools.databricks.lakebase_oauth_role_scim_marker import (
    assert_bootstrap_principal_display_name,
    assert_scim_external_id_unset,
    bootstrap_principal_display_prefix,
    is_reserved_bootstrap_display,
)


def exact_bootstrap_principals(
    client: Any,
    *,
    display_name: str,
    external_id: str,
    account_client: Any | None = None,
) -> list[Any]:
    candidates: dict[str, Any] = {}
    workspace_ids: set[str] = set()
    for principal in client.service_principals.list():
        candidate_display = str(getattr(principal, "display_name", "") or "")
        if not is_reserved_bootstrap_display(
            candidate_display,
            reservation_name=display_name,
        ):
            continue
        principal_id = str(getattr(principal, "id", "") or "").strip()
        if not principal_id:
            raise RuntimeError("bootstrap principal inventory returned an identity without id")
        exact = client.service_principals.get(principal_id)
        if str(getattr(exact, "id", "") or "").strip() != principal_id:
            raise RuntimeError("reserved Lakebase bootstrap identity marker is ambiguous")
        if str(getattr(exact, "display_name", "") or "") != candidate_display:
            raise RuntimeError("reserved Lakebase bootstrap identity marker is ambiguous")
        try:
            assert_bootstrap_principal_display_name(
                candidate_display,
                expected_name=display_name,
                ownership_marker=external_id,
            )
            assert_scim_external_id_unset(
                exact,
                label="temporary Lakebase bootstrap principal",
            )
        except RuntimeError as exc:
            raise RuntimeError("reserved Lakebase bootstrap identity marker is ambiguous") from exc
        candidates[principal_id] = exact
        workspace_ids.add(principal_id)
    if len(candidates) > 1:
        raise RuntimeError("reserved Lakebase bootstrap identity marker is duplicated")
    if account_client is not None:
        prefix = bootstrap_principal_display_prefix(display_name)
        account_ids: set[str] = set()
        for exact in exact_account_principals_by_display_prefix(
            account_client,
            display_prefix=prefix,
        ):
            principal_id, application_id, candidate_display = assert_exact_account_marker_contract(
                exact
            )
            account_ids.add(principal_id)
            try:
                assert_bootstrap_principal_display_name(
                    candidate_display,
                    expected_name=display_name,
                    ownership_marker=external_id,
                )
            except RuntimeError as exc:
                raise RuntimeError(
                    "reserved Lakebase account bootstrap identity marker is ambiguous"
                ) from exc
            assert_no_workspace_app_binding(
                client,
                application_ids={application_id},
            )
            assert_account_workspace_assignment_boundary(
                account_client,
                client,
                principal_id=principal_id,
                application_id=application_id,
                display_name=candidate_display,
                expected_workspace_active=True,
            )
            existing = candidates.get(principal_id)
            if existing is not None:
                existing_contract = tuple(
                    str(getattr(existing, field, "") or "")
                    for field in ("id", "application_id", "display_name", "external_id")
                )
                if existing_contract != (
                    principal_id,
                    application_id,
                    candidate_display,
                    "",
                ):
                    raise RuntimeError("reserved Lakebase bootstrap identity marker is ambiguous")
            else:
                candidates[principal_id] = exact
        if workspace_ids - account_ids:
            raise RuntimeError("reserved Lakebase account bootstrap identity inventory changed")
    if len(candidates) > 1:
        raise RuntimeError("reserved Lakebase bootstrap identity marker is duplicated")
    return list(candidates.values())


def assert_bootstrap_principal_contract(
    client: Any,
    principal: Any,
    *,
    display_name: str,
    external_id: str,
    account_client: Any | None = None,
) -> tuple[str, str]:
    principal_id = str(getattr(principal, "id", "") or "").strip()
    if not principal_id:
        raise RuntimeError("temporary Lakebase bootstrap principal has no immutable id")
    try:
        exact = client.service_principals.get(principal_id)
    except NotFound:
        if account_client is None:
            raise
        exact = account_client.service_principals.get(principal_id)
    application_id = str(getattr(exact, "application_id", "") or "").strip()
    try:
        assert_bootstrap_principal_display_name(
            str(getattr(exact, "display_name", "") or ""),
            expected_name=display_name,
            ownership_marker=external_id,
        )
        assert_scim_external_id_unset(
            exact,
            label="temporary Lakebase bootstrap principal",
        )
    except RuntimeError as exc:
        raise RuntimeError("temporary Lakebase bootstrap principal contract drifted") from exc
    if not application_id or any(
        getattr(exact, field, None) for field in ("groups", "roles", "entitlements")
    ):
        raise RuntimeError("temporary Lakebase bootstrap principal contract drifted")
    assert_no_workspace_app_binding(client, application_ids={application_id})
    return principal_id, application_id
