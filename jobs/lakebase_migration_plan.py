"""Read-only ``--mode plan`` and ``--mode verify`` for the Lakebase migration.

Both modes open one session whose transaction PostgreSQL itself enforces as
read-only, send every statement through a guard that admits only catalog and
ledger reads, and always roll back. They never mutate the database, so a
deploy can run them while the App keeps serving.

* ``verify`` re-runs apply's exact postflights against the live database.
* ``plan`` answers "would apply change anything, and could it succeed?":
  the executor's ownership, pending ledger versions and their compatibility
  class, the recorded schema-input digests, apply's read-only preflights,
  verify, and the catalog fingerprint apply recorded at its commit. Noop
  means no pending version, equal input digests, a passing verify and an
  equal catalog fingerprint; then the migration (and the App stop it needs)
  can be skipped.

The JSON report names checks, codes, versions and ``mip_app`` objects only.
It never carries a role name, host or exception text, so it is safe to print
in a public run log.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from jobs.lakebase_migration_executor_probe import probe_executor
from jobs.lakebase_migration_grants import (
    _await_authoritative_database_roles,
    _resolve_database_roles,
)
from jobs.lakebase_migration_inputs import (
    SchemaMigration,
    parse_schema_migrations,
    schema_input_components,
    schema_input_digest,
)
from jobs.lakebase_migration_ledger import (
    RecordedDigest,
    read_catalog_fingerprint,
    read_ledger,
)
from jobs.lakebase_migration_postflight import (
    _postflight_ai_gateway_verifier_grants,
    _postflight_app_role_grants,
)
from jobs.lakebase_migration_provider_plane import (
    _postflight_provider_schema_boundary,
    _postflight_public_schema_boundary,
)
from jobs.lakebase_migration_read_only import (
    ReadOnlyCursor,
    ReadOnlyStatementRefused,
    read_only_session,
)
from jobs.lakebase_migration_schema_hooks import (
    _postflight_event_trigger_inventory,
    _postflight_oauth_role_function_contract,
    _postflight_trigger_inventory,
    _preflight_executable_schema_hooks,
)

READ_ONLY_MODES = ("plan", "verify")

_SCHEMA_PRESENT_SQL = "SELECT to_regnamespace('mip_app') IS NOT NULL"


@dataclass(frozen=True)
class CheckContext:
    """Roles and vanilla-PostgreSQL test seams shared by every read-only check."""

    app_role: str
    verifier_role: str | None
    allow_absent_managed_event_triggers: bool = False
    allow_absent_provider_schema: bool = False

    @property
    def target_roles(self) -> tuple[str, ...]:
        return tuple(role for role in (self.app_role, self.verifier_role) if role is not None)


_Check = tuple[str, Callable[[ReadOnlyCursor, CheckContext], object]]


def _schema_present(cur: ReadOnlyCursor, _ctx: CheckContext) -> None:
    cur.execute(_SCHEMA_PRESENT_SQL)
    row = cur.fetchone()
    if row is None or not row[0]:
        raise RuntimeError("Lakebase schema mip_app is absent")


def _public_schema_boundary(cur: ReadOnlyCursor, ctx: CheckContext) -> None:
    _postflight_public_schema_boundary(
        cur,
        ctx.target_roles,
        principal_label="read-only check",
        allow_legacy_public_usage=False,
        allow_absent_provider_schema=ctx.allow_absent_provider_schema,
    )


def _provider_schema_boundary(cur: ReadOnlyCursor, ctx: CheckContext) -> None:
    _postflight_provider_schema_boundary(
        cur,
        ctx.target_roles,
        principal_label="read-only check",
        allow_absent_provider_schema=ctx.allow_absent_provider_schema,
    )


def _oauth_role_function_contract(cur: ReadOnlyCursor, ctx: CheckContext) -> None:
    _postflight_oauth_role_function_contract(
        cur,
        principal_label="read-only check",
        allow_absent_managed=ctx.allow_absent_managed_event_triggers,
    )


def _event_trigger_inventory(cur: ReadOnlyCursor, ctx: CheckContext) -> None:
    _postflight_event_trigger_inventory(
        cur,
        ctx.app_role,
        principal_label="read-only check",
        allow_absent_managed=ctx.allow_absent_managed_event_triggers,
    )


def _executable_schema_hooks(cur: ReadOnlyCursor, _ctx: CheckContext) -> None:
    _preflight_executable_schema_hooks(cur)


def _trigger_inventory_preflight(cur: ReadOnlyCursor, ctx: CheckContext) -> None:
    _postflight_trigger_inventory(
        cur,
        ctx.app_role,
        principal_label="read-only check",
        allow_missing_reviewed=True,
    )


def _trigger_contract(cur: ReadOnlyCursor, ctx: CheckContext) -> None:
    _postflight_trigger_inventory(cur, ctx.app_role, principal_label="read-only check")


def _app_role_grants(cur: ReadOnlyCursor, ctx: CheckContext) -> None:
    _postflight_app_role_grants(cur, ctx.app_role)


def _verifier_grants(cur: ReadOnlyCursor, ctx: CheckContext) -> None:
    if ctx.verifier_role is not None:
        _postflight_ai_gateway_verifier_grants(cur, ctx.verifier_role)


# The read-only gates apply's schema transaction runs before its first
# mutation, with the same arguments. A failure here is a refusal: apply would
# refuse too, after the App had already been stopped for it.
APPLY_PREFLIGHT_CHECKS: tuple[_Check, ...] = (
    ("public_schema_boundary", _public_schema_boundary),
    ("provider_schema_boundary", _provider_schema_boundary),
    ("oauth_role_function_contract", _oauth_role_function_contract),
    ("executable_schema_hooks", _executable_schema_hooks),
    ("event_trigger_inventory", _event_trigger_inventory),
    ("trigger_inventory", _trigger_inventory_preflight),
)
# Apply's exact postflights. A failure here means apply has work to do.
VERIFY_CHECKS: tuple[_Check, ...] = (
    ("schema_present", _schema_present),
    ("trigger_contract", _trigger_contract),
    ("event_trigger_inventory", _event_trigger_inventory),
    ("oauth_role_function_contract", _oauth_role_function_contract),
    ("provider_schema_boundary", _provider_schema_boundary),
    ("public_schema_boundary", _public_schema_boundary),
    ("app_role_grants", _app_role_grants),
    ("ai_gateway_verifier_grants", _verifier_grants),
)


def run_checks(
    cur: ReadOnlyCursor,
    ctx: CheckContext,
    checks: Sequence[_Check],
) -> tuple[list[str], str | None]:
    """Run checks in order; stop at the first failure and return its name."""

    passed: list[str] = []
    for name, check in checks:
        try:
            check(cur, ctx)
        except ReadOnlyStatementRefused:
            raise
        except Exception:  # noqa: BLE001 -- the check name is the public diagnostic
            return passed, name
        passed.append(name)
    return passed, None


# Input components a compatibility tag never reviews. A change to any of
# them makes the release contract, whatever its pending versions say.
_UNTAGGED_COMPONENTS = frozenset({"seed", "tenant_disclosure_seed", "grant_matrix"})


def plan_decision(
    *,
    refusals: Sequence[str],
    pending: Sequence[SchemaMigration],
    digest_state: str,
    changed_components: Sequence[str] | None,
    catalog_state: str,
    verify_passed: bool,
) -> tuple[str, bool]:
    """Return (decision, blue_restorable) for the facts a plan collected.

    Noop needs equal inputs AND an equal catalog: equal inputs alone miss
    drift (a rewritten function body, a dropped constraint or index, deleted
    seed rows) that only apply's replay of schema.sql repairs.

    An apply is blue-restorable only when its changes are pending versions
    that are all tagged expand, against a recorded digest whose seed, tenant
    seed and grant-matrix components all equal the release's. A tag reviews
    its own version's DDL; an in-place edit to existing DDL or a function
    body must ship as its own tagged version, since no tag can see it. An
    apply with nothing pending, or with no recorded digest to compare, counts
    as contract like a missing tag.
    """

    if refusals:
        return "refuse", False
    if not pending and digest_state == "equal" and catalog_state == "equal" and verify_passed:
        return "noop", True
    restorable = (
        bool(pending)
        and all(migration.compat == "expand" for migration in pending)
        and changed_components is not None
        and not _UNTAGGED_COMPONENTS.intersection(changed_components)
    )
    return "apply", restorable


def _catalog_state(cur: ReadOnlyCursor, recorded: RecordedDigest) -> str:
    try:
        live = read_catalog_fingerprint(cur)
    except ReadOnlyStatementRefused:
        raise
    except Exception:  # noqa: BLE001 -- e.g. a seeded table dropped; apply recreates it
        return "unreadable"
    return "equal" if live == recorded.catalog_fingerprint else "differs"


def _role_context(
    *,
    app_name: str | None,
    verifier_role: str | None,
    resolve_app_role_fn: Callable[..., str],
    allow_absent_managed_event_triggers: bool,
    allow_absent_provider_schema: bool,
) -> CheckContext:
    app_role, resolved_verifier = _resolve_database_roles(
        app_name=app_name,
        _resolve_app_role_fn=resolve_app_role_fn,
        _resolve_verifier_role_fn=lambda: verifier_role,
    )
    return CheckContext(
        app_role=app_role,
        verifier_role=resolved_verifier,
        allow_absent_managed_event_triggers=allow_absent_managed_event_triggers,
        allow_absent_provider_schema=allow_absent_provider_schema,
    )


def run_verify(conn_kwargs: dict, ctx: CheckContext) -> dict[str, Any]:
    """Run apply's postflights read-only and report the first failing check."""

    report: dict[str, Any] = {"mode": "verify", "refusals": [], "checks": [], "failed_check": None}
    with read_only_session(conn_kwargs) as cur:
        try:
            _await_authoritative_database_roles(
                cur, (ctx.app_role, ctx.verifier_role), timeout_s=0, interval_s=1
            )
        except ReadOnlyStatementRefused:
            raise
        except Exception:  # noqa: BLE001 -- never reflect role names
            report["refusals"].append("runtime_roles")
        else:
            report["checks"], report["failed_check"] = run_checks(cur, ctx, VERIFY_CHECKS)
    report["passed"] = not report["refusals"] and report["failed_check"] is None
    return report


