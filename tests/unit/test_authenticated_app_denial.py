from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from tools.databricks.authenticated_app_denial import (
    verify_authenticated_app_denial,
)

APP_NAME = "mip-app"
APP_URL = "https://mip-app.databricksapps.com"
HOST = "https://workspace.cloud.databricks.com"
IDENTITY = "proxy-client"
SCIM_ID = "proxy-scim"


class _Response:
    def __init__(self, status_code: int, payload: object | None = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        if self._payload is None:
            raise ValueError("no JSON")
        return self._payload


class _Config:
    def __init__(
        self,
        *,
        host: str = HOST,
        authorization: str = "Bearer exact-token",
    ) -> None:
        self.host = host
        self.authorization = authorization
        self.authenticate_calls = 0
        self.headers = {"Authorization": authorization}

    def authenticate(self) -> dict[str, str]:
        self.authenticate_calls += 1
        return self.headers


def _workspace(
    *,
    host: str = HOST,
    authorization: str = "Bearer exact-token",
) -> object:
    return SimpleNamespace(config=_Config(host=host, authorization=authorization))


def _permission(
    level: str,
    *,
    inherited: bool = False,
) -> object:
    return SimpleNamespace(permission_level=level, inherited=inherited)


def _acl_entry(
    *,
    service_principal_name: str = "",
    group_name: str = "",
    user_name: str = "",
    levels: tuple[object, ...] = (_permission("CAN_MANAGE", inherited=True),),
) -> object:
    return SimpleNamespace(
        service_principal_name=service_principal_name or None,
        group_name=group_name or None,
        user_name=user_name or None,
        all_permissions=list(levels),
    )


def _app(
    *,
    state: str = "STOPPED",
    name: str = APP_NAME,
    url: str = APP_URL,
    app_id: str = "app-id",
    app_client_id: str = "app-client-id",
    app_scim_id: str = "app-scim-id",
    active_deployment_id: str = "active-deployment",
    pending_deployment_id: str = "",
) -> object:
    return SimpleNamespace(
        id=app_id,
        name=name,
        url=url,
        service_principal_client_id=app_client_id,
        service_principal_id=app_scim_id,
        compute_status=SimpleNamespace(state=state),
        active_deployment=(
            SimpleNamespace(deployment_id=active_deployment_id)
            if active_deployment_id
            else None
        ),
        pending_deployment=(
            SimpleNamespace(deployment_id=pending_deployment_id)
            if pending_deployment_id
            else None
        ),
    )


class _AdminApps:
    def __init__(
        self,
        *,
        apps: tuple[object, ...],
        acls: tuple[tuple[object, ...], ...],
    ) -> None:
        self._apps = apps
        self._acls = acls
        self.get_calls = 0

    def get(self, _name: str) -> object:
        index = min(self.get_calls, len(self._apps) - 1)
        self.get_calls += 1
        return self._apps[index]

    def get_permissions(self, _name: str) -> object:
        index = min(max(self.get_calls - 1, 0), len(self._acls) - 1)
        return SimpleNamespace(access_control_list=list(self._acls[index]))


class _AdminPrincipals:
    def __init__(self, principals: tuple[object, ...]) -> None:
        self._principals = principals

    def list(self, **_kwargs: object) -> object:
        return iter(self._principals)


def _admin_workspace(
    *,
    apps: tuple[object, ...] | None = None,
    acls: tuple[tuple[object, ...], ...] | None = None,
    principals: tuple[object, ...] | None = None,
) -> object:
    default_acl = (
        _acl_entry(group_name="admins"),
        _acl_entry(user_name="operator@example.com"),
    )
    return SimpleNamespace(
        apps=_AdminApps(
            apps=apps or (_app(),),
            acls=acls or (default_acl,),
        ),
        service_principals=_AdminPrincipals(
            principals
            or (
                SimpleNamespace(
                    id=SCIM_ID,
                    application_id=IDENTITY,
                    display_name="mip-agent-supervisor-proxy-ci-sp",
                ),
            )
        ),
    )


def _identity_payload(
    *,
    scim_id: str = SCIM_ID,
    identity: str = IDENTITY,
) -> dict[str, str]:
    return {"id": scim_id, "userName": identity}


def _http_probe(
    *,
    app_status: int,
    permission_status: int = 403,
    pre_status: int = 200,
    post_status: int = 200,
    pre_payload: object | None = None,
    post_payload: object | None = None,
) -> tuple[Callable[..., _Response], list[dict[str, str]]]:
    identity_calls = 0
    seen_headers: list[dict[str, str]] = []

    def get(url: str, *, headers: dict[str, str], **_kwargs: object) -> _Response:
        nonlocal identity_calls
        seen_headers.append(headers)
        if url == f"{HOST}/api/2.0/preview/scim/v2/Me":
            identity_calls += 1
            if identity_calls == 1:
                return _Response(
                    pre_status,
                    _identity_payload() if pre_payload is None else pre_payload,
                )
            return _Response(
                post_status,
                _identity_payload() if post_payload is None else post_payload,
            )
        if url == f"{HOST}/api/2.0/permissions/apps/{APP_NAME}":
            return _Response(permission_status)
        assert url == f"{APP_URL}/api/v1/health"
        return _Response(app_status)

    return get, seen_headers


def _verify(
    *,
    app_status: int,
    workspace: object | None = None,
    http_get: Callable[..., Any] | None = None,
    admin_workspace: object | None = None,
    allow_stopped_app_401: bool = False,
    app_url: str = APP_URL,
) -> object:
    exact_workspace = workspace or _workspace()
    if http_get is None:
        http_get, _headers = _http_probe(app_status=app_status)
    verify_authenticated_app_denial(
        exact_workspace,
        expected_application_id=IDENTITY,
        app_url=app_url,
        label="proxy App denial",
        http_get=http_get,
        admin_workspace=admin_workspace,
        app_name=APP_NAME if admin_workspace is not None else None,
        allow_stopped_app_401=allow_stopped_app_401,
    )
    return exact_workspace


def test_accepts_403_bracketed_by_one_exact_bearer() -> None:
    http_get, seen_headers = _http_probe(app_status=403)
    workspace = _verify(app_status=403, http_get=http_get)

    assert workspace.config.authenticate_calls == 1
    assert len(seen_headers) == 3
    assert all(headers is seen_headers[0] for headers in seen_headers)
    assert seen_headers[0] == {"Authorization": "Bearer exact-token"}


def test_accepts_stopped_target_401_with_stable_admin_attestation() -> None:
    http_get, seen_headers = _http_probe(app_status=401)
    workspace = _verify(
        app_status=401,
        http_get=http_get,
        admin_workspace=_admin_workspace(),
        allow_stopped_app_401=True,
    )

    assert workspace.config.authenticate_calls == 1
    assert len(seen_headers) == 4
    assert all(headers is seen_headers[0] for headers in seen_headers)


def test_rejects_401_without_explicit_admin_attestation() -> None:
    with pytest.raises(RuntimeError, match="uncorroborated status=401"):
        _verify(app_status=401)


@pytest.mark.parametrize("state", ("RUNNING", "STARTING", "STOPPING", "UNKNOWN"))
def test_rejects_401_unless_admin_state_is_exactly_stopped(state: str) -> None:
    with pytest.raises(RuntimeError, match="attestation does not match"):
        _verify(
            app_status=401,
            admin_workspace=_admin_workspace(apps=(_app(state=state),)),
            allow_stopped_app_401=True,
        )


def test_rejects_401_with_pending_deployment() -> None:
    with pytest.raises(RuntimeError, match="attestation does not match"):
        _verify(
            app_status=401,
            admin_workspace=_admin_workspace(
                apps=(_app(pending_deployment_id="pending"),)
            ),
            allow_stopped_app_401=True,
        )


@pytest.mark.parametrize(
    "entry",
    (
        _acl_entry(
            service_principal_name=IDENTITY,
            levels=(_permission("CAN_MANAGE"),),
        ),
        _acl_entry(
            service_principal_name="mip-agent-supervisor-proxy-ci-sp",
            levels=(_permission("CAN_MANAGE"),),
        ),
        _acl_entry(
            service_principal_name=SCIM_ID,
            levels=(_permission("CAN_MANAGE"),),
        ),
        _acl_entry(group_name="users", levels=(_permission("CAN_USE"),)),
        _acl_entry(user_name="someone@example.com", levels=(_permission("CAN_USE"),)),
        _acl_entry(
            service_principal_name="unrelated-client",
            levels=(_permission("CAN_USE"),),
        ),
    ),
)
def test_rejects_direct_or_global_can_use_authority(entry: object) -> None:
    with pytest.raises(RuntimeError, match="direct App access|global CAN_USE"):
        _verify(
            app_status=401,
            admin_workspace=_admin_workspace(acls=((entry,),)),
            allow_stopped_app_401=True,
        )


@pytest.mark.parametrize("permission_status", (200, 401, 404, 429, 500, 503))
def test_rejects_401_without_same_bearer_permission_admin_403(
    permission_status: int,
) -> None:
    http_get, _headers = _http_probe(
        app_status=401,
        permission_status=permission_status,
    )
    with pytest.raises(RuntimeError, match="permission-administration denial"):
        _verify(
            app_status=401,
            http_get=http_get,
            admin_workspace=_admin_workspace(),
            allow_stopped_app_401=True,
        )


@pytest.mark.parametrize(
    ("pre_status", "post_status", "stage"),
    ((401, 200, "preflight"), (200, 401, "postflight")),
)
def test_rejects_expired_bearer_identity_bracket(
    pre_status: int,
    post_status: int,
    stage: str,
) -> None:
    http_get, _headers = _http_probe(
        app_status=403,
        pre_status=pre_status,
        post_status=post_status,
    )
    with pytest.raises(RuntimeError, match=stage):
        _verify(app_status=403, http_get=http_get)


@pytest.mark.parametrize(
    "payload",
    (
        {},
        {"id": SCIM_ID},
        {"userName": IDENTITY},
        {"id": SCIM_ID, "userName": "wrong-client"},
        {"id": SCIM_ID, "userName": IDENTITY, "applicationId": "other-client"},
        [],
    ),
)
def test_rejects_missing_wrong_or_ambiguous_bearer_identity(payload: object) -> None:
    http_get, _headers = _http_probe(app_status=403, pre_payload=payload)
    with pytest.raises(RuntimeError, match="identity|SCIM"):
        _verify(app_status=403, http_get=http_get)


def test_rejects_bearer_identity_drift() -> None:
    http_get, _headers = _http_probe(
        app_status=403,
        post_payload=_identity_payload(scim_id="changed-scim"),
    )
    with pytest.raises(RuntimeError, match="identity drifted"):
        _verify(app_status=403, http_get=http_get)


@pytest.mark.parametrize("app_status", (200, 302, 404, 429, 500, 503))
def test_rejects_non_denial_app_status(app_status: int) -> None:
    with pytest.raises(RuntimeError, match=f"status={app_status}"):
        _verify(app_status=app_status)


@pytest.mark.parametrize("authorization", ("", "Basic token", "Bearer two tokens"))
def test_rejects_missing_or_malformed_bearer(authorization: str) -> None:
    with pytest.raises(RuntimeError, match="OAuth bearer binding"):
        _verify(app_status=403, workspace=_workspace(authorization=authorization))


@pytest.mark.parametrize(
    ("host", "app_url"),
    (
        ("http://workspace.cloud.databricks.com", APP_URL),
        ("https://workspace.cloud.databricks.com.evil.example", APP_URL),
        (HOST, "http://mip-app.databricksapps.com"),
        (HOST, "https://mip-app.databricksapps.com.evil.example"),
        (HOST, f"{APP_URL}/unexpected"),
    ),
)
def test_rejects_unreviewed_workspace_or_app_origins(
    host: str,
    app_url: str,
) -> None:
    with pytest.raises(RuntimeError, match="reviewed HTTPS origin"):
        _verify(
            app_status=403,
            workspace=_workspace(host=host),
            app_url=app_url,
        )


@pytest.mark.parametrize(
    "entry",
    (
        _acl_entry(group_name="admins", user_name="also@example.com"),
        _acl_entry(group_name="admins", levels=()),
        _acl_entry(group_name="admins", levels=(_permission("UNKNOWN"),)),
        _acl_entry(
            group_name="admins",
            levels=(_permission("CAN_MANAGE", inherited="true"),),
        ),
        _acl_entry(
            group_name="admins",
            levels=(_permission("CAN_MANAGE"), _permission("CAN_MANAGE")),
        ),
    ),
)
def test_rejects_malformed_or_unknown_acl_entries(entry: object) -> None:
    with pytest.raises(RuntimeError, match="ACL|permission"):
        _verify(
            app_status=401,
            admin_workspace=_admin_workspace(acls=((entry,),)),
            allow_stopped_app_401=True,
        )


def test_rejects_duplicate_acl_principals() -> None:
    duplicate = _acl_entry(group_name="admins")
    with pytest.raises(RuntimeError, match="duplicate principal"):
        _verify(
            app_status=401,
            admin_workspace=_admin_workspace(acls=((duplicate, duplicate),)),
            allow_stopped_app_401=True,
        )


@pytest.mark.parametrize(
    "apps",
    (
        (_app(), _app(state="RUNNING")),
        (_app(), _app(url="https://other.databricksapps.com")),
        (_app(), _app(app_id="changed-app-id")),
        (_app(), _app(app_client_id="changed-app-client")),
        (_app(), _app(app_scim_id="changed-app-scim")),
        (_app(), _app(active_deployment_id="changed-deployment")),
        (_app(), _app(pending_deployment_id="pending")),
    ),
)
def test_rejects_admin_app_snapshot_drift(apps: tuple[object, ...]) -> None:
    with pytest.raises(RuntimeError, match="drifted"):
        _verify(
            app_status=401,
            admin_workspace=_admin_workspace(apps=apps),
            allow_stopped_app_401=True,
        )


def test_rejects_admin_acl_snapshot_drift() -> None:
    first = (_acl_entry(group_name="admins"),)
    second = (_acl_entry(group_name="admins"), _acl_entry(user_name="new@example.com"))
    with pytest.raises(RuntimeError, match="drifted"):
        _verify(
            app_status=401,
            admin_workspace=_admin_workspace(acls=(first, second)),
            allow_stopped_app_401=True,
        )


def test_rejects_target_identity_snapshot_drift() -> None:
    class _DriftingPrincipals:
        calls = 0

        def list(self, **_kwargs: object) -> object:
            self.calls += 1
            scim_id = SCIM_ID if self.calls == 1 else "changed-scim"
            return iter(
                (
                    SimpleNamespace(
                        id=scim_id,
                        application_id=IDENTITY,
                        display_name="mip-agent-supervisor-proxy-ci-sp",
                    ),
                )
            )

    admin = _admin_workspace()
    admin.service_principals = _DriftingPrincipals()
    with pytest.raises(RuntimeError, match="drifted"):
        _verify(
            app_status=401,
            admin_workspace=admin,
            allow_stopped_app_401=True,
        )
