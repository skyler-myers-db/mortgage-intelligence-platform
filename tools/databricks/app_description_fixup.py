"""Apply the bundle's App description online: a create-update masked to ``description`` (spike S2).

The release's binding payload (``tools/databricks/app_resource_bindings.py``)
carries the bundle's App description. Spikes S2 and S2b measured, on a
throwaway App (databricks-sdk pinned in ``uv.lock``):

* ``apps update`` (PATCH) clears a description it does not carry, so a
  resource restore that sent only ``{name, resources}`` emptied it;
* ``apps create-update`` with ``update_mask="description"`` changes only the
  description and ``update_time``: no restart, no new deployment, the compute
  and the App's Postgres sessions unchanged, and the ACL of the App's
  Lakebase role unchanged. It is a long-running operation, a waiter polled to
  SUCCEEDED.

So a description-only difference opens no quiesce window (WR1 in
``tools/databricks/quiesce_window_plan.py``): the release applies it here,
online, at its binding step, while blue serves. It never sends resources, so
it never re-grants CREATE ON DATABASE to the App's role.

The tool reads the App and proves its immutable identity first. It does
nothing when the description already equals the payload's (both empty
included), refuses to clear a live one (a payload with no description while
the App has one), and otherwise applies it and proves
afterwards that the identity, the resource bindings, the active and pending
deployments and the compute state are unchanged and the description is the
payload's. Exit 0 when applied or already equal, 2 on a refusal. It prints
names and states only, never a value.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

DESCRIPTION_UPDATE_MASK = "description"
UPDATE_TIMEOUT = timedelta(minutes=10)
_PREFIX = "[mip-app-description]"


class DescriptionFixupRefused(RuntimeError):
    """The online description fix-up refused; nothing was changed unless it says so."""


def _plain(app: object) -> dict[str, Any]:
    if hasattr(app, "as_dict"):
        value = app.as_dict()
    elif isinstance(app, dict):
        value = app
    else:
        raise DescriptionFixupRefused("the App read is not an object")
    return dict(value) if isinstance(value, dict) else {}


def _deployment_id(app: dict[str, Any], field: str) -> str:
    deployment = app.get(field)
    return str((deployment or {}).get("deployment_id") or "").strip() if isinstance(deployment, dict) else ""


def _bindings(app: dict[str, Any]) -> dict[str, Any]:
    items = app.get("resources") or []
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise DescriptionFixupRefused("the App's resource bindings are not a list of objects")
    return {str(item.get("name") or ""): item for item in items}


def _untouched(app: dict[str, Any]) -> tuple[object, ...]:
    """What a description-only create-update must leave exactly as it was (S2, S2b)."""

    compute = (app.get("compute_status") or {}).get("state") if isinstance(app.get("compute_status"), dict) else None
    return (
        _bindings(app),
        _deployment_id(app, "active_deployment"),
        _deployment_id(app, "pending_deployment"),
        str(compute or ""),
    )


def _assert_identity(app: dict[str, Any], *, app_name: str, identity: tuple[str, str, str]) -> None:
    observed = (
        str(app.get("id") or "").strip(),
        str(app.get("service_principal_client_id") or "").strip(),
        str(app.get("service_principal_id") or "").strip(),
    )
    if str(app.get("name") or "") != app_name or observed != identity or not all(identity):
        raise DescriptionFixupRefused("the App's immutable identity is not the expected one")


def apply_bundle_description(
    workspace: Any,
    *,
    app_name: str,
    description: str,
    identity: tuple[str, str, str],
) -> str:
    """``equal`` when nothing was needed, ``applied`` after the proven online fix-up."""

    before = _plain(workspace.apps.get(app_name))
    _assert_identity(before, app_name=app_name, identity=identity)
    if str(before.get("description") or "") == description:
        return "equal"
    if not description.strip():
        # The shell runs this at every no-WR1 binding step: an install whose
        # bundle declares no description is "equal" while the App has none too.
        raise DescriptionFixupRefused("the bundle declares no App description; a live one is never cleared")
    from databricks.sdk.service.apps import App

    workspace.apps.create_update(
        app_name=app_name,
        update_mask=DESCRIPTION_UPDATE_MASK,
        app=App(name=app_name, description=description),
    ).result(timeout=UPDATE_TIMEOUT)
    after = _plain(workspace.apps.get(app_name))
    _assert_identity(after, app_name=app_name, identity=identity)
    if _untouched(after) != _untouched(before):
        raise DescriptionFixupRefused(
            "the description update changed the bindings, a deployment or the compute"
        )
    if str(after.get("description") or "") != description:
        raise DescriptionFixupRefused("the App description is not the bundle's after the update")
    return "applied"


def _payload_description(path: Path, *, app_name: str) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DescriptionFixupRefused("the binding payload is unreadable") from exc
    if not isinstance(payload, dict) or str(payload.get("name") or "") != app_name:
        raise DescriptionFixupRefused("the binding payload is not this App's")
    return str(payload.get("description") or "")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--expected-app-id", required=True)
    parser.add_argument("--expected-client-id", required=True)
    parser.add_argument("--expected-scim-id", required=True)
    args = parser.parse_args(argv)
    try:
        description = _payload_description(args.payload, app_name=args.app_name)
        from databricks.sdk import WorkspaceClient

        outcome = apply_bundle_description(
            WorkspaceClient(),
            app_name=args.app_name,
            description=description,
            identity=(args.expected_app_id, args.expected_client_id, args.expected_scim_id),
        )
    except DescriptionFixupRefused as exc:
        print(f"{_PREFIX} refused: {exc}", file=sys.stderr)
        return 2
    if outcome == "equal":
        print(f"{_PREFIX} the App description already equals the bundle's")
    else:
        print(
            f"{_PREFIX} applied the bundle's App description online (a create-update masked to "
            "description: no restart, no deployment, no role change)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
