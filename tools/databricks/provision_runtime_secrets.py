"""Provision Databricks Secret keys used by the deployed App runtime.

Presence only. A key that already exists in the scope is kept: its value is
never read, compared or rewritten. That covers ``cotality-id-mask-v1`` (it
keys every masked borrower id, ``B-...``), ``genie-action-current``,
``genie-action-previous``, and every ``salesforce-*`` key whose input is
unset. No stored value reaches the runner, so no runtime secret value is ever
copied into CI or compared there.

An absent key is written once (a first install, or a key an owner deleted):

* ``cotality-id-mask-v1`` and ``genie-action-current`` take their optional
  input (``MIP_COTALITY_ID_MASK_SECRET``, ``MIP_GENIE_ACTION_SECRET_CURRENT``,
  from the shell or ``.env.local``) or a value generated on the runner;
* ``genie-action-previous`` takes ``MIP_GENIE_ACTION_SECRET_PREVIOUS`` (with
  its ``_KID``) or a disabled sentinel;
* ``salesforce-*`` takes its input or a disabled sentinel.

The App's static resource declarations need a backing key even when nothing
is bound to it, hence the sentinels. A configured ``SALESFORCE_*`` input still
replaces its key: it is a third-party credential the operator supplies, and
the App payload binds the key only when that input is set
(tests/unit/test_salesforce_secret_binding_parity.py). An input set for any
other key that already exists is ignored, and the plan says so by name. Every
write is read back for presence (the key is listed), never for its value.

``--check`` builds the same plan read-only (no scope creation, no write), so
``scripts/deploy.sh`` can refuse before it stops the App. ``--require-existing``
refuses (exit 2) a plan that writes anything: an install that is already
provisioned plans zero writes.

``--rotate-genie-action`` is the only mode that reads a stored value. It is
human-run (it refuses under GitHub Actions): it moves ``genie-action-current``
to ``genie-action-previous`` and writes a newly generated current key, which
the App signs with from its next deployment. ``--retire-previous`` replaces
``genie-action-previous`` with a fresh disabled sentinel. No mode prints a
value.

Every CLI mode first proves the workspace it is about to call is the install's.
Run by hand, the SDK's default resolution falls back to the ``~/.databrickscfg``
DEFAULT profile, which can name another workspace; this tool never binds the
``.env.local`` host or token itself. The resolved host must equal every
configured install host (``DATABRICKS_HOST`` in the shell or ``.env.local``,
and ``MIP_DATABRICKS_WORKSPACE_HOST``, which deploy.sh exports once it has
bound the deployer), and at least one must be configured; otherwise the run
refuses with exit 2 before any Secrets API call (building the SDK client may
already fetch a token from the resolved host). Each summary line names it.
"""

from __future__ import annotations

import argparse
import base64
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
# Keys an absent install generates on the runner; every other absent key gets
# the disabled sentinel when its input is unset.
GENERATED_KEYS = frozenset({MASK_KEY, CURRENT_KEY})
DISABLED_PREVIOUS_PREFIX = "disabled."
EXIT_REFUSED = 2
HOST_ENV = "DATABRICKS_HOST"
DEPLOY_HOST_ENV = "MIP_DATABRICKS_WORKSPACE_HOST"
GITHUB_ACTIONS_ENV = "GITHUB_ACTIONS"


class RuntimeSecretWriteRefused(ValueError):
    """``--require-existing`` met a plan that writes, or a write did not land."""


class GenieActionRotationRefused(ValueError):
    """The rotation cannot run here, or has no stored key to rotate."""


class WorkspaceHostRefused(ValueError):
    """The SDK resolved a workspace that is not (provably) the install's."""


class EmptySecretValue(Exception):
    """The Secrets API answered without a value."""


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

    @property
    def writes_anything(self) -> bool:
        return bool(self.writes) or not self.scope_exists


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

    Runs before any Secrets API call. A secret listed or written in the wrong
    workspace leaves audit entries there and returns a verdict about the wrong
    install.
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
            f"{HOST_ENV} in .env.local (deploy.sh documents it as required) or in the shell. "
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


def _listed_keys(workspace: Any, *, scope: str) -> set[str]:
    return {_item_name(item, "key") for item in workspace.secrets.list_secrets(scope=scope)}


def _validated_inputs(values: dict[str, str], previous_kid: str, *, used: set[str]) -> None:
    """Validate the inputs this run writes; an input for an existing key is otherwise unused.

    The previous key's KID pairing is the exception: the App payload refuses a
    ``MIP_GENIE_ACTION_SECRET_PREVIOUS`` without its ``_KID`` whether or not it
    seeds a key, and the payload is built after the App stop, so this pre-stop
    plan refuses it first (tests/unit/test_previous_key_binding_parity.py).
    """

    if values[PREVIOUS_ENV] and not previous_kid:
        raise ValueError(
            f"{PREVIOUS_KID_ENV} is required when {PREVIOUS_ENV} is retained "
            "during a rotation grace period"
        )
    for env_name in sorted(used & STRONG_SECRET_ENVS):
        values[env_name] = require_strong_runtime_secret(
            values[env_name],
            name=env_name,
            extra_placeholders=PLACEHOLDERS,
        )
    if {CURRENT_ENV, PREVIOUS_ENV} <= used:
        require_distinct_rotation_secrets(
            values[CURRENT_ENV],
            values[PREVIOUS_ENV],
            current_name=CURRENT_ENV,
            previous_name=PREVIOUS_ENV,
        )


