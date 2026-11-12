"""The deployer's read of the workspace's metastore: its summary, never ``metastores.get``.

``metastores.get`` (``GET /api/2.1/unity-catalog/metastores/{id}``) is
metastore-admin only: SDK 0.103.0's ``MetastoresAPI.get`` and the REST
reference both say "The caller must be a metastore admin to retrieve this
info". The governed deployer is a workspace admin, not a metastore admin, and
by the owner's decision a deploy never transfers the metastore. So every
deployer-run tool reads the metastore through ``current_metastore``:

* ``metastores.current()`` (``GET .../current-metastore-assignment``): the
  workspace's assignment, its ``metastore_id`` and no owner;
* ``metastores.summary()`` (``GET .../metastore_summary``): no documented
  caller restriction, and the same ``owner`` field as ``MetastoreInfo``. It
  must name the assignment's metastore, exactly.

Measured on 2026-11-11 as a fresh workspace service principal with no grants
and no admin group (spike S0(b′)): the summary returned it the same 20 fields
the owner gets, its ``owner`` present, canonical and byte-equal to the owner's
own read, and its ``metastore_id`` equal to the assignment's. An owner the
summary does not return, or returns padded, still refuses: ``owner_state``
tells the two apart, so a refusal never calls a data fault a withheld read.

``deployer_is_metastore_admin`` answers whether the deploying identity may
read another principal's metastore grants: the summary's owner, casefolded, is
its ``/Me`` user name, application id or one of its ``/Me`` groups. Anything
unproven (no readable owner, a group owner with no groups in the projection)
is "not an admin". Measured the same day as that non-admin service principal:
``grants.get_effective('metastore', <id>, principal=<another>)`` is HTTP 403
PERMISSION_DENIED (fail closed), while the same read with no ``principal``
returns HTTP 200 holding only the caller's own view (one assignment against
the owner's thirteen), a listing nothing may treat as complete.

tests/unit/test_uc_metastore_read.py pins that no tool calls
``metastores.get`` outside three owner-run or denial-expecting modules, that
no script, workflow or job does, and that every metastore grant read names
its principal.
"""

from __future__ import annotations

from typing import Any

OWNER_CANONICAL = "canonical"
# The summary has no owner attribute, or it is None or "": a withheld read.
OWNER_ABSENT = "absent"
# The summary returns an owner, but not a canonical principal name: padded,
# blank or not a string. A fault in the metastore's data, not a withheld read.
OWNER_NONCANONICAL = "noncanonical"


class MetastoreReadRefused(RuntimeError):
    """The metastore cannot be read as the deployer; the message names no value."""


def _text(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _field(value: object, name: str) -> object:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def current_metastore(workspace: Any) -> Any:
    """The summary of the workspace's current metastore; never ``metastores.get``.

    The returned object carries ``metastore_id`` and ``owner``, the attributes
    the owner policies read. Read errors propagate; callers refuse on them.
    """

    metastore_id = _text(getattr(workspace.metastores.current(), "metastore_id", None))
    if not metastore_id:
        raise MetastoreReadRefused("the current workspace has no authoritative metastore identifier")
    summary = workspace.metastores.summary()
    summary_id = getattr(summary, "metastore_id", None)
    if not isinstance(summary_id, str) or summary_id != summary_id.strip() or summary_id != metastore_id:
        raise MetastoreReadRefused(
            "the metastore summary names another metastore than the workspace's current assignment"
        )
    return summary


def owner_state(item: object) -> str:
    """``OWNER_CANONICAL``, ``OWNER_ABSENT`` (withheld) or ``OWNER_NONCANONICAL`` (bad data)."""

    owner = getattr(item, "owner", None)
    if owner is None or owner == "":
        return OWNER_ABSENT
    if not isinstance(owner, str) or owner != owner.strip() or not owner.strip():
        return OWNER_NONCANONICAL
    return OWNER_CANONICAL


def readable_owner(item: object) -> str:
    """The owner when it is a canonical principal name, else ``''``."""

    return str(getattr(item, "owner", "")) if owner_state(item) == OWNER_CANONICAL else ""


def caller_names(workspace: Any, *, require_groups: bool = True) -> frozenset[str]:
    """The deployer's own names and its ``/Me`` groups, casefolded (what an owner field names).

    With ``require_groups`` a projection without groups refuses; without it,
    the caller's own names stand alone (its groups unproven).
    """

    me = workspace.current_user.me()
    names = {_text(_field(me, "user_name")), _text(_field(me, "application_id"))}
    groups = _field(me, "groups")
    if isinstance(groups, list):
        names.update(_text(_field(group, "display")) for group in groups)
    elif require_groups:
        if groups is None:
            raise RuntimeError("the deploying identity's /Me projection omitted its groups")
        raise RuntimeError("Databricks ACL inventory returned a malformed collection")
    return frozenset(name.casefold() for name in names if name)


def deployer_is_metastore_admin(workspace: Any, metastore: object) -> bool:
    """Whether the deploying identity owns the metastore or is in its owning group.

    Only such an identity may read another principal's metastore grants. An
    unreadable owner, or a group owner the ``/Me`` projection does not list,
    is not proven, so it is "no".
    """

    owner = readable_owner(metastore)
    if not owner:
        return False
    return owner.casefold() in caller_names(workspace, require_groups=False)


__all__ = [
    "OWNER_ABSENT",
    "OWNER_CANONICAL",
    "OWNER_NONCANONICAL",
    "MetastoreReadRefused",
    "caller_names",
    "current_metastore",
    "deployer_is_metastore_admin",
    "owner_state",
    "readable_owner",
]
