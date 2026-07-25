"""Recover an interrupted signed-blue Supervisor name finalization."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Collection, Mapping
from typing import Any

from tools.databricks.agent_runtime_access import assert_runtime_creator
from tools.databricks.agentic_supervisor_endpoint import (
    SupervisorCandidates,
    exact_supervisor_endpoint_id,
    supervisor_candidates,
    supervisor_endpoint_requires_managed_query_rotation,
)
from tools.databricks.supervisor_agent_contract import SupervisorContractDrift

_SIGNED_BLUE_SUPERVISOR_PIN_ENV = "MIP_CUTOVER_SIGNED_BLUE_SUPERVISOR_PIN_JSON"
_PIN_FIELDS = frozenset({"supervisor_id", "endpoint", "endpoint_id", "creator"})


def signed_blue_supervisor_pin_from_env() -> dict[str, str] | None:
    """Read the immutable Supervisor tuple exported by signed rollback proof."""

    raw = os.environ.get(_SIGNED_BLUE_SUPERVISOR_PIN_ENV, "").strip()
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{_SIGNED_BLUE_SUPERVISOR_PIN_ENV} is not valid JSON"
        ) from exc
    if not isinstance(value, dict) or set(value) != _PIN_FIELDS:
        raise RuntimeError("signed-blue Supervisor immutable pin is incomplete")
    pin = {field: str(value.get(field) or "").strip() for field in _PIN_FIELDS}
    if not all(pin.values()):
        raise RuntimeError("signed-blue Supervisor immutable pin is incomplete")
    return pin


def _candidate_identity(candidate: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(candidate.get("supervisor_agent_id") or "").strip(),
        str(candidate.get("endpoint_name") or "").strip(),
        str(candidate.get("creator") or "").strip(),
    )


def _pre_managed_query_candidate(
    candidates: SupervisorCandidates,
) -> dict[str, Any] | None:
    rows = [
        candidate
        for candidate in (candidates.replacement, candidates.legacy_replacement)
        if candidate is not None
    ]
    if len(rows) > 1:
        raise RuntimeError(
            "multiple pre-managed-query Supervisor candidates require manual review"
        )
    return rows[0] if rows else None


def recover_interrupted_signed_blue_finalization(
    workspace: Any,
    candidates: SupervisorCandidates,
    *,
    signed_blue_pin: Mapping[str, object] | None,
    display_name: str,
    genie_space_id: str,
    catalog: str,
    runtime_application_id: str,
    managed_query_application_id: str | None,
    additional_managed_query_application_ids: Collection[str],
    assert_contract: Callable[..., None],
    assert_single_writer: Callable[[], None],
    list_agents: Callable[[], list[dict[str, Any]]],
    rename_agent: Callable[[str, str], None],
) -> SupervisorCandidates:
    """Canonicalize only the exact signed predecessor so mq1 rotation can resume.

    A hard stop after blue capture and predecessor retirement can leave the
    signed live Supervisor under its deterministic replacement name. If that
    endpoint still has direct query access, ordinary candidate reuse cannot
    safely continue. Renaming the exact signed tuple to its canonical name
    makes it the explicit predecessor of a fresh managed-query candidate
    without changing endpoint authority.
    """

    if candidates.canonical is not None:
        return candidates
    candidate = _pre_managed_query_candidate(candidates)
    if candidate is None:
        return candidates
    endpoint = str(candidate.get("endpoint_name") or "").strip()
    if not supervisor_endpoint_requires_managed_query_rotation(
        workspace,
        endpoint_name=endpoint,
        runtime_application_id=runtime_application_id,
        managed_query_application_id=managed_query_application_id,
        additional_managed_query_application_ids=(
            additional_managed_query_application_ids
        ),
    ):
        return candidates
    if signed_blue_pin is None:
        return candidates

    if set(signed_blue_pin) != _PIN_FIELDS:
        raise RuntimeError("signed-blue Supervisor immutable pin is incomplete")
    pin = {
        field: str(signed_blue_pin.get(field) or "").strip()
        for field in _PIN_FIELDS
    }
    if not all(pin.values()):
        raise RuntimeError("signed-blue Supervisor immutable pin is incomplete")
    expected_identity = (
        pin["supervisor_id"],
        pin["endpoint"],
        pin["creator"],
    )
    if _candidate_identity(candidate) != expected_identity:
        raise RuntimeError(
            "pre-managed-query Supervisor candidate differs from signed-blue identity"
        )
    assert_runtime_creator(
        pin["creator"],
        application_id=runtime_application_id,
        resource="signed-blue Supervisor candidate",
    )
    endpoint_id = exact_supervisor_endpoint_id(
        workspace,
        endpoint_name=pin["endpoint"],
        runtime_application_id=runtime_application_id,
    )
    if endpoint_id != pin["endpoint_id"]:
        raise RuntimeError("signed-blue Supervisor endpoint identity drifted")
    try:
        assert_contract(
            pin["supervisor_id"],
            genie_space_id=genie_space_id,
            catalog=catalog,
        )
    except SupervisorContractDrift as exc:
        raise RuntimeError("signed-blue Supervisor contract drifted") from exc

    refreshed = supervisor_candidates(
        list_agents(),
        display_name=display_name,
        genie_space_id=genie_space_id,
        catalog=catalog,
    )
    refreshed_candidate = _pre_managed_query_candidate(refreshed)
    if (
        refreshed.canonical is not None
        or refreshed_candidate is None
        or _candidate_identity(refreshed_candidate) != expected_identity
    ):
        raise RuntimeError(
            "signed-blue Supervisor candidate changed before canonical finalization"
        )
    assert_single_writer()
    rename_agent(pin["supervisor_id"], display_name)

    final = supervisor_candidates(
        list_agents(),
        display_name=display_name,
        genie_space_id=genie_space_id,
        catalog=catalog,
    )
    if (
        final.canonical is None
        or _candidate_identity(final.canonical) != expected_identity
        or final.replacement is not None
        or final.legacy_replacement is not None
    ):
        raise RuntimeError("signed-blue Supervisor canonical finalization failed")
    if (
        exact_supervisor_endpoint_id(
            workspace,
            endpoint_name=pin["endpoint"],
            runtime_application_id=runtime_application_id,
        )
        != pin["endpoint_id"]
    ):
        raise RuntimeError("signed-blue Supervisor endpoint drifted during finalization")
    assert_contract(
        pin["supervisor_id"],
        genie_space_id=genie_space_id,
        catalog=catalog,
    )
    return final
