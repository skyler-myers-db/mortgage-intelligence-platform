"""Inspect and migrate App query access without replacing serving ACLs."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Collection, Mapping
from typing import Any, Literal

from backend.agents.gateway_contract import (
    DEFAULT_GATEWAY_ENDPOINT,
    LEGACY_GATEWAY_ENDPOINT,
)
from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from tools.databricks.agent_runtime_access import (
    assert_current_runtime_identity,
    assert_runtime_creator,
)
from tools.databricks.cutover_journal_store import (
    clear_cutover_journal_exact,
    read_cutover_journal,
)
from tools.databricks.cutover_retirement_inventory import (
    assert_journal_endpoint_retired,
    assert_journal_query_groups_retired,
    assert_retained_journal_gateway_exact,
    validated_cutover_endpoint_inventory,
)
from tools.databricks.m2m_access_policy import resolve_effective_groups
from tools.databricks.serving_endpoint_acl import revoke_direct_permissions
from tools.databricks.serving_query_group_access import (
    managed_query_group_external_id,
    managed_query_group_name,
)

AppGatewayAccessMode = Literal["none", "legacy", "managed", "mixed"]
GatewayQueryAccessMode = Literal["none", "direct", "managed", "mixed"]


def app_service_principal_identity(
    workspace: Any,
    *,
    app_name: str,
) -> tuple[str, str]:
    """Resolve both immutable App service-principal identifiers."""

    app = workspace.apps.get(app_name)
    client_id = str(
        getattr(app, "service_principal_client_id", None)
        or (app.get("service_principal_client_id") if isinstance(app, dict) else "")
        or ""
    ).strip()
    scim_id = str(
        getattr(app, "service_principal_id", None)
        or (app.get("service_principal_id") if isinstance(app, dict) else "")
        or ""
    ).strip()
    if not client_id or not scim_id:
        raise RuntimeError(f"both App service-principal identifiers are required for {app_name!r}")
    return client_id, scim_id


def _permission_level(permission: object) -> str:
    value = getattr(permission, "permission_level", None)
    return str(getattr(value, "value", value) or "").strip()


def _direct_levels(entry: object) -> set[str]:
    return {
        _permission_level(permission)
        for permission in (getattr(entry, "all_permissions", None) or [])
        if getattr(permission, "inherited", None) is not True and _permission_level(permission)
    }


def _all_levels(entry: object) -> set[str]:
    return {
        _permission_level(permission)
        for permission in (getattr(entry, "all_permissions", None) or [])
        if _permission_level(permission)
    }


def _exact_acl_entry(
    permissions: object,
    *,
    identity_field: str,
    identity: str,
) -> object | None:
    entries = [
        entry
        for entry in (getattr(permissions, "access_control_list", None) or [])
        if str(getattr(entry, identity_field, "") or "").strip() == identity
    ]
    if len(entries) > 1:
        raise RuntimeError(f"serving endpoint ACL duplicates {identity_field} {identity!r}")
    return entries[0] if entries else None


def _assert_exact_managed_group(
    workspace: Any,
    *,
    endpoint_id: str,
    application_id: str,
    scim_id: str,
    identity_label: str,
) -> bool:
    group_name = managed_query_group_name(
        endpoint_id=endpoint_id,
        application_id=application_id,
    )
    matches = [
        group
        for group in workspace.groups.list(filter=f"displayName eq '{group_name}'")
        if str(getattr(group, "display_name", "") or "").strip() == group_name
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"signed {identity_label} managed query group {group_name!r} is absent or duplicated"
        )
    group_id = str(getattr(matches[0], "id", "") or "").strip()
    if not group_id:
        raise RuntimeError(f"signed {identity_label} managed query group has no immutable ID")
    group = workspace.groups.get(group_id)
    expected = (
        group_id,
        group_name,
        managed_query_group_external_id(
            endpoint_id=endpoint_id,
            application_id=application_id,
        ),
    )
    actual = (
        str(getattr(group, "id", "") or "").strip(),
        str(getattr(group, "display_name", "") or "").strip(),
        str(getattr(group, "external_id", "") or "").strip(),
    )
    if actual != expected:
        raise RuntimeError(f"signed {identity_label} managed query group identity drifted")
    members = {
        str(getattr(member, "value", "") or "").strip()
        for member in (getattr(group, "members", None) or [])
    }
    if "" in members or members.difference({scim_id}):
        raise RuntimeError(
            f"signed {identity_label} managed query group contains an identity "
            "outside its immutable contract"
        )
    return members == {scim_id}


def inspect_gateway_query_access_mode(
    workspace: Any,
    *,
    endpoint_name: str,
    application_id: str,
    scim_id: str,
    identity_label: str,
) -> GatewayQueryAccessMode:
    """Classify one exact identity's direct and managed Gateway query paths."""

    application = application_id.strip()
    principal_id = scim_id.strip()
    label = identity_label.strip()
    if not application or not principal_id or not label:
        raise ValueError("application, SCIM, and identity label are required")
    endpoint = workspace.serving_endpoints.get(endpoint_name)
    endpoint_id = str(getattr(endpoint, "id", "") or "").strip()
    if not endpoint_id:
        raise RuntimeError(f"signed {label} Gateway endpoint has no immutable ID")
    permissions = workspace.serving_endpoints.get_permissions(endpoint_id)
    direct_entry = _exact_acl_entry(
        permissions,
        identity_field="service_principal_name",
        identity=application,
    )
    direct_levels = _direct_levels(direct_entry or object())
    if _all_levels(direct_entry or object()) and (
        direct_levels != {"CAN_QUERY"} or _all_levels(direct_entry or object()) != {"CAN_QUERY"}
    ):
        raise RuntimeError(f"signed {label} direct Gateway access is not exact CAN_QUERY")
    group_name = managed_query_group_name(
        endpoint_id=endpoint_id,
        application_id=application,
    )
    group_entry = _exact_acl_entry(
        permissions,
        identity_field="group_name",
        identity=group_name,
    )
    group_levels = _direct_levels(group_entry or object())
    if _all_levels(group_entry or object()) and (
        group_levels != {"CAN_QUERY"} or _all_levels(group_entry or object()) != {"CAN_QUERY"}
    ):
        raise RuntimeError(f"signed {label} managed Gateway access is not exact CAN_QUERY")
    managed_membership_active = False
    if group_levels:
        managed_membership_active = _assert_exact_managed_group(
            workspace,
            endpoint_id=endpoint_id,
            application_id=application,
            scim_id=principal_id,
            identity_label=label,
        )
    effective_groups = set(resolve_effective_groups(workspace, sp_id=principal_id).values())
    for entry in getattr(permissions, "access_control_list", None) or []:
        effective_group = str(getattr(entry, "group_name", "") or "").strip()
        if (
            effective_group
            and effective_group != group_name
            and effective_group in effective_groups
            and _all_levels(entry)
        ):
            raise RuntimeError(
                f"signed {label} Gateway access includes an unreviewed effective group"
            )
    if direct_levels and group_levels and managed_membership_active:
        return "mixed"
    if direct_levels:
        return "direct"
    if group_levels and managed_membership_active:
        return "managed"
    return "none"


