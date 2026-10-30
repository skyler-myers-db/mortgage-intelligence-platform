"""Refuse an enabled Growth Agent monitor scheduler until its scheduler role exists (M2-PR5).

``mip_growth_agent_monitor_scheduler`` calls the App's admin-gated
``/api/v1/growth-agent/monitors/run-due-all`` as its run_as, and today that is
the deployer: an enabled schedule would let a workspace-admin identity act in
the App on a timer. Plan sections 4.3 and 12 keep it PAUSED until a
non-approver scheduler role exists (deferred), so until
:data:`SCHEDULER_ROLE_EXISTS` is flipped by that reviewed change:

* **validator** (``bundle_refusals``): tools/databricks/bundle_env.py refuses
  every bundle call when databricks.yml (or a target override) declares the
  job enabled;
* **plan** (``planned_refusals``): the bundle plan's ``new_state`` for the job
  enables it;
* **live** (``live_refusals``): a workspace job is enabled whose name is the
  job key or ends in it (an install deployed under a ``[dev <user>] `` prefix),
  or whose id is the plan's bound id.

"Enabled" is the Jobs API's meaning: a ``schedule`` whose ``pause_status`` is
not ``PAUSED`` (an absent one is UNPAUSED), or a ``trigger`` or ``continuous``
block that is not PAUSED. scripts/deploy.sh runs ``check`` beside the bundle
plan gate at both of its positions; it only reads (jobs.list and jobs.get).
Printed lines carry the job key and the reason, never a job id or name.
Exit 0 passes, 2 refuses.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.databricks.bundle_plan_shape import (  # noqa: E402
    BundlePlan,
    PlanShapeError,
    parse_plan,
)

JOB_KEY = "mip_growth_agent_monitor_scheduler"
# The root configuration file names the Databricks CLI loads.
BUNDLE_CONFIG_NAMES = ("databricks.yml", "databricks.yaml")
# Flipped only by the reviewed change that adds the non-approver scheduler role.
SCHEDULER_ROLE_EXISTS = False
_PREFIX = "[mip-monitor-gate]"
_REMEDY = (
    "the monitor scheduler stays PAUSED until a non-approver scheduler role exists "
    "(plan sections 4.3 and 12): pause it, then re-run"
)


def _value(item: Any, name: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(name)
    value = getattr(item, name, None)
    return getattr(value, "value", value)


def enabled_reasons(settings: Any) -> list[str]:
    """Why ``settings`` (a dict or an SDK JobSettings) runs on its own; ``[]`` when it never does."""

    reasons = []
    for block in ("schedule", "trigger", "continuous"):
        body = _value(settings, block)
        if body is None:
            continue
        status = _value(body, "pause_status")
        if str(status or "UNPAUSED").rsplit(".", 1)[-1] != "PAUSED":
            reasons.append(f"its {block} is not PAUSED")
    return reasons


def bundle_refusals(bundle_text: str) -> list[str]:
    """The validator: databricks.yml, and every target override, keep the job paused."""

    import yaml

    bundle = yaml.safe_load(bundle_text) or {}
    declared = [((bundle.get("resources") or {}).get("jobs") or {}).get(JOB_KEY)]
    for target in (bundle.get("targets") or {}).values():
        declared.append((((target or {}).get("resources") or {}).get("jobs") or {}).get(JOB_KEY))
    reasons = [reason for job in declared if job for reason in enabled_reasons(job)]
    if declared[0] is None:
        reasons.append("databricks.yml no longer declares it, so nothing pins it PAUSED")
    return reasons


def validator_refusals(bundle_root: Path) -> list[str]:
    """``bundle_refusals`` of the bundle root's databricks.yml, unless the role exists."""

    if SCHEDULER_ROLE_EXISTS:
        return []
    configs = [bundle_root / name for name in BUNDLE_CONFIG_NAMES if (bundle_root / name).exists()]
    if not configs:
        # No bundle configuration, so nothing the CLI could deploy; it refuses itself.
        return []
    try:
        texts = [config.read_text(encoding="utf-8") for config in configs]
    except (OSError, UnicodeDecodeError) as exc:
        return [f"the bundle configuration is unreadable ({type(exc).__name__})"]
    return [reason for text in texts for reason in bundle_refusals(text)]


def planned_refusals(plan: BundlePlan) -> list[str]:
    entry = plan.entry(f"resources.jobs.{JOB_KEY}")
    if entry is None or entry.new_value is None:
        return []
    if entry.new_vars and any(path.split(".", 1)[0] in {"schedule", "trigger", "continuous"} for path in entry.new_vars):
        return ["the plan leaves its schedule unresolved"]
    return enabled_reasons(entry.new_value)


def _bound_id(plan_text: str) -> str:
    """The job id the deployer's state binds (the raw entry's ``id``), unless it plans a create."""

    entry = (json.loads(plan_text).get("plan") or {}).get(f"resources.jobs.{JOB_KEY}") or {}
    return "" if entry.get("action") == "create" else str(entry.get("id") or "")


def live_refusals(workspace: Any, *, bound_id: str = "") -> list[str]:
    """Every live job that is this one (by bound id or name) and is enabled."""

    candidates = {
        str(job.job_id)
        for job in workspace.jobs.list()
        if str(_value(_value(job, "settings"), "name") or "").endswith(JOB_KEY)
    }
    if bound_id:
        candidates.add(bound_id)
    reasons = []
    for job_id in sorted(candidates):
        settings = _value(workspace.jobs.get(int(job_id)), "settings")
        reasons += [f"a live copy: {reason}" for reason in enabled_reasons(settings)]
    return reasons


def check(plan_text: str, *, workspace_factory: Callable[[], Any]) -> list[str]:
    if SCHEDULER_ROLE_EXISTS:
        return []
    try:
        plan = parse_plan(plan_text)
    except PlanShapeError as exc:
        return [f"the bundle plan could not be read ({exc})"]
    reasons = [f"the plan enables it: {reason}" for reason in planned_refusals(plan)]
    try:
        reasons += live_refusals(workspace_factory(), bound_id=_bound_id(plan_text))
    except Exception as exc:  # noqa: BLE001 - an unproven live state refuses, never passes
        reasons.append(f"its live state could not be read ({type(exc).__name__})")
    return reasons


def _workspace() -> Any:
    from tools.databricks.bundle_plan_gate import bound_workspace_client

    return bound_workspace_client()


def main(argv: list[str] | None = None, *, workspace_factory: Callable[[], Any] = _workspace) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    gate = sub.add_parser("check", help="judge the bundle plan and the live job (read-only)")
    gate.add_argument("--plan", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        text = args.plan.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        reasons = [f"the bundle plan is unreadable ({type(exc).__name__})"]
    else:
        reasons = check(text, workspace_factory=workspace_factory)
    for reason in reasons:
        print(f"{_PREFIX} REFUSED jobs.{JOB_KEY}: {reason}; {_REMEDY}", file=sys.stderr)
    if reasons:
        return 2
    print(f"{_PREFIX} jobs.{JOB_KEY} stays PAUSED, planned and live (read-only check)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
