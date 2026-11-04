"""The audited agent identity's own view of Unity Catalog, judged against the inventory (M4-PR1).

The foreign UC audit reads the runtime's (or the proxy's) effective
privileges as that identity, with its own OAuth credential, instead of as a
metastore owner: every catalog it lists with ``include_browse``, everything
below each foreign one, and every registered model it lists. A privilege its
accepted-exposure matcher does not accept refuses, as before. Whether an
identity's self ``get_effective`` is answered at all is spike S0(a), owed; a
refused read propagates and refuses, never read as absent.

The self-view alone cannot see what the identity owns where it cannot look
(spike S0(c), owed: the worst case is assumed), which is why the owner's
estate inventory exists. So the view is judged against it:

* a catalog it can see that the inventory lacks, or lists with another owner,
  type or isolation mode, means the inventory is stale and refuses;
* a binding-denied catalog it can see refuses;
* every owner of what it can see must be in that catalog's census, and never
  one of the identity's own aliases or groups.

Lines name foreign catalogs through ``redact.foreign`` (``foreign#n`` handles
when ``MIP_REDACT_FOREIGN_NAMES=1``).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from tools.clients.redact import foreign
from tools.databricks.agent_runtime_uc_baseline import _MAX_INVENTORY_WORKERS
from tools.databricks.agent_runtime_uc_inventory import (
    _assert_authenticated_runtime,
    _assert_no_catalog_child_privileges,
    _assert_privileges,
    _catalog_name,
    _effective_privilege_sources,
    _exact_owner,
    _full_name,
    _text,
)
from tools.databricks.estate.accepted_exposure import ExposureMatcher
from tools.databricks.estate.inventory import EstateInventory
from tools.databricks.estate.inventory_audit import census_owners, platform_catalog_names
from tools.databricks.estate.uc_exposure import UcExposureScope

_INTERNAL = "__databricks_internal"
# The OAuth credential each audited identity's self-view authenticates with.
CREDENTIAL_ENVS = {
    "runtime": ("DATABRICKS_AGENT_RUNTIME_CLIENT_ID", "DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET"),
    "proxy": ("DATABRICKS_AGENT_PROXY_CLIENT_ID", "DATABRICKS_AGENT_PROXY_CLIENT_SECRET"),
}


def self_view_client(
    identity: str, *, application_id: str, host: str, environ: Mapping[str, str] | None = None
) -> Any:
    """A workspace client authenticated as exactly the audited identity, without touching the environment."""

    env = os.environ if environ is None else environ
    id_env, secret_env = CREDENTIAL_ENVS[identity]
    client_id, secret = env.get(id_env, "").strip(), env.get(secret_env, "").strip()
    if not host or not application_id or client_id != application_id or not secret:
        raise RuntimeError(f"the {identity} self-view needs that identity's exact OAuth credentials and host")
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.config import Config

    return WorkspaceClient(
        config=Config(
            host=host,
            client_id=client_id,
            client_secret=secret,
            auth_type="oauth-m2m",
            http_timeout_seconds=120,
        )
    )


def _owner_check(
    catalog: str, *, census: Mapping[str, frozenset[str]], aliases: set[str]
) -> Callable[[object], None]:
    owners = census.get(catalog, frozenset())

    def check(item: object) -> None:
        owner = _exact_owner(item, label="foreign UC object in the self-view")
        if owner.casefold() in aliases:
            raise RuntimeError("the audited agent identity cannot own governed UC objects")
        if owner not in owners:
            raise RuntimeError(
                f"the estate inventory does not list an owner the audited identity sees in catalog "
                f"{foreign(catalog)}; take the inventory again"
            )

    return check


def _visible_catalogs(view: Any, *, inventory: EstateInventory) -> dict[str, object]:
    visible: dict[str, object] = {}
    for item in view.catalogs.list(include_browse=True):
        name = getattr(item, "name", None)
        if not isinstance(name, str) or not name or name != name.strip() or name in visible:
            raise RuntimeError("the self-view catalog listing returned an empty, padded or repeated name")
        visible[name] = item
    missing = stale = denied = 0
    for name, item in visible.items():
        if name == inventory.install_catalog:
            continue
        facts = inventory.catalogs.get(name)
        if facts is None:
            missing += 1
            continue
        denied += facts.binding_denied(inventory.workspace_id)
        observed = (getattr(item, "owner", None), _text(getattr(item, "catalog_type", None)).upper(),
                    _text(getattr(item, "isolation_mode", None)).upper())  # fmt: skip
        stale += observed != (facts.owner, facts.catalog_type, facts.isolation_mode)
    if denied:
        raise RuntimeError(f"{denied} binding-denied foreign catalogs became visible to the audited identity")
    if missing or stale:
        raise RuntimeError(
            f"the estate inventory is stale: the audited identity sees {missing} catalogs it lacks and "
            f"{stale} it lists with another owner, type or isolation; take it again"
        )
    return visible


def assert_self_view(
    view: Any,
    *,
    application_id: str,
    inventory: EstateInventory,
    exposure: ExposureMatcher | None = None,
) -> frozenset[str]:
    """Judge the identity's own view; return the foreign catalogs whose grants it audited."""

    principal = application_id.strip()
    aliases = _assert_authenticated_runtime(view, application_id=principal)
    scope = (
        UcExposureScope(matcher=exposure, binding=inventory.binding)
        if exposure is not None and exposure.active
        else None
    )
    visible = _visible_catalogs(view, inventory=inventory)
    census = census_owners(inventory)
    platform = platform_catalog_names(inventory) | {"system", "samples", _INTERNAL}
    if _INTERNAL in visible and _effective_privilege_sources(
        view, securable_type="catalog", full_name=_INTERNAL, principal=principal
    ):
        raise RuntimeError("the audited agent identity has forbidden access on the Databricks internal catalog")
    foreign_names = sorted(set(visible) - platform - {inventory.install_catalog})

    def inspect(name: str) -> None:
        facts = inventory.catalogs[name]
        _owner_check(name, census=census, aliases=aliases)(visible[name])
        sources = _effective_privilege_sources(view, securable_type="catalog", full_name=name, principal=principal)
        if scope is not None:
            sources = scope.residual(securable_type="catalog", full_name=name, item_id=name, sources=sources)
        if sources:
            raise RuntimeError(f"the audited agent identity has forbidden access on catalog {foreign(name)}")
        _assert_no_catalog_child_privileges(
            view,
            catalog=name,
            catalog_type=facts.catalog_type,
            catalog_owner=facts.owner,
            principal=principal,
            owner_check=_owner_check(name, census=census, aliases=aliases),
            exposure=scope,
        )

    if foreign_names:
        with ThreadPoolExecutor(
            max_workers=min(_MAX_INVENTORY_WORKERS, len(foreign_names)),
            thread_name_prefix="mip-uc-self-view",
        ) as executor:
            for future in as_completed([executor.submit(inspect, name) for name in foreign_names]):
                future.result()
    for model in view.registered_models.list(include_browse=True):
        catalog, full_name = _catalog_name(model), _full_name(model)
        if not catalog or not full_name:
            raise RuntimeError("the self-view registered-model listing returned an incomplete identity")
        if catalog == inventory.install_catalog or catalog in platform:
            continue
        facts = inventory.catalogs.get(catalog)
        if facts is None:
            raise RuntimeError("the estate inventory is stale: the audited identity sees a model it lacks")
        if facts.binding_denied(inventory.workspace_id):
            raise RuntimeError("a binding-denied foreign registered model became visible to the audited identity")
        _owner_check(catalog, census=census, aliases=aliases)(model)
        _assert_privileges(
            view,
            securable_type="function",
            full_name=full_name,
            principal=principal,
            expected=set(),
            exposure=scope,
            item_id=full_name,
        )
    return frozenset(foreign_names)


__all__ = ["CREDENTIAL_ENVS", "assert_self_view", "self_view_client"]
