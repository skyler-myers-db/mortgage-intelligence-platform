"""Crash-safe handoff from revoked creation proof to cleanup proof."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tools.databricks import supervisor_creation_journal as creation
from tools.databricks.historical_agent_endpoint_types import (
    ReviewedSupervisor,
    RuntimeEndpointInventory,
    SupervisorCleanupProof,
)
from tools.databricks.retired_serving_query_groups import (
    exact_service_principal_scim_id,
)


def _creation_tuple(record: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(record.get("supervisor_id") or "").strip(),
        str(record.get("endpoint") or "").strip(),
        str(record.get("endpoint_id") or "").strip(),
        str(record.get("creator") or "").strip(),
    )


def _cleanup_tuple(proof: SupervisorCleanupProof) -> tuple[str, str, str, str]:
    return (
        proof.supervisor_id,
        proof.endpoint,
        proof.endpoint_id,
        proof.creator,
    )


def resolved_scim_id(
    workspace: Any,
    *,
    application_id: str,
    expected_scim_id: str,
) -> str:
    application = application_id.strip()
    expected = expected_scim_id.strip()
    if not application:
        raise ValueError("historical cleanup application ID is required")
    actual = exact_service_principal_scim_id(
        workspace,
        application_id=application,
    )
    if expected and actual != expected:
        raise RuntimeError(
            f"service-principal SCIM identity drifted for application {application!r}"
        )
    return actual


def cleanup_postflight_is_complete(inventory: RuntimeEndpointInventory) -> bool:
    """Allow only an exactly admitted current creation to remain preserved."""

    pending_creation = inventory.pending_supervisor_creation
    return (
        not any(not item.preserved for item in inventory.gateways)
        and not any(not item.preserved for item in inventory.supervisors)
        and inventory.pending_supervisor_cleanup is None
        and (
            pending_creation is None
            or (
                pending_creation.get("disposition", "active") == "active"
                and bool(pending_creation.get("supervisor_id"))
            )
        )
    )


class CreationRetirementCleanupJournal:
    """Transfer creation proof to an exact cleanup proof before resource mutation."""

    def __init__(
        self,
        workspace: Any,
        delegate: Any,
        *,
        app_name: str,
        lease_id: str,
        source_git_sha: str,
        runtime_application_id: str,
        canonical_name: str,
        genie_space_id: str,
        catalog: str,
    ) -> None:
        self._workspace = workspace
        self._delegate = delegate
        self._app_name = app_name
        self._lease_id = lease_id
        self._source_git_sha = source_git_sha
        self._runtime_application_id = runtime_application_id
        self._canonical_name = canonical_name
        self._genie_space_id = genie_space_id
        self._catalog = catalog

    def read(self) -> SupervisorCleanupProof | None:
        return self._delegate.read()

    def proof_for(
        self,
        supervisor: ReviewedSupervisor,
        *,
        runtime_application_id: str,
    ) -> SupervisorCleanupProof:
        return self._delegate.proof_for(
            supervisor,
            runtime_application_id=runtime_application_id,
        )

    def stage(self, proof: SupervisorCleanupProof) -> None:
        record = creation.download(
            self._workspace,
            app_name=self._app_name,
            runtime_application_id=self._runtime_application_id,
        )
        transfers_creation = record is not None and _creation_tuple(record) == _cleanup_tuple(proof)
        if transfers_creation:
            assert record is not None
            if creation.matches_current_policy(
                record,
                canonical_name=self._canonical_name,
                genie_space_id=self._genie_space_id,
                catalog=self._catalog,
            ):
                raise RuntimeError(
                    "current Supervisor creation proof cannot transfer to historical cleanup"
                )
        pending = self._delegate.read()
        if pending is None:
            self._delegate.stage(proof)
        elif pending != proof:
            raise RuntimeError("historical Supervisor cleanup journal already pins another tuple")
        if self._delegate.read() != proof:
            raise RuntimeError("historical Supervisor cleanup proof changed during handoff")
        if not transfers_creation:
            return
        if (
            creation.download(
                self._workspace,
                app_name=self._app_name,
                runtime_application_id=self._runtime_application_id,
            )
            != record
        ):
            raise RuntimeError("Supervisor creation proof changed during cleanup handoff")
        assert record is not None
        creation.clear(
            self._workspace,
            app_name=self._app_name,
            lease_id=self._lease_id,
            source_git_sha=self._source_git_sha,
            runtime_application_id=self._runtime_application_id,
            expected=record,
        )

    def clear(
        self,
        proof: SupervisorCleanupProof,
        *,
        assert_resources_absent: Callable[[], None],
    ) -> None:
        self._delegate.clear(
            proof,
            assert_resources_absent=assert_resources_absent,
        )
