"""A refused plan's skeleton keeps its structure and drops every value (wave 7f).

The skeleton goes to the private report beside the parser's message, so an
operator can extend the parser from a document that never leaves the runner.
"""

from __future__ import annotations

import json

from tests.fixtures import bundle_plan_fixtures as fx
from tools.databricks.bundle_plan_skeleton import MAX_DEPTH, SCHEMA, skeleton

VALUES = ("zyrplax-name", "zyrplax-path", 31337, 2.5)


def test_keys_and_types_stay_and_no_value_does() -> None:
    doc = fx.document(
        {
            "resources.jobs.mip_ref_seed": fx.entry(
                "update",
                value={"name": VALUES[0], "max_concurrent_runs": VALUES[2], "ratio": VALUES[3], "queue": None},
                refs={"tasks[0].sql_task.warehouse_id": "${resources.sql_warehouses.mip_serverless_sql.id}"},
                changes={"name": {"action": "update", "old": VALUES[1], "new": VALUES[0]}},
                remote={"paused": True},
            )
        }
    )
    text = fx.dumps(doc)

    result = skeleton(text)

    rendered = json.dumps(result)
    assert not [value for value in VALUES if str(value) in rendered]
    assert "${resources" not in rendered, "a reference string is a value too"
    assert result["schema"] == SCHEMA and result["json"] is True
    assert result["bytes"] == len(text.encode()) and result["lines"] == text.count("\n")
    entry = result["shape"]["plan"]["resources.jobs.mip_ref_seed"]
    assert entry["new_state"]["value"] == {"name": "str", "max_concurrent_runs": "int", "ratio": "float", "queue": "null"}
    assert entry["new_state"]["vars"] == {"tasks[0].sql_task.warehouse_id": "str"}
    assert entry["changes"]["name"] == {"action": "str", "old": "str", "new": "str"}
    assert entry["remote_state"] == {"paused": "bool"}
    assert result["shape"]["plan_version"] == "int"


def test_lists_fold_their_items_into_one_shape_or_a_union() -> None:
    text = json.dumps({"same": [{"a": 1}, {"a": 2}], "mixed": [{"a": 1}, {"b": "x"}, 3], "empty": []})

    shape = skeleton(text)["shape"]

    assert shape["same"] == {"__list__": 2, "items": {"a": "int"}}
    assert shape["mixed"] == {"__list__": 3, "items": {"__union__": [{"a": "int"}, {"b": "str"}, "int"]}}
    assert shape["empty"] == {"__list__": 0, "items": None}


def test_a_duplicate_key_and_a_number_constant_are_kept_as_shapes() -> None:
    shape = skeleton('{"plan_version": 2, "plan_version": NaN, "serial": Infinity}')["shape"]

    assert shape == {
        "plan_version": "number_constant",
        "serial": "number_constant",
        "__duplicate_keys__": ["plan_version"],
    }


def test_a_document_that_is_not_json_keeps_only_its_size() -> None:
    text = 'Warning: zyrplax-name\n{"plan_version": 2}\n'

    assert skeleton(text) == {"schema": SCHEMA, "json": False, "bytes": len(text), "lines": 2}
    assert skeleton("")["json"] is False


def test_nesting_past_the_bound_is_cut_not_raised() -> None:
    deep: object = "leaf"
    for _ in range(MAX_DEPTH + 5):
        deep = {"k": deep}

    shape = skeleton(json.dumps(deep))["shape"]

    for _ in range(MAX_DEPTH + 1):
        assert isinstance(shape, dict)
        shape = shape["k"]
    assert shape == "nested"
    assert skeleton("[" * 100_000 + "]" * 100_000)["json"] is False, "a recursion error is a non-JSON document"
