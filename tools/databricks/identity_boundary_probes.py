"""Shared credential-side probes for managed serving identity boundaries."""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from backend.services.capability_serving_probes import (
    ServingEndpointExecution,
    query_serving_endpoint_with_proof,
)
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import Unauthenticated
from databricks.sdk.service.iam import Patch, PatchOp, PatchSchema
from tools.databricks.ai_gateway_tool_trace import is_cold_start_error
from tools.databricks.authorization_denial import is_authorization_denied
from tools.databricks.oauth_credential_creation import (
    ExactOAuthCredential,
    create_exact_oauth_credential,
    revoke_exact_oauth_credential,
)
from tools.databricks.oauth_credential_quarantine import (
    CredentialMutationContext,
    CredentialMutationQuarantineError,
)
from tools.databricks.serving_endpoint_acl import is_platform_foundation_endpoint
from tools.databricks.serving_query_group_access import (
    MANAGED_QUERY_GROUP_EXTERNAL_ID_PREFIX,
    MANAGED_QUERY_GROUP_PREFIX,
    managed_query_group_external_id,
    managed_query_group_name,
)

_MAX_INVENTORY = 1000
_MANAGED_GROUP_NAME_RE = re.compile(
    rf"{re.escape(MANAGED_QUERY_GROUP_PREFIX)}[0-9a-f]{{20}}-[0-9a-f]{{20}}"
)
_MANAGED_GROUP_EXTERNAL_ID_RE = re.compile(
    rf"{re.escape(MANAGED_QUERY_GROUP_EXTERNAL_ID_PREFIX)}[A-Za-z0-9_-]{{43}}"
)
_TEMPORARY_ADMINISTRATION_PROBE_SECRET_LIFETIME = "300s"
_QUERY_PROMPT = (
    "Confirm that the governed Mortgage Growth Agent is ready for a "
    "human-review-only workflow. Do not call tools or include borrower data."
)
_PATCH_SCHEMA = PatchSchema.URN_IETF_PARAMS_SCIM_API_MESSAGES_2_0_PATCH_OP


@dataclass(frozen=True)
class ManagedWorkspaceGroupBinding:
    """Admin-attested identity of one workspace-local capability group."""

    id: str
    name: str
    external_id: str
    resource_type: str


def _text(value: object, name: str) -> str:
    raw = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
    return str(getattr(raw, "value", raw) or "").strip()


def _authentication_failed(error: object) -> bool:
    if isinstance(error, Unauthenticated):
        return True
    response = getattr(error, "response", None)
    statuses = (
        getattr(error, "status_code", None),
        getattr(error, "http_status_code", None),
        getattr(response, "status_code", None),
    )
    if any(value == 401 or str(value or "").strip() == "401" for value in statuses):
        return True
    codes = (getattr(error, "error_code", None), getattr(error, "code", None))
    return any(
        str(getattr(value, "value", value) or "").strip().upper()
        in {"UNAUTHENTICATED", "UNAUTHORIZED"}
        for value in codes
    )


