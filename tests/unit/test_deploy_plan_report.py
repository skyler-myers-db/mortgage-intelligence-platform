"""PlanReport splits what a public log may carry from what stays with the operator.

``public_summary()`` carries codes, counts, digests and symbolic ``<kind>.<key>``
names only, and refuses to render anything else; ``full()`` adds the resolved
names and lookups. Built from the gate's own reports over the sanitized plan
fixtures, so the redaction is tested on real refusal details.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.fixtures import bundle_plan_fixtures as fx
from tools.databricks import bundle_plan_gate as gate
from tools.databricks.bundle_plan_shape import parse_plan
from tools.databricks.deploy_plan_report import (
    PUBLIC_SCHEMA,
    PlanReport,
    Refusal,
    UnsafePublicValue,
)


def _report(doc: dict[str, Any], workspace: fx.RecordingWorkspace) -> PlanReport:
    return gate.evaluate(
        parse_plan(fx.dumps(doc)),
        position="pre-lease",
        app_key=fx.APP_KEY,
        acknowledgement="",
        workspace_factory=lambda: workspace,
    )


def _resolved_values() -> list[str]:
    values = [name for kind, key in fx.declared_resources() for name in fx.names_of(kind, key)]
    return [*values, fx.LINEAGE, "deployer@example.invalid", "CU_1", "CU_2"]


def test_the_not_adopted_public_summary_carries_no_resolved_name() -> None:
    report = _report(fx.first_install(), fx.RecordingWorkspace(fx.existing_install()))

    public = json.dumps(report.public_summary())
    full = json.dumps(report.full())

    assert report.public_summary()["schema"] == PUBLIC_SCHEMA
    for value in _resolved_values()[:-2]:
        assert value not in public, value
    assert "synthetic-mip_app" in full and "apps.get" in full
    assert report.public_summary()["refusals"][0]["related"]


def test_the_instance_diff_stays_out_of_the_public_summary() -> None:
    doc = fx.with_entries(
        fx.adopted(), {"resources.database_instances.mip_app_state": fx.instance_update()}
    )
    report = _report(doc, fx.RecordingWorkspace())
    public = report.public_summary()

    assert public["instance_update_sha256"] == report.instance_update_sha256
    for value in _resolved_values():
        assert value not in json.dumps(public), value
    assert report.full()["instance_update"][0]["changes"]["capacity"]["new"] == "CU_2"
    assert report.full()["lineage"] == fx.LINEAGE


def test_the_public_summary_has_a_closed_set_of_keys() -> None:
    public = _report(fx.adopted(), fx.RecordingWorkspace()).public_summary()

    assert set(public) == {
        "schema",
        "position",
        "verdict",
        "plan_sha256",
        "plan_version",
        "cli_version",
        "counts",
        "app",
        "instance_update_sha256",
        "refusals",
        "deferred",
    }
    assert public["verdict"] == "passed" and public["app"] == {
        "resource": "apps.mip_app",
        "bound": True,
    }


@pytest.mark.parametrize(
    "report",
    (
        PlanReport(position="pre-lease", app_resource="mip-app", app_bound=False),
        PlanReport(position="live", app_resource="apps.mip_app", app_bound=False),
        PlanReport(
            position="pre-lease", app_resource="apps.mip_app", app_bound=False, plan_sha256="abc"
        ),
        PlanReport(
            position="pre-lease",
            app_resource="apps.mip_app",
            app_bound=False,
            cli_version="1.10.0 (dev x)",
        ),
        PlanReport(
            position="pre-lease",
            app_resource="apps.mip_app",
            app_bound=False,
            refusals=(Refusal(code="collision", resource="[dev someone] mip_job", remedy="r"),),
        ),
        PlanReport(
            position="pre-lease",
            app_resource="apps.mip_app",
            app_bound=False,
            refusals=(
                Refusal(
                    code="not_adopted",
                    resource="apps.mip_app",
                    remedy="r",
                    related=("synthetic name",),
                ),
            ),
        ),
        PlanReport(
            position="pre-lease",
            app_resource="apps.mip_app",
            app_bound=False,
            refusals=(Refusal(code="made_up", resource="apps.mip_app", remedy="r"),),
        ),
    ),
    ids=("resolved-app", "position", "digest", "cli-version", "resource", "related", "code"),
)
def test_a_value_outside_the_public_classes_refuses_to_render(report: PlanReport) -> None:
    with pytest.raises(UnsafePublicValue):
        report.public_summary()
    with pytest.raises(UnsafePublicValue):
        report.full()
