from __future__ import annotations

import base64
import io
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from databricks.sdk.errors import ResourceAlreadyExists, ResourceDoesNotExist

from backend.agents import supervisor_contract
from backend.services.ai_gateway_proof_attestation import (
    derive_gateway_proof_verify_key,
)
from tools.databricks import agentic_supervisor_endpoint as supervisor_endpoint
from tools.databricks import historical_supervisor_creation_admission as creation_admission
from tools.databricks import provision_agentic_resources as provision
from tools.databricks import reconcile_historical_agent_endpoints as historical
from tools.databricks import signed_blue_supervisor_recovery as signed_blue
from tools.databricks import supervisor_creation_control as control
from tools.databricks import supervisor_creation_field_update as field_update
from tools.databricks import supervisor_creation_journal as journal
from tools.databricks import supervisor_creation_runtime as runtime

_SIGNING_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")
_VERIFY_KEY = derive_gateway_proof_verify_key(_SIGNING_KEY)
_APP = "mip-app"
_RUNTIME = "runtime-client"
_LEASE = str(uuid4())
_ROOT = str(uuid4())
_SOURCE = "a" * 40
_NOW = datetime(2026, 7, 25, 12, tzinfo=UTC)
_LEASE_EXPIRES = datetime(2099, 1, 1, tzinfo=UTC)
_DEFAULT_TOOL_PAYLOAD = object()


class _Files:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    def upload(
        self,
        path: str,
        content: io.BytesIO,
        *,
        format: object,
        overwrite: bool,
    ) -> None:
        del format
        if path in self.data and not overwrite:
            raise ResourceAlreadyExists("exists")
        self.data[path] = content.read()

    def download(self, path: str) -> io.BytesIO:
        if path not in self.data:
            raise ResourceDoesNotExist("missing")
        return io.BytesIO(self.data[path])

    def delete(self, path: str) -> None:
        if path not in self.data:
            raise ResourceDoesNotExist("missing")
        del self.data[path]


class _Api:
    def __init__(self, owner: _Workspace) -> None:
        self.owner = owner

    def do(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
    ) -> Any:
        assert method == "GET"
        if path == "/api/2.1/supervisor-agents":
            return {"supervisor_agents": list(self.owner.agents.values())}
        parts = path.split("/")
        supervisor_id = parts[4]
        if path.endswith("/tools"):
            if self.owner.tool_payload is not _DEFAULT_TOOL_PAYLOAD:
                if callable(self.owner.tool_payload):
                    return self.owner.tool_payload(query or {})
                return self.owner.tool_payload
            return {"tools": list(self.owner.tools[supervisor_id].values())}
        if path.endswith("/examples"):
            if callable(self.owner.example_payload):
                return self.owner.example_payload(query or {})
            return self.owner.example_payload
        return dict(self.owner.agents[supervisor_id])


class _Workspace:
    def __init__(self) -> None:
        self.workspace = _Files()
        self.api_client = _Api(self)
        self.agents: dict[str, dict[str, Any]] = {}
        self.tools: dict[str, dict[str, dict[str, Any]]] = {}
        self.tool_payload: object = _DEFAULT_TOOL_PAYLOAD
        self.example_payload: object = {"examples": []}
        self.serving_endpoints = SimpleNamespace(get=self._endpoint)

    def get_workspace_id(self) -> int:
        return 123456789

    def _endpoint(self, name: str) -> dict[str, str]:
        return {"id": f"{name}-id", "creator": _RUNTIME}


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", _SIGNING_KEY)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", _VERIFY_KEY)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS", "")
    monkeypatch.setattr(
        journal,
        "assert_held",
        lambda *_args, **_kwargs: {
            "lease_id": _LEASE,
            "source_git_sha": _SOURCE,
            "writer_application_id": _RUNTIME,
            "recovery_root_lease_id": _ROOT,
            "expires_at": _LEASE_EXPIRES.isoformat(),
        },
    )


def _prepare(workspace: _Workspace) -> dict[str, Any]:
    return journal.prepare(
        workspace,
        app_name=_APP,
        lease_id=_LEASE,
        source_git_sha=_SOURCE,
        runtime_application_id=_RUNTIME,
        canonical_name="Mortgage Growth Agent",
        target_name="Mortgage Growth Agent",
        genie_space_id="genie-space",
        catalog="mip",
        now=_NOW,
    )


def _create(workspace: _Workspace, record: dict[str, Any]) -> dict[str, str]:
    def create(payload: dict[str, str]) -> dict[str, str]:
        supervisor_id = "supervisor-created"
        workspace.agents[supervisor_id] = {
            "supervisor_agent_id": supervisor_id,
            **payload,
            "endpoint_name": "supervisor-endpoint",
            "creator": _RUNTIME,
            "create_time": "2026-07-25T12:00:01Z",
        }
        workspace.tools[supervisor_id] = {}
        return {
            "supervisor_agent_id": supervisor_id,
            "endpoint_name": "supervisor-endpoint",
        }

    return runtime.create_from_intent(
        workspace,
        record,
        assert_single_writer=lambda: None,
        create=create,
        now=_NOW,
    )


def test_create_accepts_provider_omitted_empty_examples() -> None:
    workspace = _Workspace()
    workspace.example_payload = {}

    created = _create(workspace, _prepare(workspace))

    assert created["supervisor_id"] == "supervisor-created"


def test_audit_recovery_accepts_provider_omitted_empty_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _Workspace()
    intent = _prepare(workspace)
    created = _create(workspace, intent)
    workspace.tool_payload = {}
    monkeypatch.setattr(
        control,
        "find_supervisor_create_proof",
        lambda *_args, **_kwargs: SimpleNamespace(
            supervisor_id=created["supervisor_id"],
            event_id="audit-event",
            request_id="audit-request",
        ),
    )

    claimed = control.recover_from_audit(
        workspace,
        intent,
        warehouse_id="warehouse",
    )

    assert claimed["supervisor_id"] == created["supervisor_id"]
    assert claimed["claim_proof_kind"] == "system_access_audit"
    assert claimed["create_audit_event_id"] == "audit-event"
    assert claimed["create_audit_request_id"] == "audit-request"


@pytest.mark.parametrize("payload", ({"unexpected": []}, None))
def test_recovery_rejects_other_malformed_tool_inventory(payload: object) -> None:
    workspace = _Workspace()
    intent = _prepare(workspace)
    created = _create(workspace, intent)
    workspace.tool_payload = payload

    with pytest.raises(RuntimeError, match="tool inventory is malformed"):
        runtime.exact_tool_subset(
            workspace,
            intent,
            supervisor_id=created["supervisor_id"],
        )


def test_recovery_accepts_live_duplicate_genie_space_identifier() -> None:
    workspace = _Workspace()
    intent = _prepare(workspace)
    created = _create(workspace, intent)
    expected = json.loads(intent["contract_json"])["tools"][0]
    live = {**expected, "genie_space": {**expected["genie_space"]}}
    live["genie_space"]["space_id"] = expected["genie_space"]["id"]
    workspace.tool_payload = {"tools": [live]}

    assert runtime.exact_tool_subset(
        workspace,
        intent,
        supervisor_id=created["supervisor_id"],
    ) == {"mortgage_data_analyst"}


