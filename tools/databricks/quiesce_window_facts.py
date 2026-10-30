"""Live, read-only facts for the quiesce window plan (M0-PR8).

``collect_facts`` reads the window journal, the App, the signed rollback
record and blue's deployment, and folds in the read-only probe results the
deploy runs with their own authorities: the bundle plan gate's report (WR4),
the binding payload diffed against the live App (WR1), the App role
``--check`` (WR2) and the Lakebase migration plan (WR3). The two full-tree
digests are downloaded only when a reason exists.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tools.databricks import quiesce_window_journal as journal
from tools.databricks.app_resource_bindings import binding_diff
from tools.databricks.app_rollback_record_contract import _load_record, _text
from tools.databricks.app_start_deployment import (
    env_names,
    env_names_digest,
    last_deployment_is,
    tree_digest,
)
from tools.databricks.quiesce_window_plan import WindowFacts

NOT_FOUND_RECORD = "no server-owned last-good App rollback contract exists"
MIGRATION_DECISIONS = frozenset({"noop", "apply", "refuse"})


def app_or_none(workspace: Any, *, app_name: str) -> object | None:
    matches = [
        app for app in workspace.apps.list() if _text(getattr(app, "name", None)) == app_name
    ]
    if len(matches) > 1:
        raise RuntimeError("more than one Databricks App has the governed name")
    return workspace.apps.get(app_name) if matches else None


def _migration(plan: dict[str, Any]) -> dict[str, Any]:
    decision = str(plan.get("decision") or "")
    if plan.get("mode") != "plan" or decision not in MIGRATION_DECISIONS:
        raise ValueError("the Lakebase migration report is not a plan")
    migrations = plan.get("migrations") or {}
    return {
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
    digest: Callable[[Any, str], str] = tree_digest,
) -> WindowFacts:
    """Read every fact the window plan needs; download trees only when a reason exists."""

    if gate_report.get("verdict") != "passed":
        raise ValueError("the bundle plan gate report did not pass")
    journal_state = journal.state_of(journal.read(workspace, app_name=app_name))
    if rebase_unverified_app:
        return WindowFacts(applicable=False, not_applicable_reason="rebase")
    app = app_or_none(workspace, app_name=app_name)
    if app is None:
        return WindowFacts(applicable=False, not_applicable_reason="no_app")
    try:
        record = _load_record(
            workspace, app_name=app_name, scope=scope, expected_lakebase_instance=lakebase_instance
        )
    except RuntimeError as exc:
        if str(exc).startswith(NOT_FOUND_RECORD):
            return WindowFacts(applicable=False, not_applicable_reason="no_signed_record")
        raise
    if role_state not in {"exact", "drift"} or migration_plan is None:
        raise ValueError("an installed signed blue needs the App role check and the migration plan")
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
        binding_state=binding_state,
        binding_lines=lines,
        role_state=role_state,
        **_migration(migration_plan),
        instance_update_sha256=str(gate_report.get("instance_update_sha256") or ""),
        blue_deployment_id=blue_id,
        blue_current=last_deployment_is(workspace, app_name=app_name, deployment_id=blue_id),
        restore_source=_text(getattr(blue, "source_code_path", None)),
        default_source_code_path=_text(getattr(app, "default_source_code_path", None)),
        env_names_sha256=env_names_digest(env_names(record["payload"])),
    )
    if not facts.reasons() or not facts.restore_source:
        return facts
    return WindowFacts(
        **{
            **facts.__dict__,
            "source_tree_sha256": digest(workspace, facts.restore_source),
            "artifact_tree_sha256": digest(workspace, record["payload"]["source_code_path"]),
        }
    )
