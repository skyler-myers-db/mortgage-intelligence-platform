"""WR5: the Lakebase ownership handover's one announced window (M2-PR8, cutover step 8).

When the lock-bounded Lakebase handover (tools/clients/lakebase_handover.py)
exhausts its budget against a long App transaction, the owner's first exit is
an announced window: stop the App, apply the journal's remainder, start it.
This module plans that window read-only and proves its start; it never stops
or starts anything. ``run_lakebase_handover_window`` in the window library
(scripts/lib/deploy_quiesce_window_lifecycle.sh) does both, through the
reviewed primitives (tools/databricks/stop_primitive_map.py), from the
owner-run entry ``scripts/lakebase_handover_window.sh``.

WR5 is for an install no signed blue governs yet: the handover runs before
adoption (cutover order, attack O8), and the deployment the start creates is
what adoption then signs. A signed install refuses
(``window_wr5_signed_install``): a start would leave its signed record behind,
and the re-pin needs the deployment lease and the proof-signing key, which an
owner-run window does not hold. The release's own window (WR1-WR4) is
unchanged and never plans WR5.

**Exact digest ack.** As for WR1-WR4, the owner hardware-key attestation is
deferred: the window opens only when ``MIP_ACK_QUIESCE_WINDOW`` equals the
sha256 of this plan's canonical body, recomputed immediately before the stop.
The body pins the App's identity, the deployment it serves and the folder a
start would snapshot, and the handover journal's remainder digest, so any of
them changing in between refuses.

**Budget.** The in-window apply is bounded at ``WR5_APPLY_BUDGET_S`` (300 s,
lakebase_handover.WINDOW_BUDGET); the stop and start take #307's conservative
per-step budgets, which are not shrunk: spike S7 measured a trivial App's
compute start at 96-108 s and its stop at about 40-45 s, and the real App's
start deployment installs its whole tree on top.

**The start (spike S7).** The CLI ``apps start`` returns once the compute is
ACTIVE; the deployment that start created shows only from about then, for
about 1 s as ``pending_deployment`` and then as ``active_deployment``. A
stopped App's GET has no active deployment, so the only id that can show is
the new one. ``prove_start`` polls, at most every 5 s for
``START_ID_WAIT_S``, for an id that is not the planned active deployment.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tools.databricks import quiesce_window_journal as journal
from tools.databricks.app_deployment_state import deployment_state
from tools.databricks.app_rollback_record_contract import _load_record, _text
from tools.databricks.app_start_deployment import (
    START_ID_POLL_INTERVAL_S,
    StartProofError,
    wait_for_compute_active,
    wait_for_terminal,
)
from tools.databricks.quiesce_window_facts import NOT_FOUND_RECORD, app_or_none
from tools.databricks.quiesce_window_plan import (
    ACK_ENV,
    HANDOVER_REASON,
    STEP_BUDGET_S,
    body_sha256,
)

CANONICAL_SCHEMA = "mip.quiesce-window-plan.wr5.canonical.v1"
# The CLI start returns at compute ACTIVE; the new id shows within about 1 s of it (spike S7).
START_ID_WAIT_S = 120.0
PUBLIC_SCHEMA = "mip.quiesce-window-plan.wr5.public.v1"
WR5_APPLY_BUDGET_S = 300
REFUSAL_CODES = frozenset(
    {
        "window_unacked",
        "window_wr5_signed_install",
        "window_journal_open",
        "window_facts_incomplete",
        "window_start_source_drift",
        "window_pending_deployment",
        "window_app_not_active",
        "window_handover_not_exhausted",
    }
)
REMEDIES = {
    "window_unacked": f"review the window in the private plan, then re-run with {ACK_ENV} set to its sha256",
    "window_wr5_signed_install": "a signed install takes no owner-run window: revert the handover instead",
    "window_journal_open": "an earlier quiesce window's journal is still open; close it first",
    "window_facts_incomplete": "the App's identity or deployments could not be read; see the private plan",
    "window_start_source_drift": "a start would snapshot another folder than the App serves",
    "window_pending_deployment": "the App has a deployment in progress; wait for it to settle",
    "window_app_not_active": "the App is not serving; a window would not stop a serving App",
    "window_handover_not_exhausted": "the handover journal is not an exhausted apply",
}


@dataclass(frozen=True)
class HandoverWindowFacts:
    app_present: bool
    app_id: str = ""
    app_client_id: str = ""
    app_scim_id: str = ""
    compute_state: str = ""
    active_deployment_id: str = ""
    active_source: str = ""
    default_source: str = ""
    pending_deployment_id: str = ""
    signed_record: bool = False
    quiesce_journal_state: str = "absent"
    handover_status: str = ""
    handover_sha256: str = ""
    remainder: int = 0


def collect_facts(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
    lakebase_instance: str,
    handover_status: str,
    handover_sha256: str,
    remainder: int,
) -> HandoverWindowFacts:
    """Read-only: the App, its deployments, the signed record's presence, the window journal."""

    app = app_or_none(workspace, app_name=app_name)
    if app is None:
        return HandoverWindowFacts(app_present=False, handover_status=handover_status)
    try:
        _load_record(workspace, app_name=app_name, scope=scope, expected_lakebase_instance=lakebase_instance)
        signed = True
    except RuntimeError as exc:
        if not str(exc).startswith(NOT_FOUND_RECORD):
            raise
        signed = False
    active = getattr(app, "active_deployment", None)
    return HandoverWindowFacts(
        app_present=True,
        app_id=_text(getattr(app, "id", None)),
        app_client_id=_text(getattr(app, "service_principal_client_id", None)),
        app_scim_id=_text(getattr(app, "service_principal_id", None)),
        compute_state=_text(getattr(getattr(app, "compute_status", None), "state", None)).split(".")[-1].upper(),
        active_deployment_id=_text(getattr(active, "deployment_id", None)),
        active_source=_text(getattr(active, "source_code_path", None)),
        default_source=_text(getattr(app, "default_source_code_path", None)),
        pending_deployment_id=_text(getattr(getattr(app, "pending_deployment", None), "deployment_id", None)),
        signed_record=signed,
        quiesce_journal_state=journal.state_of(journal.read(workspace, app_name=app_name)),
        handover_status=handover_status,
        handover_sha256=handover_sha256,
        remainder=remainder,
    )


