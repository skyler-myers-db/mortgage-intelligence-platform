#!/usr/bin/env python3
# ruff: noqa: E402
"""Apply the governed Lakebase schema, seed, integrity proofs, and grants.

This file remains the Databricks Jobs Python-task entrypoint and the stable
import facade used by deployment tests. Production implementation is split by
responsibility across ``jobs/lakebase_migration_*.py`` modules.
"""

from __future__ import annotations

import argparse
import os
import sys
import time as time
from pathlib import Path


def _bootstrap_repo_imports() -> None:
    """Make absolute repository imports work for direct file execution."""

    if globals().get("__package__"):
        return
    try:
        repo_root = Path(__file__).resolve().parents[1]
    except NameError:
        repo_root = Path.cwd()
        if not (repo_root / "jobs" / "lakebase_migrate.py").is_file():
            for candidate in (Path.cwd().parent, Path("/Workspace/Users")):
                match = next(candidate.rglob("jobs/lakebase_migrate.py"), None)
                if match is not None:
                    repo_root = match.parents[1]
                    break
    repo_root_text = str(repo_root)
    if repo_root_text not in sys.path:
        sys.path.insert(0, repo_root_text)


_bootstrap_repo_imports()

from jobs.lakebase_migration_acl import (  # noqa: E402
    _postflight_direct_column_privileges as _postflight_direct_column_privileges,
)
from jobs.lakebase_migration_acl import (
    _postflight_effective_column_only_privileges as _postflight_effective_column_only_privileges,
)
from jobs.lakebase_migration_acl import (
    _postflight_effective_default_privileges as _postflight_effective_default_privileges,
)
from jobs.lakebase_migration_acl import (
    _postflight_effective_routine_privileges as _postflight_effective_routine_privileges,
)
from jobs.lakebase_migration_acl import (
    _postflight_effective_schema_privileges as _postflight_effective_schema_privileges,
)
from jobs.lakebase_migration_acl import (
    _postflight_role_security as _postflight_role_security,
)
from jobs.lakebase_migration_connection import (  # noqa: E402
    _repo_root as _repo_root,
)
from jobs.lakebase_migration_connection import (
    _resolve_connection as _resolve_connection,
)
from jobs.lakebase_migration_contracts import (  # noqa: E402
    _AI_GATEWAY_VERIFIER_TABLE_PRIVILEGES as _AI_GATEWAY_VERIFIER_TABLE_PRIVILEGES,
)
from jobs.lakebase_migration_contracts import (
    _APP_ROLE_OPTIONAL_BASELINE_SCHEMA_PRIVILEGES as _APP_ROLE_OPTIONAL_BASELINE_SCHEMA_PRIVILEGES,
)
from jobs.lakebase_migration_contracts import (
    _APP_ROLE_ROUTINE_PRIVILEGES as _APP_ROLE_ROUTINE_PRIVILEGES,
)
from jobs.lakebase_migration_contracts import (
    _APP_ROLE_SEQUENCE_PRIVILEGES as _APP_ROLE_SEQUENCE_PRIVILEGES,
)
from jobs.lakebase_migration_contracts import (
    _APP_ROLE_TABLE_PRIVILEGES as _APP_ROLE_TABLE_PRIVILEGES,
)
from jobs.lakebase_migration_contracts import (
    _APP_TRIGGER_CONTRACT as _APP_TRIGGER_CONTRACT,
)
from jobs.lakebase_migration_contracts import (
    _AUDIT_SEQUENCE_DEFAULT_EXPRESSION as _AUDIT_SEQUENCE_DEFAULT_EXPRESSION,
)
from jobs.lakebase_migration_contracts import (
    _AUDIT_SEQUENCE_DEFAULT_KEY as _AUDIT_SEQUENCE_DEFAULT_KEY,
)
from jobs.lakebase_migration_contracts import (
    _COLUMN_PRIVILEGE_NAMES as _COLUMN_PRIVILEGE_NAMES,
)
from jobs.lakebase_migration_contracts import (
    _MANAGED_EVENT_TRIGGER_CONTRACT as _MANAGED_EVENT_TRIGGER_CONTRACT,
)
from jobs.lakebase_migration_contracts import (
    _MANAGED_EVENT_TRIGGER_FUNCTION_ACLS as _MANAGED_EVENT_TRIGGER_FUNCTION_ACLS,
)
from jobs.lakebase_migration_contracts import (
    _MANAGED_OAUTH_ROLE_ATTRIBUTE_NAMES as _MANAGED_OAUTH_ROLE_ATTRIBUTE_NAMES,
)
from jobs.lakebase_migration_contracts import (
    _MANAGED_OAUTH_ROLE_ATTRIBUTE_PROFILE as _MANAGED_OAUTH_ROLE_ATTRIBUTE_PROFILE,
)
from jobs.lakebase_migration_contracts import (
    _MANAGED_OAUTH_ROLE_FUNCTION_SOURCE_BYTES as _MANAGED_OAUTH_ROLE_FUNCTION_SOURCE_BYTES,
)
from jobs.lakebase_migration_contracts import (
    _MANAGED_OAUTH_ROLE_FUNCTION_SOURCE_SHA256 as _MANAGED_OAUTH_ROLE_FUNCTION_SOURCE_SHA256,
)
from jobs.lakebase_migration_contracts import (
    _MANAGED_PROVIDER_PUBLIC_VIEW_CONTRACT as _MANAGED_PROVIDER_PUBLIC_VIEW_CONTRACT,
)
from jobs.lakebase_migration_contracts import (
    _QUARANTINED_CONSTRAINT_LEGACY_EXPRESSION_CONTRACT as _QUARANTINED_CONSTRAINT_LEGACY_EXPRESSION_CONTRACT,
)
from jobs.lakebase_migration_contracts import (
    _QUARANTINED_CONSTRAINT_ROUTINE_CONTRACT as _QUARANTINED_CONSTRAINT_ROUTINE_CONTRACT,
)
from jobs.lakebase_migration_contracts import (
    _SAFE_SCHEMA_HOOK_FUNCTION_NAMES as _SAFE_SCHEMA_HOOK_FUNCTION_NAMES,
)
from jobs.lakebase_migration_contracts import (
    _SAFE_SCHEMA_HOOK_PG_CATALOG_OPERATORS as _SAFE_SCHEMA_HOOK_PG_CATALOG_OPERATORS,
)
from jobs.lakebase_migration_contracts import (
    _SAFE_SCHEMA_HOOK_PG_CATALOG_ROUTINES as _SAFE_SCHEMA_HOOK_PG_CATALOG_ROUTINES,
)
from jobs.lakebase_migration_contracts import (
    _SCHEMA_PRIVILEGE_NAMES as _SCHEMA_PRIVILEGE_NAMES,
)
from jobs.lakebase_migration_contracts import (
    _SEQUENCE_PRIVILEGE_NAMES as _SEQUENCE_PRIVILEGE_NAMES,
)
from jobs.lakebase_migration_contracts import (
    _SQL_FUNCTION_CALL_RE as _SQL_FUNCTION_CALL_RE,
)
from jobs.lakebase_migration_contracts import (
    _SQL_STRING_LITERAL_RE as _SQL_STRING_LITERAL_RE,
)
from jobs.lakebase_migration_contracts import (
    _TABLE_PRIVILEGE_NAMES as _TABLE_PRIVILEGE_NAMES,
)
from jobs.lakebase_migration_contracts import (
    _UNSAFE_ROLE_ATTRIBUTE_NAMES as _UNSAFE_ROLE_ATTRIBUTE_NAMES,
)
from jobs.lakebase_migration_contracts import (
    _VERIFIER_OPTIONAL_APP_ENVS as _VERIFIER_OPTIONAL_APP_ENVS,
)
from jobs.lakebase_migration_contracts import (
    _VERIFIER_OPTIONAL_BUNDLE_TARGETS as _VERIFIER_OPTIONAL_BUNDLE_TARGETS,
)
from jobs.lakebase_migration_grants import (  # noqa: E402
    _apply_app_role_grants as _apply_app_role_grants_impl,
)
from jobs.lakebase_migration_grants import (
    _preflight_database_roles as _preflight_database_roles_impl,
)
from jobs.lakebase_migration_integrity import _POST_SEED_MARKER as _POST_SEED_MARKER
from jobs.lakebase_migration_integrity import (  # noqa: E402
    _expect_database_rejection as _expect_database_rejection,
)
from jobs.lakebase_migration_integrity import (
    _run_outreach_integrity_probe as _run_outreach_integrity_probe,
)
from jobs.lakebase_migration_integrity import (
    _split_schema_sql as _split_schema_sql,
)
from jobs.lakebase_migration_postflight import (  # noqa: E402
    _postflight_ai_gateway_verifier_grants as _postflight_ai_gateway_verifier_grants,
)
from jobs.lakebase_migration_postflight import (
    _postflight_app_role_grants as _postflight_app_role_grants,
)
from jobs.lakebase_migration_provider_plane import (
    _close_public_schema_boundary as _close_public_schema_boundary,
)
from jobs.lakebase_migration_provider_plane import (
    _postflight_no_pre_boundary_sessions as _postflight_no_pre_boundary_sessions,
)
from jobs.lakebase_migration_provider_plane import (  # noqa: E402
    _postflight_provider_schema_boundary as _postflight_provider_schema_boundary,
)
from jobs.lakebase_migration_provider_plane import (
    _postflight_public_schema_boundary as _postflight_public_schema_boundary,
)
from jobs.lakebase_migration_roles import (
    _raise_object_inventory_mismatch as _raise_object_inventory_mismatch,
)
from jobs.lakebase_migration_roles import (  # noqa: E402
    _resolve_ai_gateway_verifier_role as _resolve_ai_gateway_verifier_role,
)
from jobs.lakebase_migration_roles import (
    _resolve_app_role as _resolve_app_role,
)
from jobs.lakebase_migration_schema_hooks import (  # noqa: E402
    _postflight_event_trigger_inventory as _postflight_event_trigger_inventory,
)
from jobs.lakebase_migration_schema_hooks import (
    _postflight_oauth_role_function_contract as _postflight_oauth_role_function_contract,
)
from jobs.lakebase_migration_schema_hooks import (
    _postflight_trigger_inventory as _postflight_trigger_inventory,
)
from jobs.lakebase_migration_schema_hooks import (
    _preflight_executable_schema_hooks as _preflight_executable_schema_hooks,
)
from jobs.lakebase_migration_schema_hooks import (
    _quarantine_existing_reviewed_triggers as _quarantine_existing_reviewed_triggers,
)
from jobs.lakebase_migration_schema_hooks import (
    _quarantine_reviewed_constraints as _quarantine_reviewed_constraints,
)
from jobs.lakebase_migration_schema_hooks import (
    _schema_hook_function_calls as _schema_hook_function_calls,
)
from jobs.lakebase_migration_seed import _sql_literal as _sql_literal
from jobs.lakebase_migration_seed import (  # noqa: E402
    _tenant_disclosure_seed_sql as _tenant_disclosure_seed_sql,
)
from jobs.lakebase_migration_transaction import (  # noqa: E402
    _run_transaction as _run_transaction_impl,
)


