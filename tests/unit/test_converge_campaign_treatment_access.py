from __future__ import annotations

from types import SimpleNamespace

import pytest
from databricks.sdk.errors import NotFound, PermissionDenied

from tools.databricks.converge_campaign_treatment_access import (
    _effective_privileges,
    converge_campaign_treatment_access,
    target_group_membership_probe,
)
from tools.databricks.uc_owner_policy import account_client_from_env


class _UcObjects:
    def __init__(self, *, exists: bool, owner: str = "deployer", denied: bool = False) -> None:
        self.exists = exists
        self.owner = owner
        self.denied = denied
        self.get_calls: list[str] = []

    def get(self, name: str) -> object:
        self.get_calls.append(name)
        if self.denied:
            raise PermissionDenied("hidden is not absent")
        if not self.exists:
            raise NotFound("missing")
        return SimpleNamespace(owner=self.owner)


class _ServicePrincipals:
    def __init__(
        self,
        *,
        admin_role: bool = False,
        deployer_application_id: str = "",
        deployer_id: str = "",
    ) -> None:
        self.admin_role = admin_role
        self.deployer_application_id = deployer_application_id
        self.deployer_id = deployer_id

    def list(self, *, filter: str | None = None, **_: object) -> list[object]:
        if not filter or 'applicationId eq "app-client"' in filter:
            return [
                SimpleNamespace(
                    id="sp-id",
                    application_id="app-client",
                    display_name="app-sp",
                )
            ]
        if self.deployer_application_id and self.deployer_application_id in filter:
            return [
                SimpleNamespace(
                    id=self.deployer_id,
                    application_id=self.deployer_application_id,
                )
            ]
        return []

    def get(self, sp_id: str) -> object:
        assert sp_id == "sp-id"
        roles = [SimpleNamespace(value="account_admin")] if self.admin_role else []
        return SimpleNamespace(id=sp_id, roles=roles, entitlements=[])


class _Groups:
    def __init__(
        self,
        *,
        owner_group: str | None = None,
        target_is_member: bool = False,
        target_member_id: str = "sp-id",
    ) -> None:
        self.owner_group = owner_group
        self.target_is_member = target_is_member
        self.target_member_id = target_member_id

    def list(self, *, filter: str | None = None, **_: object) -> list[object]:
        if self.owner_group is None:
            return []
        group = SimpleNamespace(id="owner-group-id", display_name=self.owner_group)
        if filter and self.owner_group.casefold() not in filter.casefold():
            return []
        return [group]

    def get(self, group_id: str) -> object:
        assert self.owner_group is not None
        assert group_id == "owner-group-id"
        members = [SimpleNamespace(value=self.target_member_id)] if self.target_is_member else []
        return SimpleNamespace(
            id=group_id,
            display_name=self.owner_group,
            members=members,
        )


class _Users:
    def __init__(self, *, user_name: str, user_id: str, is_service_principal: bool) -> None:
        self.user_name = user_name
        self.user_id = user_id
        self.is_service_principal = is_service_principal

    def list(self, *, filter: str | None = None, **_: object) -> list[object]:
        if self.is_service_principal:
            return []
        if filter and self.user_name.casefold() not in filter.casefold():
            return []
        return [SimpleNamespace(id=self.user_id, user_name=self.user_name)]


class _AccountGroups(_Groups):
    pass


class _StatementExecution:
    def __init__(self) -> None:
        self.table_actions: list[str] = []
        self.statements: list[str] = []

    def execute_statement(self, *, statement: str, **_: object) -> object:
        self.statements.append(statement)
        if statement.startswith("REVOKE ALL PRIVILEGES ON TABLE"):
            self.table_actions = []
        if statement.startswith("GRANT SELECT, MODIFY ON TABLE"):
            self.table_actions = ["SELECT", "MODIFY"]
        elif statement.startswith("GRANT SELECT ON TABLE"):
            self.table_actions = ["SELECT"]
        return SimpleNamespace(
            status=SimpleNamespace(state="SUCCEEDED", error=None),
            result=SimpleNamespace(data_array=[]),
        )


