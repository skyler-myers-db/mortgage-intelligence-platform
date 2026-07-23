from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.errors import PermissionDenied

from tools.databricks import audit_agent_runtime_foreign_uc_access as auditor

APPLICATION_ID = "runtime-client"
INVENTORY_PRINCIPAL = "deployer@example.com"
CATALOG = "mip"
WORKSPACE_ID = "7474645995341779"
OTHER_WORKSPACE_ID = "2478181912221244"
FOREIGN_OWNER = "foreign-owner@example.com"
ACCOUNT_SCIM_ID = "account-runtime-id"


def _assignment(
    *privileges: str,
    principal: str = APPLICATION_ID,
    inherited_type: str | None = None,
    inherited_name: str | None = None,
) -> object:
    return SimpleNamespace(
        principal=principal,
        privileges=[
            SimpleNamespace(
                privilege=privilege,
                inherited_from_type=inherited_type,
                inherited_from_name=inherited_name,
            )
            for privilege in privileges
        ],
    )


class _Grants:
    def __init__(self, values: dict[tuple[str, str], list[object]] | None = None) -> None:
        self.values = values or {}
        self.denied: tuple[str, str] | None = None
        self.paginate: tuple[str, str] | None = None
        self.calls: list[tuple[str, str, str | None]] = []

    def get_effective(
        self,
        securable_type: str,
        full_name: str,
        *,
        principal: str,
        max_results: int,
        page_token: str | None,
    ) -> object:
        assert principal == APPLICATION_ID
        assert max_results == 1000
        self.calls.append((securable_type, full_name, page_token))
        if self.denied == (securable_type, full_name):
            raise PermissionDenied("metastore authority required")
        if self.paginate == (securable_type, full_name) and page_token is None:
            return SimpleNamespace(privilege_assignments=[], next_page_token="page-2")
        if self.paginate == (securable_type, full_name):
            assert page_token == "page-2"
        else:
            assert page_token is None
        return SimpleNamespace(
            privilege_assignments=self.values.get((securable_type, full_name), []),
            next_page_token=None,
        )


def _workspace(
    values: dict[tuple[str, str], list[object]] | None = None,
    *,
    owner: str = INVENTORY_PRINCIPAL,
    extra_catalogs: list[object] | None = None,
    owner_overrides: dict[str, str] | None = None,
) -> object:
    owner_overrides = owner_overrides or {}

    def object_owner(full_name: str) -> str:
        return owner_overrides.get(full_name, FOREIGN_OWNER)

    schemas = {
        "other": [
            SimpleNamespace(
                name="sandbox",
                full_name="other.sandbox",
                owner=object_owner("other.sandbox"),
            ),
            SimpleNamespace(
                name="information_schema",
                full_name="other.information_schema",
                owner=owner_overrides.get("other.information_schema", "System user"),
            ),
        ]
    }
    functions = {
        ("other", "sandbox"): [
            SimpleNamespace(
                name="secret_fn",
                full_name="other.sandbox.secret_fn",
                owner=object_owner("other.sandbox.secret_fn"),
            )
        ],
        ("other", "information_schema"): [],
    }
    tables = {
        ("other", "sandbox"): [
            SimpleNamespace(
                name="secret",
                full_name="other.sandbox.secret",
                owner=object_owner("other.sandbox.secret"),
            )
        ],
        ("other", "information_schema"): [
            SimpleNamespace(
                name="tables",
                full_name="other.information_schema.tables",
                owner=owner_overrides.get(
                    "other.information_schema.tables",
                    "System user",
                ),
            )
        ],
    }
    volumes = {
        ("other", "sandbox"): [
            SimpleNamespace(
                name="private",
                full_name="other.sandbox.private",
                owner=object_owner("other.sandbox.private"),
            )
        ],
        ("other", "information_schema"): [],
    }
    baseline = {
        ("schema", "other.information_schema"): [
            _assignment("USE_SCHEMA", principal="account users")
        ],
        ("table", "other.information_schema.tables"): [
            _assignment("SELECT", principal="account users")
        ],
    }
    baseline.update(values or {})
    catalogs = [
        SimpleNamespace(
            name=CATALOG,
            isolation_mode="OPEN",
            owner="mip-owner",
            catalog_type="MANAGED_CATALOG",
        ),
        SimpleNamespace(
            name="other",
            isolation_mode="OPEN",
            owner=object_owner("other"),
            catalog_type="MANAGED_CATALOG",
        ),
        SimpleNamespace(
            name="system",
            isolation_mode="OPEN",
            owner="System user",
            catalog_type="SYSTEM_CATALOG",
        ),
        SimpleNamespace(
            name="samples",
            isolation_mode="OPEN",
            owner="System user",
            catalog_type="MANAGED_CATALOG",
        ),
        SimpleNamespace(
            name="__databricks_internal",
            isolation_mode="OPEN",
            owner="System user",
            catalog_type="INTERNAL_CATALOG",
        ),
        *(extra_catalogs or []),
    ]
    for item in catalogs:
        if not hasattr(item, "catalog_type"):
            item.catalog_type = "MANAGED_CATALOG"

    def list_catalogs(**kwargs: object) -> Any:
        assert kwargs == {"include_browse": True, "include_unbound": True}
        return iter(catalogs)

    return SimpleNamespace(
        config=SimpleNamespace(
            workspace_id=WORKSPACE_ID,
            host="https://workspace.example.invalid",
        ),
        get_workspace_id=lambda: int(WORKSPACE_ID),
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(
                user_name=INVENTORY_PRINCIPAL,
                id="deployer-scim-id",
                groups=[
                    SimpleNamespace(display="admins"),
                    SimpleNamespace(display="metastore-owners"),
                ],
            )
        ),
        metastores=SimpleNamespace(
            current=lambda: SimpleNamespace(metastore_id="metastore-id"),
            get=lambda _metastore_id: SimpleNamespace(owner=owner),
        ),
        catalogs=SimpleNamespace(list=list_catalogs),
        workspace_bindings=SimpleNamespace(
            get_bindings=lambda _type, _name: iter(
                [
                    SimpleNamespace(
                        workspace_id=WORKSPACE_ID,
                        binding_type="BINDING_TYPE_READ_WRITE",
                    )
                ]
            )
        ),
        schemas=SimpleNamespace(list=lambda catalog, **_kwargs: iter(schemas[catalog])),
        functions=SimpleNamespace(
            list=lambda catalog, schema, **_kwargs: iter(functions[(catalog, schema)])
        ),
        tables=SimpleNamespace(
            list=lambda catalog, schema, **_kwargs: iter(tables[(catalog, schema)])
        ),
        volumes=SimpleNamespace(
            list=lambda catalog, schema, **_kwargs: iter(volumes[(catalog, schema)])
        ),
        users=SimpleNamespace(
            list=lambda **_kwargs: iter(
                [SimpleNamespace(user_name=FOREIGN_OWNER, id="foreign-owner-id")]
            )
        ),
        service_principals=SimpleNamespace(
            list=lambda **kwargs: iter(
                [
                    SimpleNamespace(
                        application_id=APPLICATION_ID,
                        id="runtime-scim-id",
                        display_name="runtime-workspace",
                    )
                ]
                if APPLICATION_ID in str(kwargs.get("filter", ""))
                else []
            )
        ),
        groups=SimpleNamespace(list=lambda **_kwargs: iter([])),
        registered_models=SimpleNamespace(
            list=lambda **_kwargs: iter(
                [
                    SimpleNamespace(full_name="mip.audit.reviewed", catalog_name="mip"),
                    SimpleNamespace(
                        full_name="other.sandbox.secret_model",
                        catalog_name="other",
                        owner=object_owner("other.sandbox.secret_model"),
                    ),
                    SimpleNamespace(
                        full_name="system.ai.reviewed",
                        catalog_name="system",
                    ),
                    SimpleNamespace(
                        full_name="samples.tpch.reviewed",
                        catalog_name="samples",
                    ),
                ]
            )
        ),
        grants=_Grants(baseline),
    )


