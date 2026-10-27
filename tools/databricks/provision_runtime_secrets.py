"""Provision Databricks Secret keys used by the deployed App runtime.

Secret values are read from the process environment or ``.env.local`` and
written directly through the Secrets API. They are never emitted in the
Databricks Apps deployment JSON or printed to stdout.

Two kinds of existing key are never silently replaced:

* ``cotality-id-mask-v1`` keys every masked borrower id (``B-...``). When the
  key exists, the configured value must equal the stored one as the runtime
  reads it. The two are compared as HMAC-SHA256 fingerprints under a per-run
  random salt; a difference, or a stored value that cannot be read, refuses
  the whole run before any write. No value or fingerprint is printed. The
  Secrets API returns a value outside a notebook only to a workspace admin
  with READ on the scope; deploy.sh already requires a workspace-admin
  deployer.
* ``salesforce-*`` keys are kept when their inputs are unset. The disabled
  sentinel is written only when the key is absent, because the App's static
  resource declaration needs a backing key.

An existing ``genie-action-current`` is compared the same way but never
refuses: the plan only notes whether the configured key is unchanged, a
rotation with the stored value kept as the grace key, a change with no grace
key (outstanding action and provenance tokens stop verifying), or not
comparable.

``--check`` builds the same plan read-only (no scope creation, no write), so
``scripts/deploy.sh`` can refuse before it stops the App.

Every CLI mode first proves the workspace it is about to call is the install's.
Run by hand, the SDK's default resolution falls back to the ``~/.databrickscfg``
DEFAULT profile, which can name another workspace; this tool never binds the
``.env.local`` host or token itself. The resolved host must equal every
configured install host (``DATABRICKS_HOST`` in the shell or ``.env.local``,
and ``MIP_DATABRICKS_WORKSPACE_HOST``, which deploy.sh exports once it has
bound the deployer), and at least one must be configured; otherwise the run
refuses with exit 2 before any workspace call. Each summary line names the
resolved host, which is not a secret.
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
from urllib.parse import urlsplit

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
CURRENT_ENV = "MIP_GENIE_ACTION_SECRET_CURRENT"
CURRENT_KEY = ENV_TO_KEY[CURRENT_ENV]
PREVIOUS_ENV = "MIP_GENIE_ACTION_SECRET_PREVIOUS"
PREVIOUS_KID_ENV = "MIP_GENIE_ACTION_SECRET_PREVIOUS_KID"
PREVIOUS_KEY = ENV_TO_KEY[PREVIOUS_ENV]
MASK_ENV = "MIP_COTALITY_ID_MASK_SECRET"
MASK_KEY = ENV_TO_KEY[MASK_ENV]
DISABLED_PREVIOUS_PREFIX = "disabled."
EXIT_REFUSED = 2
HOST_ENV = "DATABRICKS_HOST"
DEPLOY_HOST_ENV = "MIP_DATABRICKS_WORKSPACE_HOST"


class RuntimeSecretChangeRefused(ValueError):
    """The run would replace a stored value the install depends on."""


class WorkspaceHostRefused(ValueError):
    """The SDK resolved a workspace that is not (provably) the install's."""


class EmptySecretValue(Exception):
    """The Secrets API answered without a value, so nothing can be compared."""


@dataclass(frozen=True)
class RuntimeSecretPlan:
    """What a provisioning run writes and keeps. Values never appear in ``repr``."""

    scope_exists: bool
    writes: tuple[tuple[str, str], ...] = field(repr=False)
    kept: tuple[str, ...]
    notes: tuple[str, ...] = ()

    @property
    def write_keys(self) -> tuple[str, ...]:
        return tuple(key for key, _value in self.writes)


@dataclass(frozen=True)
class ProvisionedRuntimeSecrets:
    written: tuple[str, ...]
    kept: tuple[str, ...]
    notes: tuple[str, ...] = ()


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


def _normalized_host(value: str) -> str:
    """``scheme://host[:port]`` lowercased, as the SDK normalizes a host; "" if unusable.

    No path, query or user info survives, so the result is safe to print.
    """

    text = value.strip()
    if not text:
        return ""
    if "://" not in text:
        text = f"https://{text}"
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return ""
    if not parts.hostname:
        return ""
    netloc = parts.hostname if port in (None, 443) else f"{parts.hostname}:{port}"
    return f"{parts.scheme.lower()}://{netloc}"