class _Grants:
    def __init__(
        self,
        execution: _StatementExecution,
        *,
        parent_forbidden: bool = False,
        table_extra_calls: int = 0,
        metastore_forbidden: bool = False,
    ) -> None:
        self.execution = execution
        self.parent_forbidden = parent_forbidden
        self.table_extra_calls = table_extra_calls
        self.metastore_forbidden = metastore_forbidden
        self.calls: list[tuple[str, str, str | None]] = []

    @staticmethod
    def _response(privileges: set[str], *, next_page_token: str = "") -> object:
        entries = [
            SimpleNamespace(privilege=SimpleNamespace(value=privilege))
            for privilege in sorted(privileges)
        ]
        return SimpleNamespace(
            privilege_assignments=[SimpleNamespace(privileges=entries)],
            next_page_token=next_page_token,
        )

    def get_effective(
        self,
        securable_type: str,
        full_name: str,
        *,
        principal: str | None = None,
        page_token: str | None = None,
        max_results: int | None = None,
    ) -> object:
        assert max_results == 0
        self.calls.append((securable_type, full_name, page_token))
        if securable_type == "metastore":
            privileges = {"USE_MARKETPLACE_ASSETS"}
            if self.metastore_forbidden:
                privileges.add("CREATE_CATALOG")
            return self._response(privileges)
        if securable_type == "catalog":
            privileges = {"USE_CATALOG"}
        elif securable_type == "schema":
            privileges = {"USE_SCHEMA"}
            if self.parent_forbidden:
                privileges.add("SELECT")
        elif securable_type == "table":
            privileges = set(self.execution.table_actions)
            if self.table_extra_calls:
                self.table_extra_calls -= 1
                privileges.add("MANAGE")
        else:
            raise AssertionError(f"unexpected securable type {securable_type}")
        assert principal == "app-client"
        return self._response(privileges)


def _workspace(
    *,
    catalog_exists: bool = True,
    schema_exists: bool = True,
    table_exists: bool = True,
    parent_forbidden: bool = False,
    table_extra_calls: int = 0,
    owner: str = "deployer",
    admin_role: bool = False,
    metastore_forbidden: bool = False,
    metastore_owner: str = "deployer",
    catalog_denied: bool = False,
    current_user_name: str = "deployer",
    current_user_id: str = "deployer-id",
    current_display_name: str = "Deployer",
    current_application_id: str = "",
    owner_group: str | None = None,
    target_is_owner_group_member: bool = False,
) -> tuple[object, _StatementExecution, _Grants]:
    execution = _StatementExecution()
    grants = _Grants(
        execution,
        parent_forbidden=parent_forbidden,
        table_extra_calls=table_extra_calls,
        metastore_forbidden=metastore_forbidden,
    )
    workspace = SimpleNamespace(
        statement_execution=execution,
        service_principals=_ServicePrincipals(
            admin_role=admin_role,
            deployer_application_id=current_application_id,
            deployer_id=current_user_id,
        ),
        groups=_Groups(
            owner_group=owner_group,
            target_is_member=target_is_owner_group_member,
        ),
        users=_Users(
            user_name=current_user_name,
            user_id=current_user_id,
            is_service_principal=bool(current_application_id),
        ),
        grants=grants,
        metastores=SimpleNamespace(
            current=lambda: SimpleNamespace(metastore_id="metastore-1"),
            get=lambda metastore_id: SimpleNamespace(
                metastore_id=metastore_id,
                owner=metastore_owner,
            ),
        ),
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(
                user_name=current_user_name,
                display_name=current_display_name,
                id=current_user_id,
                application_id=current_application_id,
            )
        ),
        catalogs=_UcObjects(exists=catalog_exists, owner=owner, denied=catalog_denied),
        schemas=_UcObjects(exists=schema_exists, owner=owner),
        tables=_UcObjects(exists=table_exists, owner=owner),
    )
    return workspace, execution, grants


def _account_factory(*, owner_group: str, target_is_member: bool) -> object:
    return SimpleNamespace(
        config=SimpleNamespace(client_id="dedicated-account-client"),
        service_principals=SimpleNamespace(
            list=lambda **_: [
                SimpleNamespace(
                    id="account-sp-id",
                    application_id="app-client",
                )
            ]
        ),
        groups=_AccountGroups(
            owner_group=owner_group,
            target_is_member=target_is_member,
            target_member_id="account-sp-id",
        ),
    )


