"""Keep the PII salt write-once: create it only on an authoritative absence.

The silver SQL (``sql/transformations/silver_property_*.sql``) and the
Lakeflow feature pipeline hash PII with ``secret('mip', 'pii-salt-v1')``.
Every masked join key in an install derives from that one value, so it is
written once and never replaced: a second write changes every hash the next
refresh produces and silently breaks joins against earlier gold snapshots.

The Secrets API has no create-only put, so the only safe write is one that
follows an inventory the tool can trust:

* ``ensure`` lists the scopes and, when the scope exists, its keys. A list
  error of any kind (permission, network, a scope that vanished between the
  two calls) or a malformed inventory refuses with exit 2 before any create
  or put; an error is never read as "absent". The scope is created only when
  the scope listing lacks it, and is then listed again. A fresh random value
  is put only when a listing of the scope lacks the key, and the key must be
  listed afterwards. An existing key is never touched.
* ``check`` runs the same inventory read-only, so ``scripts/deploy.sh`` can
  refuse before the signed lease and the App stop.

``--require-existing 1`` turns an absent scope or key into a refusal (exit 2)
in both modes, for an install whose masked keys already exist.

The value is generated in this process and sent only to the Secrets API;
nothing prints it. Every mode first proves the SDK resolved the install's
workspace (``provision_runtime_secrets.bound_workspace``, whose refusal names
the hosts it compared); the tool's own lines name scopes and keys only, and
an SDK error only by its class. Exit codes: 0 done; 2 refused, with no put
attempted; 1 a put was attempted and its result could not be proven.
"""

from __future__ import annotations

import argparse
import re
import secrets
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.databricks.provision_runtime_secrets import bound_workspace  # noqa: E402

SALT_KEY = "pii-salt-v1"
DEFAULT_SCOPE = "mip"
EXIT_WRITE_UNPROVEN = 1
EXIT_REFUSED = 2
_SCOPE_RE = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")
_PREFIX = "[pii-salt]"


class SaltRefused(Exception):
    """The inventory is not authoritative or the policy forbids a write; no put ran."""


class SaltWriteUnproven(Exception):
    """A put was attempted and its result could not be proven."""


@dataclass(frozen=True)
class SaltInventory:
    scope_exists: bool
    key_present: bool


def _field(item: object, name: str) -> str:
    raw = item.get(name) if isinstance(item, dict) else getattr(item, name, None)
    return str(getattr(raw, "value", raw) or "").strip()


def _listed(call: Callable[[], Iterable[Any]], *, what: str, attribute: str) -> set[str]:
    """Every name ``call`` lists, or a refusal; an error never means "absent"."""

    try:
        names = [_field(item, attribute) for item in call()]
    except Exception as exc:  # noqa: BLE001 -- any list failure is non-authoritative
        raise SaltRefused(
            f"could not list {what} ({type(exc).__name__}), so absence is not proven"
        ) from exc
    if any(not name for name in names) or len(names) != len(set(names)):
        raise SaltRefused(f"the {what} inventory is malformed, so absence is not proven")
    return set(names)


def inspect_salt(workspace: Any, *, scope: str) -> SaltInventory:
    """List-only inventory of the scope and the salt key."""

    scopes = _listed(workspace.secrets.list_scopes, what="secret scopes", attribute="name")
    if scope not in scopes:
        return SaltInventory(scope_exists=False, key_present=False)
    keys = _listed(
        lambda: workspace.secrets.list_secrets(scope=scope),
        what=f"the keys in scope {scope}",
        attribute="key",
    )
    return SaltInventory(scope_exists=True, key_present=SALT_KEY in keys)


def _refuse_absent(scope: str, inventory: SaltInventory) -> SaltRefused:
    missing = f"scope {scope}" if not inventory.scope_exists else f"{scope}/{SALT_KEY}"
    return SaltRefused(
        f"{missing} is absent, but this install requires an existing salt; a new one "
        "would re-key every masked identifier. Restore the original value or run with "
        "--require-existing 0 only for a first install"
    )