def canonical_body(facts: HandoverWindowFacts) -> dict[str, Any]:
    return {
        "schema": CANONICAL_SCHEMA,
        "reasons": [HANDOVER_REASON],
        "app_id": facts.app_id,
        "app_client_id": facts.app_client_id,
        "app_scim_id": facts.app_scim_id,
        "active_deployment_id": facts.active_deployment_id,
        "start_source": facts.default_source,
        "handover_sha256": facts.handover_sha256,
        "remainder": facts.remainder,
    }


def predicted_seconds() -> int:
    return STEP_BUDGET_S["stop"] + WR5_APPLY_BUDGET_S + STEP_BUDGET_S["start"]


def window_plan(facts: HandoverWindowFacts, *, acknowledgement: str) -> dict[str, Any]:
    """The WR5 plan: its digest, prediction and refusals; a refusal means the App keeps serving."""

    body = canonical_body(facts)
    digest = body_sha256(body)
    refusals: list[str] = []
    if facts.handover_status != "exhausted" or not facts.handover_sha256 or facts.remainder < 1:
        refusals.append("window_handover_not_exhausted")
    if facts.signed_record:
        refusals.append("window_wr5_signed_install")
    if facts.quiesce_journal_state not in {"absent", "closed"}:
        refusals.append("window_journal_open")
    if not (facts.app_present and facts.app_id and facts.app_client_id and facts.app_scim_id and facts.active_deployment_id):
        refusals.append("window_facts_incomplete")
    if facts.pending_deployment_id:
        refusals.append("window_pending_deployment")
    if facts.app_present and facts.compute_state != "ACTIVE":
        refusals.append("window_app_not_active")
    if not facts.default_source or facts.default_source != facts.active_source:
        refusals.append("window_start_source_drift")
    ack = "matched" if acknowledgement == digest else "absent" if not acknowledgement else "mismatch"
    if ack != "matched":
        refusals.append("window_unacked")
    refusals = list(dict.fromkeys(refusals))
    assert set(refusals) <= REFUSAL_CODES
    public = {
        "schema": PUBLIC_SCHEMA,
        "code": "refused" if refusals else "window",
        "reasons": [HANDOVER_REASON],
        "predicted_seconds": predicted_seconds(),
        "window_sha256": digest,
        "ack": ack,
        "refusals": refusals,
    }
    return {"public": public, "canonical": body, "facts": asdict(facts), "remedies": {code: REMEDIES[code] for code in refusals}}