def _bounded_unique(
    values: Iterable[str],
    *,
    label: str,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    result = tuple(values)
    if (
        (not result and not allow_empty)
        or len(result) > _MAX_INVENTORY
        or any(not value for value in result)
        or len(result) != len(set(result))
    ):
        raise RuntimeError(f"{label} inventory is empty, duplicated, or unbounded")
    return tuple(sorted(result))


def exact_agent_responses_endpoint_id(details: object, *, endpoint: str) -> str:
    """Return an immutable endpoint ID only for the exact Responses contract."""

    endpoint_id = _text(details, "id")
    task = _text(details, "task").lower().replace("-", "_").replace("/", "_")
    if (
        _text(details, "name") != endpoint
        or not endpoint_id
        or task != "agent_v1_responses"
    ):
        raise RuntimeError("reviewed Gateway identity or Agent Responses protocol drifted")
    return endpoint_id


def _workspace_group_binding(group: object) -> ManagedWorkspaceGroupBinding:
    meta = (
        group.get("meta")
        if isinstance(group, dict)
        else getattr(group, "meta", None)
    )
    binding = ManagedWorkspaceGroupBinding(
        id=_text(group, "id"),
        name=_text(group, "display_name") or _text(group, "displayName"),
        external_id=_text(group, "external_id") or _text(group, "externalId"),
        resource_type=(
            _text(meta or {}, "resource_type")
            or _text(meta or {}, "resourceType")
        ),
    )
    if (
        not binding.id
        or not binding.name
        or not binding.external_id
        or binding.resource_type != "WorkspaceGroup"
    ):
        raise RuntimeError(
            "managed serving-query group is not an exact workspace-local group"
        )
    return binding


def managed_workspace_group_binding(
    workspace: Any,
    *,
    group_id: str,
) -> ManagedWorkspaceGroupBinding:
    """Hydrate and bind one exact workspace-local group by immutable ID."""

    reviewed_id = group_id.strip()
    if not reviewed_id:
        raise ValueError("managed workspace-group ID is required")
    binding = _workspace_group_binding(workspace.groups.get(reviewed_id))
    if binding.id != reviewed_id:
        raise RuntimeError("managed workspace-group immutable identity drifted")
    return binding


def collect_managed_workspace_group_bindings(
    workspace: Any,
    *,
    prefix_contracts: tuple[tuple[str, str], ...],
) -> tuple[ManagedWorkspaceGroupBinding, ...]:
    """Bind every workspace-local group matching reviewed name/external prefixes."""

    if (
        not prefix_contracts
        or len(prefix_contracts) != len(set(prefix_contracts))
        or any(not name or not external for name, external in prefix_contracts)
    ):
        raise ValueError("managed workspace-group prefix contracts are required")
    summaries = tuple(workspace.groups.list(attributes="id,displayName"))
    if len(summaries) > _MAX_INVENTORY:
        raise RuntimeError("managed workspace-group inventory is unbounded")
    bindings: list[ManagedWorkspaceGroupBinding] = []
    for summary in summaries:
        name = _text(summary, "display_name") or _text(summary, "displayName")
        matches = tuple(
            contract
            for contract in prefix_contracts
            if name.startswith(contract[0])
        )
        if not matches:
            continue
        if len(matches) != 1:
            raise RuntimeError("managed workspace-group name contract is ambiguous")
        binding = managed_workspace_group_binding(
            workspace,
            group_id=_text(summary, "id"),
        )
        if (
            binding.name != name
            or not binding.external_id.startswith(matches[0][1])
        ):
            raise RuntimeError("managed workspace-group immutable contract drifted")
        bindings.append(binding)
    ids = tuple(binding.id.casefold() for binding in bindings)
    names = tuple(binding.name.casefold() for binding in bindings)
    if (
        len(ids) != len(set(ids))
        or len(names) != len(set(names))
    ):
        raise RuntimeError("managed workspace-group inventory is ambiguous")
    return tuple(sorted(bindings, key=lambda binding: binding.id))


def collect_attached_managed_query_group_bindings(
    workspace: Any,
    *,
    expected_application_id: str,
) -> tuple[ManagedWorkspaceGroupBinding, ...]:
    """Inventory every managed serving-query group associated with one exact SP.

    Active groups are associated by exact membership. Empty retired groups are
    associated by the deterministic name and external ID derived from every
    live customer serving endpoint, so the credential-side administration
    probe cannot miss a self-readd path after atomic membership revocation.
    """

    application_id = expected_application_id.strip()
    if not application_id:
        raise ValueError("verifier application ID is required")
    escaped = application_id.replace("\\", "\\\\").replace('"', '\\"')
    principal_inventory = tuple(
        workspace.service_principals.list(
            filter=f'applicationId eq "{escaped}"',
            attributes="id,applicationId",
        )
    )
    if len(principal_inventory) > _MAX_INVENTORY:
        raise RuntimeError("verifier service-principal inventory is unbounded")
    principals = [
        item
        for item in principal_inventory
        if _text(item, "application_id") == application_id
    ]
    if len(principals) != 1:
        raise RuntimeError(
            "verifier application ID did not resolve to exactly one admin-side identity"
        )
    principal_id = _text(principals[0], "id")
    if not principal_id:
        raise RuntimeError("verifier admin-side identity has no immutable SCIM ID")

    endpoint_names = _bounded_unique(
        (
            _text(summary, "name")
            for summary in workspace.serving_endpoints.list()
        ),
        label="serving endpoint",
        allow_empty=True,
    )
    expected_groups: dict[str, str] = {}
    seen_endpoint_ids: set[str] = set()
    for endpoint_name in endpoint_names:
        details = workspace.serving_endpoints.get(endpoint_name)
        endpoint_id = _text(details, "id")
        if not endpoint_id:
            # Foundation endpoints have no customer-serving ACL identity, so no
            # endpoint-bound managed group can be derived for them.
            if is_platform_foundation_endpoint(details):
                continue
            raise RuntimeError(
                f"non-foundation serving endpoint {endpoint_name!r} has no immutable ID"
            )
        if _text(details, "name") != endpoint_name or endpoint_id in seen_endpoint_ids:
            raise RuntimeError("serving endpoint immutable identity is ambiguous")
        seen_endpoint_ids.add(endpoint_id)
        expected_name = managed_query_group_name(
            endpoint_id=endpoint_id,
            application_id=application_id,
        )
        expected_external_id = managed_query_group_external_id(
            endpoint_id=endpoint_id,
            application_id=application_id,
        )
        if expected_name in expected_groups:
            raise RuntimeError("serving endpoint managed-group identity is duplicated")
        expected_groups[expected_name] = expected_external_id

    summaries = tuple(workspace.groups.list(attributes="id,displayName"))
    if len(summaries) > _MAX_INVENTORY:
        raise RuntimeError("managed serving-query group inventory is unbounded")
    attached: list[ManagedWorkspaceGroupBinding] = []
    seen_group_ids: set[str] = set()
    seen_group_names: set[str] = set()
    for summary in summaries:
        name = _text(summary, "display_name")
        if not name.startswith(MANAGED_QUERY_GROUP_PREFIX):
            continue
        group_id = _text(summary, "id")
        if (
            not group_id
            or group_id in seen_group_ids
            or _MANAGED_GROUP_NAME_RE.fullmatch(name) is None
            or name in seen_group_names
        ):
            raise RuntimeError("managed serving-query group inventory is ambiguous")
        seen_group_ids.add(group_id)
        seen_group_names.add(name)
        group = workspace.groups.get(group_id)
        binding = _workspace_group_binding(group)
        external_id = binding.external_id
        if (
            binding.id != group_id
            or binding.name != name
            or _MANAGED_GROUP_EXTERNAL_ID_RE.fullmatch(
                external_id
            )
            is None
        ):
            raise RuntimeError("managed serving-query group contract drifted")
        member_ids = _bounded_unique(
            (_text(member, "value") for member in getattr(group, "members", None) or ()),
            label=f"managed serving-query group {group_id!r} member",
            allow_empty=True,
        )
        associated_external_id = expected_groups.get(name)
        if associated_external_id is not None and external_id != associated_external_id:
            raise RuntimeError("managed serving-query group deterministic contract drifted")
        if principal_id in member_ids or associated_external_id is not None:
            attached.append(binding)
    identities = tuple(binding.id for binding in attached)
    names = tuple(binding.name.casefold() for binding in attached)
    if (
        len(attached) > _MAX_INVENTORY
        or len(identities) != len(set(identities))
        or len(names) != len(set(names))
    ):
        raise RuntimeError(
            "attached managed serving-query group inventory is ambiguous"
        )
    return tuple(sorted(attached, key=lambda binding: binding.id))


def collect_attached_managed_query_group_ids(
    workspace: Any,
    *,
    expected_application_id: str,
) -> tuple[str, ...]:
    """Compatibility projection for callers that only persist immutable IDs."""

    return tuple(
        binding.id
        for binding in collect_attached_managed_query_group_bindings(
            workspace,
            expected_application_id=expected_application_id,
        )
    )


def verify_managed_query_group_administration_denied(
    workspace: Any,
    *,
    group_bindings: tuple[ManagedWorkspaceGroupBinding, ...],
    admin_workspace: Any | None = None,
) -> None:
    """Prove the identity cannot administer exact workspace-local groups.

    A same-name SCIM replacement is semantically idempotent. Success therefore
    proves forbidden group-management authority without changing the reviewed
    contract, while an authorization denial proves the intended boundary.
    """

    reviewed_ids = tuple(binding.id for binding in group_bindings)
    reviewed_names = tuple(binding.name.casefold() for binding in group_bindings)
    if (
        len(group_bindings) > _MAX_INVENTORY
        or len(reviewed_ids) != len(set(reviewed_ids))
        or len(reviewed_names) != len(set(reviewed_names))
        or any(
            not binding.id
            or not binding.name
            or not binding.external_id
            or binding.resource_type != "WorkspaceGroup"
            for binding in group_bindings
        )
    ):
        raise RuntimeError(
            "attached managed workspace-group inventory is ambiguous"
        )

    def assert_admin_snapshot(binding: ManagedWorkspaceGroupBinding) -> None:
        if admin_workspace is None:
            return
        observed = _workspace_group_binding(admin_workspace.groups.get(binding.id))
        if observed != binding:
            raise RuntimeError(
                "managed serving-query workspace-group contract changed during "
                "administration proof"
            )

    for binding in group_bindings:
        assert_admin_snapshot(binding)
        try:
            workspace.groups.patch(
                id=binding.id,
                operations=[
                    Patch(
                        op=PatchOp.REPLACE,
                        path="displayName",
                        value=binding.name,
                    )
                ],
                schemas=[_PATCH_SCHEMA],
            )
        except Exception as exc:  # noqa: BLE001 - classify provider authorization
            if is_authorization_denied(exc, allow_hidden_resource=True):
                assert_admin_snapshot(binding)
                continue
            raise RuntimeError(
                "managed serving-query group administration "
                f"{binding.id} was inconclusive: {type(exc).__name__}: {exc}"
            ) from exc
        raise RuntimeError(
            "managed serving-query group administration "
            f"{binding.id} unexpectedly succeeded"
        )


def probe_target_managed_query_group_administration_boundary(
    account_client: Any,
    *,
    account_sp_id: str,
    application_id: str,
    expected_workspace_scim_id: str,
    workspace_host: str,
    account_id: str,
    group_bindings: tuple[ManagedWorkspaceGroupBinding, ...],
    assert_single_writer: Callable[[], None],
    admin_workspace: Any | None = None,
    workspace_factory: Callable[..., Any] = WorkspaceClient,
) -> dict[str, str]:
    """Prove exact target credentials cannot administer their managed groups.

    Workspace-admin SCIM can omit account-level nested memberships under
    Automatic Identity Management. Mint a bounded one-use credential for the
    target service principal, bind it to the exact workspace identity, capture
    its own authoritative ``groups`` projection, and execute a semantically
    idempotent group-administration denial probe under those same credentials. The
    temporary credential must be deleted even when any proof fails.
    """

    principal_id = account_sp_id.strip()
    principal = application_id.strip()
    workspace_principal_id = expected_workspace_scim_id.strip()
    host = workspace_host.strip()
    account_identifier = account_id.strip()
    if not all(
        (principal_id, principal, workspace_principal_id, host, account_identifier)
    ):
        raise ValueError(
            "account principal, application, workspace principal, and host are required"
        )
    reviewed_group_ids = _bounded_unique(
        (binding.id for binding in group_bindings),
        label="attached managed serving-query group",
        allow_empty=True,
    )
    if tuple(sorted(reviewed_group_ids)) != tuple(
        sorted(binding.id for binding in group_bindings)
    ):
        raise RuntimeError("attached managed workspace-group inventory is ambiguous")
    credential: ExactOAuthCredential | None = None
    probe_error: BaseException | None = None
    effective_groups: dict[str, str] = {}
    try:
        credential = create_exact_oauth_credential(
            principal_id=principal_id,
            list_credentials=lambda: account_client.service_principal_secrets.list(
                principal_id
            ),
            create_credential=lambda: account_client.service_principal_secrets.create(
                principal_id,
                lifetime=_TEMPORARY_ADMINISTRATION_PROBE_SECRET_LIFETIME,
            ),
            delete_credential=lambda credential_id: (
                account_client.service_principal_secrets.delete(
                    principal_id,
                    credential_id,
                )
            ),
            assert_single_writer=assert_single_writer,
            mutation_context=CredentialMutationContext(
                authority_scope="account",
                authority_identity=principal,
                provider_api="account.service_principal_secrets",
                operation_mode="temporary_probe",
                sink_descriptor="temporary:managed-group-administration-probe",
                credential_lifetime_seconds=300,
            ),
            label="temporary managed-group administration",
        )
        target_workspace = workspace_factory(
            host=host,
            client_id=principal,
            client_secret=credential.secret,
            auth_type="oauth-m2m",
        )
        identity = target_workspace.api_client.do(
            "GET",
            "/api/2.0/preview/scim/v2/Me",
            query={"attributes": "id,userName,groups"},
            headers={"Accept": "application/json"},
        )
        if not isinstance(identity, dict):
            raise RuntimeError("managed-group target identity proof is malformed")
        if (
            identity.get("id") != workspace_principal_id
            or identity.get("userName") != principal
        ):
            raise RuntimeError(
                "temporary managed-group credential authenticated as a different identity"
            )
        groups = identity.get("groups")
        if not isinstance(groups, list):
            raise RuntimeError("managed-group target proof omitted its groups collection")
        group_names: set[str] = set()
        if len(groups) > _MAX_INVENTORY:
            raise RuntimeError("managed-group target membership inventory is unbounded")
        for group in groups:
            if not isinstance(group, dict):
                raise RuntimeError("managed-group target identity returned a malformed group")
            group_id = group.get("value")
            group_name = group.get("display")
            if (
                not isinstance(group_id, str)
                or not group_id
                or group_id != group_id.strip()
                or not isinstance(group_name, str)
                or not group_name
                or group_name != group_name.strip()
                or group_id in effective_groups
                or group_name.casefold() in group_names
            ):
                raise RuntimeError(
                    "managed-group target identity membership inventory is ambiguous"
                )
            effective_groups[group_id] = group_name
            group_names.add(group_name.casefold())
        verify_managed_query_group_administration_denied(
            target_workspace,
            group_bindings=group_bindings,
            admin_workspace=admin_workspace,
        )
    except BaseException as exc:
        probe_error = exc
    finally:
        if credential is not None:
            try:
                revoke_exact_oauth_credential(
                    credential,
                    principal_id=principal_id,
                    list_credentials=lambda: (
                        account_client.service_principal_secrets.list(principal_id)
                    ),
                    delete_credential=lambda credential_id: (
                        account_client.service_principal_secrets.delete(
                            principal_id,
                            credential_id,
                        )
                    ),
                    assert_single_writer=assert_single_writer,
                    label="temporary managed-group administration",
                )
            except CredentialMutationQuarantineError:
                raise
            except BaseException as cleanup_error:
                raise RuntimeError(
                    "temporary managed-group administration credential cleanup "
                    "could not be proven"
                ) from cleanup_error
    if probe_error is not None:
        raise probe_error
    return effective_groups


def _response_mapping(response: object) -> dict[str, Any] | None:
    if isinstance(response, dict):
        return response
    for method in ("as_dict", "to_dict"):
        converter = getattr(response, method, None)
        if callable(converter):
            try:
                value = converter()
            except Exception:  # noqa: BLE001 - exact validation below fails closed
                return None
            return value if isinstance(value, dict) else None
    return None


def is_exact_terminal_responses_execution(
    execution: ServingEndpointExecution,
    *,
    endpoint: str,
) -> bool:
    """Validate one exact endpoint-bound, terminal Responses API execution."""

    canonical_task = str(execution.task or "").lower().replace("-", "_").replace("/", "_")
    if (
        execution.endpoint != endpoint
        or execution.transport != "responses_api"
        or canonical_task != "agent_v1_responses"
        or not str(execution.client_request_id or "").strip()
    ):
        return False
    value = _response_mapping(execution.response)
    if value is None:
        return False
    required = {"id", "object", "model", "status", "error", "incomplete_details", "output"}
    if not required.issubset(value):
        return False
    model = str(value.get("model") or "").strip()
    if (
        not str(value.get("id") or "").strip()
        or str(value.get("object") or "").strip() != "response"
        or model != endpoint
        or str(value.get("status") or "").strip().casefold() != "completed"
        or value.get("error") is not None
        or value.get("incomplete_details") is not None
    ):
        return False
    output = value.get("output")
    if not isinstance(output, list) or not output:
        return False
    for item in output:
        if (
            not isinstance(item, dict)
            or item.get("type") != "message"
            or item.get("role") != "assistant"
        ):
            continue
        item_status = str(item.get("status") or "").strip().casefold()
        content = item.get("content")
        if item_status not in {"", "completed"} or not isinstance(content, list):
            continue
        if any(
            isinstance(part, dict)
            and part.get("type") == "output_text"
            and bool(str(part.get("text") or "").strip())
            for part in content
        ):
            return True
    return False


def prove_exact_gateway_responses_execution(
    workspace: Any,
    *,
    endpoint: str,
    sleep: Callable[[float], object] = time.sleep,
) -> None:
    """Execute and validate the exact approved Gateway using verifier OAuth."""

    try:
        warmup_timeout_s = float(
            os.environ.get("MIP_AI_GATEWAY_WARMUP_TIMEOUT_S", "600")
        )
        deadline = time.monotonic() + max(0.0, warmup_timeout_s)
        while True:
            try:
                query_serving_endpoint_with_proof(
                    workspace,
                    endpoint,
                    task="agent_v1_responses",
                    prompt=_QUERY_PROMPT,
                    client_request_id=f"mip-warmup-{uuid4().hex}",
                    max_tokens=64,
                )
                break
            except Exception as exc:  # noqa: BLE001 - denial-first cold-start retry
                if (
                    is_authorization_denied(exc, allow_hidden_resource=True)
                    or _authentication_failed(exc)
                    or not is_cold_start_error(exc)
                    or time.monotonic() >= deadline
                ):
                    raise
                sleep(20.0)
        execution = query_serving_endpoint_with_proof(
            workspace,
            endpoint,
            task="agent_v1_responses",
            prompt=_QUERY_PROMPT,
            client_request_id=f"mip-verifier-boundary-{uuid4().hex}",
            max_tokens=64,
        )
    except Exception as exc:  # noqa: BLE001 - positive provider proof must be exact
        raise RuntimeError(
            f"verifier Gateway query {endpoint} was inconclusive: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if not is_exact_terminal_responses_execution(execution, endpoint=endpoint):
        raise RuntimeError(
            f"verifier Gateway query {endpoint} did not return the exact terminal "
            "Gateway Responses payload"
        )
