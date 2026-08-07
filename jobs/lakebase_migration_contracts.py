"""Reviewed Lakebase object, privilege, and executable-hook contracts."""

from __future__ import annotations

import re
from typing import NamedTuple

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
# argument names cannot destabilize the matrix; a new overload fails inventory
# postflight until reviewed. Callable helpers must also have zero argument
# defaults and no stored dependency on a cloud_admin-owned public routine.
_APP_ROLE_ROUTINE_PRIVILEGES: dict[tuple[str, str], tuple[str, ...]] = {
    ("campaign_jsonb_has_only_keys", "jsonb, text[]"): ("EXECUTE",),
    ("campaign_jsonb_text_array_is_reviewed", "jsonb, text, integer"): ("EXECUTE",),
    (
        "campaign_jsonb_bounded_nonnegative_integer",
        "jsonb, text, bigint",
    ): ("EXECUTE",),
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
    (
        "campaign_household_summary_is_reviewed",
        "jsonb, jsonb, bigint, bigint",
    ): ("EXECUTE",),
    (
        "campaign_creation_response_is_reviewed",
        "jsonb, text, bigint, jsonb",
    ): ("EXECUTE",),
    ("enforce_campaign_json_contract", ""): (),
    ("enforce_campaign_treatment_boundary", ""): (),
    ("prevent_action_audit_mutation", ""): (),
    ("enforce_audit_event_finalize_only", ""): (),
    ("prevent_outreach_evidence_mutation", ""): (),
    ("enforce_ai_gateway_proof_timestamp_bounds", ""): (),
    ("enforce_campaign_decision_lifecycle", ""): (),
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
        "trg_approvals_campaign_lifecycle",
    ): ("mip_app", "enforce_campaign_decision_lifecycle", "", 7),
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


class _ManagedEventTriggerContractRow(NamedTuple):
    """Exact Databricks Lakebase provider-plane event-trigger shape."""

    event: str
    enabled: str
    tags: tuple[str, ...] | None
    event_owner: str
    function_schema: str
    function_name: str
    function_arguments: str
    function_kind: str
    function_return_type: str
    function_security_definer: bool
    function_owner: str
    function_language: str
    function_volatility: str
    function_parallel_safety: str
    function_leakproof: bool
    function_strict: bool
    function_config: tuple[str, ...] | None
    function_binary: str | None
    function_source_sha256: str
    function_source_bytes: int


# Lakebase installs these cloud_admin-owned DDL hooks in every managed
# database. They assign provider-plane gateway/superuser/reader/writer grants
# to objects created after provisioning. Because they execute during both the
# schema and ACL transactions, bind the complete inventory and raw UTF-8
# pg_proc.prosrc digest rather than trusting names or ownership alone. A plain
# local PostgreSQL fixture may explicitly opt into an absent inventory; the
# production path has no such opt-out.
_MANAGED_EVENT_TRIGGER_CONTRACT: dict[str, _ManagedEventTriggerContractRow] = {
    "on_create_schema": _ManagedEventTriggerContractRow(
        event="ddl_command_end",
        enabled="O",
        tags=("CREATE SCHEMA",),
        event_owner="cloud_admin",
        function_schema="public",
        function_name="grant_usage_on_new_schema",
        function_arguments="",
        function_kind="f",
        function_return_type="event_trigger",
        function_security_definer=False,
        function_owner="cloud_admin",
        function_language="plpgsql",
        function_volatility="v",
        function_parallel_safety="u",
        function_leakproof=False,
        function_strict=False,
        function_config=None,
        function_binary=None,
        function_source_sha256=("f8bab6f3ee88910938aaf7f2639fda82627f15d615ac497f91853d9822d3c65b"),
        function_source_bytes=1244,
    ),
    "on_create_sequence": _ManagedEventTriggerContractRow(
        event="ddl_command_end",
        enabled="O",
        tags=("CREATE SEQUENCE",),
        event_owner="cloud_admin",
        function_schema="public",
        function_name="grant_all_on_new_sequences",
        function_arguments="",
        function_kind="f",
        function_return_type="event_trigger",
        function_security_definer=False,
        function_owner="cloud_admin",
        function_language="plpgsql",
        function_volatility="v",
        function_parallel_safety="u",
        function_leakproof=False,
        function_strict=False,
        function_config=None,
        function_binary=None,
        function_source_sha256=("e5a5d3ac90274b875777ed4bd2ee3430fb860759d66ecb0bce2591b29f1761ba"),
        function_source_bytes=736,
    ),
    "on_create_table_or_view": _ManagedEventTriggerContractRow(
        event="ddl_command_end",
        enabled="O",
        tags=(
            "CREATE MATERIALIZED VIEW",
            "CREATE TABLE",
            "CREATE TABLE AS",
            "CREATE VIEW",
        ),
        event_owner="cloud_admin",
        function_schema="public",
        function_name="grant_select_on_new_objects",
        function_arguments="",
        function_kind="f",
        function_return_type="event_trigger",
        function_security_definer=False,
        function_owner="cloud_admin",
        function_language="plpgsql",
        function_volatility="v",
        function_parallel_safety="u",
        function_leakproof=False,
        function_strict=False,
        function_config=None,
        function_binary=None,
        function_source_sha256=("36601edce210b90953d4b1e84e84ad92fd9d77cc48d5dff7c79086cae5aacb82"),
        function_source_bytes=1189,
    ),
}