def plan_runtime_secrets(
    values: dict[str, str],
    *,
    scope: str,
    workspace: Any,
    previous_kid: str = "",
) -> RuntimeSecretPlan:
    """Read-only: decide every write before any write happens. Lists keys, reads no value."""

    scopes = {_item_name(item, "name") for item in workspace.secrets.list_scopes()}
    scope_exists = scope in scopes
    existing = _listed_keys(workspace, scope=scope) if scope_exists else set()
    values = dict(values)
    # (key, the input it takes, or "" for a value made here), in ENV_TO_KEY order.
    planned: list[tuple[str, str]] = []
    kept: list[str] = []
    notes: list[str] = []
    for env_name, key in ENV_TO_KEY.items():
        configured = values[env_name]
        if key in existing and not (env_name in OPTIONAL_RESOURCE_ENVS and configured):
            kept.append(key)
            if configured:
                notes.append(
                    f"{key}: kept unread; {env_name} is set but never replaces an existing key"
                )
            continue
        if configured:
            verb = "replaced" if key in existing else "absent, so written once"
            notes.append(f"{key}: {verb} from {env_name}")
            planned.append((key, env_name))
        elif key in GENERATED_KEYS:
            notes.append(f"{key}: absent, so written once from a value generated on this runner")
            planned.append((key, ""))
        else:
            # The App's resources are declared statically in the bundle, so the
            # backing key must exist even when nothing binds it. A random
            # disabled value verifies nothing and exposes no key.
            notes.append(f"{key}: absent, so written once as a disabled sentinel")
            planned.append((key, ""))
    _validated_inputs(values, previous_kid, used={env for _key, env in planned if env})
    writes = tuple((key, values[env] if env else _fresh_value(key)) for key, env in planned)
    return RuntimeSecretPlan(
        scope_exists=scope_exists, writes=writes, kept=tuple(kept), notes=tuple(notes)
    )


def _fresh_value(key: str) -> str:
    """A generated key for the mask and current key; a disabled sentinel for the rest."""

    if key in GENERATED_KEYS:
        return secrets.token_hex(32)
    return f"{DISABLED_PREVIOUS_PREFIX}{secrets.token_hex(32)}"


def _require_no_writes(plan: RuntimeSecretPlan, *, scope: str) -> None:
    if not plan.writes_anything:
        return
    planned = ", ".join(plan.write_keys) if plan.write_keys else "none"
    scope_text = "" if plan.scope_exists else f"; scope {scope} is absent"
    raise RuntimeSecretWriteRefused(
        f"--require-existing, but the plan writes {planned}{scope_text}. An install that "
        "is already provisioned plans zero writes: check the configured runtime secret "
        "scope before a first-install write mints new keys. Nothing was written."
    )


def _require_written_keys_listed(workspace: Any, *, scope: str, keys: tuple[str, ...]) -> None:
    """Read back for presence only: each written key is listed. No value is read."""

    if not keys:
        return
    listed = _listed_keys(workspace, scope=scope)
    missing = [key for key in keys if key not in listed]
    if missing:
        raise RuntimeSecretWriteRefused(
            f"wrote {', '.join(keys)} to {scope}, but the scope does not list "
            f"{', '.join(missing)}. Run again; a key that exists is kept."
        )


def check_runtime_secrets(
    *,
    scope: str = DEFAULT_SCOPE,
    client: WorkspaceClient | None = None,
    require_existing: bool = False,
) -> RuntimeSecretPlan:
    """Build the provisioning plan without creating the scope or writing a key."""

    values, previous_kid = _configured_values()
    workspace = client if client is not None else bound_workspace()[0]
    plan = plan_runtime_secrets(values, scope=scope, workspace=workspace, previous_kid=previous_kid)
    if require_existing:
        _require_no_writes(plan, scope=scope)
    return plan


def provision_runtime_secrets(
    *,
    scope: str = DEFAULT_SCOPE,
    client: WorkspaceClient | None = None,
    require_existing: bool = False,
) -> ProvisionedRuntimeSecrets:
    values, previous_kid = _configured_values()
    workspace = client if client is not None else bound_workspace()[0]
    plan = plan_runtime_secrets(values, scope=scope, workspace=workspace, previous_kid=previous_kid)
    if require_existing:
        _require_no_writes(plan, scope=scope)
    if not plan.scope_exists:
        workspace.secrets.create_scope(scope=scope)
    for key, value in plan.writes:
        workspace.secrets.put_secret(scope=scope, key=key, string_value=value)
    _require_written_keys_listed(workspace, scope=scope, keys=plan.write_keys)
    return ProvisionedRuntimeSecrets(written=plan.write_keys, kept=plan.kept, notes=plan.notes)


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
    existing = _listed_keys(workspace, scope=scope)
    workspace.secrets.put_secret(
        scope=scope,
        key=PREVIOUS_KEY,
        string_value=f"{DISABLED_PREVIOUS_PREFIX}{secrets.token_hex(32)}",
    )
    return PREVIOUS_KEY in existing


