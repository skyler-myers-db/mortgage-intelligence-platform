from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.errors import PermissionDenied

from tools.databricks import audit_agent_runtime_foreign_uc_access as auditor

APPLICATION_ID = "runtime-client"
INVENTORY_PRINCIPAL = "deployer@example.com"
CATALOG = "mip"
WORKSPACE_ID = "7474645995341779"
FOREIGN_OWNER = "foreign-owner@example.com"


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
        SimpleNamespace(name=CATALOG, isolation_mode="OPEN", owner="mip-owner"),
        SimpleNamespace(
            name="other",
            isolation_mode="OPEN",
            owner=object_owner("other"),
        ),
        SimpleNamespace(name="system", isolation_mode="OPEN", owner="System user"),
        SimpleNamespace(name="samples", isolation_mode="OPEN", owner="System user"),
        SimpleNamespace(
            name="__databricks_internal",
            isolation_mode="OPEN",
            owner="System user",
            catalog_type="INTERNAL_CATALOG",
        ),
        *(extra_catalogs or []),
    ]

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
            get_bindings=lambda _type, _name: iter([SimpleNamespace(workspace_id=WORKSPACE_ID)])
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
                [SimpleNamespace(application_id=APPLICATION_ID, id="runtime-scim-id")]
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


def _audit(workspace: Any, **kwargs: Any) -> object:
    return auditor.audit_foreign_uc_access(
        workspace,
        application_id=APPLICATION_ID,
        catalog=CATALOG,
        expected_inventory_principal=INVENTORY_PRINCIPAL,
        **kwargs,
    )


def test_foreign_uc_control_plane_passes_only_with_complete_zero_access() -> None:
    workspace = _workspace()

    proof = _audit(workspace)

    assert proof.audited_catalogs == frozenset({"other"})
    assert proof.application_id == APPLICATION_ID
    assert proof.workspace_id == WORKSPACE_ID


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


def test_foreign_uc_control_plane_rejects_hidden_unbound_catalog_access() -> None:
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

    with pytest.raises(RuntimeError, match="forbidden access.*hidden"):
        _audit(workspace)


def test_foreign_uc_control_plane_fails_closed_on_unbound_catalog_children() -> None:
    workspace = _workspace(
        extra_catalogs=[
            SimpleNamespace(
                name="hidden",
                isolation_mode="ISOLATED",
                owner=FOREIGN_OWNER,
            )
        ]
    )
    workspace.workspace_bindings.get_bindings = lambda _type, _name: iter([])

    with pytest.raises(RuntimeError, match="unbound.*cannot be completely inventoried"):
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


def test_foreign_uc_control_plane_uses_target_credential_for_group_ownership() -> None:
    workspace = _workspace(owner_overrides={"other": "runtime-owners"})
    workspace.groups.list = lambda **_kwargs: iter(
        [SimpleNamespace(display_name="runtime-owners", id="owner-group-id")]
    )

    account = SimpleNamespace(
        config=SimpleNamespace(client_id="account-auditor"),
        service_principals=SimpleNamespace(
            list=lambda **_kwargs: iter(
                [SimpleNamespace(application_id=APPLICATION_ID, id="account-runtime-id")]
            )
        ),
        groups=SimpleNamespace(
            get=lambda _group_id: SimpleNamespace(
                id="owner-group-id",
                display_name="runtime-owners",
            )
        ),
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

    assert calls == [("account-runtime-id", APPLICATION_ID, "owner-group-id", "runtime-owners")]