def inspect_app_gateway_access_mode(
    workspace: Any,
    *,
    endpoint_name: str,
    app_client_id: str,
    app_scim_id: str,
) -> AppGatewayAccessMode:
    """Read and classify exact App query access without mutating the ACL."""

    mode = inspect_gateway_query_access_mode(
        workspace,
        endpoint_name=endpoint_name,
        application_id=app_client_id,
        scim_id=app_scim_id,
        identity_label="App",
    )
    return "legacy" if mode == "direct" else mode


CutoverJournalRelation = Literal["absent", "current", "stale"]


def _required_pin(
    value: Mapping[str, object] | None,
    *,
    fields: tuple[str, ...],
    label: str,
) -> dict[str, str] | None:
    if value is None:
        return None
    normalized = {field: str(value.get(field) or "").strip() for field in fields}
    if not all(normalized.values()) or set(value) != set(fields):
        raise RuntimeError(f"{label} immutable pin is incomplete")
    return normalized


def classify_cutover_journal_against_signed_blue(
    *,
    journal_gateway_pin: Mapping[str, object] | None,
    journal_supervisor_pin: Mapping[str, object] | None,
    signed_blue_gateway_pin: Mapping[str, object],
    signed_blue_supervisor_pin: Mapping[str, object],
) -> CutoverJournalRelation:
    """Classify whether a signed journal protects blue or predates it."""

    old_gateway = _required_pin(
        journal_gateway_pin,
        fields=("name", "endpoint_id", "creator"),
        label="journal Gateway",
    )
    old_supervisor = _required_pin(
        journal_supervisor_pin,
        fields=("supervisor_id", "endpoint", "endpoint_id", "creator"),
        label="journal Supervisor",
    )
    blue_gateway = _required_pin(
        signed_blue_gateway_pin,
        fields=("name", "endpoint_id", "creator"),
        label="signed-blue Gateway",
    )
    blue_supervisor = _required_pin(
        signed_blue_supervisor_pin,
        fields=("supervisor_id", "endpoint", "endpoint_id", "creator"),
        label="signed-blue Supervisor",
    )
    assert blue_gateway is not None
    assert blue_supervisor is not None
    if old_gateway is None and old_supervisor is None:
        return "absent"
    current_match = old_gateway == blue_gateway or old_supervisor == blue_supervisor
    if (
        old_supervisor is not None
        and old_supervisor != blue_supervisor
        and old_supervisor["supervisor_id"] == blue_supervisor["supervisor_id"]
    ):
        raise RuntimeError("cutover journal reuses the signed-blue Supervisor immutable ID")

    old_endpoints = [
        ("Gateway", old_gateway["name"], old_gateway["endpoint_id"])
        for old_gateway in [old_gateway]
        if old_gateway is not None
    ] + [
        ("Supervisor", old_supervisor["endpoint"], old_supervisor["endpoint_id"])
        for old_supervisor in [old_supervisor]
        if old_supervisor is not None
    ]
    blue_endpoints = [
        ("Gateway", blue_gateway["name"], blue_gateway["endpoint_id"]),
        ("Supervisor", blue_supervisor["endpoint"], blue_supervisor["endpoint_id"]),
    ]
    for old_kind, old_name, old_id in old_endpoints:
        for blue_kind, blue_name, blue_id in blue_endpoints:
            if old_name != blue_name and old_id != blue_id:
                continue
            same_resource = (
                old_kind == blue_kind
                and (
                    old_gateway == blue_gateway
                    if old_kind == "Gateway"
                    else old_supervisor == blue_supervisor
                )
            )
            if not same_resource:
                raise RuntimeError(
                    f"cutover journal {old_kind} collides with the signed-blue "
                    f"{blue_kind} name or immutable endpoint ID"
                )
    return "current" if current_match else "stale"


