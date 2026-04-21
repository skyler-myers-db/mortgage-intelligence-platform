"""Live Databricks Genie space integration test -- gated on creds.

Skipped unless ``DATABRICKS_HOST`` / ``DATABRICKS_TOKEN`` are set AND a
Genie space id is resolvable (``GENIE_SPACE_ID`` env var or the repo-
committed ``genie/space_id.txt``). When both are present, this test
issues a real question against the space and asserts a non-empty
natural-language answer.

Cold-start latency: the first conversation against an idle Genie space
can take 5-15 seconds. The timeout is generous on purpose.

Non-negotiable: this test never mutates state. It only issues a
conversation turn; conversations auto-expire on the workspace side.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.services.genie_client import (
    GenieClient,
    GenieClientError,
    GenieResponse,
)

_REPO_SPACE_ID_FILE = Path(__file__).resolve().parents[2] / "genie" / "space_id.txt"


def _resolve_creds() -> tuple[str, str, str] | None:
    host = os.environ.get("DATABRICKS_HOST")
    token = os.environ.get("DATABRICKS_TOKEN")
    space_id = os.environ.get("GENIE_SPACE_ID")
    if not space_id and _REPO_SPACE_ID_FILE.exists():
        space_id = _REPO_SPACE_ID_FILE.read_text(encoding="utf-8").strip()
    if host and token and space_id:
        return host, token, space_id
    return None


def test_live_genie_space_answers_canonical_question() -> None:
    creds = _resolve_creds()
    if creds is None:
        pytest.skip(
            "DATABRICKS_HOST / DATABRICKS_TOKEN / GENIE_SPACE_ID not set and "
            "genie/space_id.txt not readable -- live Genie test skipped."
        )
    host, token, space_id = creds
    client = GenieClient(host=host, token=token, space_id=space_id, timeout_s=60)

    try:
        result: GenieResponse = client.ask(
            "How many borrowers are currently in the money?"
        )
    except GenieClientError as exc:  # pragma: no cover -- live-creds gated
        pytest.fail(f"Live Genie call failed: {exc}")

    assert result.source == "genie"
    assert result.conversation_id
    assert result.message_id
    assert result.answer_text, "live Genie returned an empty answer"


def test_live_genie_ping() -> None:
    creds = _resolve_creds()
    if creds is None:
        pytest.skip("live Genie ping skipped -- no creds / space id")
    host, token, space_id = creds
    client = GenieClient(host=host, token=token, space_id=space_id, timeout_s=5)
    assert client.ping() is True
