"""Trusted SQL policy parsing helpers for Genie responses."""

from __future__ import annotations

import re

from backend.services.pii_redaction import _FORBIDDEN_OUTPUT_KEYS

_SQL_IDENT_RE = r"(?:`[^`]+`|[a-zA-Z_][a-zA-Z0-9_]*)"


def _scrub_sql_for_policy(sql: str | None) -> str | None:
    """Remove literals and reject SQL shapes the proof gate cannot trust.

    Genie is allowed to generate one read-only statement over curated UC
    assets. Comments, semicolons, and double-quoted identifiers are rejected
    fail-closed: they are unnecessary for Module 0 questions and make a
    regex-backed verifier easy to spoof.
    """
    if not sql:
        return None
    # One trailing statement terminator is benign and common in generated
    # SQL (live capture 2026-08-06: Genie's valid single statements often end
    # in ";" and were rejected wholesale). Interior semicolons — the actual
    # multi-statement smuggling vector — still reject below.
    trimmed = sql.strip()
    if trimmed.endswith(";"):
        trimmed = trimmed[:-1]
    sql = trimmed
    out: list[str] = []
    i = 0
    while i < len(sql):
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < len(sql) else ""
        if ch == ";":
            return None
        if ch == "-" and nxt == "-":
            return None
        if ch == "/" and nxt == "*":
            return None
        if ch == '"':
            return None
        if ch == "'":
            out.append(" '' ")
            i += 1
            while i < len(sql):
                if sql[i] == "'" and i + 1 < len(sql) and sql[i + 1] == "'":
                    i += 2
                    continue
                if sql[i] == "'":
                    i += 1
                    break
                i += 1
            continue
        if ch == "`":
            start = i
            i += 1
            while i < len(sql) and sql[i] != "`":
                i += 1
            if i >= len(sql):
                return None
            out.append(sql[start : i + 1])
            i += 1
            continue
        out.append(ch)
        i += 1
    scrubbed = "".join(out).strip()
    return scrubbed or None


def _normalise_sql_ref(ref: str) -> str:
    parts = [part.strip().strip("`").lower() for part in ref.split(".")]
    return ".".join(part for part in parts if part)


_GENIE_PII_SQL_COLUMNS: frozenset[str] = frozenset(
    {
        *_FORBIDDEN_OUTPUT_KEYS,
        "owner_link_id",
        "owner_name",
        "owner_names",
        "owner_full_name",
        "owner_name_hash",
        "primary_owner",
        "raw_clip",
        "street_address",
        "site_address",
        "mailing_address",
        "tax_mailing_address",
        "subject_property",
        "owner_email",
        "borrower_email",
        "email",
        "phone",
        "phone_number",
        "ssn",
    }
)


_RAW_IDENTIFIER_SQL_LITERAL_RE = re.compile(
    rf"(?<![A-Za-z0-9_])(?:{_SQL_IDENT_RE}\s*\.\s*)?"
    r"`?(?:clip|raw_clip|owner_link_id|owner_1_identifier|borrower_identifier)`?"
    r"\s*(?:"
    r"=\s*(?:CAST\s*\(\s*)?(?:'[^']*[0-9][^']{7,}'|[0-9]{8,})"
    r"|IN\s*\([^)]*(?:'[^']*[0-9][^']{7,}'|[0-9]{8,})"
    r")",
    re.IGNORECASE,
)
_LONG_RAW_IDENTIFIER_LITERAL_RE = re.compile(
    r"(?:'[^']*[0-9][^']{7,}'|[0-9]{8,})",
    re.IGNORECASE,
)
_SENSITIVE_SQL_IDENTIFIER_RE = re.compile(
    rf"(?<![A-Za-z0-9_])(?:{_SQL_IDENT_RE}\s*\.\s*)?"
    r"`?(?:clip|raw_clip|owner_link_id|owner_1_identifier|borrower_identifier)`?"
    r"(?![A-Za-z0-9_])",
    re.IGNORECASE,
)


