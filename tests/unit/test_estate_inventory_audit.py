"""The foreign UC audit on the owner's inventory, the account plane and the self-view (M4-PR1).

Spike S0(c) is owed: whether an agent identity sees what it owns without
USE_CATALOG. These tests assume the worst case, a self-view that does not see
its own objects there, and pin that the inventory still catches that
ownership; that any inventory gap, stale entry or self-view object the
inventory lacks refuses; that an API refusal is never read as absent; that a
defunct workspace passes only with its exact entry; and that the pre-stop
checks refuse exactly when the post-stop audit raises, over one fixture
matrix (the harness is ``test_audit_agent_runtime_foreign_uc_access``).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.errors import PermissionDenied, ResourceDoesNotExist

from tests.unit import test_audit_agent_runtime_foreign_uc_access as harness
from tools.clients import estate_inventory
from tools.clients.handover_journal import canonical_json
from tools.databricks.estate import inventory as estate
from tools.databricks.estate.inventory_check import prelease_refusals

NOW = harness.NOW
HIDDEN = "hidden-team-catalog"
# A synthetic id: a second workspace the catalog is rebound to after the inventory.
THIRD_WORKSPACE_ID = "1000000000000003"
RUNTIME_ALIASES = (harness.APPLICATION_ID, "runtime-scim-id", harness.ACCOUNT_SCIM_ID, "runtime-workspace")


def _hidden_world(
    *,
    schema_owner: str = harness.FOREIGN_OWNER,
    bound_to: str = harness.OTHER_WORKSPACE_ID,
    isolation_mode: str = "ISOLATED",
) -> tuple[Any, dict[str, Any]]:
    """A catalog bound only to another workspace, readable through the owner's bound reader."""

    workspace = harness._workspace(
        extra_catalogs=[SimpleNamespace(name=HIDDEN, isolation_mode=isolation_mode, owner=harness.FOREIGN_OWNER)]
    )
    original = workspace.workspace_bindings.get_bindings
    workspace.workspace_bindings.get_bindings = lambda kind, name: (
        iter([SimpleNamespace(workspace_id=bound_to, binding_type="BINDING_TYPE_READ_ONLY")])
        if name == HIDDEN
        else original(kind, name)
    )
    schema = SimpleNamespace(name="models", full_name=f"{HIDDEN}.models", owner=schema_owner)
    return workspace, {harness.OTHER_WORKSPACE_ID: harness._bound_reader(schemas={HIDDEN: [schema]})}


@pytest.mark.parametrize("owner", RUNTIME_ALIASES)
def test_runtime_ownership_hidden_by_a_revoked_use_catalog_is_caught_through_the_inventory(owner: str) -> None:
    # The runtime owns a table in ``other`` but can no longer see the catalog (S0(c), worst case).
    workspace = harness._workspace(owner_overrides={"other.sandbox.secret": owner})
    blind = harness._self_view(workspace, hidden=("other",))

    with pytest.raises(RuntimeError, match="cannot own governed UC objects"):
        harness._audit(workspace, self_view=blind)
    # The control: the same blind view passes once the runtime owns nothing there.
    assert harness._audit(harness._workspace(), self_view=blind).grant_audited_catalogs == frozenset()


def test_runtime_ownership_inside_a_binding_denied_catalog_is_caught_through_the_inventory() -> None:
    workspace, readers = _hidden_world(schema_owner=harness.APPLICATION_ID)

    with pytest.raises(RuntimeError, match="cannot own governed UC objects"):
        harness._audit(workspace, bound_readers=readers)
    clean, readers = _hidden_world()
    proof = harness._audit(clean, bound_readers=readers)
    assert tuple(item.catalog for item in proof.binding_denied_catalogs) == (HIDDEN,)


def test_account_users_ownership_is_never_acceptable_even_unseen() -> None:
    workspace, readers = _hidden_world(schema_owner="account users")

    with pytest.raises(RuntimeError, match="cannot own governed UC objects"):
        harness._audit(workspace, bound_readers=readers)


