import pytest

from backend.services.proof_policy import (
    borrower_proof_assets,
    hash_sql,
    validate_borrower_proof_sql,
)
from backend.services.repositories.databricks_borrowers import _build_borrower_proof
from tests.fixtures import mock_population as mock_data


def test_borrower_proof_assets_are_bounded_gold_assets() -> None:
    assets = borrower_proof_assets()
    assert "mip.gold.borrower_dossier" in assets
    assert "mip.gold.lead_scores" in assets
    assert "mip.gold.evidence_events" in assets
    assert all(".silver." not in asset for asset in assets)
    assert all(not asset.startswith("mip_app.") for asset in assets)


def test_validate_borrower_proof_sql_accepts_fixed_select_template() -> None:
    sql = (
        "SELECT borrower_id, opportunity_score "
        "FROM mip.gold.borrower_dossier WHERE borrower_id = :borrower_id"
    )
    assert validate_borrower_proof_sql(sql) == sql
    assert len(hash_sql(sql)) == 16


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM mip.gold.borrower_dossier WHERE borrower_id = :borrower_id",
        "DELETE FROM mip.gold.borrower_dossier WHERE borrower_id = :borrower_id",
        "SELECT owner_name_hash FROM mip.gold.borrower_dossier WHERE borrower_id = :borrower_id",
        "SELECT source_table FROM mip.gold.evidence_events WHERE evidence_id = 'E-1'",
        "SELECT borrower_id FROM mip.silver.property_master WHERE borrower_id = :borrower_id",
        "SELECT borrower_id FROM system.query.history",
        "SELECT borrower_id FROM mip.gold.borrower_dossier; DROP TABLE mip.gold.borrower_dossier",
        "SELECT borrower_id FROM mip.gold.borrower_dossier -- debug",
    ],
)
def test_validate_borrower_proof_sql_rejects_unsafe_sql(sql: str) -> None:
    with pytest.raises(ValueError):
        validate_borrower_proof_sql(sql)


def test_borrower_proof_flags_score_drift() -> None:
    borrower = next(b for b in mock_data.BORROWERS if b.borrower_id == "B-48291")
    inputs = mock_data.BORROWER_OFFER_INPUTS[borrower.borrower_id]
    row = {
        **borrower.model_dump(),
        **inputs,
        **mock_data.BORROWER_SCORE_COMPONENTS[borrower.borrower_id],
        "score_opportunity_score": borrower.opportunity_score - 3,
        "score_signal_strength": borrower.confidence + 2,
        "min_spread_bps_applied": inputs["min_spread_bps"],
        "min_equity_pct_applied": inputs["min_equity_pct"],
        "heloc_equity_min_applied": inputs["heloc_equity_min_pct"],
        "cashout_equity_min_applied": inputs["cashout_equity_min_pct"],
        "retention_min_spread_applied": inputs["retention_min_spread_bps"],
        "market_rate_fraction": borrower.why_panel.market_rate,
        "dossier_refreshed_at": "2026-04-20T06:12:00Z",
        "score_refreshed_at": "2026-04-20T06:12:00Z",
    }

    proof = _build_borrower_proof(row)

    assert proof.trusted is False
    assert proof.opportunity_score == borrower.opportunity_score
    assert proof.signal_strength == borrower.confidence
    assert any("Borrower dossier opportunity score" in gap for gap in proof.known_data_gaps)
    assert any("Borrower dossier signal strength" in gap for gap in proof.known_data_gaps)
    assert any("opportunity score does not match" in gap for gap in proof.known_data_gaps)
    assert any("signal strength does not match" in gap for gap in proof.known_data_gaps)


