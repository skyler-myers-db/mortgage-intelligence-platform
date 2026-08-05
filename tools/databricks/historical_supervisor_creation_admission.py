"""Admit a signed pending Supervisor creation during historical reconciliation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from tools.databricks.historical_agent_endpoint_types import ReviewedSupervisor
from tools.databricks.supervisor_creation_journal import (
    download,
    matches_current_policy,
)
from tools.databricks.supervisor_creation_runtime import (
    exact_journaled_candidate,
    exact_tool_subset,
)


def _text(value: object) -> str:
    return str(value or "").strip()


def _field(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def read_pending_creation(
    workspace: Any,
    *,
    app_name: str,
    runtime_application_id: str,
) -> dict[str, Any] | None:
    return download(
        workspace,
        app_name=app_name,
        runtime_application_id=runtime_application_id,
    )


def protected_pending_endpoint(record: Mapping[str, Any] | None) -> str:
    return _text(record.get("endpoint")) if record is not None else ""


def pending_creation_candidate_disposition(
    workspace: Any,
    record: Mapping[str, Any] | None,
    listed: Mapping[str, Any],
    direct: Mapping[str, Any],
    endpoint_id: str,
    runtime_application_id: str,
    *,
    canonical_name: str,
    genie_space_id: str,
    catalog: str,
) -> tuple[str, ReviewedSupervisor | None]:
    """Authenticate and classify one exact signed temporary/final tuple."""

    if record is None or _text(direct.get("display_name")) not in {
        record["temporary_name"],
        record["target_name"],
    }:
        return "unrelated", None
    if _text(direct.get("creator")).casefold() != runtime_application_id.casefold():
        return "unrelated", None
    if not record.get("supervisor_id"):
        raise RuntimeError(
            "unclaimed pending Supervisor creation requires authoritative audit recovery"
        )
    journaled, journaled_endpoint_id = exact_journaled_candidate(
        workspace,
        record,
        require_claim=True,
    )
    if (
        _text(journaled.get("supervisor_agent_id")) != _text(direct.get("supervisor_agent_id"))
        or journaled_endpoint_id != endpoint_id
    ):
        raise RuntimeError("pending Supervisor creation tuple changed during inventory")
    if _text(journaled.get("display_name")) == record["target_name"]:
        contract = json.loads(record["contract_json"])
        expected_tools = {tool["tool_id"] for tool in contract["tools"]}
        if (
            journaled.get("instructions") != contract["instructions"]
            or exact_tool_subset(
                workspace,
                record,
                supervisor_id=_text(journaled.get("supervisor_agent_id")),
            )
            != expected_tools
        ):
            raise RuntimeError("finalized pending Supervisor creation has an incomplete contract")
    if matches_current_policy(
        dict(record),
        canonical_name=canonical_name,
        genie_space_id=genie_space_id,
        catalog=catalog,
    ):
        return "preserve", None
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
        raise RuntimeError("pending Supervisor list/detail identity drifted")
    return (
        "retire",
        ReviewedSupervisor(
            supervisor_id=_text(direct.get("supervisor_agent_id")),
            display_name=_text(direct.get("display_name")),
            endpoint=_text(direct.get("endpoint_name")),
            endpoint_id=endpoint_id,
            creator=_text(direct.get("creator")),
            create_time=_text(direct.get("create_time")),
            contract_json=str(record["contract_json"]),
            contract_sha256=str(record["contract_sha256"]),
            preserved=False,
        ),
    )


def assert_claimed_pending_creation_seen(
    record: Mapping[str, Any] | None,
    *,
    seen: bool,
    pending_cleanup: object | None = None,
) -> None:
    cleanup_matches = (
        record is not None
        and pending_cleanup is not None
        and tuple(
            _text(_field(record, field))
            for field in ("supervisor_id", "endpoint", "endpoint_id", "creator")
        )
        == tuple(
            _text(_field(pending_cleanup, field))
            for field in ("supervisor_id", "endpoint", "endpoint_id", "creator")
        )
    )
    if record is not None and record.get("supervisor_id") and not seen and not cleanup_matches:
        raise RuntimeError("claimed pending Supervisor creation candidate is absent")
