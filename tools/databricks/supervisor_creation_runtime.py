"""Runtime-only create and convergence phases for a signed Supervisor intent."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from databricks.sdk import WorkspaceClient
from tools.databricks import app_deployment_lease
from tools.databricks.agent_runtime_access import (
    assert_current_runtime_identity,
    assert_runtime_creator,
)
from tools.databricks.supervisor_creation_journal import (
    base_create_payload,
    download,
    matches_current_policy,
)


def _text(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _field(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def supervisor_rows(workspace: Any) -> list[dict[str, Any]]:
    """Return complete, duplicate-free Supervisor inventory."""

    rows: list[dict[str, Any]] = []
    identities: set[str] = set()
    token = ""
    seen_tokens: set[str] = set()
    while True:
        query: dict[str, Any] = {"page_size": 100}
        if token:
            query["page_token"] = token
        payload = workspace.api_client.do(
            "GET",
            "/api/2.1/supervisor-agents",
            query=query,
        )
        if not isinstance(payload, Mapping):
            raise RuntimeError("Supervisor creation inventory is malformed")
        page = payload.get("supervisor_agents", [])
        if not isinstance(page, list):
            raise RuntimeError("Supervisor creation inventory is malformed")
        for raw in page:
            if not isinstance(raw, Mapping):
                raise RuntimeError("Supervisor creation inventory is malformed")
            row = {str(key): value for key, value in raw.items()}
            supervisor_id = _text(row.get("supervisor_agent_id"))
            if not supervisor_id or supervisor_id in identities:
                raise RuntimeError(
                    "Supervisor creation inventory has duplicate or missing identities"
                )
            identities.add(supervisor_id)
            rows.append(row)
        next_token = payload.get("next_page_token")
        if next_token in {None, ""}:
            return rows
        if not isinstance(next_token, str) or not next_token.strip():
            raise RuntimeError("Supervisor creation inventory page token is malformed")
        token = next_token.strip()
        if token in seen_tokens:
            raise RuntimeError("Supervisor creation inventory pagination cycled")
        seen_tokens.add(token)


def supervisor_by_id(workspace: Any, supervisor_id: str) -> dict[str, Any]:
    payload = workspace.api_client.do(
        "GET",
        f"/api/2.1/supervisor-agents/{quote(supervisor_id, safe='')}",
    )
    if not isinstance(payload, Mapping):
        raise RuntimeError("Supervisor creation detail is malformed")
    return {str(key): value for key, value in payload.items()}


def assert_unique_target_claim(
    workspace: Any,
    record: Mapping[str, Any],
) -> dict[str, Any]:
    """Prove the complete inventory binds the target name to the signed claim."""

    supervisor_id = _text(record.get("supervisor_id"))
    if not supervisor_id:
        raise RuntimeError("Supervisor creation journal has no immutable claim")
    matches = [
        row
        for row in supervisor_rows(workspace)
        if _text(row.get("display_name")) == record["target_name"]
    ]
    if len(matches) != 1 or _text(matches[0].get("supervisor_agent_id")) != supervisor_id:
        raise RuntimeError(
            "Supervisor deterministic target name is absent, duplicated, or bound "
            "to another immutable ID"
        )
    direct = supervisor_by_id(workspace, supervisor_id)
    if (
        _text(direct.get("supervisor_agent_id")) != supervisor_id
        or _text(direct.get("display_name")) != record["target_name"]
    ):
        raise RuntimeError("Supervisor deterministic target name direct readback is inconsistent")
    return direct


def assert_unique_live_supervisor_binding(
    workspace: Any,
    *,
    supervisor_id: str,
    display_name: str,
    endpoint: str,
    runtime_application_id: str,
) -> str:
    """Bind a public Supervisor name to one exact live resource tuple."""

    expected = (
        supervisor_id.strip(),
        display_name.strip(),
        endpoint.strip(),
        runtime_application_id.strip(),
    )
    if any(not value for value in expected):
        raise RuntimeError("Supervisor binding proof input is incomplete")
    matches = [
        row for row in supervisor_rows(workspace) if _text(row.get("display_name")) == expected[1]
    ]
    if len(matches) != 1:
        raise RuntimeError(
            "Supervisor binding name is absent or duplicated in the complete inventory"
        )
    listed = matches[0]
    listed_tuple = (
        _text(listed.get("supervisor_agent_id")),
        _text(listed.get("display_name")),
        _text(listed.get("endpoint_name")),
        _text(listed.get("creator")),
    )
    if listed_tuple != expected:
        raise RuntimeError("Supervisor binding inventory tuple changed before handoff")
    direct = supervisor_by_id(workspace, supervisor_id)
    direct_tuple = (
        _text(direct.get("supervisor_agent_id")),
        _text(direct.get("display_name")),
        _text(direct.get("endpoint_name")),
        _text(direct.get("creator")),
    )
    if direct_tuple != expected:
        raise RuntimeError("Supervisor binding direct tuple changed before handoff")
    endpoint_details = workspace.serving_endpoints.get(endpoint)
    endpoint_id = _text(_field(endpoint_details, "id"))
    assert_runtime_creator(
        _field(endpoint_details, "creator"),
        application_id=runtime_application_id,
        resource=f"managed Supervisor endpoint {endpoint}",
    )
    if not endpoint_id:
        raise RuntimeError("Supervisor binding endpoint has no immutable ID")
    return endpoint_id


def _tool_rows(workspace: Any, supervisor_id: str) -> list[dict[str, Any]]:
    payload = workspace.api_client.do(
        "GET",
        f"/api/2.1/supervisor-agents/{quote(supervisor_id, safe='')}/tools",
    )
    rows = payload if isinstance(payload, list) else _field(payload, "tools")
    if not isinstance(rows, list):
        raise RuntimeError("Supervisor creation tool inventory is malformed")
    normalized: list[dict[str, Any]] = []
    identities: set[str] = set()
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise RuntimeError("Supervisor creation tool inventory is malformed")
        row = {str(key): value for key, value in raw.items()}
        tool_id = _text(row.get("tool_id"))
        if not tool_id or tool_id in identities:
            raise RuntimeError(
                "Supervisor creation tool inventory has duplicate or missing identities"
            )
        identities.add(tool_id)
        normalized.append(row)
    return normalized


def _example_rows(workspace: Any, supervisor_id: str) -> list[Any]:
    payload = workspace.api_client.do(
        "GET",
        f"/api/2.1/supervisor-agents/{quote(supervisor_id, safe='')}/examples",
    )
    if payload == {}:
        return []
    rows = payload if isinstance(payload, list) else _field(payload, "examples")
    if not isinstance(rows, list):
        raise RuntimeError("Supervisor creation example inventory is malformed")
    return rows


def _expected_tools(record: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    try:
        contract = json.loads(str(record["contract_json"]))
    except (KeyError, json.JSONDecodeError) as exc:
        raise RuntimeError("Supervisor creation journal contract is malformed") from exc
    rows = contract.get("tools") if isinstance(contract, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("Supervisor creation journal tool contract is malformed")
    expected: dict[str, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            raise RuntimeError("Supervisor creation journal tool contract is malformed")
        tool_id = _text(raw.get("tool_id"))
        if not tool_id or tool_id in expected:
            raise RuntimeError("Supervisor creation journal tool IDs are malformed")
        expected[tool_id] = raw
    return expected


def exact_tool_subset(
    workspace: Any,
    record: Mapping[str, Any],
    *,
    supervisor_id: str,
) -> set[str]:
    expected = _expected_tools(record)
    actual = {row["tool_id"]: row for row in _tool_rows(workspace, supervisor_id)}
    unexpected = sorted(set(actual) - set(expected))
    if unexpected:
        raise RuntimeError(
            "journaled Supervisor contains unexpected tools: " + ", ".join(unexpected)
        )
    for tool_id, row in actual.items():
        tool = expected[tool_id]
        tool_type = _text(tool.get("tool_type"))
        if (
            row.get("tool_type") != tool_type
            or row.get("description") != tool.get("description")
            or row.get(tool_type) != tool.get(tool_type)
        ):
            raise RuntimeError(f"journaled Supervisor tool {tool_id!r} drifted")
    return set(actual)


def exact_journaled_candidate(
    workspace: Any,
    record: Mapping[str, Any],
    *,
    require_claim: bool,
) -> tuple[dict[str, Any], str]:
    """Hydrate and authenticate only the signed temporary/final immutable tuple."""

    rows = supervisor_rows(workspace)
    if require_claim:
        supervisor_id = _text(record.get("supervisor_id"))
        if not supervisor_id:
            raise RuntimeError("Supervisor creation journal has no immutable claim")
        matches = [row for row in rows if _text(row.get("supervisor_agent_id")) == supervisor_id]
    else:
        matches = [
            row for row in rows if _text(row.get("display_name")) == record["temporary_name"]
        ]
    if len(matches) != 1:
        raise RuntimeError("journaled Supervisor candidate is absent or ambiguous")
    supervisor_id = _text(matches[0].get("supervisor_agent_id"))
    direct = supervisor_by_id(workspace, supervisor_id)
    display_name = _text(direct.get("display_name"))
    allowed_names = {record["temporary_name"]}
    if require_claim:
        allowed_names.add(record["target_name"])
    canonical_instructions = json.loads(record["contract_json"])["instructions"]
    allowed_instructions = {record["temporary_instructions"]}
    if require_claim:
        allowed_instructions.add(canonical_instructions)
    if (
        _text(direct.get("supervisor_agent_id")) != supervisor_id
        or display_name not in allowed_names
        or direct.get("description") != json.loads(record["contract_json"])["description"]
        or direct.get("instructions") not in allowed_instructions
    ):
        raise RuntimeError("journaled Supervisor base definition drifted")
    creator = _text(direct.get("creator"))
    assert_runtime_creator(
        creator,
        application_id=record["runtime_application_id"],
        resource=f"journaled Supervisor {supervisor_id}",
    )
    if _example_rows(workspace, supervisor_id):
        raise RuntimeError("journaled Supervisor must contain zero examples")
    endpoint = _text(direct.get("endpoint_name"))
    details = workspace.serving_endpoints.get(endpoint)
    endpoint_id = _text(_field(details, "id"))
    endpoint_creator = _text(_field(details, "creator"))
    assert_runtime_creator(
        endpoint_creator,
        application_id=record["runtime_application_id"],
        resource=f"journaled Supervisor endpoint {endpoint}",
    )
    if not endpoint or not endpoint_id:
        raise RuntimeError("journaled Supervisor endpoint identity is incomplete")
    if require_claim and (
        supervisor_id,
        endpoint,
        endpoint_id,
        creator,
        _text(direct.get("create_time")),
    ) != (
        record["supervisor_id"],
        record["endpoint"],
        record["endpoint_id"],
        record["creator"],
        record["create_time"],
    ):
        raise RuntimeError("journaled Supervisor immutable tuple drifted")
    exact_tool_subset(workspace, record, supervisor_id=supervisor_id)
    return direct, endpoint_id


def create_from_intent(
    workspace: Any,
    record: Mapping[str, Any],
    *,
    assert_single_writer: Callable[[], None],
    create: Callable[[dict[str, str]], Mapping[str, Any]],
    now: datetime | None = None,
) -> dict[str, str]:
    """Create once; an ambiguous provider result always requires audit recovery."""

    if record.get("disposition", "active") != "active":
        raise RuntimeError("retire-only Supervisor creation cannot create")
    if record.get("supervisor_id"):
        raise RuntimeError("claimed Supervisor creation journal cannot create again")
    current = now or datetime.now(UTC)
    authorized_until = datetime.fromisoformat(str(record["create_authorized_until"]))
    if current >= authorized_until:
        raise RuntimeError("Supervisor creation authorization window expired")
    matching = [
        row
        for row in supervisor_rows(workspace)
        if _text(row.get("display_name")) == record["temporary_name"]
    ]
    if matching:
        raise RuntimeError(
            "unclaimed Supervisor creation may exist; authoritative audit recovery is required"
        )
    assert_single_writer()
    try:
        response = create(base_create_payload(dict(record)))
    except Exception as exc:
        raise RuntimeError(
            "Supervisor create result is ambiguous; do not retry before audit recovery"
        ) from exc
    if not isinstance(response, Mapping):
        raise RuntimeError("Supervisor create returned an invalid payload")
    supervisor_id = _text(response.get("supervisor_agent_id"))
    if not supervisor_id:
        raise RuntimeError("Supervisor create did not return an immutable ID")
    direct = supervisor_by_id(workspace, supervisor_id)
    if _text(direct.get("display_name")) != record["temporary_name"]:
        raise RuntimeError("created Supervisor does not match the signed temporary name")
    hydrated, endpoint_id = exact_journaled_candidate(
        workspace,
        record,
        require_claim=False,
    )
    return {
        "supervisor_id": supervisor_id,
        "endpoint": _text(hydrated.get("endpoint_name")),
        "endpoint_id": endpoint_id,
        "creator": _text(hydrated.get("creator")),
        "create_time": _text(hydrated.get("create_time")),
    }


def _assert_unchanged_journal(
    workspace: Any,
    record: dict[str, Any],
) -> None:
    if (
        download(
            workspace,
            app_name=record["app_name"],
            runtime_application_id=record["runtime_application_id"],
        )
        != record
    ):
        raise RuntimeError("Supervisor creation journal changed during convergence")


def finalize_signed_blue_for_planning(
    workspace: Any,
    *,
    signed_blue_pin: Mapping[str, object] | None,
    canonical_name: str,
    genie_space_id: str,
    catalog: str,
    runtime_application_id: str,
    proxy_application_id: str | None,
    approved_query_application_ids: Sequence[str],
    assert_single_writer: Callable[[], None],
    list_agents: Callable[[], list[dict[str, Any]]] | None = None,
    rename_agent: Callable[[str, str], None] | None = None,
    assert_contract: Callable[..., None] | None = None,
) -> dict[str, str]:
    """Finalize only an exact signed-blue predecessor before creation planning."""

    from tools.databricks.agentic_supervisor_endpoint import supervisor_candidates
    from tools.databricks.provision_agentic_resources import (
        _rename_supervisor_agent,
        assert_exact_supervisor_contract,
    )
    from tools.databricks.signed_blue_supervisor_recovery import (
        recover_interrupted_signed_blue_finalization,
    )

    inventory = list_agents or (lambda: supervisor_rows(workspace))
    rename = rename_agent or _rename_supervisor_agent
    contract_check = assert_contract or assert_exact_supervisor_contract
    before = supervisor_candidates(
        inventory(),
        display_name=canonical_name,
        genie_space_id=genie_space_id,
        catalog=catalog,
    )
    after = recover_interrupted_signed_blue_finalization(
        workspace,
        before,
        signed_blue_pin=signed_blue_pin,
        display_name=canonical_name,
        genie_space_id=genie_space_id,
        catalog=catalog,
        runtime_application_id=runtime_application_id,
        managed_query_application_id=proxy_application_id,
        additional_managed_query_application_ids=approved_query_application_ids,
        assert_contract=contract_check,
        assert_single_writer=assert_single_writer,
        list_agents=inventory,
        rename_agent=rename,
    )
    before_id = _text(
        (before.canonical or before.replacement or before.legacy_replacement or {}).get(
            "supervisor_agent_id"
        )
    )
    after_id = _text(
        (after.canonical or after.replacement or after.legacy_replacement or {}).get(
            "supervisor_agent_id"
        )
    )
    return {
        "status": "finalized" if before != after else "unchanged",
        "supervisor_id": after_id or before_id,
    }


def converge_claimed(
    workspace: Any,
    record: dict[str, Any],
    *,
    assert_single_writer: Callable[[], None],
    create_tool: Callable[[str, str, dict[str, Any]], Any],
    update_field: Callable[[str, str, str], Any],
) -> dict[str, str]:
    """Add only missing exact tools and finalize the deterministic display name."""

    if record.get("disposition", "active") != "active":
        raise RuntimeError("retire-only Supervisor creation cannot converge")
    if not record.get("supervisor_id"):
        raise RuntimeError("Supervisor creation convergence requires an immutable claim")
    expected = _expected_tools(record)
    direct, _endpoint_id = exact_journaled_candidate(
        workspace,
        record,
        require_claim=True,
    )
    canonical_instructions = json.loads(record["contract_json"])["instructions"]
    if direct.get("instructions") == record["temporary_instructions"]:
        assert_single_writer()
        _assert_unchanged_journal(workspace, record)
        try:
            update_field(
                record["supervisor_id"],
                "instructions",
                canonical_instructions,
            )
        except Exception as update_error:  # noqa: BLE001
            refreshed = supervisor_by_id(workspace, record["supervisor_id"])
            if refreshed.get("instructions") != canonical_instructions:
                raise RuntimeError(
                    "Supervisor instructions update did not commit exactly"
                ) from update_error
    elif direct.get("instructions") != canonical_instructions:
        raise RuntimeError("journaled Supervisor instructions drifted")
    for tool_id, tool in expected.items():
        _assert_unchanged_journal(workspace, record)
        direct, _endpoint_id = exact_journaled_candidate(
            workspace,
            record,
            require_claim=True,
        )
        present = exact_tool_subset(
            workspace,
            record,
            supervisor_id=record["supervisor_id"],
        )
        if tool_id in present:
            continue
        if _text(direct.get("display_name")) != record["temporary_name"]:
            raise RuntimeError("finalized Supervisor is missing a reviewed tool")
        payload = dict(tool)
        payload.pop("tool_id", None)
        assert_single_writer()
        _assert_unchanged_journal(workspace, record)
        try:
            create_tool(record["supervisor_id"], tool_id, payload)
        except Exception as create_error:  # noqa: BLE001 - resolve ambiguous provider commit
            try:
                after = exact_tool_subset(
                    workspace,
                    record,
                    supervisor_id=record["supervisor_id"],
                )
            except Exception as read_error:  # noqa: BLE001
                raise RuntimeError(
                    f"Supervisor tool {tool_id!r} create state is ambiguous"
                ) from read_error
            if tool_id not in after:
                raise RuntimeError(
                    f"Supervisor tool {tool_id!r} did not commit exactly"
                ) from create_error
    if exact_tool_subset(
        workspace,
        record,
        supervisor_id=record["supervisor_id"],
    ) != set(expected):
        raise RuntimeError("Supervisor creation tools did not converge exactly")
    direct, endpoint_id = exact_journaled_candidate(
        workspace,
        record,
        require_claim=True,
    )
    if _text(direct.get("display_name")) == record["temporary_name"]:
        conflicts = [
            row
            for row in supervisor_rows(workspace)
            if _text(row.get("display_name")) == record["target_name"]
            and _text(row.get("supervisor_agent_id")) != record["supervisor_id"]
        ]
        if conflicts:
            raise RuntimeError("Supervisor deterministic target name is already occupied")
        assert_single_writer()
        _assert_unchanged_journal(workspace, record)
        try:
            update_field(
                record["supervisor_id"],
                "display_name",
                record["target_name"],
            )
        except Exception:  # noqa: BLE001 - resolve ambiguous provider commit
            try:
                assert_unique_target_claim(workspace, record)
            except Exception as read_error:  # noqa: BLE001
                raise RuntimeError(
                    "Supervisor deterministic target rename state is ambiguous"
                ) from read_error
    assert_unique_target_claim(workspace, record)
    final, final_endpoint_id = exact_journaled_candidate(
        workspace,
        record,
        require_claim=True,
    )
    if (
        _text(final.get("display_name")) != record["target_name"]
        or final.get("instructions") != canonical_instructions
        or final_endpoint_id != endpoint_id
        or exact_tool_subset(
            workspace,
            record,
            supervisor_id=record["supervisor_id"],
        )
        != set(expected)
    ):
        raise RuntimeError("Supervisor creation final postflight failed")
    return {
        "supervisor_id": record["supervisor_id"],
        "display_name": record["target_name"],
        "endpoint": record["endpoint"],
        "endpoint_id": record["endpoint_id"],
    }


def _write_result(path: Path, result: Mapping[str, str]) -> None:
    path.write_text(
        json.dumps(dict(result), sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase",
        choices=("finalize-signed-blue", "create", "complete"),
    )
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--runtime-application-id", required=True)
    parser.add_argument("--deployment-lease-id", required=True)
    parser.add_argument("--deployment-source-git-sha", required=True)
    parser.add_argument("--canonical-name", default="")
    parser.add_argument("--genie-space-id", default="")
    parser.add_argument("--catalog", default="")
    parser.add_argument("--proxy-application-id", default="")
    parser.add_argument("--approved-query-application-id", action="append", default=[])
    parser.add_argument("--out-json", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    workspace = WorkspaceClient()
    assert_current_runtime_identity(
        workspace,
        application_id=args.runtime_application_id,
    )
    lease_check = app_deployment_lease.held_assertion(
        workspace,
        app_name=args.app_name,
        lease_id=args.deployment_lease_id,
        source_git_sha=args.deployment_source_git_sha,
    )
    lease_check()
    if args.phase == "finalize-signed-blue":
        if not args.canonical_name or not args.genie_space_id or not args.catalog:
            raise ValueError(
                "signed-blue finalization requires canonical name, Genie space, and catalog"
            )
        from tools.databricks.signed_blue_supervisor_recovery import (
            signed_blue_supervisor_pin_from_env,
        )

        result = finalize_signed_blue_for_planning(
            workspace,
            signed_blue_pin=signed_blue_supervisor_pin_from_env(),
            canonical_name=args.canonical_name,
            genie_space_id=args.genie_space_id,
            catalog=args.catalog,
            runtime_application_id=args.runtime_application_id,
            proxy_application_id=args.proxy_application_id,
            approved_query_application_ids=tuple(args.approved_query_application_id),
            assert_single_writer=lease_check,
        )
        _write_result(args.out_json, result)
        return 0
    record = download(
        workspace,
        app_name=args.app_name,
        runtime_application_id=args.runtime_application_id,
    )
    if record is None:
        raise RuntimeError("Supervisor creation runtime phase has no signed journal")
    if (
        record["admitted_lease_id"] != args.deployment_lease_id
        or record["admitted_source_git_sha"] != args.deployment_source_git_sha
    ):
        raise RuntimeError("Supervisor creation journal was not adopted by this deployment")
    if (
        not args.canonical_name
        or not args.genie_space_id
        or not args.catalog
        or not matches_current_policy(
            record,
            canonical_name=args.canonical_name,
            genie_space_id=args.genie_space_id,
            catalog=args.catalog,
        )
    ):
        raise RuntimeError(
            "Supervisor creation mutation is not authorized by current reviewed policy"
        )
    if args.phase == "create":
        result = create_from_intent(
            workspace,
            record,
            assert_single_writer=lease_check,
            create=lambda payload: _cli_json(
                ["supervisor-agents", "create-supervisor-agent"],
                input_json=payload,
            ),
        )
    else:
        result = converge_claimed(
            workspace,
            record,
            assert_single_writer=lease_check,
            create_tool=lambda supervisor_id, tool_id, payload: _cli_json(
                [
                    "supervisor-agents",
                    "create-tool",
                    f"supervisor-agents/{supervisor_id}",
                    tool_id,
                ],
                input_json=payload,
            ),
            update_field=lambda supervisor_id, field, value: _cli_text(
                [
                    "supervisor-agents",
                    "update-supervisor-agent",
                    f"supervisor-agents/{supervisor_id}",
                    field,
                    value,
                ]
            ),
        )
    _write_result(args.out_json, result)
    return 0


def _cli_json(
    args: Sequence[str],
    *,
    input_json: dict[str, Any],
) -> Mapping[str, Any]:
    from tools.databricks.provision_agentic_resources import _run

    result = _run(list(args), input_json=input_json)
    if not isinstance(result, Mapping):
        raise RuntimeError("Supervisor creation CLI returned an invalid payload")
    return result


def _cli_text(args: Sequence[str]) -> str:
    from tools.databricks.provision_agentic_resources import _run_no_json

    return _run_no_json(list(args))


if __name__ == "__main__":
    raise SystemExit(main())