def _apply_app_role_grants(
    conn_kwargs: dict,
    *,
    app_name: str | None = None,
    ai_gateway_verifier_client_id: str | None = None,
    require_ai_gateway_verifier: bool = False,
    role_wait_timeout_s: float | None = None,
    role_wait_interval_s: float | None = None,
    allow_absent_managed_event_triggers: bool = False,
    allow_absent_provider_schema: bool = False,
    resolved_roles: tuple[str, str | None] | None = None,
) -> None:
    """Reconcile grants while preserving facade monkeypatch seams."""

    _apply_app_role_grants_impl(
        conn_kwargs,
        app_name=app_name,
        role_wait_timeout_s=role_wait_timeout_s,
        role_wait_interval_s=role_wait_interval_s,
        allow_absent_managed_event_triggers=allow_absent_managed_event_triggers,
        allow_absent_provider_schema=allow_absent_provider_schema,
        resolved_roles=resolved_roles,
        _resolve_app_role_fn=_resolve_app_role,
        _resolve_verifier_role_fn=lambda: _resolve_ai_gateway_verifier_role(
            ai_gateway_verifier_client_id,
            required=require_ai_gateway_verifier,
        ),
    )


def _preflight_database_roles(
    conn_kwargs: dict,
    *,
    app_name: str | None = None,
    ai_gateway_verifier_client_id: str | None = None,
    require_ai_gateway_verifier: bool = False,
    role_wait_timeout_s: float | None = None,
    role_wait_interval_s: float | None = None,
) -> tuple[str, str | None]:
    """Resolve and prove exact roles while preserving facade monkeypatch seams."""

    return _preflight_database_roles_impl(
        conn_kwargs,
        app_name=app_name,
        role_wait_timeout_s=role_wait_timeout_s,
        role_wait_interval_s=role_wait_interval_s,
        _resolve_app_role_fn=_resolve_app_role,
        _resolve_verifier_role_fn=lambda: _resolve_ai_gateway_verifier_role(
            ai_gateway_verifier_client_id,
            required=require_ai_gateway_verifier,
        ),
    )


