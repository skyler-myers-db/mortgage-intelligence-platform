from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from tests.unit.test_reconcile_historical_agent_endpoints import (
    _RUNTIME,
    _SUPERVISOR_NAME,
    _cleanup_proof,
    _Client,
    _inventory,
    _MemoryCleanupJournal,
    _supervisor,
)
from tools.databricks import historical_agent_endpoint_cleanup as cleanup
from tools.databricks import reconcile_historical_agent_endpoints as inventory
from tools.databricks.app_rollback_secret_scope import (
    MARKER_KEY,
    AppRollbackScopeBinding,
)
from tools.databricks.historical_supervisor_cleanup_journal import (
    HistoricalSupervisorCleanupJournal,
)


@pytest.fixture(autouse=True)
def _legacy_query_group_migration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cleanup,
        "inspect_claimed_managed_query_group",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            cleanup.MissingClaimedGroupProvenanceError(
                "fixture exercises the exact pre-provenance migration path"
            )
        ),
    )
    monkeypatch.setattr(
        cleanup.group_provenance,
        "read_existing",
        lambda *_args, **_kwargs: None,
    )


class _JournalSecrets:
    def __init__(self) -> None:
        binding = AppRollbackScopeBinding(
            app_name="mip-app",
            scope="mip-app-rollback",
            deployer_principal="deployer@example.com",
        )
        self.values = {MARKER_KEY: binding.canonical_json()}

    def list_scopes(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name="mip-app-rollback")]

    def list_acls(self, *, scope: str) -> list[SimpleNamespace]:
        assert scope == "mip-app-rollback"
        return [
            SimpleNamespace(principal="admins", permission="MANAGE"),
            SimpleNamespace(principal="deployer@example.com", permission="MANAGE"),
        ]

    def list_secrets(self, *, scope: str) -> list[SimpleNamespace]:
        assert scope == "mip-app-rollback"
        return [SimpleNamespace(key=key) for key in sorted(self.values)]

    def get_secret(self, scope: str, key: str) -> SimpleNamespace:
        assert scope == "mip-app-rollback"
        return SimpleNamespace(
            value=base64.b64encode(self.values[key].encode()).decode()
        )

    def put_secret(
        self,
        *,
        scope: str,
        key: str,
        string_value: str,
    ) -> None:
        assert scope == "mip-app-rollback"
        self.values[key] = string_value

    def delete_secret(self, scope: str, key: str) -> None:
        assert scope == "mip-app-rollback"
        del self.values[key]


def _journal(
    client: _Client,
    *,
    lease_id: str,
    source_git_sha: str,
    lease_checks: list[str],
) -> HistoricalSupervisorCleanupJournal:
    if not hasattr(client, "secrets"):
        client.secrets = _JournalSecrets()  # type: ignore[attr-defined]
        client.current_user = SimpleNamespace(  # type: ignore[attr-defined]
            me=lambda: SimpleNamespace(
                user_name="deployer@example.com",
                application_id="",
            )
        )
    return HistoricalSupervisorCleanupJournal(
        client,
        app_name="mip-app",
        scope="mip-app-rollback",
        lease_id=lease_id,
        source_git_sha=source_git_sha,
        runtime_application_id=_RUNTIME,
        assert_single_writer=lambda: lease_checks.append(lease_id),
    )


@pytest.mark.parametrize(
    "delete_agent_before_restart",
    [False, True],
    ids=["journal-staged-agent-live", "agent-deleted-endpoint-live"],
)
def test_cleanup_journal_recovers_in_fresh_process_under_new_lease(
    delete_agent_before_restart: bool,
) -> None:
    supervisor = _supervisor(
        supervisor_id="cross-process-supervisor",
        display_name=f"{_SUPERVISOR_NAME} [mip-agent-runtime-deadbeef0000]",
        endpoint="cross-process-supervisor-endpoint",
    )
    endpoint_id = "cross-process-endpoint-id"
    client = _Client(
        {
            supervisor["endpoint_name"]: SimpleNamespace(
                id=endpoint_id,
                creator=_RUNTIME,
            ),
        },
        [supervisor],
    )
    first_checks: list[str] = []
    first = _journal(
        client,
        lease_id="11111111-1111-4111-8111-111111111111",
        source_git_sha="a" * 40,
        lease_checks=first_checks,
    )
    proof = first.proof_for(
        _inventory(client).supervisors[0],
        runtime_application_id=_RUNTIME,
    )
    first.stage(proof)
    if delete_agent_before_restart:
        client.api_client.delete_endpoint_with_agent = False
        client.api_client.do(
            "DELETE",
            f"/api/2.1/supervisor-agents/{supervisor['supervisor_agent_id']}",
        )

    second_checks: list[str] = []
    second = _journal(
        client,
        lease_id="22222222-2222-4222-8222-222222222222",
        source_git_sha="b" * 40,
        lease_checks=second_checks,
    )
    assert second.read() == proof

    def read() -> inventory.RuntimeEndpointInventory:
        return _inventory(
            client,
            pending_cleanup=second.read(),
        )

    result = inventory.cleanup_runtime_endpoints(
        client,
        read(),
        app_name="mip-app",
        assert_single_writer=lambda: second_checks.append("resource-delete"),
        query_principals=inventory.QueryGroupPrincipals(
            "app-client",
            "app-scim",
            "verifier-client",
            "verifier-scim",
            "proxy-client",
            "proxy-scim",
        ),
        timeout_s=1,
        sleep=lambda _seconds: None,
        inventory_again=read,
        cleanup_journal=second,
    )

    assert result == inventory.RuntimeEndpointInventory(1, _RUNTIME, (), ())
    assert second.read() is None
    assert client.api_client.supervisors == {}
    assert client.serving_endpoints.details == {}
    assert "11111111-1111-4111-8111-111111111111" in first_checks
    assert "22222222-2222-4222-8222-222222222222" in second_checks


