"""Approve bodies after review_mode became REQUIRED (W5c, D-approval-flow-a1).

``POST /api/outreach/approve`` refuses a body with no ``review_mode`` (422
"Reload the app to approve", nothing written), and the schema has always
required the generated draft proof beside any declared mode. A test approve is
therefore built one of three ways:

* ``reviewed_approval`` -- the real flow: ``POST /outreach/draft``, then the
  body carries that draft's copy, offer and proof with ``review_mode``
  ``individual``. Use it wherever the approval must succeed.
* ``synthetic_review_proof`` -- a syntactically valid proof (a fresh UUID, a
  64-hex hash, a timestamp) with ``review_mode``, for a request the route
  refuses BEFORE it verifies the draft (unknown borrower, eligibility,
  replay, request link): it still reaches that refusal.
* ``skip_draft_verification`` -- for the defence-in-depth screens that run
  AFTER the draft verification (disclosure, names, protected-class copy):
  the verifier answers the local no-proof outcome, so the test's custom copy
  reaches the screen it pins. The screens themselves are untouched.

Lane-local to w5-lead-queue-paging (other W5c lanes send ``review_mode`` and
the draft proof inline).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import pytest

REVIEWED_MODE = "individual"


def draft_proof(draft: Mapping[str, Any]) -> dict[str, Any]:
    """The approve fields a generated draft proves, with ``review_mode``."""

    return {
        "offer_code": draft["offer_code"],
        "draft_subject": draft.get("subject"),
        "draft_body": draft["body"],
        "draft_generation_id": draft["generation_id"],
        "draft_response_hash": draft["response_hash"],
        "draft_source_refreshed_at": draft["source_refreshed_at"],
        "review_mode": REVIEWED_MODE,
    }


def reviewed_approval(
    client: Any,
    borrower_id: str,
    *,
    channel: str = "email",
    headers: Mapping[str, str] | None = None,
    draft_extra: Mapping[str, Any] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """Draft through the API, then the approve body certifying exactly that draft."""

    response = client.post(
        "/api/outreach/draft",
        json={"borrower_id": borrower_id, "channel": channel, **(draft_extra or {})},
        headers=dict(headers or {}),
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = {
        "borrower_id": borrower_id,
        "channel": channel,
        **{key: value for key, value in (draft_extra or {}).items()},
        **draft_proof(response.json()),
    }
    body.update(overrides)
    return body


def synthetic_review_proof(**overrides: Any) -> dict[str, Any]:
    """A well-formed proof and ``review_mode`` for a request refused before verification."""

    body: dict[str, Any] = {
        "draft_generation_id": str(uuid4()),
        "draft_response_hash": "a" * 64,
        "draft_source_refreshed_at": "2026-09-29T06:00:00Z",
        "review_mode": REVIEWED_MODE,
    }
    body.update(overrides)
    return body


def skip_draft_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let a synthetic proof through the draft verifier (the local no-proof outcome)."""

    from backend.api import outreach as outreach_mod
    from backend.services.outreach_drafts import VerifiedGeneratedDraft

    monkeypatch.setattr(
        outreach_mod,
        "_verified_generated_draft",
        lambda *args, **kwargs: VerifiedGeneratedDraft(None, False, None),
    )
