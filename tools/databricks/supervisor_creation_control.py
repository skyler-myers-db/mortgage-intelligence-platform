#!/usr/bin/env python3
"""Proof-authority phases for signed managed-Supervisor creation."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from databricks.sdk import WorkspaceClient
from tools.databricks import supervisor_creation_journal as journal
from tools.databricks.supervisor_creation_audit import (
    find_supervisor_create_proof,
)
from tools.databricks.supervisor_creation_runtime import (
    assert_unique_target_claim,
    exact_journaled_candidate,
    exact_tool_subset,
    supervisor_rows,
)


def _text(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _claim_live(
    workspace: Any,
    record: dict[str, Any],
    *,
    supervisor_id: str,
    endpoint: str,
    endpoint_id: str,
    creator: str,
    create_time: str,
    proof_kind: str,
    audit_event_id: str = "",
    audit_request_id: str = "",
) -> dict[str, Any]:
    direct, live_endpoint_id = exact_journaled_candidate(
        workspace,
        record,
        require_claim=False,
    )
    live = (
        _text(direct.get("supervisor_agent_id")),
        _text(direct.get("endpoint_name")),
        live_endpoint_id,
        _text(direct.get("creator")),
        _text(direct.get("create_time")),
    )
    expected = (supervisor_id, endpoint, endpoint_id, creator, create_time)
    if live != expected:
        raise RuntimeError("Supervisor creation claim differs from the exact live tuple")
    if exact_tool_subset(
        workspace,
        record,
        supervisor_id=supervisor_id,
    ):
        raise RuntimeError("unclaimed Supervisor already contains tools")
    return journal.claim(
        workspace,
        app_name=record["app_name"],
        lease_id=record["admitted_lease_id"],
        source_git_sha=record["admitted_source_git_sha"],
        runtime_application_id=record["runtime_application_id"],
        supervisor_id=supervisor_id,
        endpoint=endpoint,
        endpoint_id=endpoint_id,
        creator=creator,
        create_time=create_time,
        proof_kind=proof_kind,
        audit_event_id=audit_event_id,
        audit_request_id=audit_request_id,
    )


def claim_from_result(
    workspace: Any,
    record: dict[str, Any],
    result_path: Path,
) -> dict[str, Any]:
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Supervisor creation result is invalid") from exc
    required = {"supervisor_id", "endpoint", "endpoint_id", "creator", "create_time"}
    if (
        not isinstance(result, dict)
        or set(result) != required
        or any(not isinstance(result[key], str) or not result[key].strip() for key in required)
    ):
        raise RuntimeError("Supervisor creation result is incomplete")
    return _claim_live(
        workspace,
        record,
        supervisor_id=result["supervisor_id"],
        endpoint=result["endpoint"],
        endpoint_id=result["endpoint_id"],
        creator=result["creator"],
        create_time=result["create_time"],
        proof_kind="create_response",
    )


def recover_from_audit(
    workspace: Any,
    record: dict[str, Any],
    *,
    warehouse_id: str,
) -> dict[str, Any]:
    proof = find_supervisor_create_proof(
        workspace,
        warehouse_id=warehouse_id,
        workspace_id=record["workspace_id"],
        actor=record["runtime_application_id"],
        authorized_from=datetime.fromisoformat(record["prepared_at"]),
        authorized_until=datetime.fromisoformat(record["create_authorized_until"]),
        marker=journal.marker(record["intent_id"]),
        expected_request={"instructions": record["temporary_instructions"]},
    )
    rows = [
        row
        for row in __import__(
            "tools.databricks.supervisor_creation_runtime",
            fromlist=["supervisor_rows"],
        ).supervisor_rows(workspace)
        if _text(row.get("supervisor_agent_id")) == proof.supervisor_id
    ]
    if len(rows) != 1:
        raise RuntimeError("audited Supervisor immutable ID is absent or ambiguous")
    direct, endpoint_id = exact_journaled_candidate(
        workspace,
        record,
        require_claim=False,
    )
    if _text(direct.get("supervisor_agent_id")) != proof.supervisor_id:
        raise RuntimeError("audited Supervisor differs from the signed temporary candidate")
    return _claim_live(
        workspace,
        record,
        supervisor_id=proof.supervisor_id,
        endpoint=_text(direct.get("endpoint_name")),
        endpoint_id=endpoint_id,
        creator=_text(direct.get("creator")),
        create_time=_text(direct.get("create_time")),
        proof_kind="system_access_audit",
        audit_event_id=proof.event_id,
        audit_request_id=proof.request_id,
    )


def _assert_intent_live_absent(workspace: Any, record: dict[str, Any]) -> None:
    from tools.databricks.supervisor_creation_runtime import (
        supervisor_by_id,
        supervisor_rows,
    )

    for row in supervisor_rows(workspace):
        supervisor_id = _text(row.get("supervisor_agent_id"))
        direct = supervisor_by_id(workspace, supervisor_id)
        if (
            _text(direct.get("display_name")) == record["temporary_name"]
            or _text(direct.get("instructions")) == record["temporary_instructions"]
            or (record.get("supervisor_id") and supervisor_id == record["supervisor_id"])
        ):
            raise RuntimeError("Supervisor creation intent still has a live candidate")


def abandon_settled_absent(
    workspace: Any,
    record: dict[str, Any],
    *,
    warehouse_id: str,
    now: datetime | None = None,
) -> None:
    """Clear only after audit settlement, zero create events, and two absence reads."""

    if record["supervisor_id"]:
        raise RuntimeError("claimed Supervisor creation cannot be abandoned")
    current = now or datetime.now(UTC)
    settlement = datetime.fromisoformat(record["audit_settlement_until"])
    if current < settlement:
        raise RuntimeError("Supervisor create audit settlement remains open")
    _assert_intent_live_absent(workspace, record)
    query_started = now or datetime.now(UTC)
    if query_started < settlement:
        raise RuntimeError("Supervisor create absence query began before settlement")
    try:
        find_supervisor_create_proof(
            workspace,
            warehouse_id=warehouse_id,
            workspace_id=record["workspace_id"],
            actor=record["runtime_application_id"],
            authorized_from=datetime.fromisoformat(record["prepared_at"]),
            authorized_until=datetime.fromisoformat(record["create_authorized_until"]),
            marker=journal.marker(record["intent_id"]),
            expected_request={"instructions": record["temporary_instructions"]},
        )
    except RuntimeError as exc:
        if str(exc) != "Supervisor create audit proof is not available yet":
            raise
    else:
        raise RuntimeError("Supervisor creation intent has an authoritative create event")
    _assert_intent_live_absent(workspace, record)
    journal.clear_absent_intent(
        workspace,
        app_name=record["app_name"],
        lease_id=record["admitted_lease_id"],
        source_git_sha=record["admitted_source_git_sha"],
        runtime_application_id=record["runtime_application_id"],
        expected=record,
        assert_live_absent=lambda: _assert_intent_live_absent(workspace, record),
    )


def verify_complete(workspace: Any, record: dict[str, Any]) -> None:
    """Prove a complete claimed Supervisor without discarding handoff authority."""

    if record.get("disposition", "active") != "active":
        raise RuntimeError("retire-only Supervisor creation cannot complete")
    direct, endpoint_id = exact_journaled_candidate(
        workspace,
        record,
        require_claim=True,
    )
    expected_tools = set(
        json.loads(record["contract_json"])["tools"][index]["tool_id"]
        for index in range(len(json.loads(record["contract_json"])["tools"]))
    )
    actual_tools = exact_tool_subset(
        workspace,
        record,
        supervisor_id=record["supervisor_id"],
    )
    canonical_contract = json.loads(record["contract_json"])
    if (
        _text(direct.get("display_name")) != record["target_name"]
        or direct.get("instructions") != canonical_contract["instructions"]
        or endpoint_id != record["endpoint_id"]
        or actual_tools != expected_tools
    ):
        raise RuntimeError("Supervisor creation full postflight is incomplete")
    assert_unique_target_claim(workspace, record)


def complete_and_clear(workspace: Any, record: dict[str, Any]) -> None:
    verify_complete(workspace, record)
    journal.clear(
        workspace,
        app_name=record["app_name"],
        lease_id=record["admitted_lease_id"],
        source_git_sha=record["admitted_source_git_sha"],
        runtime_application_id=record["runtime_application_id"],
        expected=record,
    )


def plan_and_prepare(
    workspace: Any,
    *,
    app_name: str,
    lease_id: str,
    source_git_sha: str,
    runtime_application_id: str,
    canonical_name: str,
    genie_space_id: str,
    catalog: str,
    proxy_application_id: str,
    approved_query_application_ids: tuple[str, ...],
) -> dict[str, Any]:
    """Select the existing deterministic planner target and journal only creates."""

    from tools.databricks.agentic_supervisor_endpoint import (
        plan_supervisor_agent,
        supervisor_candidates,
    )
    from tools.databricks.provision_agentic_resources import (
        assert_exact_supervisor_contract,
    )

    existing = journal.download(
        workspace,
        app_name=app_name,
        runtime_application_id=runtime_application_id,
    )
    if existing is not None:
        adopted = journal.prepare(
            workspace,
            app_name=app_name,
            lease_id=lease_id,
            source_git_sha=source_git_sha,
            runtime_application_id=runtime_application_id,
            canonical_name=canonical_name,
            target_name=existing["target_name"],
            genie_space_id=genie_space_id,
            catalog=catalog,
        )
        return {
            "action": (
                "resume" if adopted.get("disposition", "active") == "active" else "handoff_required"
            ),
            "target_name": adopted["target_name"],
            "intent_id": adopted["intent_id"],
            "status": "claimed" if adopted["supervisor_id"] else "intent",
        }
    candidates = supervisor_candidates(
        supervisor_rows(workspace),
        display_name=canonical_name,
        genie_space_id=genie_space_id,
        catalog=catalog,
    )
    plan = plan_supervisor_agent(
        workspace,
        candidates,
        app_name=app_name,
        display_name=canonical_name,
        genie_space_id=genie_space_id,
        catalog=catalog,
        runtime_application_id=runtime_application_id,
        managed_query_application_id=proxy_application_id,
        additional_managed_query_application_ids=approved_query_application_ids,
        assert_contract=assert_exact_supervisor_contract,
    )
    if plan.exact_canonical is not None or plan.candidate is not None:
        return {
            "action": "reuse",
            "target_name": plan.target_name,
        }
    record = journal.prepare(
        workspace,
        app_name=app_name,
        lease_id=lease_id,
        source_git_sha=source_git_sha,
        runtime_application_id=runtime_application_id,
        canonical_name=canonical_name,
        target_name=plan.target_name,
        genie_space_id=genie_space_id,
        catalog=catalog,
    )
    return {
        "action": "create",
        "target_name": plan.target_name,
        "intent_id": record["intent_id"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase",
        choices=(
            "adopt",
            "abandon-absent",
            "classify-policy",
            "plan-prepare",
            "claim-result",
            "recover-audit",
            "recover",
            "verify-complete",
            "complete",
            "status",
        ),
    )
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--runtime-application-id", required=True)
    parser.add_argument("--deployment-lease-id", required=True)
    parser.add_argument("--deployment-source-git-sha", required=True)
    parser.add_argument("--canonical-name", default="")
    parser.add_argument("--target-name", default="")
    parser.add_argument("--genie-space-id", default="")
    parser.add_argument("--catalog", default="")
    parser.add_argument("--result-json", type=Path)
    parser.add_argument("--warehouse-id", default="")
    parser.add_argument("--proxy-application-id", default="")
    parser.add_argument("--approved-query-application-id", action="append", default=[])
    parser.add_argument("--out-json", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    workspace = WorkspaceClient()
    if args.phase == "plan-prepare":
        record = plan_and_prepare(
            workspace,
            app_name=args.app_name,
            lease_id=args.deployment_lease_id,
            source_git_sha=args.deployment_source_git_sha,
            runtime_application_id=args.runtime_application_id,
            canonical_name=args.canonical_name,
            genie_space_id=args.genie_space_id,
            catalog=args.catalog,
            proxy_application_id=args.proxy_application_id,
            approved_query_application_ids=tuple(args.approved_query_application_id),
        )
    else:
        loaded = journal.download(
            workspace,
            app_name=args.app_name,
            runtime_application_id=args.runtime_application_id,
        )
        if loaded is None:
            if args.phase == "status":
                if args.out_json is not None:
                    args.out_json.write_text(
                        '{"status":"absent"}\n',
                        encoding="utf-8",
                    )
                else:
                    print("absent")
                return 0
            raise RuntimeError("Supervisor creation control phase has no signed journal")
        record = loaded
        if args.phase == "adopt":
            if not args.canonical_name or not args.genie_space_id or not args.catalog:
                raise ValueError(
                    "journal adoption requires canonical name, Genie space, and catalog"
                )
            record = journal.prepare(
                workspace,
                app_name=args.app_name,
                lease_id=args.deployment_lease_id,
                source_git_sha=args.deployment_source_git_sha,
                runtime_application_id=args.runtime_application_id,
                canonical_name=args.canonical_name,
                target_name=record["target_name"],
                genie_space_id=args.genie_space_id,
                catalog=args.catalog,
            )
        elif args.phase != "status" and (
            record["admitted_lease_id"] != args.deployment_lease_id
            or record["admitted_source_git_sha"] != args.deployment_source_git_sha
        ):
            raise RuntimeError("Supervisor creation journal was not adopted by this deployment")
        if args.phase == "classify-policy":
            if not args.canonical_name or not args.genie_space_id or not args.catalog:
                raise ValueError(
                    "policy classification requires canonical name, Genie space, and catalog"
                )
            policy = (
                "current"
                if journal.matches_current_policy(
                    record,
                    canonical_name=args.canonical_name,
                    genie_space_id=args.genie_space_id,
                    catalog=args.catalog,
                )
                else "historical"
            )
            if args.out_json is not None:
                args.out_json.write_text(
                    json.dumps({"policy": policy}, sort_keys=True, separators=(",", ":")) + "\n",
                    encoding="utf-8",
                )
            else:
                print(policy)
            return 0
        if args.phase == "abandon-absent":
            if not args.warehouse_id:
                raise ValueError("--warehouse-id is required")
            abandon_settled_absent(
                workspace,
                record,
                warehouse_id=args.warehouse_id,
            )
            print("absent")
            return 0
        if args.phase == "claim-result":
            if args.result_json is None:
                raise ValueError("--result-json is required")
            record = claim_from_result(workspace, record, args.result_json)
        elif args.phase in {"recover-audit", "recover"}:
            if not args.warehouse_id:
                raise ValueError("--warehouse-id is required")
            try:
                record = recover_from_audit(
                    workspace,
                    record,
                    warehouse_id=args.warehouse_id,
                )
            except RuntimeError as exc:
                if (
                    args.phase != "recover"
                    or str(exc) != "Supervisor create audit proof is not available yet"
                ):
                    raise
                if datetime.now(UTC) < datetime.fromisoformat(record["audit_settlement_until"]):
                    raise RuntimeError(
                        "Supervisor create audit settlement remains open; retry later"
                    ) from exc
                abandon_settled_absent(
                    workspace,
                    record,
                    warehouse_id=args.warehouse_id,
                )
                print("absent")
                return 0
        elif args.phase == "verify-complete":
            verify_complete(workspace, record)
        elif args.phase == "complete":
            complete_and_clear(workspace, record)
            print("absent")
            return 0
        else:
            print("claimed" if record["supervisor_id"] else "intent")
    if args.out_json is not None:
        if "action" not in record:
            record = {
                **record,
                "status": "claimed" if record.get("supervisor_id") else "intent",
            }
        args.out_json.write_text(
            json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    elif args.phase != "status":
        print("claimed" if record["supervisor_id"] else "intent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