def _sql_has_raw_identifier_literal(sql: str) -> bool:
    if _RAW_IDENTIFIER_SQL_LITERAL_RE.search(sql):
        return True
    return bool(
        _LONG_RAW_IDENTIFIER_LITERAL_RE.search(sql) and _SENSITIVE_SQL_IDENTIFIER_RE.search(sql)
    )


def _projection_has_wildcard_expansion(term: str) -> bool:
    compact = re.sub(
        r"\s*\.\s*",
        ".",
        re.sub(r"\s+", " ", term.replace("`", "")).strip(),
    ).lower()
    compact = re.sub(
        r"\b(?:count|approx_count_distinct)\s*\(\s*\*\s*\)",
        "safe_count_star()",
        compact,
        flags=re.IGNORECASE,
    )
    if re.fullmatch(
        r"safe_count_star\(\)(?:\s+as\s+[a-z_][a-z0-9_]*)?",
        compact,
    ):
        return False
    if compact.startswith("*") or re.match(r"^(?:[a-z_][a-z0-9_]*\.)+\*", compact):
        return True
    for match in re.finditer(r"\*", compact):
        before = compact[: match.start()].rstrip()
        after = compact[match.end() :].lstrip()
        previous = before[-1:] if before else ""
        next_char = after[:1]
        if previous in {"(", "."} or next_char in {"", ")", ","} or after.startswith("except"):
            return True
    return False


def _sql_has_wildcard_expansion(sql: str) -> bool:
    compact = re.sub(
        r"\s*\.\s*",
        ".",
        re.sub(r"\s+", " ", sql.replace("`", "")).strip(),
    ).lower()
    compact = re.sub(
        r"\b(?:count|approx_count_distinct)\s*\(\s*\*\s*\)",
        "safe_count_star()",
        compact,
        flags=re.IGNORECASE,
    )
    if re.search(r"\bselect\s+\*", compact) or re.search(r",\s*\*", compact):
        return True
    for match in re.finditer(r"\*", compact):
        before = compact[: match.start()].rstrip()
        after = compact[match.end() :].lstrip()
        previous = before[-1:] if before else ""
        next_char = after[:1]
        if previous in {"(", ".", ","} or next_char in {"", ")", ","} or after.startswith("except"):
            return True
    return False


def _cte_names(sql: str) -> set[str]:
    names: set[str] = set()
    for match in re.finditer(
        rf"(?:\bwith\b|,)\s+({_SQL_IDENT_RE})\s+as\s*\(",
        sql,
        flags=re.IGNORECASE,
    ):
        names.add(match.group(1).strip("`").lower())
    return names


def _split_top_level_commas(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    in_quote: str | None = None
    index = 0
    while index < len(text):
        char = text[index]
        if in_quote:
            if char == in_quote:
                in_quote = None
            index += 1
            continue
        if char in {"'", '"', "`"}:
            in_quote = char
        elif char == "(":
            depth += 1
        elif char == ")" and depth > 0:
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index])
            start = index + 1
        index += 1
    parts.append(text[start:])
    return parts


def _iter_from_clause_segments(sql: str) -> list[str]:
    clause_boundary = re.compile(
        r"\b(?:where|group\s+by|order\s+by|having|limit|qualify|union|intersect|except)\b",
        flags=re.IGNORECASE,
    )
    segments: list[str] = []
    for match in re.finditer(r"\bfrom\b", sql, flags=re.IGNORECASE):
        start = match.end()
        depth = 0
        end = len(sql)
        index = start
        while index < len(sql):
            char = sql[index]
            if char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    end = index
                    break
                depth -= 1
            if depth == 0:
                boundary = clause_boundary.match(sql, index)
                if boundary is not None:
                    end = index
                    break
            index += 1
        segments.append(sql[start:end])
    return segments


