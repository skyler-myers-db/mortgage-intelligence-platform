"""The v7 signed App rollback record: tier-aware, with provenance (M2-PR7).

v5 and v6 records keep exactly the validation they had
(tools/databricks/app_rollback_record_contract.py); v7 is a separate,
exact-field schema stored in v6's current-record slot and signed with its own
algorithm name. Its fields:

* ``tier``: ``core`` or ``agentic``, which must equal the profile's tier
  (``MIP_AGENTIC_TIER`` off or on) for any capture or adoption; a restore
  re-proves the record's own tier.
  - **core**: the resource proof is exactly the core App bindings the profile
    binds (SQL warehouse, Genie space, Lakebase database, jobs, secrets:
    ``app_rollback_resource_contract.RESOURCE_KINDS``). A serving-endpoint,
    Gateway or proxy binding refuses, the record carries no Gateway resource
    contract, binding or proxy-retirement journal, its env-name set carries no
    serving, Gateway, Supervisor or proxy name, and nothing reading it ever
    consults the Gateway resource proof.
  - **agentic**: v6's Gateway resource contract, binding and proxy-retirement
    journal, validated by v6's own functions, unchanged.
* ``provenance``: ``adopted`` (adopt-live signed a deployment this script did
  not make), ``captured`` (a governed release's signed capture; its writer is
  the core release path, M2-PR7b) or ``retired`` (unadopt's tombstone, which
  holds no rollback target, so a later adopt starts clean). Nothing is ever
  deleted: a tombstone keeps every field of the record it retires.
* ``ack_purpose`` / ``ack_sha256``: the purpose-named exact digest ack that
  authorised the version (tools/databricks/operation_ack.py). The plan names a
  ``profile_attestation_digest`` here, which needs the deferred owner
  attestation (M1-PR5); this field names what actually authorised the record.
  A capture is a routine release and carries none.
* ``source_code_path`` and ``source_tree_sha256``: the deployment's source
  folder and its full-tree digest (``app_start_deployment.tree_digest``: every
  file, ``app.yaml`` and ``frontend/dist`` included).
* ``restore_source_path``: the immutable, create-only copy a tier-b restore
  deploys from (``app-src/adopted-*`` for an adoption, a release folder for a
  capture); never a ``/src/`` snapshot.
* ``frontend_provenance``: ``verified`` when ``frontend/dist`` equals the build
  artifact for ``git_sha``, else ``unverified``; a restore to an unverified
  record needs the ``restore_unverified_frontend`` ack.
* ``env_names`` / ``env_names_sha256``: the deployment's env-name set (names
  only: Databricks redacts literal values from ``get_deployment``).
* ``restore_payload`` / ``restore_payload_sha256``: blue's tier-b restore
  payload, built and proven before a green activation
  (tools/databricks/blue_restore_proof.py); both null until then.
* ``predecessor_deployment_id``: the deployment a supersede or re-pin
  replaced, else null.
* ``genie_space_id``, ``app_acl`` / ``app_acl_sha256`` (the App ACL snapshot)
  and ``treatment_mode`` (the reviewed treatment boundary the deployment was
  verified in).
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

from tools.databricks import app_release_contract as release
from tools.databricks.app_rollback_record_contract import (
    V7_RECORD_VERSION,
    _payload_digest,
    _validated_gateway_resources,
    _validated_payload,
    _validated_pending_proxy_credential_retirement_ids,
)
from tools.databricks.app_rollback_resource_contract import (
    RESOURCE_KINDS,
    app_resource_contract_digest,
    validated_app_resource_contract,
)
from tools.databricks.app_start_deployment import env_names_digest

TIERS = ("core", "agentic")
PROVENANCES = ("adopted", "captured", "retired")
FRONTEND_PROVENANCES = ("verified", "unverified")
TREATMENT_MODES = ("quiesce", "runtime")
# The client profile's MIP_AGENTIC_TIER (m2m_identity_contract.agentic_tier).
TIER_OF_PROFILE = {"off": "core", "on": "agentic"}
ACK_PURPOSES = {
    "adopted": frozenset({"adopt_live", "supersede"}),
    "retired": frozenset({"unadopt"}),
    "captured": frozenset(),
}
# The App env names only the agentic tier carries: the serving endpoint, the
# AI Gateway, the Supervisor, the agent runtime and the proxy caller.
AGENTIC_ENV_PREFIXES = (
    "MIP_AGENT_PROXY_",
    "MIP_AGENT_SUPERVISOR_",
    "MIP_AI_GATEWAY_",
    "MIP_EXPECTED_AGENT_GATEWAY_",
)
AGENTIC_ENV_NAMES = frozenset(
    {"MIP_AGENT_SERVING_ENDPOINT", "MIP_AGENT_RUNTIME_CLIENT_ID", "MIP_REVIEWED_FUNCTION_OWNER"}
)
# Binding kinds the core tier refuses by name (any other non-core kind refuses too).
AGENTIC_RESOURCE_KINDS = frozenset({"serving_endpoint", "uc_securable", "app"})
COMMON_FIELDS = frozenset(
    {
        "version",
        "tier",
        "provenance",
        "app_name",
        "deployment_id",
        "git_sha",
        "app_service_principal_client_id",
        "app_service_principal_scim_id",
        "genie_space_id",
        "app_resources",
        "app_resources_sha256",
        "app_acl",
        "app_acl_sha256",
        "treatment_mode",
        "env_names",
        "env_names_sha256",
        "source_code_path",
        "source_tree_sha256",
        "restore_source_path",
        "frontend_provenance",
        "restore_payload",
        "restore_payload_sha256",
        "predecessor_deployment_id",
        "ack_purpose",
        "ack_sha256",
    }
)
AGENTIC_FIELDS = frozenset(
    {"gateway_binding_sha256", "gateway_resources", "pending_proxy_credential_retirement_ids"}
)
SIGNATURE_FIELDS = frozenset({"attestation_alg", "attestation_verify_key", "attestation_signature"})
_SHA256 = re.compile(r"[0-9a-f]{64}")
_GIT_SHA = re.compile(r"[0-9a-f]{40}")
_ACL_KEYS = frozenset({"principal", "permission", "inherited"})


class V7RecordError(RuntimeError):
    """A v7 record, or its tier or resource proof, is invalid."""


def tier_of_profile(agentic_tier: str) -> str:
    """The record tier a ``MIP_AGENTIC_TIER`` value (exactly off or on) requires."""

    try:
        return TIER_OF_PROFILE[agentic_tier]
    except KeyError as exc:
        raise V7RecordError("MIP_AGENTIC_TIER must be exactly off or on") from exc


def is_agentic_env_name(name: str) -> bool:
    return name in AGENTIC_ENV_NAMES or name.startswith(AGENTIC_ENV_PREFIXES)


def refuse_agentic_resources(items: Iterable[object]) -> None:
    """A core-tier resource proof refuses any binding outside the core kinds, by name."""

    for item in items:
        keys = set(item) if isinstance(item, Mapping) else set()
        agentic = sorted(keys & AGENTIC_RESOURCE_KINDS)
        if agentic:
            raise V7RecordError(
                f"a core-tier App record refuses a {agentic[0]} resource binding: the core "
                "tier binds only a warehouse, a Genie space, a Lakebase database, jobs and secrets"
            )
        if not keys & RESOURCE_KINDS:
            raise V7RecordError("a core-tier App record refuses a non-core resource binding")


def canonical_app_acl(entries: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
    """The App ACL snapshot: sorted unique {principal, permission, inherited} rows."""

    rows: list[dict[str, object]] = []
    for entry in entries:
        if not isinstance(entry, Mapping) or set(entry) != _ACL_KEYS:
            raise V7RecordError("App ACL snapshot row is invalid")
        principal, permission = entry["principal"], entry["permission"]
        if (
            not isinstance(principal, str)
            or not principal.strip()
            or not isinstance(permission, str)
            or not permission.strip()
            or not isinstance(entry["inherited"], bool)
        ):
            raise V7RecordError("App ACL snapshot row is invalid")
        rows.append(
            {"principal": principal, "permission": permission, "inherited": entry["inherited"]}
        )
    rows.sort(
        key=lambda row: (str(row["principal"]), str(row["permission"]), bool(row["inherited"]))
    )
    if not rows or any(left == right for left, right in zip(rows, rows[1:], strict=False)):
        raise V7RecordError("App ACL snapshot is empty or repeats a row")
    return rows


def app_acl_digest(rows: list[dict[str, object]]) -> str:
    canonical = json.dumps(canonical_app_acl(rows), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(b"mip-app-acl-v1\0" + canonical.encode("utf-8")).hexdigest()


def _env_names(value: object, *, tier: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(n, str) or not n for n in value)
    ):
        raise V7RecordError("App rollback v7 env-name set is invalid")
    if value != sorted(set(value)):
        raise V7RecordError("App rollback v7 env-name set is not sorted and unique")
    agentic = sorted(name for name in value if is_agentic_env_name(name))
    if tier == "core" and agentic:
        raise V7RecordError("a core-tier App record carries a serving, Gateway or proxy env name")
    return list(value)


def _genie_space_of(app_resources: list[dict[str, object]]) -> str:
    spaces = [row["genie_space"] for row in app_resources if "genie_space" in row]
    if len(spaces) != 1 or not isinstance(spaces[0], dict):
        raise V7RecordError("App rollback v7 record binds no single governed Genie space")
    return str(spaces[0]["space_id"])


def _restore_source(path: object, *, provenance: str) -> str:
    try:
        if provenance == "captured":
            release.release_id_in(path)
        else:
            release.adopted_folder_in(path)
    except release.ReleaseContractError as exc:
        raise V7RecordError(f"App rollback v7 restore source is invalid: {exc}") from exc
    return str(path)


def _restore_payload(
    value: Mapping[str, Any], *, expected_lakebase_instance: str
) -> tuple[dict[str, object] | None, str | None]:
    payload, digest = value.get("restore_payload"), value.get("restore_payload_sha256")
    if payload is None and digest is None:
        return None, None
    try:
        validated = _validated_payload(
            payload,
            require_immutable_source=False,
            expected_lakebase_instance=expected_lakebase_instance,
        )
    except RuntimeError as exc:
        raise V7RecordError(f"App rollback v7 restore payload is invalid: {exc}") from exc
    if validated["source_code_path"] != value["restore_source_path"]:
        raise V7RecordError("App rollback v7 restore payload is not the immutable restore source")
    names = sorted(str(item["name"]) for item in validated["env_vars"])  # type: ignore[union-attr]
    if names != value["env_names"]:
        raise V7RecordError("App rollback v7 restore payload env-name set is not blue's")
    if digest != _payload_digest(validated):
        raise V7RecordError("App rollback v7 restore payload digest does not match")
    return validated, str(digest)


def _agentic_fields(value: Mapping[str, Any]) -> dict[str, Any]:
    resources = _validated_gateway_resources(value.get("gateway_resources"))
    binding = value.get("gateway_binding_sha256")
    if binding is not None and (not isinstance(binding, str) or len(binding) != 64):
        raise V7RecordError("App rollback v7 Gateway binding is invalid")
    if not all(
        str(resources.get(key) or "").strip()
        for key in ("gateway_endpoint", "gateway_endpoint_id", "gateway_endpoint_creator")
    ):
        raise V7RecordError("App rollback v7 record lacks immutable Gateway identity")
    pending = _validated_pending_proxy_credential_retirement_ids(
        value.get("pending_proxy_credential_retirement_ids"),
        active_credential_id=resources.get("proxy_caller_credential_id"),
    )
    if resources.get("genie_space_id") != value["genie_space_id"]:
        raise V7RecordError("App rollback v7 Gateway contract names another Genie space")
    return {
        "gateway_resources": resources,
        "gateway_binding_sha256": binding,
        "pending_proxy_credential_retirement_ids": list(pending),
    }


def validated_v7_record(
    value: object, *, app_name: str, expected_lakebase_instance: str
) -> dict[str, Any]:
    """The exact v7 schema for the record's own tier; raises V7RecordError."""

    if not isinstance(value, dict) or value.get("version") != V7_RECORD_VERSION:
        raise V7RecordError("App rollback contract version is invalid")
    tier, provenance = value.get("tier"), value.get("provenance")
    if tier not in TIERS or provenance not in PROVENANCES:
        raise V7RecordError("App rollback v7 tier or provenance is invalid")
    expected = COMMON_FIELDS | (AGENTIC_FIELDS if tier == "agentic" else frozenset())
    if set(value) - SIGNATURE_FIELDS != expected:
        raise V7RecordError(f"App rollback v7 fields are not exactly the {tier} tier's")
    if value["app_name"] != app_name:
        raise V7RecordError("App rollback contract names a different App")
    identity = (
        value["deployment_id"],
        value["app_service_principal_client_id"],
        value["app_service_principal_scim_id"],
    )
    if any(
        not isinstance(item, str) or not item.strip() or item != item.strip() for item in identity
    ):
        raise V7RecordError("App rollback contract lacks immutable deployment identity")
    if not isinstance(value["git_sha"], str) or _GIT_SHA.fullmatch(value["git_sha"]) is None:
        raise V7RecordError("App rollback contract lacks immutable deployment identity")
    if tier == "core":
        refuse_agentic_resources(
            value["app_resources"] if isinstance(value["app_resources"], list) else []
        )
    try:
        app_resources = validated_app_resource_contract(value["app_resources"])
    except RuntimeError as exc:
        raise V7RecordError(str(exc)) from exc
    if value["app_resources_sha256"] != app_resource_contract_digest(app_resources):
        raise V7RecordError("App rollback resource binding digest does not match")
    if value["genie_space_id"] != _genie_space_of(app_resources):
        raise V7RecordError("App rollback v7 Genie space is not the bound space")
    acl = canonical_app_acl(value["app_acl"]) if isinstance(value["app_acl"], list) else []
    if not acl or value["app_acl"] != acl or value["app_acl_sha256"] != app_acl_digest(acl):
        raise V7RecordError("App rollback v7 App ACL snapshot is invalid")
    if value["treatment_mode"] not in TREATMENT_MODES:
        raise V7RecordError("App rollback v7 treatment mode is invalid")
    names = _env_names(value["env_names"], tier=str(tier))
    if value["env_names_sha256"] != env_names_digest(names):
        raise V7RecordError("App rollback v7 env-name digest does not match")
    source = value["source_code_path"]
    if not isinstance(source, str) or not source.startswith("/Workspace/Users/"):
        raise V7RecordError("App rollback v7 source is outside a workspace user home")
    if not isinstance(value["source_tree_sha256"], str) or not _SHA256.fullmatch(
        value["source_tree_sha256"]
    ):
        raise V7RecordError("App rollback v7 full-tree digest is invalid")
    _restore_source(value["restore_source_path"], provenance=str(provenance))
    frontend = value["frontend_provenance"]
    if frontend not in FRONTEND_PROVENANCES or (
        provenance == "captured" and frontend != "verified"
    ):
        raise V7RecordError("App rollback v7 frontend provenance is invalid")
    payload, payload_digest = _restore_payload(
        value, expected_lakebase_instance=expected_lakebase_instance
    )
    if payload is None and (provenance == "captured" or tier == "agentic"):
        raise V7RecordError(f"a {provenance} {tier}-tier record must carry its restore payload")
    predecessor = value["predecessor_deployment_id"]
    if predecessor is not None and (
        not isinstance(predecessor, str)
        or not predecessor.strip()
        or predecessor == value["deployment_id"]
    ):
        raise V7RecordError("App rollback v7 predecessor deployment is invalid")
    purpose, ack = value["ack_purpose"], value["ack_sha256"]
    allowed = ACK_PURPOSES[str(provenance)]
    if allowed:
        ack_valid = (
            purpose in allowed and isinstance(ack, str) and _SHA256.fullmatch(ack) is not None
        )
    else:
        ack_valid = (purpose, ack) == (None, None)
    if not ack_valid:
        raise V7RecordError(f"App rollback v7 {provenance} record names no valid authorising ack")
    normalized = {
        **copy.deepcopy(value),
        "app_resources": app_resources,
        "env_names": names,
        "restore_payload": payload,
        "restore_payload_sha256": payload_digest,
    }
    if tier == "agentic":
        normalized.update(_agentic_fields(value))
    return normalized