# PostgreSQL represents the default owner-plus-PUBLIC EXECUTE state as NULL.
# Earlier hardened databases can retain the exact cloud_admin-owner-only state.
# Both are provider-owned, immutable representations; runtime lookup is denied
# at the public-schema boundary and no runtime/arbitrary grantee is permitted.
_MANAGED_EVENT_TRIGGER_FUNCTION_ACLS: frozenset[tuple[str, ...] | None] = frozenset(
    {None, ("cloud_admin=X/cloud_admin",)}
)

_MANAGED_OAUTH_ROLE_FUNCTION_SOURCE_SHA256 = (
    "c7d206fd75bb46ac9ae7e7eab342d9fd4ca57495d563547f32939ccf3a546c2e"
)
_MANAGED_OAUTH_ROLE_FUNCTION_SOURCE_BYTES = 25
_MANAGED_OAUTH_ROLE_FUNCTION_OWNER_ONLY_ACL = ("cloud_admin=X/cloud_admin",)
_MANAGED_OAUTH_ROLE_FUNCTION_PUBLIC_ACLS: frozenset[tuple[str, ...] | None] = frozenset(
    {
        None,
        ("=X/cloud_admin", "cloud_admin=X/cloud_admin"),
    }
)
_MANAGED_OAUTH_ROLE_FUNCTION_ACLS = frozenset(
    {
        *_MANAGED_OAUTH_ROLE_FUNCTION_PUBLIC_ACLS,
        _MANAGED_OAUTH_ROLE_FUNCTION_OWNER_ONLY_ACL,
    }
)
_PROVIDER_SCHEMA_NAME = "__db_system"
_PROVIDER_SCHEMA_OWNER = "databricks_control_plane"
_PROVIDER_DATABASE_WRITER_ROLE_PREFIX = "databricks_writer_"

_MANAGED_PROVIDER_PUBLIC_VIEW_CONTRACT: dict[str, tuple[str, int]] = {
    "databricks_list_roles": (
        "1e8fddb3712aa261c3db4a803f4f38300cac2fa85bb2ab953a9452f93807480a",
        127,
    ),
    "databricks_synced_table_managers": (
        "39a92930ceb1d900cd1b929fc45fc4d72392fbfe8694f143daacbbbc87cb09ad",
        173,
    ),
}

