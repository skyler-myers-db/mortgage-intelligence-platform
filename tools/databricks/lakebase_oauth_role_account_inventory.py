"""Bounded account-SCIM inventory for temporary Lakebase identities."""

from __future__ import annotations

import re
import time
from typing import Any
from uuid import UUID

from databricks.sdk.errors import NotFound

_SAFE_DISPLAY_PREFIX = re.compile(r"[A-Za-z0-9_.-]+")


def exact_account_principals_by_display_prefix(
    account_client: Any,
    *,
    display_prefix: str,
    attempts: int = 15,
) -> list[Any]:
    """Resolve a target-derived LIST result to immutable account ids once."""

    if not display_prefix or _SAFE_DISPLAY_PREFIX.fullmatch(display_prefix) is None or attempts < 1:
        raise RuntimeError("temporary Lakebase account inventory prefix is invalid")
    last_not_found: NotFound | None = None
    for attempt in range(attempts):
        candidates: dict[str, Any] = {}
        try:
            listed_principals = list(
                account_client.service_principals.list(filter=f'displayName sw "{display_prefix}"')
            )
            for listed in listed_principals:
                listed_display = str(getattr(listed, "display_name", "") or "")
                if not listed_display.startswith(display_prefix):
                    continue
                listed_contract = tuple(
                    str(getattr(listed, field, "") or "")
                    for field in ("id", "application_id", "display_name")
                )
                if not all(listed_contract):
                    raise RuntimeError(
                        "temporary Lakebase account inventory identity is incomplete"
                    )
                principal_id = listed_contract[0]
                if principal_id in candidates:
                    raise RuntimeError(
                        "temporary Lakebase account inventory duplicated an immutable id"
                    )
                exact = account_client.service_principals.get(principal_id)
                exact_contract = tuple(
                    str(getattr(exact, field, "") or "")
                    for field in ("id", "application_id", "display_name")
                )
                if exact_contract != listed_contract:
                    raise RuntimeError("temporary Lakebase account inventory changed")
                candidates[principal_id] = exact
        except NotFound as exc:
            last_not_found = exc
            if attempt + 1 < attempts:
                time.sleep(1)
            continue
        return list(candidates.values())
    raise RuntimeError("temporary Lakebase account inventory did not stabilize") from last_not_found


def assert_exact_account_marker_contract(principal: Any) -> tuple[str, str, str]:
    """Reject authorization, relationship, or immutable-field drift."""

    immutable = tuple(
        str(getattr(principal, field, "") or "")
        for field in ("id", "application_id", "display_name", "external_id")
    )
    if (
        not all(immutable[:3])
        or immutable[3]
        or any(getattr(principal, field, None) for field in ("groups", "roles", "entitlements"))
    ):
        raise RuntimeError("temporary Lakebase account marker contract drifted")
    return immutable[0], immutable[1], immutable[2]


def assert_no_workspace_app_binding(
    workspace_client: Any,
    *,
    application_ids: set[str],
) -> None:
    """Reject either a temporary identity or marker bound to a Databricks App."""

    if not application_ids or any(not value for value in application_ids):
        raise RuntimeError("temporary Lakebase App-binding contract is incomplete")
    if any(
        str(getattr(app, "service_principal_client_id", "") or "") in application_ids
        for app in workspace_client.apps.list()
    ):
        raise RuntimeError("temporary Lakebase account marker is bound to an App")


def prove_account_application_id_absent(
    account_client: Any,
    *,
    application_id: str,
    attempts: int = 3,
) -> None:
    """Reject application-id-only cleanup authority.

    The account SCIM ``applicationId`` filter is an inventory hint, not an
    absence proof: live account reads have omitted a principal that remained
    reachable through its immutable SCIM id.  A matching row is still useful
    evidence that cleanup is incomplete, but an empty result must retain the
    signed marker until recovery also has the immutable principal id.
    """

    try:
        canonical_application_id = str(UUID(application_id))
    except ValueError as exc:
        raise RuntimeError("temporary Lakebase account application id is invalid") from exc
    if canonical_application_id != application_id or attempts < 3:
        raise RuntimeError("temporary Lakebase account absence contract is invalid")
    for attempt in range(attempts):
        matches = list(
            account_client.service_principals.list(
                filter=f'applicationId eq "{canonical_application_id}"'
            )
        )
        if matches:
            # Do not follow a display name.  A caller must recover the immutable
            # id through the target-derived signed-marker inventory instead.
            raise RuntimeError("temporary Lakebase account principal remains present")
        if attempt + 1 < attempts:
            time.sleep(1)
    raise RuntimeError(
        "temporary Lakebase account principal absence requires its immutable SCIM id"
    )
