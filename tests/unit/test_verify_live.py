from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.unit.test_smoke_live_contract import SMOKE_GEO_REFUSAL_IDS
from tools import verify_live
from tools.verify_live import (
    APPROVE_DRAFT_UNAVAILABLE,
    ProbeResult,
    collect_red_flags,
    render_markdown,
    run_probes,
)

REPO = Path(__file__).resolve().parents[2]


def _source_row(
    name: str,
    *,
    status: str = "live",
    rows: int | None = 100,
    last_updated: str | None = "2999-01-01 00:00:00",
    checked_at: str | None = "2999-01-01 00:10:00",
    synthetic_demo: bool = False,
) -> dict[str, object]:
    return {
        "name": name,
        "status": status,
        "rows": rows,
        "last_updated": last_updated,
        "checked_at": checked_at,
        "synthetic_demo": synthetic_demo,
    }


def _clean_source_rows() -> list[dict[str, object]]:
    core = [
        "Cotality Public Records",
        "Voluntary Lien",
        "MMA Mortgage Analytics",
        "CLIP",
        "Owner Link",
        "AVM",
        "FRED Market Rates",
        "MLS Listings",
        "Cotality HELOC Propensity",
        "Cotality Refi Propensity",
        "UC Gold Borrower 360",
        "UC Gold Lead Scores",
        "UC Gold Lead Population",
        "UC Gold Segment Population",
        "UC Gold Borrower Dossier",
    ]
    first_party = [
        "First-party LOS / Applications",
        "First-party Servicing Portfolio",
        "First-party CRM / Campaigns",
        "First-party Customer Interactions",
        "First-party Product Balances",
    ]
    return (
        [_source_row(name) for name in core]
        + [_source_row(name, status="demo_synthetic", synthetic_demo=True) for name in first_party]
        + [
            _source_row("Building Permits", status="roadmap", rows=None, last_updated=None),
        ]
    )


def test_verify_live_flags_failed_probe() -> None:
    flags = collect_red_flags(
        [
            ProbeResult(
                name="health",
                method="GET",
                path="/api/health",
                status=503,
                ok=False,
                error="dependency unavailable",
            )
        ]
    )

    assert flags == ["health: status=503 error=dependency unavailable"]


def test_verify_live_filter_sanity_uses_current_probe_names() -> None:
    flags = collect_red_flags(
        [
            ProbeResult(
                name="portfolio.unfiltered",
                method="POST",
                path="/api/portfolio/preview",
                status=200,
                ok=True,
                sample={"marketable_population": 100},
            ),
            ProbeResult(
                name="portfolio.all_states.owner.25pct",
                method="POST",
                path="/api/portfolio/preview",
                status=200,
                ok=True,
                sample={"marketable_population": 100},
            ),
        ]
    )

    assert flags == [
        "portfolio filtered predicate did not narrow results: "
        "unfiltered=100 vs all_states.owner.25pct=100"
    ]


def test_verify_live_passes_clean_current_probe_names() -> None:
    flags = collect_red_flags(
        [
            ProbeResult(
                name="portfolio.unfiltered",
                method="POST",
                path="/api/portfolio/preview",
                status=200,
                ok=True,
                sample={"marketable_population": 100},
            ),
            ProbeResult(
                name="portfolio.all_states.owner.25pct",
                method="POST",
                path="/api/portfolio/preview",
                status=200,
                ok=True,
                sample={"marketable_population": 40},
            ),
        ]
    )

    assert flags == []


def test_verify_live_checks_admin_source_readiness_contract() -> None:
    rows = _clean_source_rows()
    rows[0]["checked_at"] = None
    rows[1]["status"] = "error"
    rows[2]["checked_at"] = "2000-01-01 00:00:00"
    rows[-1]["status"] = "live"

    flags = collect_red_flags(
        [
            ProbeResult(
                name="admin.sources",
                method="GET",
                path="/api/admin/sources",
                status=200,
                ok=True,
                sample=rows,
            )
        ]
    )

    assert "admin.sources: Cotality Public Records missing checked_at" in flags
    assert "admin.sources: Voluntary Lien status=error expected live" in flags
    # The stale flag names the measured age against the threshold (W5e).
    assert any(
        flag.startswith("admin.sources: MMA Mortgage Analytics checked_at is stale (") and flag.endswith(" d > 3 d)")
        for flag in flags
    ), flags
    assert "admin.sources: Building Permits cannot be live until the feed is loaded" in flags


def test_verify_live_accepts_clean_admin_source_readiness_contract() -> None:
    flags = collect_red_flags(
        [
            ProbeResult(
                name="admin.sources",
                method="GET",
                path="/api/admin/sources",
                status=200,
                ok=True,
                sample=_clean_source_rows(),
            )
        ]
    )

    assert flags == []


