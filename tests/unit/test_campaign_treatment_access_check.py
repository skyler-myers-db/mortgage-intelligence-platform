"""The read-only treatment-access check is side-effect free and agrees with convergence."""

from __future__ import annotations

import itertools
from typing import Any

import pytest

from tests.unit import test_campaign_treatment_access_diff_convergence as golden
from tests.unit import test_converge_campaign_treatment_access as converge_tests
from tools.databricks import campaign_treatment_access_check as check_module
from tools.databricks import converge_campaign_treatment_access as converger
from tools.databricks.campaign_treatment_access_check import check_campaign_treatment_access

CATALOG, SCHEMA, TABLE = golden.CATALOG, golden.SCHEMA, golden.TABLE
_READ_ONLY_METHODS = {"get", "list", "get_effective", "current", "me"}
# Values that identify this fake estate. None may appear in a check report.
_IDENTIFIERS = ("mip", "app-client", "sp-id", "metastore-1", "warehouse", "other-team", "deployer")


class _Spy:
    """Record every method called anywhere under the wrapped fake client."""

    def __init__(self, target: object, calls: list[str], path: str = "") -> None:
        self._target = target
        self._calls = calls
        self._path = path

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._target, name)
        path = f"{self._path}.{name}" if self._path else name
        if callable(value):

            def record(*args: Any, **kwargs: Any) -> Any:
                self._calls.append(path)
                return value(*args, **kwargs)

            return record
        if isinstance(value, str | int | float | bool | type(None) | list | dict | tuple):
            return value
        return _Spy(value, self._calls, path)


def _workspace(
    store: golden.Store,
    *,
    inherited: dict[golden.Key, set[str]] | None = None,
    catalog_exists: bool = True,
    schema_exists: bool = True,
    table_exists: bool = True,
    ignore_principal_filter: bool = False,
) -> tuple[_Spy, list[str], golden._Plane]:
    workspace, _, _ = converge_tests._workspace(
        catalog_exists=catalog_exists, schema_exists=schema_exists, table_exists=table_exists
    )
    plane = golden._Plane(
        store=store,
        inherited=inherited or {},
        ignore_principal_filter=ignore_principal_filter,
    )
    workspace.statement_execution = plane
    workspace.grants = plane
    calls: list[str] = []
    return _Spy(workspace, calls), calls, plane


def _assert_read_only(calls: list[str], plane: golden._Plane) -> None:
    assert calls, "the spy saw no calls at all"
    assert plane.statements == []
    assert not any(call.startswith("statement_execution") for call in calls)
    assert {call.rsplit(".", 1)[-1] for call in calls} <= _READ_ONLY_METHODS, sorted(set(calls))


def _check(mode: str, store: golden.Store, **kwargs: Any) -> tuple[list[str] | None, list[str]]:
    spy, calls, plane = _workspace(store, **kwargs)
    drift = check_campaign_treatment_access(
        catalog="mip",
        principal=golden.APP,
        mode=mode,
        workspace=spy,  # type: ignore[arg-type]
    )
    _assert_read_only(calls, plane)
    for line in drift or []:
        assert not any(identifier in line for identifier in _IDENTIFIERS), line
    return drift, calls


def _disagreements(mode: str, start: golden.Store, **kwargs: Any) -> list[str]:
    """Where the check and a real convergence run from ``start`` disagree.

    The check is empty exactly when convergence issues no statement, never
    predicts a refusal for a run that succeeds, and is empty after convergence.
    Without group-conveyed grants it predicts every refusal too. A group grant
    of a privilege the App also holds directly is invisible to a name-level
    read; the check still reports drift there and convergence's exact
    postflight refuses.
    """

    label = f"mode={mode} {kwargs} start={ {k[0]: sorted(v.get(golden.APP, ())) for k, v in start.items()} }"
    drift = _check(mode, golden._copy(start), **kwargs)[0] or []
    outcome = golden._run(
        converger.converge_campaign_treatment_access,
        mode=mode,
        store=golden._copy(start),
        **kwargs,
    )
    problems: list[str] = []
    if (drift == []) is not (outcome.ok and outcome.statements == ()):
        problems.append(f"{label}: drift={drift} ok={outcome.ok} {outcome.statements}")
    predicts_refusal = any("refuses" in line for line in drift)
    if predicts_refusal and outcome.ok:
        problems.append(f"{label}: drift={drift} predicts a refusal but convergence succeeded")
    if not predicts_refusal and not outcome.ok and not kwargs.get("inherited"):
        problems.append(f"{label}: drift={drift} missed a refusal ({outcome.error})")
    if outcome.ok:
        store = {
            key: {name: set(held) for name, held in grants.items()}
            for key, grants in outcome.store.items()
        }
        after = _check(mode, store, **kwargs)[0]
        if after:
            problems.append(f"{label}: drift after convergence {after}")
    return problems