def run_plan(
    conn_kwargs: dict,
    ctx: CheckContext,
    *,
    migrations: Sequence[SchemaMigration],
    repo_digest: str,
    repo_components: Mapping[str, str],
) -> dict[str, Any]:
    """Collect every plan fact in one read-only session and decide."""

    refusals: list[str] = []
    executor_missing: list[str] = []
    applied: set[str] | None = None
    recorded: RecordedDigest | None = None
    catalog_state = "absent"
    verify: dict[str, Any] = {"checks": [], "failed_check": None, "passed": False}
    with read_only_session(conn_kwargs) as cur:
        # A failed read aborts the transaction, so the first failing stage
        # ends the session's work and names itself as the refusal.
        stage = "runtime_roles"
        try:
            _await_authoritative_database_roles(
                cur, (ctx.app_role, ctx.verifier_role), timeout_s=0, interval_s=1
            )
            stage = "executor_probe"
            executor_missing = list(probe_executor(cur))
            stage = "ledger_unreadable"
            applied, recorded = read_ledger(cur)
        except ReadOnlyStatementRefused:
            raise
        except Exception:  # noqa: BLE001 -- stable codes only; never reflect secrets
            refusals.append(stage)
        if executor_missing:
            refusals.append("executor_ownership")
        if applied is not None:
            _passed, preflight_failure = run_checks(cur, ctx, APPLY_PREFLIGHT_CHECKS)
            if preflight_failure is not None:
                refusals.append(f"apply_preflight:{preflight_failure}")
            else:
                verify["checks"], verify["failed_check"] = run_checks(cur, ctx, VERIFY_CHECKS)
                verify["passed"] = verify["failed_check"] is None
            # Last: a failed read aborts the session, and a failing check
            # already decides apply, so there is nothing left to compare.
            if recorded is not None:
                catalog_state = _catalog_state(cur, recorded) if verify["passed"] else "skipped"
    declared = {migration.version for migration in migrations}
    pending = [m for m in migrations if applied is not None and m.version not in applied]
    unknown_applied = sorted((applied or set()) - declared)
    if unknown_applied:
        refusals.append("database_ahead_of_release")
    changed_components: list[str] | None = None
    if recorded is None:
        digest_state = "absent"
    else:
        digest_state = "equal" if recorded.input_digest == repo_digest else "differs"
        changed_components = [
            name
            for name, digest in repo_components.items()
            if recorded.components.get(name) != digest
        ]
    decision, blue_restorable = plan_decision(
        refusals=refusals,
        pending=pending,
        digest_state=digest_state,
        changed_components=changed_components,
        catalog_state=catalog_state,
        verify_passed=bool(verify["passed"]),
    )
    return {
        "mode": "plan",
        "decision": decision,
        "refusals": refusals,
        "blue_restorable": blue_restorable,
        "executor_missing": executor_missing,
        "migrations": {
            "declared": len(migrations),
            "applied": None if applied is None else len(applied),
            "pending": [m.version for m in pending],
            "pending_contract": [m.version for m in pending if m.compat == "contract"],
            "unknown_applied": unknown_applied,
        },
        "digest": digest_state,
        "digest_components_changed": changed_components,
        "catalog": catalog_state,
        "verify": verify,
    }