def _add_managed_online_catalog(
    workspace: Any,
    *,
    catalog: str = "online_state",
    owner: str = FOREIGN_OWNER,
    information_schema_owner: str | None = None,
    information_table_owner: str | None = None,
    information_schema_full_name: str | None = None,
    information_table_full_name: str | None = None,
    unknown_information_table_owner: str | None = None,
) -> None:
    catalogs = list(workspace.catalogs.list(include_browse=True, include_unbound=True))
    catalogs.append(
        SimpleNamespace(
            name=catalog,
            isolation_mode="OPEN",
            owner=owner,
            catalog_type="MANAGED_ONLINE_CATALOG",
        )
    )
    workspace.catalogs.list = lambda **_kwargs: iter(catalogs)

    original_schema_list = workspace.schemas.list
    workspace.schemas.list = lambda selected, **kwargs: (
        iter(
            [
                SimpleNamespace(
                    name="information_schema",
                    full_name=information_schema_full_name
                    or f"{catalog}.information_schema",
                    owner=information_schema_owner or owner,
                )
            ]
        )
        if selected == catalog
        else original_schema_list(selected, **kwargs)
    )

    original_function_list = workspace.functions.list
    workspace.functions.list = lambda selected, schema, **kwargs: (
        iter([])
        if selected == catalog
        else original_function_list(selected, schema, **kwargs)
    )

    original_table_list = workspace.tables.list
    workspace.tables.list = lambda selected, schema, **kwargs: (
        iter(
            [
                SimpleNamespace(
                    name="tables",
                    full_name=information_table_full_name
                    or f"{catalog}.information_schema.tables",
                    owner=information_table_owner or owner,
                ),
                *(
                    [
                        SimpleNamespace(
                            name="future_metadata",
                            full_name=f"{catalog}.information_schema.future_metadata",
                            owner=unknown_information_table_owner,
                        )
                    ]
                    if unknown_information_table_owner is not None
                    else []
                ),
            ]
        )
        if selected == catalog
        else original_table_list(selected, schema, **kwargs)
    )

    original_volume_list = workspace.volumes.list
    workspace.volumes.list = lambda selected, schema, **kwargs: (
        iter([])
        if selected == catalog
        else original_volume_list(selected, schema, **kwargs)
    )