def test_account_client_uses_dedicated_oauth_without_workspace_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    sentinel = object()

    def account_client(**kwargs: object) -> object:
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr("tools.databricks.uc_owner_policy.AccountClient", account_client)
    monkeypatch.setenv("DATABRICKS_HOST", "https://workspace.example.invalid")
    monkeypatch.setenv("DATABRICKS_TOKEN", "workspace-pat-must-not-be-used")
    monkeypatch.setenv("DATABRICKS_AUTH_TYPE", "pat")
    monkeypatch.setenv(
        "DATABRICKS_ACCOUNT_HOST",
        "https://accounts.cloud.databricks.com",
    )
    monkeypatch.setenv("DATABRICKS_ACCOUNT_ID", "account-id")
    monkeypatch.setenv("DATABRICKS_ACCOUNT_CLIENT_ID", "account-client-id")
    monkeypatch.setenv("DATABRICKS_ACCOUNT_CLIENT_SECRET", "account-client-secret")

    client = account_client_from_env()

    assert client is sentinel
    assert captured == {
        "host": "https://accounts.cloud.databricks.com",
        "account_id": "account-id",
        "client_id": "account-client-id",
        "client_secret": "account-client-secret",
        "auth_type": "oauth-m2m",
    }


@pytest.mark.parametrize(
    ("mode", "expected_actions"),
    [("quiesce", ["SELECT"]), ("runtime", ["SELECT", "MODIFY"])],
)
def test_converges_authoritative_exact_table_scoped_access(
    mode: str, expected_actions: list[str]
) -> None:
    workspace, execution, grants = _workspace()

    assert converge_campaign_treatment_access(
        warehouse_id="warehouse-1",
        catalog="mip",
        principal="app-client",
        mode=mode,  # type: ignore[arg-type]
        workspace=workspace,  # type: ignore[arg-type]
    )

    assert execution.table_actions == expected_actions
    assert ("catalog", "mip", None) in grants.calls
    assert ("schema", "mip.audit", None) in grants.calls
    assert ("table", "mip.audit.campaign_treatment_snapshot", None) in grants.calls


def test_runtime_failure_compensates_to_authoritative_read_only() -> None:
    workspace, execution, grants = _workspace(table_extra_calls=1)

    with pytest.raises(RuntimeError, match="privileges are not exact"):
        converge_campaign_treatment_access(
            warehouse_id="warehouse-1",
            catalog="mip",
            principal="app-client",
            mode="runtime",
            workspace=workspace,  # type: ignore[arg-type]
        )

    assert execution.table_actions == ["SELECT"]
    table_calls = [call for call in grants.calls if call[0] == "table"]
    assert len(table_calls) == 2


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"parent_forbidden": True}, "privileges are not exact"),
        ({"owner": "hidden-aim-owner-group"}, "outside the explicit approved-owner"),
        ({"metastore_owner": "app-client"}, "Target App service principal cannot own"),
        ({"metastore_forbidden": True}, "forbidden metastore privileges"),
    ],
)
def test_rejects_inherited_parent_owner_and_global_authority(
    kwargs: dict[str, object], message: str
) -> None:
    workspace, execution, _ = _workspace(**kwargs)

    with pytest.raises(RuntimeError, match=message):
        converge_campaign_treatment_access(
            warehouse_id="warehouse-1",
            catalog="mip",
            principal="app-client",
            mode="quiesce",
            workspace=workspace,  # type: ignore[arg-type]
        )

    assert "MODIFY" not in execution.table_actions


def test_rejects_target_app_as_deploying_identity_and_owner() -> None:
    workspace, execution, _ = _workspace(
        owner="app-client",
        metastore_owner="app-client",
        current_user_name="app-client",
        current_user_id="sp-id",
        current_display_name="app-sp",
    )

    with pytest.raises(RuntimeError, match="Deploying identity must be distinct"):
        converge_campaign_treatment_access(
            warehouse_id="warehouse-1",
            catalog="mip",
            principal="app-client",
            mode="quiesce",
            workspace=workspace,  # type: ignore[arg-type]
        )

    assert execution.statements == []