def _install_hosts() -> tuple[tuple[str, str], ...]:
    """Each configured name for the install's workspace, as (source, raw value)."""

    dotenv = _dotenv_config()
    candidates = (
        (f"{HOST_ENV} (shell)", os.environ.get(HOST_ENV, "")),
        (f"{DEPLOY_HOST_ENV} (shell)", os.environ.get(DEPLOY_HOST_ENV, "")),
        (f"{HOST_ENV} (.env.local)", dotenv.get(HOST_ENV, "")),
    )
    return tuple((source, value) for source, value in candidates if value.strip())


def bound_workspace(client: Any | None = None) -> tuple[Any, str]:
    """The workspace client and its resolved host, proven to be the install's.

    Runs before any workspace call. A secret read in the wrong workspace leaves
    getSecret audit entries there and returns a verdict about the wrong install.
    """

    workspace = client if client is not None else WorkspaceClient()
    config_host = getattr(getattr(workspace, "config", None), "host", None)
    resolved = _normalized_host(str(config_host or ""))
    if not resolved:
        raise WorkspaceHostRefused(
            "the Databricks SDK resolved no workspace host. Nothing was read or written."
        )
    install = _install_hosts()
    if not install:
        raise WorkspaceHostRefused(
            f"no install workspace host is configured to check {resolved} against. Set "
            f"{HOST_ENV} in .env.local (deploy.sh requires it) or in the shell. "
            "Nothing was read or written."
        )
    mismatched = [
        f"{source} names {_normalized_host(value) or 'an unparsable host'}"
        for source, value in install
        if _normalized_host(value) != resolved
    ]
    if mismatched:
        raise WorkspaceHostRefused(
            f"the Databricks SDK resolved workspace {resolved}, but {'; '.join(mismatched)}. "
            "Pin the install's identity (for example DATABRICKS_CONFIG_PROFILE=<install "
            "profile>) and run again. Nothing was read or written."
        )
    return workspace, resolved


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
        values[CURRENT_ENV],
        values[PREVIOUS_ENV] or None,
        current_name="MIP_GENIE_ACTION_SECRET_CURRENT",
        previous_name="MIP_GENIE_ACTION_SECRET_PREVIOUS",
    )
    return values


def _salted_fingerprint(value: bytes, salt: bytes) -> bytes:
    return hmac.new(salt, value, hashlib.sha256).digest()


def _stored_runtime_value(workspace: Any, *, scope: str, key: str) -> bytes:
    """The stored value as the runtime reads it (UTF-8, surrounding whitespace stripped)."""

    response = workspace.secrets.get_secret(scope=scope, key=key)
    encoded = _item_name(response, "value")
    if not encoded:
        raise EmptySecretValue(f"{scope}/{key}")
    raw = base64.b64decode(encoded, validate=True)
    return raw.decode("utf-8").strip().encode("utf-8")


def _stored_equals(
    workspace: Any, *, scope: str, key: str, configured: tuple[str, ...]
) -> tuple[bool, ...]:
    """Whether the stored value equals each configured value, by salted fingerprint.

    A read failure propagates; the caller decides whether doubt refuses.
    """

    salt = secrets.token_bytes(32)
    stored = _salted_fingerprint(_stored_runtime_value(workspace, scope=scope, key=key), salt)
    return tuple(
        hmac.compare_digest(stored, _salted_fingerprint(value.encode("utf-8"), salt))
        for value in configured
    )


def _unreadable_reason(exc: Exception) -> str:
    # The error code or class name only: an SDK message can echo request detail.
    return str(getattr(exc, "error_code", None) or type(exc).__name__)


def _require_unchanged_mask(workspace: Any, *, scope: str, configured: str) -> None:
    try:
        (unchanged,) = _stored_equals(
            workspace, scope=scope, key=MASK_KEY, configured=(configured,)
        )
    except Exception as exc:  # any doubt refuses: never overwrite what cannot be compared
        raise RuntimeSecretChangeRefused(
            f"{scope}/{MASK_KEY} exists, but its value could not be read to prove it "
            f"unchanged ({_unreadable_reason(exc)}). The Secrets API returns a value "
            "outside a notebook only to a workspace admin with READ on secret scope "
            f"{scope}; deploy with such an identity. Nothing was written."
        ) from None
    if not unchanged:
        raise RuntimeSecretChangeRefused(
            f"{scope}/{MASK_KEY} holds a different value than the configured {MASK_ENV} "
            "(salted fingerprints compared; neither value is printed). Writing it would "
            "re-key every masked borrower id and orphan the B-... ids already stored in "
            "Lakebase campaigns, approvals, audit, watchlists and snoozes. Set "
            f"{MASK_ENV} (shell or .env.local) to the value this install already uses. "
            "A deliberate re-key is an owner-run data migration, never a deploy side "
            "effect. Nothing was written."
        )