def test_verify_live_flags_empty_top_level_array() -> None:
    flags = collect_red_flags(
        [
            ProbeResult(
                name="leads.all",
                method="GET",
                path="/api/leads",
                status=200,
                ok=True,
                sample=[],
                top_keys=["[array len=0]"],
            )
        ]
    )

    assert "leads.all: returned empty array" in flags


def test_verify_live_flags_empty_nested_rows() -> None:
    flags = collect_red_flags(
        [
            ProbeResult(
                name="geo.state_rollups",
                method="GET",
                path="/api/geo/state-rollups",
                status=200,
                ok=True,
                sample={"rollups": []},
            )
        ]
    )

    assert "geo.state_rollups: `rollups` returned empty array" in flags


def test_verify_live_flags_missing_borrower_pick() -> None:
    flags = collect_red_flags(
        [
            ProbeResult(
                name="borrower.pick",
                method="INFO",
                path="/api/leads",
                status=0,
                ok=False,
                error="no real borrower_id available from /api/leads",
            )
        ]
    )

    assert flags == [
        "borrower.pick: status=0 error=no real borrower_id available from /api/leads"
    ]


def test_verify_live_does_not_positive_probe_synthetic_outreach_writes() -> None:
    text = (REPO / "tools" / "verify_live.py").read_text(encoding="utf-8")

    assert "outreach.approve.synthetic" not in text
    assert "outreach.reject.synthetic" not in text
    assert "test_uuid_approve" not in text
    assert "test_uuid_reject" not in text
    assert "Synthetic approvals/rejections written" not in text
    assert "outreach.approve.unknown_404" in text
    assert "expect_status=404" in text


# ---------------------------------------------------------------------------
# The Rate Lever probe (wave-3 remainder): structural invariants of the grid.
# ---------------------------------------------------------------------------


def _state(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "state": "IL",
        "addressable": 1000,
        "rate_movable": 900,
        "in_the_money": [400, 300, 200],
        "contactable_in_the_money": [40, 30, 20],
    }
    row.update(overrides)
    return row


def _rate_grid(**overrides: object) -> dict[str, object]:
    grid: dict[str, object] = {
        "built": True,
        "steps_bps": [-50, 0, 50],
        "scenario_market_rate_pct": [5.8, 6.3, 6.8],
        "base_market_rate_pct": 6.3,
        "thresholds": {"min_spread_bps": 75, "min_equity_pct": 15},
        "states": [
            _state(),
            _state(
                state="TX",
                addressable=500,
                rate_movable=450,
                in_the_money=[100, 100, 90],
                contactable_in_the_money=None,
            ),
        ],
        "provenance": {"note": "fixture"},
    }
    grid.update(overrides)
    return grid


def _rate_flags(sample: object) -> list[str]:
    return collect_red_flags(
        [
            ProbeResult(
                name="geo.rate_sensitivity",
                method="GET",
                path="/api/geo/rate-sensitivity",
                status=200,
                ok=True,
                sample=sample,
            )
        ]
    )


def test_verify_live_probes_rate_sensitivity_and_the_audit_explorer_reads() -> None:
    source = (REPO / "tools" / "verify_live.py").read_text(encoding="utf-8")

    assert '"geo.rate_sensitivity", "GET", "/api/geo/rate-sensitivity"' in source
    assert source.index('"geo.state_rollups"') < source.index('"geo.rate_sensitivity"')
    for name, path in (("audit.facets", "/api/audit/facets"), ("audit.count", "/api/audit/count")):
        assert f'"{name}", "GET", "{path}", extra_headers=admin_headers' in source


def test_a_clean_rate_grid_raises_no_flag() -> None:
    assert _rate_flags(_rate_grid()) == []