def test_rejects_target_app_canonical_application_id_as_deployer() -> None:
    workspace, execution, _ = _workspace(
        current_user_name="mutable-app-display",
        current_user_id="unexpected-workspace-id",
        current_application_id="app-client",
    )

    with pytest.raises(RuntimeError, match="Deploying identity must be distinct"):
        converge_campaign_treatment_access(
            warehouse_id="warehouse-1",
            catalog="mip",
            principal="app-client",
            mode="quiesce",
            workspace=workspace,  # type: ignore[arg-type]
        )

    assert execution.statements == []


def test_mutable_deployer_display_name_cannot_approve_target_app_owner() -> None:
    workspace, execution, _ = _workspace(
        owner="app-client",
        metastore_owner="app-client",
        current_user_name="distinct-deployer@example.com",
        current_user_id="distinct-deployer-id",
        current_display_name="app-client",
    )

    with pytest.raises(RuntimeError, match="Target App service principal cannot own"):
        converge_campaign_treatment_access(
            warehouse_id="warehouse-1",
            catalog="mip",
            principal="app-client",
            mode="quiesce",
            workspace=workspace,  # type: ignore[arg-type]
        )

    assert execution.statements == []


def test_approved_group_owner_uses_account_membership_and_delegated_deployer() -> None:
    owner_group = "Customer-Platform-Governance"
    workspace, execution, _ = _workspace(
        owner=owner_group,
        metastore_owner=owner_group,
        owner_group=owner_group,
    )
    account = _account_factory(owner_group=owner_group, target_is_member=False)

    probed_groups: list[str] = []
    assert converge_campaign_treatment_access(
        warehouse_id="warehouse-1",
        catalog="mip",
        principal="app-client",
        mode="quiesce",
        approved_owner_principals={owner_group},
        account_factory=lambda: account,  # type: ignore[arg-type]
        group_membership_probe=lambda *args: probed_groups.append(args[-1]) or False,
        workspace=workspace,  # type: ignore[arg-type]
    )
    assert execution.table_actions == ["SELECT"]
    assert probed_groups == [owner_group]


def test_distinct_service_principal_deployer_can_own_governed_objects() -> None:
    deployer_application_id = "trusted-deployment-sp"
    workspace, execution, _ = _workspace(
        owner=deployer_application_id,
        metastore_owner=deployer_application_id,
        current_user_name=deployer_application_id,
        current_user_id="trusted-deployment-sp-id",
        current_display_name="Trusted deployment automation",
        current_application_id=deployer_application_id,
    )

    assert converge_campaign_treatment_access(
        warehouse_id="warehouse-1",
        catalog="mip",
        principal="app-client",
        mode="quiesce",
        workspace=workspace,  # type: ignore[arg-type]
    )
    assert execution.table_actions == ["SELECT"]


def test_rejects_current_user_name_colliding_with_group_display_name() -> None:
    workspace, execution, _ = _workspace(
        owner="deployer",
        metastore_owner="deployer",
        owner_group="deployer",
    )

    with pytest.raises(RuntimeError, match="did not resolve to exactly one principal"):
        converge_campaign_treatment_access(
            warehouse_id="warehouse-1",
            catalog="mip",
            principal="app-client",
            mode="quiesce",
            workspace=workspace,  # type: ignore[arg-type]
        )

    assert execution.statements == []


def test_rejects_current_user_name_resolving_to_wrong_immutable_id() -> None:
    workspace, execution, _ = _workspace()
    workspace.users = _Users(
        user_name="deployer",
        user_id="different-deployer-id",
        is_service_principal=False,
    )

    with pytest.raises(RuntimeError, match="different immutable principal"):
        converge_campaign_treatment_access(
            warehouse_id="warehouse-1",
            catalog="mip",
            principal="app-client",
            mode="quiesce",
            workspace=workspace,  # type: ignore[arg-type]
        )

    assert execution.statements == []


