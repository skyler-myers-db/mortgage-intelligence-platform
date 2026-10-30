"""Bindings-diff fixtures: the read-only diff names changes without printing values."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.unit.test_app_resource_bindings import _summary
from tools.databricks import app_resource_bindings as bindings
from tools.databricks.app_resource_bindings import (
    _assert_exact_transition,
    binding_diff,
    build_resource_binding_payload,
)

# Concrete values in the fixture payload. A diff line must never print one.
_VALUES = ("warehouse-id", "mip-state-staging", "12345", "mip_app_state", "CAN_USE", "otel-scope")


def _payload() -> dict[str, Any]:
    return build_resource_binding_payload(_summary(), app_name="mip-app-staging")


def _live(payload: dict[str, Any]) -> dict[str, Any]:
    live = copy.deepcopy(payload)
    live.update(
        id="app-object-id",
        service_principal_client_id="app-client-id",
        compute_status={"state": "ACTIVE"},
        active_deployment={"deployment_id": "deployment-1"},
    )
    return live


def _diff(live: dict[str, Any]) -> list[str]:
    lines = binding_diff(expected=_payload(), live=live)
    for line in lines:
        assert not any(value in line for value in _VALUES), line
    return lines


def test_equal_bindings_have_no_diff() -> None:
    assert _diff(_live(_payload())) == []


def _resource(live: dict[str, Any], name: str) -> dict[str, Any]:
    return next(item for item in live["resources"] if item["name"] == name)


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda live: _resource(live, "database")["database"].update(
                instance_name="mip-state-other"
            ),
            ["binding database: database.instance_name differ (update replaces them)"],
        ),
        (
            lambda live: _resource(live, "sql_warehouse")["sql_warehouse"].update(
                id="warehouse-other", permission="CAN_MANAGE"
            ),
            [
                "binding sql_warehouse: sql_warehouse.id, sql_warehouse.permission differ "
                "(update replaces them)"
            ],
        ),
        (
            lambda live: live["resources"].remove(_resource(live, "migration_job")),
            ["binding migration_job: missing (update adds it)"],
        ),
        (
            lambda live: live["resources"].append(
                {"name": "hand_added", "secret": {"scope": "otel-scope", "key": "k"}}
            ),
            ["1 unreviewed live binding(s) (update removes them)"],
        ),
        (
            lambda live: live["resources"].reverse(),
            ["binding order differs (update reorders them)"],
        ),
        (
            lambda live: live.update(description="Edited in the UI"),
            ["description: differs (update replaces it)"],
        ),
        (
            lambda live: _resource(live, "migration_job").update(
                job={"id": 12345}, serving_endpoint={"name": "x"}
            ),
            [
                "binding migration_job: job.permission, serving_endpoint differ "
                "(update replaces them)"
            ],
        ),
        (
            lambda live: live.pop("resources"),
            [
                "binding sql_warehouse: missing (update adds it)",
                "binding database: missing (update adds it)",
                "binding migration_job: missing (update adds it)",
            ],
        ),
    ],
)
def test_binding_diff_fixtures(mutate: Any, expected: list[str]) -> None:
    live = _live(_payload())
    mutate(live)

    assert _diff(live) == expected


@pytest.mark.parametrize(
    ("live_change", "message"),
    [
        ({"name": "another-app"}, "live App identity"),
        ({"resources": [{"name": "a"}, {"name": "a"}]}, "non-empty and unique"),
        ({"resources": ["database"]}, "not a list of objects"),
    ],
)
def test_malformed_or_foreign_live_app_is_an_error_not_a_diff(
    live_change: dict[str, Any], message: str
) -> None:
    live = _live(_payload())
    live.update(live_change)

    with pytest.raises(ValueError, match=message):
        binding_diff(expected=_payload(), live=live)


def test_source_fields_in_the_expected_payload_are_refused() -> None:
    payload = _payload()
    payload["source_code_path"] = "/Workspace/x"

    with pytest.raises(ValueError, match="forbidden fields"):
        binding_diff(expected=payload, live=_live(_payload()))


def test_cli_diff_reads_only_its_two_files_and_exits_one_on_drift(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    expected_path, live_path = tmp_path / "expected.json", tmp_path / "live.json"
    expected_path.write_text(json.dumps(_payload()), encoding="utf-8")
    live = _live(_payload())
    _resource(live, "database")["database"]["permission"] = "CAN_CONNECT"
    live_path.write_text(json.dumps(live), encoding="utf-8")
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in tmp_path.iterdir()}

    status = bindings.main(["diff", "--expected", str(expected_path), "--live", str(live_path)])

    assert status == 1
    assert capsys.readouterr().out == (
        "[mip-app-bindings] diff: binding database: database.permission differ "
        "(update replaces them)\n"
    )
    after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in tmp_path.iterdir()}
    assert after == before
    live_path.write_text(json.dumps(_live(_payload())), encoding="utf-8")
    assert bindings.main(["diff", "--expected", str(expected_path), "--live", str(live_path)]) == 0


_FIELD_VALUES = st.sampled_from(("a", "b", 1, None))
_BINDING = st.fixed_dictionaries(
    {"name": st.sampled_from(("sql_warehouse", "database", "job", "secret"))},
    optional={
        "database": st.dictionaries(
            st.sampled_from(("instance_name", "permission")), _FIELD_VALUES
        ),
        "job": st.dictionaries(st.sampled_from(("id", "permission")), _FIELD_VALUES),
    },
)
_BINDINGS = st.lists(_BINDING, max_size=4, unique_by=lambda item: item["name"])


@settings(max_examples=400, deadline=None, derandomize=True)
@given(
    expected_bindings=_BINDINGS.filter(bool),
    live_bindings=_BINDINGS,
    expected_description=st.sampled_from(("", "Mortgage Intelligence Platform")),
    live_description=st.sampled_from(("", "Mortgage Intelligence Platform", "edited")),
)
def test_property_diff_is_empty_exactly_when_the_post_update_verifier_passes(
    expected_bindings: list[dict[str, Any]],
    live_bindings: list[dict[str, Any]],
    expected_description: str,
    live_description: str,
) -> None:
    expected = {"name": "mip-app", "resources": expected_bindings}
    if expected_description:
        expected["description"] = expected_description
    live = {"name": "mip-app", "resources": live_bindings, "description": live_description}

    try:
        _assert_exact_transition(
            expected=expected,
            after=live,
            before=None,
            require_stopped_without_deployment=False,
        )
        verifier_passes = True
    except ValueError:
        verifier_passes = False

    assert (binding_diff(expected=expected, live=live) == []) is verifier_passes
