"""Fixed-template policy for borrower proof SQL.

The proof layer exposes copyable SQL so a technical reviewer can reproduce a
borrower recommendation against governed Unity Catalog assets. That surface is
useful only if it is mechanically safe: fixed templates, SELECT-only, no raw PII
columns, no wildcard projections, and only approved gold/UDF assets.
"""

from __future__ import annotations

import hashlib
import re

from backend.services.databricks_sql_helpers import qualify

_FORBIDDEN_SQL_TOKENS: tuple[str, ...] = (
    ";",
    "--",
    "/*",
    "*/",
)
_FORBIDDEN_SQL_WORDS = re.compile(
    r"\b(ALTER|CREATE|DELETE|DROP|GRANT|INSERT|MERGE|REPLACE|TRUNCATE|UPDATE|USE)\b",
    re.IGNORECASE,
)
_FORBIDDEN_COLUMNS = re.compile(
    r"\b(owner_name_hash|owner_1_full_name|owner_full_name|owner_link_id|"
    r"situs_street_address|mailing_street_address|mailing_city|mailing_state|"
    r"raw_lender|current_servicer|source_table|email|phone)\b",
    re.IGNORECASE,
)
_RELATION_RE = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){2})", re.IGNORECASE)


def borrower_proof_assets() -> list[str]:
    """Return the bounded UC asset set approved for borrower proof payloads."""

    return [
        qualify("gold", "borrower_dossier"),
        qualify("gold", "lead_scores"),
        qualify("gold", "evidence_events"),
        qualify("gold", "fn_lead_score"),
        qualify("gold", "fn_bounded_mortgage_rate"),
        qualify("gold", "fn_estimated_upb"),
        qualify("gold", "fn_estimated_upb_confidence_band"),
        qualify("gold", "fn_rate_spread"),
        qualify("gold", "fn_in_the_money"),
        qualify("gold", "fn_next_best_offer"),
    ]


def borrower_proof_relations() -> set[str]:
    """Return approved table/view relations for proof SQL FROM/JOIN clauses."""

    return {
        qualify("gold", "borrower_dossier"),
        qualify("gold", "lead_scores"),
        qualify("gold", "evidence_events"),
    }


def hash_sql(sql: str) -> str:
    """Return a short stable hash for audit metadata."""

    return hashlib.sha256(sql.encode("utf-8")).hexdigest()[:16]


def validate_borrower_proof_sql(sql: str) -> str:
    """Return ``sql`` if it satisfies the fixed proof SQL policy."""

    text = str(sql).strip()
    upper = text.upper()
    if not (upper.startswith("SELECT ") or upper.startswith("WITH ")):
        raise ValueError("proof SQL must start with SELECT or WITH")
    if any(token in text for token in _FORBIDDEN_SQL_TOKENS):
        raise ValueError("proof SQL must not contain comments or semicolons")
    if re.search(r"SELECT\s+\*", text, re.IGNORECASE):
        raise ValueError("proof SQL must not use SELECT *")
    if _FORBIDDEN_SQL_WORDS.search(text):
        raise ValueError("proof SQL must be read-only")
    if _FORBIDDEN_COLUMNS.search(text):
        raise ValueError("proof SQL references a forbidden PII-adjacent column")

    approved_relations = borrower_proof_relations()
    referenced = {match.group(1) for match in _RELATION_RE.finditer(text)}
    if not referenced:
        raise ValueError("proof SQL must reference an approved UC relation")
    disallowed = referenced - approved_relations
    if disallowed:
        raise ValueError("proof SQL references unapproved relations: " + ", ".join(sorted(disallowed)))
    return text
