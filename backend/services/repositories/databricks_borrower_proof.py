"""Borrower proof construction for the Databricks borrower repository.

Split out of ``databricks_borrowers`` (2026-08-08) when that module crossed the
900-line gate: the score-component vocabulary and the proof/formula/offer-branch
builders are a self-contained unit that the repository class only touches
through ``_build_borrower_proof``. Repo policy is to split, not to allowlist.

``databricks_borrowers`` re-exports ``_build_borrower_proof`` so every existing
import path keeps working.
"""

from __future__ import annotations

from typing import Any

from backend.config.settings import settings
from backend.schemas.proof import (
    BorrowerProof,
    ProofEvidenceEvent,
    ProofFormulaLine,
    ProofOfferBranch,
    ProofReproduceQuery,
    ProofScoreComponent,
)
from backend.services.databricks_sql_helpers import qualify
from backend.services.proof_policy import (
    borrower_proof_assets,
    hash_sql,
    validate_borrower_proof_sql,
)
from backend.services.repositories.databricks_shared import (
    _coerce_bool,
    _redact_evidence_list,
)
from backend.services.scoring import (
    NBO_PRODUCT_LABELS,
    lead_score,
    next_best_offer,
    offer_display_label,
)

_SCORE_WEIGHTS: dict[str, float] = {
    "economic_incentive": 0.35,
    "intent_trigger": 0.30,
    "fit": 0.15,
    "relationship": 0.10,
    "evidence": 0.10,
}

_SCORE_LABELS: dict[str, str] = {
    "economic_incentive": "Economic incentive",
    "intent_trigger": "Intent trigger",
    "fit": "Product fit",
    "relationship": "Relationship",
    "evidence": "Evidence coverage",
}

_SCORE_EXPLANATIONS: dict[str, str] = {
    "economic_incentive": (
        "Rate spread and home equity. Higher values mean the current lien sits "
        "far enough above market and the property has usable equity."
    ),
    "intent_trigger": (
        "Time-sensitive Cotality and first-party signals such as competitor "
        "lien activity, market movement, prior payoff/refi events, and product intent."
    ),
    "fit": (
        "Borrower/property fit for the offer lane, including owner-occupancy, "
        "first-lien product type, and investor/corporate-owner posture."
    ),
    "relationship": (
        "Current or former lender relationship, competitor lien status, owner-link "
        "history, and first-party engagement depth."
    ),
    "evidence": (
        "Count and strength of governed evidence rows attached to this borrower, "
        "plus bounded second-position lien context."
    ),
}

_SCORE_FIELDS: dict[str, list[str]] = {
    "economic_incentive": ["rate_spread_bps", "equity_pct"],
    "intent_trigger": [
        "is_competitor_lien",
        "is_investor",
        "is_current_customer",
        "has_permit",
        "listed_for_sale",
        "has_heloc_propensity_trigger",
        "heloc_propensity_score",
        "has_refi_propensity_trigger",
        "refi_propensity_score",
    ],
    "fit": ["is_owner_occupied", "first_pos_loan_type", "is_corporate_owner", "is_investor"],
    "relationship": [
        "is_current_customer",
        "is_former_customer",
        "is_competitor_lien",
        "related_property_count",
        "first_party_relationship_depth",
    ],
    "evidence": ["evidence_ids", "second_pos_amount"],
}

_FIT_FAIR_LENDING_NOTE = (
    "Audit note: this signal supports marketing prioritization, not credit eligibility."
)

_RELATIONSHIP_FAIR_LENDING_NOTE = (
    "Audit note: relationship status supports marketing prioritization, not credit eligibility."
)

_INVESTOR_FAIR_LENDING_NOTE = (
    "Audit note: ownership posture supports routing; it is not used for credit eligibility."
)