def test_a_catalog_the_self_view_sees_that_the_inventory_lacks_refuses() -> None:
    taken = harness._workspace()
    inventory = harness._inventory(taken)
    now = harness._workspace(
        extra_catalogs=[SimpleNamespace(name="newer", isolation_mode="OPEN", owner=harness.FOREIGN_OWNER)]
    )

    with pytest.raises(RuntimeError, match="stale"):
        harness._audit(now, inventory=inventory, self_view=harness._self_view(taken))
    with pytest.raises(RuntimeError, match="stale: the audited identity sees 1 catalogs it lacks"):
        harness._audit(taken, inventory=inventory, self_view=harness._self_view(now))


def test_a_catalog_whose_owner_changed_since_the_inventory_refuses() -> None:
    inventory = harness._inventory(harness._workspace())
    moved = harness._workspace(owner_overrides={"other": "new-owner@example.com"})

    with pytest.raises(RuntimeError, match="stale"):
        harness._audit(moved, inventory=inventory)


def _sdk_catalogs(workspace: Any) -> list[Any]:
    """The same catalogs as the SDK returns them, enum fields included."""

    from databricks.sdk.service.catalog import CatalogInfo, CatalogIsolationMode, CatalogType

    return [
        CatalogInfo(
            name=item.name,
            owner=item.owner,
            catalog_type=CatalogType(item.catalog_type),
            isolation_mode=CatalogIsolationMode(item.isolation_mode),
        )
        for item in workspace.catalogs.list(include_browse=True, include_unbound=True)
    ]


def test_sdk_enum_fields_are_read_as_their_values_by_the_collector_and_the_audit() -> None:
    plain = harness._workspace()
    typed = harness._workspace()
    catalogs = _sdk_catalogs(typed)
    typed.catalogs.list = lambda **_kwargs: iter(catalogs)

    assert canonical_json(harness._inventory(typed).document) == canonical_json(harness._inventory(plain).document)
    assert harness._audit(typed, inventory=harness._inventory(plain), self_view=harness._self_view(plain))


def test_the_install_catalog_may_change_owner_after_the_inventory() -> None:
    inventory = harness._inventory(harness._workspace())
    handed_over = harness._workspace(owner_overrides={})
    for item in handed_over.catalogs.list(include_browse=True, include_unbound=True):
        if item.name == harness.CATALOG:
            item.owner = "mip-data-owners"

    assert harness._audit(handed_over, inventory=inventory)


def test_a_self_view_owner_the_inventory_does_not_list_refuses() -> None:
    inventory = harness._inventory(harness._workspace())
    workspace = harness._workspace(owner_overrides={"other.sandbox.secret_fn": "late-owner@example.com"})

    with pytest.raises(RuntimeError, match="does not list an owner the audited identity sees"):
        harness._audit(harness._workspace(), inventory=inventory, self_view=harness._self_view(workspace))


def test_a_binding_denied_catalog_visible_to_the_self_view_refuses() -> None:
    workspace, readers = _hidden_world()
    inventory = harness._inventory(workspace, bound_readers=readers)
    sees_all = harness._self_view(harness._workspace(
        extra_catalogs=[SimpleNamespace(name=HIDDEN, isolation_mode="ISOLATED", owner=harness.FOREIGN_OWNER)]
    ))

    with pytest.raises(RuntimeError, match="binding-denied foreign catalogs became visible"):
        harness._audit(workspace, inventory=inventory, self_view=sees_all)


def test_bindings_are_judged_as_of_the_inventory_and_isolation_and_visibility_live() -> None:
    """docs/deployment.md, "What is judged as of the inventory": the declared binding-drift behaviour."""

    taken, readers = _hidden_world()
    inventory = harness._inventory(taken, bound_readers=readers)
    rebound, _readers = _hidden_world(bound_to=THIRD_WORKSPACE_ID)
    opened, _readers = _hidden_world(isolation_mode="OPEN")
    into_view, _readers = _hidden_world(bound_to=harness.WORKSPACE_ID)

    # Rebound to a second workspace while it stays ISOLATED and unseen: passes on the old inventory,
    # and the proof and an exposure entry's binding carry the inventory's bindings, not the live ones.
    (evidence,) = harness._audit(rebound, inventory=inventory).binding_denied_catalogs
    assert (evidence.catalog, evidence.bindings) == (HIDDEN, ((harness.OTHER_WORKSPACE_ID, "BINDING_TYPE_READ_ONLY"),))
    assert inventory.binding(HIDDEN) == ("ISOLATED", (harness.OTHER_WORKSPACE_ID,))
    # Its isolation mode changed: the inventory is stale.
    with pytest.raises(RuntimeError, match="0 listed catalogs are missing from it and 1 changed owner, type or isolation"):
        harness._audit(opened, inventory=inventory)
    # Rebound into the install workspace, so the identity sees it: refused.
    with pytest.raises(RuntimeError, match="1 binding-denied foreign catalogs became visible"):
        harness._audit(into_view, inventory=inventory)


