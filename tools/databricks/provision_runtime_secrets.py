"""Provision Databricks Secret keys used by the deployed App runtime.

Secret values are read from the process environment or ``.env.local`` and
written directly through the Secrets API. They are never emitted in the
Databricks Apps deployment JSON or printed to stdout.
"""

from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from databricks.sdk import WorkspaceClient

REPO = Path(__file__).resolve().parents[2]
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


def _configured_values() -> dict[str, str]:
    dotenv = {
        key: str(value) for key, value in dotenv_values(ENV_LOCAL).items() if value is not None
    }
    values: dict[str, str] = {}
    for env_name in ENV_TO_KEY:
        value = (os.environ.get(env_name) or dotenv.get(env_name) or "").strip()
        normalized = value.lower()
        if normalized in PLACEHOLDERS or (normalized.startswith("<") and normalized.endswith(">")):
            value = ""
        values[env_name] = value
    return values


def _item_name(item: Any, field: str) -> str:
    if isinstance(item, dict):
        return str(item.get(field) or "")
    return str(getattr(item, field, "") or "")


def provision_runtime_secrets(
    *,
    scope: str = DEFAULT_SCOPE,
    client: WorkspaceClient | None = None,
) -> tuple[str, ...]:
    values = _configured_values()
    missing = sorted(name for name in REQUIRED if not values[name])
    if missing:
        raise ValueError(f"{', '.join(missing)} required before provisioning runtime secrets")

    workspace = client or WorkspaceClient()
    scopes = {_item_name(item, "name") for item in workspace.secrets.list_scopes()}
    if scope not in scopes:
        workspace.secrets.create_scope(scope=scope)

    existing = {_item_name(item, "key") for item in workspace.secrets.list_secrets(scope=scope)}
    written: list[str] = []
    for env_name, key in ENV_TO_KEY.items():
        value = values[env_name]
        if not value and key == "genie-action-previous" and key not in existing:
            value = secrets.token_urlsafe(48)
        if not value:
            continue
        workspace.secrets.put_secret(scope=scope, key=key, string_value=value)
        written.append(key)
    return tuple(written)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", default=DEFAULT_SCOPE)
    args = parser.parse_args()
    written = provision_runtime_secrets(scope=args.scope)
    print(f"runtime secret bindings ready: {len(written)} keys")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
