from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/genie", tags=["genie"])


class GenieMessageRequest(BaseModel):
    question: str
    conversation_id: str | None = None


class GenieMessageResponse(BaseModel):
    conversation_id: str
    question: str
    answer: str
    source: str
    trusted_assets: list[str]


_FALLBACK_ANSWERS: dict[str, GenieMessageResponse] = {
    "in_the_money": GenieMessageResponse(
        conversation_id="demo-conv",
        question="",
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
    ),
    "heloc": GenieMessageResponse(
        conversation_id="demo-conv",
        question="",
        answer=(
            "4,108 properties show a recent permit trigger paired with HELOC-qualifying equity. "
            "Average estimated equity is $228K; top ZIPs are 30305 and 78704."
        ),
        source="borrower_opportunity_metric_view",
        trusted_assets=[
            "mip_demo.gold.borrower_360",
            "mip_demo.gold.evidence_events",
        ],
    ),
    "retention": GenieMessageResponse(
        conversation_id="demo-conv",
        question="",
        answer=(
            "3,471 current customers show refinance, listing, or competitor-lien signals in the "
            "last 30 days. Retention risk average score is 88."
        ),
        source="segment_performance_metric_view",
        trusted_assets=[
            "mip_demo.gold.lead_segment_membership",
            "mip_demo.gold.recommended_offers",
        ],
    ),
}


def _match(question: str) -> GenieMessageResponse:
    q = question.lower()
    if any(token in q for token in ("in the money", "itm", "rate spread")):
        return _FALLBACK_ANSWERS["in_the_money"]
    if any(token in q for token in ("heloc", "permit", "equity")):
        return _FALLBACK_ANSWERS["heloc"]
    if any(token in q for token in ("retention", "competitor", "former customer")):
        return _FALLBACK_ANSWERS["retention"]
    return GenieMessageResponse(
        conversation_id="demo-conv",
        question=question,
        answer=(
            "Genie is unavailable in demo mode; the curated Module 0 metric views show "
            "In-the-Money and Home Equity segments as the highest-value opportunities."
        ),
        source="deterministic_fallback",
        trusted_assets=["mip_demo.gold.lead_population"],
    )


@router.post("/start")
def genie_start(payload: dict[str, object] | None = None) -> dict[str, object]:
    _ = payload
    return {
        "conversation_id": "demo-conv",
        "trusted_assets": [
            "mip_demo.gold.lead_population",
            "mip_demo.gold.lead_segment_membership",
            "mip_demo.semantics.lead_generation_metric_view",
        ],
    }


@router.post("/message", response_model=GenieMessageResponse)
def genie_message(payload: GenieMessageRequest) -> GenieMessageResponse:
    response = _match(payload.question)
    return response.model_copy(update={"question": payload.question})