def test_explicit_third_party_service_principal_owner_resolves_canonically() -> None:
    owner_application_id = "customer-governance-sp"
    workspace, execution, _ = _workspace(
        owner=owner_application_id,
        metastore_owner=owner_application_id,
    )
    target_api = workspace.service_principals

    def list_principals(*, filter: str | None = None, **kwargs: object) -> list[object]:
        if filter and owner_application_id in filter:
            return [
                SimpleNamespace(
                    id="customer-governance-sp-id",
                    application_id=owner_application_id,
                )
            ]
        return target_api.list(filter=filter, **kwargs)

    workspace.service_principals = SimpleNamespace(
        list=list_principals,
        get=target_api.get,
    )

    assert converge_campaign_treatment_access(
        warehouse_id="warehouse-1",
        catalog="mip",
        principal="app-client",
        mode="quiesce",
        approved_owner_principals={owner_application_id},
        workspace=workspace,  # type: ignore[arg-type]
    )
    assert execution.table_actions == ["SELECT"]


def test_rejects_app_membership_even_when_aim_omits_it_from_account_scim() -> None:
    owner_group = "customer-platform-governance"
    workspace, execution, _ = _workspace(
        owner=owner_group,
        metastore_owner=owner_group,
        owner_group=owner_group,
        target_is_owner_group_member=False,
    )
    account = _account_factory(owner_group=owner_group, target_is_member=False)

    with pytest.raises(RuntimeError, match="member of approved owner group"):
        converge_campaign_treatment_access(
            warehouse_id="warehouse-1",
            catalog="mip",
            principal="app-client",
            mode="quiesce",
            approved_owner_principals={owner_group},
            account_factory=lambda: account,  # type: ignore[arg-type]
            group_membership_probe=lambda *_: True,
            workspace=workspace,  # type: ignore[arg-type]
        )

    assert execution.statements == []


def test_rejects_nested_app_membership_in_approved_owner_group() -> None:
    owner_group = "customer-platform-governance"
    workspace, execution, _ = _workspace(
        owner=owner_group,
        metastore_owner=owner_group,
        owner_group=owner_group,
    )
    account_groups = {
        "child-group-id": SimpleNamespace(
            id="child-group-id",
            display_name="application-runtimes",
            members=[SimpleNamespace(value="account-sp-id")],
        ),
        "owner-group-id": SimpleNamespace(
            id="owner-group-id",
            display_name=owner_group,
            members=[SimpleNamespace(value="child-group-id")],
        ),
    }
    account = SimpleNamespace(
        config=SimpleNamespace(client_id="dedicated-account-client"),
        service_principals=SimpleNamespace(
            list=lambda **_: [SimpleNamespace(id="account-sp-id", application_id="app-client")]
        ),
        groups=SimpleNamespace(
            list=lambda **_: list(account_groups.values()),
            get=lambda group_id: account_groups[group_id],
        ),
    )

    with pytest.raises(RuntimeError, match="member of approved owner group"):
        converge_campaign_treatment_access(
            warehouse_id="warehouse-1",
            catalog="mip",
            principal="app-client",
            mode="quiesce",
            approved_owner_principals={owner_group},
            account_factory=lambda: account,  # type: ignore[arg-type]
            group_membership_probe=lambda *_: True,
            workspace=workspace,  # type: ignore[arg-type]
        )

    assert execution.statements == []


def test_group_membership_negative_proof_is_cached_per_policy_convergence() -> None:
    owner_group = "customer-platform-governance"
    workspace, execution, _ = _workspace(
        owner=owner_group,
        metastore_owner=owner_group,
        owner_group=owner_group,
    )
    account = _account_factory(owner_group=owner_group, target_is_member=False)
    probes: list[str] = []

    assert converge_campaign_treatment_access(
        warehouse_id="warehouse-1",
        catalog="mip",
        principal="app-client",
        mode="quiesce",
        approved_owner_principals={owner_group},
        account_factory=lambda: account,  # type: ignore[arg-type]
        group_membership_probe=lambda *args: probes.append(args[-1]) or False,
        workspace=workspace,  # type: ignore[arg-type]
    )

    assert execution.table_actions == ["SELECT"]
    assert probes == [owner_group]


def test_rejects_reused_account_client_before_target_secret_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_group = "customer-platform-governance"
    workspace, execution, _ = _workspace(
        owner=owner_group,
        metastore_owner=owner_group,
        owner_group=owner_group,
    )
    account = _account_factory(owner_group=owner_group, target_is_member=False)
    account.config.client_id = "app-client"
    monkeypatch.setenv("DATABRICKS_ACCOUNT_CLIENT_ID", "app-client")
    probe_called = False

    def probe(*_: object) -> bool:
        nonlocal probe_called
        probe_called = True
        return False

    with pytest.raises(RuntimeError, match="must be distinct"):
        converge_campaign_treatment_access(
            warehouse_id="warehouse-1",
            catalog="mip",
            principal="app-client",
            mode="quiesce",
            approved_owner_principals={owner_group},
            account_factory=lambda: account,  # type: ignore[arg-type]
            group_membership_probe=probe,
            workspace=workspace,  # type: ignore[arg-type]
        )

    assert not probe_called
    assert execution.statements == []


