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
        flag.startswith("admin.sources: MMA Mortgage Analytics checked_at is stale (") and flag.endswith(" d > 8 d)")
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
    nine_days = _admin_sources_checked(9)

    stale = [flag for flag in collect_red_flags(nine_days) if "checked_at is stale" in flag]
    assert stale and all(flag.endswith("(9.0 d > 8 d)") for flag in stale), stale
    assert collect_red_flags(nine_days, max_source_age_days=10) == []
    sixty_six = [flag for flag in collect_red_flags(_admin_sources_checked(66.2)) if "stale" in flag]
    assert sixty_six and all(flag.endswith("(66 d > 8 d)") for flag in sixty_six), sixty_six


def test_the_default_threshold_is_the_weekly_cadence_plus_one_day() -> None:
    """A run that is one cadence old is fresh; one missed Friday is flagged."""

    assert not [flag for flag in collect_red_flags(_admin_sources_checked(7.5)) if "stale" in flag]
    missed = [flag for flag in collect_red_flags(_admin_sources_checked(8.5)) if "stale" in flag]
    assert missed and all(flag.endswith("(8.5 d > 8 d)") for flag in missed), missed


def test_the_report_header_names_no_identity_and_prints_the_threshold() -> None:
    report = render_markdown([], "https://app.example", "B-TEST-*", profile="dev", max_source_age_days=7)

    assert "Auth: `databricks auth token -p dev` (OAuth bearer of the profile's identity)" in report
    assert "Source freshness: `checked_at` within 7 d (`--max-source-age-days`)" in report
    assert "@" not in report.split("## Endpoint results", 1)[0]


def test_the_cli_threads_the_threshold_into_the_flags(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, int] = {}

    def fake_collect(results: list[ProbeResult], *, max_source_age_days: int = 8) -> list[str]:
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


def test_the_cli_default_threshold_is_the_weekly_cadence_plus_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, int] = {}

    def fake_collect(results: list[ProbeResult], *, max_source_age_days: int = 0) -> list[str]:
        seen["days"] = max_source_age_days
        return []

    monkeypatch.setattr(verify_live, "get_oauth_token", lambda profile: "test-token")
    monkeypatch.setattr(verify_live, "run_probes", lambda base, token: [])
    monkeypatch.setattr(verify_live, "collect_red_flags", fake_collect)
    out = tmp_path / "report.md"
    monkeypatch.setattr("sys.argv", ["verify_live.py", "--out", str(out)])

    assert verify_live.main() == 0
    assert seen["days"] == 8
    assert "within 8 d" in out.read_text(encoding="utf-8")

    monkeypatch.setattr("sys.argv", ["verify_live.py", "--help"])
    with pytest.raises(SystemExit):
        verify_live.main()
    assert "default 8: the dev weekly cadence + 1 day" in " ".join(capsys.readouterr().out.split())