def json_pin_from_env(name: str) -> Mapping[str, object] | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{name} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{name} must contain a JSON object")
    return value


def assert_cutover_journal_retired(
    workspace: Any,
    *,
    journal: Mapping[str, str],
    supervisor_by_id: Callable[[str], Mapping[str, Any] | None],
    supervisor_inventory: Callable[[], tuple[Mapping[str, Any], ...]],
    app_application_id: str,
    verifier_application_id: str,
    proxy_application_id: str,
    app_scim_id: str | None = None,
    verifier_scim_id: str | None = None,
) -> None:
    """Prove journal resources retired under their pinned deletion policy."""

    endpoint_inventory = validated_cutover_endpoint_inventory(workspace)
    retained_gateway_live = False
    if journal.get("old_gateway_endpoint"):
        gateway = (
            journal["old_gateway_endpoint"],
            journal["old_gateway_endpoint_id"],
            journal["old_gateway_creator"],
        )
        if journal.get("old_gateway_delete_allowed") == "0":
            retained_gateway_live = assert_retained_journal_gateway_exact(
                workspace,
                endpoint_inventory=endpoint_inventory,
                name=gateway[0],
                endpoint_id=gateway[1],
                creator=gateway[2],
            )
        else:
            assert_journal_endpoint_retired(
                workspace,
                endpoint_inventory=endpoint_inventory,
                name=gateway[0],
                endpoint_id=gateway[1],
                creator=gateway[2],
                label="Gateway",
            )
    if journal.get("old_endpoint"):
        supervisor_endpoint = (
            journal["old_endpoint"],
            journal["old_endpoint_id"],
            journal["old_creator"],
        )
        assert_journal_endpoint_retired(
            workspace,
            endpoint_inventory=endpoint_inventory,
            name=supervisor_endpoint[0],
            endpoint_id=supervisor_endpoint[1],
            creator=supervisor_endpoint[2],
            label="Supervisor",
        )
    supervisors = supervisor_inventory()
    if journal.get("old_id"):
        old_agent_id = journal["old_id"]
        old_endpoint = journal["old_endpoint"]
        old_agent = supervisor_by_id(old_agent_id)
        if old_agent is not None:
            expected = (
                journal["old_id"],
                journal["canonical_name"],
                journal["old_endpoint"],
                journal["old_creator"],
                journal["old_create_time"],
            )
            actual = (
                str(old_agent.get("supervisor_agent_id") or "").strip(),
                str(old_agent.get("display_name") or "").strip(),
                str(old_agent.get("endpoint_name") or "").strip(),
                str(old_agent.get("creator") or "").strip(),
                str(old_agent.get("create_time") or "").strip(),
            )
            if actual == expected:
                raise RuntimeError("cutover journal Supervisor is not retired")
            raise RuntimeError(
                "cutover journal Supervisor immutable ID or endpoint name was reused or drifted"
            )
        collisions = [
            supervisor
            for supervisor in supervisors
            if str(supervisor.get("supervisor_agent_id") or "").strip() == old_agent_id
            or str(supervisor.get("endpoint_name") or "").strip() == old_endpoint
        ]
        if collisions:
            raise RuntimeError(
                "cutover journal Supervisor immutable ID or endpoint name was reused"
            )
    if retained_gateway_live:
        application_scim = str(app_scim_id or "").strip()
        verifier_scim = str(verifier_scim_id or "").strip()
        if not application_scim or not verifier_scim:
            raise RuntimeError(
                "retained Gateway clearance requires exact App and verifier SCIM IDs"
            )
        for label, application_id, scim_id in (
            ("App", app_application_id, application_scim),
            ("verifier", verifier_application_id, verifier_scim),
        ):
            mode = inspect_gateway_query_access_mode(
                workspace,
                endpoint_name=journal["old_gateway_endpoint"],
                application_id=application_id,
                scim_id=scim_id,
                identity_label=label,
            )
            if mode != "none":
                raise RuntimeError(
                    f"cutover journal retained Gateway still authorizes {label} query access"
                )
    assert_journal_query_groups_retired(
        workspace,
        journal=journal,
        app_application_id=app_application_id,
        verifier_application_id=verifier_application_id,
        proxy_application_id=proxy_application_id,
        allow_empty_live_gateway_groups=retained_gateway_live,
    )


