"""Reviewed Lakebase object, privilege, and executable-hook contracts."""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# The app role is the Databricks App service principal client id. Lakebase
# bindings provision that exact value as the Postgres role name; app names,
# display names, and numeric ids are not interchangeable identities.
#
# Every base table is listed, including migration/operator-owned tables with
# no app privileges. Postflight compares this inventory to pg_class so a new
# table fails closed until its runtime access is reviewed explicitly.
# ---------------------------------------------------------------------------
_APP_ROLE_TABLE_PRIVILEGES: dict[str, tuple[str, ...]] = {
    "schema_migrations": (),
    "campaigns": ("SELECT", "INSERT", "UPDATE"),
    "campaign_message_variants": ("SELECT", "INSERT"),
    "tenant_disclosures": ("SELECT",),
    "sales_team": ("SELECT",),
    "lead_assignments": ("SELECT", "INSERT", "UPDATE"),
    "call_dispositions": ("SELECT", "INSERT", "UPDATE"),
    "approvals": ("SELECT", "INSERT", "UPDATE"),
    "saved_leads": ("SELECT", "INSERT", "UPDATE"),
    "outreach_drafts": ("SELECT", "INSERT", "UPDATE"),
    "activation_destinations": ("SELECT",),
    "activation_outbox": ("SELECT", "INSERT", "UPDATE"),
    "lead_outcomes": ("SELECT", "INSERT", "UPDATE"),
    "action_audit": ("SELECT", "INSERT"),
    "action_audit_archive_runs": (),
    "generated_outreach_drafts": ("SELECT", "INSERT"),
    "genie_sessions": ("SELECT", "INSERT", "UPDATE"),
    "genie_messages": ("SELECT", "INSERT"),
    "genie_cohorts": ("SELECT", "INSERT"),
    "genie_cohort_members": ("SELECT", "INSERT", "UPDATE"),
    "agent_sessions": (),
    "growth_agent_runs": ("SELECT", "INSERT", "UPDATE"),
    "growth_agent_monitors": ("SELECT", "INSERT", "UPDATE"),
    "growth_agent_notification_drafts": ("SELECT", "INSERT", "UPDATE"),
    "ai_gateway_proof_ledger": ("SELECT",),
    "feedback": ("SELECT", "INSERT", "UPDATE"),
    "loan_officers": ("SELECT",),
    "kpi_snapshots": ("SELECT",),
    "user_visits": ("SELECT", "INSERT"),
    "genie_feedback_requests": ("SELECT", "INSERT", "UPDATE"),
}

_APP_ROLE_SEQUENCE_PRIVILEGES: dict[str, tuple[str, ...]] = {
    "action_audit_audit_sequence_seq": ("USAGE",),
}

# Only immutable, SECURITY INVOKER validation helpers are callable by the app.
# Trigger functions remain executable by PostgreSQL's trigger machinery without
# being exposed as a runtime API. Signatures use oidvectortypes(proargtypes), so
# argument names/defaults cannot destabilize the matrix; a new overload fails
# inventory postflight until it is reviewed explicitly.
_APP_ROLE_ROUTINE_PRIVILEGES: dict[tuple[str, str], tuple[str, ...]] = {
    ("campaign_jsonb_has_only_keys", "jsonb, text[]"): ("EXECUTE",),
    ("campaign_jsonb_text_array_is_reviewed", "jsonb, text, integer"): ("EXECUTE",),
    ("campaign_portfolio_criteria_is_reviewed", "jsonb"): ("EXECUTE",),
    ("campaign_replay_filters_are_reviewed", "jsonb"): ("EXECUTE",),
    ("campaign_criteria_is_reviewed", "jsonb"): ("EXECUTE",),
    ("campaign_suppression_policy_is_reviewed", "jsonb"): ("EXECUTE",),
    ("campaign_channel_cascade_is_reviewed", "jsonb"): ("EXECUTE",),
    ("campaign_send_window_is_reviewed", "jsonb"): ("EXECUTE",),
    ("campaign_holdout_is_reviewed", "jsonb"): ("EXECUTE",),
    ("campaign_roi_assumptions_is_reviewed", "jsonb"): ("EXECUTE",),
    (
        "campaign_json_contract_is_reviewed",
        "jsonb, jsonb, jsonb, jsonb, jsonb, jsonb",
    ): ("EXECUTE",),
    ("enforce_campaign_json_contract", ""): (),
    ("enforce_campaign_treatment_boundary", ""): (),
    ("prevent_action_audit_mutation", ""): (),
    ("enforce_audit_event_finalize_only", ""): (),
    ("prevent_outreach_evidence_mutation", ""): (),
    ("enforce_ai_gateway_proof_timestamp_bounds", ""): (),
    ("enforce_approval_finalize_only", ""): (),
}