def _current_key_note(workspace: Any, *, scope: str, current: str, previous: str) -> str:
    """Report-only. A rotation is an operator's call, so a change never refuses."""

    try:
        unchanged, kept_as_grace = _stored_equals(
            workspace, scope=scope, key=CURRENT_KEY, configured=(current, previous)
        )
    except Exception as exc:
        return f"{CURRENT_KEY}: not compared ({_unreadable_reason(exc)})"
    if unchanged:
        return f"{CURRENT_KEY}: unchanged"
    if previous and kept_as_grace:
        return (
            f"{CURRENT_KEY}: rotated; the stored value stays valid as {PREVIOUS_KEY} "
            "for the grace period"
        )
    return (
        f"{CURRENT_KEY}: changed with no grace key; outstanding action confirmations "
        "and provenance tokens stop verifying"
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
    notes: list[str] = []
    if CURRENT_KEY in existing:
        notes.append(
            _current_key_note(
                workspace,
                scope=scope,
                current=values[CURRENT_ENV],
                previous=values[PREVIOUS_ENV],
            )
        )

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
    return RuntimeSecretPlan(
        scope_exists=scope_exists, writes=tuple(writes), kept=tuple(kept), notes=tuple(notes)
    )


def retire_previous_secret(
    *,
    scope: str = DEFAULT_SCOPE,
    client: WorkspaceClient | None = None,
) -> bool:
    """Invalidate the previous key while preserving the required App resource."""

    workspace = client if client is not None else bound_workspace()[0]
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
    workspace = client if client is not None else bound_workspace()[0]
    return plan_runtime_secrets(values, scope=scope, workspace=workspace)


def provision_runtime_secrets(
    *,
    scope: str = DEFAULT_SCOPE,
    client: WorkspaceClient | None = None,
) -> ProvisionedRuntimeSecrets:
    values = _validated_values()
    workspace = client if client is not None else bound_workspace()[0]
    plan = plan_runtime_secrets(values, scope=scope, workspace=workspace)
    if not plan.scope_exists:
        workspace.secrets.create_scope(scope=scope)
    for key, value in plan.writes:
        workspace.secrets.put_secret(scope=scope, key=key, string_value=value)
    return ProvisionedRuntimeSecrets(written=plan.write_keys, kept=plan.kept, notes=plan.notes)


def _key_list(keys: tuple[str, ...]) -> str:
    return ", ".join(keys) if keys else "none"


def _print_notes(notes: tuple[str, ...]) -> None:
    for note in notes:
        print(f"runtime secret note: {note}")


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
    try:
        workspace, host = bound_workspace()
    except ValueError as exc:
        print(f"[runtime-secrets] refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    if args.retire_previous:
        existed = retire_previous_secret(scope=args.scope, client=workspace)
        state = "replaced" if existed else "disabled binding created"
        print(f"previous runtime verification key in {args.scope} on {host}: {state}")
        return 0
    try:
        if args.check:
            plan = check_runtime_secrets(scope=args.scope, client=workspace)
            print(
                f"runtime secret plan for {args.scope} on {host}: "
                f"write {_key_list(plan.write_keys)}; "
                f"keep {_key_list(plan.kept)} (read-only check, nothing written)"
            )
            _print_notes(plan.notes)
            return 0
        result = provision_runtime_secrets(scope=args.scope, client=workspace)
    except ValueError as exc:
        print(f"[runtime-secrets] refused on {host}: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    print(
        f"runtime secret bindings ready in {args.scope} on {host}: "
        f"{len(result.written) + len(result.kept)} keys "
        f"(written: {_key_list(result.written)}; kept: {_key_list(result.kept)})"
    )
    _print_notes(result.notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
