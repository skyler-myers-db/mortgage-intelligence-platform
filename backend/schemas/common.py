from pydantic import BaseModel, Field


class EvidenceEvent(BaseModel):
    evidence_id: str
    source_product: str
    source_table: str
    signal_type: str
    signal_value: str
    display_text: str
    confidence: float = Field(ge=0, le=1)
    timestamp: str
