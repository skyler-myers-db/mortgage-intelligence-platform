"""Deterministic Genie answer catalog + matcher for Module 0 (DAIS demo).

The booth-demo Genie path is *not* a fallback — it's the main stage. Any
question the audience throws within the Module 0 narrative should return
a polished, evidence-cited answer, not a generic "Genie unavailable"
line. This module holds the curated answer catalog (currently 12 canned
answers covering geography, offer branches, segment comparisons,
borrower lookups, trends, and policy questions) and the scoring-based
matcher.

All responses cite at least one Unity Catalog asset under ``mip_demo``.
Borrower-level answers pull real names and scores from the synthetic
``mock_data.BORROWERS`` population — no fabricated PII. The generic
fallback is the warm last resort and nudges the presenter back onto the
Module 0 narrative rails.

The matcher is intentionally simple and pure (no deps): per-answer
keyword and phrase lists with weighted scoring, optional regex patterns
for high-signal phrases, and a threshold that falls through to the warm
fallback when nothing scores strongly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from backend.services.mock_data import BORROWERS, SEGMENTS


class GenieMessageResponse(BaseModel):
    """Wire contract. Existing fields (conversation_id/question/answer/
    source/trusted_assets) are unchanged. New optional fields are purely
    additive so the current UI keeps working and a later slice can
    surface richer renderings (tables, follow-ups, headline metrics).
    """

    conversation_id: str
    question: str
    answer: str
    source: str
    trusted_assets: list[str]
    # Additive, optional:
    metric_value: str | None = None
    table_rows: list[dict[str, Any]] | None = None
    follow_up_questions: list[str] = []


# ---------------------------------------------------------------------------
# Helpers that read the live synthetic population so borrower-level
# answers cite real rows (never fabricated).
# ---------------------------------------------------------------------------


def _top_n_rows(n: int) -> list[dict[str, Any]]:
    ranked = sorted(BORROWERS, key=lambda b: b.opportunity_score, reverse=True)
    return [
        {
            "borrower_id": b.borrower_id,
            "name": b.display_name,
            "geo": f"{b.city}, {b.state}",
            "score": b.opportunity_score,
            "offer": b.recommended_offer,
        }
        for b in ranked[:n]
    ]


def _austin_rows() -> list[dict[str, Any]]:
    return [
        {
            "borrower_id": b.borrower_id,
            "name": b.display_name,
            "zip": b.zip,
            "spread_bps": b.rate_spread_bps,
            "score": b.opportunity_score,
            "offer": b.recommended_offer,
        }
        for b in BORROWERS
        if b.city == "Austin"
    ]


def _segment_row(code: str) -> dict[str, Any]:
    seg = next(s for s in SEGMENTS if s.code == code)
    return {
        "segment": seg.name,
        "count": seg.count,
        "avg_score": seg.avg_score,
        "delta": seg.delta,
    }


# ---------------------------------------------------------------------------
# Answer catalog. One dict per intent; the matcher scores questions
# against the ``phrases`` (weight=3), ``keywords`` (weight=1), and
# ``regexes`` (weight=4) then returns the highest-scoring intent if it
# clears THRESHOLD.
# ---------------------------------------------------------------------------


@dataclass
class Intent:
    key: str
    phrases: list[str] = field(default_factory=list)   # multi-word, stronger
    keywords: list[str] = field(default_factory=list)  # single tokens
    regexes: list[re.Pattern[str]] = field(default_factory=list)


def _answers() -> dict[str, GenieMessageResponse]:
    """Built lazily so tests that touch the catalog get the current
    BORROWERS snapshot. Returns a dict keyed by intent name.
    """
    top10 = _top_n_rows(10)
    austin = _austin_rows()
    itm_zip_rows = [
        {"zip": "30309", "city": "Atlanta, GA", "itm_borrowers": "~1,420"},
        {"zip": "78704", "city": "Austin, TX", "itm_borrowers": "~1,180"},
        {"zip": "94110", "city": "San Francisco, CA", "itm_borrowers": "~960"},
        {"zip": "98103", "city": "Seattle, WA", "itm_borrowers": "~720"},
        {"zip": "30305", "city": "Atlanta, GA", "itm_borrowers": "~640"},
    ]

    return {
        # --- Existing canonical three (unchanged wording) -----------------
        "in_the_money": GenieMessageResponse(
            conversation_id="demo-conv", question="",
            answer=(
                "In the Atlanta MSA, 12,840 borrowers are currently in the money with an average "
                "rate spread of 87 bps above par. The largest concentrations are in 30309, 30305, "
                "and 30324."
            ),
            source="lead_generation_metric_view",
            trusted_assets=[
                "mip_demo.gold.lead_population",
                "mip_demo.gold.lead_segment_membership",
                "mip_demo.gold.lead_scores",
            ],
            metric_value="12,840",
            follow_up_questions=[
                "Which ZIPs have the most in-the-money refi candidates?",
                "Show me the top 10 highest-score borrowers.",
                "Compare Listed for Sale vs Permit Activity.",
            ],
        ),
        "heloc": GenieMessageResponse(
            conversation_id="demo-conv", question="",
            answer=(
                "4,108 properties show a recent permit trigger paired with HELOC-qualifying equity. "
                "Average estimated equity is $228K; top ZIPs are 30305 and 78704."
            ),
            source="borrower_opportunity_metric_view",
            trusted_assets=[
                "mip_demo.gold.borrower_360",
                "mip_demo.gold.evidence_events",
            ],
            metric_value="4,108",
            follow_up_questions=[
                "What if we raised the HELOC equity floor to 50%?",
                "How many borrowers qualify for refi + HELOC cross-sell?",
                "Where is the biggest HELOC opportunity by state?",
            ],
        ),
        "retention": GenieMessageResponse(
            conversation_id="demo-conv", question="",
            answer=(
                "3,471 current customers show refinance, listing, or competitor-lien signals in the "
                "last 30 days. Retention risk average score is 88."
            ),
            source="segment_performance_metric_view",
            trusted_assets=[
                "mip_demo.gold.lead_segment_membership",
                "mip_demo.gold.recommended_offers",
            ],
            metric_value="3,471",
            follow_up_questions=[
                "Which segment converts best?",
                "How many approvals were logged today?",
                "Show me the top 10 highest-score borrowers.",
            ],
        ),

        # --- Geography ---------------------------------------------------
        "itm_zips": GenieMessageResponse(
            conversation_id="demo-conv", question="",
            answer=(
                "The top in-the-money ZIPs are 30309 Atlanta (~1,420 borrowers), 78704 Austin "
                "(~1,180), 94110 San Francisco (~960), 98103 Seattle (~720), and 30305 Atlanta "
                "(~640). Together they cover about 38% of the national ITM book."
            ),
            source="lead_generation_metric_view",
            trusted_assets=[
                "mip_demo.gold.lead_population",
                "mip_demo.semantics.lead_generation_metric_view",
            ],
            table_rows=itm_zip_rows,
            follow_up_questions=[
                "How many in-the-money borrowers in Travis County?",
                "Where is the biggest HELOC opportunity by state?",
                "Who in Austin is in the money?",
            ],
        ),
        "travis_county": GenieMessageResponse(
            conversation_id="demo-conv", question="",
            answer=(
                "Travis County (Austin metro) holds ~1,620 in-the-money borrowers across 78704, "
                "78745, and 78702 — about 12.6% of the national ITM pool. Sample top rows include "
                "Thomas Chen (B-51872, score 85) and Kevin Nakamura (B-55328, score 72)."
            ),
            source="lead_generation_metric_view",
            trusted_assets=[
                "mip_demo.gold.lead_population",
                "mip_demo.gold.lead_scores",
            ],
            metric_value="1,620",
            follow_up_questions=[
                "Who in Austin is in the money?",
                "Which ZIPs have the most in-the-money refi candidates?",
                "Show me the top 10 highest-score borrowers.",
            ],
        ),
        "heloc_by_state": GenieMessageResponse(
            conversation_id="demo-conv", question="",
            answer=(
                "The biggest HELOC opportunities by state: CA (1,185 permit+equity borrowers), "
                "TX (812), GA (604), WA (487), CO (412). California leads both on count and "
                "average equity ($312K)."
            ),
            source="borrower_opportunity_metric_view",
            trusted_assets=[
                "mip_demo.gold.borrower_360",
                "mip_demo.gold.evidence_events",
                "mip_demo.semantics.borrower_opportunity_metric_view",
            ],
            table_rows=[
                {"state": "CA", "permit_equity_borrowers": 1185, "avg_equity": "$312K"},
                {"state": "TX", "permit_equity_borrowers": 812, "avg_equity": "$244K"},
                {"state": "GA", "permit_equity_borrowers": 604, "avg_equity": "$205K"},
                {"state": "WA", "permit_equity_borrowers": 487, "avg_equity": "$296K"},
                {"state": "CO", "permit_equity_borrowers": 412, "avg_equity": "$258K"},
            ],
            follow_up_questions=[
                "What if we raised the HELOC equity floor to 50%?",
                "How many borrowers qualify for refi + HELOC cross-sell?",
                "Where's the biggest permit-activity cluster?",
            ],
        ),

        # --- Offer / branch ---------------------------------------------
        "purchase": GenieMessageResponse(
            conversation_id="demo-conv", question="",
            answer=(
                "2,614 borrowers fall into the Listed for Sale segment — purchase-mortgage "
                "candidates. The top three in the ranked sample are Lisa Thompson (Atlanta, "
                "score 82), Alicia Greenberg (Austin, 75), and Wei Zhang (San Francisco, 75 — "
                "also an existing customer, so retention stays in-house on the next home)."
            ),
            source="segment_performance_metric_view",
            trusted_assets=[
                "mip_demo.gold.lead_segment_membership",
                "mip_demo.gold.recommended_offers",
            ],
            metric_value="2,614",
            table_rows=[
                {"borrower_id": "B-48295", "name": "Lisa Thompson", "geo": "Atlanta, GA", "score": 82},
                {"borrower_id": "B-60284", "name": "Alicia Greenberg", "geo": "Austin, TX", "score": 75},
                {"borrower_id": "B-60517", "name": "Wei Zhang", "geo": "San Francisco, CA", "score": 75},
            ],
            follow_up_questions=[
                "How many borrowers qualify for refi + HELOC cross-sell?",
                "Which segment converts best?",
                "Show me the top 10 highest-score borrowers.",
            ],
        ),
        "refi_plus_heloc": GenieMessageResponse(
            conversation_id="demo-conv", question="",
            answer=(
                "1,842 borrowers clear both the refi spread floor (>= 75 bps) and the HELOC equity "
                "cushion (>= 35%) — the highest-revenue cross-sell branch. James & Maria Rodriguez "
                "(B-48291, score 94), Thomas Chen (B-51872, 85), Maria & Carlos Rivera (B-54103, "
                "85), Priya Natarajan (B-56219, 83), and David Park (B-48294, 87) lead the sample."
            ),
            source="recommended_offers",
            trusted_assets=[
                "mip_demo.gold.recommended_offers",
                "mip_demo.gold.lead_scores",
                "mip_demo.gold.fn_in_the_money",
            ],
            metric_value="1,842",
            follow_up_questions=[
                "What if we raised the HELOC equity floor to 50%?",
                "Compare Listed for Sale vs Permit Activity.",
                "Who in Austin is in the money?",
            ],
        ),

        # --- Segment comparisons ----------------------------------------
        "best_converting": GenieMessageResponse(
            conversation_id="demo-conv", question="",
            answer=(
                "Retention Risk converts best at ~14.8% (avg score 88), driven by existing "
                "relationships. In the Money follows at ~11.2% (avg 82), Home Equity Candidate at "
                "~9.6% (avg 76). Listed for Sale and Permit Activity trail at ~6–7%."
            ),
            source="segment_performance_metric_view",
            trusted_assets=[
                "mip_demo.semantics.segment_performance_metric_view",
                "mip_demo.gold.recommended_offers",
            ],
            table_rows=[
                _segment_row("retention") | {"est_conv": "14.8%"},
                _segment_row("itm") | {"est_conv": "11.2%"},
                _segment_row("equity") | {"est_conv": "9.6%"},
                _segment_row("listed") | {"est_conv": "6.9%"},
                _segment_row("permit") | {"est_conv": "6.2%"},
            ],
            follow_up_questions=[
                "How does Listed for Sale compare to Permit Activity on avg score?",
                "How is our cost per contact trending?",
                "Show me the top 10 highest-score borrowers.",
            ],
        ),
        "listed_vs_permit": GenieMessageResponse(
            conversation_id="demo-conv", question="",
            answer=(
                "Listed for Sale (2,614 borrowers, avg score 74, +9% QoQ) runs just above Permit "
                "Activity (4,108 borrowers, avg score 71, +11%). Permit has more volume; Listed "
                "has the higher avg score because every row has a confirmed intent signal."
            ),
            source="segment_performance_metric_view",
            trusted_assets=[
                "mip_demo.semantics.segment_performance_metric_view",
                "mip_demo.gold.lead_segment_membership",
            ],
            table_rows=[
                _segment_row("listed"),
                _segment_row("permit"),
            ],
            follow_up_questions=[
                "Which segment converts best?",
                "Where is the biggest HELOC opportunity by state?",
                "What's the lift from adding permit data?",
            ],
        ),

        # --- Borrower lookups -------------------------------------------
        "top_borrowers": GenieMessageResponse(
            conversation_id="demo-conv", question="",
            answer=(
                "Top 10 highest-scoring borrowers in the ranked sample: "
                + ", ".join(
                    f"{r['name']} ({r['borrower_id']}, {r['geo']}, score {r['score']})"
                    for r in top10[:5]
                )
                + ", plus five more. Every row has CLIP + Owner Link evidence backing it."
            ),
            source="lead_scores",
            trusted_assets=[
                "mip_demo.gold.lead_scores",
                "mip_demo.gold.borrower_360",
                "mip_demo.gold.evidence_events",
            ],
            table_rows=top10,
            follow_up_questions=[
                "Who in Austin is in the money?",
                "How many borrowers qualify for refi + HELOC cross-sell?",
                "Which segment converts best?",
            ],
        ),
        "austin_itm": GenieMessageResponse(
            conversation_id="demo-conv", question="",
            answer=(
                "Three Austin borrowers appear in the ranked sample. "
                + ", ".join(
                    f"{r['name']} ({r['borrower_id']}, {r['zip']}, spread {r['spread_bps']} bps, "
                    f"score {r['score']}, {r['offer']})"
                    for r in austin
                )
                + "."
            ),
            source="borrower_360",
            trusted_assets=[
                "mip_demo.gold.borrower_360",
                "mip_demo.gold.lead_scores",
                "mip_demo.gold.fn_rate_spread",
            ],
            table_rows=austin,
            follow_up_questions=[
                "How many in-the-money borrowers in Travis County?",
                "Which ZIPs have the most in-the-money refi candidates?",
                "Show me the top 10 highest-score borrowers.",
            ],
        ),

        # --- Trend / pipeline -------------------------------------------
        "cost_per_contact": GenieMessageResponse(
            conversation_id="demo-conv", question="",
            answer=(
                "Cost per contact is trending down: $2.71 six months ago, $2.42 a quarter ago, "
                "$2.18 today — a 19.6% reduction. The driver is tighter segment targeting: "
                "suppressing nurture-only borrowers cut wasted contacts ~30%."
            ),
            source="lead_generation_metric_view",
            trusted_assets=[
                "mip_demo.semantics.lead_generation_metric_view",
                "mip_demo.gold.lead_population",
            ],
            metric_value="$2.18",
            follow_up_questions=[
                "What's the lift from adding permit data?",
                "Which segment converts best?",
                "How many approvals were logged today?",
            ],
        ),
        "permit_lift": GenieMessageResponse(
            conversation_id="demo-conv", question="",
            answer=(
                "Adding Cotality Permits lifts the Permit Activity segment's avg score from 62 to "
                "71 (+14%) and surfaces 4,108 HELOC/cash-out candidates that the rate-spread-only "
                "lane would miss. Estimated incremental funded volume: $112M / quarter."
            ),
            source="segment_performance_metric_view",
            trusted_assets=[
                "mip_demo.gold.evidence_events",
                "mip_demo.semantics.segment_performance_metric_view",
            ],
            metric_value="+14%",
            follow_up_questions=[
                "Where is the biggest HELOC opportunity by state?",
                "Which segment converts best?",
                "How is our cost per contact trending?",
            ],
        ),

        # --- Policy / threshold -----------------------------------------
        "heloc_floor_50": GenieMessageResponse(
            conversation_id="demo-conv", question="",
            answer=(
                "Raising the HELOC equity floor from 35% to 50% tightens the Permit Activity "
                "segment from 4,108 to ~1,640 borrowers (-60%), lifts avg score from 71 to 78, and "
                "cuts estimated funded volume by ~$46M/quarter. It's a precision/recall trade."
            ),
            source="borrower_opportunity_metric_view",
            trusted_assets=[
                "mip_demo.gold.borrower_360",
                "mip_demo.semantics.borrower_opportunity_metric_view",
            ],
            metric_value="~1,640",
            follow_up_questions=[
                "How many borrowers qualify for refi + HELOC cross-sell?",
                "Which segment converts best?",
                "Where is the biggest HELOC opportunity by state?",
            ],
        ),
        "approvals_today": GenieMessageResponse(
            conversation_id="demo-conv", question="",
            answer=(
                "47 outreach drafts were approved today across the demo cohort — all logged to "
                "mip_app.action_audit with actor, timestamp, and borrower_id. Queue still holds "
                "118 pending drafts awaiting human review."
            ),
            source="action_audit",
            trusted_assets=[
                "mip_app.action_audit",
                "mip_app.approvals",
            ],
            metric_value="47",
            follow_up_questions=[
                "Show me the top 10 highest-score borrowers.",
                "Which segment converts best?",
                "How is our cost per contact trending?",
            ],
        ),
    }


# ---------------------------------------------------------------------------
# Matcher — pure, testable, no deps.
# ---------------------------------------------------------------------------


_INTENTS: list[Intent] = [
    # Order matters only as a stable tiebreaker; scoring drives selection.
    Intent(
        key="top_borrowers",
        phrases=["top 10", "top ten", "top borrowers", "highest score", "highest scoring", "highest-scoring"],
        keywords=["top", "ranked", "leaderboard"],
        regexes=[re.compile(r"\btop\s+\d+\b"), re.compile(r"\bhighest[-\s]?scor")],
    ),
    Intent(
        key="austin_itm",
        phrases=["in austin", "austin borrowers", "who in austin"],
        keywords=["austin"],
    ),
    Intent(
        key="travis_county",
        phrases=["travis county", "in travis"],
        keywords=["travis"],
    ),
    Intent(
        key="itm_zips",
        phrases=["which zips", "which zip", "top zips", "zips with the most", "zips have the most", "by zip"],
        keywords=["zip", "zipcode", "zips"],
        regexes=[re.compile(r"\bzip(s|\s*code)?\b.*\bitm|in[- ]?the[- ]?money\b", re.I)],
    ),
    Intent(
        key="heloc_by_state",
        phrases=["by state", "which state", "biggest heloc", "heloc opportunity", "heloc by"],
        keywords=["state", "states"],
        regexes=[re.compile(r"\bheloc\b.*\bstate\b"), re.compile(r"\bstate\b.*\bheloc\b")],
    ),
    Intent(
        key="in_the_money",
        phrases=["in the money", "in-the-money", "rate spread", "how many itm", "itm borrowers"],
        keywords=["itm"],
        regexes=[re.compile(r"\bin[- ]the[- ]money\b")],
    ),
    Intent(
        key="refi_plus_heloc",
        phrases=["refi + heloc", "refi and heloc", "refi plus heloc", "cross-sell", "cross sell", "both refi"],
        keywords=[],
        regexes=[re.compile(r"\brefi\b.*\bheloc\b"), re.compile(r"\bheloc\b.*\brefi\b")],
    ),
    Intent(
        key="heloc",
        phrases=["heloc candidates", "heloc opportunity", "permit activity", "home equity", "permits and equity", "equity and permit"],
        keywords=["heloc", "permit", "permits", "equity"],
    ),
    Intent(
        key="retention",
        phrases=["retention risk", "retention outreach", "competitor lien", "former customer", "keep customers"],
        keywords=["retention", "competitor", "churn", "attrition"],
    ),
    Intent(
        key="purchase",
        phrases=["purchase mortgage", "listed for sale", "purchase candidates", "purchase loans"],
        keywords=["purchase", "listed", "listing", "mls"],
    ),
    Intent(
        key="best_converting",
        phrases=["which segment converts", "best converting", "best conversion", "which segment is best", "highest conversion"],
        keywords=["convert", "converts", "conversion"],
    ),
    Intent(
        key="listed_vs_permit",
        phrases=["listed for sale", "permit activity", "listed vs permit", "compare listed", "compare permit"],
        keywords=["compare"],
        regexes=[re.compile(r"\blisted\b.*\bpermit\b"), re.compile(r"\bpermit\b.*\blisted\b")],
    ),
    Intent(
        key="cost_per_contact",
        phrases=["cost per contact", "cost trending", "contact cost", "cpc trend"],
        keywords=["cpc"],
        regexes=[re.compile(r"\bcost\b.*\bcontact\b")],
    ),
    Intent(
        key="permit_lift",
        phrases=["lift from permit", "permit data lift", "value of permit", "impact of permit"],
        keywords=["lift"],
        regexes=[re.compile(r"\bpermit\b.*\blift\b"), re.compile(r"\blift\b.*\bpermit\b")],
    ),
    Intent(
        key="heloc_floor_50",
        phrases=["equity floor", "heloc floor", "raise the heloc", "raised the heloc", "what if we raised", "threshold to 50"],
        keywords=[],
        regexes=[
            re.compile(r"\bheloc\b.*\b(50|equity|floor|threshold)\b"),
            re.compile(r"\b(what if|if we)\b.*\b(raise|raised|lower|lowered|change|set)\b"),
        ],
    ),
    Intent(
        key="approvals_today",
        phrases=["approvals today", "approved today", "approval count", "how many approvals"],
        keywords=["approvals", "approved"],
        regexes=[re.compile(r"\bhow many\b.*\bapprov")],
    ),
]


# Phrase = 3, regex = 4, keyword = 1. An intent must clear this to be
# returned; otherwise we fall through to the warm generic. Tuned so
# single-keyword hits alone never triumph over a phrase hit but do win
# against the warm fallback when the question is on-topic.
_SCORE_THRESHOLD = 3


def _score(q: str, intent: Intent) -> int:
    score = 0
    for phrase in intent.phrases:
        if phrase in q:
            score += 3
    for kw in intent.keywords:
        # word-boundary token match so "permit" doesn't match "permitted"
        # (we want precise intent signals).
        if re.search(rf"\b{re.escape(kw)}\b", q):
            score += 1
    for pat in intent.regexes:
        if pat.search(q):
            score += 4
    return score


def match_intent(question: str) -> str | None:
    """Return the best intent key above threshold, or ``None`` for the
    warm generic fallback. Pure function — safe to call from tests.
    """
    # Normalize hyphens/en-dashes to spaces so "purchase-mortgage" matches
    # the "purchase mortgage" phrase list, and collapse whitespace.
    q = question.lower().strip()
    q = re.sub(r"[-‐-―_]", " ", q)
    q = re.sub(r"\s+", " ", q)
    if not q:
        return None
    best_key: str | None = None
    best_score = 0
    for intent in _INTENTS:
        s = _score(q, intent)
        if s > best_score:
            best_score = s
            best_key = intent.key
    if best_score >= _SCORE_THRESHOLD:
        return best_key
    return None


def _warm_fallback(question: str) -> GenieMessageResponse:
    return GenieMessageResponse(
        conversation_id="demo-conv",
        question=question,
        answer=(
            "I can answer questions about segments, in-the-money borrowers, Owner Link "
            "relationships, permits, and retention risk — try one of the suggestions on "
            "the right, or ask about a specific ZIP, state, or offer branch."
        ),
        source="deterministic_fallback",
        trusted_assets=["mip_demo.gold.lead_population"],
        follow_up_questions=[
            "Which ZIPs have the most in-the-money refi candidates?",
            "Show me the top 10 highest-score borrowers.",
            "Which segment converts best?",
        ],
    )


def respond(question: str) -> GenieMessageResponse:
    """Top-level entry: match intent, clone the templated answer, stamp
    the caller's question onto the response. If no intent clears the
    threshold, return the warm fallback. Never raises for any input.
    """
    intent = match_intent(question)
    if intent is None:
        return _warm_fallback(question)
    templates = _answers()
    template = templates.get(intent)
    if template is None:
        return _warm_fallback(question)
    return template.model_copy(update={"question": question})
