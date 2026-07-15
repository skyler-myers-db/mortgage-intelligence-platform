from __future__ import annotations

from typing import Any

from backend.services.campaign_targeting import campaign_contains_borrower


class _CaptureLeadRepository:
    def __init__(self, *, count: int = 1) -> None:
        self.result = count
        self.kwargs: dict[str, Any] = {}

    def count(self, **kwargs: Any) -> int:
        self.kwargs = kwargs
        return self.result


def test_portfolio_campaign_replays_exact_criteria_for_one_borrower() -> None:
    repo = _CaptureLeadRepository()

    assert campaign_contains_borrower(
        repo,  # type: ignore[arg-type]
        borrower_id="B-0000000000001",
        criteria={
            "states": ["IL"],
            "owner_link": "Multi-property (2-4)",
            "marketing_eligibility": "Eligible only",
        },
    )

    assert repo.kwargs["borrower_ids"] == ["B-0000000000001"]
    assert repo.kwargs["state_codes"] is None
    portfolio = repo.kwargs["portfolio_criteria"]
    assert portfolio.states == ["IL"]
    assert portfolio.owner_link == "Multi-property (2-4)"
    assert portfolio.marketing_eligibility == "Eligible only"


def test_genie_campaign_replays_nested_filters_without_double_counting() -> None:
    repo = _CaptureLeadRepository()

    assert campaign_contains_borrower(
        repo,  # type: ignore[arg-type]
        borrower_id="B-0000000000001",
        criteria={
            "source": "trusted_sql",
            "borrower_ids": ["B-0000000000001"],
            "result_filters": {
                "states": ["IL"],
                "segment_codes": ["itm", "equity"],
                "segment_mode": "all",
                "portfolio_criteria": {"marketing_eligibility": "Eligible only"},
            },
        },
    )

    assert repo.kwargs["borrower_ids"] == ["B-0000000000001"]
    assert repo.kwargs["state_codes"] == ["IL"]
    assert repo.kwargs["segment_codes"] == ["itm", "equity"]
    assert repo.kwargs["segment_mode"] == "all"
    assert repo.kwargs["portfolio_criteria"].marketing_eligibility == "Eligible only"


def test_genie_campaign_fails_before_query_when_explicit_borrower_set_excludes_subject() -> None:
    repo = _CaptureLeadRepository()

    assert campaign_contains_borrower(
        repo,  # type: ignore[arg-type]
        borrower_id="B-0000000000001",
        criteria={
            "source": "genie",
            "borrower_ids": ["B-0000000000002"],
        },
    ) is False
    assert repo.kwargs == {}


def test_campaign_membership_requires_exactly_one_unique_borrower_match() -> None:
    repo = _CaptureLeadRepository(count=2)

    assert campaign_contains_borrower(
        repo,  # type: ignore[arg-type]
        borrower_id="B-0000000000001",
        criteria={"marketing_eligibility": "Eligible only"},
    ) is False
