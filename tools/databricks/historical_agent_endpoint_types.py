"""Typed contracts shared by historical agent endpoint reconciliation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, order=True)
class GatewayPin:
    """Immutable Gateway tuple captured by signed blue/green state."""

    name: str
    endpoint_id: str
    creator: str


@dataclass(frozen=True, order=True)
class SupervisorPin:
    """Immutable managed-Supervisor tuple captured by signed blue/green state."""

    supervisor_id: str
    endpoint: str
    endpoint_id: str
    creator: str


@dataclass(frozen=True, order=True)
class SupervisorCleanupProof:
    """Lease-bound exact tuple persisted before a Supervisor is deleted."""

    app_name: str
    lease_id: str
    source_git_sha: str
    runtime_application_id: str
    supervisor_id: str
    endpoint: str
    endpoint_id: str
    creator: str
    version: int = 1


@dataclass(frozen=True)
class ReviewedGateway:
    name: str
    endpoint_id: str
    creator: str
    supervisor_id: str
    supervisor_endpoint: str
    supervisor_endpoint_id: str
    contract_digest: str
    preserved: bool


@dataclass(frozen=True)
class ReviewedSupervisor:
    supervisor_id: str
    display_name: str
    endpoint: str
    endpoint_id: str
    creator: str
    create_time: str
    contract_json: str
    contract_sha256: str
    preserved: bool


@dataclass(frozen=True)
class RuntimeEndpointInventory:
    """Complete reviewed serving-resource set for global access audits."""

    version: int
    runtime_application_id: str
    gateways: tuple[ReviewedGateway, ...]
    supervisors: tuple[ReviewedSupervisor, ...]
    pending_supervisor_cleanup: SupervisorCleanupProof | None = None
    pending_supervisor_creation: dict[str, Any] | None = None

    def document(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "runtime_application_id": self.runtime_application_id,
            "gateways": [asdict(item) for item in self.gateways],
            "supervisors": [asdict(item) for item in self.supervisors],
            "pending_supervisor_cleanup": (
                asdict(self.pending_supervisor_cleanup)
                if self.pending_supervisor_cleanup is not None
                else None
            ),
            "pending_supervisor_creation": self.pending_supervisor_creation,
            "reviewed_serving_endpoints": [
                {
                    "kind": "gateway",
                    "name": item.name,
                    "endpoint_id": item.endpoint_id,
                    "creator": item.creator,
                    "preserved": item.preserved,
                }
                for item in self.gateways
            ]
            + [
                {
                    "kind": "supervisor",
                    "name": item.endpoint,
                    "endpoint_id": item.endpoint_id,
                    "creator": item.creator,
                    "preserved": item.preserved,
                }
                for item in self.supervisors
            ]
            + (
                [
                    {
                        "kind": "supervisor_cleanup",
                        "name": self.pending_supervisor_cleanup.endpoint,
                        "endpoint_id": self.pending_supervisor_cleanup.endpoint_id,
                        "creator": self.pending_supervisor_cleanup.creator,
                        "preserved": False,
                    }
                ]
                if self.pending_supervisor_cleanup is not None
                and not any(
                    item.endpoint == self.pending_supervisor_cleanup.endpoint
                    for item in self.supervisors
                )
                else []
            )
            + (
                [
                    {
                        "kind": "supervisor_creation",
                        "name": self.pending_supervisor_creation["endpoint"],
                        "endpoint_id": self.pending_supervisor_creation["endpoint_id"],
                        "creator": self.pending_supervisor_creation["creator"],
                        "preserved": True,
                    }
                ]
                if self.pending_supervisor_creation is not None
                and self.pending_supervisor_creation.get("endpoint")
                else []
            ),
        }


@dataclass(frozen=True)
class QueryGroupPrincipals:
    """Exact endpoint-bound identities whose managed groups may be retired."""

    app_application_id: str
    app_scim_id: str
    verifier_application_id: str
    verifier_scim_id: str
    proxy_application_id: str
    proxy_scim_id: str

    def validate(self) -> None:
        if any(not value or value != value.strip() for value in asdict(self).values()):
            raise ValueError(
                "historical cleanup requires complete App, verifier, and proxy identities"
            )
        applications = {
            self.app_application_id,
            self.verifier_application_id,
            self.proxy_application_id,
        }
        if len(applications) != 3:
            raise ValueError("historical cleanup query identities must be distinct")
