"""CLI and heartbeat process for the signed App deployment lease."""

from __future__ import annotations

import argparse
import shlex
import signal
import sys
import time
from pathlib import Path
from typing import Any


def parent_is_expected(parent_pid: int) -> bool:
    """Return whether the heartbeat remains a child of the original deployer."""

    return __import__("os").getppid() == parent_pid


def held_assertion(
    lease: Any,
    workspace: Any,
    *,
    app_name: str,
    lease_id: str,
    source_git_sha: str,
) -> Any:
    app_name = app_name.strip()
    lease_id = lease_id.strip()
    source_git_sha = lease._source_sha(source_git_sha)
    lease._path(app_name)
    if not lease_id:
        raise ValueError("App deployment lease ID is required")

    def check() -> None:
        lease.assert_held(
            workspace,
            app_name=app_name,
            lease_id=lease_id,
            source_git_sha=source_git_sha,
        )

    return check


def heartbeat(
    lease: Any,
    workspace: Any,
    *,
    app_name: str,
    lease_id: str,
    source_git_sha: str,
    parent_pid: int,
) -> None:
    while lease._parent_is_expected(parent_pid):
        time.sleep(lease.HEARTBEAT_INTERVAL_SECONDS)
        if not lease._parent_is_expected(parent_pid):
            return
        try:
            lease.renew(
                workspace,
                app_name=app_name,
                lease_id=lease_id,
                source_git_sha=source_git_sha,
            )
        except Exception as exc:
            print(
                f"[mip-deployment-lease] heartbeat failed: {type(exc).__name__}",
                file=sys.stderr,
            )
            if lease._parent_is_expected(parent_pid):
                lease.os.kill(parent_pid, signal.SIGTERM)
            raise


def main(lease: Any, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=lease.__doc__)
    parser.add_argument(
        "action", choices=("recovery-root", "acquire", "heartbeat", "release")
    )
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--source-git-sha")
    parser.add_argument("--writer-application-id")
    parser.add_argument("--expired-recovery-lease-id")
    parser.add_argument("--lease-id")
    parser.add_argument("--out-env", type=Path)
    parser.add_argument("--parent-pid", type=int)
    args = parser.parse_args(argv)
    workspace = lease.WorkspaceClient()
    if args.action == "recovery-root":
        if args.out_env is None:
            parser.error("recovery-root requires --out-env")
        recovery, candidates = lease.lease_support.recovery_context(
            lease, workspace, app_name=args.app_name
        )
        record = lease._download(workspace, app_name=args.app_name)
        recovery_lease_id = str(record.get("lease_id") or "") if recovery and record else ""
        args.out_env.write_text(
            f"MIP_APP_DEPLOYMENT_RECOVERY_ROOT={shlex.quote(recovery)}\n"
            "MIP_APP_DEPLOYMENT_RECOVERY_LEASE_ID="
            f"{shlex.quote(recovery_lease_id)}\n"
            "MIP_APP_DEPLOYMENT_RECOVERY_CANDIDATES="
            f"{shlex.quote(','.join(candidates))}\n",
            encoding="utf-8",
        )
    elif args.action == "acquire":
        if not args.source_git_sha or not args.writer_application_id or args.out_env is None:
            parser.error(
                "acquire requires --source-git-sha, --writer-application-id, and --out-env"
            )
        lease_id = lease.acquire(
            workspace,
            app_name=args.app_name,
            source_git_sha=args.source_git_sha,
            writer_application_id=args.writer_application_id,
            expired_recovery_lease_id=args.expired_recovery_lease_id,
        )
        try:
            args.out_env.write_text(
                f"MIP_APP_DEPLOYMENT_LEASE_ID={shlex.quote(lease_id)}\n",
                encoding="utf-8",
            )
        except Exception:
            try:
                lease.release(workspace, app_name=args.app_name, lease_id=lease_id)
                released = lease._download(workspace, app_name=args.app_name)
                if (
                    released is None
                    or released.get("state") != "released"
                    or released.get("lease_id") != lease_id
                ):
                    raise RuntimeError("handoff lease release did not persist exactly")
            except Exception as compensation_error:
                raise RuntimeError(
                    "App deployment lease environment handoff failed and signed compensation "
                    "did not complete"
                ) from compensation_error
            raise
    elif args.action == "heartbeat":
        if not args.lease_id or not args.source_git_sha or not args.parent_pid:
            parser.error("heartbeat requires --lease-id, --source-git-sha, and --parent-pid")
        lease._heartbeat(
            workspace,
            app_name=args.app_name,
            lease_id=args.lease_id,
            source_git_sha=args.source_git_sha,
            parent_pid=args.parent_pid,
        )
    else:
        if not args.lease_id:
            parser.error("release requires --lease-id")
        lease.release(workspace, app_name=args.app_name, lease_id=args.lease_id)
    return 0
