"""Pre-stop probe: the approved-owner policy on the governed Unity Catalog objects.

``converge_campaign_treatment_access`` runs ``ApprovedOwnerPolicy.assert_objects``
on the metastore, the governed catalog, its audit schema and the treatment
table after step 0a has quiesced (and, on the rebase path, stopped) the App.
An owner outside ``MIP_UC_APPROVED_OWNER_PRINCIPALS`` plus the deployer, or the
App's own service principal, then fails with the App down. This probe calls the
same method (``ASSERT``) on the same objects (``OBJECTS``) first, while the App
still serves, and names each refusing object and its owner.

One part of the policy cannot run read-only. For a group owner the policy asks
a membership probe whether the App's service principal belongs to that group;
the post-stop converger proves non-membership by minting a short-lived OAuth
credential for the App, which is a mutation. Here the probe answers from the
App's workspace-visible groups (plus ``users``) instead: a visible membership
refuses now; an invisible one (an account-level nested group) is still caught
by the post-stop proof. So this is an early refusal, not a replacement. Every
other check (account and workspace SCIM resolution, the exact-name rules, the
target-alias exclusion) is the policy's own.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from tools.databricks.converge_campaign_treatment_access import _object_presence
from tools.databricks.prestop_refusals import OWNER_POLICY, PrestopRefusal
from tools.databricks.uc_owner_policy import (
    ApprovedOwnerPolicy,
    account_client_from_env,
)
from tools.databricks.uc_target_identity import workspace_target_identity

# The policy method and object reads the post-stop converger uses (identity).
ASSERT = ApprovedOwnerPolicy.assert_objects
OBJECTS = _object_presence
_LABELS = ("governed catalog", "audit schema", "treatment table")
_REMEDY = (
    "Either transfer ownership to an approved owner (MIP_UC_APPROVED_OWNER_PRINCIPALS or "
    "the deployer; never the App's service principal), or, after the owner's review, add "
    "that exact owner to MIP_UC_APPROVED_OWNER_PRINCIPALS. Then re-run."
)


def read_only_membership_probe(
    app_groups: frozenset[str],
) -> Callable[[Any, str, str, str, str], bool]:
    """Answer the policy's group question from the App's workspace-visible groups."""

    def probe(
        _account: Any, _account_sp_id: str, _application_id: str, _group_id: str, group: str
    ) -> bool:
        return group in app_groups

    return probe


def _governed_objects(workspace: Any, *, catalog: str) -> list[tuple[str, object]]:
    assignment = workspace.metastores.current()
    metastore_id = str(getattr(assignment, "metastore_id", "") or "").strip()
    if not metastore_id:
        raise RuntimeError("the current workspace has no authoritative metastore identifier")
    objects: list[tuple[str, object]] = [("metastore", workspace.metastores.get(metastore_id))]
    objects.extend(
        (label, item)
        for label, item in zip(_LABELS, OBJECTS(workspace, catalog=catalog), strict=True)
        if item is not None
    )
    return objects


def owner_policy_refusals(
    workspace: Any,
    *,
    app_client_id: str,
    catalog: str,
    approved_owner_principals: Iterable[str],
    app_groups: frozenset[str],
    account_factory: Callable[[], Any] = account_client_from_env,
) -> tuple[list[PrestopRefusal], list[str]]:
    """Refuse every governed object whose owner the post-stop policy would refuse."""

    if not app_client_id:
        return [], ["no App exists yet; the owner policy runs against its SP after creation"]
    try:
        policy = ApprovedOwnerPolicy(
            workspace=workspace,
            target=workspace_target_identity(workspace, application_id=app_client_id),
            configured_principals=set(approved_owner_principals),
            account_factory=account_factory,
            group_membership_probe=read_only_membership_probe(app_groups),
        )
        objects = _governed_objects(workspace, catalog=catalog)
    except Exception as exc:  # noqa: BLE001 - an unreadable policy input refuses
        return [
            PrestopRefusal(
                OWNER_POLICY,
                f"cannot evaluate the approved-owner policy before step 0a: {exc}. The probe "
                "refuses rather than pass an owner it could not read. Then re-run.",
            )
        ], []
    refusals: list[PrestopRefusal] = []
    for label, item in objects:
        try:
            ASSERT(policy, (item,))
        except Exception as exc:  # noqa: BLE001 - each object refuses on its own
            owner = str(getattr(item, "owner", "") or "<missing>")
            refusals.append(
                PrestopRefusal(
                    OWNER_POLICY,
                    f"the {label} owner {owner!r} fails the approved-owner policy that runs "
                    f"after step 0a stops the App: {exc}. {_REMEDY}",
                )
            )
    notes = [] if refusals else [f"approved-owner policy holds on {len(objects)} governed objects"]
    return refusals, notes