@pytest.mark.parametrize("mode", ["quiesce", "runtime"])
def test_converged_boundary_reports_no_drift(mode: str) -> None:
    reviewed = {key: golden._reviewed(key, mode) for key in golden.SECURABLES}

    drift, calls = _check(mode, golden._start_store(reviewed, golden.FOREIGN))

    assert drift == []
    assert "grants.get" in calls and "grants.get_effective" in calls


@pytest.mark.parametrize(
    ("mode", "app", "kwargs", "expected"),
    [
        (
            "runtime",
            {},
            {},
            [
                "catalog: grant USE_CATALOG",
                "audit schema: grant USE_SCHEMA",
                "treatment table: grant MODIFY, SELECT",
            ],
        ),
        (
            "quiesce",
            {CATALOG: {"USE_CATALOG"}, SCHEMA: {"USE_SCHEMA"}, TABLE: {"SELECT", "MODIFY"}},
            {},
            ["treatment table: revoke MODIFY"],
        ),
        (
            "runtime",
            {CATALOG: {"USE_CATALOG", "BROWSE"}, SCHEMA: {"USE_SCHEMA"}, TABLE: {"SELECT"}},
            {},
            ["catalog: revoke BROWSE", "treatment table: grant MODIFY"],
        ),
        (
            "quiesce",
            {CATALOG: {"USE_CATALOG"}, SCHEMA: {"USE_SCHEMA"}, TABLE: {"SELECT", "MANAGE"}},
            {},
            [
                "treatment table: direct MANAGE is outside ALL PRIVILEGES "
                "(convergence leaves it and refuses)"
            ],
        ),
        (
            "quiesce",
            {CATALOG: {"USE_CATALOG"}, SCHEMA: {"USE_SCHEMA"}, TABLE: {"SELECT"}},
            {"inherited": {SCHEMA: {"SELECT"}}},
            ["audit schema: inherited SELECT (convergence cannot remove it and refuses)"],
        ),
        (
            "quiesce",
            {CATALOG: {"USE_CATALOG"}, SCHEMA: {"USE_SCHEMA"}, TABLE: {"ALL_PRIVILEGES"}},
            {},
            [
                "treatment table: revoke ALL_PRIVILEGES first (it also drops the reviewed "
                "privileges it implies, which are re-granted)",
                "treatment table: grant SELECT",
            ],
        ),
        (
            "runtime",
            {CATALOG: {"USE_CATALOG"}, SCHEMA: {"USE_SCHEMA"}},
            {"table_exists": False},
            ["treatment table: absent (runtime convergence refuses)"],
        ),
        (
            "quiesce",
            {CATALOG: {"USE_CATALOG"}, SCHEMA: {"USE_SCHEMA"}},
            {"table_exists": False},
            [],
        ),
        (
            "quiesce",
            {CATALOG: {"USE_CATALOG"}, SCHEMA: {"USE_SCHEMA"}, TABLE: {"SELECT"}},
            {"ignore_principal_filter": True},
            [],
        ),
        # Unity Catalog applies schema MODIFY to the table; convergence removes it
        # with the schema revoke, so the table line must not claim a refusal.
        (
            "quiesce",
            {CATALOG: {"USE_CATALOG"}, SCHEMA: {"USE_SCHEMA", "MODIFY"}, TABLE: {"SELECT"}},
            {},
            ["audit schema: revoke MODIFY"],
        ),
        (
            "runtime",
            {CATALOG: {"USE_CATALOG", "ALL_PRIVILEGES"}, SCHEMA: {"USE_SCHEMA"}, TABLE: {"SELECT"}},
            {},
            [
                "catalog: revoke ALL_PRIVILEGES first (it also drops the reviewed "
                "privileges it implies, which are re-granted)",
                "catalog: grant USE_CATALOG",
                "treatment table: grant MODIFY",
            ],
        ),
        # A parent grant convergence keeps cannot explain a privilege below it.
        (
            "quiesce",
            {CATALOG: {"USE_CATALOG", "MANAGE"}, SCHEMA: {"USE_SCHEMA"}, TABLE: {"SELECT"}},
            {},
            [
                "catalog: direct MANAGE is outside ALL PRIVILEGES "
                "(convergence leaves it and refuses)",
                "audit schema: inherited MANAGE (convergence cannot remove it and refuses)",
                "treatment table: inherited MANAGE (convergence cannot remove it and refuses)",
            ],
        ),
    ],
)
def test_drift_is_reported_symbolically(
    mode: str,
    app: dict[golden.Key, set[str]],
    kwargs: dict[str, Any],
    expected: list[str],
) -> None:
    drift, _ = _check(mode, golden._start_store(app, golden.FOREIGN), **kwargs)

    assert drift == expected