def assert_stale_journal_retired_under_signed_blue(
    workspace: Any,
    *,
    runtime_application_id: str,
    journal: Mapping[str, str],
    signed_blue_gateway_pin: Mapping[str, object],
    signed_blue_supervisor_pin: Mapping[str, object],
    supervisor_by_id: Callable[[str], Mapping[str, Any] | None],
    supervisor_inventory: Callable[[], tuple[Mapping[str, Any], ...]],
    app_application_id: str,
    app_scim_id: str,
    verifier_application_id: str,
    verifier_scim_id: str,
    proxy_application_id: str,
) -> None:
    """Re-prove signed blue and journal resource absence before journal deletion."""

    assert_signed_blue_runtime_live(
        workspace,
        runtime_application_id=runtime_application_id,
        signed_blue_gateway_pin=signed_blue_gateway_pin,
        signed_blue_supervisor_pin=signed_blue_supervisor_pin,
        supervisor_by_id=supervisor_by_id,
    )
    supervisor = _required_pin(
        signed_blue_supervisor_pin,
        fields=("supervisor_id", "endpoint", "endpoint_id", "creator"),
        label="signed-blue Supervisor",
    )
    assert supervisor is not None
    blue_agent = supervisor_by_id(supervisor["supervisor_id"])
    assert blue_agent is not None
    blue_inventory_matches = [
        row
        for row in supervisor_inventory()
        if str(row.get("supervisor_agent_id") or "").strip()
        == supervisor["supervisor_id"]
    ]
    if len(blue_inventory_matches) != 1 or blue_inventory_matches[0] != blue_agent:
        raise RuntimeError("signed-blue Supervisor is omitted or drifted in complete inventory")
    assert_cutover_journal_retired(
        workspace,
        journal=journal,
        supervisor_by_id=supervisor_by_id,
        supervisor_inventory=supervisor_inventory,
        app_application_id=app_application_id,
        app_scim_id=app_scim_id,
        verifier_application_id=verifier_application_id,
        verifier_scim_id=verifier_scim_id,
        proxy_application_id=proxy_application_id,
    )


