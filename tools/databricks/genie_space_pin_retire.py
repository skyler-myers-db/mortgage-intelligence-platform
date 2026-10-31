"""Retire the pin of a governed Genie space that no longer exists (disaster recovery, M1-PR3).

A pinned space that is gone refuses every deploy (tools/databricks/genie_space_pin.py):
it is never replaced by a title match, because whoever controls the space
controls the answers (attack G12). Recovery is this reviewed change:

1. ``retire_plan`` (read-only) proves the pinned space is gone, and prints the
   plan: the gone id, which signed source names it (the pin record or the
   App's signed rollback record), the deploying identity and the plan's
   sha256. A space that still exists is never unpinned: re-run the
   provisioner to repair a misconfigured one instead.
2. ``retire`` recomputes that plan immediately before it writes and refuses,
   with nothing changed, unless the ack equals its sha256 (the owner
   attestation is deferred, so an exact digest ack stands in for it). It then
   writes one signed tombstone pin record (``provenance: retired``) that pins
   nothing and adds the gone id to ``retired_space_ids``.

After the tombstone, the governed space resolves as on a first install (create
and pin, or an acked title match), a rollback record that still names the gone
id is superseded until the next green capture replaces it, and the gone id is
never pinned again. The only write is the tombstone's, through
``genie_space_pin.write_pin`` (the scope-ownership proof, signing, exact
readback).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from tools.clients.redact import emit_mask
from tools.databricks.genie_space_pin import (
    PLAN_VERSION,
    GeniePinRefusal,
    _pins,
    _space_exists,
    _text,
    plan_sha256,
    write_pin,
)


def retire_plan(
    workspace: Any, *, app_name: str, title: str, scope: str, lakebase_instance: str
) -> dict[str, Any]:
    """The read-only retire plan with its ``sha256``; refuses unless a pinned space is gone."""

    pinned, record, retired = _pins(workspace, app_name=app_name, scope=scope, lakebase_instance=lakebase_instance)
    target = pinned or record
    if not target:
        raise GeniePinRefusal("nothing pins a governed Genie space, so there is no pin to retire")
    if _space_exists(workspace, emit_mask(target)):
        raise GeniePinRefusal(
            "the pinned governed Genie space still exists, and a space that exists is never unpinned; "
            "re-run the provisioner to repair it instead"
        )
    deployer = emit_mask(_text(getattr(workspace.current_user.me(), "user_name", None)))
    plan = {
        "version": PLAN_VERSION,
        "action": "retire_pin",
        "app_name": app_name,
        "title": title,
        "space_id": target,
        "source": "pin" if pinned else "record",
        "deployer": deployer,
        "retired_space_ids": sorted({*retired, target}),
    }
    return {**plan, "sha256": plan_sha256(plan)}


def retire(
    workspace: Any,
    *,
    app_name: str,
    title: str,
    scope: str,
    lakebase_instance: str,
    ack_plan: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Write the signed tombstone, only when ``ack_plan`` names the plan recomputed now."""

    plan = retire_plan(
        workspace, app_name=app_name, title=title, scope=scope, lakebase_instance=lakebase_instance
    )
    if ack_plan.strip().lower() != plan["sha256"]:
        reason = "the ack names another plan" if ack_plan.strip() else "no ack names it"
        raise GeniePinRefusal(
            f"the Genie pin retire plan is not acked ({reason}); nothing was changed. Review the "
            "read-only plan; pass its sha256 as the ack only if this space is gone for good",
            plan=plan,
        )
    return write_pin(
        workspace,
        scope=scope,
        app_name=app_name,
        title=title,
        space_id="",
        provenance="retired",
        plan_digest=plan["sha256"],
        retired_space_ids=plan["retired_space_ids"],
        now=now,
    )