def _start_id(
    workspace: Any,
    *,
    app_name: str,
    planned: str,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> str:
    """The id the start created: pending, else active, never the planned one, polled briefly."""

    deadline = clock() + START_ID_WAIT_S
    while True:
        app = workspace.apps.get(app_name)
        pending = _text(getattr(getattr(app, "pending_deployment", None), "deployment_id", None))
        created = pending or _text(getattr(getattr(app, "active_deployment", None), "deployment_id", None))
        if created and created != planned:
            return created
        if clock() >= deadline:
            raise StartProofError("start_unreadable", "the start created no new deployment")
        sleep(START_ID_POLL_INTERVAL_S)


def prove_start(
    workspace: Any,
    *,
    app_name: str,
    plan: dict[str, Any],
    wait: Any = wait_for_terminal,
    wait_active: Any = wait_for_compute_active,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """Read-only: the start created a new deployment that SUCCEEDED from the planned folder."""

    canonical = plan["canonical"]
    if plan["public"]["code"] != "window" or body_sha256(canonical) != plan["public"]["window_sha256"]:
        raise StartProofError("start_record_changed", "the WR5 plan is not a passed, intact plan")
    created = _start_id(
        workspace, app_name=app_name, planned=canonical["active_deployment_id"], sleep=sleep, clock=clock
    )
    deployment = wait(workspace, app_name=app_name, deployment_id=created)
    if deployment_state(deployment) != "SUCCEEDED":
        raise StartProofError("start_not_succeeded", "the start deployment did not succeed")
    if _text(getattr(deployment, "source_code_path", None)) != canonical["start_source"]:
        raise StartProofError("start_source_path", "the start deployed another folder than planned")
    wait_active(workspace, app_name=app_name)
    return created


def env_lines_for(plan: dict[str, Any]) -> dict[str, str]:
    """What the window library needs from a passed plan: the digest and the App's identity pins."""

    canonical = plan["canonical"]
    return {
        "MIP_WR5_WINDOW_SHA256": plan["public"]["window_sha256"],
        "MIP_WR5_HANDOVER_SHA256": canonical["handover_sha256"],
        "MIP_WR5_APP_ID": canonical["app_id"],
        "MIP_WR5_APP_CLIENT_ID": canonical["app_client_id"],
        "MIP_WR5_APP_SCIM_ID": canonical["app_scim_id"],
    }


def load_plan(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("public", {}).get("schema") != PUBLIC_SCHEMA:
        raise ValueError("not a WR5 window plan")
    return value


_PREFIX = "[mip-quiesce-window]"


def run_mode(
    args: argparse.Namespace,
    workspace: Any,
    *,
    write_private: Callable[[Path, dict[str, Any]], None],
    env_lines: Callable[[dict[str, str]], str],
) -> int:
    """``plan-wr5`` (exit 2 when refused) and ``await-wr5``; both read-only."""

    if args.command == "await-wr5":
        created = prove_start(workspace, app_name=args.app_name, plan=load_plan(args.plan))
        values = {"MIP_WR5_START_DEPLOYMENT_ID": created}
        print(f"{_PREFIX} await-wr5: the start deployment succeeded from the planned folder")
    else:
        from tools.clients import handover_journal as journals
        from tools.clients.lakebase_handover import KIND
        from tools.clients.lakebase_handover_window import remainder, remainder_sha256

        handover = journals.load(args.lakebase_handover_journal, kind=KIND)
        facts = collect_facts(
            workspace,
            app_name=args.app_name,
            scope=args.scope,
            lakebase_instance=args.lakebase_instance,
            handover_status=handover.status,
            handover_sha256=remainder_sha256(handover),
            remainder=len(remainder(handover)),
        )
        plan = window_plan(facts, acknowledgement=os.environ.get(ACK_ENV, "").strip())
        write_private(args.out, plan)
        public = plan["public"]
        print(
            f"{_PREFIX} WR5: {public['code']} (predicted {public['predicted_seconds']} s, "
            f"ack {public['ack']}); window sha256 {public['window_sha256']}: acknowledge with {ACK_ENV}"
        )
        for code in public["refusals"]:
            print(f"{_PREFIX} REFUSED {code}: {REMEDIES[code]}", file=sys.stderr)
        if public["refusals"]:
            print(f"{_PREFIX} nothing was changed; the App keeps serving", file=sys.stderr)
            return 2
        values = env_lines_for(plan)
    if args.out_env is not None:
        args.out_env.write_text(env_lines(values), encoding="utf-8")
        args.out_env.chmod(0o600)
    return 0


__all__ = [
    "PUBLIC_SCHEMA",
    "REFUSAL_CODES",
    "WR5_APPLY_BUDGET_S",
    "HandoverWindowFacts",
    "collect_facts",
    "env_lines_for",
    "load_plan",
    "predicted_seconds",
    "prove_start",
    "run_mode",
    "window_plan",
]
