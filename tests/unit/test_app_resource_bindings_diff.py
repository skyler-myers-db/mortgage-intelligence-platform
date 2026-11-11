"""Bindings-diff fixtures: the read-only diff names changes without printing values.

Spike S2: the bindings compare as a set keyed by name (an order-only
difference is equal), and the description is read apart
(``description_differs``): a create-update masked to description is the
release's online fix-up, never a window. A binding's own description is
display text (``BINDING_DISPLAY_FIELDS``): the diff and the verifier compare
the access contract, and ``binding_description_drift`` names the bindings
whose text differs (client #1's live App has three undescribed secrets).
"""

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
    binding_description_drift,
    binding_diff,
    build_resource_binding_payload,
    description_differs,
)
from tools.databricks.check_exit_codes import CHECK_DRIFT, CHECK_EXACT

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
        # S2: an order-only difference is equal, and the description is no binding.
        (lambda live: live["resources"].reverse(), []),
        (lambda live: live.update(description="Edited in the UI"), []),
        (lambda live: live.update(description=""), []),
        # A binding's own description is display text, never a binding diff.
        (lambda live: _resource(live, "database").update(description="Edited in the UI"), []),
        (lambda live: _resource(live, "sql_warehouse").update(description=""), []),
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


def test_cli_diff_reads_only_its_two_files_and_exits_with_the_drift_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    expected_path, live_path = tmp_path / "expected.json", tmp_path / "live.json"
    expected_path.write_text(json.dumps(_payload()), encoding="utf-8")
    live = _live(_payload())
    _resource(live, "database")["database"]["permission"] = "CAN_CONNECT"
    live_path.write_text(json.dumps(live), encoding="utf-8")
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in tmp_path.iterdir()}

    status = bindings.main(["diff", "--expected", str(expected_path), "--live", str(live_path)])

    assert status == CHECK_DRIFT
    assert capsys.readouterr().out == (
        "[mip-app-bindings] diff: binding database: database.permission differ "
        "(update replaces them)\n"
    )
    after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in tmp_path.iterdir()}
    assert after == before
    live_path.write_text(json.dumps(_live(_payload())), encoding="utf-8")
    assert (
        bindings.main(["diff", "--expected", str(expected_path), "--live", str(live_path)])
        == CHECK_EXACT
    )