def assert_signed_blue_runtime_live(
    workspace: Any,
    *,
    runtime_application_id: str,
    signed_blue_gateway_pin: Mapping[str, object],
    signed_blue_supervisor_pin: Mapping[str, object],
    supervisor_by_id: Callable[[str], Mapping[str, Any] | None],
) -> None:
    """Prove both immutable signed-blue endpoints and its Supervisor by direct GET."""

    gateway = _required_pin(
        signed_blue_gateway_pin,
        fields=("name", "endpoint_id", "creator"),
        label="signed-blue Gateway",
    )
    supervisor = _required_pin(
        signed_blue_supervisor_pin,
        fields=("supervisor_id", "endpoint", "endpoint_id", "creator"),
        label="signed-blue Supervisor",
    )
    assert gateway is not None
    assert supervisor is not None
    for label, pin in (("Gateway", gateway), ("Supervisor", supervisor)):
        endpoint_name = pin["name"] if label == "Gateway" else pin["endpoint"]
        details = workspace.serving_endpoints.get(endpoint_name)
        actual = (
            str(getattr(details, "id", "") or "").strip(),
            str(getattr(details, "creator", "") or "").strip(),
        )
        if actual != (pin["endpoint_id"], pin["creator"]):
            raise RuntimeError(f"signed-blue {label} endpoint identity drifted")
        assert_runtime_creator(
            pin["creator"],
            application_id=runtime_application_id,
            resource=f"signed-blue {label} endpoint",
        )
    blue_agent = supervisor_by_id(supervisor["supervisor_id"])
    if blue_agent is None or (
        str(blue_agent.get("supervisor_agent_id") or "").strip(),
        str(blue_agent.get("endpoint_name") or "").strip(),
        str(blue_agent.get("creator") or "").strip(),
    ) != (
        supervisor["supervisor_id"],
        supervisor["endpoint"],
        supervisor["creator"],
    ):
        raise RuntimeError("signed-blue Supervisor immutable identity drifted")


