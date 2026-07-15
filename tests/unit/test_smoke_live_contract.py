"""Contracts for the governed live-smoke approval flow."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SMOKE_SCRIPT = REPO / "scripts" / "smoke_live.sh"


def _logical_shell_lines(text: str) -> list[str]:
    logical: list[str] = []
    pending = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            pending = f"{pending} {line[:-1].strip()}".strip()
            continue
        logical.append(f"{pending} {line}".strip())
        pending = ""
    if pending:
        logical.append(pending)
    return logical


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


def test_smoke_centralizes_connect_and_total_timeouts_for_every_curl_request() -> None:
    text = SMOKE_SCRIPT.read_text(encoding="utf-8")
    command_lines = [
        line.strip()
        for line in text.splitlines()
        if re.search(r"(^|\$\(|\b(?:command|if|until|while)\s+)curl\s", line.strip())
        and "command -v curl" not in line
    ]

    assert 'CURL_CONNECT_TIMEOUT="${MIP_SMOKE_CONNECT_TIMEOUT_S:-10}"' in text
    assert 'CURL_MAX_TIME="${MIP_SMOKE_REQUEST_TIMEOUT_S:-75}"' in text
    assert command_lines == ["command curl \\"]
    assert '--connect-timeout "$CURL_CONNECT_TIMEOUT"' in text
    assert '--max-time "$max_time"' in text
    assert 'curl_bounded() {' in text
    assert 'curl_with_timeout "$CURL_MAX_TIME" "$@"' in text


def test_smoke_transport_failures_enter_the_bounded_retry_loop() -> None:
    text = SMOKE_SCRIPT.read_text(encoding="utf-8")

    assert 'PROBE_ATTEMPTS="${MIP_SMOKE_PROBE_ATTEMPTS:-4}"' in text
    assert 'PROBE_RETRY_DELAY="${MIP_SMOKE_PROBE_RETRY_DELAY_S:-20}"' in text
    assert 'PROBE_RETRY_BUDGET="${MIP_SMOKE_PROBE_RETRY_BUDGET_S:-300}"' in text
    assert 'if REQUEST_HTTP_CODE="$(curl_with_timeout "$attempt_timeout"' in text
    assert 'REQUEST_CURL_RC=$?' in text
    assert 'if (( REQUEST_CURL_RC != 0 )); then' in text
    assert 'curl transport failure rc=$REQUEST_CURL_RC' in text
    assert 'retry_deadline=$((SECONDS + PROBE_RETRY_BUDGET))' in text
    assert 'retry_remaining=$((retry_deadline - SECONDS))' in text
    assert 'if (( attempt_timeout > retry_remaining )); then' in text
    assert 'if (( retry_sleep > retry_remaining )); then' in text
    assert 'attempt <= max_attempts' in text


def test_smoke_retry_policy_excludes_generic_mutations() -> None:
    text = SMOKE_SCRIPT.read_text(encoding="utf-8")
    probe_calls = [line for line in _logical_shell_lines(text) if line.startswith('probe "')]
    post_calls = [line for line in probe_calls if " POST " in line]

    assert 'never|safe_read|idempotent_mutation' in text
    assert '"$method" == "GET" || "$method" == "HEAD"' in text
    assert '"$retry_policy" == "safe_read" || "$retry_policy" == "idempotent_mutation"' in text
    assert 'retry_transient' not in text

    portfolio = next(call for call in post_calls if '"portfolio preview"' in call)
    approval = next(call for call in post_calls if '"outreach approval audit write"' in call)
    draft = next(call for call in post_calls if '"outreach draft for approval"' in call)
    genie = next(call for call in post_calls if '"genie message"' in call)
    assert portfolio.endswith("POST '{}' safe_read")
    assert approval.endswith('idempotent_mutation "$SMOKE_REQUEST_ID"')
    assert "safe_read" not in draft and "idempotent_mutation" not in draft
    assert "safe_read" not in genie and "idempotent_mutation" not in genie
    assert all(
        "safe_read" not in call and "idempotent_mutation" not in call
        for call in post_calls
        if call not in {portfolio, approval}
    )


def test_smoke_approval_retries_with_one_stable_idempotency_key() -> None:
    text = SMOKE_SCRIPT.read_text(encoding="utf-8")

    request_id_assignment = 'SMOKE_REQUEST_ID="$(new_request_id)"'
    assert text.count(request_id_assignment) == 1
    assert text.index(request_id_assignment) < text.index('"$API_PREFIX/outreach/approve"')
    assert '--arg request_id "$SMOKE_REQUEST_ID"' in text
    assert 'request_id:$request_id' in text
    assert "'.request_id == $key'" in text
    assert 'request_args+=(-H "Idempotency-Key: $idempotency_key")' in text
    assert '"$SMOKE_APPROVE_PAYLOAD" idempotent_mutation "$SMOKE_REQUEST_ID"' in text