# Exact non-internal trigger surface for the dedicated application-state
# database. Values bind the trigger function identity/signature and PostgreSQL
# tgtype bitmask (ROW=1, BEFORE=2, INSERT=4, DELETE=8, UPDATE=16,
# TRUNCATE=32). Postflight additionally requires enabled-origin state, no
# trigger arguments/WHEN clause/constraint binding, SECURITY INVOKER, trigger
# return type, matching table/function owners, no runtime-role ownership, no
# UPDATE OF column list or transition relations, and non-deferred execution.
_APP_TRIGGER_CONTRACT: dict[
    tuple[str, str, str],
    tuple[str, str, str, int],
] = {
    (
        "mip_app",
        "campaigns",
        "trg_campaigns_json_contract_enforcement",
    ): ("mip_app", "enforce_campaign_json_contract", "", 23),
    (
        "mip_app",
        "campaigns",
        "trg_campaigns_treatment_boundary",
    ): ("mip_app", "enforce_campaign_treatment_boundary", "", 23),
    (
        "mip_app",
        "action_audit",
        "trg_action_audit_append_only",
    ): ("mip_app", "prevent_action_audit_mutation", "", 58),
    (
        "mip_app",
        "ai_gateway_proof_ledger",
        "trg_ai_gateway_proof_timestamp_bounds",
    ): ("mip_app", "enforce_ai_gateway_proof_timestamp_bounds", "", 23),
    (
        "mip_app",
        "generated_outreach_drafts",
        "trg_generated_outreach_drafts_immutable",
    ): ("mip_app", "prevent_outreach_evidence_mutation", "", 58),
    (
        "mip_app",
        "campaign_message_variants",
        "trg_campaign_message_variants_immutable",
    ): ("mip_app", "prevent_outreach_evidence_mutation", "", 58),
    (
        "mip_app",
        "approvals",
        "trg_approvals_finalize_only",
    ): ("mip_app", "enforce_approval_finalize_only", "", 19),
    (
        "mip_app",
        "approvals",
        "trg_approvals_no_remove",
    ): ("mip_app", "prevent_outreach_evidence_mutation", "", 42),
    (
        "mip_app",
        "call_dispositions",
        "trg_call_dispositions_finalize_only",
    ): ("mip_app", "enforce_audit_event_finalize_only", "", 19),
    (
        "mip_app",
        "call_dispositions",
        "trg_call_dispositions_no_remove",
    ): ("mip_app", "prevent_outreach_evidence_mutation", "", 42),
    (
        "mip_app",
        "lead_outcomes",
        "trg_lead_outcomes_finalize_only",
    ): ("mip_app", "enforce_audit_event_finalize_only", "", 19),
    (
        "mip_app",
        "lead_outcomes",
        "trg_lead_outcomes_no_remove",
    ): ("mip_app", "prevent_outreach_evidence_mutation", "", 42),
    (
        "mip_app",
        "growth_agent_runs",
        "trg_growth_agent_runs_finalize_only",
    ): ("mip_app", "enforce_audit_event_finalize_only", "", 19),
    (
        "mip_app",
        "growth_agent_runs",
        "trg_growth_agent_runs_no_remove",
    ): ("mip_app", "prevent_outreach_evidence_mutation", "", 42),
}

