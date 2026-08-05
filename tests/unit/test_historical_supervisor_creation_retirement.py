from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from tools.databricks import historical_agent_endpoint_cleanup as cleanup
from tools.databricks import historical_supervisor_creation_retirement as retirement
from tools.databricks.historical_agent_endpoint_types import (
    QueryGroupPrincipals,
    ReviewedSupervisor,
    RuntimeEndpointInventory,
    SupervisorCleanupProof,
)


def _proof() -> SupervisorCleanupProof:
    return SupervisorCleanupProof(
        app_name="mip-app",
        lease_id="11111111-1111-4111-8111-111111111111",
        source_git_sha="b" * 40,
        runtime_application_id="runtime-client",
        supervisor_id="historical-supervisor",
        endpoint="historical-endpoint",
        endpoint_id="historical-endpoint-id",
        creator="runtime-client",
    )


def _record() -> dict[str, Any]:
    return {
        "disposition": "retire_only",
        "supervisor_id": "historical-supervisor",
        "endpoint": "historical-endpoint",
        "endpoint_id": "historical-endpoint-id",
        "creator": "runtime-client",
    }


class _Delegate:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.pending: SupervisorCleanupProof | None = None

    def read(self) -> SupervisorCleanupProof | None:
        return self.pending

    def proof_for(
        self,
        supervisor: ReviewedSupervisor,
        *,
        runtime_application_id: str,
    ) -> SupervisorCleanupProof:
        del supervisor, runtime_application_id
        return _proof()

    def stage(self, proof: SupervisorCleanupProof) -> None:
        self.events.append("cleanup-proof-staged")
        if self.pending not in {None, proof}:
            raise RuntimeError("occupied")
        self.pending = proof

    def clear(
        self,
        proof: SupervisorCleanupProof,
        *,
        assert_resources_absent: Callable[[], None],
    ) -> None:
        assert_resources_absent()
        assert self.pending == proof
        self.events.append("cleanup-proof-cleared")
        self.pending = None


def _wrapper(delegate: _Delegate) -> retirement.CreationRetirementCleanupJournal:
    return retirement.CreationRetirementCleanupJournal(
        object(),
        delegate,
        app_name="mip-app",
        lease_id=_proof().lease_id,
        source_git_sha="b" * 40,
        runtime_application_id="runtime-client",
        canonical_name="Successor Agent",
        genie_space_id="successor-space",
        catalog="successor",
    )


def test_retire_only_handoff_stages_sink_before_source_clear_and_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    state = {"record": _record()}
    delegate = _Delegate(events)
    wrapper = _wrapper(delegate)
    monkeypatch.setattr(
        retirement.creation,
        "download",
        lambda *_args, **_kwargs: state["record"],
    )
    monkeypatch.setattr(
        retirement.creation,
        "matches_current_policy",
        lambda *_args, **_kwargs: False,
    )

    def clear(*_args: object, expected: dict[str, Any], **_kwargs: object) -> None:
        assert expected == state["record"]
        events.append("creation-proof-cleared")
        state["record"] = None

    monkeypatch.setattr(retirement.creation, "clear", clear)

    wrapper.stage(_proof())
    events.append("resource-deleted")
    wrapper.clear(_proof(), assert_resources_absent=lambda: None)

    assert events == [
        "cleanup-proof-staged",
        "creation-proof-cleared",
        "resource-deleted",
        "cleanup-proof-cleared",
    ]
    assert state["record"] is None


def test_source_clear_failure_leaves_cleanup_sink_for_exact_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    state = {"record": _record()}
    delegate = _Delegate(events)
    wrapper = _wrapper(delegate)
    monkeypatch.setattr(
        retirement.creation,
        "download",
        lambda *_args, **_kwargs: state["record"],
    )
    monkeypatch.setattr(
        retirement.creation,
        "matches_current_policy",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        retirement.creation,
        "clear",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("source unchanged")),
    )

    with pytest.raises(RuntimeError, match="source unchanged"):
        wrapper.stage(_proof())
    assert delegate.pending == _proof()
    assert state["record"] is not None
    assert events == ["cleanup-proof-staged"]

    monkeypatch.setattr(
        retirement.creation,
        "clear",
        lambda *_args, **_kwargs: state.update(record=None),
    )
    wrapper.stage(_proof())
    assert state["record"] is None
    assert events == ["cleanup-proof-staged"]


def test_existing_cleanup_sink_completes_cross_deployment_source_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    state = {"record": _record()}
    delegate = _Delegate(events)
    historical_proof = SupervisorCleanupProof(
        **{
            **_proof().__dict__,
            "lease_id": "22222222-2222-4222-8222-222222222222",
            "source_git_sha": "a" * 40,
        }
    )
    delegate.pending = historical_proof
    wrapper = _wrapper(delegate)
    monkeypatch.setattr(
        retirement.creation,
        "download",
        lambda *_args, **_kwargs: state["record"],
    )
    monkeypatch.setattr(
        retirement.creation,
        "matches_current_policy",
        lambda *_args, **_kwargs: False,
    )

    def clear(*_args: object, expected: dict[str, Any], **_kwargs: object) -> None:
        assert expected == state["record"]
        events.append("creation-proof-cleared")
        state["record"] = None

    monkeypatch.setattr(retirement.creation, "clear", clear)

    wrapper.stage(historical_proof)

    assert events == ["creation-proof-cleared"]
    assert delegate.pending == historical_proof
    assert state["record"] is None


