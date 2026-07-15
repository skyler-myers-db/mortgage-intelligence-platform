"""Contracts for distinct normal/admin/verifier M2M provisioning."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_PMO_PATH = REPO_ROOT / "tools" / "databricks" / "provision_m2m_oauth.py"
_MODNAME = "mip_provision_m2m_oauth"
_spec = importlib.util.spec_from_file_location(_MODNAME, _PMO_PATH)
assert _spec is not None and _spec.loader is not None
pmo = importlib.util.module_from_spec(_spec)
sys.modules[_MODNAME] = pmo
_spec.loader.exec_module(pmo)  # type: ignore[union-attr]

_MINT_PATH = REPO_ROOT / "tools" / "oauth_m2m_mint.py"
_MINT_MODNAME = "mip_oauth_m2m_mint"
_mint_spec = importlib.util.spec_from_file_location(_MINT_MODNAME, _MINT_PATH)
assert _mint_spec is not None and _mint_spec.loader is not None
mint = importlib.util.module_from_spec(_mint_spec)
sys.modules[_MINT_MODNAME] = mint
_mint_spec.loader.exec_module(mint)  # type: ignore[union-attr]


@pytest.fixture(autouse=True)
def _clear_role_client_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    for defaults in pmo.IDENTITY_DEFAULTS.values():
        monkeypatch.delenv(defaults.client_id_secret_name, raising=False)


def _sp(
    display_name: str = "mip-nightly-ci-sp",
    *,
    sp_id: str = "1234",
    application_id: str = "app-id-abc",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=sp_id,
        application_id=application_id,
        display_name=display_name,
    )


def _make_client(
    *,
    existing_sp: SimpleNamespace | None = None,
    create_returns: SimpleNamespace | None = None,
    groups: list[SimpleNamespace] | None = None,
    lakebase_roles: list[SimpleNamespace] | None = None,
    mint_secret_value: str = "dose_fake_secret_value",
) -> MagicMock:
    client = MagicMock(name="WorkspaceClient")
    client.service_principals.list.return_value = iter(
        [existing_sp] if existing_sp is not None else []
    )
    client.service_principals.create.return_value = create_returns
    client.service_principal_secrets_proxy.create.return_value = SimpleNamespace(
        id="secret-id-xyz",
        secret=mint_secret_value,
    )
    group_values = groups or []
    client.groups.list.side_effect = lambda **_kwargs: iter(group_values)
    groups_by_id = {str(group.id): group for group in group_values if getattr(group, "id", None)}
    client.groups.get.side_effect = lambda group_id: groups_by_id[str(group_id)]
    client.database.list_database_instance_roles.return_value = iter(lakebase_roles or [])
    client.serving_endpoints.get.return_value = SimpleNamespace(
        id="mip-gateway-endpoint-id",
        name="mip-agent-gateway",
    )
    client.apps.get_permissions.return_value = SimpleNamespace(access_control_list=[])
    return client


def _provision(client: MagicMock, **overrides: object):
    kwargs: dict[str, object] = {
        "sp_name": "mip-nightly-ci-sp",
        "expected_application_id": None,
        "app_name": "mip-app",
        "grant_can_use": True,
        "group_name": None,
        "create_group": False,
        "lakebase_instance": None,
        "gateway_endpoint": None,
        "warehouse_id": None,
        "gh_repo": "acme/repo",
        "set_gh_secrets": True,
        "mint_secret": True,
        "rotate": False,
        "app_url": "https://mip-app-test.aws.databricksapps.com",
        "client_id_secret_name": "DATABRICKS_CLIENT_ID",
        "client_secret_secret_name": "DATABRICKS_CLIENT_SECRET",
        "app_url_secret_name": "MIP_APP_URL",
        "identity_role": "normal",
        "client_factory": lambda: client,
    }
    kwargs.update(overrides)
    role_defaults = pmo.IDENTITY_DEFAULTS[kwargs["identity_role"]]
    if "sp_name" not in overrides:
        kwargs["sp_name"] = role_defaults.sp_name
    if "client_id_secret_name" not in overrides:
        kwargs["client_id_secret_name"] = role_defaults.client_id_secret_name
    if "client_secret_secret_name" not in overrides:
        kwargs["client_secret_secret_name"] = role_defaults.client_secret_secret_name
    if "app_url_secret_name" not in overrides:
        kwargs["app_url_secret_name"] = role_defaults.app_url_secret_name
    with patch.object(pmo, "_gh_available", return_value=True):
        return pmo.provision(**kwargs)


def test_help_exposes_role_group_and_secret_name_contract(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        pmo.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for option in (
        "--identity-role",
        "--create-group",
        "--client-id-secret-name",
        "--client-secret-secret-name",
        "--warehouse-id",
        "--no-mint-secret",
    ):
        assert option in out


@pytest.mark.parametrize(
    ("role", "sp_name", "client_id_name", "client_secret_name"),
    [
        ("normal", "mip-nightly-ci-sp", "DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET"),
        (
            "admin",
            "mip-nightly-admin-ci-sp",
            "DATABRICKS_ADMIN_CLIENT_ID",
            "DATABRICKS_ADMIN_CLIENT_SECRET",
        ),
        (
            "verifier",
            "mip-ai-gateway-verifier-ci-sp",
            "DATABRICKS_VERIFIER_CLIENT_ID",
            "DATABRICKS_VERIFIER_CLIENT_SECRET",
        ),
    ],
)
def test_role_defaults_are_distinct(
    role: str,
    sp_name: str,
    client_id_name: str,
    client_secret_name: str,
) -> None:
    defaults = pmo.IDENTITY_DEFAULTS[role]
    assert defaults.sp_name == sp_name
    assert defaults.client_id_secret_name == client_id_name
    assert defaults.client_secret_secret_name == client_secret_name


def test_dry_run_does_not_touch_sdk() -> None:
    with patch.object(pmo, "provision") as mock_provision:
        rc = pmo.main(["--identity-role", "admin", "--create-group", "--dry-run"])
    assert rc == 0
    mock_provision.assert_not_called()


@pytest.mark.parametrize("dry_run", [False, True], ids=["live", "dry-run"])
def test_cli_rejects_verifier_app_can_use_before_provision(
    dry_run: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv = ["--identity-role", "verifier", "--grant-can-use"]
    if dry_run:
        argv.append("--dry-run")

    with (
        patch.object(pmo, "provision") as mock_provision,
        pytest.raises(SystemExit) as exc,
    ):
        pmo.main(argv)

    assert exc.value.code == 2
    assert "verifier forbids Databricks App CAN_USE" in capsys.readouterr().err
    mock_provision.assert_not_called()


@pytest.mark.parametrize(
    ("role", "role_args"),
    [
        pytest.param("normal", [], id="app-runtime"),
        pytest.param("admin", ["--create-group"], id="admin"),
    ],
)
def test_cli_allows_app_can_use_for_app_runtime_and_admin_roles(
    role: str,
    role_args: list[str],
) -> None:
    assert pmo.main(["--identity-role", role, "--grant-can-use", "--dry-run", *role_args]) == 0


@pytest.mark.parametrize("option", ["--lakebase-instance", "--gateway-endpoint", "--warehouse-id"])
def test_verifier_grant_options_are_rejected_for_other_roles(option: str) -> None:
    with pytest.raises(SystemExit) as exc:
        pmo.main(["--identity-role", "normal", option, "unexpected-resource", "--dry-run"])
    assert exc.value.code == 2


def test_mint_output_file_is_mode_0600_and_secret_free_on_console(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "bearer"
    mint._write_output("short-lived-secret", github_env_names=None, output_file=output)

    assert output.read_text(encoding="utf-8") == "short-lived-secret\n"
    assert output.stat().st_mode & 0o777 == 0o600
    captured = capsys.readouterr()
    assert "short-lived-secret" not in captured.out + captured.err


def test_mint_can_write_same_token_to_multiple_github_env_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    github_env = tmp_path / "github-env"
    monkeypatch.setenv("GITHUB_ENV", str(github_env))

    mint._write_output(
        "short-lived-secret",
        github_env_names=["MIP_BEARER_TOKEN", "MIP_NON_ADMIN_BEARER_TOKEN"],
        output_file=None,
    )

    assert github_env.read_text(encoding="utf-8").splitlines() == [
        "MIP_BEARER_TOKEN=short-lived-secret",
        "MIP_NON_ADMIN_BEARER_TOKEN=short-lived-secret",
    ]


def test_normal_happy_path_creates_grants_mints_and_uses_role_owned_secret_names() -> None:
    new_sp = _sp()
    client = _make_client(create_returns=new_sp)

    with patch.object(pmo, "_set_gh_secret") as set_secret:
        result = _provision(client)

    client.service_principals.create.assert_called_once_with(
        display_name="mip-nightly-ci-sp",
        active=True,
    )
    client.apps.update_permissions.assert_called_once()
    app_acl = client.apps.update_permissions.call_args.kwargs["access_control_list"]
    assert app_acl[0].service_principal_name == "app-id-abc"
    assert getattr(app_acl[0].permission_level, "value", app_acl[0].permission_level) == "CAN_USE"
    client.service_principal_secrets_proxy.create.assert_called_once_with(
        service_principal_id="1234"
    )
    assert set_secret.call_args_list == [
        call("acme/repo", "DATABRICKS_CLIENT_SECRET", "dose_fake_secret_value"),
        call("acme/repo", "DATABRICKS_CLIENT_ID", "app-id-abc"),
        call("acme/repo", "MIP_APP_URL", "https://mip-app-test.aws.databricksapps.com"),
    ]
    assert result.created_sp is True
    assert result.secret_minted is True
    assert result.secret_written_to_gh is True
    assert not hasattr(result, "client_secret")


@pytest.mark.parametrize(
    "argv",
    [
        ["--identity-role", "normal", "--sp-name", "mip-nightly-admin-ci-sp"],
        [
            "--identity-role",
            "normal",
            "--client-id-secret-name",
            "DATABRICKS_ADMIN_CLIENT_ID",
        ],
        ["--identity-role", "normal", "--no-app-url-secret"],
    ],
    ids=["reserved-sp", "admin-secret-sink", "missing-owned-app-url-sink"],
)
def test_dry_run_rejects_cross_role_identity_binding_before_external_checks(
    argv: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    with (
        patch.object(pmo, "_load_app_name_from_bundle") as load_app,
        patch.object(pmo, "_infer_gh_repo") as infer_repo,
        patch.object(pmo, "_gh_available") as gh_available,
        patch.object(pmo, "provision") as mock_provision,
        pytest.raises(SystemExit) as exc,
    ):
        pmo.main([*argv, "--dry-run"])

    error = capsys.readouterr().err
    assert exc.value.code == 2
    assert "bound to reserved service principal" in error or "role-owned" in error
    load_app.assert_not_called()
    infer_repo.assert_not_called()
    gh_available.assert_not_called()
    mock_provision.assert_not_called()


def test_direct_provision_rejects_client_id_reserved_for_another_role_before_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_ADMIN_CLIENT_ID", "admin-client-id")
    client_factory = MagicMock()

    with pytest.raises(SystemExit, match="reserved for the admin identity role"):
        pmo.provision(
            sp_name="mip-nightly-ci-sp",
            expected_application_id="admin-client-id",
            app_name="mip-app",
            grant_can_use=True,
            group_name=None,
            create_group=False,
            lakebase_instance=None,
            gateway_endpoint=None,
            warehouse_id=None,
            gh_repo=None,
            set_gh_secrets=False,
            mint_secret=False,
            rotate=False,
            app_url="https://mip-app-test.aws.databricksapps.com",
            client_id_secret_name="DATABRICKS_CLIENT_ID",
            client_secret_secret_name="DATABRICKS_CLIENT_SECRET",
            app_url_secret_name="MIP_APP_URL",
            identity_role="normal",
            client_factory=client_factory,
        )

    client_factory.assert_not_called()


def test_dry_run_rejects_cross_role_client_id_before_external_checks(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABRICKS_ADMIN_CLIENT_ID", "admin-client-id")

    with (
        patch.object(pmo, "_load_app_name_from_bundle") as load_app,
        patch.object(pmo, "_infer_gh_repo") as infer_repo,
        patch.object(pmo, "_gh_available") as gh_available,
        patch.object(pmo, "provision") as mock_provision,
        pytest.raises(SystemExit) as exc,
    ):
        pmo.main(
            [
                "--identity-role",
                "normal",
                "--expected-application-id",
                "admin-client-id",
                "--dry-run",
            ]
        )

    assert exc.value.code == 2
    assert "reserved for the admin identity role" in capsys.readouterr().err
    load_app.assert_not_called()
    infer_repo.assert_not_called()
    gh_available.assert_not_called()
    mock_provision.assert_not_called()


def test_admin_missing_group_fails_closed_without_create_group() -> None:
    admin_sp = _sp("mip-nightly-admin-ci-sp")
    client = _make_client(existing_sp=admin_sp)

    with pytest.raises(SystemExit, match="--create-group"):
        _provision(
            client,
            sp_name=admin_sp.display_name,
            group_name="mip-admin",
            identity_role="admin",
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )

    client.groups.create.assert_not_called()
    client.groups.patch.assert_not_called()


def test_admin_missing_group_fails_before_creating_service_principal() -> None:
    client = _make_client(create_returns=_sp("mip-nightly-admin-ci-sp"))

    with pytest.raises(SystemExit, match="--create-group"):
        _provision(
            client,
            sp_name="mip-nightly-admin-ci-sp",
            group_name="mip-admin",
            identity_role="admin",
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )

    client.service_principals.list.assert_not_called()
    client.service_principals.create.assert_not_called()


def test_admin_create_group_then_adds_membership() -> None:
    admin_sp = _sp("mip-nightly-admin-ci-sp", sp_id="admin-scim-id")
    created_group = SimpleNamespace(id="group-1", display_name="mip-admin", members=[])
    client = _make_client(existing_sp=admin_sp)
    client.groups.create.return_value = created_group

    result = _provision(
        client,
        sp_name=admin_sp.display_name,
        group_name="mip-admin",
        identity_role="admin",
        create_group=True,
        mint_secret=False,
        set_gh_secrets=False,
        gh_repo=None,
    )

    client.groups.create.assert_called_once_with(display_name="mip-admin")
    client.groups.patch.assert_called_once()
    patch_kwargs = client.groups.patch.call_args.kwargs
    assert patch_kwargs["id"] == "group-1"
    operation = patch_kwargs["operations"][0]
    assert operation.value["members"][0]["value"] == "admin-scim-id"
    assert operation.as_dict()["value"] == {
        "members": [{"value": "admin-scim-id"}],
    }
    assert result.added_to_group is True


def test_admin_existing_membership_is_idempotent() -> None:
    admin_sp = _sp("mip-nightly-admin-ci-sp", sp_id="admin-scim-id")
    group = SimpleNamespace(
        id="group-1",
        display_name="mip-admin",
        members=[SimpleNamespace(value="admin-scim-id")],
    )
    client = _make_client(existing_sp=admin_sp, groups=[group])

    result = _provision(
        client,
        sp_name=admin_sp.display_name,
        group_name="mip-admin",
        identity_role="admin",
        mint_secret=False,
        set_gh_secrets=False,
        gh_repo=None,
    )

    client.groups.create.assert_not_called()
    client.groups.patch.assert_not_called()
    assert result.added_to_group is False


def test_verifier_creates_distinct_lakebase_role_without_admin_or_app_grants() -> None:
    verifier = _sp(
        "mip-ai-gateway-verifier-ci-sp",
        sp_id="verifier-scim-id",
        application_id="verifier-application-id",
    )
    client = _make_client(existing_sp=verifier)

    result = _provision(
        client,
        sp_name=verifier.display_name,
        grant_can_use=False,
        group_name=None,
        lakebase_instance="mip-app-state",
        gateway_endpoint="mip-agent-gateway",
        warehouse_id="warehouse-123",
        identity_role="verifier",
        mint_secret=False,
        set_gh_secrets=False,
        gh_repo=None,
    )

    client.groups.list.assert_called_once()
    client.groups.patch.assert_not_called()
    client.apps.update_permissions.assert_not_called()
    client.database.create_database_instance_role.assert_called_once()
    instance_name, role = client.database.create_database_instance_role.call_args.args
    assert instance_name == "mip-app-state"
    assert role.name == "verifier-application-id"
    assert getattr(role.identity_type, "value", role.identity_type) == "SERVICE_PRINCIPAL"
    client.serving_endpoints.update_permissions.assert_called_once()
    client.serving_endpoints.get.assert_called_once_with("mip-agent-gateway")
    (endpoint_id,) = client.serving_endpoints.update_permissions.call_args.args
    endpoint_acl = client.serving_endpoints.update_permissions.call_args.kwargs[
        "access_control_list"
    ]
    assert endpoint_id == "mip-gateway-endpoint-id"
    assert endpoint_acl[0].service_principal_name == "verifier-application-id"
    assert getattr(endpoint_acl[0].permission_level, "value", endpoint_acl[0].permission_level) == (
        "CAN_QUERY"
    )
    assert result.created_lakebase_role is True
    assert result.granted_can_query is True
    client.warehouses.update_permissions.assert_called_once()
    (warehouse_id,) = client.warehouses.update_permissions.call_args.args
    warehouse_acl = client.warehouses.update_permissions.call_args.kwargs["access_control_list"]
    assert warehouse_id == "warehouse-123"
    assert warehouse_acl[0].service_principal_name == "verifier-application-id"
    assert (
        getattr(warehouse_acl[0].permission_level, "value", warehouse_acl[0].permission_level)
        == "CAN_USE"
    )
    assert result.granted_warehouse_can_use is True


def test_provision_rejects_verifier_app_can_use_before_client_or_any_mutation() -> None:
    client_factory = MagicMock()

    with pytest.raises(SystemExit, match="verifier forbids Databricks App CAN_USE"):
        pmo.provision(
            sp_name="mip-ai-gateway-verifier-ci-sp",
            expected_application_id=None,
            app_name="mip-app",
            grant_can_use=True,
            group_name=None,
            create_group=False,
            lakebase_instance="mip-app-state",
            gateway_endpoint="mip-agent-gateway",
            warehouse_id="warehouse-123",
            gh_repo="acme/repo",
            set_gh_secrets=True,
            mint_secret=True,
            rotate=True,
            app_url="https://mip-app-test.aws.databricksapps.com",
            client_id_secret_name="DATABRICKS_VERIFIER_CLIENT_ID",
            client_secret_secret_name="DATABRICKS_VERIFIER_CLIENT_SECRET",
            app_url_secret_name=None,
            identity_role="verifier",
            client_factory=client_factory,
        )

    client_factory.assert_not_called()


def test_expected_application_id_fails_before_creating_missing_principal() -> None:
    client = _make_client(create_returns=_sp(application_id="unexpected-id"))

    with pytest.raises(SystemExit, match="refusing to create"):
        _provision(
            client,
            expected_application_id="authoritative-id",
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )

    client.service_principals.create.assert_not_called()
    client.apps.update_permissions.assert_not_called()


def test_verifier_fails_closed_on_forbidden_direct_app_permission() -> None:
    verifier = _sp(
        "mip-ai-gateway-verifier-ci-sp",
        application_id="verifier-application-id",
    )
    client = _make_client(existing_sp=verifier)
    client.apps.get_permissions.return_value = SimpleNamespace(
        access_control_list=[
            SimpleNamespace(
                service_principal_name="verifier-application-id",
                all_permissions=[SimpleNamespace(inherited=False)],
            )
        ]
    )

    with pytest.raises(SystemExit, match="forbidden direct Databricks App"):
        _provision(
            client,
            sp_name=verifier.display_name,
            identity_role="verifier",
            grant_can_use=False,
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )

    client.warehouses.update_permissions.assert_not_called()


def test_verifier_fails_closed_on_inherited_effective_app_permission() -> None:
    verifier = _sp(
        "mip-ai-gateway-verifier-ci-sp",
        application_id="verifier-application-id",
    )
    client = _make_client(existing_sp=verifier)
    client.apps.get_permissions.return_value = SimpleNamespace(
        access_control_list=[
            SimpleNamespace(
                service_principal_name="verifier-application-id",
                all_permissions=[SimpleNamespace(inherited=True)],
            )
        ]
    )

    with pytest.raises(SystemExit, match="including inherited/effective permissions"):
        _provision(
            client,
            sp_name=verifier.display_name,
            identity_role="verifier",
            grant_can_use=False,
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )

    client.database.create_database_instance_role.assert_not_called()


def test_verifier_fails_closed_on_direct_group_app_permission() -> None:
    verifier = _sp(
        "mip-ai-gateway-verifier-ci-sp",
        sp_id="verifier-scim-id",
        application_id="verifier-application-id",
    )
    app_group = SimpleNamespace(
        id="app-group-id",
        display_name="mip-app-users",
        members=[SimpleNamespace(value="verifier-scim-id")],
    )
    client = _make_client(existing_sp=verifier, groups=[app_group])
    client.apps.get_permissions.return_value = SimpleNamespace(
        access_control_list=[
            SimpleNamespace(
                group_name="mip-app-users",
                all_permissions=[SimpleNamespace(inherited=False)],
            )
        ]
    )

    with pytest.raises(SystemExit, match="through group 'mip-app-users'"):
        _provision(
            client,
            sp_name=verifier.display_name,
            identity_role="verifier",
            grant_can_use=False,
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )

    client.database.create_database_instance_role.assert_not_called()


def test_verifier_fails_closed_on_nested_group_app_permission() -> None:
    verifier = _sp(
        "mip-ai-gateway-verifier-ci-sp",
        sp_id="verifier-scim-id",
        application_id="verifier-application-id",
    )
    child_group = SimpleNamespace(
        id="child-group-id",
        display_name="mip-verifier-automation",
        members=[SimpleNamespace(value="verifier-scim-id")],
    )
    app_group = SimpleNamespace(
        id="app-group-id",
        display_name="mip-app-users",
        members=[SimpleNamespace(value="child-group-id")],
    )
    client = _make_client(existing_sp=verifier, groups=[app_group, child_group])
    client.apps.get_permissions.return_value = SimpleNamespace(
        access_control_list=[
            SimpleNamespace(
                group_name="mip-app-users",
                all_permissions=[SimpleNamespace(inherited=True)],
            )
        ]
    )

    with (
        patch.object(pmo, "_set_gh_secret") as set_secret,
        pytest.raises(SystemExit, match="through group 'mip-app-users'"),
    ):
        _provision(
            client,
            sp_name=verifier.display_name,
            identity_role="verifier",
            grant_can_use=False,
            lakebase_instance="mip-app-state",
            gateway_endpoint="mip-agent-gateway",
            warehouse_id="warehouse-123",
            mint_secret=True,
            rotate=True,
        )

    client.database.create_database_instance_role.assert_not_called()
    client.serving_endpoints.get.assert_not_called()
    client.serving_endpoints.update_permissions.assert_not_called()
    client.warehouses.update_permissions.assert_not_called()
    client.apps.update_permissions.assert_not_called()
    client.service_principal_secrets_proxy.create.assert_not_called()
    set_secret.assert_not_called()


def test_non_admin_identity_fails_closed_on_admin_group_membership() -> None:
    normal = _sp(sp_id="normal-scim-id")
    admin_group = SimpleNamespace(
        id="group-1",
        display_name="mip-admin",
        members=[SimpleNamespace(value="normal-scim-id")],
    )
    client = _make_client(existing_sp=normal, groups=[admin_group])

    with pytest.raises(SystemExit, match="forbidden admin group"):
        _provision(
            client,
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )

    client.apps.update_permissions.assert_not_called()


def test_verifier_fails_closed_on_nested_admin_group_membership() -> None:
    verifier = _sp(
        "mip-ai-gateway-verifier-ci-sp",
        sp_id="verifier-scim-id",
        application_id="verifier-application-id",
    )
    child_group = SimpleNamespace(
        id="child-group-id",
        display_name="mip-verifier-automation",
        members=[SimpleNamespace(value="verifier-scim-id")],
    )
    admin_group = SimpleNamespace(
        id="admin-group-id",
        display_name="mip-admin",
        members=[SimpleNamespace(value="child-group-id")],
    )
    client = _make_client(existing_sp=verifier, groups=[admin_group, child_group])

    with pytest.raises(SystemExit, match="direct or nested membership"):
        _provision(
            client,
            sp_name=verifier.display_name,
            identity_role="verifier",
            grant_can_use=False,
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )

    client.apps.get_permissions.assert_not_called()
    client.database.create_database_instance_role.assert_not_called()


def test_verifier_group_resolution_error_fails_closed_before_grants() -> None:
    verifier = _sp(
        "mip-ai-gateway-verifier-ci-sp",
        application_id="verifier-application-id",
    )
    client = _make_client(existing_sp=verifier)
    client.groups.list.side_effect = RuntimeError("SCIM group resolution unavailable")

    with pytest.raises(SystemExit, match="resolve effective group memberships failed"):
        _provision(
            client,
            sp_name=verifier.display_name,
            identity_role="verifier",
            grant_can_use=False,
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )

    client.apps.get_permissions.assert_not_called()
    client.database.create_database_instance_role.assert_not_called()


def test_verifier_app_permission_resolution_error_fails_closed_before_grants() -> None:
    verifier = _sp(
        "mip-ai-gateway-verifier-ci-sp",
        application_id="verifier-application-id",
    )
    client = _make_client(existing_sp=verifier)
    client.apps.get_permissions.side_effect = RuntimeError("App ACL resolution unavailable")

    with pytest.raises(SystemExit, match="inspect app permissions failed"):
        _provision(
            client,
            sp_name=verifier.display_name,
            identity_role="verifier",
            grant_can_use=False,
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )

    client.database.create_database_instance_role.assert_not_called()
    client.serving_endpoints.update_permissions.assert_not_called()


def test_non_admin_identity_hydrates_sparse_group_before_membership_check() -> None:
    normal = _sp(sp_id="normal-scim-id")
    sparse_group = SimpleNamespace(id="group-1", display_name="mip-admin")
    hydrated_group = SimpleNamespace(
        id="group-1",
        display_name="mip-admin",
        members=[SimpleNamespace(value="normal-scim-id")],
    )
    client = _make_client(existing_sp=normal, groups=[sparse_group])
    client.groups.get.side_effect = None
    client.groups.get.return_value = hydrated_group

    with pytest.raises(SystemExit, match="forbidden admin group"):
        _provision(
            client,
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )

    client.groups.get.assert_called_once_with("group-1")
    client.apps.update_permissions.assert_not_called()


def test_verifier_gateway_grant_fails_closed_without_endpoint_id() -> None:
    client = _make_client()
    client.serving_endpoints.get.return_value = SimpleNamespace(
        id=None,
        name="mip-agent-gateway",
    )

    with pytest.raises(SystemExit, match="has no immutable id"):
        pmo._grant_can_query_on_endpoint(
            client,
            "mip-agent-gateway",
            "verifier-application-id",
        )

    client.serving_endpoints.update_permissions.assert_not_called()


def test_existing_verifier_lakebase_role_is_idempotent() -> None:
    verifier = _sp(
        "mip-ai-gateway-verifier-ci-sp",
        application_id="verifier-application-id",
    )
    client = _make_client(
        existing_sp=verifier,
        lakebase_roles=[SimpleNamespace(name="verifier-application-id")],
    )

    result = _provision(
        client,
        grant_can_use=False,
        lakebase_instance="mip-app-state",
        identity_role="verifier",
        mint_secret=False,
        set_gh_secrets=False,
        gh_repo=None,
    )

    client.database.create_database_instance_role.assert_not_called()
    assert result.created_lakebase_role is False


def test_live_mint_requires_authenticated_gh_before_sdk_mutation() -> None:
    client_factory = MagicMock()
    with (
        patch.object(pmo, "_gh_available", return_value=False),
        pytest.raises(SystemExit, match="authenticated gh CLI"),
    ):
        pmo.provision(
            sp_name="mip-nightly-ci-sp",
            expected_application_id=None,
            app_name="mip-app",
            grant_can_use=True,
            group_name=None,
            create_group=False,
            lakebase_instance=None,
            gateway_endpoint=None,
            warehouse_id=None,
            gh_repo="acme/repo",
            set_gh_secrets=True,
            mint_secret=True,
            rotate=False,
            app_url="https://example",
            client_id_secret_name="DATABRICKS_CLIENT_ID",
            client_secret_secret_name="DATABRICKS_CLIENT_SECRET",
            app_url_secret_name="MIP_APP_URL",
            identity_role="normal",
            client_factory=client_factory,
        )
    client_factory.assert_not_called()


def test_set_gh_secret_uses_stdin_and_never_outputs_value(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch.object(pmo.subprocess, "run") as run:
        pmo._set_gh_secret("acme/repo", "DATABRICKS_CLIENT_SECRET", "s3cr3t")

    args, kwargs = run.call_args
    assert args[0] == [
        "gh",
        "secret",
        "set",
        "DATABRICKS_CLIENT_SECRET",
        "--repo",
        "acme/repo",
    ]
    assert kwargs["input"] == b"s3cr3t"
    assert "s3cr3t" not in " ".join(args[0])
    captured = capsys.readouterr()
    assert "s3cr3t" not in captured.out
    assert "s3cr3t" not in captured.err


def test_mint_missing_secret_fails_without_printing_response_values(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _make_client(create_returns=_sp(), mint_secret_value="")
    with (
        patch.object(pmo, "_set_gh_secret"),
        pytest.raises(SystemExit, match="no .secret"),
    ):
        _provision(client)
    captured = capsys.readouterr()
    assert "dose_fake_secret_value" not in captured.out + captured.err


def test_app_not_found_prompts_bundle_deploy() -> None:
    client = _make_client(existing_sp=_sp())
    client.apps.update_permissions.side_effect = RuntimeError("App mip-app NOT FOUND")
    with pytest.raises(SystemExit, match="bundle deploy -t dev"):
        _provision(
            client,
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )
