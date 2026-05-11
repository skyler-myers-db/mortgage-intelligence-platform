import re

from pydantic import BaseModel, Field

PUBLIC_BORROWER_ID_PATTERN = re.compile(r"^B-[A-Za-z0-9][A-Za-z0-9_-]{0,126}$")


def validate_public_borrower_id(value: str) -> str:
    """Return a normalized public borrower id or raise ``ValueError``.

    Raw Cotality identifiers, numeric IDs, and blank values must not be accepted
    on state-changing API contracts. Public borrower identifiers are generated
    by the app boundary and always use the ``B-`` prefix.
    """

    borrower_id = str(value).strip()
    if not PUBLIC_BORROWER_ID_PATTERN.fullmatch(borrower_id):
        raise ValueError("borrower_id must be an app-scoped public borrower id")
    return borrower_id


class EvidenceEvent(BaseModel):
    evidence_id: str
    source_product: str
    source_table: str
    signal_type: str
    signal_value: str
    display_text: str
    confidence: float = Field(ge=0, le=1)
    timestamp: str