def clear_stale_aware_cutover_journal(
    workspace: Any,
    *,
    runtime_application_id: str,
    assert_single_writer: Callable[[], None],
    supervisor_by_id: Callable[[str], Mapping[str, Any] | None],
    supervisor_inventory: Callable[[], tuple[Mapping[str, Any], ...]],
    app_application_id: str,
    app_scim_id: str,
    verifier_application_id: str,
    verifier_scim_id: str,
    proxy_application_id: str,
) -> None:
    """Clear an ordinary journal, or exactly prove a stale journal first."""

    assert_current_runtime_identity(workspace, application_id=runtime_application_id)
    signed_blue_gateway_pin = json_pin_from_env(
        "MIP_CUTOVER_SIGNED_BLUE_GATEWAY_PIN_JSON"
    )
    signed_blue_supervisor_pin = json_pin_from_env(
        "MIP_CUTOVER_SIGNED_BLUE_SUPERVISOR_PIN_JSON"
    )
    if (signed_blue_gateway_pin is None) != (signed_blue_supervisor_pin is None):
        raise RuntimeError("stale-journal clearance requires both signed-blue runtime pins")
    journal = read_cutover_journal(
        workspace,
        runtime_application_id=runtime_application_id,
    )
    if journal is None:
        clear_cutover_journal_exact(
            workspace,
            runtime_application_id=runtime_application_id,
            assert_single_writer=assert_single_writer,
        )
        return
    relation: CutoverJournalRelation | None = None
    if signed_blue_gateway_pin is not None and signed_blue_supervisor_pin is not None:
        relation = classify_cutover_journal_against_signed_blue(
            journal_gateway_pin=(
                {
                    "name": journal["old_gateway_endpoint"],
                    "endpoint_id": journal["old_gateway_endpoint_id"],
                    "creator": journal["old_gateway_creator"],
                }
                if journal.get("old_gateway_endpoint")
                else None
            ),
            journal_supervisor_pin=(
                {
                    "supervisor_id": journal["old_id"],
                    "endpoint": journal["old_endpoint"],
                    "endpoint_id": journal["old_endpoint_id"],
                    "creator": journal["old_creator"],
                }
                if journal.get("old_id")
                else None
            ),
            signed_blue_gateway_pin=signed_blue_gateway_pin,
            signed_blue_supervisor_pin=signed_blue_supervisor_pin,
        )
        if relation != "stale":
            raise RuntimeError("cutover journal still protects the signed-blue runtime")

    def assert_clear_boundary() -> None:
        assert_single_writer()
        immediately_before = read_cutover_journal(
            workspace,
            runtime_application_id=runtime_application_id,
        )
        if immediately_before != journal:
            raise RuntimeError("cutover journal changed before clearance proof")
        if relation == "stale":
            assert signed_blue_gateway_pin is not None
            assert signed_blue_supervisor_pin is not None
            assert_stale_journal_retired_under_signed_blue(
                workspace,
                runtime_application_id=runtime_application_id,
                journal=journal,
                signed_blue_gateway_pin=signed_blue_gateway_pin,
                signed_blue_supervisor_pin=signed_blue_supervisor_pin,
                supervisor_by_id=supervisor_by_id,
                supervisor_inventory=supervisor_inventory,
                app_application_id=app_application_id,
                app_scim_id=app_scim_id,
                verifier_application_id=verifier_application_id,
                verifier_scim_id=verifier_scim_id,
                proxy_application_id=proxy_application_id,
            )
            return
        assert_cutover_journal_retired(
            workspace,
            journal=journal,
            supervisor_by_id=supervisor_by_id,
            supervisor_inventory=supervisor_inventory,
            app_application_id=app_application_id,
            app_scim_id=app_scim_id,
            verifier_application_id=verifier_application_id,
            verifier_scim_id=verifier_scim_id,
            proxy_application_id=proxy_application_id,
        )

    clear_cutover_journal_exact(
        workspace,
        runtime_application_id=runtime_application_id,
        assert_single_writer=assert_clear_boundary,
    )