@pytest.mark.parametrize("mode", ["quiesce", "runtime"])
@pytest.mark.parametrize(
    "universe", [golden.UNIVERSE, golden.WRITE_UNIVERSE], ids=["base", "write"]
)
def test_check_agrees_with_convergence_on_every_full_presence_state(
    mode: str, universe: dict[golden.Key, tuple[str, ...]]
) -> None:
    disagreements: list[str] = []
    for catalog, schema, table in itertools.product(
        golden._subsets(universe[CATALOG]),
        golden._subsets(universe[SCHEMA]),
        golden._subsets(universe[TABLE]),
    ):
        start = golden._start_store(
            {CATALOG: catalog, SCHEMA: schema, TABLE: table}, golden.FOREIGN
        )
        disagreements.extend(_disagreements(mode, start))
    assert disagreements == []


@pytest.mark.parametrize("mode", ["quiesce", "runtime"])
def test_check_agrees_with_convergence_on_partial_presence_and_group_grants(mode: str) -> None:
    universe = golden.WRITE_UNIVERSE
    disagreements: list[str] = []
    for catalog, schema in itertools.product(
        golden._subsets(universe[CATALOG]), golden._subsets(universe[SCHEMA])
    ):
        start = golden._start_store({CATALOG: catalog, SCHEMA: schema}, golden.FOREIGN)
        disagreements.extend(_disagreements(mode, start, table_exists=False))
    for catalog in golden._subsets(universe[CATALOG]):
        start = golden._start_store({CATALOG: catalog}, golden.FOREIGN)
        disagreements.extend(_disagreements(mode, start, schema_exists=False, table_exists=False))
    for table in golden._subsets(universe[TABLE]):
        for inherited in ({TABLE: {"MODIFY"}}, {SCHEMA: {"SELECT"}}, {CATALOG: {"BROWSE"}}):
            start = golden._start_store(
                {CATALOG: {"USE_CATALOG"}, SCHEMA: {"USE_SCHEMA"}, TABLE: table}, golden.FOREIGN
            )
            disagreements.extend(_disagreements(mode, start, inherited=inherited))
    assert disagreements == []


@pytest.mark.parametrize("mode", ["quiesce", "runtime"])
def test_absent_catalog_is_not_drift_because_convergence_returns_without_a_statement(
    mode: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    start = golden._start_store({}, golden.FOREIGN)

    drift, _ = _check(mode, golden._copy(start), catalog_exists=False)
    outcome = golden._run(
        converger.converge_campaign_treatment_access,
        mode=mode,
        store=golden._copy(start),
        catalog_exists=False,
    )
    spy, calls, plane = _workspace(golden._copy(start), catalog_exists=False)
    monkeypatch.setattr(check_module, "deployment_workspace_client", lambda: spy)

    assert drift is None
    assert outcome.ok and outcome.result is False and outcome.statements == ()
    assert check_module.run_check(catalog="mip", principal=golden.APP, mode=mode) == 0
    assert "governed catalog is absent, nothing to converge" in capsys.readouterr().out
    _assert_read_only(calls, plane)


def test_run_check_exits_nonzero_on_drift_and_prints_no_identifiers(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    spy, calls, plane = _workspace(golden._start_store({}, golden.FOREIGN))
    monkeypatch.setattr(check_module, "deployment_workspace_client", lambda: spy)

    assert check_module.run_check(catalog="mip", principal=golden.APP, mode="quiesce") == 1

    output = capsys.readouterr().out
    _assert_read_only(calls, plane)
    assert "[mip-treatment-access] drift mode=quiesce: catalog: grant USE_CATALOG" in output
    assert "owner policy is not evaluated read-only" in output
    body = output.replace("[mip-treatment-access]", "")
    assert not any(identifier in body for identifier in _IDENTIFIERS), output


def test_cli_check_needs_no_warehouse_and_converge_still_requires_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    reviewed = {key: golden._reviewed(key, "runtime") for key in golden.SECURABLES}
    spy, calls, plane = _workspace(golden._start_store(reviewed, golden.FOREIGN))
    monkeypatch.setattr(check_module, "deployment_workspace_client", lambda: spy)

    assert converger.main(["--check", "--principal", golden.APP, "--mode", "runtime"]) == 0
    assert "check passed mode=runtime" in capsys.readouterr().out
    _assert_read_only(calls, plane)
    with pytest.raises(SystemExit) as excinfo:
        converger.main(["--principal", golden.APP, "--mode", "runtime"])
    assert excinfo.value.code == 2
    assert "--warehouse-id is required unless --check" in capsys.readouterr().err
