from __future__ import annotations

from pathlib import Path

import pytest

from tools.verify_live import ProbeResult, collect_red_flags

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
    assert "admin.sources: MMA Mortgage Analytics checked_at is stale" in flags
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
