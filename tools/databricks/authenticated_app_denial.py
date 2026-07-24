"""Prove a Databricks App denial with one independently authenticated bearer."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

import requests

_APP_PERMISSION_LEVELS = {"CAN_MANAGE", "CAN_USE"}


def _text(value: object, name: str) -> str:
    raw = value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)
    return str(getattr(raw, "value", raw) or "").strip()


def _https_origin(value: str, *, suffixes: tuple[str, ...], label: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or not parsed.hostname
        or not parsed.hostname.endswith(suffixes)
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError(f"{label} is not a reviewed HTTPS origin")
    return value.strip().rstrip("/")


@dataclass(frozen=True)
class BearerIdentity:
    scim_id: str
    application_id: str


@dataclass(frozen=True)
class AdminAppSnapshot:
    app_id: str
    app_name: str
    app_service_principal_client_id: str
    app_service_principal_scim_id: str
    app_url: str
    compute_state: str
    active_deployment_id: str
    pending_deployment_id: str
    target_scim_id: str
    target_display_name: str
    acl: tuple[tuple[str, str, tuple[tuple[str, bool], ...]], ...]


def _bearer_identity(response: Any, *, label: str, stage: str) -> BearerIdentity:
    if getattr(response, "status_code", None) != 200:
        raise RuntimeError(
            f"{label} exact-bearer {stage} identity proof returned "
            f"status={getattr(response, 'status_code', 'UNKNOWN')}"
        )
    try:
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 - classify an untrusted HTTP response
        raise RuntimeError(
            f"{label} exact-bearer {stage} identity proof returned invalid JSON"
        ) from exc
    if not isinstance(payload, Mapping):
        raise RuntimeError(
            f"{label} exact-bearer {stage} identity proof returned a malformed identity"
        )
    application_ids = {
        str(payload.get(field) or "").strip()
        for field in ("applicationId", "application_id", "userName", "user_name")
        if str(payload.get(field) or "").strip()
    }
    if len(application_ids) != 1:
        raise RuntimeError(
            f"{label} exact-bearer {stage} application identity is ambiguous"
        )
    scim_id = str(payload.get("id") or "").strip()
    if not scim_id:
        raise RuntimeError(
            f"{label} exact-bearer {stage} immutable SCIM identity is absent"
        )
    return BearerIdentity(
        scim_id=scim_id,
        application_id=next(iter(application_ids)),
    )


def _canonical_acl(permissions: Any, *, label: str) -> tuple[
    tuple[str, str, tuple[tuple[str, bool], ...]], ...
]:
    entries = getattr(permissions, "access_control_list", None)
    if not isinstance(entries, list):
        raise RuntimeError(f"{label} admin App ACL inventory is malformed")
    canonical: list[tuple[str, str, tuple[tuple[str, bool], ...]]] = []
    principals: set[tuple[str, str]] = set()
    for entry in entries:
        candidates = tuple(
            (kind, _text(entry, field))
            for kind, field in (
                ("service_principal", "service_principal_name"),
                ("group", "group_name"),
                ("user", "user_name"),
            )
            if _text(entry, field)
        )
        if len(candidates) != 1 or candidates[0] in principals:
            raise RuntimeError(
                f"{label} admin App ACL contains an ambiguous or duplicate principal"
            )
        principals.add(candidates[0])
        permissions_list = getattr(entry, "all_permissions", None)
        if not isinstance(permissions_list, list) or not permissions_list:
            raise RuntimeError(f"{label} admin App ACL permission list is malformed")
        levels: list[tuple[str, bool]] = []
        for permission in permissions_list:
            level = _text(permission, "permission_level").upper()
            inherited = getattr(permission, "inherited", None)
            if level not in _APP_PERMISSION_LEVELS or not isinstance(inherited, bool):
                raise RuntimeError(
                    f"{label} admin App ACL permission is unknown or malformed"
                )
            levels.append((level, inherited))
        if len(levels) != len(set(levels)):
            raise RuntimeError(f"{label} admin App ACL permissions are duplicated")
        canonical.append((*candidates[0], tuple(sorted(levels))))
    return tuple(sorted(canonical))


def _admin_snapshot(
    admin_workspace: Any,
    *,
    app_name: str,
    app_url: str,
    expected_application_id: str,
    label: str,
) -> AdminAppSnapshot:
    app = admin_workspace.apps.get(app_name)
    observed_name = _text(app, "name")
    observed_url = _https_origin(
        _text(app, "url"),
        suffixes=(".databricksapps.com",),
        label=f"{label} admin App URL",
    )
    if observed_name != app_name or observed_url != app_url:
        raise RuntimeError(f"{label} admin App identity or URL drifted")
    escaped = expected_application_id.replace("\\", "\\\\").replace('"', '\\"')
    principals = list(
        admin_workspace.service_principals.list(
            filter=f'applicationId eq "{escaped}"',
            attributes="id,applicationId,displayName",
        )
    )
    matches = [
        item
        for item in principals
        if _text(item, "application_id") == expected_application_id
    ]
    if len(matches) != 1:
        raise RuntimeError(f"{label} admin target identity inventory is ambiguous")
    target_scim_id = _text(matches[0], "id")
    target_display_name = _text(matches[0], "display_name")
    compute_state = _text(getattr(app, "compute_status", None), "state").split(".")[
        -1
    ].upper()
    active_deployment = getattr(app, "active_deployment", None)
    pending_deployment = getattr(app, "pending_deployment", None)
    active_id = _text(active_deployment, "deployment_id")
    pending_id = _text(pending_deployment, "deployment_id")
    if (active_deployment is not None and not active_id) or (
        pending_deployment is not None and not pending_id
    ):
        raise RuntimeError(f"{label} admin App deployment inventory is malformed")
    values = (
        _text(app, "id"),
        _text(app, "service_principal_client_id"),
        _text(app, "service_principal_id"),
        target_scim_id,
        target_display_name,
        compute_state,
    )
    if any(not value for value in values):
        raise RuntimeError(f"{label} admin App or target identity inventory is incomplete")
    return AdminAppSnapshot(
        app_id=values[0],
        app_name=observed_name,
        app_service_principal_client_id=values[1],
        app_service_principal_scim_id=values[2],
        app_url=observed_url,
        compute_state=compute_state,
        active_deployment_id=active_id,
        pending_deployment_id=pending_id,
        target_scim_id=target_scim_id,
        target_display_name=target_display_name,
        acl=_canonical_acl(
            admin_workspace.apps.get_permissions(app_name),
            label=label,
        ),
    )


def _assert_401_attestation(
    snapshot: AdminAppSnapshot,
    *,
    identity: BearerIdentity,
    expected_application_id: str,
    label: str,
) -> None:
    if (
        snapshot.compute_state not in {"ACTIVE", "STOPPED"}
        or snapshot.pending_deployment_id
        or (
            snapshot.compute_state == "ACTIVE"
            and not snapshot.active_deployment_id
        )
        or identity.scim_id != snapshot.target_scim_id
        or identity.application_id != expected_application_id
    ):
        raise RuntimeError(f"{label} App 401 attestation does not match")
    target_names = {
        expected_application_id,
        snapshot.target_scim_id,
        snapshot.target_display_name,
    }
    for kind, principal, levels in snapshot.acl:
        permission_levels = {item[0] for item in levels}
        if (
            snapshot.compute_state == "STOPPED"
            and "CAN_USE" in permission_levels
        ):
            raise RuntimeError(
                f"{label} stopped-App 401 attestation found a global CAN_USE grant"
            )
        if kind == "service_principal" and principal in target_names:
            raise RuntimeError(
                f"{label} App 401 attestation found direct App access"
            )


def verify_authenticated_app_denial(
    workspace: Any,
    *,
    expected_application_id: str,
    app_url: str,
    label: str,
    http_get: Callable[..., Any] = requests.get,
    admin_workspace: Any | None = None,
    app_name: str | None = None,
    allow_attested_app_401: bool = False,
) -> None:
    """Prove App denial; 401 requires a stable, independent admin attestation."""

    expected = expected_application_id.strip()
    host = _https_origin(
        str(getattr(workspace.config, "host", "") or ""),
        suffixes=(".databricks.com", ".azuredatabricks.net"),
        label=f"{label} workspace host",
    )
    reviewed_app_url = _https_origin(
        app_url,
        suffixes=(".databricksapps.com",),
        label=f"{label} App URL",
    )
    headers = workspace.config.authenticate()
    if not isinstance(headers, Mapping):
        raise RuntimeError(f"{label} lacks an exact workspace OAuth bearer binding")
    authorization = str(headers.get("Authorization") or "").strip()
    if (
        not expected
        or not authorization.startswith("Bearer ")
        or len(authorization.split()) != 2
    ):
        raise RuntimeError(f"{label} lacks an exact workspace OAuth bearer binding")
    attested_app_name = str(app_name or "").strip()
    if allow_attested_app_401 and (
        admin_workspace is None
        or admin_workspace is workspace
        or not attested_app_name
    ):
        raise RuntimeError(f"{label} App 401 attestation authority is absent")

    before_snapshot = (
        _admin_snapshot(
            admin_workspace,
            app_name=attested_app_name,
            app_url=reviewed_app_url,
            expected_application_id=expected,
            label=label,
        )
        if allow_attested_app_401
        else None
    )
    identity_url = f"{host}/api/2.0/preview/scim/v2/Me"
    before_identity = _bearer_identity(
        http_get(
            identity_url,
            headers=headers,
            allow_redirects=False,
            timeout=30,
        ),
        label=label,
        stage="preflight",
    )
    if before_identity.application_id != expected:
        raise RuntimeError(f"{label} exact-bearer preflight identity does not match")

    response = http_get(
        f"{reviewed_app_url}/api/v1/health",
        headers=headers,
        allow_redirects=False,
        timeout=30,
    )
    status = getattr(response, "status_code", None)
    if status not in {401, 403}:
        raise RuntimeError(f"{label} unexpectedly returned status={status or 'UNKNOWN'}")
    if status == 401 and not allow_attested_app_401:
        raise RuntimeError(f"{label} returned uncorroborated status=401")
    if status == 401:
        permission_url = (
            f"{host}/api/2.0/permissions/apps/{quote(attested_app_name, safe='')}"
        )
        permission_response = http_get(
            permission_url,
            headers=headers,
            allow_redirects=False,
            timeout=30,
        )
        if getattr(permission_response, "status_code", None) != 403:
            raise RuntimeError(
                f"{label} App permission-administration denial returned "
                f"status={getattr(permission_response, 'status_code', 'UNKNOWN')}"
            )

    after_identity = _bearer_identity(
        http_get(
            identity_url,
            headers=headers,
            allow_redirects=False,
            timeout=30,
        ),
        label=label,
        stage="postflight",
    )
    if after_identity != before_identity:
        raise RuntimeError(f"{label} exact-bearer identity drifted")
    if status == 401:
        assert before_snapshot is not None
        after_snapshot = _admin_snapshot(
            admin_workspace,
            app_name=attested_app_name,
            app_url=reviewed_app_url,
            expected_application_id=expected,
            label=label,
        )
        if after_snapshot != before_snapshot:
            raise RuntimeError(f"{label} admin App identity, state, or ACL drifted")
        _assert_401_attestation(
            after_snapshot,
            identity=after_identity,
            expected_application_id=expected,
            label=label,
        )
