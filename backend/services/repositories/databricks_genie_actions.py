"""Genie governed-action routing helpers.

These helpers translate trusted Genie result rows into auditable UI actions
and Lead Queue routes. They are intentionally pure: no SQL calls, no Lakebase
writes, and no background reads.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlencode

from backend.schemas.common import validate_public_borrower_id
from backend.schemas.lead import GENIE_REPLAY_SEGMENT_CODES
from backend.services.genie_answers import GenieActionSuggestion, GenieVisualizationSpec
from backend.services.genie_sql_predicates import SqlFilterReading, read_sql_filters

# Cohort filter keys `_route_from_answer_rows` can emit that the Lead Queue
# actually replays. A disclosure key rides alongside them but must never be
# the ONLY thing in `result_filters`: `_materialize_genie_cohort` rejects a
# cohort with no replayable filter, so a disclosure-only cohort would offer an
# action that 400s.
_REPLAYABLE_ROUTE_FILTER_KEYS = frozenset(
    {
        "borrower_ids",
        "zips",
        "county",
        "counties",
        "states",
        "segment_codes",
        "segment_mode",
        "portfolio_criteria",
        "min_opportunity_score",
        "min_equity_pct",
        "min_rate_spread_bps",
    }
)

# Reviewed `product` label -> the exact offer-code set the Lead Queue compiles
# it to. Mirrors ``PORTFOLIO_PRODUCT_CODES`` in ``databricks_portfolio`` and is
# pinned to it by ``tests/unit/test_genie_sql_floor_extraction.py``; it is
# duplicated rather than imported so this module stays free of the SQL and
# Lakebase imports that module carries.
_PRODUCT_LABEL_CODES: dict[str, frozenset[str]] = {
    "Refi": frozenset({"refi", "refi_plus_heloc"}),
    "HELOC": frozenset({"heloc", "refi_plus_heloc"}),
    "Cash-out": frozenset({"cash_out"}),
    "Purchase": frozenset({"purchase"}),
    "Retention": frozenset({"retention"}),
}

# `array_contains(segment_codes, 'itm')`, with or without a table alias. The
# code is anchored on its own quotes so a match cannot run past its literal.
_SEGMENT_CONTAINS_RE = re.compile(
    r"array_contains\(\s*(?:\w+\.)?segment_codes\s*,\s*['\"]([a-z0-9_]+)['\"]\s*\)"
)
# Gold boolean column -> the segment it EXACTLY defines. `segment_codes` is
# built by a CASE ladder in sql/transformations/gold_borrower_360.sql
# (`with_segments`), and for these entries the arm is nothing but this one
# column, so `WHERE <column>` and `array_contains(segment_codes,'<code>')`
# select the identical population. Genie writes the flag at least as often as
# the array -- borrower_360 exposes both and the flag is shorter -- and
# without this map "how many borrowers are in the money?" answered over
# `WHERE in_the_money` produced NO replayable filter and therefore no cohort
# action at all.
#
# Only arms that are ONE BARE COLUMN belong here. `permit`, `equity`,
# `retention` and `refi_propensity` are compound (an OR across two columns, a
# two-sided comparison, a threshold), so no single column stands in for them:
# filtering on one part of a compound arm is not the segment, and replaying it
# as the segment would open a population the answer never described. Those
# columns already reach the queue as reviewed portfolio criteria instead.
# `itm_on_related_property` is left out for the same reason even though it is
# exact today -- its arm is a COALESCE, and admitting a wrapper here is how a
# pin stops being a pin.
# ``test_segment_flag_columns_match_the_gold_case_ladder`` parses the SQL and
# fails if an arm here stops being one bare column, or if a compound arm
# becomes one and is missing.
_SEGMENT_FLAG_COLUMNS: dict[str, str] = {
    "in_the_money": "itm",
    "listed_for_sale": "listed",
    "is_investor": "investor",
    "second_lien_itm": "second_lien_itm",
    "has_heloc_draw_ending": "heloc_draw_to_payback",
    "has_home_equity_history": "home_equity_history",
    "is_payoff_loss": "payoff_loss_leads",
    "has_permit": "permit_activity",
}
# `flag`, `flag = TRUE`, `t.flag = 1`. The `= FALSE` form deliberately does NOT
# match: the optional tail is left in the residue, the span fails the purity
# check, and the complement is disclosed instead of inverted into membership.
_SEGMENT_FLAG_TERM_RE = re.compile(
    r"(?:\w+\.)?(" + "|".join(sorted(_SEGMENT_FLAG_COLUMNS, key=len, reverse=True)) + r")\b"
    r"(?:\s*=\s*(?:true|1)\b)?"
)
# Any mention of a segment-defining name at all. Separates "this conjunct
# filters segments in a shape we could not read" from "this conjunct is about
# something else".
_SEGMENT_COLUMN_RE = re.compile(
    r"\b(?:segment_codes?|" + "|".join(_SEGMENT_FLAG_COLUMNS) + r")\b"
)
# Disclosure name for the former. Identifier-shaped and bounded, like the
# threshold disclosures it rides alongside in `unreplayable_filters`.
_SEGMENT_PREDICATE_DISCLOSURE = "segment_codes_predicate"

# `recommended_offer_code = 'x'` or `recommended_offer_code IN ('x', 'y')`.
# The alternation is anchored on the quotes so the list branch cannot run past
# its own closing paren into a neighbouring conjunct.
_OFFER_CODE_PREDICATE_RE = re.compile(
    r"\brecommended_offer_code\s*(?:=\s*['\"](?P<single>[a-z_]+)['\"]"
    r"|in\s*\((?P<list>[^)]*)\))"
)


def _sql_hash(sql_query: str | None) -> str | None:
    if not sql_query:
        return None
    normalized = re.sub(r"\s+", " ", sql_query.strip())
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _borrower_ids_from_rows(rows: list[dict[str, Any]] | None) -> list[str]:
    ids: list[str] = []
    for row in rows or []:
        value = row.get("borrower_id")
        if not isinstance(value, str):
            continue
        try:
            borrower_id = validate_public_borrower_id(value)
        except ValueError:
            continue
        if borrower_id not in ids:
            ids.append(borrower_id)
    return ids


def _total_matching_from_rows(rows: list[dict[str, Any]] | None) -> int:
    for row in rows or []:
        raw = row.get("total_matching_borrowers")
        if raw is None:
            continue
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            continue
    return len(rows or [])


def _actionable_total_from_rows(rows: list[dict[str, Any]] | None) -> int | None:
    for column in (
        "lead_queue_borrowers",
        "lead_queue_leads",
        "action_ready_borrowers",
        "actionable_borrowers",
        "marketing_eligible_borrowers",
    ):
        total = 0
        found = False
        for row in rows or []:
            raw = row.get(column)
            if raw is None:
                continue
            try:
                total += max(0, int(raw))
                found = True
            except (TypeError, ValueError):
                continue
        if found:
            return total
    return None


def _row_values(
    rows: list[dict[str, Any]] | None,
    *columns: str,
    digits: int | None = None,
    upper: bool = False,
) -> list[str]:
    values: list[str] = []
    for row in rows or []:
        for column in columns:
            raw = row.get(column)
            if raw is None:
                continue
            value = str(raw).strip()
            if upper:
                value = value.upper()
            if digits is not None:
                if re.fullmatch(r"\d+\.0", value):
                    value = value.split(".", 1)[0]
                if re.fullmatch(r"\d+", value) and len(value) < digits:
                    value = value.zfill(digits)
                match = re.fullmatch(rf"\d{{{digits}}}", value)
                if not match:
                    continue
            elif upper and not re.fullmatch(r"[A-Z]{2}", value):
                continue
            if value not in values:
                values.append(value)
    return values


def _segment_selection_from_sql(
    reading: SqlFilterReading,
) -> tuple[list[str], str, bool]:
    """Read the segments the ANSWER filtered on out of its own conjuncts.

    Returns ``(codes, mode, unreadable)``.

    This replaces a reader that inferred segments from the QUESTION'S WORDING
    (deleted 2026-08-11), the last of that family. It failed the same way its
    siblings did — prose has no position to gate on, so a segment the answer
    merely *mentioned* was indistinguishable from one it *filtered on*, and the
    mismatch always narrowed. Live: "Which segment converts best: HELOC,
    cash-out, or retention?" is answered over all 5,156,184 borrowers (segment
    is a GROUP BY, not a filter) while the queue opened 468,703 — a comparison
    read as a cohort. The wording reader also mapped "refi" to ``itm`` and
    "purchase" to ``listed``, so questions that never named a segment at all
    still narrowed.

    Segments are read the way the numeric floors are: only from the accepted
    top-level AND-conjuncts of the outermost statement. A conjunct counts when
    it is a pure disjunction of ``array_contains(segment_codes, '<code>')``
    tests, which is exactly the shape the Lead Queue's ``segment_mode="any"``
    compiles back to. Anything else that names the column — a mixed
    ``OR`` across two columns, a negation, an equality on the array itself, an
    unreviewed code — is reported as unreadable rather than guessed at, because
    every possible guess is a narrowing one. ``(a OR b) AND c`` is a
    conjunction of disjunctions that the queue's single any/all switch cannot
    express, so it is unreadable too.

    Reading nothing is safe in a way that reading wrong is not: the queue then
    opens the population the answer actually described. An answer with no
    replayable filter at all simply offers no cohort action.
    """

    # A segment name the reader refused for a reason no re-read can recover --
    # a negation, a bound over a re-aggregating CTE, a span it could not prove
    # complete. `unread_predicates` deliberately excludes these.
    unreadable = bool(_SEGMENT_COLUMN_RE.search(" ".join(reading.unread_filter_words)))
    disjunctions: list[list[str]] = []
    # Accepted conjuncts, then the ones the floor reader refused. A refused
    # span is where a real segment filter usually lives -- "itm OR equity" is
    # one conjunct, and the splitter will not break an OR apart -- so skipping
    # them would open every segment for a question that named two. Each span
    # is accepted ONLY if the WHOLE of it reduces to segment membership tests
    # joined by OR, which is exactly what ``segment_mode="any"`` replays. A
    # span that mentions the column in any other shape is disclosed, never
    # partially read: half of an OR is narrower than the answer, and a
    # negation is its complement.
    for predicate in (*reading.predicates, *reading.unread_predicates):
        if not _SEGMENT_COLUMN_RE.search(predicate):
            continue
        codes = list(
            dict.fromkeys(
                [
                    *_SEGMENT_CONTAINS_RE.findall(predicate),
                    *(
                        _SEGMENT_FLAG_COLUMNS[flag]
                        for flag in _SEGMENT_FLAG_TERM_RE.findall(predicate)
                    ),
                ]
            )
        )
        residue = _SEGMENT_FLAG_TERM_RE.sub(
            " ", _SEGMENT_CONTAINS_RE.sub(" ", predicate)
        )
        residue = residue.replace("(", " ").replace(")", " ")
        leftover = residue.split()
        # Every OR must sit BETWEEN two membership tests. Counting matches
        # rather than deduped codes matters: `array_contains(...,'itm') OR
        # in_the_money` is two tests for one code. A span with a dangling `or`
        # is a fragment of a longer disjunction whose other branches were cut
        # off, and reading it would replay a strict subset of the answer.
        matches = len(_SEGMENT_CONTAINS_RE.findall(predicate)) + len(
            _SEGMENT_FLAG_TERM_RE.findall(predicate)
        )
        if (
            not codes
            or any(token != "or" for token in leftover)
            or len(leftover) != matches - 1
            or any(code not in GENIE_REPLAY_SEGMENT_CODES for code in codes)
        ):
            unreadable = True
            continue
        disjunctions.append(codes)

    if not disjunctions:
        return [], "any", unreadable
    if len(disjunctions) == 1:
        return disjunctions[0], "any", unreadable
    if all(len(codes) == 1 for codes in disjunctions):
        flattened = list(dict.fromkeys(code for codes in disjunctions for code in codes))
        return flattened, "all", unreadable
    return [], "any", True


def _product_label_from_offer_codes(sql: str) -> str | None:
    """Return a reviewed product label only when the SQL pins the exact code set.

    A product label is not a synonym for one offer code: the reviewed
    vocabulary compiles ``HELOC`` to ``recommended_offer_code IN ('heloc',
    'refi_plus_heloc')`` and ``Refi`` to ``('refi', 'refi_plus_heloc')``. So a
    label may only be replayed when the answer's own predicate pins *exactly*
    that set. A strict subset (``= 'heloc'``) would replay BROADER than the
    answer, and a superset or mixed set would replay NARROWER — the truncation
    failure this module exists to prevent. When nothing matches exactly we
    emit no product at all and let the answer's other conjuncts carry the
    cohort, which errs broad rather than silently dropping borrowers.
    """

    code_sets: list[frozenset[str]] = []
    for match in _OFFER_CODE_PREDICATE_RE.finditer(sql):
        raw = match.group("single") or match.group("list") or ""
        codes = frozenset(
            code
            for code in (part.strip().strip("'\"") for part in raw.split(","))
            if re.fullmatch(r"[a-z_]+", code)
        )
        if codes:
            code_sets.append(codes)
    # Two predicates on the same column intersect; replaying either one alone
    # would misreport the cohort, so only an unambiguous single reading counts.
    if len(set(code_sets)) != 1:
        return None
    for label, label_codes in _PRODUCT_LABEL_CODES.items():
        if code_sets[0] == label_codes:
            return label
    return None


def _portfolio_criteria_from_sql(
    sql_query: str | None, reading: SqlFilterReading | None = None
) -> dict[str, Any]:
    """Read reviewed Portfolio Builder criteria out of the answer's own SQL.

    The answer's SQL is the ONLY source of these criteria. Every criterion
    below narrows the replayed cohort exactly as a numeric floor does
    (``min_equity_pct_label`` compiles to ``equity_pct >=``), so it is read
    from the same position-gated conjuncts rather than from the raw statement
    text. Matching the whole statement would lift a criterion out of a CASE
    arm, an aggregate breakdown, a comment, or a CTE body and hand the queue a
    population the answer never described.

    A sibling reader that inferred the same criteria from the QUESTION'S
    WORDING was deleted (adversarial review 2026-08-11). Prose has no position
    to gate on, so it could not distinguish a filter the answer applied from a
    word the answer merely mentioned, and it silently narrowed real cohorts:

    * "Show the top 20 investors by related property count" emitted
      ``owner_link="Multi-property (2-4)"``, i.e. ``related_property_count
      BETWEEN 2 AND 4`` — against an answer ordered by that column DESC. Live
      measurement: 10 of 10 returned borrowers dropped, all at 3,686
      properties.
    * "How many of our current customers are at risk of going to a
      competitor?" emitted ``product="Retention"``, turning the answer's
      ``segment_codes CONTAINS 'retention' OR recommended_offer_code =
      'retention'`` into the strict second branch. Live: the answer reports
      19,166 and the queue opened 1,991.
    * "Show me borrowers who are NOT owner-occupied but have a permit" emitted
      ``occupancy="Owner-occupied"``: negation is invisible to a keyword
      match. Live: 39,969 borrowers asked for, 410,821 opened, intersection
      zero.

    None of these are fixable by better patterns. A comparison ("owner-occupied
    vs non-owner-occupied"), an enumeration ("HELOC, cash-out, or retention"),
    an exclusion ("everyone except cash-out") and a filter all put the same
    word in the same sentence. Only the SQL says which one it was.
    """

    if not sql_query:
        return {}
    predicates = (reading or read_sql_filters(sql_query)).predicates
    sql = " and ".join(predicates)
    if not sql:
        return {}

    criteria: dict[str, Any] = {}
    if re.search(r"\bis_owner_occupied\s*=\s*(true|1)\b", sql) or "is_owner_occupied" in predicates:
        criteria["occupancy"] = "Owner-occupied"
    elif re.search(r"\bis_owner_occupied\s*=\s*(false|0)\b", sql):
        criteria["occupancy"] = "Non-owner-occupied"

    second_pos_positive = bool(
        re.search(r"\bcoalesce\(\s*second_pos_amount\s*,\s*0\s*\)\s*>\s*0\b", sql)
        or re.search(r"\bsecond_pos_amount\s*>\s*0\b", sql)
    )
    second_pos_zero = bool(
        re.search(r"\bcoalesce\(\s*second_pos_amount\s*,\s*0\s*\)\s*=\s*0\b", sql)
        or re.search(r"\bsecond_pos_amount\s*=\s*0\b", sql)
        or re.search(r"\bsecond_pos_amount\s+is\s+null\b", sql)
    )
    first_pos_positive = bool(
        re.search(r"\bcoalesce\(\s*current_lien_balance\s*,\s*0\s*\)\s*>\s*0\b", sql)
        or re.search(r"\bcurrent_lien_balance\s*>\s*0\b", sql)
    )
    first_pos_zero = bool(
        re.search(r"\bcoalesce\(\s*current_lien_balance\s*,\s*0\s*\)\s*<=\s*0\b", sql)
        or re.search(r"\bcurrent_lien_balance\s*<=\s*0\b", sql)
        or re.search(r"\bcurrent_lien_balance\s+is\s+null\b", sql)
    )
    if second_pos_positive:
        criteria["lien_status"] = "Open HELOC"
    elif first_pos_positive and second_pos_zero:
        criteria["lien_status"] = "Open 1st lien"
    elif first_pos_zero and second_pos_zero:
        criteria["lien_status"] = "Free & clear"

    if re.search(r"\bcoalesce\(\s*related_property_count\s*,\s*1\s*\)\s*<=\s*1\b", sql):
        criteria["owner_link"] = "Single-property owner"
    elif re.search(
        r"\bcoalesce\(\s*related_property_count\s*,\s*1\s*\)\s+between\s+2\s+and\s+4\b",
        sql,
    ):
        criteria["owner_link"] = "Multi-property (2-4)"
    elif re.search(r"\bcoalesce\(\s*related_property_count\s*,\s*1\s*\)\s*>=\s*5\b", sql):
        criteria["owner_link"] = "Portfolio investor (5+)"

    has_listing = bool(re.search(r"\blisted_for_sale\s*=\s*true\b", sql))
    has_heloc_intent = bool(
        re.search(r"\bhas_permit\s*=\s*true\b", sql)
        or re.search(r"\bhas_heloc_propensity_trigger\s*=\s*true\b", sql)
    )
    if has_listing and has_heloc_intent:
        criteria["purchase_intent"] = "Both"
    elif has_listing:
        criteria["purchase_intent"] = "Listed for sale"
    elif has_heloc_intent:
        criteria["purchase_intent"] = "HELOC intent"

    if re.search(r"\bis_current_customer\s*=\s*true\b", sql):
        criteria["lender_relationship"] = "Current customer"
    elif re.search(r"\bis_former_customer\s*=\s*true\b", sql):
        criteria["lender_relationship"] = "Former customer"
    elif re.search(r"\bis_competitor_lien\s*=\s*true\b", sql):
        criteria["lender_relationship"] = "Competitor customer"

    product = _product_label_from_offer_codes(sql)
    if product:
        criteria["product"] = product

    equity_match = re.search(r"\bequity_pct\s*>=\s*(15|25|40)\b", sql)
    if equity_match:
        criteria["min_equity_pct_label"] = f"≥ {equity_match.group(1)}%"
    return criteria


def _numeric_floors_from_sql(sql_query: str | None) -> dict[str, int]:
    """Lift the answer's own numeric thresholds into replayable cohort filters.

    Position-gated, not text-matched: see
    ``backend/services/genie_sql_predicates`` for why a bound only counts as a
    cohort floor when it stands as a top-level AND-conjunct of the outermost
    statement's own WHERE / inner-join ON / QUALIFY.
    """

    return read_sql_filters(sql_query).floors


def _route_from_answer_rows(
    *,
    question: str,
    rows: list[dict[str, Any]] | None,
    borrower_ids: list[str],
    sql_query: str | None = None,
    declared_segments: tuple[str, ...] = (),
) -> tuple[str, dict[str, Any]]:
    params: dict[str, str] = {}
    filter_criteria: dict[str, Any] = {}
    zips = _row_values(rows, "zip", "zip_code", "zipcode", "postal_code", digits=5)
    counties = _row_values(rows, "county_fips_5", "county_fips", "fips_5", "fips", digits=5)
    states = _row_values(rows, "state", "state_code", upper=True)
    if (
        states
        and re.search(r"\bwhich\s+state\b|\bwhat\s+state\b", question.lower())
        and _actionable_total_from_rows(rows) is None
    ):
        states = states[:1]
    reading = read_sql_filters(sql_query)
    # Segments come from the ANSWER'S OWN conjuncts, never from the question's
    # wording: see ``_segment_selection_from_sql``.
    segment_codes, segment_mode, segments_unreadable = _segment_selection_from_sql(reading)
    if declared_segments:
        # A canonical statement this repo AUTHORED, whose segment filter sits
        # where the position gate cannot see it -- inside a CTE body, or in a
        # statement whose sources the reader will not resolve. The cohort is
        # then declared next to the SQL and reviewed with it, which is the one
        # honest alternative to inference: we are not guessing what someone
        # else's SQL meant, we are stating what ours does. Never reachable
        # from a live Genie answer -- ``respond()`` has no declaration to make.
        segment_codes = [
            code for code in declared_segments if code in GENIE_REPLAY_SEGMENT_CODES
        ]
        segment_mode = "all" if len(segment_codes) > 1 else "any"
        segments_unreadable = False
    # Portfolio criteria come from the ANSWER'S OWN SQL only. They used to be
    # merged with criteria inferred from the question's wording, which is
    # removed: see ``_portfolio_criteria_from_sql`` for why every criterion
    # here narrows, and why prose cannot be gated the way SQL conjuncts can.
    portfolio_criteria = _portfolio_criteria_from_sql(sql_query, reading)

    if borrower_ids:
        filter_criteria["borrower_ids"] = borrower_ids
        params["borrower_ids"] = ",".join(borrower_ids)
    elif zips:
        params["zips"] = ",".join(zips)
        filter_criteria["zips"] = zips
    elif counties:
        if len(counties) == 1:
            params["county"] = counties[0]
            filter_criteria["county"] = counties[0]
        else:
            params["counties"] = ",".join(counties)
            filter_criteria["counties"] = counties
    elif states:
        params["states"] = ",".join(states)
        filter_criteria["states"] = states

    if segment_codes:
        if len(segment_codes) == 1:
            params["segment"] = segment_codes[0]
        else:
            params["segment_codes"] = ",".join(segment_codes)
            params["segment_mode"] = segment_mode
        filter_criteria["segment_codes"] = segment_codes
        filter_criteria["segment_mode"] = segment_mode

    if portfolio_criteria:
        filter_criteria["portfolio_criteria"] = portfolio_criteria
        for key in (
            "occupancy",
            "lien_status",
            "lender_relationship",
            "product",
            "target_lender_ref",
            "min_equity_pct_label",
            "owner_link",
            "purchase_intent",
        ):
            value = portfolio_criteria.get(key)
            if isinstance(value, str) and value.strip():
                params[key] = value

    for floor_key, floor in reading.floors.items():
        # The queue applies these verbatim, so the cohort it opens is the
        # population the answer just reported rather than a broader one.
        filter_criteria[floor_key] = floor
        params[floor_key] = str(floor)

    disclosures = list(reading.unreplayable)
    if segments_unreadable:
        # A segment predicate the queue cannot express. Named for the same
        # reason a threshold is: the queue's count will be broader than the
        # answer's, and a named reason beats a silent divergence. This fires
        # even when other conjuncts DID set the cohort's segments -- in
        # `itm AND (listed OR score >= 80)` the queue replays `itm` and is
        # still broader on the very axis it filtered.
        disclosures.append(_SEGMENT_PREDICATE_DISCLOSURE)
    if disclosures and _REPLAYABLE_ROUTE_FILTER_KEYS & set(filter_criteria):
        # A threshold that really filters the answer but cannot be replayed —
        # a bound parameter whose value never reaches us, a bound outside the
        # reviewed cohort domain, or several disagreeing bounds on one column.
        # Naming it makes the queue's broader count self-explaining
        # (`X-Cohort-Unreplayable-Filters`) instead of a silent divergence.
        # These keys are outside the replayable vocabulary on purpose: every
        # downstream gate folds them into `unreplayable_filters`. They are
        # never added to the URL, and never stand alone — a cohort with no
        # replayable filter is rejected, so a disclosure-only cohort would
        # offer an action that 400s.
        for disclosure in disclosures:
            filter_criteria[disclosure] = True

    if params:
        return f"/lead-queue?{urlencode(params)}", filter_criteria
    return "/lead-queue", filter_criteria


def _suggest_genie_actions(
    *,
    question: str,
    rows: list[dict[str, Any]] | None,
    trusted_assets: list[str],
    visualization: GenieVisualizationSpec | None,
    conversation_id: str | None,
    message_id: str | None,
    question_hash: str | None,
    sql_query: str | None,
    source: str = "genie",
    declared_segments: tuple[str, ...] = (),
) -> list[GenieActionSuggestion]:
    actions: list[GenieActionSuggestion] = []
    borrower_ids = _borrower_ids_from_rows(rows)
    row_count = len(rows) if rows else 0
    q = question.lower()
    base_criteria: dict[str, Any] = {
        "source": source,
        "source_assets": trusted_assets,
        "visualization_kind": visualization.kind if visualization else None,
        "row_count": row_count,
    }
    sql_digest = _sql_hash(sql_query)
    if sql_digest:
        base_criteria["sql_hash"] = sql_digest
    lead_queue_route, result_filters = _route_from_answer_rows(
        question=question,
        rows=rows,
        borrower_ids=borrower_ids,
        sql_query=sql_query,
        declared_segments=declared_segments,
    )
    if result_filters:
        base_criteria["result_filters"] = result_filters
    actionable_total = _actionable_total_from_rows(rows)
    if actionable_total is not None:
        base_criteria["actionable_total"] = actionable_total
    if borrower_ids:
        criteria = dict(base_criteria)
        actions.append(
            GenieActionSuggestion(
                id="save-borrowers",
                label=f"Save {len(borrower_ids)} borrower{'' if len(borrower_ids) == 1 else 's'}",
                action_type="save_borrowers",
                description="Add returned borrowers to the governed saved workspace.",
                borrower_ids=borrower_ids,
                criteria=criteria,
            )
        )
        criteria = dict(base_criteria)
        route = f"/borrower-360/{borrower_ids[0]}"
        actions.append(
            GenieActionSuggestion(
                id="show-first-rationale",
                label="Show why first borrower is ranked",
                action_type="show_rationale",
                description="Open Borrower 360 for the top returned borrower.",
                route=route,
                borrower_ids=[borrower_ids[0]],
                criteria=criteria,
            )
        )
    if row_count > 0 and result_filters:
        criteria = dict(base_criteria)
        open_label = "Open this cohort in Lead Queue"
        open_description = "Navigate into the lead queue with this Genie result audited."
        if actionable_total is not None:
            open_label = f"Open eligible Lead Queue subset ({actionable_total:,})"
            open_description = (
                "Navigate into the marketing-eligible Lead Queue subset for this Genie result; "
                "the analytic count above may be broader."
            )
        actions.append(
            GenieActionSuggestion(
                id="open-cohort",
                label=open_label,
                action_type="open_cohort",
                description=open_description,
                route=lead_queue_route,
                borrower_ids=borrower_ids,
                criteria=criteria,
            )
        )
        criteria = dict(base_criteria)
        actions.append(
            GenieActionSuggestion(
                id="create-campaign-draft",
                label="Create draft campaign",
                action_type="create_draft_campaign",
                description="Create a Lakebase draft campaign from this governed Genie result.",
                route=lead_queue_route,
                borrower_ids=borrower_ids,
                criteria=criteria,
            )
        )
    if borrower_ids and ("offer" in q or "strategy" in q or "10,000" in q):
        criteria = dict(base_criteria)
        route = f"/offer-orchestrator/{borrower_ids[0]}"
        actions.append(
            GenieActionSuggestion(
                id="compare-offers",
                label="Compare offer strategies",
                action_type="compare_offer_strategies",
                description="Audit this strategy comparison and open the offer surface.",
                route=route,
                borrower_ids=borrower_ids[:1],
                criteria=criteria,
            )
        )
    return actions[:5]