def test_cleanup_journal_rejects_other_runtime_identity() -> None:
    supervisor = _supervisor()
    client = _Client(
        {
            supervisor["endpoint_name"]: SimpleNamespace(
                id="endpoint-id",
                creator=_RUNTIME,
            ),
        },
        [supervisor],
    )
    first = _journal(
        client,
        lease_id="11111111-1111-4111-8111-111111111111",
        source_git_sha="a" * 40,
        lease_checks=[],
    )
    first.stage(
        first.proof_for(
            _inventory(client).supervisors[0],
            runtime_application_id=_RUNTIME,
        )
    )

    foreign = HistoricalSupervisorCleanupJournal(
        client,
        app_name="mip-app",
        scope="mip-app-rollback",
        lease_id="22222222-2222-4222-8222-222222222222",
        source_git_sha="b" * 40,
        runtime_application_id="different-runtime",
        assert_single_writer=lambda: None,
    )

    with pytest.raises(RuntimeError, match="different deployment"):
        foreign.read()


def test_cleanup_rechecks_endpoint_tuple_after_lease_before_endpoint_delete() -> None:
    endpoint_name = "historical-endpoint"
    client = _Client(
        {
            endpoint_name: SimpleNamespace(
                id="expected-endpoint-id",
                creator=_RUNTIME,
            ),
        },
        [],
    )

    def swap_after_lease() -> None:
        client.serving_endpoints.details[endpoint_name] = SimpleNamespace(
            id="replacement-endpoint-id",
            creator=_RUNTIME,
        )

    with pytest.raises(RuntimeError, match="changed at exact deletion boundary"):
        cleanup._delete_endpoint_exact(
            client,
            name=endpoint_name,
            endpoint_id="expected-endpoint-id",
            creator=_RUNTIME,
            assert_single_writer=swap_after_lease,
            timeout_s=1,
            sleep=lambda _seconds: None,
        )

    assert client.serving_endpoints.deleted == []


def test_cleanup_rechecks_supervisor_tuple_after_lease_before_agent_delete() -> None:
    supervisor = _supervisor(
        supervisor_id="historical-supervisor",
        endpoint="historical-supervisor-endpoint",
    )
    endpoint_id = "historical-supervisor-endpoint-id"
    client = _Client(
        {
            supervisor["endpoint_name"]: SimpleNamespace(
                id=endpoint_id,
                creator=_RUNTIME,
            ),
        },
        [supervisor],
    )
    proof = _cleanup_proof(supervisor, endpoint_id=endpoint_id)
    journal = _MemoryCleanupJournal()
    journal.pending = proof

    def swap_after_lease() -> None:
        client.api_client.supervisors[supervisor["supervisor_agent_id"]][
            "endpoint_name"
        ] = "replacement-endpoint"

    with pytest.raises(RuntimeError, match="changed from its cleanup proof"):
        cleanup._cleanup_supervisor_proof(
            client,
            proof,
            app_name="mip-app",
            assert_single_writer=swap_after_lease,
            query_principals=inventory.QueryGroupPrincipals(
                "app-client",
                "app-scim",
                "verifier-client",
                "verifier-scim",
                "proxy-client",
                "proxy-scim",
            ),
            cleanup_journal=journal,
            timeout_s=1,
            sleep=lambda _seconds: None,
            stage=False,
        )

    assert client.api_client.deleted == []
    assert journal.pending == proof
