"""The shrink-only KNOWN_OMISSIONS ledger for tests/unit/test_e2e_fixture_contract.py.

quality-09 item 2 (a4-i): a served 2xx fixture body must carry every key the
real server serializes. The bodies recorded here predate that check; each entry
must still reproduce exactly its keys (test_known_omissions_is_shrink_only), and
w5-wire-types empties it as it types the served bodies from the generated
contract. Kept apart from the test so that file stays small; never grow it.
"""

from __future__ import annotations


def _per_item(count: int, keys: str, overrides: dict[int, str] | None = None) -> str:
    """Comma-joined ``[i].key`` paths for ``count`` list items omitting ``keys``
    (``overrides`` gives an item its own list)."""
    chosen = overrides or {}
    return ",".join(
        f"[{index}].{key}" for index in range(count) for key in chosen.get(index, keys).split(",")
    )


_LEAD_SUMMARY_OMITTED = (
    "approved_at,outreach_at,is_former_customer,is_competitor_lien,second_pos_amount,"
    "has_permit,listing_status_category,listing_status_description,listing_date,"
    "listing_status_date,listing_price,listing_days_on_market,listing_service,"
    "heloc_propensity_score,heloc_propensity_run_date,refi_propensity_score,"
    "refi_propensity_run_date,has_refi_propensity_trigger,current_lender_ref,"
    "last_touch_at,eligible_recontact_at,dnc,assigned_to_email,assigned_to_label,"
    "assigned_at,assignment_expires_at,assignment_status,assignment_id,"
    "latest_disposition_outcome,latest_disposition_at,latest_callback_at,aging_days"
)
_BORROWER_360_OMITTED = (
    "current_lien_balance_low,current_lien_balance_high,ltv_basis_is_unreliable,"
    "situs_cbsa_code,first_pos_loan_type,is_absentee,is_corporate_owner,"
    "has_first_party_relationship,first_party_relationship_depth,"
    "first_party_recent_interactions,first_party_recent_application,"
    "first_party_synthetic_demo"
)
_QUEUE_LAYOUT_ITEM_2 = (
    "approved_at,outreach_at,is_former_customer,is_competitor_lien,second_pos_amount,"
    "has_permit,listing_status_category,listing_status_description,listing_date,"
    "listing_status_date,listing_price,listing_days_on_market,listing_service,"
    "heloc_propensity_score,heloc_propensity_run_date,refi_propensity_score,"
    "refi_propensity_run_date,has_refi_propensity_trigger,current_lender_ref,"
    "last_touch_at,eligible_recontact_at,assigned_at,assignment_expires_at,assignment_id,"
    "latest_callback_at"
)


_OMISSION_WHY = {
    "leads": (
        "LeadSummary rows predate the assignment, listing, propensity and disposition "
        "columns; the real server sends each (null or its default)"
    ),
    "b360": (
        "the Borrower 360 body predates the LeadSummary columns and the lien-band, "
        "CBSA, owner-type and first-party fields the real server always sends"
    ),
    "genie": (
        "Genie answer and refusal builders leave optional response fields out; the real"
        " server serializes every one (null, [] or its default)"
    ),
    "session": (
        "session bodies leave capability and display fields to an implied default; the "
        "real server always sends them"
    ),
    "portfolio": (
        "portfolio bodies predate offers_available / household_summary; the real server"
        " always sends them"
    ),
    "lifecycle": (
        "lifecycle bodies leave the decision ids and timestamps out instead of sending "
        "null"
    ),
    "admin": (
        "admin, data-estate and asset-metadata bodies predate checked_at, "
        "synthetic_demo, catalog_explorer_url and redacted"
    ),
    "proof": (
        "proof body predates fair_lending_note, databricks_sql_url and margins"
    ),
    "outreach": (
        "outreach draft body predates campaign_treatment_fingerprint"
    ),
    "genie_jobs": (
        "job-status bodies leave typical_seconds and the finished answer's optional "
        "fields out; the real server sends each (null or [])"
    ),
}


def _omission(domain: str, keys: str) -> dict[str, str]:
    return {
        "finding": "quality-09 item 2",
        "owner": "w5-wire-types",
        "recorded": "2026-09-30",
        "why": _OMISSION_WHY[domain],
        "keys": keys,
    }