def _in_github_actions() -> bool:
    return os.environ.get(GITHUB_ACTIONS_ENV, "").strip().lower() not in ("", "false")


def _require_human_run() -> None:
    if _in_github_actions():
        raise GenieActionRotationRefused(
            f"--rotate-genie-action is human-run only, and {GITHUB_ACTIONS_ENV} is set. A "
            "governed deploy keeps every existing key. Nothing was read or written."
        )


def _stored_runtime_value(workspace: Any, *, scope: str, key: str) -> str:
    """The stored value as the runtime reads it (UTF-8, surrounding whitespace stripped)."""

    response = workspace.secrets.get_secret(scope=scope, key=key)
    encoded = _item_name(response, "value")
    if not encoded:
        raise EmptySecretValue(f"{scope}/{key}")
    return base64.b64decode(encoded, validate=True).decode("utf-8").strip()


def _unreadable_reason(exc: Exception) -> str:
    # The error code or class name only: an SDK message can echo request detail.
    return str(getattr(exc, "error_code", None) or type(exc).__name__)


def rotate_genie_action_secret(
    *,
    scope: str = DEFAULT_SCOPE,
    client: WorkspaceClient | None = None,
) -> None:
    """Move ``genie-action-current`` to ``genie-action-previous``; write a new current key.

    The one value read in this tool. Previous is written first, so the stored
    current key is never lost: if the second write fails, both keys hold the
    old value and a rerun completes the rotation.
    """

    _require_human_run()
    workspace = client if client is not None else bound_workspace()[0]
    scopes = {_item_name(item, "name") for item in workspace.secrets.list_scopes()}
    if scope not in scopes or CURRENT_KEY not in _listed_keys(workspace, scope=scope):
        raise GenieActionRotationRefused(
            f"{scope}/{CURRENT_KEY} does not exist, so there is nothing to rotate; a deploy "
            "writes the first key. Nothing was read or written."
        )
    try:
        current = _stored_runtime_value(workspace, scope=scope, key=CURRENT_KEY)
    except Exception as exc:  # any doubt refuses: never rotate a key that cannot be moved
        raise GenieActionRotationRefused(
            f"{scope}/{CURRENT_KEY} could not be read ({_unreadable_reason(exc)}). The Secrets "
            "API returns a value outside a notebook only to a workspace admin with READ on "
            f"secret scope {scope}. Nothing was written."
        ) from None
    if not current:
        raise GenieActionRotationRefused(
            f"{scope}/{CURRENT_KEY} is blank, so there is no key to keep for the grace "
            "period. Nothing was written."
        )
    replacement = secrets.token_hex(32)
    workspace.secrets.put_secret(scope=scope, key=PREVIOUS_KEY, string_value=current)
    workspace.secrets.put_secret(scope=scope, key=CURRENT_KEY, string_value=replacement)
    _require_written_keys_listed(workspace, scope=scope, keys=(PREVIOUS_KEY, CURRENT_KEY))


def _key_list(keys: tuple[str, ...]) -> str:
    return ", ".join(keys) if keys else "none"


def _print_notes(notes: tuple[str, ...]) -> None:
    for note in notes:
        print(f"runtime secret note: {note}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", default=DEFAULT_SCOPE)
    parser.add_argument(
        "--require-existing",
        action="store_true",
        help="refuse (exit 2) a plan that writes anything: a provisioned install plans zero writes",
    )
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
    mode.add_argument(
        "--rotate-genie-action",
        action="store_true",
        help="human-run: move the current Genie action key to previous and generate a new one",
    )
    args = parser.parse_args(argv)
    if args.require_existing and (args.retire_previous or args.rotate_genie_action):
        parser.error("--require-existing applies to --check and the provisioning run only")
    try:
        if args.rotate_genie_action:
            _require_human_run()
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
        if args.rotate_genie_action:
            rotate_genie_action_secret(scope=args.scope, client=workspace)
            print(
                f"Genie action keys in {args.scope} on {host}: rotated ({CURRENT_KEY} moved "
                f"to {PREVIOUS_KEY}; a new {CURRENT_KEY} was generated on this machine). "
                "The App signs with the new key from its next deployment."
            )
            return 0
        if args.check:
            plan = check_runtime_secrets(
                scope=args.scope, client=workspace, require_existing=args.require_existing
            )
            print(
                f"runtime secret plan for {args.scope} on {host}: "
                f"write {_key_list(plan.write_keys)}; "
                f"keep {_key_list(plan.kept)} (read-only check, nothing written)"
            )
            _print_notes(plan.notes)
            return 0
        result = provision_runtime_secrets(
            scope=args.scope, client=workspace, require_existing=args.require_existing
        )
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
