"""No-refusal battery: valid analytical questions must never dead-end.

The product's central promise is an agent that does deep borrower analysis on
demand. This battery pins two systemic invariants across a representative
family of VALID questions:

1. **Prompt guards never intercept them.** The deterministic pre-Genie
   matchers (protected-class, PII lookup, off-topic, scope-bypass,
   instruction-override, cross-lender) must all pass valid analytics
   questions through to the live path.

2. **A policy-trusted SQL turn never refuses.** Whatever the model narrative
   does — clean, guard-flagged, or carrying unverifiable numbers — once the
   turn holds trusted SQL over trusted assets, the governed rows ship. Only
   the prose is conditionally withheld (and disclosed). ``policy_blocked``
   remains reserved for unsafe SQL, missing data, and pending-feed turns.

If a future guard or matcher change breaks either invariant for any question
here, this file fails before a customer sees a "Governed refusal".
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.services.genie_client import GenieResponse
from backend.services.genie_message_policy import (
    identity_prompt_match,
    protected_prompt_match,
)
from backend.services.genie_prompt_guardrails import (
    cross_lender_prompt_match,
    instruction_override_prompt_match,
    off_topic_prompt_match,
    pii_prompt_match,
    scope_bypass_prompt_match,
)
from backend.services.repositories.databricks_repo import _adapt_genie_response

VALID_ANALYTICAL_QUESTIONS: tuple[str, ...] = (
    # The essence question from the product demo.
    "What are the top borrower candidates across all segments overall, what makes "
    "them such good candidates exactly (for each one), and what is the exact offer "
    "we should make to each and why?",
    "Show the top borrowers across all segments and explain why each one is a good candidate.",
    "Which borrowers should we prioritize overall for any offer, and why?",
    "Show me the top 10 borrowers by lead score across the current Cotality data coverage.",
    "Which zips have the most in-the-money refi candidates?",
    "Which state has the most cash-out opportunity right now?",
    "How many borrowers are currently in-the-money and what is the average rate spread?",
    "Compare mean lead score by MSA for our top five markets.",
    "Which borrowers on our retention list have a competitor lien filed in the last 30 days?",
    "Break down the Investor / Multi-Property segment by state and average current rate.",
    "Where should we spend our next 10,000 outreach touches this week, and why?",
)

_PROMPT_MATCHERS = (
    ("protected", protected_prompt_match),
    ("identity", identity_prompt_match),
    ("pii", pii_prompt_match),
    ("off_topic", off_topic_prompt_match),
    ("scope_bypass", scope_bypass_prompt_match),
    ("instruction_override", instruction_override_prompt_match),
    ("cross_lender", cross_lender_prompt_match),
)


@pytest.mark.parametrize("question", VALID_ANALYTICAL_QUESTIONS)
def test_prompt_guards_pass_valid_analytical_questions_through(question: str) -> None:
    intercepted = [name for name, matcher in _PROMPT_MATCHERS if matcher(question)]
    assert intercepted == [], f"prompt guard(s) {intercepted} intercepted: {question!r}"


_TRUSTED_TURN_ROWS: list[dict[str, Any]] = [
    {"state": "IL", "borrowers": 70939},
    {"state": "TX", "borrowers": 54210},
]

_NARRATIVE_VARIANTS: tuple[tuple[str, str], ...] = (
    ("clean", "IL leads the returned cohort ahead of TX."),
    # Output-guard flag (a real name) — prose withheld, data ships.
    ("guard_flagged", "Call John Smith about the IL cohort."),
    # Unverifiable numeric claim — prose withheld, data ships.
    ("claims_mismatch", "There are 999,999 borrowers in the returned cohort."),
    # No narrative at all — data still ships.
    ("empty", ""),
)


@pytest.mark.parametrize("variant_name,narrative", _NARRATIVE_VARIANTS)
@pytest.mark.parametrize("question", VALID_ANALYTICAL_QUESTIONS)
def test_trusted_sql_turn_never_refuses(question: str, variant_name: str, narrative: str) -> None:
    live = GenieResponse(
        answer_text=narrative,
        sql_query=(
            "SELECT state, COUNT(*) AS borrowers FROM mip.gold.borrower_360 "
            "WHERE marketing_eligible = TRUE GROUP BY state ORDER BY borrowers DESC"
        ),
        sql_result_rows=list(_TRUSTED_TURN_ROWS),
        conversation_id="conv-battery",
        message_id=f"msg-battery-{variant_name}",
    )

    result = _adapt_genie_response(question, live, sql_client=None)

    assert result.source != "policy_blocked", (
        f"{variant_name!r} narrative refused a trusted-SQL turn for: {question!r}"
    )
    assert result.table_rows, "governed rows must always ship on a trusted turn"
    if variant_name == "guard_flagged":
        assert "John Smith" not in result.answer
    if variant_name == "claims_mismatch":
        assert "999,999" not in result.answer
