#!/usr/bin/env python3
"""Inventory or retire every attested MIP agent-runtime serving endpoint."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import quote

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backend.agents.gateway_contract import (  # noqa: E402
    DEFAULT_GATEWAY_ENDPOINT,
    LEGACY_GATEWAY_ENDPOINT,
    gateway_exact_resource_digest,
)
from backend.agents.supervisor_contract import (  # noqa: E402
    RUNTIME_REPLACEMENT_PREFIX,
    RUNTIME_REPLACEMENT_SUFFIX,
    canonical_supervisor_contract_json,
)
from databricks.sdk import WorkspaceClient  # noqa: E402
from databricks.sdk.errors import NotFound, ResourceDoesNotExist  # noqa: E402
from tools.databricks import app_deployment_lease  # noqa: E402
from tools.databricks import historical_supervisor_creation_admission as creation  # noqa: E402
from tools.databricks.agent_runtime_access import assert_runtime_creator  # noqa: E402
from tools.databricks.historical_agent_endpoint_cleanup import (  # noqa: E402
    cleanup_runtime_endpoints,
)
from tools.databricks.historical_agent_endpoint_types import (  # noqa: E402
    GatewayPin,
    QueryGroupPrincipals,
    ReviewedGateway,
    ReviewedSupervisor,
    RuntimeEndpointInventory,
    SupervisorCleanupProof,
    SupervisorPin,
)
from tools.databricks.historical_gateway_runtime_attestation import (  # noqa: E402
    assert_retirement_gateway_pin_from_signed_journal,
)
from tools.databricks.historical_gateway_runtime_attestation import (  # noqa: E402
    gateway_family as _gateway_family,
)
from tools.databricks.historical_gateway_runtime_attestation import (  # noqa: E402
    live_gateway_contract as _live_gateway_contract,
)
from tools.databricks.historical_supervisor_cleanup_journal import (  # noqa: E402
    HistoricalSupervisorCleanupJournal,
    validate_pending_cleanup_inventory,
)
from tools.databricks.historical_supervisor_creation_retirement import (  # noqa: E402
    CreationRetirementCleanupJournal,
    cleanup_postflight_is_complete,
    resolved_scim_id,
)
from tools.databricks.historical_supervisor_retirement_attestation import (  # noqa: E402
    attest_historical_supervisor_retirement_predecessor,
)
from tools.databricks.provision_agentic_resources import (  # noqa: E402
    assert_exact_supervisor_contract,
)
from tools.databricks.serving_endpoint_acl import (  # noqa: E402
    is_platform_foundation_endpoint,
)

_HASH = r"[0-9a-f]{12}"


def _text(value: object) -> str:
    return str(value or "").strip()


def _item_name(value: object) -> str:
    if isinstance(value, Mapping):
        return _text(value.get("name"))
    return _text(getattr(value, "name", None))


def _supervisor_family(name: str, canonical_name: str) -> bool:
    replacement = (
        rf"{re.escape(canonical_name)}"
        rf"{re.escape(RUNTIME_REPLACEMENT_PREFIX)}{_HASH}\](?:-mq1)?"
    )
    return (
        name in {canonical_name, f"{canonical_name}{RUNTIME_REPLACEMENT_SUFFIX}"}
        or re.fullmatch(replacement, name) is not None
    )


def _supervisor_rows(client: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    ids: set[str] = set()
    token: str | None = None
    seen_tokens: set[str] = set()
    while True:
        query: dict[str, Any] = {"page_size": 100}
        if token:
            query["page_token"] = token
        payload = client.api_client.do(
            "GET",
            "/api/2.1/supervisor-agents",
            query=query,
        )
        if not isinstance(payload, Mapping):
            raise RuntimeError("Supervisor inventory is malformed")
        page = payload.get("supervisor_agents", [])
        if not isinstance(page, list):
            raise RuntimeError("Supervisor inventory is malformed")
        for raw in page:
            if not isinstance(raw, Mapping):
                raise RuntimeError("Supervisor inventory is malformed")
            row = {str(key): value for key, value in raw.items()}
            supervisor_id = _text(row.get("supervisor_agent_id"))
            if not supervisor_id or supervisor_id in ids:
                raise RuntimeError("Supervisor inventory has a duplicate or missing identity")
            ids.add(supervisor_id)
            rows.append(row)
        next_token = payload.get("next_page_token")
        if next_token in {None, ""}:
            return rows
        if not isinstance(next_token, str) or not next_token.strip():
            raise RuntimeError("Supervisor inventory page token is malformed")
        token = next_token.strip()
        if token in seen_tokens:
            raise RuntimeError("Supervisor inventory pagination cycled")
        seen_tokens.add(token)


def _supervisor_by_id(client: Any, supervisor_id: str) -> dict[str, Any] | None:
    try:
        payload = client.api_client.do(
            "GET",
            f"/api/2.1/supervisor-agents/{quote(supervisor_id, safe='')}",
        )
    except (NotFound, ResourceDoesNotExist):
        return None
    if not isinstance(payload, Mapping):
        raise RuntimeError("Supervisor metadata is malformed")
    return {str(key): value for key, value in payload.items()}


def _supervisor_pin(
    row: Mapping[str, Any],
    *,
    endpoint_id: str,
) -> SupervisorPin:
    return SupervisorPin(
        supervisor_id=_text(row.get("supervisor_agent_id")),
        endpoint=_text(row.get("endpoint_name")),
        endpoint_id=endpoint_id,
        creator=_text(row.get("creator")),
    )


def _assert_complete_gateway_pin(pin: GatewayPin) -> None:
    if not all(asdict(pin).values()):
        raise RuntimeError("Gateway endpoint has an incomplete immutable identity")


def _assert_complete_supervisor_pin(pin: SupervisorPin) -> None:
    if not all(asdict(pin).values()):
        raise RuntimeError("managed Supervisor has an incomplete immutable identity")


def _validate_pin_sets(
    actual_gateways: set[GatewayPin],
    actual_supervisors: set[SupervisorPin],
    *,
    gateway_pins: Sequence[GatewayPin],
    supervisor_pins: Sequence[SupervisorPin],
    observed_gateways: Sequence[GatewayPin],
    observed_supervisors: Sequence[SupervisorPin],
) -> None:
    for gateway_identity in (*gateway_pins, *observed_gateways):
        _assert_complete_gateway_pin(gateway_identity)
    for supervisor_identity in (*supervisor_pins, *observed_supervisors):
        _assert_complete_supervisor_pin(supervisor_identity)
    if len(set(gateway_pins)) != len(gateway_pins):
        raise ValueError("preserved Gateway tuples contain duplicates")
    if len(set(supervisor_pins)) != len(supervisor_pins):
        raise ValueError("preserved Supervisor tuples contain duplicates")
    for gateway_pin in gateway_pins:
        gateway_conflicts = [
            item
            for item in observed_gateways
            if item.name == gateway_pin.name or item.endpoint_id == gateway_pin.endpoint_id
        ]
        if gateway_conflicts and (
            len(gateway_conflicts) != 1 or gateway_conflicts[0] != gateway_pin
        ):
            raise RuntimeError(f"preserved Gateway tuple {gateway_pin.name!r} drifted")
    for supervisor_pin in supervisor_pins:
        supervisor_conflicts = [
            item
            for item in observed_supervisors
            if (
                item.supervisor_id == supervisor_pin.supervisor_id
                or item.endpoint == supervisor_pin.endpoint
                or item.endpoint_id == supervisor_pin.endpoint_id
            )
        ]
        if supervisor_conflicts and (
            len(supervisor_conflicts) != 1 or supervisor_conflicts[0] != supervisor_pin
        ):
            raise RuntimeError(
                f"preserved Supervisor tuple {supervisor_pin.supervisor_id!r} drifted"
            )
    # Missing blue resources are valid after a partial retirement. A pin whose
    # immutable name/ID is reused, however, is never silently treated as absent.
    if len({item.name for item in observed_gateways}) != len(observed_gateways) or len(
        {item.endpoint_id for item in observed_gateways}
    ) != len(observed_gateways):
        raise RuntimeError("multiple Gateway endpoints share a governed name or immutable ID")
    if (
        len({item.supervisor_id for item in observed_supervisors}) != len(observed_supervisors)
        or len({item.endpoint for item in observed_supervisors}) != len(observed_supervisors)
        or len({item.endpoint_id for item in observed_supervisors}) != len(observed_supervisors)
    ):
        raise RuntimeError(
            "multiple Supervisor agents share an immutable agent or endpoint identity"
        )
    observed_gateway_set = set(observed_gateways)
    observed_supervisor_set = set(observed_supervisors)
    if (set(gateway_pins) & observed_gateway_set) - actual_gateways:
        raise RuntimeError("preserved Gateway remains live but was not attested")
    if (set(supervisor_pins) & observed_supervisor_set) - actual_supervisors:
        raise RuntimeError("preserved Supervisor remains live but was not attested")
    if not actual_gateways.issubset(observed_gateway_set) or not actual_supervisors.issubset(
        observed_supervisor_set
    ):
        raise RuntimeError("reviewed runtime endpoint inventory is internally inconsistent")


def _assert_preserved_gateway_dependencies(
    gateways: Sequence[ReviewedGateway],
    supervisors: Sequence[ReviewedSupervisor],
) -> None:
    preserved_supervisors = {
        supervisor.supervisor_id for supervisor in supervisors if supervisor.preserved
    }
    stranded = {
        gateway.name: gateway.supervisor_id
        for gateway in gateways
        if gateway.preserved and gateway.supervisor_id not in preserved_supervisors
    }
    if stranded:
        dependencies = ", ".join(
            f"{gateway!r} -> {supervisor!r}" for gateway, supervisor in sorted(stranded.items())
        )
        raise RuntimeError(
            "preserved Gateway requires its signed upstream Supervisor to be preserved: "
            + dependencies
        )


def inventory_runtime_endpoints(
    client: Any,
    *,
    runtime_application_id: str,
    gateway_prefixes: Sequence[str],
    supervisor_name: str,
    catalog: str,
    genie_space_id: str,
    gateway_pins: Sequence[GatewayPin] = (),
    retirement_gateway_pins: Sequence[GatewayPin] = (),
    supervisor_pins: Sequence[SupervisorPin] = (),
    retirement_supervisor_pins: Sequence[SupervisorPin] = (),
    pending_supervisor_cleanup: SupervisorCleanupProof | None = None,
    pending_supervisor_creation: dict[str, Any] | None = None,
    assert_single_writer: Callable[[], None],
    assert_supervisor_contract: Callable[..., None] = assert_exact_supervisor_contract,
) -> RuntimeEndpointInventory:
    """Return the complete set only after every governed candidate is attested."""

    required = {
        "runtime application ID": runtime_application_id,
        "Supervisor name": supervisor_name,
        "catalog": catalog,
        "Genie Space ID": genie_space_id,
    }
    missing = [label for label, value in required.items() if not value.strip()]
    if missing:
        raise ValueError("historical endpoint inventory requires " + ", ".join(missing))
    normalized_prefixes = tuple(prefix.strip() for prefix in gateway_prefixes if prefix.strip())
    if not normalized_prefixes or len(set(normalized_prefixes)) != len(normalized_prefixes):
        raise ValueError("historical endpoint inventory requires distinct Gateway prefixes")
    if set(supervisor_pins) & set(retirement_supervisor_pins):
        raise ValueError("active and retirement-only Supervisor preservation pins must be disjoint")
    if set(gateway_pins) & set(retirement_gateway_pins):
        raise ValueError("active and retirement-only Gateway preservation pins must be disjoint")
    if len(retirement_gateway_pins) > 1:
        raise ValueError("retirement-only Gateway preservation requires one signed journal tuple")
    if len(retirement_supervisor_pins) > 1:
        raise ValueError(
            "retirement-only Supervisor preservation requires one signed journal tuple"
        )
    all_supervisor_pins = (*supervisor_pins, *retirement_supervisor_pins)
    all_gateway_pins = (*gateway_pins, *retirement_gateway_pins)
    retirement_gateway_names = {pin.name for pin in retirement_gateway_pins}
    gateway_contracts: dict[str, dict[str, str]] = {}
    gateway_details: dict[str, Any] = {}
    observed_gateway_pins: list[GatewayPin] = []
    listed_endpoint_names = [_item_name(item) for item in client.serving_endpoints.list()]
    if any(not name for name in listed_endpoint_names) or len(listed_endpoint_names) != len(
        set(listed_endpoint_names)
    ):
        raise RuntimeError("serving endpoint inventory has a duplicate or missing name")
    listed_endpoint_names.sort()
    endpoint_details: dict[str, Any] = {}
    foundation_endpoint_names: set[str] = set()
    for name in listed_endpoint_names:
        details = client.serving_endpoints.get(name)
        endpoint_details[name] = details
        if is_platform_foundation_endpoint(details):
            foundation_endpoint_names.add(name)
            continue
        observed_pin = GatewayPin(
            name=name,
            endpoint_id=_text(getattr(details, "id", None)),
            creator=_text(getattr(details, "creator", None)),
        )
        _assert_complete_gateway_pin(observed_pin)
        observed_gateway_pins.append(observed_pin)
    protected_endpoint_names = {
        *(pin.name for pin in all_gateway_pins),
        *(pin.endpoint for pin in all_supervisor_pins),
    }
    if pending_supervisor_cleanup is not None:
        protected_endpoint_names.add(pending_supervisor_cleanup.endpoint)
    pending_endpoint = creation.protected_pending_endpoint(pending_supervisor_creation)
    if pending_endpoint:
        protected_endpoint_names.add(pending_endpoint)
    foundation_collisions = foundation_endpoint_names & protected_endpoint_names
    if foundation_collisions:
        raise RuntimeError(
            "platform foundation endpoint collides with a preserved or pending "
            "runtime tuple: " + ", ".join(sorted(foundation_collisions))
        )
    names = [
        name
        for name in listed_endpoint_names
        if name not in foundation_endpoint_names
        and any(_gateway_family(name, prefix) for prefix in normalized_prefixes)
    ]
    for name in names:
        details = endpoint_details[name]
        observed_pin = GatewayPin(
            name=name,
            endpoint_id=_text(getattr(details, "id", None)),
            creator=_text(getattr(details, "creator", None)),
        )
        if observed_pin.creator != runtime_application_id:
            continue
        gateway_details[name] = details
        gateway_contracts[name] = _live_gateway_contract(
            client,
            details,
            name=name,
            gateway_prefixes=normalized_prefixes,
            runtime_application_id=runtime_application_id,
            supervisor_name=supervisor_name,
            catalog=catalog,
            genie_space_id=genie_space_id,
            assert_single_writer=assert_single_writer,
        )
        if observed_pin in retirement_gateway_pins:
            assert_retirement_gateway_pin_from_signed_journal(
                client,
                pin=observed_pin,
                runtime_application_id=runtime_application_id,
                canonical_name=supervisor_name,
            )

    signed_supervisors: dict[str, dict[str, str]] = {}
    signed_supervisor_gateways: dict[str, set[str]] = {}
    for gateway_name, contract in gateway_contracts.items():
        supervisor_id = contract["supervisor_id"]
        signed_supervisor_gateways.setdefault(supervisor_id, set()).add(gateway_name)
        existing = signed_supervisors.get(supervisor_id)
        signed_fields = {
            key: contract[key]
            for key in (
                "supervisor_id",
                "supervisor_canonical_name",
                "supervisor_endpoint",
                "supervisor_endpoint_id",
                "supervisor_creator",
                "supervisor_endpoint_creator",
                "supervisor_contract_json",
                "supervisor_contract_sha256",
            )
        }
        if existing is not None and existing != signed_fields:
            raise RuntimeError(f"signed Gateway proofs disagree about Supervisor {supervisor_id!r}")
        signed_supervisors[supervisor_id] = signed_fields

    supervisor_rows = _supervisor_rows(client)
    reviewed_supervisors: list[ReviewedSupervisor] = []
    actual_supervisor_pins: set[SupervisorPin] = set()
    observed_supervisor_pins: list[SupervisorPin] = []
    creation_candidate_seen = False
    for listed in supervisor_rows:
        supervisor_id = _text(listed.get("supervisor_agent_id"))
        direct = _supervisor_by_id(client, supervisor_id)
        if direct is None:
            raise RuntimeError("listed managed Supervisor disappeared during inventory")
        endpoint = _text(direct.get("endpoint_name"))
        creator = _text(direct.get("creator"))
        try:
            details = client.serving_endpoints.get(endpoint)
        except (NotFound, ResourceDoesNotExist):
            proof = pending_supervisor_cleanup
            if proof is None or (
                _text(direct.get("supervisor_agent_id")),
                endpoint,
                creator,
            ) != (proof.supervisor_id, proof.endpoint, proof.creator):
                raise RuntimeError(
                    "managed Supervisor endpoint disappeared without an exact cleanup proof"
                ) from None
            observed_supervisor_pins.append(
                SupervisorPin(
                    proof.supervisor_id,
                    proof.endpoint,
                    proof.endpoint_id,
                    proof.creator,
                )
            )
            continue
        pin = _supervisor_pin(
            direct,
            endpoint_id=_text(getattr(details, "id", None)),
        )
        _assert_complete_supervisor_pin(pin)
        observed_supervisor_pins.append(pin)
        creation_disposition, historical_creation = creation.pending_creation_candidate_disposition(
            client,
            pending_supervisor_creation,
            listed,
            direct,
            pin.endpoint_id,
            runtime_application_id,
            canonical_name=supervisor_name,
            genie_space_id=genie_space_id,
            catalog=catalog,
        )
        if creation_disposition != "unrelated":
            creation_candidate_seen = True
            if creation_disposition == "preserve":
                continue
            if pin in all_supervisor_pins or supervisor_id in signed_supervisors:
                raise RuntimeError("revoked pending Supervisor creation collides with signed blue")
            if historical_creation is None:
                raise RuntimeError("revoked pending Supervisor classification is incomplete")
            actual_supervisor_pins.add(pin)
            reviewed_supervisors.append(historical_creation)
            continue
        if not _supervisor_family(_text(direct.get("display_name")), supervisor_name):
            continue
        if creator != runtime_application_id:
            continue
        if any(
            _text(listed.get(field)) != _text(direct.get(field))
            for field in (
                "supervisor_agent_id",
                "display_name",
                "endpoint_name",
                "creator",
                "create_time",
            )
        ):
            raise RuntimeError("managed Supervisor list/detail identity drifted")
        assert_runtime_creator(
            pin.creator,
            application_id=runtime_application_id,
            resource=f"historical Supervisor agent {pin.supervisor_id}",
        )
        assert_runtime_creator(
            getattr(details, "creator", None),
            application_id=runtime_application_id,
            resource=f"historical Supervisor endpoint {pin.endpoint}",
        )
        if pin in retirement_supervisor_pins:
            active_dependencies = signed_supervisor_gateways.get(
                supervisor_id,
                set(),
            ) & {item.name for item in gateway_pins}
            if active_dependencies:
                raise RuntimeError(
                    "retirement-only Supervisor is still referenced by an active "
                    "preserved Gateway"
                )
            historical_json, historical_sha256 = (
                attest_historical_supervisor_retirement_predecessor(
                    client,
                    direct=direct,
                    endpoint_details=details,
                    pin=pin,
                    canonical_name=supervisor_name,
                    genie_space_id=genie_space_id,
                    catalog=catalog,
                    runtime_application_id=runtime_application_id,
                )
            )
            actual_supervisor_pins.add(pin)
            reviewed_supervisors.append(
                ReviewedSupervisor(
                    **asdict(pin),
                    display_name=_text(direct.get("display_name")),
                    create_time=_text(direct.get("create_time")),
                    contract_json=historical_json,
                    contract_sha256=historical_sha256,
                    preserved=True,
                )
            )
            continue
        signed = signed_supervisors.get(supervisor_id)
        if signed is None:
            expected_json = canonical_supervisor_contract_json(
                genie_space_id=genie_space_id,
                catalog=catalog,
            )
        else:
            expected_json = signed["supervisor_contract_json"]
            signed_pin = (
                signed["supervisor_id"],
                signed["supervisor_endpoint"],
                signed["supervisor_endpoint_id"],
                signed["supervisor_creator"],
                signed["supervisor_endpoint_creator"],
            )
            if signed_pin != (
                pin.supervisor_id,
                pin.endpoint,
                pin.endpoint_id,
                pin.creator,
                _text(getattr(details, "creator", None)),
            ):
                raise RuntimeError("signed Gateway upstream Supervisor tuple drifted")
            signed_display_names = {
                contract["supervisor_display_name"]
                for contract in gateway_contracts.values()
                if contract["supervisor_id"] == supervisor_id
            }
            if _text(direct.get("display_name")) not in {
                signed["supervisor_canonical_name"],
                *signed_display_names,
            }:
                raise RuntimeError("signed Gateway upstream Supervisor display name drifted")
            if any(
                contract["supervisor_canonical_name"] != supervisor_name
                for contract in gateway_contracts.values()
                if contract["supervisor_id"] == supervisor_id
            ):
                raise RuntimeError("signed Gateway Supervisor canonical name drifted")
        try:
            expected_contract = json.loads(expected_json)
        except json.JSONDecodeError as exc:
            raise RuntimeError("signed Supervisor contract is invalid") from exc
        if not isinstance(expected_contract, dict):
            raise RuntimeError("signed Supervisor contract is invalid")
        contract_sha256 = hashlib.sha256(expected_json.encode("utf-8")).hexdigest()
        if signed is not None and contract_sha256 != signed["supervisor_contract_sha256"]:
            raise RuntimeError("signed Supervisor contract digest drifted")
        assert_supervisor_contract(
            supervisor_id,
            genie_space_id=genie_space_id,
            catalog=catalog,
            expected_contract=expected_contract,
        )
        actual_supervisor_pins.add(pin)
        reviewed_supervisors.append(
            ReviewedSupervisor(
                **asdict(pin),
                display_name=_text(direct.get("display_name")),
                create_time=_text(direct.get("create_time")),
                contract_json=expected_json,
                contract_sha256=contract_sha256,
                preserved=(
                    pin in all_supervisor_pins
                    or bool(
                        signed_supervisor_gateways.get(supervisor_id, set())
                        & retirement_gateway_names
                    )
                ),
            )
        )
    missing_signed = set(signed_supervisors) - {item.supervisor_id for item in reviewed_supervisors}
    if missing_signed:
        raise RuntimeError(
            "signed Gateway references missing governed Supervisor agents: "
            + ", ".join(sorted(missing_signed))
        )
    creation.assert_claimed_pending_creation_seen(
        pending_supervisor_creation,
        seen=creation_candidate_seen,
        pending_cleanup=pending_supervisor_cleanup,
    )

    actual_gateway_pins = {
        GatewayPin(
            name=name,
            endpoint_id=_text(getattr(details, "id", None)),
            creator=_text(getattr(details, "creator", None)),
        )
        for name, details in gateway_details.items()
    }
    if pending_supervisor_cleanup is not None:
        validate_pending_cleanup_inventory(
            pending_supervisor_cleanup,
            runtime_application_id=runtime_application_id,
            supervisor_pins=all_supervisor_pins,
            observed_supervisors=observed_supervisor_pins,
            endpoint_details=endpoint_details,
        )
    _validate_pin_sets(
        actual_gateway_pins,
        actual_supervisor_pins,
        gateway_pins=all_gateway_pins,
        supervisor_pins=all_supervisor_pins,
        observed_gateways=observed_gateway_pins,
        observed_supervisors=observed_supervisor_pins,
    )
    reviewed_gateways = tuple(
        ReviewedGateway(
            name=pin.name,
            endpoint_id=pin.endpoint_id,
            creator=pin.creator,
            supervisor_id=gateway_contracts[pin.name]["supervisor_id"],
            supervisor_endpoint=gateway_contracts[pin.name]["supervisor_endpoint"],
            supervisor_endpoint_id=gateway_contracts[pin.name]["supervisor_endpoint_id"],
            contract_digest=gateway_exact_resource_digest(gateway_contracts[pin.name]),
            preserved=pin in all_gateway_pins,
        )
        for pin in sorted(actual_gateway_pins)
    )
    _assert_preserved_gateway_dependencies(
        reviewed_gateways,
        reviewed_supervisors,
    )
    return RuntimeEndpointInventory(
        version=1,
        runtime_application_id=runtime_application_id,
        gateways=reviewed_gateways,
        supervisors=tuple(sorted(reviewed_supervisors, key=lambda item: item.supervisor_id)),
        pending_supervisor_cleanup=pending_supervisor_cleanup,
        pending_supervisor_creation=pending_supervisor_creation,
    )


def _json_pin(value: str, cls: type[GatewayPin] | type[SupervisorPin]) -> Any:
    try:
        raw = json.loads(value)
        if not isinstance(raw, dict):
            raise ValueError
        pin = cls(**raw)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(f"{cls.__name__} must be an exact JSON object") from exc
    if any(
        not isinstance(item, str) or not item or item != item.strip()
        for item in asdict(pin).values()
    ):
        raise argparse.ArgumentTypeError(f"{cls.__name__} fields must be non-empty strings")
    return pin


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inventory", "cleanup"))
    parser.add_argument("--runtime-application-id", required=True)
    parser.add_argument("--gateway-prefix", action="append", default=[])
    parser.add_argument("--supervisor-name", default="Mortgage Growth Agent")
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--genie-space-id", required=True)
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--rollback-scope", required=True)
    parser.add_argument("--deployment-lease-id", required=True)
    parser.add_argument("--deployment-source-git-sha", required=True)
    parser.add_argument(
        "--preserve-gateway-json",
        action="append",
        default=[],
        type=lambda value: _json_pin(value, GatewayPin),
    )
    parser.add_argument(
        "--preserve-retirement-gateway-json",
        action="append",
        default=[],
        type=lambda value: _json_pin(value, GatewayPin),
    )
    parser.add_argument(
        "--preserve-supervisor-json",
        action="append",
        default=[],
        type=lambda value: _json_pin(value, SupervisorPin),
    )
    parser.add_argument(
        "--preserve-retirement-supervisor-json",
        action="append",
        default=[],
        type=lambda value: _json_pin(value, SupervisorPin),
    )
    parser.add_argument("--timeout-s", type=int, default=900)
    for flag in (
        "--app-application-id",
        "--app-scim-id",
        "--verifier-application-id",
        "--verifier-scim-id",
        "--proxy-application-id",
        "--proxy-scim-id",
    ):
        parser.add_argument(flag, default="")
    parser.add_argument("--out-json", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client = WorkspaceClient()
    lease_check = app_deployment_lease.held_assertion(
        client,
        app_name=args.app_name,
        lease_id=args.deployment_lease_id,
        source_git_sha=args.deployment_source_git_sha,
    )
    lease_check()
    cleanup_journal = CreationRetirementCleanupJournal(
        client,
        HistoricalSupervisorCleanupJournal(
            client,
            app_name=args.app_name,
            scope=args.rollback_scope,
            lease_id=args.deployment_lease_id,
            source_git_sha=args.deployment_source_git_sha,
            runtime_application_id=args.runtime_application_id,
            assert_single_writer=lease_check,
        ),
        app_name=args.app_name,
        lease_id=args.deployment_lease_id,
        source_git_sha=args.deployment_source_git_sha,
        runtime_application_id=args.runtime_application_id,
        canonical_name=args.supervisor_name,
        genie_space_id=args.genie_space_id,
        catalog=args.catalog,
    )

    def read(
        gateway_pins: Sequence[GatewayPin],
        supervisor_pins: Sequence[SupervisorPin],
    ) -> RuntimeEndpointInventory:
        lease_check()
        return inventory_runtime_endpoints(
            client,
            runtime_application_id=args.runtime_application_id,
            gateway_prefixes=tuple(args.gateway_prefix)
            or (DEFAULT_GATEWAY_ENDPOINT, LEGACY_GATEWAY_ENDPOINT),
            supervisor_name=args.supervisor_name,
            catalog=args.catalog,
            genie_space_id=args.genie_space_id,
            gateway_pins=gateway_pins,
            retirement_gateway_pins=tuple(args.preserve_retirement_gateway_json),
            supervisor_pins=supervisor_pins,
            retirement_supervisor_pins=tuple(args.preserve_retirement_supervisor_json),
            pending_supervisor_cleanup=cleanup_journal.read(),
            pending_supervisor_creation=creation.read_pending_creation(
                client,
                app_name=args.app_name,
                runtime_application_id=args.runtime_application_id,
            ),
            assert_single_writer=lease_check,
        )

    gateway_pins = tuple(args.preserve_gateway_json)
    supervisor_pins = tuple(args.preserve_supervisor_json)
    inventory = read(gateway_pins, supervisor_pins)
    if args.command == "cleanup":
        inventory = cleanup_runtime_endpoints(
            client,
            inventory,
            assert_single_writer=lease_check,
            query_principals=QueryGroupPrincipals(
                app_application_id=args.app_application_id,
                app_scim_id=resolved_scim_id(
                    client,
                    application_id=args.app_application_id,
                    expected_scim_id=args.app_scim_id,
                ),
                verifier_application_id=args.verifier_application_id,
                verifier_scim_id=resolved_scim_id(
                    client,
                    application_id=args.verifier_application_id,
                    expected_scim_id=args.verifier_scim_id,
                ),
                proxy_application_id=args.proxy_application_id,
                proxy_scim_id=resolved_scim_id(
                    client,
                    application_id=args.proxy_application_id,
                    expected_scim_id=args.proxy_scim_id,
                ),
            ),
            timeout_s=args.timeout_s,
            inventory_again=lambda: read(gateway_pins, supervisor_pins),
            cleanup_journal=cleanup_journal,
        )
        if not cleanup_postflight_is_complete(inventory):
            raise RuntimeError("historical runtime cleanup left an unpreserved resource live")
    args.out_json.write_text(
        json.dumps(inventory.document(), sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