def _int_value(row: dict[str, Any], key: str, default: int = 0) -> int:
    value = row.get(key)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_value(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = row.get(key)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _fair_lending_note(key: str) -> str | None:
    if key == "fit":
        return _FIT_FAIR_LENDING_NOTE
    if key == "relationship":
        return _RELATIONSHIP_FAIR_LENDING_NOTE
    if key == "intent_trigger":
        return _INVESTOR_FAIR_LENDING_NOTE
    return None


def _component_rows(row: dict[str, Any]) -> tuple[list[ProofScoreComponent], list[str]]:
    missing = [key for key in _SCORE_WEIGHTS if row.get(key) is None]
    if missing:
        return [], [
            "mip.gold.lead_scores did not return component sub-scores for this borrower; "
            "the proof is limited to materialized dossier outputs."
        ]
    out: list[ProofScoreComponent] = []
    for key, weight in _SCORE_WEIGHTS.items():
        value = _int_value(row, key)
        out.append(
            ProofScoreComponent(
                key=key,  # type: ignore[arg-type]
                label=_SCORE_LABELS[key],
                value=value,
                weight=weight,
                weighted_points=round(value * weight, 2),
                explanation=_SCORE_EXPLANATIONS[key],
                source_fields=_SCORE_FIELDS[key],
                fair_lending_note=_fair_lending_note(key),
            )
        )
    return out, []


def _offer_branches(row: dict[str, Any], selected_code: str) -> list[ProofOfferBranch]:
    spread = _int_value(row, "rate_spread_bps")
    equity = _int_value(row, "equity_pct")
    min_spread = _int_value(row, "min_spread_bps_applied", 75)
    min_equity = _int_value(row, "min_equity_pct_applied", 15)
    heloc_min = _int_value(row, "heloc_equity_min_applied", 35)
    cashout_min = _int_value(row, "cashout_equity_min_applied", 25)
    retention_min = _int_value(row, "retention_min_spread_applied", 50)
    has_permit = _coerce_bool(row.get("has_permit"))
    has_heloc_intent = has_permit or _coerce_bool(row.get("has_heloc_propensity_trigger"))
    listed = _coerce_bool(row.get("listed_for_sale"))
    investor = _coerce_bool(row.get("is_investor"))
    customer = _coerce_bool(row.get("is_current_customer"))
    competitor = _coerce_bool(row.get("is_competitor_lien"))

    specs: list[tuple[str, bool, str]] = [
        ("purchase", listed, "Listed-for-sale signal is true."),
        (
            "refi_plus_heloc",
            spread >= min_spread and equity >= heloc_min,
            f"Rate spread {spread} bps >= {min_spread} and equity {equity}% >= {heloc_min}%.",
        ),
        (
            "heloc",
            has_heloc_intent and equity >= heloc_min,
            f"HELOC-intent signal is {has_heloc_intent} and equity {equity}% >= {heloc_min}%.",
        ),
        (
            "refi",
            spread >= min_spread and equity >= min_equity,
            f"Rate spread {spread} bps >= {min_spread} and equity {equity}% >= {min_equity}%.",
        ),
        ("cash_out", equity >= cashout_min, f"Equity {equity}% >= cash-out threshold {cashout_min}%."),
        ("investor", investor, f"Investor or multi-property signal is {investor}."),
        (
            "retention",
            customer and (spread >= retention_min or competitor),
            f"Current-customer signal is {customer}; spread {spread} bps >= {retention_min} or competitor lien is {competitor}.",
        ),
    ]
    positive_branch_passed = any(passed for _, passed, _ in specs)
    branches = [
        ProofOfferBranch(
            code=code,
            label=offer_display_label(code, NBO_PRODUCT_LABELS[code]),
            passed=passed,
            selected=selected_code == code,
            reason=reason,
        )
        for code, passed, reason in specs
    ]
    branches.append(
        ProofOfferBranch(
            code="nurture",
            label=offer_display_label("nurture", NBO_PRODUCT_LABELS["nurture"]),
            passed=not positive_branch_passed,
            selected=selected_code == "nurture",
            reason="Fallback lane when no positive outreach branch is selected.",
        )
    )
    return branches


def _recomputed_offer_code(row: dict[str, Any]) -> str:
    return next_best_offer(
        _int_value(row, "rate_spread_bps"),
        _int_value(row, "equity_pct"),
        _coerce_bool(row.get("has_permit")) or _coerce_bool(row.get("has_heloc_propensity_trigger")),
        _coerce_bool(row.get("listed_for_sale")),
        _coerce_bool(row.get("is_investor")),
        _coerce_bool(row.get("is_current_customer")),
        _coerce_bool(row.get("is_competitor_lien")),
        _int_value(row, "min_spread_bps_applied", 75),
        _int_value(row, "min_equity_pct_applied", 15),
        _int_value(row, "heloc_equity_min_applied", 35),
        _int_value(row, "cashout_equity_min_applied", 25),
        _int_value(row, "retention_min_spread_applied", 50),
    )


def _proof_evidence_rows(raw: Any) -> list[ProofEvidenceEvent]:
    rows: list[ProofEvidenceEvent] = []
    for event in _redact_evidence_list(raw):
        rows.append(ProofEvidenceEvent(**event.model_dump(exclude={"source_table"})))
    return rows


def _proof_sql_templates() -> list[ProofReproduceQuery]:
    databricks_url = (
        f"{settings.databricks_host.rstrip('/')}/sql/editor"
        if settings.databricks_host
        else None
    )
    borrower_dossier = qualify("gold", "borrower_dossier")
    lead_scores = qualify("gold", "lead_scores")
    evidence_events = qualify("gold", "evidence_events")
    templates = [
        (
            "Score components",
            (
                "WITH borrower AS ("
                " SELECT borrower_id, clip, opportunity_score AS dossier_opportunity_score,"
                f" confidence AS dossier_signal_strength FROM {borrower_dossier}"
                " WHERE borrower_id = :borrower_id"
                "), score AS ("
                " SELECT ls.clip, ls.economic_incentive, ls.intent_trigger, ls.fit, ls.relationship,"
                " ls.evidence, ls.opportunity_score AS lead_scores_opportunity_score,"
                " ls.confidence AS lead_scores_signal_strength"
                f" FROM {lead_scores} AS ls"
                " JOIN borrower AS b ON b.clip = ls.clip"
                ") SELECT borrower.borrower_id, score.economic_incentive, score.intent_trigger,"
                " score.fit, score.relationship, score.evidence,"
                f" {qualify('gold', 'fn_lead_score')}(score.economic_incentive, score.intent_trigger,"
                " score.fit, score.relationship, score.evidence) AS recomputed_opportunity_score,"
                " CAST(ROUND((score.economic_incentive + score.intent_trigger + score.fit"
                " + score.relationship + score.evidence) / 5) AS INT) AS recomputed_signal_strength,"
                " borrower.dossier_opportunity_score, score.lead_scores_opportunity_score,"
                " borrower.dossier_signal_strength, score.lead_scores_signal_strength"
                " FROM borrower LEFT JOIN score ON score.clip = borrower.clip"
            ),
            "Recomputes the displayed score and signal strength, then shows both gold materializations.",
        ),
        (
            "Decision inputs",
            (
                "SELECT borrower_id, recommended_offer_code, recommended_offer, rate_spread_bps, equity_pct,"
                " has_permit, has_heloc_propensity_trigger, heloc_propensity_score, listed_for_sale,"
                " is_investor, is_current_customer, is_competitor_lien,"
                " min_spread_bps_applied, min_equity_pct_applied, heloc_equity_min_applied,"
                " cashout_equity_min_applied, retention_min_spread_applied,"
                f" {qualify('gold', 'fn_next_best_offer')}(rate_spread_bps, equity_pct,"
                " (has_permit OR has_heloc_propensity_trigger),"
                " listed_for_sale, is_investor, is_current_customer, is_competitor_lien,"
                " min_spread_bps_applied, min_equity_pct_applied, heloc_equity_min_applied,"
                " cashout_equity_min_applied, retention_min_spread_applied) AS recomputed_offer_code"
                f" FROM {borrower_dossier}"
                " WHERE borrower_id = :borrower_id"
            ),
            "Recomputes the primary offer from the exact public inputs and thresholds.",
        ),
        (
            "Evidence rows",
            (
                "SELECT evidence_id, source_product, signal_type, signal_value, display_text,"
                " confidence, `timestamp`"
                f" FROM {evidence_events} AS ev"
                f" WHERE EXISTS (SELECT 1 FROM {borrower_dossier} AS b"
                " WHERE b.borrower_id = :borrower_id AND b.clip = ev.clip)"
                " ORDER BY signal_rank ASC LIMIT 20"
            ),
            "Returns the same redacted evidence row shape used by the app drawer.",
        ),
    ]
    return [
        ProofReproduceQuery(
            title=title,
            sql=validate_borrower_proof_sql(sql),
            sql_hash=hash_sql(sql),
            note=note,
            databricks_sql_url=databricks_url,
        )
        for title, sql, note in templates
    ]


def _build_borrower_proof(row: dict[str, Any]) -> BorrowerProof:
    components, gaps = _component_rows(row)
    selected_code = str(row.get("recommended_offer_code") or "nurture")
    if selected_code not in NBO_PRODUCT_LABELS:
        selected_code = "nurture"
        gaps.append("recommended_offer_code was outside the governed primary-offer vocabulary.")
    recomputed_offer = _recomputed_offer_code(row)
    if recomputed_offer != selected_code:
        gaps.append(
            "Recomputed primary offer does not match the borrower dossier displayed offer; "
            f"recomputed {recomputed_offer}, dossier {selected_code}."
        )

    score = _int_value(row, "opportunity_score")
    signal_strength = _int_value(row, "confidence")
    score_row_score = row.get("score_opportunity_score")
    score_row_strength = row.get("score_signal_strength")
    dossier_refreshed_at = _str_or_none(row.get("dossier_refreshed_at"))
    score_refreshed_at = _str_or_none(row.get("score_refreshed_at"))
    if dossier_refreshed_at and score_refreshed_at and dossier_refreshed_at != score_refreshed_at:
        gaps.append(
            "Borrower dossier and lead_scores were refreshed at different times; "
            f"dossier {dossier_refreshed_at}, lead_scores {score_refreshed_at}."
        )
    if components:
        recomputed_score = lead_score(**{c.key: c.value for c in components})
        weighted_expr = " + ".join(f"{c.weight:.2f}*{c.value}" for c in components)
        score_result = f"{score} displayed opportunity score (recomputed {recomputed_score})"
        if score_row_score is not None and _int_value(row, "score_opportunity_score") != score:
            gaps.append(
                "Borrower dossier opportunity score does not match lead_scores materialized score; "
                f"dossier {score}, lead_scores {_int_value(row, 'score_opportunity_score')}."
            )
        if recomputed_score != score:
            gaps.append(
                "Recomputed opportunity score does not match the borrower dossier displayed score; "
                f"recomputed {recomputed_score}, dossier {score}."
            )
        if score_row_score is not None and recomputed_score != _int_value(row, "score_opportunity_score"):
            gaps.append(
                "Recomputed opportunity score does not match lead_scores materialized score; "
                f"recomputed {recomputed_score}, lead_scores {_int_value(row, 'score_opportunity_score')}."
            )
        strength_expr = "(" + " + ".join(str(c.value) for c in components) + ") / 5"
        strength_result = f"{signal_strength}% displayed signal strength"
        recomputed_strength = round(sum(c.value for c in components) / len(components))
        if score_row_strength is not None and _int_value(row, "score_signal_strength") != signal_strength:
            gaps.append(
                "Borrower dossier signal strength does not match lead_scores materialized value; "
                f"dossier {signal_strength}, lead_scores {_int_value(row, 'score_signal_strength')}."
            )
        if recomputed_strength != signal_strength:
            gaps.append(
                "Recomputed signal strength does not match the borrower dossier displayed value; "
                f"recomputed {recomputed_strength}, dossier {signal_strength}."
            )
        if score_row_strength is not None and recomputed_strength != _int_value(row, "score_signal_strength"):
            gaps.append(
                "Recomputed signal strength does not match lead_scores materialized value; "
                f"recomputed {recomputed_strength}, lead_scores {_int_value(row, 'score_signal_strength')}."
            )
    else:
        gaps.append("Governed lead_scores component row was unavailable, so the score cannot be recomputed.")
        weighted_expr = "component row unavailable"
        score_result = f"{score} materialized opportunity score"
        strength_expr = "component row unavailable"
        strength_result = f"{signal_strength}% materialized signal strength"

    current_rate_pct = _float_value(row, "current_rate")
    market_rate_pct = _float_value(row, "market_rate_fraction") * 100
    avm = _int_value(row, "avm_value")
    lien = _int_value(row, "current_lien_balance")
    equity = _int_value(row, "equity_estimate", max(0, avm - lien))
    equity_pct = _int_value(row, "equity_pct")
    ltv = _int_value(row, "ltv")

    return BorrowerProof(
        borrower_id=str(row.get("borrower_id") or ""),
        trusted=not gaps,
        known_data_gaps=gaps,
        generated_from=f"{qualify('gold', 'borrower_dossier')} + {qualify('gold', 'lead_scores')}",
        source_refresh_at=" / ".join(
            part
            for part in (
                f"dossier {dossier_refreshed_at}" if dossier_refreshed_at else None,
                f"lead_scores {score_refreshed_at}" if score_refreshed_at else None,
            )
            if part
        )
        or None,
        opportunity_score=score,
        signal_strength=signal_strength,
        signal_strength_note=(
            "Signal strength is a deterministic average of the five scoring sub-scores. "
            "It is not a statistical confidence interval and is not a credit decision probability."
        ),
        evidence_confidence_note=(
            "Evidence confidence is row-level source confidence from gold.evidence_events; "
            "AVM-backed rows use upstream AVM confidence, while count-based rows use governed constants."
        ),
        score_components=components,
        score_formula=ProofFormulaLine(
            label="Opportunity score",
            expression=weighted_expr,
            result=score_result,
            source=qualify("gold", "fn_lead_score"),
        ),
        signal_strength_formula=ProofFormulaLine(
            label="Signal strength",
            expression=strength_expr,
            result=strength_result,
            source=qualify("gold", "lead_scores"),
        ),
        rate_spread_formula=ProofFormulaLine(
            label="Rate spread",
            expression=f"({current_rate_pct:.3f}% current rate - {market_rate_pct:.3f}% market rate) * 100",
            result=f"{_int_value(row, 'rate_spread_bps')} bps",
            source=qualify("gold", "fn_rate_spread"),
        ),
        equity_formula=ProofFormulaLine(
            label="Equity",
            expression=f"{avm:,} AVM - {lien:,} current lien",
            result=f"{equity:,} equity ({equity_pct}%)",
            source=qualify("gold", "borrower_dossier"),
        ),
        ltv_formula=ProofFormulaLine(
            label="LTV",
            expression=f"{lien:,} current lien / {avm:,} AVM",
            result=f"{ltv}% LTV",
            source=qualify("gold", "borrower_dossier"),
        ),
        offer_code=selected_code,
        offer_label=offer_display_label(selected_code, NBO_PRODUCT_LABELS[selected_code]),
        offer_branches=_offer_branches(row, selected_code),
        evidence_rows=_proof_evidence_rows(row.get("evidence_events") or []),
        source_assets=borrower_proof_assets(),
        reproduce=_proof_sql_templates(),
    )
