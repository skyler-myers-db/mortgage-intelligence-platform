"""Typed identity binding extracted from a verified signed App rollback record."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ProxyRollbackMode = Literal["exact-proxy", "legacy-proxyless"]


@dataclass(frozen=True)
class SignedLastGoodAppContract:
    record_version: int
    proxy_rollback_mode: ProxyRollbackMode
    deployment_id: str
    deployment_lease_id: str
    git_sha: str
    gateway_binding_sha256: str | None
    gateway_endpoint: str
    gateway_endpoint_id: str
    gateway_endpoint_creator: str
    gateway_inference_table_family: str
    supervisor_id: str
    supervisor_creator: str
    supervisor_endpoint: str
    supervisor_endpoint_id: str
    runtime_application_id: str
    genie_space_id: str
    proxy_application_id: str | None
    active_proxy_credential_id: str | None
    pending_proxy_credential_retirement_ids: tuple[str, ...]
