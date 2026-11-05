"""The core release's signed-blue commands (M2-PR7b).

  python -m tools.databricks.app_core_rollback ensure ...           # step 0a
  python -m tools.databricks.app_core_rollback capture ...          # signed core capture
  python -m tools.databricks.app_core_rollback restore ...          # tier b
  python -m tools.databricks.app_core_rollback restore-payload ...  # blue's, before activation

scripts/deploy.sh runs these in the core tier (``MIP_AGENTIC_TIER=off``)
where the agentic tier runs ``app_deployment_rollback``: ``ensure`` and
``restore`` from the EXIT-trap library only (they may deploy and start the
App: tools/databricks/stop_primitive_map.py), ``capture`` and
``restore-payload`` from the core activation step. The logic is
tools/databricks/core_blue.py, core_blue_capture.py and core_blue_restore.py.

``--agentic-tier`` is the profile's tier and is required: the tier gate
(tools/databricks/blue_tier_gate.py) judges blue against it, and capture
refuses any tier but core. ``--tier-transition-ack`` is the exact digest the
gate prints for a core blue under an agentic profile; no shell passes it yet.

Exit codes: 0 done; 2 refused (a reviewed code, nothing more changed by the
refused step; any treatment the command touched is left quiesced); 1 an
unexpected failure (its class only, since an SDK message can carry an id).
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.databricks.app_rollback_record_v7 import V7RecordError  # noqa: E402
from tools.databricks.blue_restore_proof import RestoreProofRefused  # noqa: E402
from tools.databricks.blue_tier_gate import BlueTierRefused  # noqa: E402
from tools.databricks.core_blue import CoreBlueRefused, binding_values  # noqa: E402

_PREFIX = "[mip-core-blue]"
MODES = ("ensure", "capture", "restore", "restore-payload")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("action", choices=MODES)
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--lakebase-instance", required=True)
    parser.add_argument("--agentic-tier", required=True, choices=("off", "on"))
    parser.add_argument("--tier-transition-ack", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--token-env", default="MIP_BEARER_TOKEN")
    parser.add_argument("--deployment-lease-id", default="")
    parser.add_argument("--deployment-source-git-sha", default="")
    parser.add_argument("--treatment-warehouse-id", default="")
    parser.add_argument("--treatment-catalog", default="mip")
    parser.add_argument("--out-env", type=Path)
    # ensure and restore (MIP_ACK_RESTORE_UNVERIFIED_FRONTEND, a one-run export)
    parser.add_argument("--expected-rollback-deployment-id", default="")
    parser.add_argument("--restore-unverified-frontend-ack", default="")
    # capture
    parser.add_argument("--payload", type=Path)
    parser.add_argument("--app-resource-payload", type=Path)
    parser.add_argument("--expected-git-sha", default="")
    parser.add_argument("--genie-space-id", default="")
    parser.add_argument("--release-id", default="")
    parser.add_argument("--stage-dir", type=Path)
    # restore-payload
    parser.add_argument("--out", type=Path)
    parser.add_argument("--install-class", default="")
    parser.add_argument("--app-env", default="")
    parser.add_argument("--catalog", default="mip")
    parser.add_argument("--schema", default="gold")
    return parser


def _write_env(path: Path, values: dict[str, str]) -> None:
    path.write_text("".join(f"{k}={shlex.quote(v)}\n" for k, v in values.items()), encoding="utf-8")
    path.chmod(0o600)


def _treatment(workspace: Any, args: argparse.Namespace, app_name: str) -> Callable[[str], None]:
    from tools.databricks.converge_campaign_treatment_access import (
        converge_campaign_treatment_access,
    )

    if not args.treatment_warehouse_id:
        raise CoreBlueRefused("usage", "--treatment-warehouse-id is required")

    def converge(mode: str) -> None:
        principal = str(getattr(workspace.apps.get(app_name), "service_principal_client_id", ""))
        if not converge_campaign_treatment_access(
            warehouse_id=args.treatment_warehouse_id,
            catalog=args.treatment_catalog,
            principal=principal.strip(),
            mode=mode,  # type: ignore[arg-type]
            workspace=workspace,
        ):
            raise CoreBlueRefused("treatment_absent", "the governed treatment table is absent")

    return converge


def _lease(workspace: Any, args: argparse.Namespace) -> Callable[[], None]:
    from tools.databricks.app_deployment_lease import held_assertion

    UUID(args.deployment_lease_id)
    return held_assertion(
        workspace,
        app_name=args.app_name,
        lease_id=args.deployment_lease_id,
        source_git_sha=args.deployment_source_git_sha or args.expected_git_sha,
    )


def _token(args: argparse.Namespace) -> str:
    token = os.environ.get(args.token_env, "").strip()
    if not token:
        raise CoreBlueRefused("usage", f"{args.token_env} is empty")
    return token


def _green_health(workspace: Any, args: argparse.Namespace) -> Callable[[], tuple[str, Any, str, str]]:
    from tools.databricks.app_deployment_health import health
    from tools.databricks.app_health_contract import active_app_deployment_pin

    token = _token(args)

    def check() -> tuple[str, Any, str, str]:
        pin = active_app_deployment_pin(
            workspace, app_name=args.app_name, expected_lease_id=args.deployment_lease_id
        )
        sha, binding, lease = health(
            workspace,
            app_name=args.app_name,
            base_url=args.base_url,
            bearer_token=token,
            expected_pin=pin,
        )
        return sha, binding, lease, pin.deployment_id

    return check


def run(args: argparse.Namespace, workspace: Any) -> str:
    """One mode; returns its PASS line or raises a reviewed refusal."""

    from tools.databricks import core_blue, core_blue_capture, core_blue_restore

    common = {
        "app_name": args.app_name,
        "scope": args.scope,
        "lakebase_instance": args.lakebase_instance,
        "agentic_tier": args.agentic_tier,
    }
    if args.action == "restore-payload":
        record = core_blue.load_core_blue(
            workspace, **common, tier_transition_ack=args.tier_transition_ack
        )
        payload, why = core_blue.blue_restore_payload(
            record,
            install_class=args.install_class,
            app_env=args.app_env,
            catalog=args.catalog,
            schema=args.schema,
            lakebase_instance=args.lakebase_instance,
        )
        if payload is not None and args.out is not None:
            args.out.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
            args.out.chmod(0o600)
        return f"restore-payload: {why}"
    lease = _lease(workspace, args)
    treatment = _treatment(workspace, args, args.app_name)
    if args.action == "capture":
        from tools.databricks.app_deployment_rollback_inputs import (
            payload_file,
            reviewed_resources_file,
        )

        if args.payload is None or args.app_resource_payload is None or args.stage_dir is None:
            raise CoreBlueRefused("usage", "capture needs --payload, --app-resource-payload, --stage-dir")
        record = core_blue_capture.capture_core_green(
            workspace,
            **common,
            payload=payload_file(str(args.payload)),
            release_id=args.release_id,
            staged=core_blue_capture.stage_files(args.stage_dir),
            expected_git_sha=args.expected_git_sha,
            lease_id=args.deployment_lease_id,
            genie_space_id=args.genie_space_id,
            reviewed_resources=reviewed_resources_file(str(args.app_resource_payload)),
            health=_green_health(workspace, args),
            lease=lease,
            treatment=treatment,
        )
    else:
        health = core_blue.core_health(
            workspace, app_name=args.app_name, base_url=args.base_url, bearer_token=_token(args)
        )
        if args.action == "ensure":
            record = core_blue_restore.ensure_core_blue(
                workspace,
                **common,
                health=health,
                lease=lease,
                treatment=treatment,
                tier_transition_ack=args.tier_transition_ack,
                unverified_frontend_ack=args.restore_unverified_frontend_ack,
            )
        else:
            record = core_blue_restore.restore_core_blue(
                workspace,
                **common,
                health=health,
                lease=lease,
                treatment=treatment,
                tier_transition_ack=args.tier_transition_ack,
                unverified_frontend_ack=args.restore_unverified_frontend_ack,
                expected_rollback_deployment_id=args.expected_rollback_deployment_id or None,
            )
    if args.out_env is not None:
        _write_env(args.out_env, binding_values(record))
    return f"{args.action}: a signed v7 core record ({record['provenance']}) is the App's last good"


def main(
    argv: list[str] | None = None, *, workspace_factory: Callable[[], Any] | None = None
) -> int:
    args = _parser().parse_args(argv)
    if workspace_factory is None:
        from tools.databricks.workspace_auth import deployment_workspace_client

        workspace_factory = deployment_workspace_client
    try:
        line = run(args, workspace_factory())
    except (CoreBlueRefused, RestoreProofRefused, BlueTierRefused) as exc:
        print(f"{_PREFIX} REFUSED {exc.code}: {exc}", file=sys.stderr)
        return 2
    except V7RecordError as exc:
        print(f"{_PREFIX} REFUSED record_invalid: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - the class only: an SDK message can carry an id
        print(f"{_PREFIX} FAILED: {args.action} could not complete ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(f"{_PREFIX} {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
