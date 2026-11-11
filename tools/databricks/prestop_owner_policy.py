"""Pre-stop probe: the approved-owner policy on the governed Unity Catalog objects.

``converge_campaign_treatment_access`` runs ``ApprovedOwnerPolicy.assert_objects``
on the governed catalog, its audit schema and the treatment table, and
``MetastoreOwnerPolicy`` (exactly ``MIP_UC_METASTORE_OWNER``) on the metastore,
after step 0a has quiesced (and, on the rebase path, stopped) the App. An
owner outside ``MIP_UC_APPROVED_OWNER_PRINCIPALS`` plus the deployer, a
metastore owner other than the configured one, or the App's own service
principal, then fails with the App down. This probe calls the same methods
(``ASSERT`` and ``ASSERT_METASTORE``) on the same objects (``OBJECTS``) first,
while the App still serves, and names each refusing object and its owner.

One part of the policy cannot run read-only. For a group owner the policy asks
a membership probe whether the App's service principal belongs to that group;
the post-stop converger proves non-membership by minting a short-lived OAuth
credential for the App, which is a mutation. Here the probe answers from the
App's workspace-visible groups (plus ``users``) instead: a visible membership
refuses now; an invisible one (an account-level nested group) is still caught
by the post-stop proof. So this is an early refusal, not a replacement. Every
other check (account and workspace SCIM resolution, the exact-name rules, the
target-alias exclusion) is the policy's own.

``interim_pairs`` (M2-PR8, ``tools/databricks/uc_interim_owner_pairs.py``) is
the policy's exact, expiring exception for a pipeline dataset the ownership
handover leaves with its owner. None of ``OBJECTS`` is a pipeline dataset, so
the deploy passes none; the handover's ``--verify`` judges every governed
object with the client's pairs.

The metastore's owner comes from its summary (``uc_metastore_read``), because
``metastores.get`` is metastore-admin only and the deployer is not one. Once
the metastore's owner passes, the probe also proves what the converger proves
next, the App's metastore privileges (``metastore_app_grants``, spike S0(b′)):
read live by a metastore-admin deployer, else attested by the owner's exact
ack. An unproven set refuses ``metastore_authority`` here, while the App still
serves, rather than in the release's treatment quiesce after the lease.

A refusing object's owner is registered as a runner mask before it is printed
(``redact.emit_mask``): the line may reach a public CI log. ``users``,
``account users`` and ``admins`` are platform vocabulary, never masked.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from tools.clients.redact import emit_mask
from tools.clients.shapes import PRINCIPAL_VOCABULARY
from tools.databricks.converge_campaign_treatment_access import (
    _effective_privileges,
    _object_presence,
)
from tools.databricks.metastore_app_grants import (
    SAFE_METASTORE_PRIVILEGES,
    MetastoreAuthorityUnproven,
    app_metastore_privileges,
)
from tools.databricks.prestop_refusals import METASTORE_AUTHORITY, OWNER_POLICY, PrestopRefusal
from tools.databricks.uc_interim_owner_pairs import InterimOwnerPairs
from tools.databricks.uc_metastore_read import (
    OWNER_ABSENT,
    OWNER_NONCANONICAL,
    current_metastore,
    owner_state,
)
from tools.databricks.uc_owner_policy import (
    METASTORE_OWNER_ENV,
    ApprovedOwnerPolicy,
    MetastoreOwnerPolicy,
    account_client_from_env,
)
from tools.databricks.uc_target_identity import workspace_target_identity

# The policy methods and object reads the post-stop converger uses (identity).
ASSERT = ApprovedOwnerPolicy.assert_objects
ASSERT_METASTORE = MetastoreOwnerPolicy.assert_objects
OBJECTS = _object_presence
# The metastore read and the App's metastore-privilege proof the converger uses (identity).
READ_METASTORE = current_metastore
AUTHORITY = app_metastore_privileges
METASTORE = "metastore"
_LABELS = ("governed catalog", "audit schema", "treatment table")
_REMEDY = (
    "Either transfer ownership to an approved owner (MIP_UC_APPROVED_OWNER_PRINCIPALS or "
    "the deployer; never the App's service principal), or, after the owner's review, add "
    "that exact owner to MIP_UC_APPROVED_OWNER_PRINCIPALS. Then re-run."
)
_METASTORE_REMEDY = (
    f"The metastore owner must be exactly {METASTORE_OWNER_ENV} (never users, account "
    "users, admins, the App's service principal or a group holding it): after the "
    f"client's review, set {METASTORE_OWNER_ENV} to the metastore's actual owner in the "
    "client profile. No metastore transfer is part of a deploy. Then re-run."
)
_METASTORE_UNREAD_REMEDY = (
    "The deploying identity cannot read the metastore's owner (spike S0(b)): no profile value "
    f"stands in for the read, so {METASTORE_OWNER_ENV} cannot be checked. Stop: the owner-read "
    "follow-up (docs/deployment.md, \"Metastore owner\") must land first. Nothing was changed."
)
_METASTORE_NONCANONICAL_REMEDY = (
    "The metastore summary returns an owner that is not a canonical principal name (padded, "
    "blank or not a string): a fault in the metastore's own data, not a withheld read, so no "
    "follow-up for spike S0(b) helps. Have the metastore's owner read it and correct it. "
    "Nothing was changed."
)
_METASTORE_AUTHORITY_REMEDY = (
    "The treatment converger proves the same set after step 0a stops the App, so the probe "
    "refuses now, while the App still serves. Then re-run."
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
    # The summary, never metastores.get (metastore-admin only): spike S0(b).
    objects: list[tuple[str, object]] = [(METASTORE, READ_METASTORE(workspace))]
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
    metastore_owner: str,
    account_factory: Callable[[], Any] = account_client_from_env,
    interim_pairs: InterimOwnerPairs | None = None,
) -> tuple[list[PrestopRefusal], list[str]]:
    """Refuse every governed object whose owner the post-stop policy would refuse."""

    if not app_client_id:
        return [], ["no App exists yet; the owner policy runs against its SP after creation"]
    try:
        target = workspace_target_identity(workspace, application_id=app_client_id)
        probe = read_only_membership_probe(app_groups)
        policy = ApprovedOwnerPolicy(
            workspace=workspace,
            target=target,
            configured_principals=set(approved_owner_principals),
            account_factory=account_factory,
            group_membership_probe=probe,
            interim_pairs=interim_pairs,
        )
        metastore_policy = MetastoreOwnerPolicy(
            workspace=workspace,
            target=target,
            account_factory=account_factory,
            group_membership_probe=probe,
            metastore_owner=metastore_owner,
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
    metastore_passed = False
    for label, item in objects:
        try:
            if label == METASTORE:
                ASSERT_METASTORE(metastore_policy, (item,))
                metastore_passed = True
            else:
                ASSERT(policy, (item,))
        except Exception as exc:  # noqa: BLE001 - each object refuses on its own
            owner = _shown_owner(item)
            remedy = _remedy(label, item)
            refusals.append(
                PrestopRefusal(
                    OWNER_POLICY,
                    f"the {label} owner {owner!r} fails the approved-owner policy that runs "
                    f"after step 0a stops the App: {exc}. {remedy}",
                )
            )
    notes = [] if refusals else [f"approved-owner policy holds on {len(objects)} governed objects"]
    if metastore_passed:
        found, said = metastore_authority_refusals(
            workspace, metastore=objects[0][1], app_client_id=app_client_id
        )
        refusals.extend(found)
        notes.extend(said)
    return refusals, notes


def _shown_owner(item: object) -> str:
    """The owner as the refusal prints it, registered as a runner mask first."""

    if owner_state(item) == OWNER_ABSENT:
        return "<missing>"
    owner = getattr(item, "owner", None)
    if not isinstance(owner, str):
        return "<not a string>"
    if owner.strip() and not _vocabulary(owner):
        emit_mask(owner.strip())
    return owner


def _vocabulary(owner: object) -> bool:
    return isinstance(owner, str) and " ".join(owner.split()).casefold() in PRINCIPAL_VOCABULARY


def _remedy(label: str, item: object) -> str:
    if label != METASTORE:
        return _REMEDY
    state = owner_state(item)
    if state == OWNER_ABSENT:
        return _METASTORE_UNREAD_REMEDY
    if state == OWNER_NONCANONICAL and not _vocabulary(getattr(item, "owner", None)):
        return _METASTORE_NONCANONICAL_REMEDY
    return _METASTORE_REMEDY


def metastore_authority_refusals(
    workspace: Any, *, metastore: object, app_client_id: str
) -> tuple[list[PrestopRefusal], list[str]]:
    """What the converger's metastore proof would refuse after step 0a, judged now."""

    try:
        proof = AUTHORITY(
            workspace, metastore=metastore, principal=app_client_id, effective=_effective_privileges
        )
    except MetastoreAuthorityUnproven as exc:
        return [PrestopRefusal(METASTORE_AUTHORITY, f"{exc}. {_METASTORE_AUTHORITY_REMEDY}")], []
    except Exception as exc:  # noqa: BLE001 - the class only: an SDK message can name a principal
        return [
            PrestopRefusal(
                METASTORE_AUTHORITY,
                f"the App's metastore privileges could not be read before step 0a "
                f"({type(exc).__name__}); the probe refuses rather than read an unreadable grant "
                f"list as empty. {_METASTORE_AUTHORITY_REMEDY}",
            )
        ], []
    forbidden = proof.privileges - SAFE_METASTORE_PRIVILEGES
    if forbidden:
        return [
            PrestopRefusal(
                METASTORE_AUTHORITY,
                "the App's service principal holds forbidden metastore privileges "
                f"{sorted(forbidden)} ({proof.route}), which the treatment converger refuses after "
                "step 0a stops the App. Revoke them with the metastore owner's consent, then re-run.",
            )
        ], []
    return [], [f"the App holds no metastore privilege outside the reviewed set ({proof.route})"]