def test_target_group_probe_uses_short_lived_secret_and_deletes_it() -> None:
    secret_calls: list[tuple[object, ...]] = []

    class Secrets:
        def create(self, sp_id: str, *, lifetime: str) -> object:
            secret_calls.append(("create", sp_id, lifetime))
            return SimpleNamespace(id="temporary-secret-id", secret="temporary-value")

        def delete(self, sp_id: str, secret_id: str) -> None:
            secret_calls.append(("delete", sp_id, secret_id))

    workspace_kwargs: dict[str, object] = {}
    api_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def current_identity(*args: object, **kwargs: object) -> object:
        api_calls.append((args, kwargs))
        return {
            "id": "account-sp-id",
            "userName": "app-client",
            "groups": [{"value": "other-group-id", "display": "other-group"}],
        }

    def workspace_factory(**kwargs: object) -> object:
        workspace_kwargs.update(kwargs)
        return SimpleNamespace(api_client=SimpleNamespace(do=current_identity))

    account = SimpleNamespace(service_principal_secrets=Secrets())

    assert not target_group_membership_probe(
        account,  # type: ignore[arg-type]
        "account-sp-id",
        "app-client",
        "owner-group-id",
        "Customer's Governance",
        workspace_host="https://workspace.example.invalid",
        workspace_factory=workspace_factory,  # type: ignore[arg-type]
    )
    assert secret_calls == [
        ("create", "account-sp-id", "300s"),
        ("delete", "account-sp-id", "temporary-secret-id"),
    ]
    assert workspace_kwargs == {
        "host": "https://workspace.example.invalid",
        "client_id": "app-client",
        "client_secret": "temporary-value",
        "auth_type": "oauth-m2m",
    }
    assert api_calls == [
        (
            ("GET", "/api/2.0/preview/scim/v2/Me"),
            {
                "query": {"attributes": "id,userName,groups"},
                "headers": {"Accept": "application/json"},
            },
        )
    ]


def test_target_group_probe_recognizes_effective_group_by_immutable_id() -> None:
    class Secrets:
        def create(self, _sp_id: str, *, lifetime: str) -> object:
            assert lifetime == "300s"
            return SimpleNamespace(id="temporary-secret-id", secret="temporary-value")

        def delete(self, _sp_id: str, _secret_id: str) -> None:
            return None

    account = SimpleNamespace(service_principal_secrets=Secrets())
    workspace = SimpleNamespace(
        api_client=SimpleNamespace(
            do=lambda *_args, **_kwargs: {
                "id": "account-sp-id",
                "userName": "app-client",
                "groups": [{"value": "owner-group-id", "display": "MIP Owners"}],
            }
        )
    )

    assert target_group_membership_probe(
        account,  # type: ignore[arg-type]
        "account-sp-id",
        "app-client",
        "owner-group-id",
        "mip owners",
        workspace_host="https://workspace.example.invalid",
        workspace_factory=lambda **_: workspace,  # type: ignore[arg-type]
    )


def test_target_group_probe_fails_closed_when_groups_are_omitted() -> None:
    deleted: list[tuple[str, str]] = []

    class Secrets:
        def create(self, _sp_id: str, *, lifetime: str) -> object:
            assert lifetime == "300s"
            return SimpleNamespace(id="temporary-secret-id", secret="temporary-value")

        def delete(self, sp_id: str, secret_id: str) -> None:
            deleted.append((sp_id, secret_id))

    account = SimpleNamespace(service_principal_secrets=Secrets())
    workspace = SimpleNamespace(
        api_client=SimpleNamespace(
            do=lambda *_args, **_kwargs: {
                "id": "account-sp-id",
                "userName": "app-client",
            }
        )
    )

    with pytest.raises(RuntimeError, match="omitted the authoritative groups"):
        target_group_membership_probe(
            account,  # type: ignore[arg-type]
            "account-sp-id",
            "app-client",
            "owner-group-id",
            "mip owners",
            workspace_host="https://workspace.example.invalid",
            workspace_factory=lambda **_: workspace,  # type: ignore[arg-type]
        )

    assert deleted == [("account-sp-id", "temporary-secret-id")]


