"""Provision Databricks Secret keys used by the deployed App runtime.

Secret values are read from the process environment or ``.env.local`` and
written directly through the Secrets API. They are never emitted in the
Databricks Apps deployment JSON or printed to stdout.

Two kinds of existing key are never silently replaced:

* ``cotality-id-mask-v1`` keys every masked borrower id (``B-...``). When the
  key exists, the configured value must equal the stored one as the runtime
  reads it. The two are compared as HMAC-SHA256 fingerprints under a per-run
  random salt; a difference, or a stored value that cannot be read, refuses
  the whole run before any write. No value or fingerprint is printed.
* ``salesforce-*`` keys are kept when their inputs are unset. The disabled
  sentinel is written only when the key is absent, because the App's static
  resource declaration needs a backing key.

``--check`` builds the same plan read-only (no scope creation, no write), so
``scripts/deploy.sh`` can refuse before it stops the App.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import os
import secrets
import sys
from dataclasses import dataclass, field
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
    "SALESFORCE_CLIENT_SECRET": "salesforce-client-secret",
    "SALESFORCE_PASSWORD": "salesforce-password",
    "SALESFORCE_SECURITY_TOKEN": "salesforce-security-token",
}
REQUIRED = frozenset({"MIP_COTALITY_ID_MASK_SECRET", "MIP_GENIE_ACTION_SECRET_CURRENT"})
STRONG_SECRET_ENVS = frozenset(
    {
        "MIP_COTALITY_ID_MASK_SECRET",
        "MIP_GENIE_ACTION_SECRET_CURRENT",
        "MIP_GENIE_ACTION_SECRET_PREVIOUS",
    }
)
OPTIONAL_RESOURCE_ENVS = frozenset(
    {"SALESFORCE_CLIENT_SECRET", "SALESFORCE_PASSWORD", "SALESFORCE_SECURITY_TOKEN"}
)
PREVIOUS_ENV = "MIP_GENIE_ACTION_SECRET_PREVIOUS"
PREVIOUS_KID_ENV = "MIP_GENIE_ACTION_SECRET_PREVIOUS_KID"
PREVIOUS_KEY = ENV_TO_KEY[PREVIOUS_ENV]
MASK_ENV = "MIP_COTALITY_ID_MASK_SECRET"
MASK_KEY = ENV_TO_KEY[MASK_ENV]
DISABLED_PREVIOUS_PREFIX = "disabled."
EXIT_REFUSED = 2


class RuntimeSecretChangeRefused(ValueError):
    """The run would replace a stored value the install depends on."""


@dataclass(frozen=True)
class RuntimeSecretPlan:
    """What a provisioning run writes and keeps. Values never appear in ``repr``."""

    scope_exists: bool
    writes: tuple[tuple[str, str], ...] = field(repr=False)
    kept: tuple[str, ...]

    @property
    def write_keys(self) -> tuple[str, ...]:
        return tuple(key for key, _value in self.writes)


@dataclass(frozen=True)
class ProvisionedRuntimeSecrets:
    written: tuple[str, ...]
    kept: tuple[str, ...]


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


def _item_name(item: Any, attribute: str) -> str:
    if isinstance(item, dict):
        return str(item.get(attribute) or "")
    return str(getattr(item, attribute, "") or "")


def _validated_values() -> dict[str, str]:
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
        if value and env_name in STRONG_SECRET_ENVS:
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
    return values


def _salted_fingerprint(value: bytes, salt: bytes) -> bytes:
    return hmac.new(salt, value, hashlib.sha256).digest()


def _stored_runtime_value(workspace: Any, *, scope: str, key: str) -> bytes:
    """The stored value as the runtime reads it (UTF-8, surrounding whitespace stripped)."""

    response = workspace.secrets.get_secret(scope=scope, key=key)
    raw = base64.b64decode(_item_name(response, "value"), validate=True)
    return raw.decode("utf-8").strip().encode("utf-8")


def _require_unchanged_mask(workspace: Any, *, scope: str, configured: str) -> None:
    salt = secrets.token_bytes(32)
    try:
        stored = _stored_runtime_value(workspace, scope=scope, key=MASK_KEY)
    except Exception as exc:  # any doubt refuses: never overwrite what cannot be compared
        reason = getattr(exc, "error_code", None) or type(exc).__name__
        raise RuntimeSecretChangeRefused(
            f"{scope}/{MASK_KEY} exists, but its value could not be read to prove it "
            f"unchanged ({reason}). The deploying identity needs READ on secret scope "
            f"{scope}. Nothing was written."
        ) from None
    if not hmac.compare_digest(
        _salted_fingerprint(stored, salt),
        _salted_fingerprint(configured.encode("utf-8"), salt),
    ):
        raise RuntimeSecretChangeRefused(
            f"{scope}/{MASK_KEY} holds a different value than the configured {MASK_ENV} "
            "(salted fingerprints compared; neither value is printed). Writing it would "
            "re-key every masked borrower id and orphan the B-... ids already stored in "
            "Lakebase campaigns, approvals, audit, watchlists and snoozes. Set "
            f"{MASK_ENV} (shell or .env.local) to the value this install already uses. "
            "A deliberate re-key is an owner-run data migration, never a deploy side "
            "effect. Nothing was written."
        )


def plan_runtime_secrets(
    values: dict[str, str],
    *,
    scope: str,
    workspace: Any,
) -> RuntimeSecretPlan:
    """Read-only: decide every write before any write happens."""

    scopes = {_item_name(item, "name") for item in workspace.secrets.list_scopes()}
    scope_exists = scope in scopes
    existing = (
        {_item_name(item, "key") for item in workspace.secrets.list_secrets(scope=scope)}
        if scope_exists
        else set()
    )
    if MASK_KEY in existing:
        _require_unchanged_mask(workspace, scope=scope, configured=values[MASK_ENV])

    writes: list[tuple[str, str]] = []
    kept: list[str] = []
    for env_name, key in ENV_TO_KEY.items():
        value = values[env_name]
        if key in existing and (
            key == MASK_KEY or (env_name in OPTIONAL_RESOURCE_ENVS and not value)
        ):
            # The mask was just proven equal; an unset optional input never
            # replaces a stored credential with a disabled sentinel.
            kept.append(key)
            continue
        if (key == PREVIOUS_KEY or env_name in OPTIONAL_RESOURCE_ENVS) and not value:
            # Databricks App resources are declared statically in the bundle,
            # so the backing key must exist even when no rotation grace key is
            # injected into the runtime. Replacing it with a random disabled
            # value invalidates old tokens without exposing a verifier key.
            value = f"{DISABLED_PREVIOUS_PREFIX}{secrets.token_hex(32)}"
        if not value:
            continue
        writes.append((key, value))
    return RuntimeSecretPlan(scope_exists=scope_exists, writes=tuple(writes), kept=tuple(kept))


def retire_previous_secret(
    *,
    scope: str = DEFAULT_SCOPE,
    client: WorkspaceClient | None = None,
) -> bool:
    """Invalidate the previous key while preserving the required App resource."""

    workspace = client or WorkspaceClient()
    scopes = {_item_name(item, "name") for item in workspace.secrets.list_scopes()}
    if scope not in scopes:
        workspace.secrets.create_scope(scope=scope)
    existing = {_item_name(item, "key") for item in workspace.secrets.list_secrets(scope=scope)}
    workspace.secrets.put_secret(
        scope=scope,
        key=PREVIOUS_KEY,
        string_value=f"{DISABLED_PREVIOUS_PREFIX}{secrets.token_hex(32)}",
    )
    return PREVIOUS_KEY in existing


def check_runtime_secrets(
    *,
    scope: str = DEFAULT_SCOPE,
    client: WorkspaceClient | None = None,
) -> RuntimeSecretPlan:
    """Build the provisioning plan without creating the scope or writing a key."""

    values = _validated_values()
    return plan_runtime_secrets(values, scope=scope, workspace=client or WorkspaceClient())


def provision_runtime_secrets(
    *,
    scope: str = DEFAULT_SCOPE,
    client: WorkspaceClient | None = None,
) -> ProvisionedRuntimeSecrets:
    values = _validated_values()
    workspace = client or WorkspaceClient()
    plan = plan_runtime_secrets(values, scope=scope, workspace=workspace)
    if not plan.scope_exists:
        workspace.secrets.create_scope(scope=scope)
    for key, value in plan.writes:
        workspace.secrets.put_secret(scope=scope, key=key, string_value=value)
    return ProvisionedRuntimeSecrets(written=plan.write_keys, kept=plan.kept)


def _key_list(keys: tuple[str, ...]) -> str:
    return ", ".join(keys) if keys else "none"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", default=DEFAULT_SCOPE)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--retire-previous",
        action="store_true",
        help="replace the previous Genie action HMAC key with a disabled sentinel",
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="read-only: build the provisioning plan and refuse exactly as a write run would",
    )
    args = parser.parse_args(argv)
    if args.retire_previous:
        existed = retire_previous_secret(scope=args.scope)
        state = "replaced" if existed else "disabled binding created"
        print(f"previous runtime verification key: {state}")
        return 0
    try:
        if args.check:
            plan = check_runtime_secrets(scope=args.scope)
            print(
                f"runtime secret plan for {args.scope}: write {_key_list(plan.write_keys)}; "
                f"keep {_key_list(plan.kept)} (read-only check, nothing written)"
            )
            return 0
        result = provision_runtime_secrets(scope=args.scope)
    except ValueError as exc:
        print(f"[runtime-secrets] refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    print(
        f"runtime secret bindings ready: {len(result.written) + len(result.kept)} keys "
        f"(written: {_key_list(result.written)}; kept: {_key_list(result.kept)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
