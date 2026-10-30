"""The Lakebase instance acknowledgement digests what the update will actually do.

The Databricks CLI's instance ``DoUpdate`` (v1.10.0,
``bundle/direct/dresources/database_instance.go``) sends the whole
``new_state.value`` with ``UpdateMask: "*"``, and the planner classifies a
remote-only difference on an input-only field (``node_count``,
``retention_window_in_days``, ... ``spec:input_only`` in
``resources.generated.yml``) as ``skip``. So a reviewed capacity change also
reverts any such drift. The digest therefore binds the full new state, every
change skip included, the instance name and the state lineage: a drift the
reviewer never saw, or another install's identical diff, is a different
digest and refuses.
"""

from __future__ import annotations

import copy
from typing import Any

from tests.fixtures import bundle_plan_fixtures as fx
from tools.databricks import bundle_plan_gate as gate
from tools.databricks.bundle_plan_shape import parse_plan, sha256_hex

INSTANCE = "resources.database_instances.mip_app_state"


def _plan(body: dict[str, Any], *, lineage: str = fx.LINEAGE) -> dict[str, Any]:
    doc = fx.with_entries(fx.adopted(), {INSTANCE: body})
    doc["lineage"] = lineage
    return doc


def _reviewed() -> dict[str, Any]:
    """A capacity change whose configured state also pins secondaries and PITR."""

    body = fx.instance_update("CU_2")
    body["new_state"]["value"].update(node_count=1, retention_window_in_days=7)
    return body


def _drifted() -> dict[str, Any]:
    """The reviewed change, after secondaries and PITR were raised out of band."""

    body = _reviewed()
    body["changes"]["node_count"] = {
        "action": "skip",
        "reason": "spec:input_only",
        "old": 1,
        "new": 1,
        "remote": 3,
    }
    body["changes"]["retention_window_in_days"] = {
        "action": "skip",
        "reason": "spec:input_only",
        "old": 7,
        "new": 7,
        "remote": 35,
    }
    return body


def _evaluate(doc: dict[str, Any], ack: str, position: str = "pre-apply") -> gate.PlanReport:
    return gate.evaluate(
        parse_plan(fx.dumps(doc)),
        position=position,
        app_key=fx.APP_KEY,
        acknowledgement=ack,
        workspace_factory=lambda: fx.RecordingWorkspace(),
    )


def _digest(doc: dict[str, Any]) -> str:
    return gate.instance_update_digest(parse_plan(fx.dumps(doc)))


def test_a_skipped_input_only_drift_changes_the_digest_and_refuses_the_reviewed_ack() -> None:
    reviewed = _digest(_plan(_reviewed()))
    drifted = _plan(_drifted())

    assert _digest(drifted) != reviewed
    report = _evaluate(drifted, reviewed)
    assert report.codes == ("instance_update_unacked",)
    assert report.refusals[0].detail == {"digest": _digest(drifted), "acknowledged": True}
    changes = report.instance_update[0]["changes"]
    assert changes["node_count"] == {
        "action": "skip",
        "reason": "spec:input_only",
        "old": 1,
        "new": 1,
        "remote": 3,
    }, "the full report shows the drift the update reverts"
    assert changes["retention_window_in_days"]["remote"] == 35
    # Positive control: the reviewed plan itself passes with its own digest.
    assert _evaluate(_plan(_reviewed()), reviewed).codes == ()


def test_any_change_to_the_body_the_update_sends_changes_the_digest() -> None:
    base = _reviewed()
    other = copy.deepcopy(base)
    other["new_state"]["value"]["enable_readable_secondaries"] = False

    assert _digest(_plan(base)) != _digest(_plan(other))


def test_another_install_with_the_identical_diff_digests_differently() -> None:
    here = _plan(_reviewed())
    elsewhere = _plan(_reviewed(), lineage="11111111-1111-4111-8111-111111111111")
    renamed = copy.deepcopy(_reviewed())
    renamed["new_state"]["value"]["name"] = "synthetic-other_app_state"

    digests = {_digest(here), _digest(elsewhere), _digest(_plan(renamed))}
    assert len(digests) == 3
    assert _evaluate(elsewhere, _digest(here)).codes == ("instance_update_unacked",)


def test_the_full_report_shows_exactly_what_is_digested() -> None:
    doc = _plan(_drifted())

    report = _evaluate(doc, "")
    full = report.full()

    assert sha256_hex(full["instance_update"]) == report.instance_update_sha256
    item = full["instance_update"][0]
    assert set(item) == {
        "resource",
        "action",
        "instance_name",
        "lineage",
        "new_state",
        "new_vars",
        "changes",
    }
    assert item["new_state"]["node_count"] == 1 and item["lineage"] == fx.LINEAGE
    assert set(item["changes"]) == {
        "capacity",
        "effective_capacity",
        "node_count",
        "retention_window_in_days",
    }


def test_an_instance_update_without_its_new_state_refuses_even_when_acknowledged() -> None:
    body = _reviewed()
    del body["new_state"]
    doc = _plan(body)

    report = _evaluate(doc, _digest(doc))

    assert report.codes == ("malformed_plan",)
    assert report.refusals[0].resource == "database_instances.mip_app_state"
    assert report.refusals[0].detail["reason"] == (
        "database_instances.mip_app_state update carries no new_state"
    )
