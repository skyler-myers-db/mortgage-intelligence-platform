"""No treatment-access run opens a write path the revoke-all converger would not.

Unity Catalog applies MODIFY granted on a catalog or a schema to every table
below it, so the App can write the treatment table through USE CATALOG, USE
SCHEMA and MODIFY held on any level (``golden._write_path``). Over every App
start state in ``golden.WRITE_UNIVERSE`` and every object-presence shape:

- quiesce never opens a write path, and runtime opens one only at the table
  grant issued after the parent boundary proof (``golden._compare`` checks
  both convergers on every run, so the rule is today's behaviour);
- a run whose k-th statement fails, for every k, never ends with a write path
  its start did not hold, and a failed runtime run that keeps one says so.
"""

from __future__ import annotations

import itertools

import pytest

from tests.unit import test_campaign_treatment_access_diff_convergence as golden
from tools.databricks import converge_campaign_treatment_access as converger

APP = golden.APP
CATALOG, SCHEMA, TABLE = golden.CATALOG, golden.SCHEMA, golden.TABLE
UNIVERSE = golden.WRITE_UNIVERSE
_RELATION = "`mip`.`audit`.`campaign_treatment_snapshot`"
_COMPENSATION_UNPROVEN = "compensating write quiescence could not be proven"


def _full_presence_starts() -> list[golden.Store]:
    return [
        golden._start_store({CATALOG: catalog, SCHEMA: schema, TABLE: table}, golden.FOREIGN)
        for catalog, schema, table in itertools.product(
            golden._subsets(UNIVERSE[CATALOG]),
            golden._subsets(UNIVERSE[SCHEMA]),
            golden._subsets(UNIVERSE[TABLE]),
        )
    ]


@pytest.mark.parametrize("mode", ["quiesce", "runtime"])
def test_no_run_opens_a_write_path_before_the_parent_proof(mode: str) -> None:
    problems: list[str] = []
    for start in _full_presence_starts():
        problems.extend(golden._compare(mode=mode, start=start, present=golden.SECURABLES)[0])
    for catalog, schema in itertools.product(
        golden._subsets(UNIVERSE[CATALOG]), golden._subsets(UNIVERSE[SCHEMA])
    ):
        start = golden._start_store({CATALOG: catalog, SCHEMA: schema}, golden.FOREIGN)
        problems.extend(
            golden._compare(mode=mode, start=start, present=(CATALOG, SCHEMA), table_exists=False)[
                0
            ]
        )
    assert problems == []


@pytest.mark.parametrize("mode", ["quiesce", "runtime"])
def test_a_failed_statement_never_leaves_a_write_path_the_start_lacked(mode: str) -> None:
    problems: list[str] = []
    injected = 0
    for start in _full_presence_starts():
        clean = golden._run(
            converger.converge_campaign_treatment_access, mode=mode, store=golden._copy(start)
        )
        for fail_at in range(len(clean.statements)):
            outcome = golden._run(
                converger.converge_campaign_treatment_access,
                mode=mode,
                store=golden._copy(start),
                fail_at=fail_at,
            )
            injected += 1
            label = f"mode={mode} fail_at={fail_at} start={golden._app_state(start)}"
            if outcome.ok:
                problems.append(f"{label}: an injected failure did not surface")
            problems.extend(golden._write_path_problems(label, mode, start, outcome))
            final = {key: golden._app_final(outcome, key) for key in golden.SECURABLES}
            if (
                mode == "runtime"
                and golden._write_path(final)
                and _COMPENSATION_UNPROVEN not in outcome.message
            ):
                problems.append(f"{label}: runtime kept a write path without saying so")
    assert problems == []
    assert injected > 1000


def test_quiesce_strips_parent_modify_before_any_parent_grant() -> None:
    start = golden._start_store(
        {CATALOG: set(), SCHEMA: {"MODIFY"}, TABLE: {"SELECT"}}, golden.FOREIGN
    )

    outcome = golden._run(converger.converge_campaign_treatment_access, mode="quiesce", store=start)

    assert outcome.ok
    assert outcome.statements == (
        "REVOKE MODIFY ON SCHEMA `mip`.`audit` FROM `app-client`",
        "GRANT USE CATALOG ON CATALOG `mip` TO `app-client`",
        "GRANT USE SCHEMA ON SCHEMA `mip`.`audit` TO `app-client`",
    )
    assert not any(golden._write_path(snapshot) for snapshot in outcome.snapshots)


def test_runtime_holds_table_modify_back_until_the_parent_boundary_is_proven() -> None:
    start = golden._start_store(
        {CATALOG: set(), SCHEMA: {"USE_SCHEMA"}, TABLE: {"SELECT", "MODIFY"}}, golden.FOREIGN
    )

    outcome = golden._run(converger.converge_campaign_treatment_access, mode="runtime", store=start)

    assert outcome.ok
    assert outcome.statements == (
        f"REVOKE MODIFY ON TABLE {_RELATION} FROM `app-client`",
        "GRANT USE CATALOG ON CATALOG `mip` TO `app-client`",
        f"GRANT MODIFY ON TABLE {_RELATION} TO `app-client`",
    )
    assert outcome.after_parent_proof == (False, False, True)
    assert [golden._write_path(snapshot) for snapshot in outcome.snapshots] == [
        False,
        False,
        True,
    ]


def test_runtime_keeps_table_modify_when_no_parent_grant_is_needed() -> None:
    start = golden._start_store(
        {CATALOG: {"USE_CATALOG"}, SCHEMA: {"USE_SCHEMA", "MODIFY"}, TABLE: {"SELECT", "MODIFY"}},
        golden.FOREIGN,
    )

    outcome = golden._run(converger.converge_campaign_treatment_access, mode="runtime", store=start)

    assert outcome.ok
    assert outcome.statements == ("REVOKE MODIFY ON SCHEMA `mip`.`audit` FROM `app-client`",)
    assert all(golden._write_path(snapshot) for snapshot in outcome.snapshots)
