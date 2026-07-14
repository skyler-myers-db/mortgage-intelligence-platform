"""Contracts for the governed live-smoke approval flow."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SMOKE_SCRIPT = REPO / "scripts" / "smoke_live.sh"


def test_smoke_live_shell_is_syntactically_valid() -> None:
    result = subprocess.run(
        ["bash", "-n", str(SMOKE_SCRIPT)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_smoke_approves_persisted_email_draft_with_complete_proof() -> None:
    text = SMOKE_SCRIPT.read_text(encoding="utf-8")

    assert text.index('"$API_PREFIX/outreach/draft"') < text.index('"$API_PREFIX/outreach/approve"')
    assert "SMOKE_EVIDENCE_IDS=" in text
    assert '--argjson evidence_ids "$SMOKE_EVIDENCE_IDS"' in text
    for proof_field in (
        "draft_subject",
        "draft_generation_id",
        "draft_response_hash",
        "draft_source_refreshed_at",
    ):
        assert f"--arg {proof_field} " in text
        assert f"{proof_field}:${proof_field}" in text
    assert ".draft_generation_id == $generation_id" in text