def _relation_refs(sql: str) -> list[str]:
    seen: list[str] = []
    relation_pattern = re.compile(
        rf"\b(?:from|join)\s+({_SQL_IDENT_RE}(?:\s*\.\s*{_SQL_IDENT_RE}){{0,2}})(?!\s*\.)",
        flags=re.IGNORECASE,
    )
    parenthesized_relation_pattern = re.compile(
        rf"\b(?:from|join)\s*\(\s*({_SQL_IDENT_RE}(?:\s*\.\s*{_SQL_IDENT_RE}){{0,2}})\s*\)",
        flags=re.IGNORECASE,
    )
    for match in relation_pattern.finditer(sql):
        ref = _normalise_sql_ref(match.group(1))
        if ref and ref not in seen:
            seen.append(ref)
    for match in parenthesized_relation_pattern.finditer(sql):
        ref = _normalise_sql_ref(match.group(1))
        if ref and ref not in seen:
            seen.append(ref)

    leading_relation = re.compile(
        rf"^\s*({_SQL_IDENT_RE}(?:\s*\.\s*{_SQL_IDENT_RE}){{0,2}})(?!\s*\.)",
        flags=re.IGNORECASE,
    )
    parenthesized_leading_relation = re.compile(
        rf"^\s*\(\s*({_SQL_IDENT_RE}(?:\s*\.\s*{_SQL_IDENT_RE}){{0,2}})\s*\)",
        flags=re.IGNORECASE,
    )
    for segment in _iter_from_clause_segments(sql):
        for part in _split_top_level_commas(segment):
            stripped = part.lstrip()
            if re.match(r"^\(\s*(?:select|with)\b", stripped, flags=re.IGNORECASE):
                continue
            match = leading_relation.match(part) or parenthesized_leading_relation.match(part)
            if match is None:
                continue
            ref = _normalise_sql_ref(match.group(1))
            if ref and ref not in seen:
                seen.append(ref)
    return seen


def _sql_has_unqualified_relations(sql: str) -> bool:
    ctes = _cte_names(sql)
    for ref in _relation_refs(sql):
        parts = ref.split(".")
        if len(parts) == 1 and parts[0] not in ctes:
            return True
    return False


def _sql_mentions_pii_columns(sql: str | None) -> bool:
    if sql and _sql_has_raw_identifier_literal(sql):
        return True
    scrubbed = _scrub_sql_for_policy(sql)
    if not scrubbed:
        return False
    if _sql_has_wildcard_expansion(scrubbed):
        return True
    select_match = re.search(
        r"\bselect\b(?P<select>.*?)\bfrom\b", scrubbed, re.IGNORECASE | re.DOTALL
    )
    if select_match is not None:
        select_list = select_match.group("select")
        projected_terms = [term.strip() for term in _split_top_level_commas(select_list)]
        for term in projected_terms:
            if _projection_has_wildcard_expansion(term):
                return True
            if re.search(
                r"(?<![A-Za-z0-9_])(?:[A-Za-z_][A-Za-z0-9_]*\.)?clip(?![A-Za-z0-9_])", term
            ) and not re.search(r"\b(count|approx_count_distinct)\s*\(", term, flags=re.IGNORECASE):
                return True
    normalised = re.sub(r"[^a-z0-9_]+", " ", scrubbed.lower())
    tokens = set(normalised.split())
    return bool(tokens & _GENIE_PII_SQL_COLUMNS)


def _extract_asset_refs(sql: str | None) -> list[str]:
    """Pull three-part UC references out of a SQL string.

    Best-effort, but intentionally returns every unique reference it
    sees. UI callers can choose how many chips to render; trust
    enforcement must evaluate the whole query.
    """
    if not sql:
        return []
    policy_sql = _scrub_sql_for_policy(sql)
    if not policy_sql:
        return []

    seen: list[str] = []
    for ref in _relation_refs(policy_sql):
        if ref and len(ref.split(".")) >= 2 and ref not in seen:
            seen.append(ref)
    return seen
