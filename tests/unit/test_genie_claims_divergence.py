"""Owner ruling O2 (2026-10-06): the Figures metric counts the answer's OWN figures.

``docs/audits/ui-ux-technology-audit-2026-09-21.md`` 12.4 #11 and the W5b owner
rulings: the denominator is the figures in Genie's governed prose; a
deterministic cross-check divergence note is the disclosure layer, not the
answer, so its figures are never counted and the note stays visible as its own
disclosure (in the answer and in Known data gaps). A figure the cross-check
DISPUTES counts as not verified; today the only disputing cross-check is the
count divergence, which withholds the prose and ships no claims, so a disputed
figure can never be counted as verified or shown.

Pinned through the real adapter (``_adapt_genie_response``) with a stub SQL
client for the canonical side, and through ``_sweep_claims`` for a section.
"""

from __future__ import annotations

from typing import Any

from backend.services.genie_client import GenieResponse
from backend.services.repositories.databricks_genie import _adapt_genie_response
from backend.services.repositories.databricks_genie_numeric import _NUMBER_RE
from backend.services.repositories.databricks_genie_sweep import _sweep_claims

_RANKING_QUESTION = "Show me the top 10 borrowers by lead score across the current Cotality data coverage."
_COUNT_QUESTION = "How many in-the-money borrowers are there?"
_SQL = "SELECT borrower_id, lead_score FROM mip.gold.borrower_360 ORDER BY lead_score DESC LIMIT 10"
_ASSETS = ["mip.gold.borrower_360"]
_NOTE = "overlaps 3 of 10 borrowers"


class _StubSql:
    """The canonical side of the cross-check: fixed rows, no warehouse."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def execute(self, statement: str, parameters: Any = None) -> list[dict[str, Any]]:  # noqa: ARG002
        return self.rows

    def execute_one(self, statement: str, parameters: Any = None) -> dict[str, Any] | None:  # noqa: ARG002
        return self.rows[0] if self.rows else None


_LIVE = [{"borrower_id": f"B-{i:013d}", "lead_score": 97 - i} for i in range(10)]
# Three of the canonical top ten are in the live top ten: overlap 3 of 10.
_CANONICAL = [{"borrower_id": f"B-{i:013d}"} for i in range(3)] + [
    {"borrower_id": f"B-{i:013d}"} for i in range(100, 107)
]


def _live_turn(text: str, rows: list[dict[str, Any]] = _LIVE) -> GenieResponse:
    return GenieResponse(
        answer_text=text,
        sql_query=_SQL,
        sql_result_rows=rows,
        conversation_id="conv-o2",
        message_id="msg-o2",
        trusted_assets=list(_ASSETS),
    )


def _tokens(text: str) -> set[str]:
    return {match.group(0).strip()[:32] for match in _NUMBER_RE.finditer(text)}


def _ranking_turn() -> Any:
    return _adapt_genie_response(
        _RANKING_QUESTION,
        _live_turn("The two highest lead scores are 97 and 95."),
        sql_client=_StubSql(_CANONICAL),  # type: ignore[arg-type]
    )


def test_a_divergence_note_is_disclosed_but_never_counted() -> None:
    response = _ranking_turn()

    assert response.proof is not None and response.proof.claims is not None
    claims = response.proof.claims
    assert (claims.verified, claims.total) == (2, 2), (
        "O2: the Figures metric counts Genie's own prose figures only, never the "
        "divergence note's"
    )
    assert [item.token for item in claims.items] == ["97", "95"]
    note_only = _tokens(_NOTE)
    assert note_only == {"3", "10"}
    assert not note_only & {item.token for item in claims.items}
    # The note stays its own disclosure: in the answer and in Known data gaps.
    assert _NOTE in response.answer
    assert any(_NOTE in gap for gap in response.proof.known_data_gaps)


def test_a_sweep_section_counts_only_its_own_prose() -> None:
    section = _ranking_turn()
    assert _NOTE in (section.answer or "")

    claims = _sweep_claims(
        _RANKING_QUESTION,
        [("Who leads the ranking?", section)],
        {"Who leads the ranking?": "Ranking leaders"},
        None,
    )

    assert claims is not None
    assert (claims.verified, claims.total) == (2, 2)
    assert [(item.token, item.section) for item in claims.items] == [
        ("97", "Ranking leaders"),
        ("95", "Ranking leaders"),
    ]


def test_a_count_divergence_ships_no_claims_and_no_disputed_figure() -> None:
    draft = "There are 4,200 in-the-money borrowers, about 46.7% of the governed count."
    response = _adapt_genie_response(
        _COUNT_QUESTION,
        _live_turn(draft, rows=[{"in_the_money_borrowers": 4200}]),
        sql_client=_StubSql([{"in_the_money_borrowers": 9000}]),  # type: ignore[arg-type]
    )

    assert response.proof is not None
    assert response.proof.claims is None, (
        "O2 tripwire: a count divergence disputes the draft's figures, so the prose "
        "is withheld and NO claims ship. If a live-first change ever ships prose "
        "under a disputing cross-check, the disputed figures must join the "
        "denominator as NOT verified and be listed with the divergence (owner "
        "ruling O2, 2026-10-06); never count them as verified."
    )
    assert "4,200" not in response.answer
    assert "46.7%" not in response.answer
    assert any("withheld" in gap for gap in response.proof.known_data_gaps)