def build_v7_record(
    *,
    expected_lakebase_instance: str,
    gateway: Mapping[str, Any] | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """A validated unsigned v7 record from its fields (digests are derived here)."""

    if fields.get("tier") == "core":
        refuse_agentic_resources(fields["app_resources"])
    resources = validated_app_resource_contract(fields["app_resources"])
    acl = canonical_app_acl(fields["app_acl"])
    names = sorted(set(fields["env_names"]))
    payload = fields.get("restore_payload")
    record: dict[str, Any] = {
        **{key: fields.get(key) for key in COMMON_FIELDS},
        "version": V7_RECORD_VERSION,
        "app_resources": resources,
        "app_resources_sha256": app_resource_contract_digest(resources),
        "genie_space_id": _genie_space_of(resources),
        "app_acl": acl,
        "app_acl_sha256": app_acl_digest(acl),
        "env_names": names,
        "env_names_sha256": env_names_digest(names),
        "restore_payload": payload,
        "restore_payload_sha256": None if payload is None else _payload_digest(payload),
    }
    if record["tier"] == "agentic":
        record.update(dict(gateway or {}))
    return validated_v7_record(
        record,
        app_name=str(record["app_name"]),
        expected_lakebase_instance=expected_lakebase_instance,
    )


def unsigned(record: Mapping[str, Any]) -> dict[str, Any]:
    """A verified record without its attestation fields, ready to build its next version."""

    return {
        key: copy.deepcopy(value) for key, value in record.items() if key not in SIGNATURE_FIELDS
    }


def assert_record_tier(record: Mapping[str, Any], *, agentic_tier: str) -> None:
    """A record's tier must equal the profile's (any capture, adoption or restore)."""

    if record.get("tier") != tier_of_profile(agentic_tier):
        raise V7RecordError(
            f"the signed record is {record.get('tier')}-tier but the profile's MIP_AGENTIC_TIER "
            f"is {agentic_tier}; a record's tier must equal the profile's"
        )


def assert_core_resource_proof(
    record: Mapping[str, Any],
    *,
    live: list[dict[str, object]],
    reviewed: list[dict[str, object]] | None = None,
) -> None:
    """The core tier's whole resource proof: exactly the record's (and the profile's) bindings.

    It never consults the Gateway resource proof.
    """

    if record.get("tier") != "core":
        raise V7RecordError("the core resource proof applies to a core-tier record only")
    refuse_agentic_resources(live)
    actual = validated_app_resource_contract(live)
    if actual != record["app_resources"]:
        raise V7RecordError("Databricks App resource bindings drifted from the signed core record")
    if reviewed is not None and validated_app_resource_contract(reviewed) != actual:
        raise V7RecordError(
            "Databricks App resource bindings are not the profile's reviewed bindings"
        )
