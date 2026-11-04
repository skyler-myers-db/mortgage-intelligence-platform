"""Golden proof that diff convergence keeps today's treatment-access postcondition.

The frozen revoke-all converger (``tests/fixtures/legacy_campaign_treatment_access``)
and the diff converger run against the same fake Unity Catalog grant store,
from every starting state in an exhaustive enumeration over small privilege
universes, in both modes and for every object-presence shape. The store models
the documented UC semantics: ``REVOKE ALL PRIVILEGES`` removes ALL PRIVILEGES
and every privilege it implies, and leaves MANAGE, READ METADATA, EXTERNAL USE
LOCATION and EXTERNAL USE SCHEMA in place; a privilege granted on the catalog
or the schema reaches every securable below it (``_effective``).

For every state where today's converger succeeds, the diff converger succeeds
with the identical final grant store. Where today's converger fails closed,
the diff converger fails closed too, never holds a write privilege today's run
removed, and differs only by keeping reviewed privileges today revoked mid-run.
In every run, a treatment write path (``_write_path``, inheritance included)
never appears in quiesce, and in runtime appears only at a table grant issued
after the parent boundary proof. Other principals' grants are never touched. A
Hypothesis property covers larger universes, extra foreign principals and a
grants API that ignores the principal filter.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Iterable, Mapping
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
# Every privilege that can complete a treatment write path, on every level it
# can be granted: MODIFY and USE SCHEMA reach the table from a parent, and
# ALL PRIVILEGES implies both.
WRITE_UNIVERSE = {
    CATALOG: ("USE_CATALOG", "USE_SCHEMA", "MODIFY", "ALL_PRIVILEGES"),
    SCHEMA: ("USE_SCHEMA", "MODIFY", "ALL_PRIVILEGES"),
    TABLE: ("SELECT", "MODIFY", "ALL_PRIVILEGES"),
}

Key = tuple[str, str]
Store = dict[Key, dict[str, set[str]]]
# A usage privilege reaches only the levels it gates: USE SCHEMA granted on the
# catalog reaches its schemas but no table, and USE CATALOG stays on the
# catalog. The converger's exact postflights pass on the live estate, which
# pins that neither is reported below that level.
_USAGE_LEVEL = {"USE_CATALOG": 0, "USE_SCHEMA": 1, "EXTERNAL_USE_SCHEMA": 1}


def _effective(app: Mapping[Key, Iterable[str]], key: Key) -> set[str]:
    """The App's effective privileges on ``key`` from its own direct grants.

    Unity Catalog applies a privilege granted on a catalog or a schema to every
    current and future securable below it, so MODIFY on the audit schema is
    MODIFY on the treatment table.
    """

    level = SECURABLES.index(key)
    inherited = {
        privilege
        for ancestor in SECURABLES[:level]
        for privilege in app.get(ancestor, ())
        if _USAGE_LEVEL.get(privilege, level) >= level
    }
    return set(app.get(key, ())) | inherited


def _has(held: set[str], privilege: str) -> bool:
    return privilege in held or "ALL_PRIVILEGES" in held


def _write_path(app: Mapping[Key, Iterable[str]]) -> bool:
    """Whether the App's own grants let it write the treatment table."""

    return (
        _has(_effective(app, CATALOG), "USE_CATALOG")
        and _has(_effective(app, SCHEMA), "USE_SCHEMA")
        and _has(_effective(app, TABLE), "MODIFY")
    )


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
    fail_at: int | None = None
    statements: list[str] = field(default_factory=list)
    snapshots: list[dict[Key, frozenset[str]]] = field(default_factory=list)
    # Per statement: whether an effective-grants read of the catalog, the first
    # read of the parent boundary proof, had already run.
    after_parent_proof: list[bool] = field(default_factory=list)
    catalog_proofs: int = 0

    def app(self, key: Key) -> set[str]:
        return self.store.setdefault(key, {}).setdefault(APP, set())

    def app_state(self) -> dict[Key, frozenset[str]]:
        return {k: frozenset(self.app(k)) for k in SECURABLES}

    # statement_execution
    def execute_statement(self, *, statement: str, **_: object) -> object:
        self.statements.append(statement)
        self.after_parent_proof.append(self.catalog_proofs > 0)
        if len(self.statements) - 1 == self.fail_at:
            # An injected warehouse failure: the grant store is unchanged.
            self.snapshots.append(self.app_state())
            return SimpleNamespace(
                status=SimpleNamespace(state="FAILED", error="injected failure"),
                result=None,
            )
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
        self.snapshots.append(self.app_state())
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
            self.catalog_proofs += key == CATALOG
            app = {k: v.get(APP, set()) for k, v in self.store.items()}
            held = _effective(app, key) | self.inherited.get(key, set())
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
    after_parent_proof: tuple[bool, ...] = ()
    message: str = ""


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
    catalog_exists: bool = True,
    schema_exists: bool = True,
    table_exists: bool = True,
    ignore_principal_filter: bool = False,
    fail_at: int | None = None,
) -> _Outcome:
    workspace, _, _ = converge_tests._workspace(
        catalog_exists=catalog_exists,
        schema_exists=schema_exists,
        table_exists=table_exists,
    )
    plane = _Plane(
        store=store,
        inherited=inherited or {},
        ignore_principal_filter=ignore_principal_filter,
        fail_at=fail_at,
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
    if function is converger.converge_campaign_treatment_access:
        # The frozen legacy converger predates MIP_UC_METASTORE_OWNER (M2-PR4).
        kwargs["metastore_owner"] = "deployer"

    def outcome(ok: bool, result: bool | None, error: str, message: str = "") -> _Outcome:
        return _Outcome(
            ok,
            result,
            error,
            _frozen(store),
            tuple(plane.statements),
            tuple(plane.snapshots),
            tuple(plane.after_parent_proof),
            message,
        )

    try:
        result = function(**kwargs)
    except Exception as exc:  # noqa: BLE001 - both outcomes are compared
        return outcome(False, None, type(exc).__name__, str(exc))
    return outcome(True, result, "")


def _app_final(outcome: _Outcome, key: Key) -> frozenset[str]:
    return outcome.store.get(key, {}).get(APP, frozenset())


def _reviewed(key: Key, mode: str) -> frozenset[str]:
    return converger.reviewed_privileges(key[0], mode)  # type: ignore[arg-type]


def _app_state(store: Store) -> dict[Key, frozenset[str]]:
    return {key: frozenset(store.get(key, {}).get(APP, ())) for key in SECURABLES}


def _parent_boundary_held(app: dict[Key, frozenset[str]], present: tuple[Key, ...]) -> bool:
    """Whether every present parent already held its reviewed grant, with no ALL PRIVILEGES."""

    return all(
        _reviewed(key, "runtime") <= app[key] and "ALL_PRIVILEGES" not in app[key]
        for key in (CATALOG, SCHEMA)
        if key in present
    )


def _write_path_problems(label: str, mode: str, start: Store, outcome: _Outcome) -> list[str]:
    """A write path never appears in quiesce; in runtime only at a proven table grant.

    Also: a failed run never leaves a write path its start did not hold.
    """

    states = [_app_state(start), *outcome.snapshots]
    problems: list[str] = []
    for index, statement in enumerate(outcome.statements):
        if _write_path(states[index + 1]) and not _write_path(states[index]):
            table_grant = statement.startswith("GRANT ") and " ON TABLE " in statement
            if mode == "quiesce" or not (table_grant and outcome.after_parent_proof[index]):
                problems.append(f"{label}: write path opened by {statement!r}")
    if mode == "quiesce" and outcome.ok and _write_path(states[-1]):
        problems.append(f"{label}: quiesce succeeded with a write path")
    if not outcome.ok and _write_path(states[-1]) and not _write_path(states[0]):
        problems.append(f"{label}: failed run left a write path the start did not hold")
    return problems


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
        # The oracle meets the write-path invariant too, so the diff converger
        # is held to today's behaviour, not to a stricter rule.
        problems.extend(_write_path_problems(f"{label} {name}", mode, start, outcome))
    if old.ok:
        if not new.ok or new.result != old.result:
            problems.append(f"{label}: legacy ok={old.result} but diff ok={new.ok} {new.error}")
        elif new.store != old.store:
            problems.append(f"{label}: final grants differ {new.store} != {old.store}")
        start_app = _app_state(start)
        for key in present:
            if "ALL_PRIVILEGES" in start_app[key]:
                continue
            kept = start_app[key] & _reviewed(key, mode)
            if key == TABLE and not _parent_boundary_held(start_app, present):
                # No reviewed write path ran through the table at the start, and
                # runtime holds MODIFY back until the parent boundary is proven.
                kept -= {"MODIFY"}
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
    old_final, new_final = ({key: _app_final(run, key) for key in SECURABLES} for run in (old, new))
    if _write_path(new_final) and not _write_path(old_final):
        problems.append(f"{label}: diff left an inherited write path legacy removed")
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