_REFUSED_SUBMIT_OMITTED = (
    "completion_jobs,response.summary,response.sections,response.message_id,"
    "response.elapsed_ms,response.sql_query,response.proof.sql_query,"
    "response.proof.data_freshness,response.proof.reasoning_trace,response.proof.message_id,"
    "response.proof.elapsed_ms,response.proof.generated_at,response.visualization,"
    "response.actions,response.metric_value,response.native_visualization,"
    "response.reasoning_trace,response.genie_status"
)
_REFUSED_TURN_OMITTED = (
    "summary,sections,message_id,elapsed_ms,sql_query,proof.sql_query,proof.data_freshness,"
    "proof.reasoning_trace,proof.message_id,proof.elapsed_ms,proof.generated_at,visualization,"
    "actions,metric_value,native_visualization,reasoning_trace,genie_status"
)

# 2xx samples that OMIT keys the real server always sends (quality-09 item 2),
# keyed by exported source. SHRINK-ONLY: populated once when the check landed
# (2026-09-30) from `node tools/export_e2e_fixtures.mjs --out <file>`; each
# entry must still reproduce exactly its keys (test below), so it is only
# ever narrowed or removed, never added or widened: a new partial body is
# completed in its fixture. Shape: {"finding", "owner", "recorded", "why",
# "keys"}, where "keys" is the omitted dotted paths, comma-joined. The
# domain's P3 PR (w5-wire-types, D-api-types-a4 part a4-ii) types its served
# bodies with the generated presence view and deletes its entries.
KNOWN_OMISSIONS: dict[str, dict[str, str]] = {
    "contractSamples.ts:CONTACTABLE_PORTFOLIO_PREVIEW": _omission("portfolio", "offers_available"),
    "contractSamples.ts:LIVE_SHAPED_HOME_PREVIEW": _omission("portfolio", "offers_available"),
    "contractSamples.ts:MAX_HOME_PREVIEW": _omission("portfolio", "offers_available"),
    "contractSamples.ts:QUEUE_LAYOUT_LEADS": _omission(
        "leads",
        _per_item(24, _LEAD_SUMMARY_OMITTED, {2: _QUEUE_LAYOUT_ITEM_2}),
    ),
    "contractSamples.ts:SIGNED_IN_APPROVER": _omission("session", "lender_name,rum_enabled"),
    "contractSamples.ts:decidedLifecycle(approved)": _omission("lifecycle", "outreach_at"),
    "contractSamples.ts:decidedLifecycle(rejected)": _omission("lifecycle", "outreach_at"),
    "contractSamples.ts:genieAnswerFixture()": _omission(
        "genie",
        (
            "summary,sections,visualization.series,native_visualization,reasoning_trace,"
            "refusal_reason,refusal_report_hash"
        ),
    ),
    "contractSamples.ts:genieDeepAnswerFixture()": _omission(
        "genie",
        (
            "sections[0].visualization.series,sections[0].visualization.reason,"
            "sections[0].narrative_withheld,sections[1].visualization.series,"
            "sections[1].visualization.reason,sections[1].narrative_withheld,visualization.series,"
            "native_visualization,reasoning_trace,refusal_reason,refusal_report_hash"
        ),
    ),
    "contractSamples.ts:portfolioCreated()": _omission("portfolio", "household_summary"),
    "contractSamples.ts:refusedSubmit(instruction_override)": _omission(
        "genie",
        _REFUSED_SUBMIT_OMITTED,
    ),
    "contractSamples.ts:refusedSubmit(out_of_scope)": _omission("genie", _REFUSED_SUBMIT_OMITTED),
    "contractSamples.ts:refusedSubmit(output_policy)": _omission("genie", _REFUSED_SUBMIT_OMITTED),
    "contractSamples.ts:refusedSubmit(outreach_instruction)": _omission(
        "genie",
        _REFUSED_SUBMIT_OMITTED,
    ),
    "contractSamples.ts:refusedSubmit(pii_request)": _omission("genie", _REFUSED_SUBMIT_OMITTED),
    "contractSamples.ts:refusedSubmit(protected_class)": _omission(
        "genie",
        _REFUSED_SUBMIT_OMITTED,
    ),
    "contractSamples.ts:refusedSubmit(scope_bypass)": _omission("genie", _REFUSED_SUBMIT_OMITTED),
    "contractSamples.ts:refusedSubmit(unknown)": _omission("genie", _REFUSED_SUBMIT_OMITTED),
    "contractSamples.ts:refusedSubmit(unreviewed_criterion)": _omission(
        "genie",
        _REFUSED_SUBMIT_OMITTED,
    ),
    "contractSamples.ts:refusedTurn(instruction_override)": _omission(
        "genie",
        _REFUSED_TURN_OMITTED,
    ),
    "contractSamples.ts:refusedTurn(out_of_scope)": _omission("genie", _REFUSED_TURN_OMITTED),
    "contractSamples.ts:refusedTurn(output_policy)": _omission("genie", _REFUSED_TURN_OMITTED),
    "contractSamples.ts:refusedTurn(outreach_instruction)": _omission(
        "genie",
        _REFUSED_TURN_OMITTED,
    ),
    "contractSamples.ts:refusedTurn(pii_request)": _omission("genie", _REFUSED_TURN_OMITTED),
    "contractSamples.ts:refusedTurn(protected_class)": _omission("genie", _REFUSED_TURN_OMITTED),
    "contractSamples.ts:refusedTurn(scope_bypass)": _omission("genie", _REFUSED_TURN_OMITTED),
    "contractSamples.ts:refusedTurn(unknown)": _omission("genie", _REFUSED_TURN_OMITTED),
    "contractSamples.ts:refusedTurn(unreviewed_criterion)": _omission(
        "genie",
        _REFUSED_TURN_OMITTED,
    ),
    "contractSamples.ts:registerGenieTurn submit": _omission("genie", "completion_jobs"),
    "data/genie.ts:genieSessionDetail(GENIE_LINK_HEX_ID)": _omission(
        "genie",
        (
            "turns[0].response.summary,turns[0].response.sections,turns[0].response.elapsed_ms,"
            "turns[0].response.question_hash,turns[0].response.sql_query,turns[0].response.row_count,"
            "turns[0].response.proof,turns[0].response.visualization,turns[0].response.actions,"
            "turns[0].response.metric_value,turns[0].response.table_rows,"
            "turns[0].response.native_visualization,turns[0].response.reasoning_trace,"
            "turns[0].response.refusal_reason,turns[0].response.refusal_report_hash"
        ),
    ),
    "data/genieJobs.ts:data/genieJobs.ts": _omission(
        "genie_jobs",
        (
            "typical_seconds,response.summary,response.sections,response.visualization.series,"
            "response.native_visualization,response.reasoning_trace,response.refusal_reason,"
            "response.refusal_report_hash"
        ),
    ),
    "data/genieReading.ts:data/genieReading.ts": _omission(
        "genie",
        (
            "summary,sections,native_visualization,reasoning_trace,refusal_reason,refusal_report_hash,"
            "sections[0].narrative_withheld,sections[1].narrative_withheld,"
            "sections[2].narrative_withheld,sections[3].narrative_withheld,"
            "sections[4].narrative_withheld,visualization.series"
        ),
    ),
    "data/queuePlace.ts:data/queuePlace.ts#LO_SESSION": _omission(
        "session",
        "actor_display_name,role_labels,lender_name,rum_enabled",
    ),
    "registry:GET /api/admin/assets/:assetKey/metadata": _omission(
        "admin",
        "columns[0].redacted,columns[1].redacted,columns[2].redacted,columns[3].redacted",
    ),
    "registry:GET /api/admin/sources": _omission(
        "admin",
        _per_item(7, "checked_at,synthetic_demo"),
    ),
    "registry:GET /api/borrowers/:id": _omission(
        "b360",
        _LEAD_SUMMARY_OMITTED + "," + _BORROWER_360_OMITTED,
    ),
    "registry:GET /api/borrowers/:id/lifecycle": _omission(
        "lifecycle",
        "approval_id,audit_event_id,approved_at,outreach_at",
    ),
    "registry:GET /api/borrowers/:id/proof": _omission(
        "proof",
        (
            "score_components[0].fair_lending_note,score_components[1].fair_lending_note,"
            "score_components[2].fair_lending_note,score_components[3].fair_lending_note,"
            "reproduce[0].databricks_sql_url,margins"
        ),
    ),
    "registry:GET /api/borrowers/search": _omission("leads", _per_item(8, _LEAD_SUMMARY_OMITTED)),
    "registry:GET /api/data-estate": _omission(
        "admin",
        (
            "lanes[0].assets[0].catalog_explorer_url,lanes[0].assets[0].synthetic_demo,"
            "lanes[0].assets[1].catalog_explorer_url,lanes[0].assets[1].synthetic_demo,"
            "lanes[1].assets[0].catalog_explorer_url,lanes[1].assets[0].synthetic_demo,"
            "lanes[2].assets[0].catalog_explorer_url,lanes[2].assets[0].synthetic_demo,"
            "lanes[2].assets[1].catalog_explorer_url,lanes[2].assets[1].synthetic_demo,"
            "lanes[3].assets[0].catalog_explorer_url"
        ),
    ),
    "registry:GET /api/leads": _omission("leads", _per_item(24, _LEAD_SUMMARY_OMITTED)),
    "registry:GET /api/session": _omission("session", "actor_display_name,role_labels"),
    "registry:POST /api/portfolio/preview": _omission("portfolio", "offers_available"),
}
