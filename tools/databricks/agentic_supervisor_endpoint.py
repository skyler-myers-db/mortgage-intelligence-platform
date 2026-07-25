"""Exact immutable identity proof for a managed Supervisor serving endpoint."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from typing import Any, NamedTuple

from tools.databricks.agent_runtime_access import assert_runtime_creator
from tools.databricks.agentic_resource_contract import SupervisorAgentBinding
from tools.databricks.serving_endpoint_acl import endpoint_has_legacy_direct_query_principal
from tools.databricks.supervisor_agent_contract import (
    RUNTIME_REPLACEMENT_SUFFIX,
    SupervisorContractDrift,
    supervisor_replacement_name,
)


class SupervisorCandidates(NamedTuple):
    canonical: dict[str, Any] | None
    replacement_name: str
    replacement: dict[str, Any] | None
    managed_query_name: str
    managed_query_replacement: dict[str, Any] | None
    legacy_replacement: dict[str, Any] | None


class SupervisorPlan(NamedTuple):
    target_name: str
    candidate: dict[str, Any] | None
    replaced: dict[str, Any] | None
    exact_canonical: dict[str, Any] | None


def exact_supervisor_endpoint_id(
    workspace: Any,
    *,
    endpoint_name: str,
    runtime_application_id: str,
) -> str:
    details = workspace.serving_endpoints.get(endpoint_name)
    endpoint_id = str(getattr(details, "id", "") or "").strip()
    if not endpoint_id:
        raise RuntimeError("managed Supervisor endpoint has no immutable id")
    assert_runtime_creator(
        getattr(details, "creator", None),
        application_id=runtime_application_id,
        resource=f"managed Supervisor endpoint {endpoint_name}",
    )
    return endpoint_id


def managed_query_supervisor_replacement_name(
    display_name: str,
    *,
    genie_space_id: str,
    catalog: str,
) -> str:
    """Return the immutable Supervisor name for the managed-query ACL epoch."""

    return (
        supervisor_replacement_name(
            display_name,
            genie_space_id=genie_space_id,
            catalog=catalog,
        )
        + "-mq1"
    )


def supervisor_candidates(
    agents: list[dict[str, Any]],
    *,
    display_name: str,
    genie_space_id: str,
    catalog: str,
) -> SupervisorCandidates:
    """Resolve every reserved Supervisor name and reject ambiguous candidates."""

    replacement_name = supervisor_replacement_name(
        display_name,
        genie_space_id=genie_space_id,
        catalog=catalog,
    )
    managed_query_name = managed_query_supervisor_replacement_name(
        display_name,
        genie_space_id=genie_space_id,
        catalog=catalog,
    )

    def matching(name: str) -> dict[str, Any] | None:
        matches = [row for row in agents if row.get("display_name") == name]
        if len(matches) > 1:
            raise RuntimeError(
                f"multiple Supervisor agents use reserved display name {name!r}; "
                "manual governance review is required"
            )
        return matches[0] if matches else None

    canonical = matching(display_name)
    replacement = matching(replacement_name)
    managed_query_replacement = matching(managed_query_name)
    legacy_replacement = matching(f"{display_name}{RUNTIME_REPLACEMENT_SUFFIX}")
    if (
        sum(
            candidate is not None
            for candidate in (replacement, managed_query_replacement, legacy_replacement)
        )
        > 1
    ):
        raise RuntimeError(
            "contract-hashed and legacy runtime Supervisor replacements coexist; "
            "manual governance cleanup is required before selecting a candidate"
        )
    return SupervisorCandidates(
        canonical,
        replacement_name,
        replacement,
        managed_query_name,
        managed_query_replacement,
        legacy_replacement,
    )


def supervisor_endpoint_requires_managed_query_rotation(
    workspace: Any,
    *,
    endpoint_name: str,
    runtime_application_id: str,
    managed_query_application_id: str | None = None,
    additional_managed_query_application_ids: Collection[str] = (),
) -> bool:
    """Require rotation only for query principals other than the exact manager."""

    exact_supervisor_endpoint_id(
        workspace,
        endpoint_name=endpoint_name,
        runtime_application_id=runtime_application_id,
    )
    approved = (
        (managed_query_application_id.strip(),)
        if managed_query_application_id and managed_query_application_id.strip()
        else ()
    )
    return endpoint_has_legacy_direct_query_principal(
        workspace,
        endpoint_name=endpoint_name,
        runtime_manager_application_id=runtime_application_id,
        approved_managed_query_application_ids=approved,
        approved_empty_managed_query_application_ids=tuple(
            application_id.strip()
            for application_id in additional_managed_query_application_ids
            if application_id.strip()
        ),
    )


def canonical_supervisor_contract_is_exact(
    candidate: Mapping[str, Any],
    *,
    display_name: str,
    genie_space_id: str,
    catalog: str,
    runtime_application_id: str,
    assert_contract: Callable[..., None],
) -> bool:
    """Distinguish replaceable owner/contract drift from provider failures."""

    try:
        assert_runtime_creator(
            candidate.get("creator"),
            application_id=runtime_application_id,
            resource=f"Supervisor agent {display_name}",
        )
    except RuntimeError:
        return False
    try:
        assert_contract(
            str(candidate["supervisor_agent_id"]),
            genie_space_id=genie_space_id,
            catalog=catalog,
            expected_display_name=display_name,
        )
    except SupervisorContractDrift:
        return False
    return True


def plan_supervisor_agent(
    workspace: Any,
    candidates: SupervisorCandidates,
    *,
    display_name: str,
    genie_space_id: str,
    catalog: str,
    runtime_application_id: str,
    managed_query_application_id: str | None,
    additional_managed_query_application_ids: Collection[str] = (),
    assert_contract: Callable[..., None],
) -> SupervisorPlan:
    """Select a safe immutable candidate or a deterministic creation target."""

    canonical = candidates.canonical
    if canonical is None:
        if candidates.replacement is not None:
            return SupervisorPlan(
                candidates.replacement_name,
                candidates.replacement,
                None,
                None,
            )
        if candidates.legacy_replacement is not None:
            return SupervisorPlan(
                f"{display_name}{RUNTIME_REPLACEMENT_SUFFIX}",
                candidates.legacy_replacement,
                None,
                None,
            )
        return SupervisorPlan(
            candidates.managed_query_name
            if candidates.managed_query_replacement is not None
            else display_name,
            candidates.managed_query_replacement,
            None,
            None,
        )

    canonical_exact = canonical_supervisor_contract_is_exact(
        canonical,
        display_name=display_name,
        genie_space_id=genie_space_id,
        catalog=catalog,
        runtime_application_id=runtime_application_id,
        assert_contract=assert_contract,
    )
    if not canonical_exact:
        if candidates.managed_query_replacement is not None:
            raise RuntimeError(
                "a managed-query Supervisor replacement remains beside "
                "a non-current canonical contract"
            )
        if candidates.legacy_replacement is not None:
            return SupervisorPlan(
                f"{display_name}{RUNTIME_REPLACEMENT_SUFFIX}",
                candidates.legacy_replacement,
                canonical,
                None,
            )
        return SupervisorPlan(
            candidates.replacement_name,
            candidates.replacement,
            canonical,
            None,
        )

    endpoint = str(canonical.get("endpoint_name") or "")
    rotate_query_access = supervisor_endpoint_requires_managed_query_rotation(
        workspace,
        endpoint_name=endpoint,
        runtime_application_id=runtime_application_id,
        managed_query_application_id=managed_query_application_id,
        additional_managed_query_application_ids=additional_managed_query_application_ids,
    )
    if rotate_query_access:
        if candidates.replacement is not None or candidates.legacy_replacement is not None:
            raise RuntimeError(
                "a pre-managed-query Supervisor replacement remains beside "
                "an exact canonical contract"
            )
        return SupervisorPlan(
            candidates.managed_query_name,
            candidates.managed_query_replacement,
            canonical,
            None,
        )
    if (
        candidates.replacement is not None
        or candidates.legacy_replacement is not None
        or candidates.managed_query_replacement is not None
    ):
        raise RuntimeError("a replacement Supervisor remains beside an exact canonical contract")
    return SupervisorPlan(display_name, None, None, canonical)


def supervisor_agent_binding(
    *,
    supervisor_id: str,
    display_name: str,
    endpoint: str,
    replaced: Mapping[str, Any] | None = None,
) -> SupervisorAgentBinding:
    """Build the complete replacement tuple consumed by the cutover journal."""

    return SupervisorAgentBinding(
        supervisor_id=supervisor_id,
        display_name=display_name,
        endpoint=endpoint,
        replaced_supervisor_id=(
            str(replaced.get("supervisor_agent_id") or "") if replaced else None
        ),
        replaced_supervisor_endpoint=(
            str(replaced.get("endpoint_name") or "") if replaced else None
        ),
        replaced_supervisor_creator=(str(replaced.get("creator") or "") if replaced else None),
        replaced_supervisor_create_time=(
            str(replaced.get("create_time") or "") if replaced else None
        ),
    )
