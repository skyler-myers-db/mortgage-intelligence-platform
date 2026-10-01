"""Pins for the 'select all N matching' / server batch ruling (audit tables-07,
tables-02; D-approval-flow-a3; report section 12.4 #9).

Ruling (2026-09-30): there is no "select all N matching" control and no server
batch decision endpoint. Every approve and reject names exactly one borrower,
carries that borrower's own governed draft proof (approve) and writes that
borrower's own audit row; a bulk run is N such requests from the browser under
one bulk_id. Approving or rejecting "everything matching these filters" would
decide borrowers no human saw and bypass the per-borrower draft proof, against
the "human approval always required" posture. A whole cohort is worked as a
campaign instead (the Lead Queue's "Build a campaign from these filters"
handoff to Portfolio Builder).

These pins fail if a decision request grows a cohort shape:

1. The /outreach operation set in the committed OpenAPI baseline is exactly
   POST draft, approve and reject, plus the separately ruled maker-checker
   request operations (12.4 #10: POST and GET approval-requests and POST
   approval-requests/{batch_id}/withdraw), under /api/outreach and
   /api/v1/outreach. A request never decides a borrower; its batch id is
   optional single-valued context on a per-borrower approve or reject.
2. OutreachApproveRequest and OutreachRejectRequest each have a required
   ``borrower_id: str``, and ``evidence_ids`` is their only list field.
3. No field name of either contains borrower_ids, filter, fingerprint, cohort,
   matching or criteria.
"""

from __future__ import annotations

import json
import typing
from pathlib import Path

import pytest

from backend.schemas.offer import OutreachApproveRequest, OutreachRejectRequest

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "tests" / "fixtures" / "openapi_baseline.json"
DECISION_MODELS = (OutreachApproveRequest, OutreachRejectRequest)
COHORT_FIELD_FRAGMENTS = ("borrower_ids", "filter", "fingerprint", "cohort", "matching", "criteria")


def test_the_outreach_operations_are_exactly_the_ruled_set() -> None:
    paths = json.loads(BASELINE.read_text(encoding="utf-8"))["paths"]
    operations = {
        (method.upper(), path)
        for path, item in paths.items()
        if "/outreach" in path
        for method in item
    }
    expected = {
        (method, f"{prefix}/outreach/{verb}")
        for prefix in ("/api", "/api/v1")
        for method, verb in (
            ("POST", "draft"),
            ("POST", "approve"),
            ("POST", "reject"),
            ("POST", "approval-requests"),
            ("GET", "approval-requests"),
            ("POST", "approval-requests/{batch_id}/withdraw"),
        )
    }
    assert operations == expected


@pytest.mark.parametrize("model", DECISION_MODELS, ids=lambda model: model.__name__)
def test_a_decision_names_exactly_one_borrower(model: type) -> None:
    field = model.model_fields["borrower_id"]
    assert field.is_required()
    assert field.annotation is str
    list_fields = sorted(
        name
        for name, info in model.model_fields.items()
        if typing.get_origin(info.annotation) is list or info.annotation is list
    )
    assert list_fields == ["evidence_ids"]


@pytest.mark.parametrize("model", DECISION_MODELS, ids=lambda model: model.__name__)
def test_no_decision_field_has_a_cohort_shape(model: type) -> None:
    offending = sorted(
        name
        for name in model.model_fields
        if any(fragment in name for fragment in COHORT_FIELD_FRAGMENTS)
    )
    assert offending == []