def check_salt(workspace: Any, *, scope: str, require_existing: bool) -> SaltInventory:
    """Read-only: refuse exactly where ``ensure_salt`` would refuse before a put."""

    inventory = inspect_salt(workspace, scope=scope)
    if not inventory.key_present and require_existing:
        raise _refuse_absent(scope, inventory)
    return inventory


def ensure_salt(
    workspace: Any,
    *,
    scope: str,
    require_existing: bool,
    token: Callable[[int], str] | None = None,
) -> str:
    """Create the salt once. Returns ``"kept"`` or ``"written"``."""

    inventory = check_salt(workspace, scope=scope, require_existing=require_existing)
    if inventory.key_present:
        return "kept"
    if not inventory.scope_exists:
        try:
            workspace.secrets.create_scope(scope=scope)
        except Exception as exc:  # noqa: BLE001 -- no put follows a failed create
            raise SaltRefused(f"could not create scope {scope} ({type(exc).__name__})") from exc
        inventory = inspect_salt(workspace, scope=scope)
        if not inventory.scope_exists:
            raise SaltRefused(f"scope {scope} is not listed after its create")
        if inventory.key_present:
            return "kept"
    try:
        workspace.secrets.put_secret(
            scope=scope, key=SALT_KEY, string_value=(token or secrets.token_hex)(32)
        )
    except Exception as exc:  # noqa: BLE001 -- the put may still have landed
        raise SaltWriteUnproven(
            f"the put of {scope}/{SALT_KEY} failed ({type(exc).__name__}); its result is unknown"
        ) from exc
    try:
        after = inspect_salt(workspace, scope=scope)
    except SaltRefused as exc:
        raise SaltWriteUnproven(f"{scope}/{SALT_KEY} was put but not re-listed: {exc}") from exc
    if not after.key_present:
        raise SaltWriteUnproven(f"{scope}/{SALT_KEY} was put but is not listed afterwards")
    return "written"


def main(argv: list[str] | None = None, *, client: Any | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=("ensure", "check"))
    parser.add_argument("--scope", required=True)
    parser.add_argument("--require-existing", required=True, choices=("0", "1"))
    args = parser.parse_args(argv)
    scope = args.scope
    require_existing = args.require_existing == "1"
    if _SCOPE_RE.fullmatch(scope) is None:
        print(
            f"{_PREFIX} refused: the scope name is invalid. Nothing was read or written.",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    try:
        workspace, _host = bound_workspace(client)
    except Exception as exc:  # noqa: BLE001 -- includes WorkspaceHostRefused
        print(f"{_PREFIX} refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    try:
        if args.mode == "check":
            inventory = check_salt(workspace, scope=scope, require_existing=require_existing)
            if inventory.key_present:
                state = "present; nothing to write"
            elif inventory.scope_exists:
                state = "absent; the provisioning step will write it once"
            else:
                state = "absent with its scope; the provisioning step will create both"
            print(f"{_PREFIX} {scope}/{SALT_KEY} is {state} (read-only check, nothing written)")
            return 0
        outcome = ensure_salt(workspace, scope=scope, require_existing=require_existing)
    except SaltRefused as exc:
        print(f"{_PREFIX} refused: {exc}. No put was attempted.", file=sys.stderr)
        return EXIT_REFUSED
    except SaltWriteUnproven as exc:
        print(
            f"{_PREFIX} failed: {exc}. Re-run to re-list before any further write.", file=sys.stderr
        )
        return EXIT_WRITE_UNPROVEN
    if outcome == "kept":
        print(
            f"{_PREFIX} {scope}/{SALT_KEY} already present; left untouched "
            "(rotation would break masked-ID stability)"
        )
    else:
        print(
            f"{_PREFIX} generated {scope}/{SALT_KEY} (random 64-hex, write-once) "
            "and verified it is listed"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