def test_borrower_proof_flags_refresh_skew() -> None:
    borrower = next(b for b in mock_data.BORROWERS if b.borrower_id == "B-48291")
    inputs = mock_data.BORROWER_OFFER_INPUTS[borrower.borrower_id]
    row = {
        **borrower.model_dump(),
        **inputs,
        **mock_data.BORROWER_SCORE_COMPONENTS[borrower.borrower_id],
        "score_opportunity_score": borrower.opportunity_score,
        "score_signal_strength": borrower.confidence,
        "min_spread_bps_applied": inputs["min_spread_bps"],
        "min_equity_pct_applied": inputs["min_equity_pct"],
        "heloc_equity_min_applied": inputs["heloc_equity_min_pct"],
        "cashout_equity_min_applied": inputs["cashout_equity_min_pct"],
        "retention_min_spread_applied": inputs["retention_min_spread_bps"],
        "market_rate_fraction": borrower.why_panel.market_rate,
        "dossier_refreshed_at": "2026-04-20T06:12:00Z",
        "score_refreshed_at": "2026-04-20T06:17:00Z",
    }

    proof = _build_borrower_proof(row)

    assert proof.trusted is False
    assert any("refreshed at different times" in gap for gap in proof.known_data_gaps)
    assert proof.source_refresh_at == (
        "dossier 2026-04-20T06:12:00Z / lead_scores 2026-04-20T06:17:00Z"
    )


def test_borrower_proof_flags_next_best_offer_drift() -> None:
    borrower = next(b for b in mock_data.BORROWERS if b.borrower_id == "B-48291")
    inputs = mock_data.BORROWER_OFFER_INPUTS[borrower.borrower_id]
    row = {
        **borrower.model_dump(),
        **inputs,
        **mock_data.BORROWER_SCORE_COMPONENTS[borrower.borrower_id],
        "recommended_offer_code": "nurture",
        "score_opportunity_score": borrower.opportunity_score,
        "score_signal_strength": borrower.confidence,
        "min_spread_bps_applied": inputs["min_spread_bps"],
        "min_equity_pct_applied": inputs["min_equity_pct"],
        "heloc_equity_min_applied": inputs["heloc_equity_min_pct"],
        "cashout_equity_min_applied": inputs["cashout_equity_min_pct"],
        "retention_min_spread_applied": inputs["retention_min_spread_bps"],
        "market_rate_fraction": borrower.why_panel.market_rate,
        "dossier_refreshed_at": "2026-04-20T06:12:00Z",
        "score_refreshed_at": "2026-04-20T06:12:00Z",
    }

    proof = _build_borrower_proof(row)

    assert proof.trusted is False
    assert any("primary offer" in gap for gap in proof.known_data_gaps)
    nurture = next(branch for branch in proof.offer_branches if branch.code == "nurture")
    assert nurture.selected is True
    assert nurture.passed is False


def test_borrower_proof_reproduce_sql_recomputes_and_hides_raw_source_paths() -> None:
    borrower = next(b for b in mock_data.BORROWERS if b.borrower_id == "B-48291")
    inputs = mock_data.BORROWER_OFFER_INPUTS[borrower.borrower_id]
    row = {
        **borrower.model_dump(),
        **inputs,
        **mock_data.BORROWER_SCORE_COMPONENTS[borrower.borrower_id],
        "score_opportunity_score": borrower.opportunity_score,
        "score_signal_strength": borrower.confidence,
        "min_spread_bps_applied": inputs["min_spread_bps"],
        "min_equity_pct_applied": inputs["min_equity_pct"],
        "heloc_equity_min_applied": inputs["heloc_equity_min_pct"],
        "cashout_equity_min_applied": inputs["cashout_equity_min_pct"],
        "retention_min_spread_applied": inputs["retention_min_spread_bps"],
        "market_rate_fraction": borrower.why_panel.market_rate,
        "dossier_refreshed_at": "2026-04-20T06:12:00Z",
        "score_refreshed_at": "2026-04-20T06:12:00Z",
    }

    proof = _build_borrower_proof(row)
    score_query = next(query for query in proof.reproduce if query.title == "Score components")
    decision_query = next(query for query in proof.reproduce if query.title == "Decision inputs")
    evidence_query = next(query for query in proof.reproduce if query.title == "Evidence rows")

    assert "fn_lead_score" in score_query.sql
    assert "recomputed_opportunity_score" in score_query.sql
    assert "recomputed_signal_strength" in score_query.sql
    assert "fn_next_best_offer" in decision_query.sql
    assert "recomputed_offer_code" in decision_query.sql
    assert "source_table" not in evidence_query.sql
