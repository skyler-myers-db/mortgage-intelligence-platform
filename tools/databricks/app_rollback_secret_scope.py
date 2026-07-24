"""Own and audit the deterministic Databricks App rollback secret scope."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
from dataclasses import dataclass
from typing import Any

from databricks.sdk import WorkspaceClient

MARKER_KEY = "mip-app-rollback-scope-binding-v1"
MARKER_VERSION = 1
_APP_NAME_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_SCOPE_RE = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")


def _field(value: object, name: str) -> str:
    raw = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
    return str(getattr(raw, "value", raw) or "").strip()


def expected_app_rollback_scope(app_name: str) -> str:
    normalized = app_name.strip()
    if _APP_NAME_RE.fullmatch(normalized) is None:
        raise ValueError("rollback-scope App name is invalid")
    if normalized.endswith("-app"):
        scope = f"{normalized}-rollback"
    elif "-app-" in normalized:
        prefix, suffix = normalized.rsplit("-app-", 1)
        scope = f"{prefix}-app-rollback-{suffix}"
    else:
        scope = f"{normalized}-rollback"
    if _SCOPE_RE.fullmatch(scope) is None:
        raise ValueError("deterministic App rollback scope is invalid")
    return scope


@dataclass(frozen=True)
class AppRollbackScopeBinding:
    app_name: str
    scope: str
    deployer_principal: str
    version: int = MARKER_VERSION

    def canonical_json(self) -> str:
        return json.dumps(
            {
                "app_name": self.app_name,
                "deployer_principal": self.deployer_principal,
                "scope": self.scope,
                "version": self.version,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )


def _scope_names(workspace: Any) -> set[str]:
    names = [_field(item, "name") for item in workspace.secrets.list_scopes()]
    if any(not name for name in names) or len(names) != len(set(names)):
        raise RuntimeError("App rollback secret-scope inventory is malformed")
    return set(names)


def _scope_acls(workspace: Any, *, scope: str) -> dict[str, str]:
    rows = list(workspace.secrets.list_acls(scope=scope))
    principals = [_field(item, "principal") for item in rows]
    permissions = [_field(item, "permission").upper() for item in rows]
    if any(not item for item in (*principals, *permissions)) or len(principals) != len(
        set(principals)
    ):
        raise RuntimeError("App rollback secret-scope ACL inventory is malformed")
    return dict(zip(principals, permissions, strict=True))


def _scope_keys(workspace: Any, *, scope: str) -> set[str]:
    keys = [_field(item, "key") for item in workspace.secrets.list_secrets(scope=scope)]
    if any(not key for key in keys) or len(keys) != len(set(keys)):
        raise RuntimeError("App rollback secret-key inventory is malformed")
    return set(keys)


def _request_issuer_principals(workspace: Any) -> set[str]:
    issuer = workspace.current_user.me()
    principals = {
        value
        for value in (
            _field(issuer, "user_name"),
            _field(issuer, "application_id"),
        )
        if value
    }
    if not principals:
        raise RuntimeError("App rollback scope request issuer is unidentified")
    return principals


def _marker_value(workspace: Any, *, scope: str) -> str:
    encoded = _field(workspace.secrets.get_secret(scope, MARKER_KEY), "value")
    if not encoded:
        raise RuntimeError("App rollback scope ownership marker is empty")
    try:
        return base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError("App rollback scope ownership marker is invalid") from exc


def _binding_from_marker(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
) -> AppRollbackScopeBinding:
    try:
        value = json.loads(_marker_value(workspace, scope=scope))
    except json.JSONDecodeError as exc:
        raise RuntimeError("App rollback scope ownership marker is invalid") from exc
    if not isinstance(value, dict) or set(value) != {
        "app_name",
        "deployer_principal",
        "scope",
        "version",
    }:
        raise RuntimeError("App rollback scope ownership marker is invalid")
    if (
        not isinstance(value["app_name"], str)
        or not isinstance(value["scope"], str)
        or not isinstance(value["deployer_principal"], str)
        or not isinstance(value["version"], int)
        or isinstance(value["version"], bool)
    ):
        raise RuntimeError("App rollback scope ownership marker is invalid")
    binding = AppRollbackScopeBinding(
        app_name=value["app_name"],
        scope=value["scope"],
        deployer_principal=value["deployer_principal"],
        version=value["version"],
    )
    if (
        binding.version != MARKER_VERSION
        or binding.app_name != app_name
        or binding.scope != scope
        or not binding.deployer_principal.strip()
        or binding.deployer_principal != binding.deployer_principal.strip()
        or binding.deployer_principal.casefold() == "admins"
        or binding.scope != expected_app_rollback_scope(binding.app_name)
        or binding.canonical_json() != json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
    ):
        raise RuntimeError("App rollback scope belongs to a different deployment")
    return binding


def _expected_keys(app_name: str) -> set[str]:
    return {
        MARKER_KEY,
        f"app-last-good-v5-{app_name}",
        f"app-last-good-v6-{app_name}",
    }


def _assert_valid_signed_legacy_record(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
) -> None:
    key = f"app-last-good-v5-{app_name}"
    encoded = _field(workspace.secrets.get_secret(scope, key), "value")
    try:
        raw = base64.b64decode(encoded, validate=True).decode("utf-8")
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("legacy App rollback record is invalid") from exc
    from tools.databricks.app_rollback_record_contract import (
        LEGACY_RECORD_VERSION,
        _validated_record,
        _verify_record_attestation,
    )

    if not isinstance(value, dict) or value.get("version") != LEGACY_RECORD_VERSION:
        raise RuntimeError("legacy App rollback record is invalid")
    _verify_record_attestation(value)
    lakebase_instance = os.environ.get("MIP_LAKEBASE_INSTANCE", "").strip()
    if not lakebase_instance:
        raise RuntimeError("legacy App rollback adoption requires its Lakebase target")
    _validated_record(
        value,
        app_name=app_name,
        expected_lakebase_instance=lakebase_instance,
    )


def assert_owned_app_rollback_scope(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
) -> AppRollbackScopeBinding:
    expected_scope = expected_app_rollback_scope(app_name)
    if scope.strip() != expected_scope:
        raise ValueError(
            "App rollback secret scope must equal the deterministic App-bound "
            f"scope {expected_scope!r}"
        )
    if expected_scope not in _scope_names(workspace):
        raise RuntimeError("App rollback secret scope is absent")
    keys = _scope_keys(workspace, scope=expected_scope)
    if MARKER_KEY not in keys:
        raise RuntimeError("existing App rollback scope has no ownership marker")
    binding = _binding_from_marker(
        workspace,
        app_name=app_name.strip(),
        scope=expected_scope,
    )
    if binding.deployer_principal not in _request_issuer_principals(workspace):
        raise RuntimeError(
            "App rollback scope deployer does not match the current deployment issuer"
        )
    if _scope_acls(workspace, scope=expected_scope) != {
        "admins": "MANAGE",
        binding.deployer_principal: "MANAGE",
    }:
        raise RuntimeError("App rollback secret-scope ACL ownership proof failed")
    if not keys.issubset(_expected_keys(binding.app_name)):
        raise RuntimeError("App rollback secret scope contains an unreviewed key")
    return binding


def ensure_owned_app_rollback_scope(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
) -> AppRollbackScopeBinding:
    expected_scope = expected_app_rollback_scope(app_name)
    if scope.strip() != expected_scope:
        raise ValueError(
            "App rollback secret scope must equal the deterministic App-bound "
            f"scope {expected_scope!r}"
        )
    names = _scope_names(workspace)
    keys: set[str] = set()
    if expected_scope in names:
        keys = _scope_keys(workspace, scope=expected_scope)
        if MARKER_KEY in keys:
            return assert_owned_app_rollback_scope(
                workspace,
                app_name=app_name,
                scope=expected_scope,
            )
        acls = _scope_acls(workspace, scope=expected_scope)
    else:
        workspace.secrets.create_scope(scope=expected_scope)
        acls = _scope_acls(workspace, scope=expected_scope)
    issuer_principals = _request_issuer_principals(workspace)
    creator_acls = {
        principal: permission
        for principal, permission in acls.items()
        if principal != "admins"
    }
    if (
        len(creator_acls) != 1
        or set(creator_acls.values()) != {"MANAGE"}
        or not set(creator_acls).issubset(issuer_principals)
        or set(acls) - {"admins"} - set(creator_acls)
        or ("admins" in acls and acls["admins"] != "MANAGE")
    ):
        raise RuntimeError(
            "unmarked App rollback scope is not exclusively owned by the "
            "current request issuer"
        )
    deployer_principal = next(iter(creator_acls))
    if deployer_principal.casefold() == "admins":
        raise RuntimeError("App rollback scope request issuer cannot be the admins group")
    if keys:
        expected_legacy_key = f"app-last-good-v5-{app_name.strip()}"
        if keys != {expected_legacy_key}:
            raise RuntimeError(
                "existing unmarked App rollback scope contains unreviewed keys"
            )
        _assert_valid_signed_legacy_record(
            workspace,
            app_name=app_name.strip(),
            scope=expected_scope,
        )
    binding = AppRollbackScopeBinding(
        app_name=app_name.strip(),
        scope=expected_scope,
        deployer_principal=deployer_principal,
    )
    if "admins" not in acls:
        workspace.secrets.put_acl(
            scope=expected_scope,
            principal="admins",
            permission="MANAGE",
        )
    workspace.secrets.put_secret(
        scope=expected_scope,
        key=MARKER_KEY,
        string_value=binding.canonical_json(),
    )
    return assert_owned_app_rollback_scope(
        workspace,
        app_name=app_name,
        scope=expected_scope,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("ensure", "assert"))
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--scope", required=True)
    args = parser.parse_args(argv)
    workspace = WorkspaceClient()
    if args.mode == "ensure":
        ensure_owned_app_rollback_scope(
            workspace,
            app_name=args.app_name,
            scope=args.scope,
        )
    else:
        assert_owned_app_rollback_scope(
            workspace,
            app_name=args.app_name,
            scope=args.scope,
        )
    print("[app-rollback-scope] deterministic ownership and key inventory verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
