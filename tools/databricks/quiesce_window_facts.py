"""Live, read-only facts for the quiesce window plan (M0-PR8).

``collect_facts`` reads the window journal, the App, the signed rollback
record and blue's deployment, and folds in the read-only probe results the
deploy runs with their own authorities: the bundle plan gate's report (WR4),
the binding payload diffed against the live App (WR1), the App, verifier and
jobs-reader role ``--check`` and the jobs reader's grants (WR2) and the
Lakebase migration plan (WR3). The two full-tree digests are downloaded only
when a reason exists. An open window's dead-man marker is read too (M0-PR9):
a restart it records lets the pre-lease plan through to the recovery under
the lease, which re-proves it (``repin --from-deadman``).

A v7 core record (M2-PR7b: an adopted blue, or a core release's own capture)
is read too, its env-name set from the record itself. Spike S7 recorded how a
start snapshots source (the CURRENT bytes of blue's folder, with the newest
deployment's env names), so a v7 core blue gets the window v5/v6 get: the
folder's full-tree digest now is checked against the record's own signed
``source_tree_sha256`` (blue's folder at adoption or capture) instead of a
download of a ``/src/`` payload, and the start is re-pinned onto the next v7
version (tools/databricks/core_blue_repin.py). A v7 agentic record is refused
as before (M4-PR1).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tools.databricks import quiesce_window_journal as journal
from tools.databricks.app_resource_bindings import binding_diff
from tools.databricks.app_rollback_record_contract import (
    V7_RECORD_VERSION,
    V7_REFUSAL,
    _load_record,
    _text,
)
from tools.databricks.app_start_deployment import (
    env_names,
    env_names_digest,
    last_deployment_is,
    tree_digest,
)
from tools.databricks.quiesce_window_deadman import deadman_status
from tools.databricks.quiesce_window_plan import WindowFacts

NOT_FOUND_RECORD = "no server-owned last-good App rollback contract exists"
MIGRATION_DECISIONS = frozenset({"noop", "apply", "refuse"})
ROLE_STATES = frozenset({"exact", "drift"})
# The core tier (MIP_AGENTIC_TIER=off, M2-PR3) deploys no verifier: its role is
# recorded absent, which no converge can rotate. The App and the jobs reader
# always exist.
VERIFIER_ROLE_STATES = ROLE_STATES | {"absent"}


def app_or_none(workspace: Any, *, app_name: str) -> object | None:
    matches = [
        app for app in workspace.apps.list() if _text(getattr(app, "name", None)) == app_name
    ]
    if len(matches) > 1:
        raise RuntimeError("more than one Databricks App has the governed name")
    return workspace.apps.get(app_name) if matches else None


def takes_unsigned_rebase(
    workspace: Any, *, app_name: str, scope: str, lakebase_instance: str, requested: bool
) -> bool:
    """Whether step 0a takes the unsigned rebase: requested, and no signed first-install journal.

    Step 0a and the pre-stop probe (``prestop_rollback_record``) apply the same
    rule: a signed journal is signed blue whatever ``MIP_REBASE_UNVERIFIED_APP``
    says, so the window governs it. The journal is read read-only, without the
    lease, as the probe reads it.
    """

    if not requested:
        return False
    from tools.databricks.app_first_install_journal import recovery_state

    state = recovery_state(
        workspace,
        app_name=app_name,
        rollback_scope=scope,
        expected_lakebase_instance=lakebase_instance,
    )
    return state != "signed"


def _migration(plan: dict[str, Any]) -> dict[str, Any]:
    decision = str(plan.get("decision") or "")
    if plan.get("mode") != "plan" or decision not in MIGRATION_DECISIONS:
        raise ValueError("the Lakebase migration report is not a plan")
    migrations = plan.get("migrations") or {}
    # The bundle's migration job always grants the jobs reader (M2-PR5), so a
    # plan that did not check its grants ("off" or missing) is incomplete. A
    # refusing plan is the exception: main_read_only's early refusal (stage
    # runtime_roles, migration_inputs, read_only_session or read_only_guard)
    # never reached the grants, and the window refuses it as
    # window_migration_refused whatever they are, so they are only recorded.
    jobs_reader_grants = plan.get("jobs_reader_grants")
    if decision == "refuse" and jobs_reader_grants in {None, "off"}:
        jobs_reader_grants = "unchecked"
    elif jobs_reader_grants not in ROLE_STATES:
        raise ValueError("the Lakebase migration plan did not check the jobs reader's grants")
    return {
        "jobs_reader_grants": jobs_reader_grants,
        "migration_decision": decision,
        "migration_blue_restorable": bool(plan.get("blue_restorable")),
        "migration_pending": tuple(str(item) for item in migrations.get("pending") or ()),
        "migration_pending_contract": tuple(
            str(item) for item in migrations.get("pending_contract") or ()
        ),
    }


def collect_facts(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
    lakebase_instance: str,
    gate_report: dict[str, Any],
    binding_payload: dict[str, Any] | None,
    role_state: str | None,
    migration_plan: dict[str, Any] | None,
    rebase_unverified_app: bool,
    verifier_role_state: str | None = None,
    jobs_reader_role_state: str | None = None,
    digest: Callable[[Any, str], str] = tree_digest,
) -> WindowFacts:
    """Read every fact the window plan needs; download trees only when a reason exists."""

    if gate_report.get("verdict") != "passed":
        raise ValueError("the bundle plan gate report did not pass")
    current = journal.read(workspace, app_name=app_name)
    journal_state = journal.state_of(current)
    # An open window whose dead-man restarted blue is the recovery's (M0-PR9).
    deadman_state = (
        deadman_status(workspace, app_name=app_name, current=current)[0]
        if journal_state in {"window_open", "window_starting"}
        else "absent"
    )
    if rebase_unverified_app:
        return WindowFacts(applicable=False, not_applicable_reason="rebase")
    app = app_or_none(workspace, app_name=app_name)
    if app is None:
        return WindowFacts(applicable=False, not_applicable_reason="no_app")
    try:
        record = _load_record(
            workspace,
            app_name=app_name,
            scope=scope,
            expected_lakebase_instance=lakebase_instance,
            accept_v7=True,
        )
    except RuntimeError as exc:
        if str(exc).startswith(NOT_FOUND_RECORD):
            return WindowFacts(applicable=False, not_applicable_reason="no_signed_record")
        raise
    v7 = record["version"] == V7_RECORD_VERSION
    if v7 and record["tier"] != "core":
        raise RuntimeError(f"the signed blue is a v7 agentic record; {V7_REFUSAL}")
    if (
        not {role_state, jobs_reader_role_state} <= ROLE_STATES
        or verifier_role_state not in VERIFIER_ROLE_STATES
        or migration_plan is None
    ):
        raise ValueError(
            "an installed signed blue needs the App, verifier and jobs-reader role checks "
            "and the migration plan"
        )
    if binding_payload is None:
        binding_state, lines = "unresolved", ()
    else:
        live = app.as_dict() if hasattr(app, "as_dict") else dict(app.__dict__)
        lines = tuple(binding_diff(expected=binding_payload, live=live))
        binding_state = "diff" if lines else "equal"
    blue_id = record["deployment_id"]
    blue = workspace.apps.get_deployment(app_name, blue_id)
    facts = WindowFacts(
        applicable=True,
        journal_state=journal_state,
        deadman_state=deadman_state,
        binding_state=binding_state,
        binding_lines=lines,
        role_state=role_state,
        verifier_role_state=verifier_role_state,
        jobs_reader_role_state=jobs_reader_role_state,
        **_migration(migration_plan),
        instance_update_sha256=str(gate_report.get("instance_update_sha256") or ""),
        blue_deployment_id=blue_id,
        blue_current=last_deployment_is(workspace, app_name=app_name, deployment_id=blue_id),
        restore_source=_text(getattr(blue, "source_code_path", None)),
        default_source_code_path=_text(getattr(app, "default_source_code_path", None)),
        env_names_sha256=(
            str(record["env_names_sha256"])
            if v7
            else env_names_digest(env_names(record["payload"]))
        ),
    )
    if not facts.reasons() or not facts.restore_source:
        return facts
    if v7:
        # The record's own digest of blue's folder; another folder is a drift.
        same = record["source_code_path"] == facts.restore_source
        artifact = str(record["source_tree_sha256"]) if same else ""
    else:
        artifact = digest(workspace, record["payload"]["source_code_path"])
    return WindowFacts(
        **{
            **facts.__dict__,
            "source_tree_sha256": digest(workspace, facts.restore_source),
            "artifact_tree_sha256": artifact,
        }
    )