def test_an_inventory_gap_refuses_the_audit() -> None:
    workspace, _readers = _hidden_world()

    with pytest.raises(RuntimeError, match="1 gaps"):
        harness._audit(workspace, bound_readers={})


def test_an_inventory_of_another_metastore_refuses_the_audit() -> None:
    inventory = harness._inventory(harness._workspace())
    workspace = harness._workspace()
    workspace.metastores.current = lambda: SimpleNamespace(metastore_id="metastore-other")

    with pytest.raises(RuntimeError, match="another metastore"):
        harness._audit(workspace, inventory=inventory)


@pytest.mark.parametrize("plane", ["self_view_grants", "self_view_catalogs", "self_view_models", "account"])
def test_an_api_refusal_is_never_read_as_absent(plane: str) -> None:
    workspace = harness._workspace()
    inventory = harness._inventory(workspace)
    view = harness._self_view(workspace)
    account = harness._account()

    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise PermissionDenied(f"{plane} refused")

    if plane == "self_view_grants":
        workspace.grants.denied = ("schema", "other.sandbox")
    elif plane == "self_view_catalogs":
        view.catalogs.list = refuse
    elif plane == "self_view_models":
        view.registered_models.list = refuse
    else:
        account.workspaces.get = refuse

    with pytest.raises(PermissionDenied):
        harness._audit(workspace, inventory=inventory, self_view=view, account_factory=lambda: account)


def _defunct_account(*, assignments: Callable[[], Any]) -> Any:
    account = harness._account()

    def get(workspace_id: int) -> Any:
        if str(workspace_id) == harness.OTHER_WORKSPACE_ID:
            raise ResourceDoesNotExist("workspace deleted")
        return SimpleNamespace(workspace_id=workspace_id)

    account.workspaces.get = get
    original = account.workspace_assignment.list
    account.workspace_assignment.list = lambda workspace_id: (
        assignments() if str(workspace_id) == harness.OTHER_WORKSPACE_ID else original(workspace_id)
    )
    return account


def _assignment(principal_id: str, *, service_principal: str = "", group: str = "") -> Any:
    return SimpleNamespace(
        permissions=["USER"],
        principal=SimpleNamespace(
            principal_id=principal_id, service_principal_name=service_principal, group_name=group, user_name=""
        ),
    )


def test_a_defunct_workspace_passes_only_with_its_exact_entry() -> None:
    lingering = [_assignment("other-team-group-id", group="other-team")]
    account = _defunct_account(assignments=lambda: iter(lingering))
    workspace = harness._workspace()
    inventory = harness._inventory(workspace, account=account)

    assert harness._audit(workspace, inventory=inventory, account_factory=lambda: account)
    # Without the entry (an inventory taken while the workspace still existed): refused.
    with pytest.raises(RuntimeError, match="no exact defunct entry"):
        harness._audit(workspace, inventory=harness._inventory(workspace), account_factory=lambda: account)
    # Its assignments changed since the inventory: refused.
    lingering.append(_assignment("another-group-id", group="another-team"))
    with pytest.raises(RuntimeError, match="differ from the estate inventory"):
        harness._audit(workspace, inventory=inventory, account_factory=lambda: account)