def assert_pinned_access_retirement_authority(
    workspace: Any,
    *,
    journal: Mapping[str, str] | None,
    canonical_name: str,
    green_gateway_endpoint: str,
    runtime_application_id: str,
    app_client_id: str,
    app_scim_id: str,
    verifier_application_id: str,
    verifier_scim_id: str,
    agent_by_id: Callable[[str], Mapping[str, Any] | None],
    preserve_endpoints: Collection[str] = (),
) -> None:
    """Fail before activation when signed legacy access cannot be retired."""

    verifier_application = verifier_application_id.strip()
    verifier_principal_id = verifier_scim_id.strip()
    if not verifier_application or not verifier_principal_id:
        raise ValueError("verifier application and SCIM IDs are required before activation")
    preserved = {
        endpoint.strip()
        for endpoint in preserve_endpoints
        if endpoint.strip() and endpoint.strip() != green_gateway_endpoint
    }
    if journal is None:
        if preserved:
            raise RuntimeError("preserved App endpoint has no signed cutover retirement journal")
        return
    if journal.get("canonical_name") != canonical_name:
        raise RuntimeError("signed cutover journal canonical name drifted before prepare")
    pinned_endpoints = {
        journal.get("old_gateway_endpoint", ""),
        journal.get("old_endpoint", ""),
    } - {""}
    if not preserved.issubset(pinned_endpoints):
        raise RuntimeError("preserved App endpoint is absent from the signed cutover journal")
    gateway = journal.get("old_gateway_endpoint")
    if gateway and gateway != green_gateway_endpoint:
        expected = (
            journal["old_gateway_endpoint_id"],
            journal["old_gateway_creator"],
        )
        try:
            details = workspace.serving_endpoints.get(gateway)
        except (NotFound, ResourceDoesNotExist):
            details = None
        if details is not None:
            actual = (
                str(getattr(details, "id", "") or "").strip(),
                str(getattr(details, "creator", "") or "").strip(),
            )
            if actual != expected:
                raise RuntimeError("pinned old Gateway identity drifted before prepare")
            mode = inspect_app_gateway_access_mode(
                workspace,
                endpoint_name=gateway,
                app_client_id=app_client_id,
                app_scim_id=app_scim_id,
            )
            if mode in {"legacy", "mixed"} and (
                journal.get("old_gateway_delete_allowed") != "1"
                or expected[1] != runtime_application_id
            ):
                raise RuntimeError(
                    "legacy pinned Gateway cannot be deleted under its creator policy"
                )
            verifier_mode = inspect_gateway_query_access_mode(
                workspace,
                endpoint_name=gateway,
                application_id=verifier_application,
                scim_id=verifier_principal_id,
                identity_label="verifier",
            )
            if verifier_mode in {"direct", "mixed"} and (
                journal.get("old_gateway_delete_allowed") != "1"
                or expected[1] != runtime_application_id
            ):
                raise RuntimeError(
                    "direct pinned Gateway verifier access cannot be atomically retired "
                    "under its creator policy"
                )

    old_id = journal.get("old_id")
    if not old_id:
        return
    # Supervisor deletion is authorized by its signed immutable agent tuple.
    # A standalone outer Gateway has no such agent identity, so its creator
    # policy is separately pinned and enforced above.
    endpoint = journal["old_endpoint"]
    expected_endpoint = (journal["old_endpoint_id"], journal["old_creator"])
    old = agent_by_id(old_id)
    try:
        details = workspace.serving_endpoints.get(endpoint)
    except (NotFound, ResourceDoesNotExist) as exc:
        if old is not None:
            raise RuntimeError(
                "pinned old Supervisor still exists without its endpoint before prepare"
            ) from exc
        return
    actual_endpoint = (
        str(getattr(details, "id", "") or "").strip(),
        str(getattr(details, "creator", "") or "").strip(),
    )
    if actual_endpoint != expected_endpoint:
        raise RuntimeError("pinned old Supervisor endpoint identity drifted before prepare")
    if old is not None:
        actual_agent = (
            str(old.get("display_name") or ""),
            str(old.get("endpoint_name") or ""),
            str(old.get("creator") or ""),
            str(old.get("create_time") or ""),
        )
        expected_agent = (
            canonical_name,
            endpoint,
            journal["old_creator"],
            journal["old_create_time"],
        )
        if actual_agent != expected_agent:
            raise RuntimeError("pinned old Supervisor ownership drifted before prepare")
    inspect_app_gateway_access_mode(
        workspace,
        endpoint_name=endpoint,
        app_client_id=app_client_id,
        app_scim_id=app_scim_id,
    )