@pytest.mark.parametrize(
    ("identity", "error"),
    [
        (
            {"id": "wrong-sp-id", "userName": "app-client", "groups": []},
            "different target identity",
        ),
        (
            {"id": "account-sp-id", "userName": "wrong-client", "groups": []},
            "different target identity",
        ),
        (
            {
                "id": "account-sp-id",
                "userName": "app-client",
                "groups": [{"value": "owner-group-id", "display": "wrong owners"}],
            },
            "mismatched group name",
        ),
        (
            {
                "id": "account-sp-id",
                "userName": "app-client",
                "groups": [{"value": "wrong-group-id", "display": "mip owners"}],
            },
            "mismatched group id",
        ),
        (
            {
                "id": "account-sp-id",
                "userName": "app-client",
                "groups": [{"display": "unidentified group"}],
            },
            "group without an id",
        ),
    ],
)
def test_target_group_probe_rejects_mismatched_or_malformed_identity_evidence(
    identity: dict[str, object], error: str
) -> None:
    deleted: list[tuple[str, str]] = []

    class Secrets:
        def create(self, _sp_id: str, *, lifetime: str) -> object:
            assert lifetime == "300s"
            return SimpleNamespace(id="temporary-secret-id", secret="temporary-value")

        def delete(self, sp_id: str, secret_id: str) -> None:
            deleted.append((sp_id, secret_id))

    account = SimpleNamespace(service_principal_secrets=Secrets())
    workspace = SimpleNamespace(api_client=SimpleNamespace(do=lambda *_args, **_kwargs: identity))

    with pytest.raises(RuntimeError, match=error):
        target_group_membership_probe(
            account,  # type: ignore[arg-type]
            "account-sp-id",
            "app-client",
            "owner-group-id",
            "mip owners",
            workspace_host="https://workspace.example.invalid",
            workspace_factory=lambda **_: workspace,  # type: ignore[arg-type]
        )

    assert deleted == [("account-sp-id", "temporary-secret-id")]


def test_target_group_probe_fails_closed_when_secret_cleanup_fails() -> None:
    class Secrets:
        def create(self, sp_id: str, *, lifetime: str) -> object:
            return SimpleNamespace(id="temporary-secret-id", secret="temporary-value")

        def delete(self, sp_id: str, secret_id: str) -> None:
            raise RuntimeError("delete denied")

    identity = {"id": "account-sp-id", "userName": "app-client", "groups": []}
    account = SimpleNamespace(service_principal_secrets=Secrets())

    with pytest.raises(RuntimeError, match="cleanup could not be proven"):
        target_group_membership_probe(
            account,  # type: ignore[arg-type]
            "account-sp-id",
            "app-client",
            "owner-group-id",
            "customer-platform-governance",
            workspace_host="https://workspace.example.invalid",
            workspace_factory=lambda **_: SimpleNamespace(
                api_client=SimpleNamespace(do=lambda *_args, **_kwargs: identity)
            ),  # type: ignore[arg-type]
        )


def test_rejects_admin_role_before_any_object_absence_claim() -> None:
    workspace, execution, _ = _workspace(
        admin_role=True,
        catalog_exists=False,
        schema_exists=False,
        table_exists=False,
    )

    with pytest.raises(SystemExit, match="administrative role"):
        converge_campaign_treatment_access(
            warehouse_id="warehouse-1",
            catalog="mip",
            principal="app-client",
            mode="quiesce",
            workspace=workspace,  # type: ignore[arg-type]
        )

    assert execution.statements == []