# These six constraints are the only retained schema expressions allowed to
# depend on application-owned code before an upgrade. Their exact dependency
# is reviewed here and they are dropped under lock before schema.sql runs, so a
# replaced function body cannot execute during an earlier ALTER/backfill. The
# post-seed suffix recreates and validates the constraints transactionally.
_QUARANTINED_CONSTRAINT_ROUTINE_CONTRACT: dict[
    tuple[str, str, str],
    frozenset[tuple[str, str]],
] = {
    (
        "mip_app",
        "campaigns",
        "campaigns_criteria_reviewed_shape_chk",
    ): frozenset({("campaign_criteria_is_reviewed", "jsonb")}),
    (
        "mip_app",
        "campaigns",
        "campaigns_suppression_policy_reviewed_shape_chk",
    ): frozenset({("campaign_suppression_policy_is_reviewed", "jsonb")}),
    (
        "mip_app",
        "campaigns",
        "campaigns_channel_cascade_reviewed_shape_chk",
    ): frozenset({("campaign_channel_cascade_is_reviewed", "jsonb")}),
    (
        "mip_app",
        "campaigns",
        "campaigns_send_window_reviewed_shape_chk",
    ): frozenset({("campaign_send_window_is_reviewed", "jsonb")}),
    (
        "mip_app",
        "campaigns",
        "campaigns_holdout_reviewed_shape_chk",
    ): frozenset({("campaign_holdout_is_reviewed", "jsonb")}),
    (
        "mip_app",
        "campaigns",
        "campaigns_roi_assumptions_reviewed_shape_chk",
    ): frozenset({("campaign_roi_assumptions_is_reviewed", "jsonb")}),
}

# pg_depend omits pinned built-ins, so executable-hook review has two layers:
# exact catalog dependency identities when PostgreSQL records them, plus a
# lexical function-call allowlist over pg_get_expr/pg_get_constraintdef output.
# This prevents a preserved expression from introducing privileged built-ins
# such as pg_read_file, current_setting, set_config, or lo_import merely because
# they live in pg_catalog.
_SAFE_SCHEMA_HOOK_PG_CATALOG_ROUTINES = frozenset(
    {
        ("now", ""),
        ("gen_random_uuid", ""),
        ("nextval", "regclass"),
        ("length", "text"),
        ("btrim", "text"),
        ("left", "text, integer"),
    }
)
_SAFE_SCHEMA_HOOK_FUNCTION_NAMES = frozenset(
    {
        "all",
        "any",
        "btrim",
        "check",
        "coalesce",
        "gen_random_uuid",
        "left",
        "length",
        "nextval",
        "now",
        "nullif",
    }
    | {
        name
        for names in _QUARANTINED_CONSTRAINT_ROUTINE_CONTRACT.values()
        for name, _arguments in names
    }
)
_SAFE_SCHEMA_HOOK_PG_CATALOG_OPERATORS = frozenset(
    {
        (operator, f"{type_name}, {type_name}")
        for type_name in (
            "bigint",
            "boolean",
            "date",
            "double precision",
            "integer",
            "numeric",
            "smallint",
            "text",
            "timestamp with time zone",
            "uuid",
        )
        for operator in ("=", "<>", "<", "<=", ">", ">=")
    }
    | {
        ("=", "smallint, integer"),
        ("=", "integer, smallint"),
        ("=", "integer, bigint"),
        ("=", "bigint, integer"),
        ("~~", "text, text"),
        ("~", "text, text"),
    }
)
_AUDIT_SEQUENCE_DEFAULT_KEY = ("mip_app", "action_audit", "audit_sequence")
_AUDIT_SEQUENCE_DEFAULT_EXPRESSION = "nextval('mip_app.action_audit_audit_sequence_seq'::regclass)"
_SQL_STRING_LITERAL_RE = re.compile(r"(?is)\bE?'(?:''|[^'])*'")
_SQL_FUNCTION_CALL_RE = re.compile(
    r'(?ix)(?:(?:"[^"]+"|[a-z_][a-z0-9_$]*)\s*\.\s*)?' r'(?P<name>"[^"]+"|[a-z_][a-z0-9_$]*)\s*\('
)

_AI_GATEWAY_VERIFIER_TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE")
_VERIFIER_OPTIONAL_APP_ENVS = frozenset(
    {"local", "test", "testing", "pytest", "dev", "development", "sandbox"}
)
_VERIFIER_OPTIONAL_BUNDLE_TARGETS = frozenset({"", "local", "test", "ci", "dev"})

_TABLE_PRIVILEGE_NAMES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)
_SEQUENCE_PRIVILEGE_NAMES = ("USAGE", "SELECT", "UPDATE")
_COLUMN_PRIVILEGE_NAMES = ("SELECT", "INSERT", "UPDATE", "REFERENCES")
_SCHEMA_PRIVILEGE_NAMES = ("USAGE", "CREATE")
_APP_ROLE_OPTIONAL_BASELINE_SCHEMA_PRIVILEGES = frozenset({("public", "USAGE")})
_UNSAFE_ROLE_ATTRIBUTE_NAMES = (
    "rolsuper",
    "rolcreaterole",
    "rolcreatedb",
    "rolreplication",
    "rolbypassrls",
)
