"""The bundle plan parser accepts the reviewed CLI v1.10.0 shape and nothing else.

The fixtures mirror ``bundle/deployplan/plan.go`` and
``libs/structs/structvar/structvar.go`` at the v1.10.0 tag
(tests/fixtures/bundle_plan_fixtures.py). Any field, action, type or key the
parser does not know must refuse, because the gate would otherwise judge a
plan whose meaning it cannot know.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from tests.fixtures import bundle_plan_fixtures as fx
from tools.databricks.bundle_plan_shape import (
    ACTIONS,
    PLAN_VERSION,
    PlanShapeError,
    parse_plan,
)

JOB = "resources.jobs.mip_ref_seed"


def _job_doc(body: dict[str, Any]) -> dict[str, Any]:
    return fx.document({JOB: body})


def test_the_reviewed_vocabulary_is_the_cli_v1_10_0_vocabulary() -> None:
    # bundle/deployplan/action.go and plan.go at v1.10.0.
    assert {"skip", "resize", "update", "update_id", "create", "recreate", "delete"} == ACTIONS
    assert PLAN_VERSION == 2


def test_a_full_entry_parses_into_kind_key_and_resolved_state() -> None:
    body = fx.entry(
        "update",
        value={"name": "synthetic-mip_ref_seed"},
        refs={
            "tasks[0].sql_task.warehouse_id": "${resources.sql_warehouses.mip_serverless_sql.id}"
        },
        changes={
            "name": {"action": "update", "reason": "custom", "old": "a", "new": "b", "remote": "a"}
        },
        depends_on=[
            {
                "node": "resources.sql_warehouses.mip_serverless_sql",
                "label": "${resources.sql_warehouses.mip_serverless_sql.id}",
            }
        ],
        remote={"job_id": 1},
    )
    body["id"] = "1"

    plan = parse_plan(fx.dumps(_job_doc(body)))

    (entry,) = plan.entries
    assert (entry.kind, entry.name, entry.symbolic, entry.action) == (
        "jobs",
        "mip_ref_seed",
        "jobs.mip_ref_seed",
        "update",
    )
    assert entry.new_value == {"name": "synthetic-mip_ref_seed"}
    assert entry.changes["name"].old == "a" and entry.changes["name"].reason == "custom"
    assert (plan.plan_version, plan.cli_version, plan.lineage, plan.serial) == (
        2,
        "1.10.0",
        fx.LINEAGE,
        7,
    )


def test_an_empty_state_plan_omits_lineage_and_serial() -> None:
    plan = parse_plan(fx.dumps(fx.first_install()))

    assert plan.lineage == "" and plan.serial == 0
    assert [entry.key for entry in plan.entries] == sorted(entry.key for entry in plan.entries)
    assert {entry.action for entry in plan.entries} == {"create"}


def test_a_child_node_keeps_its_kind_suffix() -> None:
    plan = parse_plan(fx.dumps(fx.document({f"{JOB}.permissions": fx.entry("skip")})))

    assert plan.entries[0].kind == "jobs.permissions"
    assert plan.entries[0].symbolic == "jobs.mip_ref_seed.permissions"


def test_the_digest_ignores_formatting_but_not_content() -> None:
    doc = fx.adopted()
    compact = json.dumps(doc, separators=(",", ":"))
    shuffled = json.dumps(dict(reversed(list(doc.items()))), indent=4)

    assert (
        parse_plan(compact).sha256
        == parse_plan(shuffled).sha256
        == parse_plan(fx.dumps(doc)).sha256
    )
    changed = copy.deepcopy(doc)
    changed["serial"] = 8
    assert parse_plan(fx.dumps(changed)).sha256 != parse_plan(compact).sha256


def _refuses(text: str, fragment: str) -> None:
    with pytest.raises(PlanShapeError, match=fragment):
        parse_plan(text)


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    (
        (lambda d: d.update(engine="direct"), "unknown field"),
        (lambda d: d.update(plan_version=1), "plan_version 1"),
        (lambda d: d.update(plan_version=3), "plan_version 3"),
        (lambda d: d.pop("plan_version"), "plan_version 0"),
        (lambda d: d.update(plan_version=True), "plan_version is not an integer"),
        (lambda d: d.pop("plan"), "no plan map"),
        (lambda d: d.update(plan=[]), "plan is not an object"),
        (lambda d: d.update(serial=-1), "serial is negative"),
        (lambda d: d.update(serial="7"), "serial is not an integer"),
        (lambda d: d.update(cli_version=10), "cli_version is not a string"),
        (lambda d: d.update(lineage=None), "lineage is not a string"),
    ),
)
def test_an_unknown_or_mistyped_top_level_refuses(mutate: Any, fragment: str) -> None:
    doc = fx.adopted()
    mutate(doc)
    _refuses(json.dumps(doc), fragment)


@pytest.mark.parametrize(
    ("body", "fragment"),
    (
        ({"action": "skip", "drift": True}, "unknown field"),
        ({}, "has no action"),
        ({"action": ""}, "not a known action"),
        ({"action": "replace"}, "not a known action"),
        ({"action": "skip", "gone": "yes"}, "gone is not a boolean"),
        ({"action": "skip", "id": 1}, "id is not a string"),
        ({"action": "skip", "depends_on": {}}, "depends_on is not a list"),
        ({"action": "skip", "depends_on": [{"node": "x", "weight": 1}]}, "unknown field"),
        ({"action": "skip", "depends_on": [{"label": "x"}]}, "node is not a string"),
        ({"action": "create", "new_state": {"value": {}, "refs": {}}}, "unknown field"),
        ({"action": "create", "new_state": {"vars": {}}}, "has no value"),
        ({"action": "create", "new_state": {"value": []}}, "value is not an object"),
        (
            {"action": "create", "new_state": {"value": {}, "vars": {"name": 1}}},
            "vars is not a string",
        ),
        ({"action": "update", "changes": []}, "changes is not an object"),
        ({"action": "update", "changes": {"name": {"old": 1}}}, "has no action"),
        ({"action": "update", "changes": {"name": {"action": "move"}}}, "not a known action"),
        (
            {"action": "update", "changes": {"name": {"action": "update", "why": "x"}}},
            "unknown field",
        ),
        (
            {"action": "update", "changes": {"name": {"action": "update", "reason": 3}}},
            "reason is not a string",
        ),
    ),
)
def test_an_unknown_or_mistyped_entry_refuses(body: dict[str, Any], fragment: str) -> None:
    _refuses(json.dumps(_job_doc(body)), fragment)


@pytest.mark.parametrize(
    "key",
    (
        "jobs.mip_ref_seed",
        "resources.jobs",
        "resources.Jobs.mip_ref_seed",
        "resources.jobs.mip_ref_seed.acls",
        "resources.jobs.mip_ref_seed.permissions.extra",
        "resources.jobs.mip.ref.seed",
        "resources.jobs.mip ref",
        "resources.jobs.-leading",
    ),
)
def test_a_key_outside_the_resource_grammar_refuses(key: str) -> None:
    _refuses(json.dumps(fx.document({key: {"action": "skip"}})), "plan\\[")


def test_a_duplicate_field_refuses() -> None:
    text = '{"plan_version": 2, "plan_version": 2, "plan": {}}'
    _refuses(text, "duplicate field 'plan_version'")


@pytest.mark.parametrize("text", ("", "{", "[]", '"plan"', '{"plan_version": NaN, "plan": {}}'))
def test_a_non_object_or_non_json_document_refuses(text: str) -> None:
    with pytest.raises(PlanShapeError):
        parse_plan(text)


def test_shape_errors_name_fields_and_keys_never_values() -> None:
    secret_like = "do-not-print-this-value"
    body = {
        "action": "update",
        "changes": {"name": {"action": "update", "old": secret_like, "why": secret_like}},
    }
    with pytest.raises(PlanShapeError) as raised:
        parse_plan(json.dumps(_job_doc(body)))
    assert secret_like not in str(raised.value)