def preserve_blue_and_revoke_managed_candidates(
    workspace: Any,
    *,
    blue_endpoint: str,
    app_client_id: str,
    app_scim_id: str,
    candidate_endpoints: Collection[str] = (),
    assert_before_mutation: Callable[[], None],
) -> AppGatewayAccessMode:
    """Preserve blue exactly and remove only exact managed candidate membership."""

    blue_mode = inspect_app_gateway_access_mode(
        workspace,
        endpoint_name=blue_endpoint,
        app_client_id=app_client_id,
        app_scim_id=app_scim_id,
    )
    if blue_mode == "none":
        raise RuntimeError("signed-blue App has no exact query access to its Gateway endpoint")
    candidates = {
        DEFAULT_GATEWAY_ENDPOINT,
        LEGACY_GATEWAY_ENDPOINT,
        *(name.strip() for name in candidate_endpoints if name.strip()),
    }
    list_endpoints = getattr(getattr(workspace, "serving_endpoints", None), "list", None)
    if callable(list_endpoints):
        for item in list_endpoints():
            name = str(
                item.get("name") if isinstance(item, dict) else getattr(item, "name", None) or ""
            ).strip()
            if name in {LEGACY_GATEWAY_ENDPOINT, DEFAULT_GATEWAY_ENDPOINT} or name.startswith(
                f"{DEFAULT_GATEWAY_ENDPOINT}-"
            ):
                candidates.add(name)
    for endpoint in sorted(candidates - {blue_endpoint, ""}):
        try:
            mode = inspect_app_gateway_access_mode(
                workspace,
                endpoint_name=endpoint,
                app_client_id=app_client_id,
                app_scim_id=app_scim_id,
            )
        except (NotFound, ResourceDoesNotExist):
            continue
        if mode in {"legacy", "mixed"}:
            raise RuntimeError(
                f"candidate Gateway {endpoint!r} retains legacy direct App access; "
                "rotate and retire the pinned endpoint instead of replacing its ACL"
            )
        revoke_direct_permissions(
            workspace,
            endpoint_name=endpoint,
            service_principal=app_client_id,
            service_principal_id=app_scim_id,
            missing_ok=True,
            assert_single_writer=assert_before_mutation,
        )
    return blue_mode


def revoke_managed_app_access(
    workspace: Any,
    *,
    endpoint_name: str,
    app_client_id: str,
    app_scim_id: str,
    missing_ok: bool,
    assert_before_mutation: Callable[[], None],
) -> AppGatewayAccessMode:
    """Revoke exact managed membership, preserving any legacy direct access."""

    mode = inspect_app_gateway_access_mode(
        workspace,
        endpoint_name=endpoint_name,
        app_client_id=app_client_id,
        app_scim_id=app_scim_id,
    )
    if mode not in {"legacy", "mixed"}:
        revoke_direct_permissions(
            workspace,
            endpoint_name=endpoint_name,
            service_principal=app_client_id,
            service_principal_id=app_scim_id,
            missing_ok=missing_ok,
            assert_single_writer=assert_before_mutation,
        )
    return mode