@pytest.mark.parametrize(
    ("actual", "expected", "is_exact"),
    (
        ({"id": "space"}, {"id": "space"}, True),
        ({"id": "space", "space_id": "space"}, {"id": "space"}, True),
        ({}, {}, False),
        ({"id": ""}, {"id": ""}, False),
        ({"id": " space "}, {"id": " space "}, False),
        (
            {"id": "space", "permission": "CAN_EDIT"},
            {"id": "space", "permission": "CAN_EDIT"},
            False,
        ),
    ),
)
def test_shared_genie_resource_matcher_requires_canonical_expected_contract(
    actual: object,
    expected: object,
    is_exact: bool,
) -> None:
    assert (
        supervisor_contract.supervisor_tool_resource_is_exact(
            "genie_space",
            actual,
            expected,
        )
        is is_exact
    )


@pytest.mark.parametrize(
    "genie_space",
    (
        {"id": "genie-space", "space_id": "other-space"},
        {"id": "other-space", "space_id": "genie-space"},
        {"id": "other-space"},
        {"space_id": "genie-space"},
        {"id": "genie-space", "space_id": "genie-space", "permission": "CAN_EDIT"},
        "genie-space",
    ),
)
def test_recovery_rejects_noncanonical_genie_space_readback(
    genie_space: object,
) -> None:
    workspace = _Workspace()
    intent = _prepare(workspace)
    created = _create(workspace, intent)
    expected = json.loads(intent["contract_json"])["tools"][0]
    workspace.tool_payload = {"tools": [{**expected, "genie_space": genie_space}]}

    with pytest.raises(RuntimeError, match="mortgage_data_analyst.*drifted"):
        runtime.exact_tool_subset(
            workspace,
            intent,
            supervisor_id=created["supervisor_id"],
        )


def test_recovery_keeps_uc_function_resource_comparison_exact() -> None:
    workspace = _Workspace()
    intent = _prepare(workspace)
    created = _create(workspace, intent)
    expected = json.loads(intent["contract_json"])["tools"][1]
    workspace.tool_payload = {
        "tools": [{**expected, "uc_function": {"name": "mip.gold.other_function"}}]
    }

    with pytest.raises(RuntimeError, match="build_cohort.*drifted"):
        runtime.exact_tool_subset(
            workspace,
            intent,
            supervisor_id=created["supervisor_id"],
        )


def test_recovery_rejects_unexpected_tool_on_second_page() -> None:
    workspace = _Workspace()
    intent = _prepare(workspace)
    created = _create(workspace, intent)
    expected = json.loads(intent["contract_json"])["tools"][0]

    def pages(query: dict[str, Any]) -> object:
        if query == {"page_size": 100}:
            return {"tools": [expected], "next_page_token": "second-page"}
        assert query == {"page_size": 100, "page_token": "second-page"}
        return {
            "tools": [
                {
                    "tool_id": "unreviewed",
                    "tool_type": "function",
                    "description": "unreviewed",
                    "function": {"name": "mip.gold.unreviewed"},
                }
            ]
        }

    workspace.tool_payload = pages
    with pytest.raises(RuntimeError, match="unexpected tools: unreviewed"):
        runtime.exact_tool_subset(
            workspace,
            intent,
            supervisor_id=created["supervisor_id"],
        )


def test_recovery_rejects_cross_page_duplicate_tool() -> None:
    workspace = _Workspace()
    intent = _prepare(workspace)
    created = _create(workspace, intent)
    expected = json.loads(intent["contract_json"])["tools"][0]

    def pages(query: dict[str, Any]) -> object:
        if query == {"page_size": 100}:
            return {"tools": [expected], "next_page_token": "second-page"}
        assert query == {"page_size": 100, "page_token": "second-page"}
        return {"tools": [expected]}

    workspace.tool_payload = pages
    with pytest.raises(RuntimeError, match="duplicate or missing identities"):
        runtime.exact_tool_subset(
            workspace,
            intent,
            supervisor_id=created["supervisor_id"],
        )


@pytest.mark.parametrize(
    ("first_token", "second_payload", "error"),
    (
        ([], None, "page token is malformed"),
        (
            "cycle",
            {"tools": [], "next_page_token": "cycle"},
            "pagination cycled",
        ),
        ("second-page", {}, "tool inventory is malformed"),
    ),
)
def test_recovery_rejects_malformed_tool_pagination(
    first_token: object,
    second_payload: object,
    error: str,
) -> None:
    workspace = _Workspace()
    intent = _prepare(workspace)
    created = _create(workspace, intent)

    def pages(query: dict[str, Any]) -> object:
        if query == {"page_size": 100}:
            return {"tools": [], "next_page_token": first_token}
        assert query == {"page_size": 100, "page_token": first_token}
        return second_payload

    workspace.tool_payload = pages
    with pytest.raises(RuntimeError, match=error):
        runtime.exact_tool_subset(
            workspace,
            intent,
            supervisor_id=created["supervisor_id"],
        )


def test_supervisor_inventory_rejects_unique_token_overflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _Workspace()
    calls = 0

    class _UnboundedInventory:
        def do(
            self,
            method: str,
            path: str,
            *,
            query: dict[str, Any] | None = None,
        ) -> object:
            nonlocal calls
            assert method == "GET"
            assert path == "/api/2.1/supervisor-agents"
            assert query is not None
            calls += 1
            return {
                "supervisor_agents": [],
                "next_page_token": f"unique-{calls}",
            }

    workspace.api_client = _UnboundedInventory()
    monkeypatch.setattr(runtime, "_MAX_INVENTORY_PAGES", 3)

    with pytest.raises(RuntimeError, match="inventory exceeded the page limit"):
        runtime.supervisor_rows(workspace)
    assert calls == 3


def test_recovery_rejects_example_on_second_page() -> None:
    workspace = _Workspace()
    intent = _prepare(workspace)
    _create(workspace, intent)

    def pages(query: dict[str, Any]) -> object:
        if query == {"page_size": 100}:
            return {"examples": [], "next_page_token": "second-page"}
        assert query == {"page_size": 100, "page_token": "second-page"}
        return {"examples": [{"instructions": "unreviewed"}]}

    workspace.example_payload = pages
    with pytest.raises(RuntimeError, match="must contain zero examples"):
        runtime.exact_journaled_candidate(
            workspace,
            intent,
            require_claim=False,
        )


@pytest.mark.parametrize("payload", ({"unexpected": []}, None))
def test_create_rejects_other_malformed_example_inventory(payload: object) -> None:
    workspace = _Workspace()
    workspace.example_payload = payload

    with pytest.raises(RuntimeError, match="example inventory is malformed"):
        _create(workspace, _prepare(workspace))


def test_create_rejects_nonempty_example_inventory() -> None:
    workspace = _Workspace()
    workspace.example_payload = {"examples": [{"instructions": "unreviewed"}]}

    with pytest.raises(RuntimeError, match="must contain zero examples"):
        _create(workspace, _prepare(workspace))