_FIELD_VALUES = st.sampled_from(("a", "b", 1, None))
_BINDING = st.fixed_dictionaries(
    {"name": st.sampled_from(("sql_warehouse", "database", "job", "secret"))},
    optional={
        "description": st.sampled_from(("", "Governed warehouse.", "edited")),
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

    equal = binding_diff(expected=expected, live=live) == [] and not description_differs(
        expected=expected, live=live
    )
    assert equal is verifier_passes


def test_a_reordered_binding_set_is_equal_to_the_diff_and_to_the_verifier() -> None:
    live = _live(_payload())
    live["resources"] = list(reversed(live["resources"]))

    assert binding_diff(expected=_payload(), live=live) == []
    _assert_exact_transition(
        expected=_payload(), after=live, before=None, require_stopped_without_deployment=False
    )


def test_the_description_is_read_apart_from_the_bindings() -> None:
    payload = _payload()
    assert payload["description"], "the bundle declares the App's description"
    live = _live(payload)

    assert description_differs(expected=payload, live=live) is False
    live["description"] = ""  # what a PATCH without one leaves (S2)
    assert description_differs(expected=payload, live=live) is True
    assert binding_diff(expected=payload, live=live) == []
    with pytest.raises(ValueError, match="description"):
        _assert_exact_transition(
            expected=payload, after=live, before=None, require_stopped_without_deployment=False
        )


def test_cli_diff_reports_a_description_only_difference_and_exits_exact(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    expected_path, live_path = tmp_path / "expected.json", tmp_path / "live.json"
    expected_path.write_text(json.dumps(_payload()), encoding="utf-8")
    live = _live(_payload())
    live.update(description="")
    live["resources"].reverse()
    live_path.write_text(json.dumps(live), encoding="utf-8")

    status = bindings.main(["diff", "--expected", str(expected_path), "--live", str(live_path)])

    assert status == CHECK_EXACT, "a description and an order are no window"
    out = capsys.readouterr().out
    assert "equal the bundle payload (as a set)" in out
    assert "description: differs (an online fix-up" in out


def test_a_bundle_with_no_description_owes_no_fix_up_and_never_reads_as_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Round 2 of #337: the plan and the fix-up agree; the verifier stays exact."""

    payload = _payload()
    payload.pop("description")
    live = _live(payload)
    live["description"] = "Set by an earlier send"

    assert bindings.description_fixup_owed(expected=payload, live=live) is False
    assert bindings.description_fixup_owed(expected=_payload(), live=live) is True
    assert description_differs(expected=payload, live=live) is True, "the verifier still compares"
    expected_path, live_path = tmp_path / "expected.json", tmp_path / "live.json"
    expected_path.write_text(json.dumps(payload), encoding="utf-8")
    live_path.write_text(json.dumps(live), encoding="utf-8")

    status = bindings.main(["diff", "--expected", str(expected_path), "--live", str(live_path)])

    out = capsys.readouterr().out
    assert status == CHECK_EXACT and "an online fix-up" not in out
    assert "the bundle declares none; the live one is left as it is" in out
    assert "Set by an earlier send" not in out


def _described() -> dict[str, Any]:
    """The fixture payload with a description on every binding, as databricks.yml declares them."""

    payload = _payload()
    for item in payload["resources"]:
        item["description"] = f"Reviewed {item['name']} binding."
    return payload


def _undescribed(live: dict[str, Any], *names: str) -> dict[str, Any]:
    for name in names:
        _resource(live, name).pop("description")
    return live


def test_a_binding_description_is_reported_apart_by_name_only() -> None:
    payload = _described()
    live = _undescribed(_live(payload), "database", "migration_job")
    _resource(live, "sql_warehouse")["description"] = "Edited in the UI"

    assert binding_diff(expected=payload, live=live) == []
    assert binding_description_drift(expected=payload, live=live) == [
        "database",
        "migration_job",
        "sql_warehouse",
    ]
    assert binding_description_drift(expected=payload, live=_live(payload)) == []


def test_a_binding_description_absent_live_is_no_drift_for_a_missing_binding() -> None:
    live = _live(_described())
    live["resources"].remove(_resource(live, "database"))

    assert binding_description_drift(expected=_described(), live=live) == []
    assert binding_diff(expected=_described(), live=live) == ["binding database: missing (update adds it)"]


def test_the_verifier_accepts_a_send_whose_binding_text_differs_and_says_so(
    capsys: pytest.CaptureFixture[str],
) -> None:
    after = _undescribed(_live(_described()), "database")

    _assert_exact_transition(
        expected=_described(), after=after, before=None, require_stopped_without_deployment=False
    )
    err = capsys.readouterr().err
    assert "binding descriptions differ (display text, never a window" in err and err.rstrip().endswith(
        ": database"
    )
    assert not any(value in err for value in _VALUES)


def test_the_verifier_still_refuses_an_access_difference_beside_a_text_one() -> None:
    after = _undescribed(_live(_described()), "database")
    _resource(after, "database")["database"]["permission"] = "CAN_CONNECT"

    with pytest.raises(ValueError, match="do not exactly match the bundle"):
        _assert_exact_transition(
            expected=_described(), after=after, before=None, require_stopped_without_deployment=False
        )


def test_cli_diff_names_undescribed_bindings_and_exits_exact(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    expected_path, live_path = tmp_path / "expected.json", tmp_path / "live.json"
    expected_path.write_text(json.dumps(_described()), encoding="utf-8")
    live_path.write_text(json.dumps(_undescribed(_live(_described()), "database")), encoding="utf-8")

    status = bindings.main(["diff", "--expected", str(expected_path), "--live", str(live_path)])

    assert status == CHECK_EXACT, "a binding's own description is no window"
    out = capsys.readouterr().out
    assert "equal the bundle payload (as a set)" in out
    assert "binding descriptions differ (display text, never a window; a resources send carries " in out
    assert out.rstrip().endswith("the bundle's): database")
