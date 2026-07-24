"""Own and inspect the deterministic Databricks secret scope for the agent proxy."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Any

MARKER_KEY = "mip-agent-proxy-scope-binding-v1"
MARKER_VERSION = 1
_APP_NAME_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_SCOPE_RE = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")
_CREDENTIAL_KEY_PREFIX = "oauth-client-secret-"


def _field(value: object, name: str) -> str:
    raw = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
    return str(getattr(raw, "value", raw) or "").strip()


def expected_agent_proxy_secret_scope(app_name: str) -> str:
    """Return the only secret-scope name accepted for one App."""

    normalized = app_name.strip()
    if _APP_NAME_RE.fullmatch(normalized) is None:
        raise ValueError("agent-proxy App name is invalid")
    scope = f"{normalized}-agent-proxy"
    if _SCOPE_RE.fullmatch(scope) is None:
        raise ValueError("deterministic agent-proxy secret scope is invalid")
    return scope


@dataclass(frozen=True)
class AgentProxyScopeBinding:
    app_name: str
    scope: str
    runtime_application_id: str
    proxy_application_id: str
    version: int = MARKER_VERSION

    def canonical_json(self) -> str:
        return json.dumps(
            {
                "app_name": self.app_name,
                "proxy_application_id": self.proxy_application_id,
                "runtime_application_id": self.runtime_application_id,
                "scope": self.scope,
                "version": self.version,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )


def validated_scope_binding(
    *,
    app_name: str,
    scope: str,
    runtime_application_id: str,
    proxy_application_id: str,
) -> AgentProxyScopeBinding:
    expected_scope = expected_agent_proxy_secret_scope(app_name)
    normalized_scope = scope.strip()
    runtime_id = runtime_application_id.strip()
    proxy_id = proxy_application_id.strip()
    if normalized_scope != expected_scope:
        raise ValueError(
            "agent-proxy secret scope must equal the deterministic App-bound scope "
            f"{expected_scope!r}"
        )
    if not runtime_id or not proxy_id or runtime_id.casefold() == proxy_id.casefold():
        raise ValueError("runtime and agent-proxy application IDs must be distinct")
    return AgentProxyScopeBinding(
        app_name=app_name.strip(),
        scope=normalized_scope,
        runtime_application_id=runtime_id,
        proxy_application_id=proxy_id,
    )


def _scope_names(workspace: Any) -> list[str]:
    names = [_field(item, "name") for item in workspace.secrets.list_scopes()]
    if any(not name for name in names) or len(names) != len(set(names)):
        raise RuntimeError("Databricks secret-scope inventory is malformed")
    return names


def _scope_acls(workspace: Any, *, scope: str) -> dict[str, str]:
    rows = list(workspace.secrets.list_acls(scope=scope))
    principals = [_field(row, "principal") for row in rows]
    permissions = [_field(row, "permission").upper() for row in rows]
    if any(not value for value in (*principals, *permissions)) or len(principals) != len(
        set(principals)
    ):
        raise RuntimeError("agent-proxy secret-scope ACL inventory is malformed")
    return dict(zip(principals, permissions, strict=True))


def _scope_keys(workspace: Any, *, scope: str) -> set[str]:
    values = [_field(item, "key") for item in workspace.secrets.list_secrets(scope=scope)]
    if any(not value for value in values) or len(values) != len(set(values)):
        raise RuntimeError("agent-proxy secret-key inventory is malformed")
    return set(values)


def _marker_value(workspace: Any, *, scope: str) -> str:
    encoded = _field(workspace.secrets.get_secret(scope, MARKER_KEY), "value")
    if not encoded:
        raise RuntimeError("agent-proxy secret-scope binding marker is empty")
    try:
        return base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError("agent-proxy secret-scope binding marker is invalid") from exc


def _assert_marker(
    workspace: Any,
    *,
    binding: AgentProxyScopeBinding,
) -> None:
    if _marker_value(workspace, scope=binding.scope) != binding.canonical_json():
        raise RuntimeError("agent-proxy secret scope belongs to a different deployment")


def _assert_reviewed_keys(keys: set[str]) -> None:
    unexpected = {
        key for key in keys if key != MARKER_KEY and not key.startswith(_CREDENTIAL_KEY_PREFIX)
    }
    if unexpected:
        raise RuntimeError("agent-proxy secret scope contains an unreviewed key")


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
        raise RuntimeError("agent-proxy secret-scope request issuer is unidentified")
    return principals


def _initialization_creator(
    workspace: Any,
    *,
    acls: dict[str, str],
    runtime_application_id: str | None = None,
) -> str | None:
    if (
        runtime_application_id
        and runtime_application_id in acls
        and acls[runtime_application_id] != "READ"
    ):
        raise RuntimeError("agent-proxy runtime secret-scope ACL is not READ")
    creator_acls = {
        principal: permission
        for principal, permission in acls.items()
        if principal not in {"admins", runtime_application_id}
    }
    if not creator_acls:
        return None
    if (
        len(creator_acls) != 1
        or set(creator_acls.values()) != {"MANAGE"}
        or not set(creator_acls).issubset(_request_issuer_principals(workspace))
    ):
        raise RuntimeError("agent-proxy secret-scope creator ACL is not the request issuer")
    return next(iter(creator_acls))


def ensure_owned_agent_proxy_scope(
    workspace: Any,
    *,
    binding: AgentProxyScopeBinding,
) -> None:
    """Create or prove the owned scope before adding its one missing runtime ACL."""

    names = _scope_names(workspace)
    if binding.scope not in names:
        # The Secrets API supports only ``users`` for
        # initial_manage_principal. Omitting it gives MANAGE to the exact
        # request issuer, which we prove below before converging the durable
        # admins/runtime ACL.
        workspace.secrets.create_scope(scope=binding.scope)

    acls = _scope_acls(workspace, scope=binding.scope)
    keys = _scope_keys(workspace, scope=binding.scope)
    expected_acl = {
        "admins": "MANAGE",
        binding.runtime_application_id: "READ",
    }
    initialization_acl = {"admins": "MANAGE"}
    if MARKER_KEY not in keys:
        try:
            creator = _initialization_creator(workspace, acls=acls)
        except RuntimeError as exc:
            raise RuntimeError(
                "existing agent-proxy scope lacks ownership proof and is not a safe "
                "empty request-issuer/admins interrupted initialization"
            ) from exc
        allowed_initial_principals = {"admins"}
        if creator:
            allowed_initial_principals.add(creator)
        if (
            keys
            or not acls
            or not set(acls) <= allowed_initial_principals
            or set(acls.values()) != {"MANAGE"}
        ):
            raise RuntimeError(
                "existing agent-proxy scope lacks ownership proof and is not a safe "
                "empty request-issuer/admins interrupted initialization"
            )
        if "admins" not in acls:
            workspace.secrets.put_acl(
                scope=binding.scope,
                principal="admins",
                permission="MANAGE",
            )
        workspace.secrets.put_secret(
            scope=binding.scope,
            key=MARKER_KEY,
            string_value=binding.canonical_json(),
        )
        _assert_marker(workspace, binding=binding)
        keys = _scope_keys(workspace, scope=binding.scope)
    else:
        _assert_marker(workspace, binding=binding)

    _assert_reviewed_keys(keys)
    acls = _scope_acls(workspace, scope=binding.scope)
    creator = _initialization_creator(
        workspace,
        acls=acls,
        runtime_application_id=binding.runtime_application_id,
    )
    converging_acl = dict(initialization_acl)
    if creator:
        converging_acl[creator] = "MANAGE"
    if binding.runtime_application_id in acls:
        converging_acl[binding.runtime_application_id] = "READ"
    if acls != converging_acl:
        raise RuntimeError(
            "agent-proxy secret-scope ACL contains an unexpected principal or permission"
        )
    if creator:
        workspace.secrets.delete_acl(binding.scope, creator)
    if binding.runtime_application_id not in acls:
        workspace.secrets.put_acl(
            scope=binding.scope,
            principal=binding.runtime_application_id,
            permission="READ",
        )
    final_acl = _scope_acls(workspace, scope=binding.scope)
    final_keys = _scope_keys(workspace, scope=binding.scope)
    _assert_marker(workspace, binding=binding)
    _assert_reviewed_keys(final_keys)
    if final_acl != expected_acl:
        raise RuntimeError("agent-proxy secret-scope ACL postflight failed")


def assert_owned_agent_proxy_scope(
    workspace: Any,
    *,
    binding: AgentProxyScopeBinding,
) -> set[str]:
    """Return reviewed keys only after exact durable ownership and ACL proof."""

    if binding.scope not in _scope_names(workspace):
        raise RuntimeError("agent-proxy secret scope is absent")
    acls = _scope_acls(workspace, scope=binding.scope)
    keys = _scope_keys(workspace, scope=binding.scope)
    _assert_reviewed_keys(keys)
    if MARKER_KEY not in keys:
        raise RuntimeError("agent-proxy secret scope has no ownership marker")
    _assert_marker(workspace, binding=binding)
    if acls != {
        "admins": "MANAGE",
        binding.runtime_application_id: "READ",
    }:
        raise RuntimeError("agent-proxy secret-scope ACL ownership proof failed")
    return keys


__all__ = [
    "MARKER_KEY",
    "AgentProxyScopeBinding",
    "assert_owned_agent_proxy_scope",
    "ensure_owned_agent_proxy_scope",
    "expected_agent_proxy_secret_scope",
    "validated_scope_binding",
]
