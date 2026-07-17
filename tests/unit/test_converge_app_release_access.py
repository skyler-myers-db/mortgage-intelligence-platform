from __future__ import annotations

from types import SimpleNamespace

import pytest
from databricks.sdk.service.apps import (
    AppAccessControlResponse,
    AppPermission,
    AppPermissionLevel,
    AppPermissions,
)

from tools.databricks.converge_app_release_access import converge_app_release_access


def _permission(level: str, *, inherited: bool = False) -> AppPermission:
    return AppPermission(
        inherited=inherited,
        permission_level=AppPermissionLevel(level),
    )


def _entry(
    field: str,
    principal: str,
    level: str,
    *,
    inherited: bool = False,
) -> AppAccessControlResponse:
    return AppAccessControlResponse(
        **{
            field: principal,
            "all_permissions": [_permission(level, inherited=inherited)],
        }
    )


def _acl(*entries: AppAccessControlResponse) -> AppPermissions:
    return AppPermissions(access_control_list=list(entries))


class _Apps:
    def __init__(self, current: AppPermissions, postflight: AppPermissions) -> None:
        self.responses = [current, postflight]
        self.get_calls: list[str] = []
        self.set_calls: list[tuple[str, list[object]]] = []

    def get_permissions(self, app_name: str) -> AppPermissions:
        self.get_calls.append(app_name)
        return self.responses.pop(0)

    def set_permissions(self, app_name: str, *, access_control_list: list[object]) -> None:
        self.set_calls.append((app_name, access_control_list))


def _invoke(apps: _Apps, *, mode: str, **overrides: str) -> None:
    arguments = {
        "app_name": "mip-app",
        "mode": mode,
        "release_probe_application_id": "release-probe",
        "normal_application_id": "normal",
        "operator2_application_id": "operator2",
        "admin_application_id": "admin",
    }
    arguments.update(overrides)
    converge_app_release_access(SimpleNamespace(apps=apps), **arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("mode", "expected_can_use"),
    [
        ("quarantine", []),
        ("probe", ["release-probe"]),
        ("runtime", ["normal", "operator2", "admin"]),
    ],
)
def test_modes_set_exact_direct_can_use(mode: str, expected_can_use: list[str]) -> None:
    current = _acl(
        _entry("user_name", "owner@example.com", "CAN_MANAGE"),
        _entry("service_principal_name", "stale-principal", "CAN_USE"),
        _entry("group_name", "platform-admins", "CAN_MANAGE", inherited=True),
    )
    postflight_entries = [
        _entry("user_name", "owner@example.com", "CAN_MANAGE"),
        _entry("group_name", "platform-admins", "CAN_MANAGE", inherited=True),
        *[
            _entry("service_principal_name", application_id, "CAN_USE")
            for application_id in expected_can_use
        ],
    ]
    apps = _Apps(current, _acl(*postflight_entries))

    _invoke(apps, mode=mode)

    assert apps.get_calls == ["mip-app", "mip-app"]
    requests = apps.set_calls[0][1]
    direct_can_use = [
        request.service_principal_name
        for request in requests
        if request.permission_level == AppPermissionLevel.CAN_USE
    ]
    assert direct_can_use == expected_can_use
    assert "stale-principal" not in direct_can_use


def test_preserves_user_group_and_service_principal_direct_managers() -> None:
    managers = [
        _entry("user_name", "owner@example.com", "CAN_MANAGE"),
        _entry("group_name", "release-managers", "CAN_MANAGE"),
        _entry("service_principal_name", "deployment-sp", "CAN_MANAGE"),
    ]
    apps = _Apps(_acl(*managers), _acl(*managers))

    _invoke(apps, mode="quarantine")

    requests = apps.set_calls[0][1]
    assert {
        (request.user_name, request.group_name, request.service_principal_name)
        for request in requests
    } == {
        ("owner@example.com", None, None),
        (None, "release-managers", None),
        (None, None, "deployment-sp"),
    }
    assert all(request.permission_level == AppPermissionLevel.CAN_MANAGE for request in requests)


def test_rejects_inherited_can_use_without_mutation() -> None:
    inherited_use = _acl(_entry("group_name", "workspace-users", "CAN_USE", inherited=True))
    apps = _Apps(inherited_use, inherited_use)

    with pytest.raises(RuntimeError, match="inherited non-manager"):
        _invoke(apps, mode="quarantine")

    assert apps.set_calls == []