def _binding_policy(
    catalog: str = "hidden",
    *,
    owner: str = FOREIGN_OWNER,
) -> str:
    return json.dumps(
        {
            "version": 1,
            "catalogs": {
                catalog: {
                    "owner": owner,
                    "catalog_type": "MANAGED_CATALOG",
                    "bindings": [
                        {
                            "workspace_id": OTHER_WORKSPACE_ID,
                            "binding_type": "BINDING_TYPE_READ_WRITE",
                        }
                    ],
                }
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _account() -> object:
    account_users = SimpleNamespace(
        id="account-users-id",
        display_name="account users",
        members=[SimpleNamespace(value=ACCOUNT_SCIM_ID)],
    )
    return SimpleNamespace(
        config=SimpleNamespace(client_id="account-auditor"),
        service_principals=SimpleNamespace(
            list=lambda **_kwargs: iter(
                [
                    SimpleNamespace(
                        application_id=APPLICATION_ID,
                        id=ACCOUNT_SCIM_ID,
                        display_name="runtime",
                        active=True,
                    )
                ]
            )
        ),
        groups=SimpleNamespace(
            list=lambda **_kwargs: iter([account_users]),
            get=lambda group_id: account_users
            if group_id == "account-users-id"
            else None,
        ),
        metastore_assignments=SimpleNamespace(
            list=lambda _metastore_id: iter([WORKSPACE_ID, OTHER_WORKSPACE_ID])
        ),
        workspace_assignment=SimpleNamespace(
            list=lambda workspace_id: iter(
                [
                    SimpleNamespace(
                        permissions=["USER"],
                        principal=SimpleNamespace(
                            principal_id=ACCOUNT_SCIM_ID,
                            service_principal_name=APPLICATION_ID,
                            group_name=None,
                        ),
                    )
                ]
                if str(workspace_id) == WORKSPACE_ID
                else []
            )
        ),
    )


def _audit(workspace: Any, **kwargs: Any) -> object:
    kwargs.setdefault("account_factory", _account)
    kwargs.setdefault(
        "target_groups_probe",
        lambda *_args, **_probe_kwargs: {"account-users-id": "account users"},
    )
    return auditor.audit_foreign_uc_access(
        workspace,
        application_id=APPLICATION_ID,
        catalog=CATALOG,
        expected_inventory_principal=INVENTORY_PRINCIPAL,
        **kwargs,
    )


def test_foreign_catalog_binding_policy_rejects_ambiguous_json() -> None:
    with pytest.raises(ValueError, match="duplicate key"):
        auditor.parse_foreign_catalog_binding_policy(
            '{"version":1,"version":1,"catalogs":{}}'
        )


@pytest.mark.parametrize(
    "value",
    [
        "[]",
        '{"version":true,"catalogs":{}}',
        '{"version":1.0,"catalogs":{}}',
        '{"version":2,"catalogs":{}}',
        '{"version":1,"catalogs":{"hidden":{"owner":"owner","catalog_type":'
        '"MANAGED_CATALOG","bindings":[]}}}',
        '{"version":1,"catalogs":{"hidden":{"owner":"owner","catalog_type":'
        '"MANAGED_CATALOG","bindings":[{"workspace_id":"01","binding_type":'
        '"BINDING_TYPE_READ_WRITE"}]}}}',
        '{"version":1,"catalogs":{"hidden":{"owner":"owner","catalog_type":'
        '"MANAGED_CATALOG","bindings":[{"workspace_id":"2478181912221244",'
        '"binding_type":"UNKNOWN"}]}}}',
    ],
)
def test_foreign_catalog_binding_policy_rejects_invalid_contract(value: str) -> None:
    with pytest.raises(ValueError, match="foreign catalog binding policy"):
        auditor.parse_foreign_catalog_binding_policy(value)


def test_foreign_uc_control_plane_rejects_runtime_foreign_workspace_assignment() -> None:
    account = _account()
    original = account.workspace_assignment.list
    account.workspace_assignment.list = lambda workspace_id: iter(
        [
            SimpleNamespace(
                permissions=["USER"],
                principal=SimpleNamespace(
                    principal_id=ACCOUNT_SCIM_ID,
                    service_principal_name=APPLICATION_ID,
                    group_name=None,
                ),
            )
        ]
        if str(workspace_id) in {WORKSPACE_ID, OTHER_WORKSPACE_ID}
        else list(original(workspace_id))
    )

    with pytest.raises(RuntimeError, match="unexpected account workspace assignment"):
        _audit(_workspace(), account_factory=lambda: account)


def test_foreign_uc_control_plane_rejects_assignment_name_omission_for_target_id() -> None:
    account = _account()
    account.workspace_assignment.list = lambda workspace_id: iter(
        [
            SimpleNamespace(
                permissions=["USER"],
                principal=SimpleNamespace(
                    principal_id=ACCOUNT_SCIM_ID,
                    service_principal_name=(
                        APPLICATION_ID if str(workspace_id) == WORKSPACE_ID else ""
                    ),
                    group_name=None,
                ),
            )
        ]
        if str(workspace_id) in {WORKSPACE_ID, OTHER_WORKSPACE_ID}
        else []
    )

    with pytest.raises(RuntimeError, match="assignment inventory is incomplete"):
        _audit(_workspace(), account_factory=lambda: account)


def test_foreign_uc_control_plane_rejects_group_assignment_as_direct_runtime() -> None:
    account = _account()
    direct = list(account.workspace_assignment.list(int(WORKSPACE_ID)))
    account.workspace_assignment.list = lambda workspace_id: iter(
        [
            *direct,
            SimpleNamespace(
                permissions=["USER"],
                principal=SimpleNamespace(
                    principal_id="account-users-id",
                    service_principal_name="",
                    group_name="account users",
                    user_name="",
                ),
            ),
        ]
        if str(workspace_id) == WORKSPACE_ID
        else []
    )

    with pytest.raises(RuntimeError, match="unexpected account workspace assignment"):
        _audit(_workspace(), account_factory=lambda: account)


def test_foreign_uc_control_plane_rejects_mismatched_system_group_assignment() -> None:
    account = _account()
    account.workspace_assignment.list = lambda workspace_id: iter(
        [
            SimpleNamespace(
                permissions=["USER"],
                principal=SimpleNamespace(
                    principal_id="wrong-id",
                    service_principal_name="",
                    group_name="account users",
                    user_name="",
                ),
            )
        ]
        if str(workspace_id) == WORKSPACE_ID
        else []
    )

    with pytest.raises(RuntimeError, match="group assignment identity fields disagree"):
        _audit(_workspace(), account_factory=lambda: account)


def test_foreign_uc_control_plane_rejects_unnamed_retained_workspace_assignment() -> None:
    account = _account()
    account.groups.list = lambda **_kwargs: iter([])
    account.groups.get = lambda _group_id: None
    original = account.workspace_assignment.list
    account.workspace_assignment.list = lambda workspace_id: iter(
        [
            SimpleNamespace(
                permissions=["USER"],
                principal=SimpleNamespace(
                    principal_id="account-users-id",
                    service_principal_name="",
                    group_name="",
                    user_name="",
                ),
            )
        ]
        if str(workspace_id) == OTHER_WORKSPACE_ID
        else original(workspace_id)
    )

    with pytest.raises(RuntimeError, match="assignment inventory is incomplete"):
        _audit(
            _workspace(),
            account_factory=lambda: account,
            target_groups_probe=lambda *_args, **_kwargs: {
                "account-users-id": "account users"
            },
        )


def test_foreign_uc_control_plane_rejects_runtime_non_system_account_group() -> None:
    account = _account()
    account_users = next(account.groups.list())
    foreign_group = SimpleNamespace(
        id="foreign-group-id",
        display_name="foreign-data-users",
        members=[SimpleNamespace(value=ACCOUNT_SCIM_ID)],
    )
    account.groups.list = lambda **_kwargs: iter(
        [
            account_users,
            SimpleNamespace(
                id="foreign-group-id",
                display_name="foreign-data-users",
                members=[],
            ),
        ]
    )
    account.groups.get = lambda group_id: (
        foreign_group if group_id == "foreign-group-id" else account_users
    )

    with pytest.raises(RuntimeError, match="forbidden ordinary account group"):
        _audit(
            _workspace(),
            account_factory=lambda: account,
            target_groups_probe=lambda *_args, **_kwargs: {
                "account-users-id": "account users",
                "foreign-group-id": "foreign-data-users",
            },
        )


def test_foreign_uc_control_plane_accepts_implicit_account_users_baseline() -> None:
    account = _account()
    account.groups.list = lambda **_kwargs: iter([])
    account.groups.get = lambda _group_id: None

    proof = _audit(_workspace(), account_factory=lambda: account)

    assert proof.application_id == APPLICATION_ID
    assert proof.workspace_id == WORKSPACE_ID


def test_foreign_uc_control_plane_accepts_target_omitted_account_users_baseline() -> None:
    proof = _audit(
        _workspace(),
        target_groups_probe=lambda *_args, **_kwargs: {},
    )

    assert proof.application_id == APPLICATION_ID
    assert proof.workspace_id == WORKSPACE_ID


def test_foreign_uc_control_plane_uses_one_frozen_target_group_snapshot() -> None:
    calls: list[tuple[str, str, str, str]] = []

    def probe(
        _account: object,
        account_sp_id: str,
        application_id: str,
        *,
        expected_workspace_scim_id: str,
        workspace_host: str,
    ) -> dict[str, str]:
        calls.append(
            (
                account_sp_id,
                expected_workspace_scim_id,
                application_id,
                workspace_host,
            )
        )
        return {"account-users-id": "account users"}

    proof = _audit(_workspace(), target_groups_probe=probe)

    assert proof.application_id == APPLICATION_ID
    assert calls == [
        (
            ACCOUNT_SCIM_ID,
            "runtime-scim-id",
            APPLICATION_ID,
            "https://workspace.example.invalid",
        )
    ]


def test_foreign_uc_control_plane_rejects_dynamic_target_group_absent_from_account_members() -> None:
    account = _account()
    account.groups.list = lambda **_kwargs: iter([])
    account.groups.get = lambda _group_id: None

    with pytest.raises(RuntimeError, match="forbidden ordinary account group"):
        _audit(
            _workspace(),
            account_factory=lambda: account,
            target_groups_probe=lambda *_args, **_kwargs: {
                "dynamic-id": "dynamic-governance"
            },
        )


def test_foreign_uc_control_plane_rejects_ordinary_group_missing_from_target_snapshot() -> None:
    account = _account()
    account_users = next(account.groups.list())
    ordinary_group = SimpleNamespace(
        id="ordinary-group-id",
        display_name="ordinary-group",
        members=[SimpleNamespace(value=ACCOUNT_SCIM_ID)],
    )
    account.groups.list = lambda **_kwargs: iter([account_users, ordinary_group])
    account.groups.get = lambda group_id: (
        ordinary_group if group_id == ordinary_group.id else account_users
    )

    with pytest.raises(RuntimeError, match="account and credentialed group identities disagree"):
        _audit(
            _workspace(),
            account_factory=lambda: account,
            target_groups_probe=lambda *_args, **_kwargs: {},
        )


def test_foreign_uc_control_plane_rejects_mismatched_credentialed_system_group_id() -> None:
    account = _account()
    account_users = next(account.groups.list())
    account_users.members = []

    with pytest.raises(RuntimeError, match="managed system group identities disagree"):
        _audit(
            _workspace(),
            account_factory=lambda: account,
            target_groups_probe=lambda *_args, **_kwargs: {
                "different-id": "account users"
            },
        )


def test_foreign_uc_control_plane_rejects_duplicate_account_group_display_names() -> None:
    account = _account()
    account_users = next(account.groups.list())
    duplicate = SimpleNamespace(
        id="duplicate-account-users-id",
        display_name="Account Users",
        members=[],
    )
    account.groups.list = lambda **_kwargs: iter([account_users, duplicate])
    account.groups.get = lambda group_id: (
        duplicate if group_id == duplicate.id else account_users
    )

    with pytest.raises(RuntimeError, match="duplicate display name"):
        _audit(_workspace(), account_factory=lambda: account)


@pytest.mark.parametrize(
    "snapshot",
    [
        [],
        {"": "account users"},
        {"account-users-id": ""},
        {"group-1": "Same Group", "group-2": " same group "},
    ],
)
def test_foreign_uc_control_plane_rejects_malformed_target_group_snapshot(
    snapshot: object,
) -> None:
    with pytest.raises(
        RuntimeError,
        match="credentialed target group inventory is (malformed|incomplete|ambiguous)",
    ):
        _audit(
            _workspace(),
            target_groups_probe=lambda *_args, **_kwargs: snapshot,
        )


def test_foreign_uc_control_plane_passes_only_with_complete_zero_access() -> None:
    workspace = _workspace()

    proof = _audit(workspace)

    assert proof.audited_catalogs == frozenset({"other"})
    assert proof.application_id == APPLICATION_ID
    assert proof.workspace_id == WORKSPACE_ID


def test_foreign_uc_control_plane_accepts_managed_online_information_schema_contract() -> None:
    workspace = _workspace()
    _add_managed_online_catalog(workspace)

    proof = _audit(workspace)

    assert proof.audited_catalogs == frozenset({"online_state", "other"})
    assert ("schema", "online_state.information_schema", None) in workspace.grants.calls
    assert ("table", "online_state.information_schema.tables", None) in workspace.grants.calls


@pytest.mark.parametrize(
    ("information_schema_owner", "information_table_owner"),
    [
        ("different-owner@example.com", None),
        (None, "different-owner@example.com"),
    ],
)
def test_foreign_uc_control_plane_binds_managed_online_information_schema_owner(
    information_schema_owner: str | None,
    information_table_owner: str | None,
) -> None:
    workspace = _workspace()
    _add_managed_online_catalog(
        workspace,
        information_schema_owner=information_schema_owner,
        information_table_owner=information_table_owner,
    )

    with pytest.raises(RuntimeError, match="managed-online catalog owner"):
        _audit(workspace)


def test_foreign_uc_control_plane_binds_unknown_managed_online_metadata_owner() -> None:
    workspace = _workspace()
    _add_managed_online_catalog(
        workspace,
        unknown_information_table_owner="different-owner@example.com",
    )

    with pytest.raises(RuntimeError, match="managed-online catalog owner"):
        _audit(workspace)


@pytest.mark.parametrize(
    ("information_schema_full_name", "information_table_full_name"),
    [
        ("other.information_schema", None),
        (None, "other.information_schema.tables"),
    ],
)
def test_foreign_uc_control_plane_binds_managed_online_metadata_to_parent(
    information_schema_full_name: str | None,
    information_table_full_name: str | None,
) -> None:
    workspace = _workspace()
    _add_managed_online_catalog(
        workspace,
        information_schema_full_name=information_schema_full_name,
        information_table_full_name=information_table_full_name,
    )

    with pytest.raises(RuntimeError, match="invalid parent identity"):
        _audit(workspace)


@pytest.mark.parametrize(
    ("securable_type", "full_name", "privilege"),
    [
        ("schema", "online_state.information_schema", "USE_SCHEMA"),
        ("table", "online_state.information_schema.tables", "SELECT"),
    ],
)
def test_foreign_uc_control_plane_requires_zero_managed_online_metadata_access(
    securable_type: str,
    full_name: str,
    privilege: str,
) -> None:
    workspace = _workspace(
        {
            (securable_type, full_name): [
                _assignment(privilege, principal="account users")
            ]
        }
    )
    _add_managed_online_catalog(workspace)

    with pytest.raises(RuntimeError, match="effective UC boundary failed"):
        _audit(workspace)


def test_foreign_uc_control_plane_audits_all_ordinary_catalogs_before_mip_exists() -> None:
    workspace = _workspace()
    catalogs = [
        item
        for item in workspace.catalogs.list(include_browse=True, include_unbound=True)
        if item.name != CATALOG
    ]
    workspace.catalogs.list = lambda **_kwargs: iter(catalogs)
    models = [
        item
        for item in workspace.registered_models.list(include_browse=True)
        if item.catalog_name != CATALOG
    ]
    workspace.registered_models.list = lambda **_kwargs: iter(models)

    proof = _audit(workspace, allow_missing_mip_catalog=True)

    assert proof.audited_catalogs == frozenset({"other"})
    assert ("catalog", "other", None) in workspace.grants.calls


def test_foreign_uc_control_plane_rejects_missing_mip_after_bootstrap() -> None:
    workspace = _workspace()
    catalogs = [
        item
        for item in workspace.catalogs.list(include_browse=True, include_unbound=True)
        if item.name != CATALOG
    ]
    workspace.catalogs.list = lambda **_kwargs: iter(catalogs)

    with pytest.raises(RuntimeError, match="configured MIP catalog is missing"):
        _audit(workspace)


@pytest.mark.parametrize("privileges", [("BROWSE",), ("MANAGE", "USE_CATALOG")])
def test_foreign_uc_control_plane_rejects_inherited_catalog_access(
    privileges: tuple[str, ...],
) -> None:
    workspace = _workspace(
        {("catalog", "other"): [_assignment(*privileges, principal="account users")]}
    )

    with pytest.raises(RuntimeError, match="forbidden access.*other"):
        _audit(workspace)


@pytest.mark.parametrize(
    ("securable_type", "full_name", "privilege"),
    [
        ("schema", "other.sandbox", "USE_SCHEMA"),
        ("table", "other.sandbox.secret", "SELECT"),
        ("function", "other.sandbox.secret_fn", "EXECUTE"),
        ("volume", "other.sandbox.private", "READ_VOLUME"),
        ("function", "other.sandbox.secret_model", "EXECUTE"),
    ],
)
def test_foreign_uc_control_plane_rejects_hidden_child_access(
    securable_type: str,
    full_name: str,
    privilege: str,
) -> None:
    workspace = _workspace(
        {
            (securable_type, full_name): [
                _assignment(
                    privilege,
                    principal="account users",
                    inherited_type="CATALOG",
                    inherited_name="other",
                )
            ]
        }
    )

    with pytest.raises(RuntimeError, match="effective UC boundary"):
        _audit(workspace)


def test_foreign_uc_control_plane_propagates_authorization_denial() -> None:
    workspace = _workspace()
    workspace.grants.denied = ("catalog", "other")

    with pytest.raises(PermissionDenied, match="metastore authority required"):
        _audit(workspace)


def test_foreign_uc_control_plane_reads_every_effective_grant_page() -> None:
    workspace = _workspace(
        {("catalog", "other"): [_assignment("BROWSE", principal="account users")]}
    )
    workspace.grants.paginate = ("catalog", "other")

    with pytest.raises(RuntimeError, match="forbidden access.*other"):
        _audit(workspace)

    assert ("catalog", "other", None) in workspace.grants.calls
    assert ("catalog", "other", "page-2") in workspace.grants.calls


def test_foreign_uc_control_plane_requires_exact_inventory_identity() -> None:
    workspace = _workspace()
    workspace.current_user.me = lambda: SimpleNamespace(
        user_name="other-admin@example.com",
        groups=[SimpleNamespace(display="admins")],
    )

    with pytest.raises(RuntimeError, match="unexpected principal"):
        _audit(workspace)


def test_foreign_uc_control_plane_requires_current_metastore_ownership() -> None:
    workspace = _workspace(owner="unrelated-owner@example.com")

    with pytest.raises(RuntimeError, match="own the current metastore directly"):
        _audit(workspace)


def test_foreign_uc_control_plane_rejects_display_name_owner_group() -> None:
    workspace = _workspace(owner="metastore-owners")

    with pytest.raises(RuntimeError, match="own the current metastore directly"):
        _audit(workspace)


def test_foreign_uc_control_plane_accepts_binding_excluded_catalog_grants() -> None:
    workspace = _workspace(
        {("catalog", "hidden"): [_assignment("BROWSE", principal="account users")]},
        extra_catalogs=[
            SimpleNamespace(
                name="hidden",
                isolation_mode="ISOLATED",
                owner=FOREIGN_OWNER,
            )
        ],
    )
    workspace.workspace_bindings.get_bindings = lambda _type, name: iter(
        [
            SimpleNamespace(
                workspace_id=OTHER_WORKSPACE_ID,
                binding_type="BINDING_TYPE_READ_WRITE",
            )
        ]
        if name == "hidden"
        else [
            SimpleNamespace(
                workspace_id=WORKSPACE_ID,
                binding_type="BINDING_TYPE_READ_WRITE",
            )
        ]
    )

    proof = _audit(
        workspace,
        foreign_catalog_binding_policy=_binding_policy(),
    )

    assert proof.audited_catalogs == frozenset({"hidden", "other"})
    assert proof.grant_audited_catalogs == frozenset({"other"})
    assert tuple(item.catalog for item in proof.binding_denied_catalogs) == ("hidden",)
    assert ("catalog", "hidden", None) not in workspace.grants.calls


def test_foreign_uc_control_plane_accepts_unassigned_binding_excluded_owner() -> None:
    owner = "unassigned-owner@example.com"
    workspace = _workspace(
        extra_catalogs=[
            SimpleNamespace(
                name="hidden",
                isolation_mode="ISOLATED",
                owner=owner,
            )
        ],
    )
    workspace.workspace_bindings.get_bindings = lambda _type, name: iter(
        [
            SimpleNamespace(
                workspace_id=OTHER_WORKSPACE_ID,
                binding_type="BINDING_TYPE_READ_WRITE",
            )
        ]
        if name == "hidden"
        else [
            SimpleNamespace(
                workspace_id=WORKSPACE_ID,
                binding_type="BINDING_TYPE_READ_WRITE",
            )
        ]
    )

    proof = _audit(
        workspace,
        foreign_catalog_binding_policy=_binding_policy(owner=owner),
    )

    assert tuple(item.catalog for item in proof.binding_denied_catalogs) == ("hidden",)


def test_foreign_uc_control_plane_still_rejects_unresolved_accessible_owner() -> None:
    owner = "unassigned-owner@example.com"

    with pytest.raises(RuntimeError, match="did not resolve to exactly one principal"):
        _audit(_workspace(owner_overrides={"other": owner}))


@pytest.mark.parametrize("full_name", ["other", "other.sandbox.secret_model"])
def test_foreign_uc_control_plane_rejects_implicit_account_users_owner(
    full_name: str,
) -> None:
    workspace = _workspace(owner_overrides={full_name: "account users"})
    workspace.groups.list = lambda **_kwargs: iter(
        [SimpleNamespace(display_name="account users", id="account-users-id")]
    )
    account = _account()
    account_users = next(account.groups.list())
    account_users.members = []

    with pytest.raises(RuntimeError, match="member of approved owner group"):
        _audit(
            workspace,
            account_factory=lambda: account,
            target_groups_probe=lambda *_args, **_kwargs: {},
        )


@pytest.mark.parametrize(
    "owner",
    [
        APPLICATION_ID,
        APPLICATION_ID.upper(),
        ACCOUNT_SCIM_ID,
        "runtime-scim-id",
        "runtime",
        "runtime-workspace",
        "account users",
        "account-users-id",
    ],
)
def test_foreign_uc_control_plane_rejects_binding_excluded_account_alias_owner(
    owner: str,
) -> None:
    workspace = _workspace(
        extra_catalogs=[
            SimpleNamespace(
                name="hidden",
                isolation_mode="ISOLATED",
                owner=owner,
            )
        ],
    )
    workspace.workspace_bindings.get_bindings = lambda _type, name: iter(
        [
            SimpleNamespace(
                workspace_id=OTHER_WORKSPACE_ID,
                binding_type="BINDING_TYPE_READ_WRITE",
            )
        ]
        if name == "hidden"
        else [
            SimpleNamespace(
                workspace_id=WORKSPACE_ID,
                binding_type="BINDING_TYPE_READ_WRITE",
            )
        ]
    )

    with pytest.raises(RuntimeError, match="cannot own governed UC objects"):
        _audit(
            workspace,
            foreign_catalog_binding_policy=_binding_policy(owner=owner),
        )


def test_foreign_uc_control_plane_rejects_binding_excluded_model_ownership() -> None:
    owner = "unassigned-owner@example.com"
    workspace = _workspace(
        extra_catalogs=[
            SimpleNamespace(
                name="hidden",
                isolation_mode="ISOLATED",
                owner=owner,
            )
        ],
    )
    workspace.workspace_bindings.get_bindings = lambda _type, name: iter(
        [
            SimpleNamespace(
                workspace_id=OTHER_WORKSPACE_ID,
                binding_type="BINDING_TYPE_READ_WRITE",
            )
        ]
        if name == "hidden"
        else [
            SimpleNamespace(
                workspace_id=WORKSPACE_ID,
                binding_type="BINDING_TYPE_READ_WRITE",
            )
        ]
    )
    models = list(workspace.registered_models.list(include_browse=True))
    models.append(
        SimpleNamespace(
            full_name="hidden.private.runtime_owned",
            catalog_name="hidden",
            owner=APPLICATION_ID,
        )
    )
    workspace.registered_models.list = lambda **_kwargs: iter(models)

    with pytest.raises(RuntimeError, match="cannot own governed UC objects"):
        _audit(
            workspace,
            foreign_catalog_binding_policy=_binding_policy(owner=owner),
        )


def test_foreign_uc_control_plane_accepts_unassigned_binding_excluded_model_owner() -> None:
    owner = "unassigned-owner@example.com"
    workspace = _workspace(
        extra_catalogs=[
            SimpleNamespace(
                name="hidden",
                isolation_mode="ISOLATED",
                owner=owner,
            )
        ],
    )
    workspace.workspace_bindings.get_bindings = lambda _type, name: iter(
        [
            SimpleNamespace(
                workspace_id=OTHER_WORKSPACE_ID,
                binding_type="BINDING_TYPE_READ_WRITE",
            )
        ]
        if name == "hidden"
        else [
            SimpleNamespace(
                workspace_id=WORKSPACE_ID,
                binding_type="BINDING_TYPE_READ_WRITE",
            )
        ]
    )
    models = list(workspace.registered_models.list(include_browse=True))
    models.append(
        SimpleNamespace(
            full_name="hidden.private.human_owned",
            catalog_name="hidden",
            owner=owner,
        )
    )
    workspace.registered_models.list = lambda **_kwargs: iter(models)

    proof = _audit(
        workspace,
        foreign_catalog_binding_policy=_binding_policy(owner=owner),
    )

    assert tuple(item.catalog for item in proof.binding_denied_catalogs) == ("hidden",)


def test_binding_excluded_account_users_owner_rejects_without_group_inventory() -> None:
    account = _account()
    account.groups.list = lambda **_kwargs: iter([])
    account.groups.get = lambda _group_id: None
    workspace = _workspace(
        extra_catalogs=[
            SimpleNamespace(
                name="hidden",
                isolation_mode="ISOLATED",
                owner="account users",
            )
        ],
    )
    workspace.workspace_bindings.get_bindings = lambda _type, name: iter(
        [
            SimpleNamespace(
                workspace_id=OTHER_WORKSPACE_ID,
                binding_type="BINDING_TYPE_READ_WRITE",
            )
        ]
        if name == "hidden"
        else [
            SimpleNamespace(
                workspace_id=WORKSPACE_ID,
                binding_type="BINDING_TYPE_READ_WRITE",
            )
        ]
    )

    with pytest.raises(RuntimeError, match="managed system group identity could not be proven"):
        _audit(
            workspace,
            account_factory=lambda: account,
            target_groups_probe=lambda *_args, **_kwargs: {},
            foreign_catalog_binding_policy=_binding_policy(owner="account users"),
        )


def test_binding_excluded_account_users_id_rejects_when_identity_is_omitted() -> None:
    account = _account()
    account.groups.list = lambda **_kwargs: iter([])
    account.groups.get = lambda _group_id: None
    workspace = _workspace(
        extra_catalogs=[
            SimpleNamespace(
                name="hidden",
                isolation_mode="ISOLATED",
                owner="account-users-id",
            )
        ],
    )
    workspace.workspace_bindings.get_bindings = lambda _type, name: iter(
        [
            SimpleNamespace(
                workspace_id=OTHER_WORKSPACE_ID,
                binding_type="BINDING_TYPE_READ_WRITE",
            )
        ]
        if name == "hidden"
        else [
            SimpleNamespace(
                workspace_id=WORKSPACE_ID,
                binding_type="BINDING_TYPE_READ_WRITE",
            )
        ]
    )

    with pytest.raises(RuntimeError, match="managed system group identity could not be proven"):
        _audit(
            workspace,
            account_factory=lambda: account,
            target_groups_probe=lambda *_args, **_kwargs: {},
            foreign_catalog_binding_policy=_binding_policy(owner="account-users-id"),
        )


def test_binding_excluded_account_users_id_rejects_when_membership_is_implicit() -> None:
    account = _account()
    account_users = next(account.groups.list())
    account_users.members = []
    workspace = _workspace(
        extra_catalogs=[
            SimpleNamespace(
                name="hidden",
                isolation_mode="ISOLATED",
                owner="account-users-id",
            )
        ],
    )
    workspace.workspace_bindings.get_bindings = lambda _type, name: iter(
        [
            SimpleNamespace(
                workspace_id=OTHER_WORKSPACE_ID,
                binding_type="BINDING_TYPE_READ_WRITE",
            )
        ]
        if name == "hidden"
        else [
            SimpleNamespace(
                workspace_id=WORKSPACE_ID,
                binding_type="BINDING_TYPE_READ_WRITE",
            )
        ]
    )

    with pytest.raises(RuntimeError, match="cannot own governed UC objects"):
        _audit(
            workspace,
            account_factory=lambda: account,
            target_groups_probe=lambda *_args, **_kwargs: {},
            foreign_catalog_binding_policy=_binding_policy(owner="account-users-id"),
        )


def test_foreign_uc_control_plane_accepts_binding_excluded_hidden_children() -> None:
    workspace = _workspace(
        {
            ("schema", "hidden.private"): [
                _assignment("USE_SCHEMA", principal="account users")
            ]
        },
        extra_catalogs=[
            SimpleNamespace(
                name="hidden",
                isolation_mode="ISOLATED",
                owner=FOREIGN_OWNER,
            )
        ]
    )
    workspace.workspace_bindings.get_bindings = lambda _type, name: iter(
        [
            SimpleNamespace(
                workspace_id=OTHER_WORKSPACE_ID,
                binding_type="BINDING_TYPE_READ_WRITE",
            )
        ]
        if name == "hidden"
        else [
            SimpleNamespace(
                workspace_id=WORKSPACE_ID,
                binding_type="BINDING_TYPE_READ_WRITE",
            )
        ]
    )

    proof = _audit(
        workspace,
        foreign_catalog_binding_policy=_binding_policy(),
    )

    assert proof.audited_catalogs == frozenset({"hidden", "other"})
    assert ("schema", "hidden.private", None) not in workspace.grants.calls


@pytest.mark.parametrize(
    "binding_type",
    [
        "BINDING_TYPE_READ_ONLY",
        "BINDING_TYPE_READ_WRITE",
    ],
)
def test_foreign_uc_control_plane_rejects_grants_when_target_workspace_is_bound(
    binding_type: str,
) -> None:
    workspace = _workspace(
        {("catalog", "hidden"): [_assignment("BROWSE", principal="account users")]},
        extra_catalogs=[
            SimpleNamespace(
                name="hidden",
                isolation_mode="ISOLATED",
                owner=FOREIGN_OWNER,
            )
        ],
    )
    workspace.workspace_bindings.get_bindings = lambda _type, name: iter(
        [
            SimpleNamespace(
                workspace_id=WORKSPACE_ID,
                binding_type=binding_type,
            )
        ]
        if name == "hidden"
        else [
            SimpleNamespace(
                workspace_id=WORKSPACE_ID,
                binding_type="BINDING_TYPE_READ_WRITE",
            )
        ]
    )

    with pytest.raises(RuntimeError, match="not binding-denied as reviewed"):
        _audit(
            workspace,
            foreign_catalog_binding_policy=_binding_policy(),
        )


def test_foreign_uc_control_plane_propagates_binding_authorization_denial() -> None:
    workspace = _workspace(
        extra_catalogs=[
            SimpleNamespace(
                name="hidden",
                isolation_mode="ISOLATED",
                owner=FOREIGN_OWNER,
            )
        ]
    )

    def deny_hidden(_type: str, name: str) -> object:
        if name == "hidden":
            raise PermissionDenied("binding inventory requires metastore authority")
        return iter(
            [
                SimpleNamespace(
                    workspace_id=WORKSPACE_ID,
                    binding_type="BINDING_TYPE_READ_WRITE",
                )
            ]
        )

    workspace.workspace_bindings.get_bindings = deny_hidden

    with pytest.raises(PermissionDenied, match="binding inventory requires"):
        _audit(
            workspace,
            foreign_catalog_binding_policy=_binding_policy(),
        )


def test_foreign_uc_control_plane_rejects_binding_excluded_runtime_ownership() -> None:
    workspace = _workspace(
        extra_catalogs=[
            SimpleNamespace(
                name="hidden",
                isolation_mode="ISOLATED",
                owner=APPLICATION_ID,
            )
        ]
    )
    workspace.workspace_bindings.get_bindings = lambda _type, name: iter(
        [
            SimpleNamespace(
                workspace_id=OTHER_WORKSPACE_ID,
                binding_type="BINDING_TYPE_READ_WRITE",
            )
        ]
        if name == "hidden"
        else [
            SimpleNamespace(
                workspace_id=WORKSPACE_ID,
                binding_type="BINDING_TYPE_READ_WRITE",
            )
        ]
    )

    with pytest.raises(RuntimeError, match="cannot own governed UC objects"):
        _audit(
            workspace,
            foreign_catalog_binding_policy=_binding_policy(
                owner=APPLICATION_ID,
            ),
        )


def test_foreign_uc_control_plane_skips_binding_excluded_registered_model_grants() -> None:
    workspace = _workspace(
        {
            ("function", "hidden.private.model"): [
                _assignment("EXECUTE", principal="account users")
            ]
        },
        extra_catalogs=[
            SimpleNamespace(
                name="hidden",
                isolation_mode="ISOLATED",
                owner=FOREIGN_OWNER,
            )
        ],
    )
    models = list(workspace.registered_models.list(include_browse=True))
    models.append(
        SimpleNamespace(
            full_name="hidden.private.model",
            catalog_name="hidden",
            owner=FOREIGN_OWNER,
        )
    )
    workspace.registered_models.list = lambda **_kwargs: iter(models)
    workspace.workspace_bindings.get_bindings = lambda _type, name: iter(
        [
            SimpleNamespace(
                workspace_id=OTHER_WORKSPACE_ID,
                binding_type="BINDING_TYPE_READ_WRITE",
            )
        ]
        if name == "hidden"
        else [
            SimpleNamespace(
                workspace_id=WORKSPACE_ID,
                binding_type="BINDING_TYPE_READ_WRITE",
            )
        ]
    )

    proof = _audit(
        workspace,
        foreign_catalog_binding_policy=_binding_policy(),
    )

    assert proof.audited_catalogs == frozenset({"hidden", "other"})
    assert ("function", "hidden.private.model", None) not in workspace.grants.calls


@pytest.mark.parametrize(
    "binding",
    [
        SimpleNamespace(
            workspace_id="",
            binding_type="BINDING_TYPE_READ_WRITE",
        ),
        SimpleNamespace(
            workspace_id=OTHER_WORKSPACE_ID,
            binding_type="",
        ),
        SimpleNamespace(
            workspace_id=OTHER_WORKSPACE_ID,
            binding_type="BINDING_TYPE_UNKNOWN",
        ),
    ],
)
def test_foreign_uc_control_plane_rejects_incomplete_workspace_binding(
    binding: object,
) -> None:
    workspace = _workspace(
        extra_catalogs=[
            SimpleNamespace(
                name="hidden",
                isolation_mode="ISOLATED",
                owner=FOREIGN_OWNER,
            )
        ]
    )
    workspace.workspace_bindings.get_bindings = lambda _type, name: iter(
        [binding]
        if name == "hidden"
        else [
            SimpleNamespace(
                workspace_id=WORKSPACE_ID,
                binding_type="BINDING_TYPE_READ_WRITE",
            )
        ]
    )

    with pytest.raises(RuntimeError, match="incomplete workspace binding"):
        _audit(workspace)


def test_foreign_uc_control_plane_rejects_duplicate_workspace_binding() -> None:
    workspace = _workspace(
        extra_catalogs=[
            SimpleNamespace(
                name="hidden",
                isolation_mode="ISOLATED",
                owner=FOREIGN_OWNER,
            )
        ]
    )
    workspace.workspace_bindings.get_bindings = lambda _type, name: iter(
        [
            SimpleNamespace(
                workspace_id=OTHER_WORKSPACE_ID,
                binding_type="BINDING_TYPE_READ_WRITE",
            ),
            SimpleNamespace(
                workspace_id=OTHER_WORKSPACE_ID,
                binding_type="BINDING_TYPE_READ_ONLY",
            ),
        ]
        if name == "hidden"
        else [
            SimpleNamespace(
                workspace_id=WORKSPACE_ID,
                binding_type="BINDING_TYPE_READ_WRITE",
            )
        ]
    )

    with pytest.raises(RuntimeError, match="duplicate workspace bindings"):
        _audit(workspace)


def test_foreign_uc_control_plane_rejects_configured_workspace_id_drift() -> None:
    workspace = _workspace()
    workspace.config.workspace_id = "other-bound-workspace"

    with pytest.raises(RuntimeError, match="does not match.*workspace host"):
        _audit(workspace)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("owner", "lookalike-owner"),
        ("catalog_type", "MANAGED_CATALOG"),
        ("isolation_mode", "ISOLATED"),
    ],
)
def test_foreign_uc_control_plane_source_binds_internal_catalog(
    field: str,
    value: str,
) -> None:
    workspace = _workspace()
    catalogs = list(workspace.catalogs.list(include_browse=True, include_unbound=True))
    internal = next(item for item in catalogs if item.name == "__databricks_internal")
    setattr(internal, field, value)
    workspace.catalogs.list = lambda **_kwargs: iter(catalogs)

    with pytest.raises(RuntimeError, match="internal catalog.*fixed platform identity"):
        _audit(workspace)


def test_foreign_uc_control_plane_requires_zero_internal_catalog_privileges() -> None:
    workspace = _workspace(
        {("catalog", "__databricks_internal"): [_assignment("BROWSE", principal="account users")]}
    )

    with pytest.raises(RuntimeError, match="forbidden access.*internal catalog"):
        _audit(workspace)


def test_foreign_uc_control_plane_rejects_model_from_absent_catalog() -> None:
    workspace = _workspace()
    models = list(workspace.registered_models.list(include_browse=True))
    models.append(
        SimpleNamespace(
            full_name="unlisted.sandbox.hidden_model",
            catalog_name="unlisted",
            owner=FOREIGN_OWNER,
        )
    )
    workspace.registered_models.list = lambda **_kwargs: iter(models)

    with pytest.raises(RuntimeError, match="registered-model catalog is absent"):
        _audit(workspace)


@pytest.mark.parametrize(
    "full_name",
    ["other.information_schema", "other.information_schema.tables"],
)
def test_foreign_uc_control_plane_source_binds_information_schema_owner(
    full_name: str,
) -> None:
    workspace = _workspace(owner_overrides={full_name: FOREIGN_OWNER})

    with pytest.raises(RuntimeError, match="information-schema.*System user"):
        _audit(workspace)


@pytest.mark.parametrize(
    "full_name",
    [
        "other",
        "other.sandbox",
        "other.sandbox.secret",
        "other.sandbox.secret_fn",
        "other.sandbox.private",
        "other.sandbox.secret_model",
    ],
)
def test_foreign_uc_control_plane_rejects_direct_ownership(full_name: str) -> None:
    workspace = _workspace(owner_overrides={full_name: APPLICATION_ID})

    with pytest.raises(RuntimeError, match="cannot own governed UC objects"):
        _audit(workspace)


@pytest.mark.parametrize(
    "owner",
    [ACCOUNT_SCIM_ID, "runtime-scim-id", "runtime", "runtime-workspace"],
)
def test_foreign_uc_control_plane_rejects_accessible_runtime_alias_owner(
    owner: str,
) -> None:
    with pytest.raises(RuntimeError, match="cannot own governed UC objects"):
        _audit(_workspace(owner_overrides={"other": owner}))


def test_foreign_uc_control_plane_uses_target_credential_for_group_ownership() -> None:
    workspace = _workspace(owner_overrides={"other": "runtime-owners"})
    workspace.groups.list = lambda **_kwargs: iter(
        [SimpleNamespace(display_name="runtime-owners", id="owner-group-id")]
    )

    account = _account()
    account_users = next(account.groups.list())
    account.groups.get = lambda group_id: (
        SimpleNamespace(
            id="owner-group-id",
            display_name="runtime-owners",
            members=[],
        )
        if group_id == "owner-group-id"
        else account_users
    )
    calls: list[tuple[str, str, str, str]] = []

    def probe(
        _account: object,
        account_sp_id: str,
        application_id: str,
        group_id: str,
        group_name: str,
    ) -> bool:
        calls.append((account_sp_id, application_id, group_id, group_name))
        return True

    with pytest.raises(RuntimeError, match="member of approved owner group"):
        _audit(
            workspace,
            account_factory=lambda: account,
            group_membership_probe=probe,
        )

    assert calls == [(ACCOUNT_SCIM_ID, APPLICATION_ID, "owner-group-id", "runtime-owners")]