@pytest.mark.parametrize(
    ("grid", "flag"),
    [
        (_rate_grid(built=False, steps_bps=[], scenario_market_rate_pct=[], states=[]), "built is not true"),
        (_rate_grid(steps_bps=[], scenario_market_rate_pct=[]), "steps_bps is empty"),
        (_rate_grid(steps_bps=[-50, 25, 50]), "steps_bps is missing step 0"),
        (_rate_grid(steps_bps=[0, -50, 50]), "steps_bps is not strictly ascending"),
        (_rate_grid(steps_bps=[-50, 0, 0]), "steps_bps is not strictly ascending"),
        (_rate_grid(scenario_market_rate_pct=[6.3]), "scenario_market_rate_pct length differs from steps_bps"),
        (_rate_grid(states=[]), "states is empty"),
        (_rate_grid(states=[_state(in_the_money=[400, 300])]), "IL: in_the_money length differs from steps_bps"),
        (_rate_grid(states=[_state(in_the_money=[1001, 300, 200])]), "IL: in_the_money above addressable"),
        (_rate_grid(states=[_state(rate_movable=1001)]), "IL: rate_movable above addressable"),
        (
            _rate_grid(states=[_state(contactable_in_the_money=[40, 30])]),
            "IL: contactable_in_the_money length differs from steps_bps",
        ),
        (
            _rate_grid(states=[_state(contactable_in_the_money=[401, 30, 20])]),
            "IL: contactable_in_the_money above in_the_money",
        ),
        (_rate_grid(states=[_state(in_the_money=[300, 301, 200])]), "IL: in_the_money increases as par rises"),
    ],
    ids=[
        "not-built",
        "no-steps",
        "no-zero-step",
        "not-ascending",
        "repeated-step",
        "rate-length",
        "no-states",
        "itm-length",
        "itm-above-addressable",
        "movable-above-addressable",
        "contactable-length",
        "contactable-above-itm",
        "itm-increases",
    ],
)
def test_each_rate_grid_defect_raises_its_flag(grid: dict[str, object], flag: str) -> None:
    flags = _rate_flags(grid)

    assert any(flag in line for line in flags), flags
    assert all(line.startswith("geo.rate_sensitivity: ") for line in flags)


def test_rate_grid_offenders_are_capped_at_five_states() -> None:
    states = [_state(state=f"S{index}", rate_movable=5000) for index in range(8)]

    flags = _rate_flags(_rate_grid(states=states))

    assert len([line for line in flags if "rate_movable above addressable" in line]) == 5
    assert flags[-1] == "geo.rate_sensitivity: and 3 more states"


# ---------------------------------------------------------------------------
# W5e (W5b deploy follow-ups): outreach.approve.real certifies the audited
# draft, and the source-age threshold is an operator argument.
# ---------------------------------------------------------------------------

_BORROWERS = [{"borrower_id": "B-0000000000AA1"}, {"borrower_id": "B-0000000000AA2"}]
_DRAFT: dict[str, Any] = {
    "generation_id": "0b1c2d3e-4f50-4a61-8b72-93a4b5c6d7e8",
    "response_hash": "f" * 64,
    "source_refreshed_at": "2026-08-09T12:00:00Z",
    "borrower_id": "B-0000000000AA1",
    "campaign_id": None,
    "variant_name": None,
    "offer_code": "heloc",
    "channel": "sms",
    "subject": None,
    "body": "Summit Mortgage: review your home equity options. Reply STOP to opt out.",
}


class _Response:
    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class _Recorder:
    """A urlopen stand-in: canned answers per (method, path), every request recorded."""

    def __init__(self, routes: dict[tuple[str, str], tuple[int, Any]]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, Any]] = []

    def __call__(self, request: urllib.request.Request, timeout: float | None = None) -> _Response:
        method = request.get_method()
        path = urllib.parse.urlsplit(request.full_url).path
        raw = request.data
        body = json.loads(raw) if isinstance(raw, bytes) else None
        self.calls.append((method, path, body))
        status, payload = self.routes.get((method, path), (200, {}))
        if status >= 400:
            fp = io.BytesIO(json.dumps(payload).encode("utf-8"))
            raise urllib.error.HTTPError(request.full_url, status, "error", hdrs=None, fp=fp)  # type: ignore[arg-type]
        return _Response(status, payload)

    def posts(self, path: str) -> list[Any]:
        return [body for method, called, body in self.calls if method == "POST" and called == path]


def _run(monkeypatch: pytest.MonkeyPatch, draft: tuple[int, Any]) -> tuple[_Recorder, list[ProbeResult]]:
    recorder = _Recorder({("GET", "/api/leads"): (200, _BORROWERS), ("POST", "/api/outreach/draft"): draft})
    monkeypatch.setattr(urllib.request, "urlopen", recorder)
    return recorder, run_probes("https://app.example", "test-token")


def test_approve_real_certifies_the_audited_draft_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder, results = _run(monkeypatch, (200, _DRAFT))

    [body] = [b for b in recorder.posts("/api/outreach/approve") if b["borrower_id"] == "B-0000000000AA1"]
    assert body["borrower_id"] == "B-0000000000AA1"
    assert body["draft_generation_id"] == _DRAFT["generation_id"]
    assert body["draft_response_hash"] == _DRAFT["response_hash"]
    assert body["draft_source_refreshed_at"] == _DRAFT["source_refreshed_at"]
    assert body["draft_subject"] == _DRAFT["subject"]
    assert body["draft_body"] == _DRAFT["body"]
    assert body["offer_code"] == _DRAFT["offer_code"]
    assert body["channel"] == _DRAFT["channel"]
    assert body["campaign_id"] is None and body["variant_name"] is None
    assert body["review_mode"] == "individual"
    assert body["request_id"]
    approve = next(r for r in results if r.name == "outreach.approve.real")
    assert approve.ok and approve.status == 200