def test_a_defunct_workspace_entry_never_admits_a_runtime_assignment() -> None:
    account = _defunct_account(
        assignments=lambda: iter([_assignment(harness.ACCOUNT_SCIM_ID, service_principal=harness.APPLICATION_ID)])
    )
    workspace = harness._workspace()
    inventory = harness._inventory(workspace, account=account)

    with pytest.raises(RuntimeError, match="unexpected account workspace assignment"):
        harness._audit(workspace, inventory=inventory, account_factory=lambda: account)


def test_a_defunct_workspace_whose_assignments_no_longer_list_passes_with_that_exact_entry() -> None:
    def gone() -> Any:
        raise ResourceDoesNotExist("no assignments")

    account = _defunct_account(assignments=gone)
    workspace = harness._workspace()

    assert harness._audit(
        workspace, inventory=harness._inventory(workspace, account=account), account_factory=lambda: account
    )


@pytest.mark.parametrize(
    ("bound", "refused"),
    [
        ({harness.OTHER_WORKSPACE_ID}, False),
        ({harness.WORKSPACE_ID}, True),
        ({harness.OTHER_WORKSPACE_ID, "unassigned-workspace"}, True),
    ],
    ids=["another-assigned-workspace", "the-install-workspace", "off-the-metastore"],
)
def test_a_binding_denied_catalog_must_be_bound_to_another_assigned_workspace(
    bound: set[str], refused: bool
) -> None:
    # The bound workspaces come from the inventory now (M4-PR1); the retired
    # binding policy is not named in the refusal (M4-PR2).
    def check() -> object:
        return harness.auditor._assert_runtime_workspace_assignment_boundary(
            harness._account(),
            application_id=harness.APPLICATION_ID,
            target_scim_ids={harness.ACCOUNT_SCIM_ID},
            account_target_scim_id=harness.ACCOUNT_SCIM_ID,
            account_effective_groups={},
            account_group_inventory={},
            effective_target_groups={},
            implicit_system_groups={"account-users-id": "account users"},
            workspace_system_groups={},
            allowed_workspace_groups={},
            metastore_id="metastore-under-test",
            workspace_id=harness.WORKSPACE_ID,
            approved_foreign_workspace_ids=bound,
        )

    if not refused:
        assert check() == {harness.WORKSPACE_ID, harness.OTHER_WORKSPACE_ID}
        return
    with pytest.raises(RuntimeError) as raised:
        check()
    assert str(raised.value) == (
        "the estate inventory binds a foreign catalog to the install workspace or to a workspace "
        "not assigned to the metastore"
    )


# --- Parity: the pre-stop checks refuse exactly when the post-stop audit raises ---------------------


def _matrix() -> dict[str, Callable[[], dict[str, Any]]]:
    """Each world: the deployer's workspace, the inventory's document, the env overrides, the self-view."""

    def world(**changes: Any) -> dict[str, Any]:
        workspace = changes.pop("workspace", None) or harness._workspace()
        readers = changes.pop("bound_readers", {harness.OTHER_WORKSPACE_ID: harness._bound_reader()})
        account = changes.pop("account", None) or harness._account()
        document = estate_inventory.collect(
            changes.pop("inventory_workspace", workspace),
            account=account,
            install_catalog=harness.CATALOG,
            now=changes.pop("issued", NOW),
            bound_readers=readers,
        )
        return {
            "workspace": workspace,
            "document": document,
            "account": account,
            "view": changes.pop("view", None) or harness._self_view(workspace),
            "env": changes.pop("env", {}),
            "ack": changes.pop("ack", None),
        }

    def owned_hidden() -> dict[str, Any]:
        workspace = harness._workspace(owner_overrides={"other.sandbox.secret": harness.APPLICATION_ID})
        return world(workspace=workspace, view=harness._self_view(workspace, hidden=("other",)))

    def binding_denied_owned() -> dict[str, Any]:
        workspace, readers = _hidden_world(schema_owner=harness.APPLICATION_ID)
        return world(workspace=workspace, bound_readers=readers)

    def gapped() -> dict[str, Any]:
        workspace, _readers = _hidden_world()
        return world(workspace=workspace, bound_readers={})

    def visible_grant() -> dict[str, Any]:
        grants = {("catalog", "other"): [harness._assignment("SELECT", principal="account users")]}
        return world(workspace=harness._workspace(grants))

    def stale() -> dict[str, Any]:
        newer = SimpleNamespace(name="newer", isolation_mode="OPEN", owner=harness.FOREIGN_OWNER)
        return world(workspace=harness._workspace(extra_catalogs=[newer]), inventory_workspace=harness._workspace())

    def defunct_without_entry() -> dict[str, Any]:
        account = _defunct_account(assignments=lambda: iter([]))
        state = world()
        state["account"] = account
        return state

    return {
        "clean": world,
        "fresh enough for the whole run": lambda: world(issued=NOW - timedelta(days=28)),
        "expires within the run's margin": lambda: world(issued=NOW - timedelta(days=29, hours=12)),
        "issued in the future": lambda: world(issued=NOW + timedelta(minutes=5)),
        "runtime ownership hidden from the self-view": owned_hidden,
        "runtime ownership in a binding-denied catalog": binding_denied_owned,
        "inventory gap": gapped,
        "visible foreign grant": visible_grant,
        "stale inventory": stale,
        "defunct workspace without its entry": defunct_without_entry,
        "ack for another document": lambda: world(ack="0" * 64),
        "retired policy set": lambda: world(env={estate.RETIRED_POLICY_ENV: '{"version":1,"catalogs":{}}'}),
        "another metastore": lambda: world(
            inventory_workspace=_on_metastore(harness._workspace(), "metastore-other")
        ),
    }