def test_origin_is_immutable_while_successor_lease_is_adopted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _Workspace()
    original = _prepare(workspace)
    successor = str(uuid4())
    monkeypatch.setattr(
        journal,
        "assert_held",
        lambda *_args, **_kwargs: {
            "lease_id": successor,
            "source_git_sha": "b" * 40,
            "writer_application_id": _RUNTIME,
            "recovery_root_lease_id": _ROOT,
            "expires_at": _LEASE_EXPIRES.isoformat(),
        },
    )

    adopted = journal.prepare(
        workspace,
        app_name=_APP,
        lease_id=successor,
        source_git_sha="b" * 40,
        runtime_application_id=_RUNTIME,
        canonical_name=original["canonical_name"],
        target_name=original["target_name"],
        genie_space_id=original["genie_space_id"],
        catalog=original["catalog"],
        now=_NOW,
    )

    assert adopted["origin_lease_id"] == _LEASE
    assert adopted["origin_source_git_sha"] == _SOURCE
    assert adopted["admitted_lease_id"] == successor
    assert adopted["admitted_source_git_sha"] == "b" * 40
    assert adopted["intent_id"] == original["intent_id"]
    assert adopted["disposition"] == "active"


def test_successor_contract_change_marks_historical_intent_retire_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _Workspace()
    original = _prepare(workspace)
    created = _create(workspace, original)
    original = journal.claim(
        workspace,
        app_name=_APP,
        lease_id=_LEASE,
        source_git_sha=_SOURCE,
        runtime_application_id=_RUNTIME,
        **created,
        proof_kind="create_response",
        now=_NOW + timedelta(seconds=2),
    )
    legacy = {**original, "version": 1}
    legacy.pop("disposition")
    workspace.workspace.data[journal.path(_APP)] = journal._canonical(  # noqa: SLF001
        journal._sign(legacy)  # noqa: SLF001
    ).encode()
    original = journal.download(
        workspace,
        app_name=_APP,
        runtime_application_id=_RUNTIME,
    )
    assert original is not None
    assert original["version"] == 1
    historical_contract = json.loads(original["contract_json"])
    successor = str(uuid4())
    successor_contract = {
        **historical_contract,
        "instructions": "Successor instructions with a changed governance contract.",
        "tools": [
            {
                "tool_id": "successor_tool",
                "tool_type": "uc_function",
                "description": "A successor-only reviewed tool.",
                "uc_function": {"name": "successor.gold.fn_successor"},
            }
        ],
    }
    monkeypatch.setattr(
        journal,
        "canonical_supervisor_contract_json",
        lambda **_kwargs: json.dumps(
            successor_contract,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    monkeypatch.setattr(
        journal,
        "SUPERVISOR_INSTRUCTIONS",
        successor_contract["instructions"],
    )
    monkeypatch.setattr(
        journal,
        "assert_held",
        lambda *_args, **_kwargs: {
            "lease_id": successor,
            "source_git_sha": "b" * 40,
            "writer_application_id": _RUNTIME,
            "recovery_root_lease_id": _ROOT,
            "expires_at": _LEASE_EXPIRES.isoformat(),
        },
    )

    assert (
        journal.download(
            workspace,
            app_name=_APP,
            runtime_application_id=_RUNTIME,
        )
        == original
    )
    adopted = journal.prepare(
        workspace,
        app_name=_APP,
        lease_id=successor,
        source_git_sha="b" * 40,
        runtime_application_id=_RUNTIME,
        canonical_name="Successor Agent",
        target_name="Successor Agent",
        genie_space_id="successor-space",
        catalog="successor",
        now=_NOW,
    )
    assert adopted["contract_json"] == original["contract_json"]
    assert adopted["temporary_instructions"] == original["temporary_instructions"]
    assert adopted["admitted_lease_id"] == successor
    assert adopted["admitted_source_git_sha"] == "b" * 40
    assert adopted["disposition"] == "retire_only"

    mutations: list[str] = []
    with pytest.raises(RuntimeError, match="retire-only"):
        runtime.converge_claimed(
            workspace,
            adopted,
            assert_single_writer=lambda: mutations.append("lease"),
            create_tool=lambda *_args, **_kwargs: mutations.append("tool"),
            update_field=lambda *_args, **_kwargs: mutations.append("field"),
        )
    assert mutations == []
    assert workspace.tools[adopted["supervisor_id"]] == {}
    direct = workspace.agents[adopted["supervisor_id"]]
    disposition, reviewed = creation_admission.pending_creation_candidate_disposition(
        workspace,
        adopted,
        direct,
        direct,
        adopted["endpoint_id"],
        _RUNTIME,
        canonical_name="Successor Agent",
        genie_space_id="successor-space",
        catalog="successor",
    )
    assert disposition == "retire"
    assert reviewed is not None
    assert reviewed.supervisor_id == adopted["supervisor_id"]
    assert reviewed.contract_json == adopted["contract_json"]
    with pytest.raises(RuntimeError, match="retire-only"):
        control.complete_and_clear(workspace, adopted)
    assert (
        journal.download(
            workspace,
            app_name=_APP,
            runtime_application_id=_RUNTIME,
        )
        == adopted
    )

    plan = control.plan_and_prepare(
        workspace,
        app_name=_APP,
        lease_id=successor,
        source_git_sha="b" * 40,
        runtime_application_id=_RUNTIME,
        canonical_name="Successor Agent",
        genie_space_id="successor-space",
        catalog="successor",
        proxy_application_id="proxy-client",
        approved_query_application_ids=("app-client",),
    )
    assert plan["action"] == "handoff_required"

    journal.clear(
        workspace,
        app_name=_APP,
        lease_id=successor,
        source_git_sha="b" * 40,
        runtime_application_id=_RUNTIME,
        expected=adopted,
    )
    successor_plan = control.plan_and_prepare(
        workspace,
        app_name=_APP,
        lease_id=successor,
        source_git_sha="b" * 40,
        runtime_application_id=_RUNTIME,
        canonical_name="Successor Agent",
        genie_space_id="successor-space",
        catalog="successor",
        proxy_application_id="proxy-client",
        approved_query_application_ids=("app-client",),
    )
    assert successor_plan["action"] == "create"
    successor_record = journal.download(
        workspace,
        app_name=_APP,
        runtime_application_id=_RUNTIME,
    )
    assert successor_record is not None
    assert successor_record["disposition"] == "active"
    assert successor_record["contract_json"] == json.dumps(
        successor_contract,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert workspace.tools[adopted["supervisor_id"]] == {}


def test_adoption_rejects_another_recovery_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _Workspace()
    original = _prepare(workspace)
    successor = str(uuid4())
    monkeypatch.setattr(
        journal,
        "assert_held",
        lambda *_args, **_kwargs: {
            "lease_id": successor,
            "source_git_sha": "b" * 40,
            "writer_application_id": _RUNTIME,
            "recovery_root_lease_id": str(uuid4()),
            "expires_at": _LEASE_EXPIRES.isoformat(),
        },
    )

    with pytest.raises(RuntimeError, match="another recovery scope"):
        journal.prepare(
            workspace,
            app_name=_APP,
            lease_id=successor,
            source_git_sha="b" * 40,
            runtime_application_id=_RUNTIME,
            canonical_name=original["canonical_name"],
            target_name=original["target_name"],
            genie_space_id=original["genie_space_id"],
            catalog=original["catalog"],
            now=_NOW,
        )


def test_signed_journal_tamper_and_claim_overwrite_fail_closed() -> None:
    workspace = _Workspace()
    _prepare(workspace)
    journal_path = journal.path(_APP)
    signed = json.loads(workspace.workspace.data[journal_path])
    signed["target_name"] = "attacker target"
    workspace.workspace.data[journal_path] = json.dumps(signed).encode()
    with pytest.raises(RuntimeError, match="signature is invalid"):
        journal.download(
            workspace,
            app_name=_APP,
            runtime_application_id=_RUNTIME,
        )

    workspace = _Workspace()
    intent = _prepare(workspace)
    created = _create(workspace, intent)
    claimed = journal.claim(
        workspace,
        app_name=_APP,
        lease_id=_LEASE,
        source_git_sha=_SOURCE,
        runtime_application_id=_RUNTIME,
        **created,
        proof_kind="create_response",
        now=_NOW + timedelta(seconds=2),
    )
    with pytest.raises(RuntimeError, match="already claims another tuple"):
        journal.claim(
            workspace,
            app_name=_APP,
            lease_id=_LEASE,
            source_git_sha=_SOURCE,
            runtime_application_id=_RUNTIME,
            **{**created, "endpoint_id": "different-endpoint-id"},
            proof_kind="create_response",
            now=_NOW + timedelta(seconds=3),
        )
    assert (
        journal.download(
            workspace,
            app_name=_APP,
            runtime_application_id=_RUNTIME,
        )
        == claimed
    )


def test_signed_blue_finalization_precedes_mq1_journaled_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _Workspace()
    canonical_name = "Mortgage Growth Agent"
    empty = supervisor_endpoint.supervisor_candidates(
        [],
        display_name=canonical_name,
        genie_space_id="genie-space",
        catalog="mip",
    )
    workspace.agents["signed-blue"] = {
        "supervisor_agent_id": "signed-blue",
        "display_name": empty.replacement_name,
        "endpoint_name": "signed-blue-endpoint",
        "creator": _RUNTIME,
        "create_time": "2026-07-25T11:00:00Z",
    }
    workspace.tools["signed-blue"] = {}
    monkeypatch.setattr(
        signed_blue,
        "exact_supervisor_endpoint_id",
        lambda *_args, **_kwargs: "signed-blue-endpoint-id",
    )
    monkeypatch.setattr(
        signed_blue,
        "supervisor_endpoint_requires_managed_query_rotation",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        supervisor_endpoint,
        "supervisor_endpoint_requires_managed_query_rotation",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        provision,
        "assert_exact_supervisor_contract",
        lambda *_args, **_kwargs: None,
    )
    mutations: list[str] = []

    def rename(supervisor_id: str, display_name: str) -> None:
        mutations.append("rename")
        workspace.agents[supervisor_id]["display_name"] = display_name

    finalized = runtime.finalize_signed_blue_for_planning(
        workspace,
        signed_blue_pin={
            "supervisor_id": "signed-blue",
            "endpoint": "signed-blue-endpoint",
            "endpoint_id": "signed-blue-endpoint-id",
            "creator": _RUNTIME,
        },
        canonical_name=canonical_name,
        genie_space_id="genie-space",
        catalog="mip",
        runtime_application_id=_RUNTIME,
        proxy_application_id="proxy-client",
        approved_query_application_ids=("app-client",),
        assert_single_writer=lambda: None,
        list_agents=lambda: list(workspace.agents.values()),
        rename_agent=rename,
        assert_contract=lambda *_args, **_kwargs: None,
    )
    assert finalized == {
        "status": "finalized",
        "supervisor_id": "signed-blue",
    }
    assert mutations == ["rename"]

    planned = control.plan_and_prepare(
        workspace,
        app_name=_APP,
        lease_id=_LEASE,
        source_git_sha=_SOURCE,
        runtime_application_id=_RUNTIME,
        canonical_name=canonical_name,
        genie_space_id="genie-space",
        catalog="mip",
        proxy_application_id="proxy-client",
        approved_query_application_ids=("app-client",),
    )
    assert planned["action"] == "create"
    assert planned["target_name"] == empty.managed_query_name
    pending = journal.download(
        workspace,
        app_name=_APP,
        runtime_application_id=_RUNTIME,
    )
    assert pending is not None
    assert pending["target_name"] == empty.managed_query_name


def test_crash_after_intent_and_each_incremental_mutation_converges() -> None:
    workspace = _Workspace()
    intent = _prepare(workspace)

    # A fresh invocation after the durable intent creates exactly the marked
    # temporary candidate, but never creates any tools before proof claim.
    created = _create(workspace, intent)
    assert workspace.tools[created["supervisor_id"]] == {}
    claimed = journal.claim(
        workspace,
        app_name=_APP,
        lease_id=_LEASE,
        source_git_sha=_SOURCE,
        runtime_application_id=_RUNTIME,
        **created,
        proof_kind="create_response",
        now=_NOW + timedelta(seconds=2),
    )

    crash_points = [
        "instructions",
        *[tool["tool_id"] for tool in __import__("json").loads(claimed["contract_json"])["tools"]],
        "display_name",
    ]
    created_payloads: dict[str, dict[str, Any]] = {}

    def update_field(supervisor_id: str, field: str, value: str) -> None:
        workspace.agents[supervisor_id][field] = value
        if crash_points and crash_points[0] == field:
            crash_points.pop(0)
            raise KeyboardInterrupt(field)

    def create_tool(
        supervisor_id: str,
        tool_id: str,
        payload: dict[str, Any],
    ) -> None:
        created_payloads[tool_id] = dict(payload)
        workspace.tools[supervisor_id][tool_id] = {
            "tool_id": tool_id,
            **payload,
        }
        if crash_points and crash_points[0] == tool_id:
            crash_points.pop(0)
            raise KeyboardInterrupt(tool_id)

    while crash_points:
        with pytest.raises(KeyboardInterrupt):
            runtime.converge_claimed(
                workspace,
                journal.download(
                    workspace,
                    app_name=_APP,
                    runtime_application_id=_RUNTIME,
                )
                or {},
                assert_single_writer=lambda: None,
                create_tool=create_tool,
                update_field=update_field,
            )

    result = runtime.converge_claimed(
        workspace,
        journal.download(
            workspace,
            app_name=_APP,
            runtime_application_id=_RUNTIME,
        )
        or {},
        assert_single_writer=lambda: None,
        create_tool=create_tool,
        update_field=update_field,
    )
    assert result["display_name"] == "Mortgage Growth Agent"
    assert len(workspace.tools[created["supervisor_id"]]) == 4
    assert created_payloads["mortgage_data_analyst"] == {
        "tool_type": "genie_space",
        "description": (
            "Answers governed data questions over the Mortgage Lead Intelligence Genie Space."
        ),
        "genie_space": {"id": "genie-space"},
    }


def _claimed_complete(workspace: _Workspace) -> dict[str, Any]:
    intent = _prepare(workspace)
    created = _create(workspace, intent)
    claimed = journal.claim(
        workspace,
        app_name=_APP,
        lease_id=_LEASE,
        source_git_sha=_SOURCE,
        runtime_application_id=_RUNTIME,
        **created,
        proof_kind="create_response",
        now=_NOW + timedelta(seconds=2),
    )
    canonical = json.loads(claimed["contract_json"])
    workspace.agents[created["supervisor_id"]]["instructions"] = canonical["instructions"]
    for tool in canonical["tools"]:
        workspace.tools[created["supervisor_id"]][tool["tool_id"]] = dict(tool)
    return claimed


def test_instruction_update_cli_preserves_signed_positional_display_name() -> None:
    workspace = _Workspace()
    claimed = _claimed_complete(workspace)
    supervisor_id = claimed["supervisor_id"]
    canonical = json.loads(claimed["contract_json"])
    workspace.agents[supervisor_id]["display_name"] = claimed["target_name"]
    workspace.agents[supervisor_id]["instructions"] = claimed["temporary_instructions"]
    commands: list[list[str]] = []

    result = field_update.update_signed_supervisor_field(
        workspace,
        claimed,
        supervisor_id,
        "instructions",
        canonical["instructions"],
        read_supervisor=runtime.supervisor_by_id,
        assert_exact_candidate=runtime.exact_journaled_candidate,
        run_cli=lambda args: commands.append(list(args)) or "updated",
    )

    assert result == "updated"
    assert commands == [
        [
            "supervisor-agents",
            "update-supervisor-agent",
            f"supervisor-agents/{supervisor_id}",
            "instructions",
            claimed["target_name"],
            "--instructions",
            canonical["instructions"],
        ]
    ]


def test_display_name_update_cli_retains_exact_positional_contract() -> None:
    workspace = _Workspace()
    claimed = _claimed_complete(workspace)
    supervisor_id = claimed["supervisor_id"]
    commands: list[list[str]] = []

    field_update.update_signed_supervisor_field(
        workspace,
        claimed,
        supervisor_id,
        "display_name",
        claimed["target_name"],
        read_supervisor=runtime.supervisor_by_id,
        assert_exact_candidate=runtime.exact_journaled_candidate,
        run_cli=lambda args: commands.append(list(args)) or "updated",
    )

    assert commands == [
        [
            "supervisor-agents",
            "update-supervisor-agent",
            f"supervisor-agents/{supervisor_id}",
            "display_name",
            claimed["target_name"],
        ]
    ]


def test_field_update_cli_rejects_unsigned_display_name_drift() -> None:
    workspace = _Workspace()
    claimed = _claimed_complete(workspace)
    supervisor_id = claimed["supervisor_id"]
    workspace.agents[supervisor_id]["display_name"] = "unsigned provider projection"
    commands: list[list[str]] = []

    with pytest.raises(RuntimeError, match="display name is outside the signed"):
        field_update.update_signed_supervisor_field(
            workspace,
            claimed,
            supervisor_id,
            "instructions",
            json.loads(claimed["contract_json"])["instructions"],
            read_supervisor=runtime.supervisor_by_id,
            assert_exact_candidate=runtime.exact_journaled_candidate,
            run_cli=lambda args: commands.append(list(args)) or "updated",
        )

    assert commands == []


def test_claimed_convergence_resumes_live_genie_alias_without_recreating_it() -> None:
    workspace = _Workspace()
    intent = _prepare(workspace)
    created = _create(workspace, intent)
    claimed = journal.claim(
        workspace,
        app_name=_APP,
        lease_id=_LEASE,
        source_git_sha=_SOURCE,
        runtime_application_id=_RUNTIME,
        **created,
        proof_kind="create_response",
        now=_NOW + timedelta(seconds=2),
    )
    tools = json.loads(claimed["contract_json"])["tools"]
    genie = dict(tools[0])
    genie["genie_space"] = {
        **genie["genie_space"],
        "space_id": genie["genie_space"]["id"],
    }
    workspace.tools[created["supervisor_id"]][genie["tool_id"]] = genie
    created_tool_ids: list[str] = []

    def create_tool(
        supervisor_id: str,
        tool_id: str,
        payload: dict[str, Any],
    ) -> None:
        created_tool_ids.append(tool_id)
        workspace.tools[supervisor_id][tool_id] = {"tool_id": tool_id, **payload}

    def update_field(supervisor_id: str, field: str, value: str) -> None:
        workspace.agents[supervisor_id][field] = value

    runtime.converge_claimed(
        workspace,
        claimed,
        assert_single_writer=lambda: None,
        create_tool=create_tool,
        update_field=update_field,
    )

    assert created_tool_ids == ["build_cohort", "segment_counts", "lead_queue_url"]


def test_successful_instruction_update_waits_for_delayed_exact_readback() -> None:
    workspace = _Workspace()
    claimed = _claimed_complete(workspace)
    supervisor_id = claimed["supervisor_id"]
    canonical = json.loads(claimed["contract_json"])
    workspace.agents[supervisor_id]["instructions"] = claimed["temporary_instructions"]
    workspace.agents[supervisor_id]["display_name"] = claimed["target_name"]
    mutations: list[str] = []
    lease_checks = 0

    def assert_single_writer() -> None:
        nonlocal lease_checks
        lease_checks += 1

    def update_field(_supervisor_id: str, field: str, _value: str) -> None:
        assert field == "instructions"
        mutations.append(field)

    def publish_delayed_readback(_seconds: float) -> None:
        workspace.agents[supervisor_id]["instructions"] = canonical["instructions"]

    result = runtime.converge_claimed(
        workspace,
        claimed,
        assert_single_writer=assert_single_writer,
        create_tool=lambda *_args, **_kwargs: None,
        update_field=update_field,
        sleep=publish_delayed_readback,
    )

    assert result["display_name"] == claimed["target_name"]
    assert mutations == ["instructions"]
    assert lease_checks >= 4


def test_ambiguous_field_updates_poll_without_reissuing_mutations() -> None:
    workspace = _Workspace()
    claimed = _claimed_complete(workspace)
    supervisor_id = claimed["supervisor_id"]
    canonical = json.loads(claimed["contract_json"])
    workspace.agents[supervisor_id]["instructions"] = claimed["temporary_instructions"]
    mutations: list[str] = []

    def ambiguous_update(
        _supervisor_id: str,
        field: str,
        _value: str,
    ) -> None:
        mutations.append(field)
        raise TimeoutError("provider response was lost after submission")

    def publish_delayed_readback(_seconds: float) -> None:
        if workspace.agents[supervisor_id]["instructions"] == claimed["temporary_instructions"]:
            workspace.agents[supervisor_id]["instructions"] = canonical["instructions"]
        else:
            workspace.agents[supervisor_id]["display_name"] = claimed["target_name"]

    result = runtime.converge_claimed(
        workspace,
        claimed,
        assert_single_writer=lambda: None,
        create_tool=lambda *_args, **_kwargs: None,
        update_field=ambiguous_update,
        sleep=publish_delayed_readback,
    )

    assert result["display_name"] == claimed["target_name"]
    assert mutations == ["instructions", "display_name"]


def test_instruction_update_never_converges_without_blind_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _Workspace()
    claimed = _claimed_complete(workspace)
    supervisor_id = claimed["supervisor_id"]
    workspace.agents[supervisor_id]["instructions"] = claimed["temporary_instructions"]
    mutations: list[str] = []
    sleeps: list[float] = []
    monkeypatch.setattr(runtime, "_FIELD_READBACK_ATTEMPTS", 3)

    with pytest.raises(
        RuntimeError,
        match=r"instructions readback did not converge.*provider_result=accepted",
    ):
        runtime.converge_claimed(
            workspace,
            claimed,
            assert_single_writer=lambda: None,
            create_tool=lambda *_args, **_kwargs: None,
            update_field=lambda *_args: mutations.append("instructions"),
            sleep=sleeps.append,
        )

    assert mutations == ["instructions"]
    assert sleeps == [runtime._FIELD_READBACK_INTERVAL_S] * 2


def test_instruction_readback_stops_immediately_when_lease_is_lost() -> None:
    workspace = _Workspace()
    claimed = _claimed_complete(workspace)
    supervisor_id = claimed["supervisor_id"]
    workspace.agents[supervisor_id]["instructions"] = claimed["temporary_instructions"]
    lease_checks = 0
    mutations = 0

    def assert_single_writer() -> None:
        nonlocal lease_checks
        lease_checks += 1
        if lease_checks == 3:
            raise RuntimeError("deployment lease changed")

    def update_field(*_args: object) -> None:
        nonlocal mutations
        mutations += 1

    with pytest.raises(RuntimeError, match="deployment lease changed"):
        runtime.converge_claimed(
            workspace,
            claimed,
            assert_single_writer=assert_single_writer,
            create_tool=lambda *_args, **_kwargs: None,
            update_field=update_field,
            sleep=lambda _seconds: None,
        )

    assert mutations == 1
    assert lease_checks == 3


def test_successful_instruction_readback_revalidates_lease_before_return() -> None:
    workspace = _Workspace()
    claimed = _claimed_complete(workspace)
    lease_checks = 0

    def assert_single_writer() -> None:
        nonlocal lease_checks
        lease_checks += 1
        if lease_checks == 2:
            raise RuntimeError("deployment lease changed during provider read")

    with pytest.raises(RuntimeError, match="changed during provider read"):
        runtime._await_field_readback(
            workspace,
            claimed,
            field="instructions",
            previous=claimed["temporary_instructions"],
            expected=json.loads(claimed["contract_json"])["instructions"],
            mutation_outcome="preexisting",
            assert_single_writer=assert_single_writer,
            sleep=lambda _seconds: pytest.fail("exact readback must not poll"),
        )
    assert lease_checks == 2


def test_successful_instruction_readback_revalidates_journal_before_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _Workspace()
    claimed = _claimed_complete(workspace)
    journal_checks = 0
    original_assert = runtime._assert_unchanged_journal

    def assert_available(current_workspace: Any, record: dict[str, Any]) -> None:
        nonlocal journal_checks
        journal_checks += 1
        if journal_checks == 2:
            raise RuntimeError("journal became unavailable during provider read")
        original_assert(current_workspace, record)

    monkeypatch.setattr(runtime, "_assert_unchanged_journal", assert_available)
    with pytest.raises(RuntimeError, match="became unavailable during provider read"):
        runtime._await_field_readback(
            workspace,
            claimed,
            field="instructions",
            previous=claimed["temporary_instructions"],
            expected=json.loads(claimed["contract_json"])["instructions"],
            mutation_outcome="preexisting",
            assert_single_writer=lambda: None,
            sleep=lambda _seconds: pytest.fail("exact readback must not poll"),
        )
    assert journal_checks == 2


def test_instruction_update_readback_rejects_non_journaled_drift() -> None:
    workspace = _Workspace()
    claimed = _claimed_complete(workspace)
    supervisor_id = claimed["supervisor_id"]
    workspace.agents[supervisor_id]["instructions"] = claimed["temporary_instructions"]

    def drift(supervisor_id: str, field: str, _value: str) -> None:
        workspace.agents[supervisor_id][field] = "unreviewed provider projection"

    with pytest.raises(
        RuntimeError,
        match="instructions readback drifted outside the signed",
    ):
        runtime.converge_claimed(
            workspace,
            claimed,
            assert_single_writer=lambda: None,
            create_tool=lambda *_args, **_kwargs: None,
            update_field=drift,
            sleep=lambda _seconds: pytest.fail("drift must fail without polling"),
        )


def test_successful_rename_waits_for_direct_and_inventory_consistency() -> None:
    workspace = _Workspace()
    claimed = _claimed_complete(workspace)
    supervisor_id = claimed["supervisor_id"]
    rename_calls = 0
    sleeps = 0

    def delayed_rename(
        _supervisor_id: str,
        field: str,
        _value: str,
    ) -> None:
        nonlocal rename_calls
        assert field == "display_name"
        rename_calls += 1

    def publish_delayed_readback(_seconds: float) -> None:
        nonlocal sleeps
        sleeps += 1
        workspace.agents[supervisor_id]["display_name"] = claimed["target_name"]

    result = runtime.converge_claimed(
        workspace,
        claimed,
        assert_single_writer=lambda: None,
        create_tool=lambda *_args, **_kwargs: None,
        update_field=delayed_rename,
        sleep=publish_delayed_readback,
    )

    assert result["display_name"] == claimed["target_name"]
    assert rename_calls == 1
    assert sleeps == 1
    assert runtime.assert_unique_target_claim(workspace, claimed)["supervisor_agent_id"] == (
        supervisor_id
    )


def test_retry_waits_for_lagging_inventory_after_direct_rename() -> None:
    workspace = _Workspace()
    claimed = _claimed_complete(workspace)
    supervisor_id = claimed["supervisor_id"]
    workspace.agents[supervisor_id]["display_name"] = claimed["target_name"]
    live_api = workspace.api_client
    inventory_reads = 0
    sleeps = 0

    class _LaggingInventoryApi:
        def do(
            self,
            method: str,
            path: str,
            *,
            query: dict[str, Any] | None = None,
        ) -> Any:
            nonlocal inventory_reads
            payload = live_api.do(method, path, query=query)
            if path != "/api/2.1/supervisor-agents":
                return payload
            inventory_reads += 1
            if inventory_reads > 8:
                return payload
            rows = [dict(row) for row in payload["supervisor_agents"]]
            rows[0]["display_name"] = claimed["temporary_name"]
            return {"supervisor_agents": rows}

    workspace.api_client = _LaggingInventoryApi()

    def observe_poll(_seconds: float) -> None:
        nonlocal sleeps
        sleeps += 1

    result = runtime.converge_claimed(
        workspace,
        claimed,
        assert_single_writer=lambda: None,
        create_tool=lambda *_args, **_kwargs: None,
        update_field=lambda *_args: pytest.fail("canonical rename must not be reissued"),
        sleep=observe_poll,
    )

    assert result["supervisor_id"] == supervisor_id
    assert sleeps == 1
    assert inventory_reads > 8


def test_claimed_journal_clear_resolves_committed_and_uncommitted_timeouts() -> None:
    workspace = _Workspace()
    claimed = _claimed_complete(workspace)
    original_delete = workspace.workspace.delete

    def timeout_without_commit(_path: str) -> None:
        raise TimeoutError("request never committed")

    workspace.workspace.delete = timeout_without_commit  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="deletion did not converge"):
        journal.clear(
            workspace,
            app_name=_APP,
            lease_id=_LEASE,
            source_git_sha=_SOURCE,
            runtime_application_id=_RUNTIME,
            expected=claimed,
        )
    assert (
        journal.download(
            workspace,
            app_name=_APP,
            runtime_application_id=_RUNTIME,
        )
        == claimed
    )

    def commit_then_timeout(path: str) -> None:
        original_delete(path)
        raise TimeoutError("response lost after commit")

    workspace.workspace.delete = commit_then_timeout  # type: ignore[method-assign]
    journal.clear(
        workspace,
        app_name=_APP,
        lease_id=_LEASE,
        source_git_sha=_SOURCE,
        runtime_application_id=_RUNTIME,
        expected=claimed,
    )
    assert (
        journal.download(
            workspace,
            app_name=_APP,
            runtime_application_id=_RUNTIME,
        )
        is None
    )


def test_ambiguous_target_rename_commit_resolves_only_exact_singleton() -> None:
    workspace = _Workspace()
    claimed = _claimed_complete(workspace)

    def commit_then_timeout(supervisor_id: str, field: str, value: str) -> None:
        workspace.agents[supervisor_id][field] = value
        raise TimeoutError("provider response lost")

    result = runtime.converge_claimed(
        workspace,
        claimed,
        assert_single_writer=lambda: None,
        create_tool=lambda *_args, **_kwargs: None,
        update_field=commit_then_timeout,
    )

    assert result["supervisor_id"] == claimed["supervisor_id"]
    assert (
        runtime.assert_unique_target_claim(workspace, claimed)["supervisor_agent_id"]
        == (claimed["supervisor_id"])
    )


def test_target_collision_during_rename_fails_runtime_postflight() -> None:
    workspace = _Workspace()
    claimed = _claimed_complete(workspace)

    def rename_with_collision(supervisor_id: str, field: str, value: str) -> None:
        workspace.agents["intruder"] = {
            "supervisor_agent_id": "intruder",
            "display_name": value,
            "instructions": "foreign",
            "description": "foreign",
            "endpoint_name": "intruder-endpoint",
            "creator": "foreign",
            "create_time": "2026-07-25T12:00:03Z",
        }
        workspace.tools["intruder"] = {}
        workspace.agents[supervisor_id][field] = value

    with pytest.raises(RuntimeError, match="canonical target became occupied"):
        runtime.converge_claimed(
            workspace,
            claimed,
            assert_single_writer=lambda: None,
            create_tool=lambda *_args, **_kwargs: None,
            update_field=rename_with_collision,
        )
    assert (
        journal.download(
            workspace,
            app_name=_APP,
            runtime_application_id=_RUNTIME,
        )
        == claimed
    )


def test_unique_target_claim_scans_every_inventory_page() -> None:
    target = "Mortgage Growth Agent"

    class _PagedApi:
        def do(
            self,
            method: str,
            path: str,
            *,
            query: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            assert method == "GET"
            assert path == "/api/2.1/supervisor-agents"
            if query == {"page_size": 100}:
                return {
                    "supervisor_agents": [
                        {
                            "supervisor_agent_id": "claimed",
                            "display_name": target,
                        }
                    ],
                    "next_page_token": "second-page",
                }
            assert query == {"page_size": 100, "page_token": "second-page"}
            return {
                "supervisor_agents": [
                    {
                        "supervisor_agent_id": "intruder",
                        "display_name": target,
                    }
                ]
            }

    workspace = SimpleNamespace(api_client=_PagedApi())
    with pytest.raises(RuntimeError, match="absent, duplicated, or bound"):
        runtime.assert_unique_target_claim(
            workspace,
            {"supervisor_id": "claimed", "target_name": target},
        )


def test_full_postflight_rejects_duplicate_target_before_journal_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _Workspace()
    claimed = _claimed_complete(workspace)
    workspace.agents[claimed["supervisor_id"]]["display_name"] = claimed["target_name"]
    workspace.agents["intruder"] = {
        "supervisor_agent_id": "intruder",
        "display_name": claimed["target_name"],
        "instructions": "foreign",
        "description": "foreign",
        "endpoint_name": "intruder-endpoint",
        "creator": "foreign",
        "create_time": "2026-07-25T12:00:03Z",
    }
    workspace.tools["intruder"] = {}
    cleared = False

    def clear(*_args: object, **_kwargs: object) -> None:
        nonlocal cleared
        cleared = True

    monkeypatch.setattr(journal, "clear", clear)
    with pytest.raises(RuntimeError, match="absent, duplicated, or bound"):
        control.complete_and_clear(workspace, claimed)
    assert cleared is False


def test_complete_verification_retains_signed_handoff_journal() -> None:
    workspace = _Workspace()
    claimed = _claimed_complete(workspace)
    workspace.agents[claimed["supervisor_id"]]["display_name"] = claimed["target_name"]

    control.verify_complete(workspace, claimed)

    assert (
        journal.download(
            workspace,
            app_name=_APP,
            runtime_application_id=_RUNTIME,
        )
        == claimed
    )


@pytest.mark.parametrize("child", ("tools", "examples"))
def test_complete_rejects_bare_child_list_without_clearing(child: str) -> None:
    workspace = _Workspace()
    claimed = _claimed_complete(workspace)
    workspace.agents[claimed["supervisor_id"]]["display_name"] = claimed["target_name"]
    if child == "tools":
        workspace.tool_payload = list(workspace.tools[claimed["supervisor_id"]].values())
    else:
        workspace.example_payload = []

    with pytest.raises(RuntimeError, match=f"{child[:-1]} inventory is malformed"):
        control.complete_and_clear(workspace, claimed)

    assert (
        journal.download(
            workspace,
            app_name=_APP,
            runtime_application_id=_RUNTIME,
        )
        == claimed
    )


def test_complete_clears_after_exact_paginated_child_inventories() -> None:
    workspace = _Workspace()
    claimed = _claimed_complete(workspace)
    workspace.agents[claimed["supervisor_id"]]["display_name"] = claimed["target_name"]
    tools = list(workspace.tools[claimed["supervisor_id"]].values())

    def tool_pages(query: dict[str, Any]) -> object:
        if query == {"page_size": 100}:
            return {"tools": tools[:2], "next_page_token": "second-page"}
        assert query == {"page_size": 100, "page_token": "second-page"}
        return {"tools": tools[2:]}

    def example_pages(query: dict[str, Any]) -> object:
        if query == {"page_size": 100}:
            return {"examples": [], "next_page_token": "second-page"}
        assert query == {"page_size": 100, "page_token": "second-page"}
        return {"examples": []}

    workspace.tool_payload = tool_pages
    workspace.example_payload = example_pages

    control.complete_and_clear(workspace, claimed)

    assert (
        journal.download(
            workspace,
            app_name=_APP,
            runtime_application_id=_RUNTIME,
        )
        is None
    )


def test_complete_clears_with_live_duplicate_genie_space_identifier() -> None:
    workspace = _Workspace()
    claimed = _claimed_complete(workspace)
    supervisor_id = claimed["supervisor_id"]
    workspace.agents[supervisor_id]["display_name"] = claimed["target_name"]
    genie = workspace.tools[supervisor_id]["mortgage_data_analyst"]["genie_space"]
    genie["space_id"] = genie["id"]

    control.complete_and_clear(workspace, claimed)

    assert (
        journal.download(
            workspace,
            app_name=_APP,
            runtime_application_id=_RUNTIME,
        )
        is None
    )


def test_complete_rejects_conflicting_genie_alias_without_clearing() -> None:
    workspace = _Workspace()
    claimed = _claimed_complete(workspace)
    supervisor_id = claimed["supervisor_id"]
    workspace.agents[supervisor_id]["display_name"] = claimed["target_name"]
    workspace.tools[supervisor_id]["mortgage_data_analyst"]["genie_space"]["space_id"] = (
        "other-space"
    )

    with pytest.raises(RuntimeError, match="mortgage_data_analyst.*drifted"):
        control.complete_and_clear(workspace, claimed)

    assert (
        journal.download(
            workspace,
            app_name=_APP,
            runtime_application_id=_RUNTIME,
        )
        == claimed
    )


def test_claimed_retry_survives_cleanup_binding_handoff_then_clears() -> None:
    workspace = _Workspace()
    claimed = _claimed_complete(workspace)
    workspace.agents[claimed["supervisor_id"]]["display_name"] = claimed["target_name"]

    control.verify_complete(workspace, claimed)
    cleanup_inventory = historical.RuntimeEndpointInventory(
        version=1,
        runtime_application_id=_RUNTIME,
        gateways=(),
        supervisors=(),
        pending_supervisor_cleanup=None,
        pending_supervisor_creation=claimed,
    )
    assert historical.cleanup_postflight_is_complete(cleanup_inventory)
    endpoint_id = runtime.assert_unique_live_supervisor_binding(
        workspace,
        supervisor_id=claimed["supervisor_id"],
        display_name=claimed["target_name"],
        endpoint=claimed["endpoint"],
        runtime_application_id=_RUNTIME,
    )
    assert endpoint_id == claimed["endpoint_id"]

    control.complete_and_clear(workspace, claimed)

    assert (
        journal.download(
            workspace,
            app_name=_APP,
            runtime_application_id=_RUNTIME,
        )
        is None
    )


def test_ambiguous_create_commit_is_never_reissued_by_runtime() -> None:
    workspace = _Workspace()
    intent = _prepare(workspace)
    create_calls = 0

    def commit_then_timeout(payload: dict[str, str]) -> dict[str, str]:
        nonlocal create_calls
        create_calls += 1
        supervisor_id = "ambiguous-supervisor"
        workspace.agents[supervisor_id] = {
            "supervisor_agent_id": supervisor_id,
            **payload,
            "endpoint_name": "ambiguous-endpoint",
            "creator": _RUNTIME,
            "create_time": "2026-07-25T12:00:01Z",
        }
        workspace.tools[supervisor_id] = {}
        raise TimeoutError("provider response lost")

    with pytest.raises(RuntimeError, match="audit recovery"):
        runtime.create_from_intent(
            workspace,
            intent,
            assert_single_writer=lambda: None,
            create=commit_then_timeout,
            now=_NOW,
        )
    with pytest.raises(RuntimeError, match="audit recovery"):
        runtime.create_from_intent(
            workspace,
            intent,
            assert_single_writer=lambda: None,
            create=commit_then_timeout,
            now=_NOW,
        )
    assert create_calls == 1


def test_unclaimed_intent_clears_only_after_settled_negative_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _Workspace()
    intent = _prepare(workspace)
    absence_reads = 0
    cleared = False

    def absent(*_args: object, **_kwargs: object) -> None:
        nonlocal absence_reads
        absence_reads += 1

    def no_event(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("Supervisor create audit proof is not available yet")

    def clear_absent(*_args: object, **kwargs: object) -> None:
        nonlocal cleared
        assert_live_absent = kwargs["assert_live_absent"]
        assert callable(assert_live_absent)
        assert_live_absent()
        cleared = True

    monkeypatch.setattr(control, "_assert_intent_live_absent", absent)
    monkeypatch.setattr(control, "find_supervisor_create_proof", no_event)
    monkeypatch.setattr(journal, "clear_absent_intent", clear_absent)
    with pytest.raises(RuntimeError, match="settlement remains open"):
        control.abandon_settled_absent(
            workspace,
            intent,
            warehouse_id="warehouse",
            now=_NOW,
        )
    control.abandon_settled_absent(
        workspace,
        intent,
        warehouse_id="warehouse",
        now=datetime.fromisoformat(intent["audit_settlement_until"]) + timedelta(seconds=1),
    )
    assert cleared is True
    assert absence_reads == 3


def test_absence_check_finds_instruction_marker_after_display_name_changes() -> None:
    workspace = _Workspace()
    intent = _prepare(workspace)
    created = _create(workspace, intent)
    workspace.agents[created["supervisor_id"]]["display_name"] = "renamed elsewhere"

    with pytest.raises(RuntimeError, match="still has a live candidate"):
        control._assert_intent_live_absent(workspace, intent)


def test_full_postflight_rejects_temporary_instructions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _Workspace()
    intent = _prepare(workspace)
    created = _create(workspace, intent)
    claimed = journal.claim(
        workspace,
        app_name=_APP,
        lease_id=_LEASE,
        source_git_sha=_SOURCE,
        runtime_application_id=_RUNTIME,
        **created,
        proof_kind="create_response",
        now=_NOW + timedelta(seconds=2),
    )
    workspace.agents[created["supervisor_id"]]["display_name"] = claimed["target_name"]
    for tool in json.loads(claimed["contract_json"])["tools"]:
        workspace.tools[created["supervisor_id"]][tool["tool_id"]] = dict(tool)
    cleared = False

    def clear(*_args: object, **_kwargs: object) -> None:
        nonlocal cleared
        cleared = True

    monkeypatch.setattr(journal, "clear", clear)
    with pytest.raises(RuntimeError, match="full postflight is incomplete"):
        control.complete_and_clear(workspace, claimed)
    assert cleared is False