def main_read_only(
    mode: str,
    conn_kwargs: dict,
    *,
    app_name: str | None,
    verifier_role: str | None,
    resolve_app_role_fn: Callable[..., str],
    schema_inputs_fn: Callable[[], tuple[str, tuple[str, str, str, str]]],
    allow_absent_managed_event_triggers: bool = False,
    allow_absent_provider_schema: bool = False,
) -> int:
    """Print one public-safe JSON report and return the process exit code.

    plan exits 0 for ``noop`` and ``apply`` and 2 for ``refuse``; verify exits
    0 only when every postflight passes.
    """

    if mode not in READ_ONLY_MODES:
        raise ValueError(f"unknown read-only mode {mode!r}")
    report: dict[str, Any] = {"mode": mode}
    stage = "runtime_roles"
    try:
        ctx = _role_context(
            app_name=app_name,
            verifier_role=verifier_role,
            resolve_app_role_fn=resolve_app_role_fn,
            allow_absent_managed_event_triggers=allow_absent_managed_event_triggers,
            allow_absent_provider_schema=allow_absent_provider_schema,
        )
        if mode == "verify":
            stage = "read_only_session"
            report = run_verify(conn_kwargs, ctx)
        else:
            stage = "migration_inputs"
            schema_sql, sql_texts = schema_inputs_fn()
            migrations = parse_schema_migrations(schema_sql)
            repo_digest = schema_input_digest(sql_texts)
            repo_components = schema_input_components(sql_texts)
            stage = "read_only_session"
            report = run_plan(
                conn_kwargs,
                ctx,
                migrations=migrations,
                repo_digest=repo_digest,
                repo_components=repo_components,
            )
    except ReadOnlyStatementRefused:
        report["refusals"] = ["read_only_guard"]
    except Exception:  # noqa: BLE001 -- the stage code is the public diagnostic
        report["refusals"] = [stage]
    if mode == "plan":
        report.setdefault("decision", "refuse")
        ok = report["decision"] != "refuse"
    else:
        report.setdefault("passed", False)
        ok = bool(report["passed"])
    print(json.dumps(report, sort_keys=True))
    return 0 if ok else 2
