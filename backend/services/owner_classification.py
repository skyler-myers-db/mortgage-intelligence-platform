"""Owner-entity classification for silver.property_owners (S1.1 multi-owner).

Single source of truth for the owner_entity_type / resolution_confidence
contract shared by three code paths:

    * sql/transformations/silver_property_owners.sql  (warehouse MERGE)
    * pipelines/lakeflow/mip_feature_pipeline.py      (live DLT path)
    * this module                                      (Python mirror, pinned
                                                        by tests/fixtures/
                                                        owner_entity_type_golden.json)

tests/unit/test_property_owners.py asserts the regex patterns and confidence
literals below appear verbatim in both SQL/DLT files, so a threshold or
pattern change must land in all three or CI fails.

SCOPE (ROADMAP-TEMPORARY): this is a name/indicator CLASSIFIER, not entity
resolution. It buckets each Cotality owner slot into
{individual, trust, llc, unresolved} from the owner name string, the Y/N
corporate indicator, the slot-1 original trust name, and Owner Link presence.
Cotality entity resolution is work-in-progress upstream; when it ships, the
`unresolved` bucket shrinks to true resolution failures and this classifier
is replaced by mastered entity types. Classify + caveat + suppress is the
current ceiling by design, not a product limit.

PII posture: raw owner names are consumed transiently at classification time
(silver ingest) and never persisted; silver lands only the salted
owner_name_hash, the classification, and the opaque Owner Link.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

OwnerEntityType = Literal["individual", "trust", "llc", "unresolved"]

# Max owner slots carried by entrada_eval_property_domain_v3
# (owner_1..owner_4 name / corporate-indicator / identifier column families).
MAX_OWNER_SLOTS = 4

# Applied against UPPER(TRIM(name)). Kept Java-regex compatible so the same
# pattern string works in Python `re`, Spark `rlike`, and SQL RLIKE.
TRUST_NAME_PATTERN = r"\b(TRUST|TRUSTEE|TRUSTEES|TRST|REVOCABLE|IRREVOCABLE)\b|\bTR\s*$"
LLC_NAME_PATTERN = (
    r"\b(LLC|L L C|LC|LTD|LIMITED|INC|INCORPORATED|CORP|CORPORATION|COMPANY"
    r"|LP|LLP|LLLP|PARTNERSHIP|PARTNERS|HOLDINGS|VENTURES|PROPERTIES"
    r"|ENTERPRISES|INVESTMENTS)\b"
)

# Resolution confidence per classification branch. Deterministic literals,
# pinned by the golden fixture; the SQL/DLT parity test asserts each value
# appears in both data-plane files.
CONFIDENCE_TRUST_NAME_COLUMN = 0.95   # owner_1_original_trust_name populated
CONFIDENCE_TRUST_NAME_PATTERN = 0.90  # name matches TRUST_NAME_PATTERN
CONFIDENCE_LLC_PATTERN_AND_FLAG = 0.95  # LLC pattern + corporate indicator Y
CONFIDENCE_LLC_PATTERN_ONLY = 0.85    # LLC pattern, indicator not Y
CONFIDENCE_LLC_FLAG_ONLY = 0.60       # corporate Y, no entity pattern in name
CONFIDENCE_INDIVIDUAL = 0.90          # personal name + Owner Link present
CONFIDENCE_UNRESOLVED_NO_LINK = 0.40  # name present, no Owner Link
CONFIDENCE_UNRESOLVED_NO_NAME = 0.50  # Owner Link present, name blank

_TRUST_RE = re.compile(TRUST_NAME_PATTERN)
_LLC_RE = re.compile(LLC_NAME_PATTERN)


@dataclass(frozen=True)
class OwnerClassification:
    entity_type: OwnerEntityType
    resolution_confidence: float

    @property
    def is_contact_eligible(self) -> bool:
        """Unresolved owners are excluded from every contact-eligible
        population (gold_borrower_360 fails marketing_eligible closed and
        stamps suppression_reason = 'unresolved_owner')."""
        return self.entity_type != "unresolved"


@dataclass(frozen=True)
class PropertyOwnerRow:
    """One silver.property_owners row (raw name already consumed/dropped)."""

    owner_position: int
    owner_link_id: str | None
    entity_type: OwnerEntityType
    resolution_confidence: float
    is_corporate_indicator: bool

    @property
    def is_contact_eligible(self) -> bool:
        return self.entity_type != "unresolved"


def _blank(value: object) -> bool:
    return value is None or not str(value).strip()


def classify_owner(
    full_name: str | None,
    corporate_indicator: str | None,
    trust_name: str | None,
    owner_identifier: str | None,
) -> OwnerClassification | None:
    """Classify one owner slot. Returns None when the slot is vacant.

    Branch order is the contract — it must match the CASE expression in
    sql/transformations/silver_property_owners.sql and the when-chain in
    pipelines/lakeflow/mip_feature_pipeline.py exactly:

    1. vacant slot (no name, no trust name, no Owner Link)  -> no row
    2. original trust name populated                        -> trust  0.95
    3. name matches TRUST_NAME_PATTERN                      -> trust  0.90
    4. name matches LLC_NAME_PATTERN, indicator Y           -> llc    0.95
    5. name matches LLC_NAME_PATTERN, indicator not Y       -> llc    0.85
    6. corporate indicator Y, no entity pattern             -> llc    0.60
    7. name + Owner Link present                            -> individual 0.90
    8. name present, Owner Link missing                     -> unresolved 0.40
    9. Owner Link present, name blank                       -> unresolved 0.50
    """
    has_name = not _blank(full_name)
    has_trust_name = not _blank(trust_name)
    has_link = not _blank(owner_identifier)
    if not (has_name or has_trust_name or has_link):
        return None

    name = str(full_name or "").strip().upper()
    is_corporate = str(corporate_indicator or "").strip().upper() == "Y"

    if has_trust_name:
        return OwnerClassification("trust", CONFIDENCE_TRUST_NAME_COLUMN)
    if has_name and _TRUST_RE.search(name):
        return OwnerClassification("trust", CONFIDENCE_TRUST_NAME_PATTERN)
    if has_name and _LLC_RE.search(name):
        if is_corporate:
            return OwnerClassification("llc", CONFIDENCE_LLC_PATTERN_AND_FLAG)
        return OwnerClassification("llc", CONFIDENCE_LLC_PATTERN_ONLY)
    if is_corporate:
        return OwnerClassification("llc", CONFIDENCE_LLC_FLAG_ONLY)
    if has_name and has_link:
        return OwnerClassification("individual", CONFIDENCE_INDIVIDUAL)
    if has_name:
        return OwnerClassification("unresolved", CONFIDENCE_UNRESOLVED_NO_LINK)
    return OwnerClassification("unresolved", CONFIDENCE_UNRESOLVED_NO_NAME)


def build_property_owner_rows(
    record: Mapping[str, object],
) -> list[PropertyOwnerRow]:
    """Explode one property-domain source record into owner rows.

    Mirrors the LATERAL VIEW INLINE explode in
    sql/transformations/silver_property_owners.sql: one row per occupied
    owner slot (max 4), duplicate Owner Links collapsed to the lowest slot
    so resolved owners keep the one-row-per-(clip, owner_link) grain.
    """
    rows: list[PropertyOwnerRow] = []
    seen_links: set[str] = set()
    for position in range(1, MAX_OWNER_SLOTS + 1):
        full_name = record.get(f"owner_{position}_full_name")
        corporate = record.get(f"owner_{position}_corporate_indicator")
        identifier = record.get(f"owner_{position}_identifier")
        trust_name = (
            record.get("owner_1_original_trust_name") if position == 1 else None
        )
        classification = classify_owner(
            None if full_name is None else str(full_name),
            None if corporate is None else str(corporate),
            None if trust_name is None else str(trust_name),
            None if identifier is None else str(identifier),
        )
        if classification is None:
            continue
        link = None if _blank(identifier) else str(identifier).strip()
        if link is not None:
            if link in seen_links:
                continue
            seen_links.add(link)
        rows.append(
            PropertyOwnerRow(
                owner_position=position,
                owner_link_id=link,
                entity_type=classification.entity_type,
                resolution_confidence=classification.resolution_confidence,
                is_corporate_indicator=(
                    str(corporate or "").strip().upper() == "Y"
                ),
            )
        )
    return rows
