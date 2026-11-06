"""Where a plan's PlanReport goes: the public summary to GitHub, the full report to the workspace (M1-PR7).

``--phase plan`` in deploy-dev.yml runs the pre-lease bundle plan gate, which
writes two files: ``PlanReport.public_summary()`` and ``PlanReport.full()``
(tools/databricks/deploy_plan_report.py). The repository and its run logs are
public, so:

* ``summary`` appends the public summary, and nothing else of the report, to
  the job's step summary (``$GITHUB_STEP_SUMMARY``). It refuses a document
  that is not exactly the public schema's keys.
* ``publish`` writes the full report, which names resolved objects and
  principals, create-only to the deployer's own bundle root in the client
  workspace: ``<root>/plan-reports/<run id>-<attempt>.json``, beside
  ``app-src/`` (tools/databricks/app_release_contract.bundle_root). It is the
  plan phase's one workspace write, under the deployer's own identity. No
  GitHub artifact ever carries either file.

Exit 0, or 2 on a refusal (named, never a value).
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from tools.databricks.app_release_contract import api_path, bundle_root
from tools.databricks.deploy_plan_report import PUBLIC_SCHEMA

PUBLIC_KEYS = frozenset(
    {
        "schema",
        "position",
        "verdict",
        "plan_sha256",
        "plan_version",
        "cli_version",
        "counts",
        "app",
        "instance_update_sha256",
        "refusals",
        "deferred",
        "app_acl",
        "execute_gap",
        "adopt",
    }
)
FOLDER = "plan-reports"
_RUN = re.compile(r"[0-9]{1,20}")


class Refused(Exception):
    """A value-free refusal."""


def public_markdown(summary: Mapping[str, Any]) -> str:
    """The step-summary block of one public summary; refuses any other document."""

    if summary.get("schema") != PUBLIC_SCHEMA or set(summary) != PUBLIC_KEYS:
        raise Refused("the plan report summary is not exactly PlanReport.public_summary()")
    counts = summary["counts"]
    codes = sorted({str(refusal["code"]) for refusal in summary["refusals"]})
    return (
        f"### Bundle plan gate ({summary['position']}): {summary['verdict']}\n\n"
        f"- plan sha256 `{summary['plan_sha256'] or '(unread)'}`, "
        + ", ".join(f"{counts[action]} {action}" for action in counts)
        + "\n"
        + f"- refusal codes: {', '.join(codes) or 'none'}\n"
        + "- the full report (resolved names and principals) is in the deployer's plan-reports folder only\n\n"
        + "```json\n"
        + json.dumps(summary, indent=2, sort_keys=True)
        + "\n```\n"
    )


def report_path(*, principal: str, client: str, run_id: str, attempt: str) -> str:
    if _RUN.fullmatch(run_id) is None or _RUN.fullmatch(attempt) is None:
        raise Refused("--run-id and --run-attempt are the run's decimal id and attempt")
    root = bundle_root(principal=principal.strip().lower(), target="client", root_leaf=client)
    return f"{root}/{FOLDER}/{run_id}-{attempt}.json"


def publish(workspace: Any, *, full: Mapping[str, Any], path: str) -> None:
    """Create ``path`` (never overwrite it) and read it back."""

    from databricks.sdk.service.workspace import ImportFormat

    payload = (json.dumps(full, indent=2, sort_keys=True) + "\n").encode("utf-8")
    workspace.workspace.mkdirs(api_path(path.rsplit("/", 1)[0]))
    workspace.workspace.upload(api_path(path), io.BytesIO(payload), format=ImportFormat.AUTO, overwrite=False)
    if workspace.workspace.download(api_path(path)).read() != payload:
        raise Refused("the plan report read back differently than it was written")


def _client() -> Any:
    from tools.databricks.workspace_auth import deployment_workspace_client

    return deployment_workspace_client()


def main(argv: Sequence[str] | None = None, *, workspace_factory: Callable[[], Any] = _client) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.databricks.plan_report_publish", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    summary = sub.add_parser("summary")
    summary.add_argument("--public", required=True, type=Path)
    summary.add_argument("--out", required=True, type=Path)
    full = sub.add_parser("publish")
    full.add_argument("--full", required=True, type=Path)
    for name in ("--principal", "--client", "--run-id", "--run-attempt"):
        full.add_argument(name, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "summary":
            block = public_markdown(json.loads(args.public.read_text(encoding="utf-8")))
            with args.out.open("a", encoding="utf-8") as handle:
                handle.write(block + "\n")
            print("[plan-report] wrote the public summary to the step summary")
            return 0
        path = report_path(principal=args.principal, client=args.client, run_id=args.run_id, attempt=args.run_attempt)
        publish(workspace_factory(), full=json.loads(args.full.read_text(encoding="utf-8")), path=path)
        print(f"[plan-report] wrote the full report to the deployer's {FOLDER} folder as {args.run_id}-{args.run_attempt}.json")
        return 0
    except Refused as refusal:
        print(f"[plan-report] refused: {refusal}", file=sys.stderr)
    except (OSError, ValueError) as exc:
        print(f"[plan-report] refused: the report could not be read ({type(exc).__name__})", file=sys.stderr)
    except Exception as exc:  # the class only: an SDK message can carry a path
        print(f"[plan-report] refused: the workspace write failed ({type(exc).__name__})", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
