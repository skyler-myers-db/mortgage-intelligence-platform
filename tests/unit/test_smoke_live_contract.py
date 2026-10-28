"""Contracts for the governed live-smoke approval flow."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

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
    # review_mode is required on approve since W5c (D-approval-flow-a1).
    assert 'review_mode:"individual"' in text


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


def test_smoke_proves_the_edge_forwards_the_actor_binding_header() -> None:
    """D-identity-review-a4: right after health, a stale key must answer 409
    actor_changed (a 200 means the edge dropped the header or the binding is
    missing) and the session's own key 200; a deployed app whose session
    carries no key fails the smoke. Status codes only, never the key."""
    text = SMOKE_SCRIPT.read_text(encoding="utf-8")
    lines = _logical_shell_lines(text)
    health_ok = text.index("[smoke] health ok")
    stale = text.index("-H 'X-MIP-Actor-Key: actor_0000000000000000'")
    own = text.index('-H "X-MIP-Actor-Key: $SESSION_ACTOR_KEY"')

    assert health_ok < stale < own < text.index("# --- Five canonical API calls")
    probes = [line for line in lines if "X-MIP-Actor-Key" in line and line.startswith("ACTOR_PROBE_CODE=")]
    assert len(probes) == 2
    assert all('"$APP_URL$API_PREFIX/config/options"' in line for line in probes)
    assert all("curl_bounded -sS" in line and "-w '%{http_code}'" in line for line in probes)
    assert 'if [[ "$ACTOR_PROBE_CODE" != "409" ]]' in text
    assert '!= "actor_changed" ]]' in text
    assert 'if [[ "$ACTOR_PROBE_CODE" != "200" ]]' in text
    assert "jq -r '.actor_cache_key // empty'" in text
    deployed_null = text.index('elif [[ "$BOOT_LOCAL" == "0" ]]; then', own)
    assert "exit 1" in text[deployed_null : text.index("else", deployed_null)]
    assert not any("SESSION_ACTOR_KEY" in line and line.startswith("echo") for line in lines)


# ---------------------------------------------------------------------------
# The live geo drill (post-W5 live-gate-geo-drill). The map drills state ->
# ZIP (PR #182); the county grain is retired, so the county endpoint is a
# shape contract whose empty answer passes by design. These tests EXECUTE the
# script's own geo block with a stubbed `probe`, so each jq check is proven to
# refuse its bad shape, not just to be present.
# ---------------------------------------------------------------------------

GEO_BEGIN = "# --- Geo drill: state -> ZIP (begin)"
GEO_END = "# --- Geo drill: state -> ZIP (end)"
SMOKE_OUT = "/tmp/mip-smoke-out.json"


def _require_jq() -> None:
    if shutil.which("jq") is not None:
        return
    if os.environ.get("CI"):
        pytest.fail("jq is required to execute the smoke geo drill (smoke_live.sh requires it too)")
    pytest.skip("jq is not installed; smoke_live.sh requires it")


def _geo_block() -> str:
    text = SMOKE_SCRIPT.read_text(encoding="utf-8")
    assert text.count(GEO_BEGIN) == 1 and text.count(GEO_END) == 1
    block = text[text.index(GEO_BEGIN) : text.index(GEO_END)]
    assert SMOKE_OUT in block
    return block


def _smoke_states(**overrides: object) -> dict[str, Any]:
    # The smoke cohort's live shape (itm + equity, all) with CA first: the
    # filtered state SQL has no ORDER BY, so CA was `.[0]` on 2026-10-26.
    rows: list[dict[str, Any]] = [
        {"state": "CA", "addressable": 3455, "zip_unassigned_count": 0},
        {"state": "FL", "addressable": 7441, "zip_unassigned_count": 0},
        {"state": "IL", "addressable": 21428, "zip_unassigned_count": 2},
        {"state": "WA", "addressable": 2880, "zip_unassigned_count": 14},
    ]
    payload: dict[str, Any] = {"rollups": rows, "snapshot_date": None}
    payload.update(overrides)
    return payload


def _smoke_zip_row(zip_code: object, borrowers: object, state: object = "IL") -> dict[str, Any]:
    return {"zip": zip_code, "state": state, "county_fips_5": None, "addressable_borrowers": borrowers}


def _smoke_zips(**overrides: object) -> dict[str, Any]:
    # 21,428 IL borrowers, 2 without a 5-digit ZIP: the ZIP layer sums to 21,426.
    payload: dict[str, Any] = {
        "state": "IL",
        "fips_5": None,
        "rollups": [
            _smoke_zip_row("60628", 21000),
            _smoke_zip_row("60617", 400),
            _smoke_zip_row("60601", 26),
        ],
        "snapshot_date": None,
    }
    payload.update(overrides)
    return payload


def _smoke_counties(**overrides: object) -> dict[str, Any]:
    payload: dict[str, Any] = {"state": "IL", "rollups": [], "snapshot_date": None, "scope_note": None}
    payload.update(overrides)
    return payload


def _run_geo_block(
    tmp_path: Path,
    *,
    states: dict[str, Any] | None = None,
    zips: dict[str, Any] | None = None,
    counties: dict[str, Any] | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    _require_jq()
    files: dict[str, Path] = {}
    for name, payload in (
        ("state", states or _smoke_states()),
        ("zip", zips or _smoke_zips()),
        ("county", counties or _smoke_counties()),
    ):
        files[name] = tmp_path / f"{name}.json"
        files[name].write_text(json.dumps(payload), encoding="utf-8")
    out = tmp_path / "out.json"
    calls = tmp_path / "calls.log"
    calls.touch()
    q = shlex.quote
    harness = "\n".join(
        [
            "set -euo pipefail",
            "API_PREFIX=/api/v1",
            "probe() {",
            f'  printf "%s\\n" "$2" >> {q(str(calls))}',
            '  case "$2" in',
            f'    *"/geo/state-rollups?"*) cp {q(str(files["state"]))} {q(str(out))} ;;',
            f'    *"/geo/zip-rollups?"*) cp {q(str(files["zip"]))} {q(str(out))} ;;',
            f'    *"/geo/county-rollups?"*) cp {q(str(files["county"]))} {q(str(out))} ;;',
            '    *) echo "unexpected probe $2" >&2; exit 97 ;;',
            "  esac",
            '  echo "[smoke] ok · $1"',
            "}",
            _geo_block().replace(SMOKE_OUT, str(out)),
            "echo GEO_DRILL_PASSED",
        ]
    )
    result = subprocess.run(
        ["bash", "-c", harness], text=True, capture_output=True, check=False, timeout=60
    )
    return result, calls.read_text(encoding="utf-8").splitlines()


def test_smoke_geo_drill_passes_a_reconciling_drill_with_the_empty_county_answer(tmp_path: Path) -> None:
    result, calls = _run_geo_block(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "GEO_DRILL_PASSED" in result.stdout
    assert (
        "[smoke] ok · geo drill IL -> ZIP reconciles (21428 addressable, 2 without a 5-digit ZIP)"
        in result.stdout
    )
    # The largest populated state, not CA (the first row), and no county key.
    assert calls == [
        "/api/v1/geo/state-rollups?segment_codes=itm,equity&segment_mode=all",
        "/api/v1/geo/zip-rollups?state=IL&segment_codes=itm,equity&segment_mode=all",
        "/api/v1/geo/county-rollups?state=IL&segment_codes=itm,equity&segment_mode=all",
    ]


def test_smoke_geo_drill_breaks_a_size_tie_by_state_code(tmp_path: Path) -> None:
    states = _smoke_states(
        rollups=[
            {"state": "TX", "addressable": 21428, "zip_unassigned_count": 0},
            {"state": "IL", "addressable": 21428, "zip_unassigned_count": 2},
        ]
    )

    result, calls = _run_geo_block(tmp_path, states=states)

    assert result.returncode == 0, result.stderr
    assert "zip-rollups?state=IL&" in calls[1]


def test_smoke_geo_drill_accepts_a_county_row_with_a_five_digit_fips(tmp_path: Path) -> None:
    counties = _smoke_counties(rollups=[{"fips_5": "17031", "state": "IL", "addressable_borrowers": 21428}])

    result, _ = _run_geo_block(tmp_path, counties=counties)

    assert result.returncode == 0, result.stderr


_ZIP_LAYER_REFUSED = "geo zip rollups for state=IL are not a populated, in-state, 5-digit-ZIP layer"
_COUNTY_REFUSED = "geo county rollups broke the retired-grain contract for state=IL"
_STATE_ROW_REFUSED = "geo state rollups did not carry exactly one row with whole addressable and zip_unassigned_count for state=IL"
_STATE_SHAPE_REFUSED = "geo state rollups carried a row without a 2-letter state and a whole addressable count"
_IL_ROW = {"state": "IL", "addressable": 21428, "zip_unassigned_count": 2}


def _unreconciled(total: int) -> str:
    return (
        f"geo drill does not reconcile for state=IL: sum(zip.addressable_borrowers)={total}, "
        "expected state.addressable 21428 - state.zip_unassigned_count 2"
    )


# Every bad shape the smoke geo block refuses. The ids are the shared bad-shape
# vocabulary: tests/unit/test_verify_live.py refuses the same list (pinned there).
SMOKE_GEO_REFUSALS = [
    # The ZIP layer: populated, in-state, 5-digit ZIPs, whole counts.
    pytest.param({"zips": _smoke_zips(rollups=[])}, _ZIP_LAYER_REFUSED, id="empty-zip-list"),
    pytest.param({"zips": _smoke_zips(rollups=None)}, _ZIP_LAYER_REFUSED, id="zip-rollups-not-an-array"),
    pytest.param(
        {"zips": _smoke_zips(rollups=[_smoke_zip_row("60628", 21000), _smoke_zip_row("60617", 426), "60601"])},
        _ZIP_LAYER_REFUSED,
        id="non-object-zip-row",
    ),
    pytest.param({"zips": _smoke_zips(rollups=[_smoke_zip_row("6062", 21426)])}, _ZIP_LAYER_REFUSED, id="four-digit-zip"),
    pytest.param(
        {"zips": _smoke_zips(rollups=[_smoke_zip_row("6062A", 21426)])}, _ZIP_LAYER_REFUSED, id="non-numeric-zip"
    ),
    pytest.param(
        {"zips": _smoke_zips(rollups=[_smoke_zip_row(60628, 21426)])}, _ZIP_LAYER_REFUSED, id="numeric-zip-not-a-string"
    ),
    pytest.param(
        {"zips": _smoke_zips(rollups=[_smoke_zip_row("60628\n", 21426)])},
        _ZIP_LAYER_REFUSED,
        id="zip-with-trailing-newline",
    ),
    pytest.param(
        {"zips": _smoke_zips(rollups=[_smoke_zip_row("60628", 21426), _smoke_zip_row("75201", 0, "TX")])},
        _ZIP_LAYER_REFUSED,
        id="zip-row-from-another-state",
    ),
    pytest.param({"zips": _smoke_zips(state="TX")}, _ZIP_LAYER_REFUSED, id="zip-envelope-other-state"),
    pytest.param({"zips": _smoke_zips(rollups=[_smoke_zip_row("60628", 0)])}, _ZIP_LAYER_REFUSED, id="no-populated-zip"),
    # Each count case sums to the reconciling 21,426, so only the
    # whole-count conjuncts can refuse it.
    pytest.param(
        {"zips": _smoke_zips(rollups=[_smoke_zip_row("60628", 21000), _smoke_zip_row("60617", "426")])},
        _ZIP_LAYER_REFUSED,
        id="string-zip-count",
    ),
    pytest.param(
        {
            "zips": _smoke_zips(
                rollups=[
                    _smoke_zip_row("60628", 21000),
                    _smoke_zip_row("60617", 427),
                    _smoke_zip_row("60601", -1),
                ]
            )
        },
        _ZIP_LAYER_REFUSED,
        id="negative-zip-count",
    ),
    pytest.param(
        {
            "zips": _smoke_zips(
                rollups=[
                    _smoke_zip_row("60628", 21000),
                    _smoke_zip_row("60617", 425.5),
                    _smoke_zip_row("60601", 0.5),
                ]
            )
        },
        _ZIP_LAYER_REFUSED,
        id="fractional-zip-count",
    ),
    pytest.param(
        {
            "zips": _smoke_zips(
                rollups=[
                    _smoke_zip_row("60628", 21000),
                    _smoke_zip_row("60617", 425),
                    _smoke_zip_row("60601", True),
                ]
            )
        },
        _ZIP_LAYER_REFUSED,
        id="boolean-zip-count",
    ),
    # The retired county grain: empty passes; any row is in-state with a 5-digit FIPS.
    pytest.param(
        {"counties": _smoke_counties(rollups=[{"fips_5": "", "state": "IL"}])},
        _COUNTY_REFUSED,
        id="county-row-empty-fips",
    ),
    pytest.param(
        {"counties": _smoke_counties(rollups=[{"fips_5": None, "state": "IL"}])},
        _COUNTY_REFUSED,
        id="county-row-null-fips",
    ),
    pytest.param(
        {"counties": _smoke_counties(rollups=[{"fips_5": "1703", "state": "IL"}])},
        _COUNTY_REFUSED,
        id="county-row-four-digit-fips",
    ),
    pytest.param(
        {"counties": _smoke_counties(rollups=[{"fips_5": "1703A", "state": "IL"}])},
        _COUNTY_REFUSED,
        id="county-row-non-numeric-fips",
    ),
    pytest.param(
        {"counties": _smoke_counties(rollups=[{"fips_5": "17031", "state": "WI"}])},
        _COUNTY_REFUSED,
        id="county-row-other-state",
    ),
    pytest.param({"counties": _smoke_counties(state="CA")}, _COUNTY_REFUSED, id="county-envelope-other-state"),
    pytest.param({"counties": _smoke_counties(rollups=None)}, _COUNTY_REFUSED, id="county-rollups-not-an-array"),
    pytest.param({"counties": _smoke_counties(rollups=["17031"])}, _COUNTY_REFUSED, id="non-object-county-row"),
    # The reconciliation: 21,428 - 2 = 21,426 exactly, so it refuses both directions.
    pytest.param(
        {"zips": _smoke_zips(rollups=[_smoke_zip_row("60628", 21000), _smoke_zip_row("60617", 427)])},
        _unreconciled(21427),
        id="broken-invariant-over-sum",
    ),
    pytest.param(
        {"zips": _smoke_zips(rollups=[_smoke_zip_row("60628", 21000), _smoke_zip_row("60617", 400)])},
        _unreconciled(21400),
        id="broken-invariant-under-sum",
    ),
    pytest.param(
        {"states": _smoke_states(rollups=[{"state": "IL", "addressable": 21428}])},
        _STATE_ROW_REFUSED,
        id="state-row-without-zip-unassigned",
    ),
    pytest.param(
        {"states": _smoke_states(rollups=[_IL_ROW, dict(_IL_ROW)])},
        _STATE_ROW_REFUSED,
        id="duplicate-target-state-rows",
    ),
    # The drill target: every state row has a 2-letter state and a whole count.
    pytest.param(
        {"states": _smoke_states(rollups=[{"state": "IL", "addressable": 0, "zip_unassigned_count": 0}])},
        "geo state rollups did not include a populated state",
        id="no-populated-state",
    ),
    pytest.param({"states": _smoke_states(rollups=[])}, "geo state rollups returned no filtered rows", id="no-state-rows"),
    pytest.param(
        {"states": _smoke_states(rollups=None)},
        "geo state rollups returned no filtered rows",
        id="state-rollups-not-an-array",
    ),
    pytest.param(
        {"states": _smoke_states(rollups=[{"state": "il", "addressable": 21428, "zip_unassigned_count": 2}])},
        _STATE_SHAPE_REFUSED,
        id="lowercase-state",
    ),
    pytest.param(
        {"states": _smoke_states(rollups=[{"state": "IL", "addressable": "21428", "zip_unassigned_count": 2}])},
        _STATE_SHAPE_REFUSED,
        id="string-addressable",
    ),
    # A malformed row that is not the target still refuses the whole payload.
    pytest.param(
        {"states": _smoke_states(rollups=[{"state": "CA", "addressable": -1, "zip_unassigned_count": 0}, _IL_ROW])},
        _STATE_SHAPE_REFUSED,
        id="negative-state-addressable",
    ),
    pytest.param(
        {"states": _smoke_states(rollups=[{"state": "CA", "addressable": 3455.5, "zip_unassigned_count": 0}, _IL_ROW])},
        _STATE_SHAPE_REFUSED,
        id="fractional-state-addressable",
    ),
]
SMOKE_GEO_REFUSAL_IDS = tuple(str(case.id) for case in SMOKE_GEO_REFUSALS)


@pytest.mark.parametrize(("payloads", "refusal"), SMOKE_GEO_REFUSALS)
def test_smoke_geo_drill_refuses_each_bad_shape(
    tmp_path: Path, payloads: dict[str, dict[str, Any]], refusal: str
) -> None:
    result, _ = _run_geo_block(tmp_path, **payloads)

    assert result.returncode == 1, (result.returncode, result.stderr)
    assert f"[smoke] {refusal}" in result.stderr, result.stderr
    assert "GEO_DRILL_PASSED" not in result.stdout
    # Every refusal dumps the payload it refused, so an operator can read it.
    assert (tmp_path / "out.json").read_text(encoding="utf-8") in result.stderr


def test_smoke_geo_drill_captures_the_state_counts_before_the_zip_probe() -> None:
    block = _geo_block()
    zip_probe = block.index('probe "geo zip rollups"')

    assert block.index("SMOKE_STATE_ADDRESSABLE=") < zip_probe
    assert block.index("SMOKE_STATE_ZIP_UNASSIGNED=") < zip_probe
    assert '"$API_PREFIX/geo/zip-rollups?state=$SMOKE_STATE&' in block
    assert zip_probe < block.index('probe "geo county rollups (retired grain)"')
    # The retired county rung is gone from the whole script, not just moved.
    text = SMOKE_SCRIPT.read_text(encoding="utf-8")
    assert "county_fips=" not in text
    assert "SMOKE_FIPS" not in text


def test_the_runbook_map_drill_playbook_is_state_to_zip_and_never_runs_the_stale_gold_job() -> None:
    runbook = (REPO / "docs" / "runbook.md").read_text(encoding="utf-8")
    start = runbook.index("### R4-08 Map won't drill from state to ZIP")
    end = runbook.find("\n### ", start + 1)
    section = " ".join(runbook[start : end if end != -1 else None].split())

    assert "Map won't drill from state to county" not in runbook, "the retired county drill playbook is gone"
    for retired in ("Loading counties", "us-counties.json", "county-rollups?state=IL"):
        assert retired not in section, retired
    assert "`/api/v1/geo/zip-rollups` is 503, or `mip.gold.zip_rollup` is empty or stale" in section
    assert "`sum(zip.addressable_borrowers)` differs from `state.addressable - state.zip_unassigned_count`" in section
    assert '"Interim refresh (governed deploy NO-GO)" procedure' in section
    assert (
        "**Never `databricks bundle run` or `run-now` the deployed `mip_refresh_scores`, and never press Admin → "
        "Rebuild scoring snapshot, on an install whose deployed jobs predate the current bundle:**"
    ) in section
    assert "bundle run mip_refresh_scores" not in section, "no fix tells an operator to run the deployed gold job"
    # docs/testing.md: only verify_live probes the Lead Queue deep link.
    testing = " ".join((REPO / "docs" / "testing.md").read_text(encoding="utf-8").split())
    assert "`verify_live` alone also probes the Lead Queue deep link, by `state` + `zip`, never `county`;" in testing
    assert "`smoke_live.sh` has no Lead Queue geo probe." in testing
