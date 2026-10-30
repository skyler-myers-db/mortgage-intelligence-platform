"""Golden proof that diff convergence keeps today's treatment-access postcondition.

The frozen revoke-all converger (``tests/fixtures/legacy_campaign_treatment_access``)
and the diff converger run against the same fake Unity Catalog grant store,
from every starting state in an exhaustive enumeration over small privilege
universes, in both modes and for every object-presence shape. The store models
the documented UC semantics: ``REVOKE ALL PRIVILEGES`` removes ALL PRIVILEGES
and every privilege it implies, and leaves MANAGE, READ METADATA, EXTERNAL USE
LOCATION and EXTERNAL USE SCHEMA in place.

For every state where today's converger succeeds, the diff converger succeeds
with the identical final grant store. Where today's converger fails closed,
the diff converger fails closed too, never holds a write privilege today's run
removed, and differs only by keeping reviewed privileges today revoked mid-run.
Other principals' grants are never touched. A Hypothesis property covers
larger universes, extra foreign principals and a grants API that ignores the
principal filter.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.service.catalog import Privilege
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.fixtures import legacy_campaign_treatment_access as legacy
from tests.unit import test_converge_campaign_treatment_access as converge_tests
from tools.databricks import converge_campaign_treatment_access as converger

APP = "app-client"
CATALOG = ("catalog", "mip")
SCHEMA = ("schema", "mip.audit")
TABLE = ("table", "mip.audit.campaign_treatment_snapshot")
SECURABLES = (CATALOG, SCHEMA, TABLE)
OUTSIDE = converger.OUTSIDE_ALL_PRIVILEGES
_STATEMENT_RE = re.compile(
    r"^(?P<verb>GRANT|REVOKE) (?P<privileges>.+) ON (?P<kind>CATALOG|SCHEMA|TABLE) "
    r"(?P<object>\S+) (?:TO|FROM) `(?P<principal>[^`]+)`$"
)
# Other teams' grants on the same objects. The converger must leave every one
# of them exactly as it found it.
FOREIGN = {
    CATALOG: {
        "other-team": {"USE_CATALOG", "ALL_PRIVILEGES", "MANAGE"},
        "account users": {"BROWSE"},
    },
    SCHEMA: {"other-team": {"USE_SCHEMA", "SELECT", "EXTERNAL_USE_SCHEMA"}},
    TABLE: {"other-team": {"SELECT", "MODIFY", "MANAGE"}},
}
UNIVERSE = {
    CATALOG: ("USE_CATALOG", "BROWSE", "ALL_PRIVILEGES", "MANAGE"),
    SCHEMA: ("USE_SCHEMA", "SELECT", "ALL_PRIVILEGES", "EXTERNAL_USE_SCHEMA"),
    TABLE: ("SELECT", "MODIFY", "ALL_PRIVILEGES", "MANAGE"),
}

Key = tuple[str, str]
Store = dict[Key, dict[str, set[str]]]


def _privilege(name: str) -> object:
    try:
        return Privilege(name)
    except ValueError:
        return SimpleNamespace(value=name)


@dataclass
class _Plane:
    """Fake UC grants plus statement execution over one shared grant store."""

    store: Store
    inherited: dict[Key, set[str]] = field(default_factory=dict)
    ignore_principal_filter: bool = False
    statements: list[str] = field(default_factory=list)
    snapshots: list[dict[Key, frozenset[str]]] = field(default_factory=list)

    def app(self, key: Key) -> set[str]:
        return self.store.setdefault(key, {}).setdefault(APP, set())

    # statement_execution
    def execute_statement(self, *, statement: str, **_: object) -> object:
        self.statements.append(statement)
        match = _STATEMENT_RE.fullmatch(statement)
        assert match is not None, statement
        assert match["principal"] == APP, statement
        key = (match["kind"].lower(), match["object"].replace("`", ""))
        held = self.app(key)
        names = {name.strip().replace(" ", "_") for name in match["privileges"].split(",")}
        if match["verb"] == "GRANT":
            held.update(names)
        else:
            if "ALL_PRIVILEGES" in names:
                held.intersection_update(OUTSIDE)
            held.difference_update(names)
        self.snapshots.append({k: frozenset(self.app(k)) for k in SECURABLES})
        return SimpleNamespace(
            status=SimpleNamespace(state="SUCCEEDED", error=None),
            result=SimpleNamespace(data_array=[]),
        )

    # grants
    def get(self, securable_type: str, full_name: str, **kwargs: Any) -> object:
        assert kwargs.get("max_results") == 0
        principal = kwargs.get("principal")
        grants = self.store.get((securable_type, full_name), {})
        assignments = [
            SimpleNamespace(principal=name, privileges=[_privilege(p) for p in sorted(held)])
            for name, held in sorted(grants.items())
            if held and (self.ignore_principal_filter or principal in (None, name))
        ]
        return SimpleNamespace(privilege_assignments=assignments, next_page_token="")

    def get_effective(self, securable_type: str, full_name: str, **kwargs: Any) -> object:
        assert kwargs.get("principal") == APP
        if securable_type == "metastore":
            held = {"USE_MARKETPLACE_ASSETS"}
        else:
            key = (securable_type, full_name)
            held = set(self.store.get(key, {}).get(APP, set())) | self.inherited.get(key, set())
        entries = [SimpleNamespace(privilege=_privilege(p)) for p in sorted(held)]
        return SimpleNamespace(
            privilege_assignments=[SimpleNamespace(principal=APP, privileges=entries)],
            next_page_token="",
        )


@dataclass(frozen=True)
class _Outcome:
    ok: bool
    result: bool | None
    error: str
    store: dict[Key, dict[str, frozenset[str]]]
    statements: tuple[str, ...]
    snapshots: tuple[dict[Key, frozenset[str]], ...]


def _frozen(store: Store) -> dict[Key, dict[str, frozenset[str]]]:
    return {
        key: {name: frozenset(held) for name, held in grants.items() if held}
        for key, grants in store.items()
    }


def _start_store(app: dict[Key, Iterable[str]], foreign: dict[Key, dict[str, set[str]]]) -> Store:
    store: Store = {
        key: {name: set(held) for name, held in grants.items()} for key, grants in foreign.items()
    }
    for key, held in app.items():
        store.setdefault(key, {})[APP] = set(held)
    return store


def _run(
    function: Any,
    *,
    mode: str,
    store: Store,
    inherited: dict[Key, set[str]] | None = None,
    schema_exists: bool = True,
    table_exists: bool = True,
    ignore_principal_filter: bool = False,
) -> _Outcome:
    workspace, _, _ = converge_tests._workspace(
        schema_exists=schema_exists,
        table_exists=table_exists,
    )
    plane = _Plane(
        store=store,
        inherited=inherited or {},
        ignore_principal_filter=ignore_principal_filter,
    )
    workspace.statement_execution = plane
    workspace.grants = plane
    kwargs: dict[str, Any] = {
        "warehouse_id": "warehouse-1",
        "catalog": "mip",
        "principal": APP,
        "mode": mode,
        "workspace": workspace,
        "account_factory": lambda: converge_tests._mirrored_account(workspace),
        "assert_single_writer": lambda: None,
    }
    try:
        result = function(**kwargs)
    except Exception as exc:  # noqa: BLE001 - both outcomes are compared
        return _Outcome(
            False,
            None,
            type(exc).__name__,
            _frozen(store),
            tuple(plane.statements),
            tuple(plane.snapshots),
        )
    return _Outcome(
        True, result, "", _frozen(store), tuple(plane.statements), tuple(plane.snapshots)
    )


def _app_final(outcome: _Outcome, key: Key) -> frozenset[str]:
    return outcome.store.get(key, {}).get(APP, frozenset())


def _reviewed(key: Key, mode: str) -> frozenset[str]:
    return converger.reviewed_privileges(key[0], mode)  # type: ignore[arg-type]


def _compare(
    *,
    mode: str,
    start: Store,
    present: tuple[Key, ...],
    **run_kwargs: Any,
) -> tuple[list[str], _Outcome]:
    """Run both convergers from ``start``; return violated invariants and the diff run."""

    before = _frozen(start)
    old = _run(
        legacy.converge_campaign_treatment_access, mode=mode, store=_copy(start), **run_kwargs
    )
    new = _run(
        converger.converge_campaign_treatment_access, mode=mode, store=_copy(start), **run_kwargs
    )
    label = f"mode={mode} start={ {k[0]: sorted(v.get(APP, ())) for k, v in start.items()} }"
    problems: list[str] = []
    for outcome, name in ((old, "legacy"), (new, "diff")):
        for key, grants in before.items():
            for principal, held in grants.items():
                if principal != APP and outcome.store.get(key, {}).get(principal) != held:
                    problems.append(f"{label}: {name} changed a foreign grant on {key[0]}")
    if old.ok:
        if not new.ok or new.result != old.result:
            problems.append(f"{label}: legacy ok={old.result} but diff ok={new.ok} {new.error}")
        elif new.store != old.store:
            problems.append(f"{label}: final grants differ {new.store} != {old.store}")
        for key in present:
            kept = {p for p in before.get(key, {}).get(APP, ()) if p in _reviewed(key, mode)}
            if "ALL_PRIVILEGES" in before.get(key, {}).get(APP, ()):
                continue
            if any(not kept <= snap[key] for snap in new.snapshots):
                problems.append(f"{label}: diff opened a window on {key[0]}")
        return problems, new
    if new.ok:
        problems.append(f"{label}: legacy refused ({old.error}) but diff succeeded")
        return problems, new
    for key in SECURABLES:
        old_app, new_app = _app_final(old, key), _app_final(new, key)
        if not old_app <= new_app:
            problems.append(f"{label}: diff lost {sorted(old_app - new_app)} on {key[0]}")
        if not new_app - old_app <= _reviewed(key, mode):
            problems.append(f"{label}: diff kept unreviewed {sorted(new_app - old_app)}")
    if "MODIFY" in _app_final(new, TABLE) and "MODIFY" not in _app_final(old, TABLE):
        problems.append(f"{label}: diff left a treatment write path legacy removed")
    return problems, new


def _copy(store: Store) -> Store:
    return {
        key: {name: set(held) for name, held in grants.items()} for key, grants in store.items()
    }


def _subsets(universe: tuple[str, ...]) -> list[frozenset[str]]:
    return [
        frozenset(combination)
        for size in range(len(universe) + 1)
        for combination in itertools.combinations(universe, size)
    ]


@pytest.mark.parametrize("mode", ["quiesce", "runtime"])
def test_every_full_presence_state_matches_the_revoke_all_postcondition(mode: str) -> None:
    problems: list[str] = []
    successes = refusals = 0
    for catalog, schema, table in itertools.product(
        _subsets(UNIVERSE[CATALOG]), _subsets(UNIVERSE[SCHEMA]), _subsets(UNIVERSE[TABLE])
    ):
        start = _start_store({CATALOG: catalog, SCHEMA: schema, TABLE: table}, FOREIGN)
        found, outcome = _compare(mode=mode, start=start, present=SECURABLES)
        problems.extend(found)
        expected_ok = not any(
            (held - _reviewed(key, mode)) & OUTSIDE
            for key, held in ((CATALOG, catalog), (SCHEMA, schema), (TABLE, table))
        )
        if outcome.ok != expected_ok:
            problems.append(f"mode={mode}: refusal set differs for {catalog}/{schema}/{table}")
        successes += outcome.ok
        refusals += not outcome.ok
    assert problems == []
    # 16**3 states; refusals are exactly the outside-ALL-PRIVILEGES drift states.
    assert successes + refusals == 4096
    assert successes == 8 * 8 * 8


@pytest.mark.parametrize("mode", ["quiesce", "runtime"])
def test_partial_presence_states_match_the_revoke_all_postcondition(mode: str) -> None:
    problems: list[str] = []
    for catalog, schema in itertools.product(
        _subsets(UNIVERSE[CATALOG]), _subsets(UNIVERSE[SCHEMA])
    ):
        start = _start_store({CATALOG: catalog, SCHEMA: schema}, FOREIGN)
        problems.extend(
            _compare(mode=mode, start=start, present=(CATALOG, SCHEMA), table_exists=False)[0]
        )
    for catalog in _subsets(UNIVERSE[CATALOG]):
        start = _start_store({CATALOG: catalog}, FOREIGN)
        problems.extend(
            _compare(
                mode=mode,
                start=start,
                present=(CATALOG,),
                schema_exists=False,
                table_exists=False,
            )[0]
        )
    assert problems == []


@pytest.mark.parametrize("mode", ["quiesce", "runtime"])
@pytest.mark.parametrize(
    "inherited",
    [{SCHEMA: {"SELECT"}}, {TABLE: {"MODIFY"}}, {CATALOG: {"USE_CATALOG"}}],
)
def test_inherited_authority_refuses_exactly_as_before(
    mode: str, inherited: dict[Key, set[str]]
) -> None:
    problems: list[str] = []
    for table in _subsets(UNIVERSE[TABLE]):
        start = _start_store(
            {CATALOG: {"USE_CATALOG"}, SCHEMA: {"USE_SCHEMA"}, TABLE: table}, FOREIGN
        )
        problems.extend(
            _compare(mode=mode, start=start, present=SECURABLES, inherited=inherited)[0]
        )
    assert problems == []


@pytest.mark.parametrize("mode", ["quiesce", "runtime"])
def test_converged_boundary_issues_no_statement(mode: str) -> None:
    reviewed = {key: _reviewed(key, mode) for key in SECURABLES}
    start = _start_store(reviewed, FOREIGN)

    outcome = _run(converger.converge_campaign_treatment_access, mode=mode, store=start)

    assert outcome.ok and outcome.result is True
    assert outcome.statements == ()


def test_quiesce_from_runtime_revokes_only_modify() -> None:
    start = _start_store(
        {CATALOG: {"USE_CATALOG"}, SCHEMA: {"USE_SCHEMA"}, TABLE: {"SELECT", "MODIFY"}}, FOREIGN
    )

    outcome = _run(converger.converge_campaign_treatment_access, mode="quiesce", store=start)

    assert outcome.ok
    assert outcome.statements == (
        "REVOKE MODIFY ON TABLE `mip`.`audit`.`campaign_treatment_snapshot` FROM `app-client`",
    )


def test_runtime_from_quiesce_grants_only_modify() -> None:
    start = _start_store(
        {CATALOG: {"USE_CATALOG"}, SCHEMA: {"USE_SCHEMA"}, TABLE: {"SELECT"}}, FOREIGN
    )

    outcome = _run(converger.converge_campaign_treatment_access, mode="runtime", store=start)

    assert outcome.ok
    assert outcome.statements == (
        "GRANT MODIFY ON TABLE `mip`.`audit`.`campaign_treatment_snapshot` TO `app-client`",
    )


def test_first_install_grants_parents_before_the_table() -> None:
    start = _start_store({}, FOREIGN)

    outcome = _run(converger.converge_campaign_treatment_access, mode="runtime", store=start)

    assert outcome.ok
    assert outcome.statements == (
        "GRANT USE CATALOG ON CATALOG `mip` TO `app-client`",
        "GRANT USE SCHEMA ON SCHEMA `mip`.`audit` TO `app-client`",
        "GRANT SELECT, MODIFY ON TABLE `mip`.`audit`.`campaign_treatment_snapshot` "
        "TO `app-client`",
    )


def test_all_privileges_drift_is_revoked_before_the_reviewed_regrant() -> None:
    start = _start_store(
        {CATALOG: {"USE_CATALOG"}, SCHEMA: {"USE_SCHEMA"}, TABLE: {"ALL_PRIVILEGES"}}, FOREIGN
    )

    outcome = _run(converger.converge_campaign_treatment_access, mode="quiesce", store=start)

    assert outcome.ok
    assert outcome.statements == (
        "REVOKE ALL PRIVILEGES ON TABLE `mip`.`audit`.`campaign_treatment_snapshot` "
        "FROM `app-client`",
        "GRANT SELECT ON TABLE `mip`.`audit`.`campaign_treatment_snapshot` TO `app-client`",
    )


def test_a_grants_api_that_ignores_the_principal_filter_cannot_widen_the_plan() -> None:
    start = _start_store(
        {CATALOG: {"USE_CATALOG"}, SCHEMA: {"USE_SCHEMA"}, TABLE: {"SELECT"}}, FOREIGN
    )

    outcome = _run(
        converger.converge_campaign_treatment_access,
        mode="quiesce",
        store=start,
        ignore_principal_filter=True,
    )

    assert outcome.ok
    assert outcome.statements == ()
    assert outcome.store == _frozen(start)


def test_case_variant_app_principal_assignment_fails_closed() -> None:
    start = _start_store({CATALOG: {"USE_CATALOG"}}, {CATALOG: {APP.upper(): {"MANAGE"}}})

    outcome = _run(
        converger.converge_campaign_treatment_access,
        mode="quiesce",
        store=start,
        ignore_principal_filter=True,
    )

    assert not outcome.ok
    assert outcome.statements == ()


_PRIVILEGES = st.sampled_from(
    (
        "SELECT",
        "MODIFY",
        "USE_CATALOG",
        "USE_SCHEMA",
        "BROWSE",
        "APPLY_TAG",
        "ALL_PRIVILEGES",
        "MANAGE",
        "READ_METADATA",
        "EXTERNAL_USE_SCHEMA",
    )
)
_SETS = st.frozensets(_PRIVILEGES, max_size=5)
_FOREIGN_NAMES = st.sampled_from(("other-team", "account users", "data-stewards"))


@settings(
    max_examples=300,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    mode=st.sampled_from(("quiesce", "runtime")),
    app=st.fixed_dictionaries({key: _SETS for key in SECURABLES}),
    foreign=st.fixed_dictionaries(
        {key: st.dictionaries(_FOREIGN_NAMES, _SETS, max_size=3) for key in SECURABLES}
    ),
    ignore_filter=st.booleans(),
)
def test_property_converged_equals_reviewed_and_foreign_grants_never_change(
    mode: str,
    app: dict[Key, frozenset[str]],
    foreign: dict[Key, dict[str, frozenset[str]]],
    ignore_filter: bool,
) -> None:
    start = _start_store(app, {k: {n: set(v) for n, v in g.items()} for k, g in foreign.items()})

    problems, new = _compare(
        mode=mode, start=start, present=SECURABLES, ignore_principal_filter=ignore_filter
    )

    assert problems == []
    refuses = any((held - _reviewed(key, mode)) & OUTSIDE for key, held in app.items())
    assert new.ok is not refuses
    if new.ok:
        assert all(_app_final(new, key) == _reviewed(key, mode) for key in SECURABLES)