@pytest.mark.parametrize(
    ("supervisor_live", "endpoint_live"),
    [(True, True), (False, True), (False, False)],
)
def test_occupied_sink_retry_clears_source_before_any_remaining_resource(
    monkeypatch: pytest.MonkeyPatch,
    supervisor_live: bool,
    endpoint_live: bool,
) -> None:
    events: list[str] = []
    state: dict[str, Any] = {
        "record": _record(),
        "supervisor_live": supervisor_live,
        "endpoint_live": endpoint_live,
    }
    historical_proof = SupervisorCleanupProof(
        **{
            **_proof().__dict__,
            "lease_id": "22222222-2222-4222-8222-222222222222",
            "source_git_sha": "a" * 40,
        }
    )
    delegate = _Delegate(events)
    delegate.pending = historical_proof
    wrapper = _wrapper(delegate)
    monkeypatch.setattr(
        retirement.creation,
        "download",
        lambda *_args, **_kwargs: state["record"],
    )
    monkeypatch.setattr(
        retirement.creation,
        "matches_current_policy",
        lambda *_args, **_kwargs: False,
    )

    def clear_source(
        *_args: object,
        lease_id: str,
        source_git_sha: str,
        expected: dict[str, Any],
        **_kwargs: object,
    ) -> None:
        assert lease_id == _proof().lease_id
        assert source_git_sha == "b" * 40
        assert expected == state["record"]
        events.append("creation-proof-cleared")
        state["record"] = None

    monkeypatch.setattr(retirement.creation, "clear", clear_source)
    monkeypatch.setattr(
        cleanup,
        "_supervisor_exact",
        lambda *_args, **_kwargs: (object() if state["supervisor_live"] else None),
    )
    monkeypatch.setattr(
        cleanup,
        "_endpoint_exact_or_absent",
        lambda *_args, **_kwargs: bool(state["endpoint_live"]),
    )
    monkeypatch.setattr(
        cleanup,
        "_retire_live_endpoint_query_groups",
        lambda *_args, **_kwargs: events.append("query-groups-retired"),
    )
    monkeypatch.setattr(cleanup, "_wait_supervisor_absent", lambda *_args, **_kwargs: None)

    def delete_endpoint(*_args: object, **_kwargs: object) -> None:
        events.append("endpoint-deleted")
        state["endpoint_live"] = False

    monkeypatch.setattr(cleanup, "_delete_endpoint_exact", delete_endpoint)

    def assert_absent(*_args: object, **_kwargs: object) -> None:
        assert not state["supervisor_live"]
        assert not state["endpoint_live"]

    monkeypatch.setattr(cleanup, "_assert_supervisor_resources_absent", assert_absent)

    class _Api:
        @staticmethod
        def do(method: str, _path: str) -> None:
            assert method == "DELETE"
            events.append("supervisor-deleted")
            state["supervisor_live"] = False

    client = SimpleNamespace(api_client=_Api())
    initial = RuntimeEndpointInventory(
        1,
        "runtime-client",
        (),
        (),
        pending_supervisor_cleanup=historical_proof,
        pending_supervisor_creation=_record(),
    )
    empty = RuntimeEndpointInventory(1, "runtime-client", (), ())
    reads = iter((initial, empty, empty))

    result = cleanup.cleanup_runtime_endpoints(
        client,
        initial,
        app_name="mip-app",
        assert_single_writer=lambda: None,
        query_principals=QueryGroupPrincipals(
            "app-client",
            "app-scim",
            "verifier-client",
            "verifier-scim",
            "proxy-client",
            "proxy-scim",
        ),
        timeout_s=1,
        inventory_again=lambda: next(reads),
        cleanup_journal=wrapper,
        sleep=lambda _seconds: None,
    )

    assert result == empty
    assert events[0] == "creation-proof-cleared"
    assert events[-1] == "cleanup-proof-cleared"
    assert state["record"] is None
    assert delegate.pending is None


def test_current_creation_never_stages_historical_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    delegate = _Delegate(events)
    wrapper = _wrapper(delegate)
    monkeypatch.setattr(
        retirement.creation,
        "download",
        lambda *_args, **_kwargs: {**_record(), "disposition": "active"},
    )
    monkeypatch.setattr(
        retirement.creation,
        "matches_current_policy",
        lambda *_args, **_kwargs: True,
    )

    with pytest.raises(RuntimeError, match="current Supervisor creation"):
        wrapper.stage(_proof())
    assert delegate.pending is None
    assert events == []