# Lakebase OAuth service-principal roles created through the documented
# databricks_create_role SQL function report this exact LOGIN-only profile.
# The legacy Database Instances create-role path can silently set REPLICATION;
# that profile is forbidden because a live IDENTIFY_SYSTEM probe proved the
# capability is executable, not merely a catalog marker.
_MANAGED_OAUTH_ROLE_ATTRIBUTE_PROFILE = (
    False,  # rolsuper
    False,  # rolcreaterole
    False,  # rolcreatedb
    False,  # rolreplication
    False,  # rolbypassrls
    True,  # rolinherit
    True,  # rolcanlogin
)

# These seven constraints are the only retained schema expressions allowed to
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
    (
        "mip_app",
        "campaigns",
        "campaigns_ready_treatment_manifest_chk",
    ): frozenset(
        {
            (
                "campaign_household_summary_is_reviewed",
                "jsonb, jsonb, bigint, bigint",
            ),
            (
                "campaign_creation_response_is_reviewed",
                "jsonb, text, bigint, jsonb",
            ),
            ("campaign_holdout_is_reviewed", "jsonb"),
        }
    ),
}

# The immediately preceding production schema used the same ready-manifest
# constraint name without application-routine dependencies. Permit only its
# exact rendered expression during this upgrade transition, then quarantine it
# under lock like the current custom-code constraint. Once schema.sql runs, the
# recurring contract above is the only accepted shape.
_QUARANTINED_CONSTRAINT_LEGACY_EXPRESSION_CONTRACT: dict[
    tuple[str, str, str],
    frozenset[str],
] = {
    (
        "mip_app",
        "campaigns",
        "campaigns_ready_treatment_manifest_chk",
    ): frozenset(
        {
            "CHECK (treatment_state <> 'ready'::text OR "
            "treatment_materialization_id IS NOT NULL AND "
            "treatment_algorithm_version = 'campaign-treatment-v2'::text AND "
            "treatment_contract_fingerprint ~ '^[0-9a-f]{64}$'::text AND "
            "treatment_fingerprint ~ '^[0-9a-f]{64}$'::text AND "
            "treatment_source_snapshot_id ~ '^[0-9a-f]{64}$'::text AND "
            "treatment_delta_version >= 0 AND "
            "treatment_assignment_digest ~ '^[0-9a-f]{64}$'::text AND "
            "treatment_candidate_count IS NOT NULL AND "
            "treatment_selected_primary_count IS NOT NULL AND "
            "treatment_count IS NOT NULL AND "
            "treatment_holdout_count IS NOT NULL AND "
            "treatment_materialized_at IS NOT NULL)"
        }
    ),
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
        "and",
        "any",
        "btrim",
        "check",
        "coalesce",
        "gen_random_uuid",
        "left",
        "length",
        "nextval",
        "not",
        "now",
        "nullif",
        "or",
        # Storage-size measurement only (genie_messages response budget check);
        # no I/O, settings, or side effects -- same review class as length().
        "pg_column_size",
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
_SQL_STRING_LITERAL_RE = re.compile(r"(?is)(?<![a-z0-9_$])(?:E'(?:''|\\.|[^'])*'|'(?:''|[^'])*')")
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
# The application database is dedicated to Module 0. Runtime identities must
# not inherit PostgreSQL's default ``public`` schema lookup surface: managed
# Lakebase installs provider-owned routines there whose ACLs cannot be altered
# by the database deployer. Stored expressions are reviewed separately by the
# executable-hook contract, so no ambient runtime schema access is required.
_APP_ROLE_OPTIONAL_BASELINE_SCHEMA_PRIVILEGES: frozenset[tuple[str, str]] = frozenset()
_MANAGED_OAUTH_ROLE_ATTRIBUTE_NAMES = (
    "rolsuper",
    "rolcreaterole",
    "rolcreatedb",
    "rolreplication",
    "rolbypassrls",
    "rolinherit",
    "rolcanlogin",
)
# Stable compatibility seam for tests and external audit tooling that consume
# the original five security-sensitive attribute names.
_UNSAFE_ROLE_ATTRIBUTE_NAMES = _MANAGED_OAUTH_ROLE_ATTRIBUTE_NAMES[:5]