def test_a_failed_draft_sends_no_approve_and_raises_the_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder, results = _run(monkeypatch, (503, {"detail": "draft generation unavailable"}))

    real_approves = [
        body for body in recorder.posts("/api/outreach/approve") if body["borrower_id"] == "B-0000000000AA1"
    ]
    assert real_approves == []
    approve = next(r for r in results if r.name == "outreach.approve.real")
    assert not approve.ok and approve.error == APPROVE_DRAFT_UNAVAILABLE
    assert f"outreach.approve.real: status=0 error={APPROVE_DRAFT_UNAVAILABLE}" in collect_red_flags(results)


def test_a_draft_without_its_proof_sends_no_approve(monkeypatch: pytest.MonkeyPatch) -> None:
    unproven = {key: value for key, value in _DRAFT.items() if key != "response_hash"}
    recorder, results = _run(monkeypatch, (200, unproven))

    assert [b for b in recorder.posts("/api/outreach/approve") if b["borrower_id"] == "B-0000000000AA1"] == []
    assert next(r for r in results if r.name == "outreach.approve.real").error == APPROVE_DRAFT_UNAVAILABLE


def _admin_sources_checked(days_ago: float) -> list[ProbeResult]:
    stamp = (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
    rows = _clean_source_rows()
    for row in rows:
        if row["checked_at"] is not None:
            row["checked_at"] = stamp
    return [ProbeResult(name="admin.sources", method="GET", path="/api/admin/sources", status=200, ok=True, sample=rows)]


def test_the_threshold_argument_moves_the_stale_boundary() -> None:
    five_days = _admin_sources_checked(5)

    stale = [flag for flag in collect_red_flags(five_days) if "checked_at is stale" in flag]
    assert stale and all(flag.endswith("(5.0 d > 3 d)") for flag in stale), stale
    assert collect_red_flags(five_days, max_source_age_days=7) == []
    sixty_six = [flag for flag in collect_red_flags(_admin_sources_checked(66.2)) if "stale" in flag]
    assert sixty_six and all(flag.endswith("(66 d > 3 d)") for flag in sixty_six), sixty_six


def test_the_report_header_names_no_identity_and_prints_the_threshold() -> None:
    report = render_markdown([], "https://app.example", "B-TEST-*", profile="dev", max_source_age_days=7)

    assert "Auth: `databricks auth token -p dev` (OAuth bearer of the profile's identity)" in report
    assert "Source freshness: `checked_at` within 7 d (`--max-source-age-days`)" in report
    assert "@" not in report.split("## Endpoint results", 1)[0]


def test_the_cli_threads_the_threshold_into_the_flags(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, int] = {}

    def fake_collect(results: list[ProbeResult], *, max_source_age_days: int = 3) -> list[str]:
        seen["days"] = max_source_age_days
        return []

    monkeypatch.setattr(verify_live, "get_oauth_token", lambda profile: "test-token")
    monkeypatch.setattr(verify_live, "run_probes", lambda base, token: [])
    monkeypatch.setattr(verify_live, "collect_red_flags", fake_collect)
    out = tmp_path / "report.md"
    monkeypatch.setattr("sys.argv", ["verify_live.py", "--out", str(out), "--max-source-age-days", "9"])

    assert verify_live.main() == 0
    assert seen["days"] == 9
    assert "within 9 d" in out.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The live geo drill (post-W5 live-gate-geo-drill): largest populated state ->
# its ZIPs, the Lead Queue deep link by state + ZIP, and the county endpoint
# probed only as a retired-grain shape contract whose empty answer passes.
# ---------------------------------------------------------------------------


def _drill_states(**overrides: object) -> dict[str, Any]:
    # verify_live's cohort shape with CA first: the filtered state SQL has no
    # ORDER BY, so CA was the old first-row pick on 2026-10-26.
    payload: dict[str, Any] = {
        "rollups": [
            {"state": "CA", "addressable": 104, "zip_unassigned_count": 0},
            {"state": "IL", "addressable": 751, "zip_unassigned_count": 3},
            {"state": "FL", "addressable": 259, "zip_unassigned_count": 0},
        ],
        "snapshot_date": None,
    }
    payload.update(overrides)
    return payload


def _drill_zip(zip_code: object, borrowers: object, state: object = "IL") -> dict[str, Any]:
    return {"zip": zip_code, "state": state, "county_fips_5": None, "addressable_borrowers": borrowers}


def _drill_zips(**overrides: object) -> dict[str, Any]:
    # 751 - 3 unassigned = 748 borrowers on the ZIP layer.
    payload: dict[str, Any] = {
        "state": "IL",
        "fips_5": None,
        "rollups": [_drill_zip("60628", 690), _drill_zip("60617", 29), _drill_zip("60601", 29)],
        "snapshot_date": None,
    }
    payload.update(overrides)
    return payload


def _drill_counties(**overrides: object) -> dict[str, Any]:
    payload: dict[str, Any] = {"state": "IL", "rollups": [], "snapshot_date": None, "scope_note": None}
    payload.update(overrides)
    return payload


class _GeoProbe:
    """A `verify_live.probe` stand-in: canned 200 payloads per endpoint, every path recorded."""

    def __init__(self, payloads: dict[str, Any], failing: frozenset[str] = frozenset()) -> None:
        self.payloads = payloads
        self.failing = failing
        self.paths: list[str] = []

    def __call__(self, base: str, token: str, name: str, method: str, path: str, **_: Any) -> ProbeResult:
        self.paths.append(path)
        endpoint = urllib.parse.urlsplit(path).path
        if endpoint in self.failing:
            return ProbeResult(name=name, method=method, path=path, status=503, ok=False, error="unavailable")
        payload = self.payloads[endpoint]
        if isinstance(payload, list):
            return ProbeResult(
                name=name,
                method=method,
                path=path,
                status=200,
                ok=True,
                sample=payload[0] if payload else [],
                top_keys=[f"[array len={len(payload)}]"],
            )
        return ProbeResult(
            name=name, method=method, path=path, status=200, ok=True, sample=payload, top_keys=sorted(payload)
        )

    def query(self, endpoint: str) -> dict[str, list[str]]:
        [path] = [p for p in self.paths if urllib.parse.urlsplit(p).path == endpoint]
        return urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)


