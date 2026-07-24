from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.agents.reviewed_uc_function_contract import REVIEWED_FUNCTIONS
from tools.databricks.verify_agent_proxy_uc_grants import (
    _audit_mip_catalog,
    verify_effective_agent_proxy_uc_boundary,
)

_PRINCIPAL = "proxy-client"
_DIRECT = (_PRINCIPAL, "", "")


def _item(*, catalog: str, schema: str, name: str, owner: str = "governance"):
    full_name = f"{catalog}.{schema}" + (f".{name}" if name else "")
    return SimpleNamespace(
        catalog_name=catalog,
        schema_name=schema if name else None,
        name=name or schema,
        full_name=full_name,
        owner=owner,
    )


def _assignment(action: str, source: tuple[str, str, str]) -> object:
    principal, inherited_type, inherited_name = source
    return SimpleNamespace(
        principal=principal,
        privileges=[
            SimpleNamespace(
                privilege=action,
                inherited_from_type=inherited_type or None,
                inherited_from_name=inherited_name or None,
            )
        ],
    )


class _Grants:
    def __init__(self, values: dict[tuple[str, str], dict[str, set[tuple[str, str, str]]]]):
        self.values = values

    def get_effective(
        self,
        securable_type: str,
        full_name: str,
        *,
        principal: str,
        max_results: int,
        page_token: str | None,
    ) -> object:
        assert principal == _PRINCIPAL
        assert max_results == 1000
        assert page_token is None
        assignments = [
            _assignment(action, source)
            for action, sources in self.values.get((securable_type, full_name), {}).items()
            for source in sorted(sources)
        ]
        return SimpleNamespace(
            privilege_assignments=assignments,
            next_page_token=None,
        )


def _mip_workspace(
    *,
    extra_function: bool = False,
    extra_table: bool = False,
    grants: dict[tuple[str, str], dict[str, set[tuple[str, str, str]]]] | None = None,
) -> object:
    gold = _item(catalog="mip", schema="gold", name="")
    functions = [
        _item(catalog="mip", schema="gold", name=spec.leaf_name)
        for spec in REVIEWED_FUNCTIONS
    ]
    if extra_function:
        functions.append(_item(catalog="mip", schema="gold", name="fn_unreviewed"))
    tables = (
        [_item(catalog="mip", schema="gold", name="borrower_private")]
        if extra_table
        else []
    )
    exact = {
        ("schema", "mip.gold"): {"USE_SCHEMA": {_DIRECT}},
        **{
            ("function", f"mip.gold.{spec.leaf_name}"): {"EXECUTE": {_DIRECT}}
            for spec in REVIEWED_FUNCTIONS
        },
    }
    if grants:
        exact.update(grants)
    return SimpleNamespace(
        schemas=SimpleNamespace(list=lambda *_args, **_kwargs: iter([gold])),
        functions=SimpleNamespace(list=lambda *_args, **_kwargs: iter(functions)),
        tables=SimpleNamespace(list=lambda *_args, **_kwargs: iter(tables)),
        volumes=SimpleNamespace(list=lambda *_args, **_kwargs: iter([])),
        grants=_Grants(exact),
    )


def test_mip_boundary_accepts_only_exact_direct_reviewed_functions() -> None:
    _audit_mip_catalog(
        _mip_workspace(),
        catalog="mip",
        principal=_PRINCIPAL,
        owner_aliases={_PRINCIPAL},
    )


@pytest.mark.parametrize(
    ("workspace", "resource"),
    [
        (
            _mip_workspace(
                extra_function=True,
                grants={
                    ("function", "mip.gold.fn_unreviewed"): {
                        "EXECUTE": {_DIRECT}
                    }
                },
            ),
            "fn_unreviewed",
        ),
        (
            _mip_workspace(
                extra_table=True,
                grants={
                    ("table", "mip.gold.borrower_private"): {
                        "SELECT": {_DIRECT}
                    }
                },
            ),
            "borrower_private",
        ),
    ],
    ids=["fourth-function", "table"],
)
def test_mip_boundary_rejects_extra_function_or_table_privilege(
    workspace: object,
    resource: str,
) -> None:
    with pytest.raises(RuntimeError, match=resource):
        _audit_mip_catalog(
            workspace,
            catalog="mip",
            principal=_PRINCIPAL,
            owner_aliases={_PRINCIPAL},
        )


def test_global_boundary_rejects_inherited_target_catalog_privilege(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _mip_workspace(
        grants={
            ("metastore", "metastore-1"): {
                "USE_MARKETPLACE_ASSETS": {("account users", "", "")}
            },
            ("catalog", "mip"): {
                "USE_CATALOG": {("proxy-group", "METASTORE", "metastore-1")}
            },
        }
    )
    workspace.current_user = SimpleNamespace(
        me=lambda: SimpleNamespace(
            id="proxy-scim",
            user_name=_PRINCIPAL,
            application_id=_PRINCIPAL,
            groups=[],
        )
    )
    workspace.metastores = SimpleNamespace(
        current=lambda: SimpleNamespace(metastore_id="metastore-1")
    )
    workspace.catalogs = SimpleNamespace(
        list=lambda **_kwargs: iter(
            [
                SimpleNamespace(
                    name="mip",
                    owner="governance",
                    catalog_type="MANAGED_CATALOG",
                    isolation_mode="OPEN",
                )
            ]
        )
    )
    workspace.registered_models = SimpleNamespace(
        list=lambda **_kwargs: iter([])
    )

    with pytest.raises(RuntimeError, match="catalog mip"):
        verify_effective_agent_proxy_uc_boundary(
            workspace,
            application_id=_PRINCIPAL,
            catalog="mip",
        )