@pytest.mark.parametrize(
    "postflight",
    [
        _acl(_entry("service_principal_name", "unexpected", "CAN_USE")),
        _acl(),
        _acl(_entry("service_principal_name", "normal", "CAN_MANAGE")),
    ],
)
def test_rejects_readback_drift(postflight: AppPermissions) -> None:
    apps = _Apps(_acl(), postflight)

    with pytest.raises(RuntimeError, match="exact direct release access"):
        _invoke(apps, mode="runtime")

    assert len(apps.set_calls) == 1


def test_rejects_lost_inherited_manager() -> None:
    apps = _Apps(
        _acl(_entry("group_name", "platform-admins", "CAN_MANAGE", inherited=True)),
        _acl(),
    )

    with pytest.raises(RuntimeError, match="changed the inherited manager boundary"):
        _invoke(apps, mode="quarantine")


def test_rejects_added_inherited_manager() -> None:
    apps = _Apps(
        _acl(),
        _acl(_entry("group_name", "late-manager", "CAN_MANAGE", inherited=True)),
    )

    with pytest.raises(RuntimeError, match="changed the inherited manager boundary"):
        _invoke(apps, mode="quarantine")


def test_rejects_lost_direct_manager() -> None:
    apps = _Apps(
        _acl(_entry("user_name", "owner@example.com", "CAN_MANAGE")),
        _acl(),
    )

    with pytest.raises(RuntimeError, match="exact direct release access"):
        _invoke(apps, mode="quarantine")


def test_rejects_unexpected_direct_permission_on_readback() -> None:
    malformed = SimpleNamespace(
        access_control_list=[
            SimpleNamespace(
                service_principal_name="normal",
                all_permissions=[SimpleNamespace(permission_level="CAN_VIEW", inherited=False)],
            )
        ]
    )
    apps = _Apps(_acl(), malformed)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="unexpected permission"):
        _invoke(apps, mode="runtime")


@pytest.mark.parametrize(
    "overrides",
    [
        {"release_probe_application_id": ""},
        {"normal_application_id": "   "},
        {"operator2_application_id": "admin"},
        {"release_probe_application_id": "NORMAL"},
        {"app_name": ""},
    ],
)
def test_invalid_identity_inputs_do_not_read_or_mutate(overrides: dict[str, str]) -> None:
    apps = _Apps(_acl(), _acl())

    with pytest.raises(ValueError):
        _invoke(apps, mode="runtime", **overrides)

    assert apps.get_calls == []
    assert apps.set_calls == []


def test_rejects_release_probe_manager_overlap_without_mutation() -> None:
    current = _acl(_entry("service_principal_name", "release-probe", "CAN_MANAGE"))
    apps = _Apps(current, current)

    with pytest.raises(RuntimeError, match="release lifecycle identity overlaps"):
        _invoke(apps, mode="probe")

    assert apps.set_calls == []


@pytest.mark.parametrize("mode", ["quarantine", "probe", "runtime"])
@pytest.mark.parametrize("principal", ["normal", "operator2", "admin"])
def test_rejects_lifecycle_manager_overlap_in_every_mode_without_mutation(
    mode: str,
    principal: str,
) -> None:
    current = _acl(_entry("service_principal_name", principal, "CAN_MANAGE"))
    apps = _Apps(current, current)

    with pytest.raises(RuntimeError, match="release lifecycle identity overlaps"):
        _invoke(apps, mode=mode)

    assert apps.set_calls == []


@pytest.mark.parametrize("mode", ["quarantine", "probe", "runtime"])
@pytest.mark.parametrize("principal", ["release-probe", "normal", "operator2", "admin"])
def test_rejects_inherited_lifecycle_manager_overlap_in_every_mode_without_mutation(
    mode: str,
    principal: str,
) -> None:
    current = _acl(
        _entry(
            "service_principal_name",
            principal,
            "CAN_MANAGE",
            inherited=True,
        )
    )
    apps = _Apps(current, current)

    with pytest.raises(RuntimeError, match="release lifecycle identity overlaps"):
        _invoke(apps, mode=mode)

    assert apps.set_calls == []