def _run_transaction(
    sql_texts: tuple[str, ...],
    conn_kwargs: dict,
    *,
    app_role: str,
    ai_gateway_verifier_role: str | None = None,
    verify_outreach_integrity: bool = False,
    allow_absent_managed_event_triggers: bool = False,
    allow_absent_provider_schema: bool = False,
) -> None:
    """Run migration while preserving the integrity-probe monkeypatch seam."""

    _run_transaction_impl(
        sql_texts,
        conn_kwargs,
        app_role=app_role,
        ai_gateway_verifier_role=ai_gateway_verifier_role,
        verify_outreach_integrity=verify_outreach_integrity,
        allow_absent_managed_event_triggers=allow_absent_managed_event_triggers,
        allow_absent_provider_schema=allow_absent_provider_schema,
        _run_integrity_probe_fn=_run_outreach_integrity_probe,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply the governed Lakebase schema and seed")
    parser.add_argument(
        "--app-name",
        default=os.environ.get("MIP_APP_NAME", "mip-app"),
    )
    parser.add_argument(
        "--lakebase-instance",
        default=os.environ.get("LAKEBASE_INSTANCE_NAME", "mip-app-state"),
    )
    parser.add_argument(
        "--lakebase-database",
        default=os.environ.get("LAKEBASE_DATABASE", "mip_app_state"),
    )
    parser.add_argument(
        "--lender-name",
        default=os.environ.get("MIP_LENDER_NAME", "Summit Mortgage"),
    )
    parser.add_argument(
        "--lender-nmls-id",
        default=os.environ.get("MIP_LENDER_NMLS_ID", ""),
    )
    parser.add_argument(
        "--tenant-id",
        default=os.environ.get("MIP_TENANT_ID", ""),
    )
    parser.add_argument(
        "--ai-gateway-verifier-client-id",
        default=os.environ.get("MIP_AI_GATEWAY_VERIFIER_CLIENT_ID", ""),
        help="Dedicated verifier service-principal client ID granted proof-ledger writes",
    )
    parser.add_argument(
        "--require-ai-gateway-verifier",
        action="store_true",
        help="Fail before schema mutation unless an explicit real verifier client ID is bound",
    )
    return parser


def main(
    *,
    app_name: str | None = None,
    lakebase_instance: str | None = None,
    lakebase_database: str | None = None,
    lender_name: str = "Summit Mortgage",
    lender_nmls_id: str = "",
    tenant_id: str = "",
    ai_gateway_verifier_client_id: str | None = None,
    require_ai_gateway_verifier: bool = False,
) -> None:
    try:
        verifier_role = _resolve_ai_gateway_verifier_role(
            ai_gateway_verifier_client_id,
            required=require_ai_gateway_verifier,
        )
    except Exception:  # noqa: BLE001 -- stable deployment diagnostic; never reflect secrets
        print(
            "[lakebase-migrate] verifier identity preflight failed; verify the dedicated "
            "verifier client ID and deployment configuration before schema mutation.",
            file=sys.stderr,
        )
        sys.exit(2)

    if lakebase_instance is None and lakebase_database is None:
        conn_kwargs = _resolve_connection()
    else:
        conn_kwargs = _resolve_connection(
            instance_name=lakebase_instance,
            database_name=lakebase_database,
        )
    try:
        resolved_roles = _preflight_database_roles(
            conn_kwargs,
            app_name=app_name,
            ai_gateway_verifier_client_id=verifier_role,
            require_ai_gateway_verifier=require_ai_gateway_verifier,
        )
    except Exception:  # noqa: BLE001 -- stable deployment diagnostic; never reflect secrets
        print(
            "[lakebase-migrate] runtime-role preflight failed; verify App and verifier "
            "workspace identities and Lakebase role visibility before schema mutation.",
            file=sys.stderr,
        )
        sys.exit(2)

    app_role = resolved_roles[0]
    repo_root = _repo_root()
    schema_sql = (repo_root / "lakebase" / "schema.sql").read_text(encoding="utf-8")
    seed_sql = (repo_root / "lakebase" / "seed_campaigns.sql").read_text(encoding="utf-8")
    tenant_disclosure_seed_sql = _tenant_disclosure_seed_sql(
        lender_name=lender_name,
        lender_nmls_id=lender_nmls_id,
        tenant_id=tenant_id,
    )
    pre_seed_schema_sql, post_seed_schema_sql = _split_schema_sql(schema_sql)

    try:
        _run_transaction(
            (
                pre_seed_schema_sql,
                seed_sql,
                tenant_disclosure_seed_sql,
                post_seed_schema_sql,
            ),
            conn_kwargs,
            app_role=app_role,
            ai_gateway_verifier_role=resolved_roles[1],
            verify_outreach_integrity=True,
        )
        print(
            "[lakebase-migrate] schema + sample seed + configured tenant disclosures + "
            "integrity proof applied atomically"
        )
    except Exception:  # noqa: BLE001 -- stable deployment diagnostic; never reflect secrets
        print(
            "[lakebase-migrate] schema transaction failed; inspect sanitized Lakebase "
            "service diagnostics and retry only after the database is healthy.",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        _apply_app_role_grants(conn_kwargs, resolved_roles=resolved_roles)
    except Exception:  # noqa: BLE001 -- stable deployment diagnostic; never reflect secrets
        print(
            "[lakebase-migrate] app-role grants failed; refusing a false-green deploy. "
            "See docs/security/GRANTS.md §Lakebase.",
            file=sys.stderr,
        )
        sys.exit(2)


if __name__ == "__main__":
    _args = build_parser().parse_args()
    main(
        app_name=_args.app_name,
        lakebase_instance=_args.lakebase_instance,
        lakebase_database=_args.lakebase_database,
        lender_name=_args.lender_name,
        lender_nmls_id=_args.lender_nmls_id,
        tenant_id=_args.tenant_id,
        ai_gateway_verifier_client_id=_args.ai_gateway_verifier_client_id,
        require_ai_gateway_verifier=_args.require_ai_gateway_verifier,
    )
