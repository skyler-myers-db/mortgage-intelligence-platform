"""Exact, fail-closed ACL contract for a governed MLflow experiment."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

_CONTRACT_VERSION = "mip-gateway-experiment-acl-v1"
_CAN_MANAGE = "CAN_MANAGE"
_ADMINS_GROUP = "admins"


@dataclass(frozen=True)
class ExactExperimentAcl:
    """Canonical ACL bytes and digest bound to one immutable experiment ID."""

    canonical_json: str
    sha256: str


def _mapping(value: Any, *, resource: str) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        converted = as_dict()
        if isinstance(converted, Mapping):
            return converted
    raise RuntimeError(f"{resource} returned an invalid permission document")


def _principal(entry: Mapping[str, Any]) -> tuple[str, str]:
    candidates = [
        ("service_principal", str(entry.get("service_principal_name") or "").strip()),
        ("user", str(entry.get("user_name") or "").strip()),
        ("group", str(entry.get("group_name") or "").strip()),
    ]
    resolved = [(kind, name) for kind, name in candidates if name]
    if len(resolved) != 1:
        raise RuntimeError("Gateway MLflow experiment ACL has an invalid principal")
    return resolved[0]


def _permission(entry: Mapping[str, Any]) -> dict[str, Any]:
    permissions = entry.get("all_permissions")
    if not isinstance(permissions, list) or len(permissions) != 1:
        raise RuntimeError("Gateway MLflow experiment ACL is not exact")
    permission = _mapping(
        permissions[0],
        resource="Gateway MLflow experiment ACL",
    )
    level = str(permission.get("permission_level") or "").strip().upper()
    inherited = permission.get("inherited")
    inherited_from = permission.get("inherited_from_object") or []
    if level != _CAN_MANAGE or not isinstance(inherited, bool):
        raise RuntimeError("Gateway MLflow experiment ACL is not exact")
    if not isinstance(inherited_from, list) or any(
        not isinstance(item, str) or not item.strip() for item in inherited_from
    ):
        raise RuntimeError("Gateway MLflow experiment ACL is not exact")
    return {
        "permission_level": level,
        "inherited": inherited,
        "inherited_from_object": sorted(item.strip() for item in inherited_from),
    }


def resolve_exact_experiment_acl(
    workspace: Any,
    *,
    experiment_id: str,
    runtime_application_id: str,
) -> ExactExperimentAcl:
    """Require runtime CAN_MANAGE and no access beyond the admins group."""

    immutable_id = experiment_id.strip()
    runtime_id = runtime_application_id.strip()
    if not immutable_id or not runtime_id:
        raise RuntimeError("Gateway MLflow experiment ACL scope is incomplete")
    try:
        response = workspace.api_client.do(
            "GET",
            f"/api/2.0/permissions/experiments/{quote(immutable_id, safe='')}",
        )
    except Exception as exc:  # noqa: BLE001 - permission proof is fail-closed
        raise RuntimeError("could not read the exact Gateway MLflow experiment ACL") from exc
    document = _mapping(response, resource="Gateway MLflow experiment ACL")
    entries = document.get("access_control_list")
    if not isinstance(entries, list) or not entries:
        raise RuntimeError("Gateway MLflow experiment ACL is missing")

    normalized: list[dict[str, Any]] = []
    runtime_grants = 0
    admin_grants = 0
    for raw_entry in entries:
        entry = _mapping(raw_entry, resource="Gateway MLflow experiment ACL")
        principal_type, principal_name = _principal(entry)
        permission = _permission(entry)
        if principal_type == "service_principal" and principal_name == runtime_id:
            if permission["inherited"] or permission["inherited_from_object"]:
                raise RuntimeError(
                    "Gateway MLflow experiment runtime CAN_MANAGE grant is not direct"
                )
            runtime_grants += 1
        elif principal_type == "group" and principal_name == _ADMINS_GROUP:
            admin_grants += 1
        else:
            raise RuntimeError(
                "Gateway MLflow experiment ACL grants access to an unexpected principal"
            )
        normalized.append(
            {
                "principal_type": principal_type,
                "principal_name": principal_name,
                **permission,
            }
        )
    if runtime_grants != 1:
        raise RuntimeError(
            "Gateway MLflow experiment ACL requires one direct runtime CAN_MANAGE grant"
        )
    if admin_grants > 1:
        raise RuntimeError("Gateway MLflow experiment ACL has duplicate admins grants")

    contract = {
        "contract_version": _CONTRACT_VERSION,
        "experiment_id": immutable_id,
        "access_control_list": sorted(
            normalized,
            key=lambda item: (item["principal_type"], item["principal_name"]),
        ),
    }
    canonical_json = json.dumps(contract, sort_keys=True, separators=(",", ":"))
    return ExactExperimentAcl(
        canonical_json=canonical_json,
        sha256=hashlib.sha256(canonical_json.encode("utf-8")).hexdigest(),
    )