def _drill(
    monkeypatch: pytest.MonkeyPatch,
    *,
    states: Any = None,
    zips: Any = None,
    counties: Any = None,
    failing: frozenset[str] = frozenset(),
) -> tuple[_GeoProbe, list[ProbeResult]]:
    fake = _GeoProbe(
        {
            "/api/geo/state-rollups": _drill_states() if states is None else states,
            "/api/geo/zip-rollups": _drill_zips() if zips is None else zips,
            "/api/geo/county-rollups": _drill_counties() if counties is None else counties,
            "/api/leads": [{"borrower_id": "B-0000000000AA1"}],
        },
        failing,
    )

    def no_direct_reads(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the drill reads only through probe(), so every checked payload is the recorded one")

    monkeypatch.setattr(verify_live, "probe", fake)
    monkeypatch.setattr(urllib.request, "urlopen", no_direct_reads)
    return fake, verify_live._geo_drill_probes("https://app.example", "test-token")


def test_geo_drill_targets_the_largest_populated_state_then_its_zips(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, results = _drill(monkeypatch)

    assert [urllib.parse.urlsplit(path).path for path in fake.paths] == [
        "/api/geo/state-rollups",
        "/api/geo/zip-rollups",
        "/api/geo/county-rollups",
        "/api/leads",
    ]
    assert [r.name for r in results] == [
        "geo.state_rollups.filtered",
        "geo.zip_rollups.filtered",
        "geo.county_rollups.retired_grain",
        "leads.filtered_geo",
    ]
    zip_query = fake.query("/api/geo/zip-rollups")
    assert zip_query["state"] == ["IL"] and "county_fips" not in zip_query
    assert fake.query("/api/geo/county-rollups")["state"] == ["IL"]
    leads = fake.query("/api/leads")
    assert leads["state"] == ["IL"] and leads["zip"] == ["60628"]
    assert "county" not in leads and "county_fips" not in leads
    for query in (zip_query, leads):
        assert query["segment_codes"] == ["itm,equity"] and query["segment_mode"] == ["all"]
        assert query["min_equity_pct_label"] == ["≥ 25%"]


def test_a_clean_drill_with_the_empty_county_answer_raises_no_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    _, results = _drill(monkeypatch)

    assert collect_red_flags(results) == []


def test_a_county_row_with_a_five_digit_fips_raises_no_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    _, results = _drill(monkeypatch, counties=_drill_counties(rollups=[{"fips_5": "17031", "state": "IL"}]))

    assert collect_red_flags(results) == []


def test_the_drill_breaks_size_ties_by_state_then_zip(monkeypatch: pytest.MonkeyPatch) -> None:
    states = _drill_states(
        rollups=[
            {"state": "TX", "addressable": 751, "zip_unassigned_count": 0},
            {"state": "IL", "addressable": 751, "zip_unassigned_count": 3},
        ]
    )
    zips = _drill_zips(rollups=[_drill_zip("60628", 374), _drill_zip("60617", 374)])

    fake, results = _drill(monkeypatch, states=states, zips=zips)

    assert fake.query("/api/geo/zip-rollups")["state"] == ["IL"]
    assert fake.query("/api/leads")["zip"] == ["60617"]
    assert collect_red_flags(results) == []


_ZIP_FLAG = "geo.zip_rollups.filtered: "
_COUNTY_FLAG = "geo.county_rollups.retired_grain: "
_IL_STATE_ROW = {"state": "IL", "addressable": 751, "zip_unassigned_count": 3}


def _unreconciled(total: int) -> str:
    return (
        f"geo.drill_reconciliation: IL does not reconcile: sum(zip.addressable_borrowers)={total}, "
        "expected state.addressable 751 - state.zip_unassigned_count 3 = 748"
    )


# Every recorded-payload defect and the flag it raises. The ids are the shared
# bad-shape vocabulary with tests/unit/test_smoke_live_contract.py (pinned below).
GEO_DRILL_DEFECTS = [
    # The ZIP layer: populated, in-state, 5-digit ZIPs, whole counts.
    pytest.param({"zips": _drill_zips(rollups=[])}, _ZIP_FLAG + "`rollups` returned empty array", id="empty-zip-list"),
    pytest.param(
        {"zips": _drill_zips(rollups=[])},
        _ZIP_FLAG + "no ZIP row with addressable borrowers for IL",
        id="empty-zip-list-no-populated",
    ),
    pytest.param(
        {"zips": _drill_zips(rollups=[])},
        "geo.drill_target.zip: status=0 error=no filtered ZIP rollup with addressable borrowers for IL",
        id="empty-zip-list-no-target",
    ),
    pytest.param({"zips": _drill_zips(rollups=None)}, _ZIP_FLAG + "`rollups` is not an array", id="zip-rollups-not-an-array"),
    pytest.param(
        {"zips": _drill_zips(rollups=[_drill_zip("60628", 690), _drill_zip("60617", 29), _drill_zip("60601", 29), "60602"])},
        _ZIP_FLAG + "a ZIP rollup row is not an object",
        id="non-object-zip-row",
    ),
    pytest.param(
        {"zips": _drill_zips(rollups=[_drill_zip("60628", 719), _drill_zip("6061", 29)])},
        _ZIP_FLAG + "ZIP row without a 5-digit zip: 1 row(s), first '6061'",
        id="four-digit-zip",
    ),
    pytest.param(
        {"zips": _drill_zips(rollups=[_drill_zip("60628", 719), _drill_zip("6061A", 29)])},
        _ZIP_FLAG + "ZIP row without a 5-digit zip: 1 row(s), first '6061A'",
        id="non-numeric-zip",
    ),
    pytest.param(
        {"zips": _drill_zips(rollups=[_drill_zip("60628", 719), _drill_zip(60617, 29)])},
        _ZIP_FLAG + "ZIP row without a 5-digit zip: 1 row(s), first 60617",
        id="numeric-zip-not-a-string",
    ),
    pytest.param(
        {"zips": _drill_zips(rollups=[_drill_zip("60628", 719), _drill_zip("60617\n", 29)])},
        _ZIP_FLAG + "ZIP row without a 5-digit zip: 1 row(s), first '60617\\n'",
        id="zip-with-trailing-newline",
    ),
    pytest.param(
        {"zips": _drill_zips(rollups=[_drill_zip("60628", 748), _drill_zip("75201", 0, "TX")])},
        _ZIP_FLAG + "ZIP row outside IL: 1 row(s), first 'TX'",
        id="zip-row-from-another-state",
    ),
    pytest.param(
        {"zips": _drill_zips(state="TX")},
        _ZIP_FLAG + "envelope state 'TX' is not the drilled IL",
        id="zip-envelope-other-state",
    ),
    pytest.param(
        {"zips": _drill_zips(rollups=[_drill_zip("60628", 0)])},
        _ZIP_FLAG + "no ZIP row with addressable borrowers for IL",
        id="no-populated-zip",
    ),
    pytest.param(
        {"zips": _drill_zips(rollups=[_drill_zip("60628", 719), _drill_zip("60617", "29")])},
        "geo.drill_reconciliation: cannot sum the IL ZIP layer: a row lacks a whole addressable_borrowers",
        id="string-zip-count",
    ),
    # Each count case below sums to the reconciling 748, so only whole_count
    # can refuse it.
    pytest.param(
        {
            "zips": _drill_zips(
                rollups=[_drill_zip("60628", 690), _drill_zip("60617", 30), _drill_zip("60601", 29), _drill_zip("60602", -1)]
            )
        },
        _ZIP_FLAG + "ZIP row without a whole addressable_borrowers: 1 row(s), first -1",
        id="negative-zip-count",
    ),
    pytest.param(
        {
            "zips": _drill_zips(
                rollups=[_drill_zip("60628", 690), _drill_zip("60617", 29), _drill_zip("60601", 28.5), _drill_zip("60602", 0.5)]
            )
        },
        _ZIP_FLAG + "ZIP row without a whole addressable_borrowers: 2 row(s), first 28.5",
        id="fractional-zip-count",
    ),
    pytest.param(
        {
            "zips": _drill_zips(
                rollups=[_drill_zip("60628", 690), _drill_zip("60617", 29), _drill_zip("60601", 28), _drill_zip("60602", True)]
            )
        },
        _ZIP_FLAG + "ZIP row without a whole addressable_borrowers: 1 row(s), first True",
        id="boolean-zip-count",
    ),
    # The retired county grain: empty passes; any row is in-state with a 5-digit FIPS.
    pytest.param(
        {"counties": _drill_counties(rollups=[{"fips_5": "", "state": "IL"}])},
        _COUNTY_FLAG + "county row without a 5-digit fips_5: 1 row(s), first ''",
        id="county-row-empty-fips",
    ),
    pytest.param(
        {"counties": _drill_counties(rollups=[{"fips_5": None, "state": "IL"}])},
        _COUNTY_FLAG + "county row without a 5-digit fips_5: 1 row(s), first None",
        id="county-row-null-fips",
    ),
    pytest.param(
        {"counties": _drill_counties(rollups=[{"fips_5": "1703", "state": "IL"}])},
        _COUNTY_FLAG + "county row without a 5-digit fips_5: 1 row(s), first '1703'",
        id="county-row-four-digit-fips",
    ),
    pytest.param(
        {"counties": _drill_counties(rollups=[{"fips_5": "17031", "state": "WI"}])},
        _COUNTY_FLAG + "county row outside IL: 1 row(s), first 'WI'",
        id="county-row-other-state",
    ),
    pytest.param(
        {"counties": _drill_counties(state="CA")},
        _COUNTY_FLAG + "envelope state 'CA' is not the drilled 'IL'",
        id="county-envelope-other-state",
    ),
    pytest.param(
        {"counties": _drill_counties(rollups=None)},
        _COUNTY_FLAG + "`rollups` is not an array",
        id="county-rollups-not-an-array",
    ),
    pytest.param(
        {"counties": _drill_counties(rollups=["17031"])},
        _COUNTY_FLAG + "a county rollup row is not an object",
        id="non-object-county-row",
    ),
    # The reconciliation: 751 - 3 = 748 exactly, so it flags both directions.
    pytest.param(
        {"zips": _drill_zips(rollups=[_drill_zip("60628", 690), _drill_zip("60617", 30), _drill_zip("60601", 29)])},
        _unreconciled(749),
        id="broken-invariant-over-sum",
    ),
    pytest.param(
        {"zips": _drill_zips(rollups=[_drill_zip("60628", 690), _drill_zip("60617", 29), _drill_zip("60601", 28)])},
        _unreconciled(747),
        id="broken-invariant-under-sum",
    ),
    pytest.param(
        {"states": _drill_states(rollups=[{"state": "IL", "addressable": 751}, {"state": "CA", "addressable": 104}])},
        "geo.drill_reconciliation: IL state row lacks a whole addressable or zip_unassigned_count "
        "(addressable=751, zip_unassigned_count=None)",
        id="state-row-without-zip-unassigned",
    ),
    pytest.param(
        {"states": _drill_states(rollups=[_IL_STATE_ROW, dict(_IL_STATE_ROW)])},
        "geo.drill_reconciliation: expected exactly one filtered state rollup row for 'IL', found 2",
        id="duplicate-target-state-rows",
    ),
]


@pytest.mark.parametrize(("payloads", "flag"), GEO_DRILL_DEFECTS)
def test_each_geo_drill_defect_raises_its_flag(
    monkeypatch: pytest.MonkeyPatch, payloads: dict[str, Any], flag: str
) -> None:
    _, results = _drill(monkeypatch, **payloads)

    flags = collect_red_flags(results)

    assert flag in flags, flags


def test_an_empty_zip_layer_sends_no_lead_queue_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, results = _drill(monkeypatch, zips=_drill_zips(rollups=[]))

    assert all(urllib.parse.urlsplit(path).path != "/api/leads" for path in fake.paths)
    assert results[-1].name == "geo.drill_target.zip" and not results[-1].ok


def test_the_lead_queue_zip_comes_from_the_drilled_state_only(monkeypatch: pytest.MonkeyPatch) -> None:
    # A larger out-of-state row is flagged, and it never becomes the deep link.
    zips = _drill_zips(rollups=[_drill_zip("60628", 748), _drill_zip("75201", 900, "TX")])

    fake, results = _drill(monkeypatch, zips=zips)

    assert fake.query("/api/leads")["zip"] == ["60628"]
    assert _ZIP_FLAG + "ZIP row outside IL: 1 row(s), first 'TX'" in collect_red_flags(results)


def _bad_state_row(addressable: object) -> str:
    return (
        f"filtered state rollup row state='CA' addressable={addressable!r} "
        "lacks a 2-letter state or a whole addressable count"
    )


GEO_DRILL_TARGET_REFUSALS = [
    pytest.param(
        _drill_states(rollups=[{"state": "IL", "addressable": 0, "zip_unassigned_count": 0}]),
        frozenset(),
        "no filtered state rollup with addressable borrowers",
        id="no-populated-state",
    ),
    pytest.param(
        _drill_states(rollups=[]), frozenset(), "no filtered state rollup with addressable borrowers", id="no-state-rows"
    ),
    pytest.param(
        _drill_states(rollups=None),
        frozenset(),
        "filtered state rollups carried no `rollups` array",
        id="state-rollups-not-an-array",
    ),
    pytest.param(
        _drill_states(rollups=[{"state": "il", "addressable": 751, "zip_unassigned_count": 3}]),
        frozenset(),
        "filtered state rollup row state='il' addressable=751 lacks a 2-letter state or a whole addressable count",
        id="lowercase-state",
    ),
    pytest.param(
        _drill_states(
            rollups=[
                {"state": "CA", "addressable": 104, "zip_unassigned_count": 0},
                {"state": "IL", "addressable": "751", "zip_unassigned_count": 3},
            ]
        ),
        frozenset(),
        "filtered state rollup row state='IL' addressable='751' lacks a 2-letter state or a whole addressable count",
        id="string-addressable",
    ),
    # A malformed row that is not the target still refuses the whole payload.
    pytest.param(
        _drill_states(rollups=[{"state": "CA", "addressable": -1, "zip_unassigned_count": 0}, _IL_STATE_ROW]),
        frozenset(),
        _bad_state_row(-1),
        id="negative-state-addressable",
    ),
    pytest.param(
        _drill_states(rollups=[{"state": "CA", "addressable": 103.5, "zip_unassigned_count": 0}, _IL_STATE_ROW]),
        frozenset(),
        _bad_state_row(103.5),
        id="fractional-state-addressable",
    ),
    pytest.param(
        _drill_states(), frozenset({"/api/geo/state-rollups"}), "filtered state rollups probe failed", id="state-probe-failed"
    ),
]


@pytest.mark.parametrize(("states", "failing", "reason"), GEO_DRILL_TARGET_REFUSALS)
def test_a_state_payload_without_a_clean_target_stops_the_drill(
    monkeypatch: pytest.MonkeyPatch, states: dict[str, Any], failing: frozenset[str], reason: str
) -> None:
    fake, results = _drill(monkeypatch, states=states, failing=failing)

    assert len(fake.paths) == 1
    assert results[-1].name == "geo.drill_target.state"
    assert not results[-1].ok and results[-1].error == reason
    assert f"geo.drill_target.state: status=0 error={reason}" in collect_red_flags(results)


# verify_live-only ids: the extra flags it raises for the empty-ZIP payload,
# and a failed state probe (smoke's own probe() exits on any non-200 before
# its geo checks run).
VERIFY_LIVE_ONLY_GEO_IDS = frozenset({"empty-zip-list-no-populated", "empty-zip-list-no-target", "state-probe-failed"})


def test_smoke_and_verify_live_refuse_the_same_geo_bad_shapes() -> None:
    verify_ids = [str(case.id) for case in (*GEO_DRILL_DEFECTS, *GEO_DRILL_TARGET_REFUSALS)]
    assert len(verify_ids) == len(set(verify_ids))
    assert VERIFY_LIVE_ONLY_GEO_IDS.issubset(verify_ids)
    assert len(SMOKE_GEO_REFUSAL_IDS) == len(set(SMOKE_GEO_REFUSAL_IDS))
    assert set(SMOKE_GEO_REFUSAL_IDS) == set(verify_ids) - VERIFY_LIVE_ONLY_GEO_IDS


@pytest.mark.parametrize(
    "name",
    ["geo.county_rollups.filtered", "geo.zip_rollups.filtered", "geo.state_rollups.filtered"],
)
def test_the_empty_rollups_waiver_is_scoped_to_the_retired_county_probe(name: str) -> None:
    flags = collect_red_flags(
        [ProbeResult(name=name, method="GET", path="/api/geo/x?state=IL", status=200, ok=True, sample={"rollups": []})]
    )

    assert f"{name}: `rollups` returned empty array" in flags


def test_the_retired_county_probe_keeps_every_other_empty_array_flag() -> None:
    flags = collect_red_flags(
        [
            ProbeResult(
                name="geo.county_rollups.retired_grain",
                method="GET",
                path="/api/geo/county-rollups?state=IL",
                status=200,
                ok=True,
                sample={"state": "IL", "rollups": [], "rows": []},
            )
        ]
    )

    assert flags == ["geo.county_rollups.retired_grain: `rows` returned empty array"]
