"""Borrower proof contracts for reproducible, governed explanations.

These schemas power the lazy `/api/v1/borrowers/{id}/proof` route. The
payload is intentionally display-safe: public borrower ids, masked evidence
rows, deterministic arithmetic, and copyable SQL against governed Unity
Catalog objects. It never exposes raw CLIP, owner names, street addresses, or
raw lender strings.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ScoreComponentKey = Literal[
    "economic_incentive",
    "intent_trigger",
    "fit",
    "relationship",
    "evidence",
]


class ProofFormulaLine(BaseModel):
    label: str
    expression: str
    result: str
    source: str | None = None


class ProofScoreComponent(BaseModel):
    key: ScoreComponentKey
    label: str
    value: int = Field(ge=0, le=100)
    weight: float = Field(gt=0, le=1)
    weighted_points: float = Field(ge=0, le=100)
    explanation: str
    source_fields: list[str] = []
    fair_lending_note: str | None = None


class ProofOfferBranch(BaseModel):
    code: str
    label: str
    passed: bool
    selected: bool = False
    reason: str


class ProofReproduceQuery(BaseModel):
    title: str
    sql: str
    sql_hash: str
    note: str
    databricks_sql_url: str | None = None


class ProofEvidenceEvent(BaseModel):
    evidence_id: str
    source_product: str
    signal_type: str
    signal_value: str
    display_text: str
    confidence: float = Field(ge=0, le=1)
    timestamp: str


class BorrowerProof(BaseModel):
    borrower_id: str
    trusted: bool = True
    known_data_gaps: list[str] = []
    generated_from: str
    source_refresh_at: str | None = None
    opportunity_score: int = Field(ge=0, le=100)
    signal_strength: int = Field(ge=0, le=100)
    signal_strength_note: str
    evidence_confidence_note: str
    score_components: list[ProofScoreComponent]
    score_formula: ProofFormulaLine
    signal_strength_formula: ProofFormulaLine
    rate_spread_formula: ProofFormulaLine
    equity_formula: ProofFormulaLine
    ltv_formula: ProofFormulaLine
    offer_code: str
    offer_label: str
    offer_branches: list[ProofOfferBranch]
    evidence_rows: list[ProofEvidenceEvent]
    source_assets: list[str]
    reproduce: list[ProofReproduceQuery]