@pytest.mark.parametrize(
    ("catalog_exists", "schema_exists"),
    [(False, False), (True, False), (True, True)],
)
def test_quiesce_recovers_authoritatively_from_partial_first_install(
    catalog_exists: bool, schema_exists: bool
) -> None:
    workspace, execution, grants = _workspace(
        catalog_exists=catalog_exists,
        schema_exists=schema_exists,
        table_exists=False,
    )

    assert not converge_campaign_treatment_access(
        warehouse_id="warehouse-1",
        catalog="mip",
        principal="app-client",
        mode="quiesce",
        workspace=workspace,  # type: ignore[arg-type]
    )

    assert ("metastore", "metastore-1", None) in grants.calls
    if not catalog_exists:
        assert execution.statements == []
    elif not schema_exists:
        assert any(" ON CATALOG " in sql for sql in execution.statements)
        assert not any(" ON SCHEMA " in sql for sql in execution.statements)
    else:
        assert any(" ON SCHEMA " in sql for sql in execution.statements)
        assert not any(" ON TABLE " in sql for sql in execution.statements)


def test_authoritative_object_permission_error_is_not_treated_as_absence() -> None:
    workspace, execution, _ = _workspace(catalog_denied=True)

    with pytest.raises(PermissionDenied, match="hidden is not absent"):
        converge_campaign_treatment_access(
            warehouse_id="warehouse-1",
            catalog="mip",
            principal="app-client",
            mode="quiesce",
            workspace=workspace,  # type: ignore[arg-type]
        )

    assert execution.statements == []


def test_effective_grant_pagination_is_complete_and_bounded() -> None:
    class _PagedGrants:
        def __init__(self) -> None:
            self.max_results: list[int | None] = []

        def get_effective(
            self,
            *_: object,
            page_token: str | None = None,
            max_results: int | None = None,
            **__: object,
        ) -> object:
            self.max_results.append(max_results)
            privilege = "SELECT" if page_token is None else "MODIFY"
            next_token = "page-2" if page_token is None else ""
            return _Grants._response({privilege}, next_page_token=next_token)

    grants = _PagedGrants()
    workspace = SimpleNamespace(grants=grants)

    assert _effective_privileges(
        workspace,  # type: ignore[arg-type]
        securable_type="table",
        full_name="mip.audit.campaign_treatment_snapshot",
        principal="app-client",
    ) == {"SELECT", "MODIFY"}
    assert grants.max_results == [0, 0]


def test_runtime_quiesces_parent_authority_before_refusing_missing_table() -> None:
    workspace, execution, _ = _workspace(table_exists=False)

    with pytest.raises(RuntimeError, match="before the treatment table exists"):
        converge_campaign_treatment_access(
            warehouse_id="warehouse-1",
            catalog="mip",
            principal="app-client",
            mode="runtime",
            workspace=workspace,  # type: ignore[arg-type]
        )

    assert any(" ON CATALOG " in sql for sql in execution.statements)
    assert any(" ON SCHEMA " in sql for sql in execution.statements)
    assert not any(" ON TABLE " in sql for sql in execution.statements)


def test_quiesce_handles_table_appearing_between_presence_reads() -> None:
    workspace, execution, grants = _workspace(table_exists=False)
    execution.table_actions = ["SELECT", "MODIFY"]

    class _AppearingTable:
        def __init__(self) -> None:
            self.calls = 0

        def get(self, _: str) -> object:
            self.calls += 1
            if self.calls == 1:
                raise NotFound("initially absent")
            return SimpleNamespace(owner="deployer")

    appearing = _AppearingTable()
    workspace.tables = appearing

    assert converge_campaign_treatment_access(
        warehouse_id="warehouse-1",
        catalog="mip",
        principal="app-client",
        mode="quiesce",
        workspace=workspace,  # type: ignore[arg-type]
    )

    assert appearing.calls >= 3
    assert execution.table_actions == ["SELECT"]
    assert any("REVOKE ALL PRIVILEGES ON TABLE" in sql for sql in execution.statements)
    assert ("table", "mip.audit.campaign_treatment_snapshot", None) in grants.calls


@pytest.mark.parametrize("principal", ["", "bad`principal"])
def test_rejects_unsafe_principal(principal: str) -> None:
    workspace, execution, _ = _workspace()

    with pytest.raises(ValueError, match="principal"):
        converge_campaign_treatment_access(
            warehouse_id="warehouse-1",
            catalog="mip",
            principal=principal,
            mode="runtime",
            workspace=workspace,  # type: ignore[arg-type]
        )

    assert execution.statements == []
