"""Provision Databricks Secret keys used by the deployed App runtime.

Secret values are read from the process environment or ``.env.local`` and
written directly through the Secrets API. They are never emitted in the
Databricks Apps deployment JSON or printed to stdout.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backend.config.runtime_secret_policy import (  # noqa: E402
    require_distinct_rotation_secrets,
    require_strong_runtime_secret,
    runtime_secret_text,
)
from databricks.sdk import WorkspaceClient  # noqa: E402

ENV_LOCAL = REPO / ".env.local"
DEFAULT_SCOPE = "mip-runtime"
PLACEHOLDERS = frozenset(
    {
        "redacted",
        "changeme",
        "change-me",
        "change_me",
        "placeholder",
        "example",
        "your-secret",
        "your_secret",
        "mip-cotality-id-mask-v1",
    }
)
ENV_TO_KEY = {
    "MIP_COTALITY_ID_MASK_SECRET": "cotality-id-mask-v1",
    "MIP_GENIE_ACTION_SECRET_CURRENT": "genie-action-current",
    "MIP_GENIE_ACTION_SECRET_PREVIOUS": "genie-action-previous",
}
REQUIRED = frozenset({"MIP_COTALITY_ID_MASK_SECRET", "MIP_GENIE_ACTION_SECRET_CURRENT"})
PREVIOUS_ENV = "MIP_GENIE_ACTION_SECRET_PREVIOUS"
PREVIOUS_KID_ENV = "MIP_GENIE_ACTION_SECRET_PREVIOUS_KID"
PREVIOUS_KEY = ENV_TO_KEY[PREVIOUS_ENV]


def _dotenv_config() -> dict[str, str]:
    return {key: str(value) for key, value in dotenv_values(ENV_LOCAL).items() if value is not None}


def _configured_values() -> tuple[dict[str, str], str]:
    dotenv = _dotenv_config()
    values: dict[str, str] = {}
    for env_name in ENV_TO_KEY:
        raw = os.environ.get(env_name) or dotenv.get(env_name) or ""
        values[env_name] = (
            runtime_secret_text(
                raw,
                extra_placeholders=PLACEHOLDERS,
            )
            or ""
        )
    previous_kid = (os.environ.get(PREVIOUS_KID_ENV) or dotenv.get(PREVIOUS_KID_ENV) or "").strip()
    return values, previous_kid


def _item_name(item: Any, field: str) -> str:
    if isinstance(item, dict):
        return str(item.get(field) or "")
    return str(getattr(item, field, "") or "")


def retire_previous_secret(
    *,
    scope: str = DEFAULT_SCOPE,
    client: WorkspaceClient | None = None,
) -> bool:
    """Delete the previous verification key, returning whether it existed."""

    workspace = client or WorkspaceClient()
    scopes = {_item_name(item, "name") for item in workspace.secrets.list_scopes()}
    if scope not in scopes:
        return False
    existing = {_item_name(item, "key") for item in workspace.secrets.list_secrets(scope=scope)}
    if PREVIOUS_KEY not in existing:
        return False
    workspace.secrets.delete_secret(scope=scope, key=PREVIOUS_KEY)
    return True


def provision_runtime_secrets(
    *,
    scope: str = DEFAULT_SCOPE,
    client: WorkspaceClient | None = None,
) -> tuple[str, ...]:
    values, previous_kid = _configured_values()
    missing = sorted(name for name in REQUIRED if not values[name])
    if missing:
        raise ValueError(f"{', '.join(missing)} required before provisioning runtime secrets")

    if values[PREVIOUS_ENV] and not previous_kid:
        raise ValueError(
            f"{PREVIOUS_KID_ENV} is required when {PREVIOUS_ENV} is retained "
            "during a rotation grace period"
        )

    for env_name, value in values.items():
        if value:
            values[env_name] = require_strong_runtime_secret(
                value,
                name=env_name,
                extra_placeholders=PLACEHOLDERS,
            )
    require_distinct_rotation_secrets(
        values["MIP_GENIE_ACTION_SECRET_CURRENT"],
        values["MIP_GENIE_ACTION_SECRET_PREVIOUS"] or None,
        current_name="MIP_GENIE_ACTION_SECRET_CURRENT",
        previous_name="MIP_GENIE_ACTION_SECRET_PREVIOUS",
    )

    workspace = client or WorkspaceClient()
    scopes = {_item_name(item, "name") for item in workspace.secrets.list_scopes()}
    if scope not in scopes:
        workspace.secrets.create_scope(scope=scope)

    existing = {_item_name(item, "key") for item in workspace.secrets.list_secrets(scope=scope)}
    written: list[str] = []
    for env_name, key in ENV_TO_KEY.items():
        value = values[env_name]
        if key == PREVIOUS_KEY and not value:
            if key in existing:
                workspace.secrets.delete_secret(scope=scope, key=key)
            continue
        if not value:
            continue
        workspace.secrets.put_secret(scope=scope, key=key, string_value=value)
        written.append(key)
    return tuple(written)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", default=DEFAULT_SCOPE)
    parser.add_argument(
        "--retire-previous",
        action="store_true",
        help="delete the previous Genie action HMAC key without provisioning other keys",
    )
    args = parser.parse_args()
    if args.retire_previous:
        deleted = retire_previous_secret(scope=args.scope)
        state = "deleted" if deleted else "already absent"
        print(f"previous runtime verification key: {state}")
        return 0
    written = provision_runtime_secrets(scope=args.scope)
    print(f"runtime secret bindings ready: {len(written)} keys")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
