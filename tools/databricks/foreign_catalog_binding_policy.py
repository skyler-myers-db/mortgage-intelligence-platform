"""The reviewed foreign-catalog binding-denial policy (``MIP_UC_FOREIGN_CATALOG_BINDING_POLICY``).

Moved verbatim out of ``audit_agent_runtime_foreign_uc_access`` (M2-PR4, to
keep that audit under the file-size gate). The audit still imports the parser
and the binding-type set from here, so its callers and tests are unchanged.
"""

from __future__ import annotations

import json

from tools.databricks.agent_runtime_uc_baseline import CatalogBindingEvidence

_WORKSPACE_BINDING_TYPES = frozenset(
    {
        "BINDING_TYPE_READ_ONLY",
        "BINDING_TYPE_READ_WRITE",
    }
)


class _DuplicatePolicyKey(ValueError):
    pass


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicatePolicyKey(key)
        result[key] = value
    return result


def parse_foreign_catalog_binding_policy(raw: str) -> dict[str, CatalogBindingEvidence]:
    """Parse the exact reviewed binding-denial policy without permissive defaults."""

    value = raw.strip()
    if not value:
        return {}
    try:
        payload = json.loads(value, object_pairs_hook=_strict_json_object)
    except json.JSONDecodeError as exc:
        raise ValueError("foreign catalog binding policy is not valid JSON") from exc
    except _DuplicatePolicyKey as exc:
        raise ValueError("foreign catalog binding policy contains a duplicate key") from exc
    if not isinstance(payload, dict) or set(payload) != {"version", "catalogs"}:
        raise ValueError("foreign catalog binding policy has an invalid top-level contract")
    if (
        type(payload["version"]) is not int
        or payload["version"] != 1
        or not isinstance(payload["catalogs"], dict)
    ):
        raise ValueError("foreign catalog binding policy has an unsupported version or catalogs")
    policy: dict[str, CatalogBindingEvidence] = {}
    for catalog, contract in payload["catalogs"].items():
        if (
            not isinstance(catalog, str)
            or not catalog.strip()
            or catalog != catalog.strip()
            or not isinstance(contract, dict)
            or set(contract) != {"owner", "catalog_type", "bindings"}
        ):
            raise ValueError("foreign catalog binding policy has an invalid catalog contract")
        owner = contract["owner"]
        catalog_type = contract["catalog_type"]
        bindings = contract["bindings"]
        if (
            not isinstance(owner, str)
            or not owner.strip()
            or owner != owner.strip()
            or not isinstance(catalog_type, str)
            or not catalog_type.strip()
            or catalog_type != catalog_type.strip()
            or not isinstance(bindings, list)
            or not bindings
        ):
            raise ValueError("foreign catalog binding policy has incomplete catalog evidence")
        normalized_bindings: list[tuple[str, str]] = []
        for binding in bindings:
            if not isinstance(binding, dict) or set(binding) != {
                "workspace_id",
                "binding_type",
            }:
                raise ValueError("foreign catalog binding policy has an invalid binding")
            binding_workspace_id = binding["workspace_id"]
            binding_type = binding["binding_type"]
            if (
                not isinstance(binding_workspace_id, str)
                or not binding_workspace_id.isdecimal()
                or int(binding_workspace_id) <= 0
                or str(int(binding_workspace_id)) != binding_workspace_id
                or binding_workspace_id != binding_workspace_id.strip()
                or not isinstance(binding_type, str)
                or binding_type not in _WORKSPACE_BINDING_TYPES
            ):
                raise ValueError("foreign catalog binding policy has an incomplete binding")
            normalized_bindings.append((binding_workspace_id, binding_type))
        if len({item[0] for item in normalized_bindings}) != len(normalized_bindings):
            raise ValueError("foreign catalog binding policy has duplicate workspace bindings")
        policy[catalog] = CatalogBindingEvidence(
            catalog=catalog,
            owner=owner,
            catalog_type=catalog_type,
            isolation_mode="ISOLATED",
            bindings=tuple(sorted(normalized_bindings)),
        )
    return policy
