"""Construct exact signed Supervisor field mutations for the Databricks CLI."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any


def _text(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


def update_signed_supervisor_field(
    workspace: Any,
    record: Mapping[str, Any],
    supervisor_id: str,
    field: str,
    value: str,
    *,
    read_supervisor: Callable[[Any, str], Mapping[str, Any]],
    assert_exact_candidate: Callable[..., tuple[Mapping[str, Any], str]],
    run_cli: Callable[[Sequence[str]], str],
) -> str:
    """Update one reviewed field while preserving the signed display-name operand."""

    claimed_id = _text(record.get("supervisor_id"))
    if not claimed_id or supervisor_id != claimed_id:
        raise RuntimeError("Supervisor field update immutable claim is inconsistent")
    direct = read_supervisor(workspace, supervisor_id)
    if _text(direct.get("supervisor_agent_id")) != claimed_id:
        raise RuntimeError("Supervisor field update direct identity drifted")
    current_display_name = _text(direct.get("display_name"))
    allowed_display_names = {
        _text(record.get("temporary_name")),
        _text(record.get("target_name")),
    }
    if (
        not current_display_name
        or "" in allowed_display_names
        or current_display_name not in allowed_display_names
    ):
        raise RuntimeError(
            "Supervisor field update display name is outside the signed "
            "temporary/canonical states"
        )
    exact_direct, _endpoint_id = assert_exact_candidate(
        workspace,
        record,
        require_claim=True,
    )
    if _text(exact_direct.get("supervisor_agent_id")) != claimed_id:
        raise RuntimeError("Supervisor field update exact identity drifted")
    current_display_name = _text(exact_direct.get("display_name"))
    if current_display_name not in allowed_display_names:
        raise RuntimeError(
            "Supervisor field update exact display name is outside the signed "
            "temporary/canonical states"
        )
    resource = f"supervisor-agents/{supervisor_id}"
    args: tuple[str, ...]
    if field == "display_name":
        if value != record["target_name"]:
            raise RuntimeError("Supervisor display_name update is outside the signed target")
        args = (
            "supervisor-agents",
            "update-supervisor-agent",
            resource,
            "display_name",
            value,
        )
    elif field == "instructions":
        try:
            canonical_instructions = json.loads(str(record["contract_json"]))["instructions"]
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Supervisor instruction update contract is malformed") from exc
        if not isinstance(canonical_instructions, str) or value != canonical_instructions:
            raise RuntimeError("Supervisor instructions update is outside the signed contract")
        args = (
            "supervisor-agents",
            "update-supervisor-agent",
            resource,
            "instructions",
            current_display_name,
            "--instructions",
            value,
        )
    else:
        raise ValueError("Supervisor field update is unsupported")
    return run_cli(args)