def _on_metastore(workspace: Any, metastore_id: str) -> Any:
    workspace.metastores.current = lambda: SimpleNamespace(metastore_id=metastore_id)
    return workspace


def _environ(state: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    document = state["document"]
    monkeypatch.delenv(estate.INVENTORY_FILE_ENV, raising=False)
    monkeypatch.delenv(estate.RETIRED_POLICY_ENV, raising=False)
    monkeypatch.setenv(estate.INVENTORY_JSON_ENV, canonical_json(document))
    monkeypatch.setenv(estate.ACK_ENV, state["ack"] or estate.estate_digest(document))
    for name, value in state["env"].items():
        monkeypatch.setenv(name, value)


def _audit(state: dict[str, Any], *, now: Any, allow_missing: bool) -> None:
    inventory = estate.load_estate_inventory(
        metastore_id="metastore-id", workspace_id=harness.WORKSPACE_ID, install_catalog=harness.CATALOG, now=now
    )
    harness._audit(
        state["workspace"],
        inventory=inventory,
        self_view=state["view"],
        account_factory=lambda: state["account"],
        allow_missing_mip_catalog=allow_missing,
    )


def _prestop_refuses(state: dict[str, Any]) -> bool:
    """The pre-lease inventory check, then the pre-stop audit (``--allow-missing-mip-catalog``)."""

    refusals, _notes = prelease_refusals(state["workspace"], catalog=harness.CATALOG, now=NOW)
    if refusals:
        return True
    try:
        _audit(state, now=NOW, allow_missing=True)
    except RuntimeError:
        return True
    return False


def _poststop_raises(state: dict[str, Any], *, elapsed: timedelta) -> bool:
    """Step 10b's audit, as late in the run as the margin allows."""

    try:
        _audit(state, now=NOW + elapsed, allow_missing=False)
    except RuntimeError:
        return True
    return False


@pytest.mark.parametrize("case", sorted(_matrix()))
def test_the_post_stop_audit_raises_exactly_when_the_pre_stop_checks_refuse(
    case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-stop at ``t`` refuses iff the post-stop audit raises at ``t`` or at ``t + RUN_MARGIN``.

    The post-stop audit runs somewhere in that window (``RUN_MARGIN`` is the
    reviewed bound on one run); judging age at the window's end is what lets the
    pre-stop checks refuse an inventory that would expire with the App stopped.
    """

    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    state = _matrix()[case]()
    _environ(state, monkeypatch)

    prestop = _prestop_refuses(state)
    poststop = _poststop_raises(state, elapsed=timedelta(0)) or _poststop_raises(state, elapsed=estate.RUN_MARGIN)

    assert poststop == prestop, case
    assert prestop == (case not in {"clean", "fresh enough for the whole run"}), case
