from __future__ import annotations

from pathlib import Path

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
            _source_row("MLS", status="roadmap", rows=None, last_updated=None),
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
    rows[-2]["status"] = "live"

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
    assert "admin.sources: MLS cannot be live until the feed is loaded" in flags


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
