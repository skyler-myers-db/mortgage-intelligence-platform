"""The App's metastore privileges, proven without a metastore-admin deployer (spike S0(b′)).

The treatment converger (``_assert_metastore_boundary``), its read-only
``--check`` (which adopt-live's treatment fact runs) and the pre-stop owner
probe refuse unless the App's service principal holds no metastore privilege
outside ``SAFE_METASTORE_PRIVILEGES``. Reading that set takes
``grants.get_effective('metastore', <id>, principal=<App>)``, which only a
metastore admin may make for another principal. Measured on 2026-11-11 as a
non-admin workspace service principal: HTTP 403 PERMISSION_DENIED, never an
empty listing. The same read with no principal returned HTTP 200 with only the
caller's own view, so no read here is ever unfiltered; and a principal-filtered
read returns the grants that reach the principal through its groups (the
assignment then names the group, ``account users``), so every returned
assignment is summed, never filtered by principal.

``app_metastore_privileges`` is the one proof all of them use:

* a metastore-admin deployer (``uc_metastore_read.deployer_is_metastore_admin``)
  reads the set live;
* any other deployer needs the metastore owner's attestation, the exact ack
  ``MIP_ACK_METASTORE_APP_GRANTS`` (dispatch purpose ``metastore_app_grants``,
  admitted in ``plan``, ``adopt``, ``unadopt`` and ``release``, the phases
  whose runs read it; shell only, never ``.env.local``). The owner prints it,
  read-only, with ``python -m tools.databricks.metastore_app_grants --app-name
  <app-name>``: the sha256 of a body naming this purpose, the metastore's id,
  its owner, the App's application id, the App's effective metastore
  privileges as the owner reads them (groups included) and the UTC date. The
  deployer recomputes that body from what it reads itself (the metastore's id
  and owner, the App) for every date the age rule admits and every privilege
  set the converger accepts; the ack must equal one exactly, or the run
  refuses. A date is admitted while the run's end (now plus the 24-hour
  ``RUN_MARGIN``) is at most 30 days after the start of that UTC day, the
  estate inventory's rule (tools/databricks/estate/inventory.py). The deployer
  cannot re-read the privileges, so the ack stands for the owner's read on
  that date; a grant made after it is the attestation's residual, bounded by
  the 30 days;
* a 403 on the live read, a missing or non-matching ack, or an owner the
  summary does not return: each is a value-free ``MetastoreAuthorityUnproven``
  refusal, never an empty set.

An ack given to an admin deployer is verified too, and the live set must equal
the attested one: a given ack is never ignored.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import itertools
import os
import re
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from databricks.sdk.errors import PermissionDenied
from tools.databricks.bundle_plan_shape import canonical_json
from tools.databricks.uc_metastore_read import (
    MetastoreReadRefused,
    current_metastore,
    deployer_is_metastore_admin,
    readable_owner,
)

PURPOSE = "metastore_app_grants"
ACK_ENV = "MIP_ACK_METASTORE_APP_GRANTS"
# The only metastore privilege the App may hold: the built-in ``account users``
# grant every principal receives (spike S0(b′) measured exactly this set).
SAFE_METASTORE_PRIVILEGES = frozenset({"USE_MARKETPLACE_ASSETS"})
MAX_AGE = timedelta(days=30)
# The reviewed bound on one deploy run (estate.inventory.RUN_MARGIN).
RUN_MARGIN = timedelta(hours=24)
LIVE_ROUTE = "a live read as a metastore admin"
ATTESTED_ROUTE = "the metastore owner's attestation"
_DOMAIN = b"mip-metastore-app-grants"
_DIGEST = re.compile(r"[0-9a-f]{64}")
_PREFIX = "[mip-metastore-grants]"
_ATTEST = (
    f"the metastore owner's attestation: {ACK_ENV} (operation_acks "
    f"{PURPOSE}=<sha256>), which `python -m tools.databricks.metastore_app_grants "
    "--app-name <app-name>` prints when the owner runs it read-only"
)

EffectiveReader = Callable[..., Iterable[str]]


class MetastoreAuthorityUnproven(RuntimeError):
    """The App's metastore privileges are not proven; the message names no value."""


@dataclass(frozen=True)
class AppMetastorePrivileges:
    """The App's effective metastore privileges and how they were proven."""

    privileges: frozenset[str]
    route: str


def attestation_body(
    *,
    metastore_id: str,
    metastore_owner: str,
    app_application_id: str,
    privileges: Iterable[str],
    attested_on: date,
) -> dict[str, str]:
    """The canonical body the owner's ack digests."""

    return {
        "purpose": PURPOSE,
        "metastore_id": metastore_id,
        "metastore_owner": metastore_owner,
        "app_application_id": app_application_id,
        "privileges": ",".join(sorted(set(privileges))),
        "attested_on": attested_on.isoformat(),
    }


def attestation_digest(**fields: Any) -> str:
    """The sha256 ack over ``attestation_body(**fields)``, domain-separated."""

    body = attestation_body(**fields)
    return hashlib.sha256(_DOMAIN + b"\0" + canonical_json(body).encode("ascii")).hexdigest()


def admissible_dates(now: datetime) -> list[date]:
    """Every UTC attestation date a run starting at ``now`` accepts, newest first."""

    end = now.astimezone(UTC) + RUN_MARGIN
    today = now.astimezone(UTC).date()
    dates: list[date] = []
    for back in range(MAX_AGE.days + 1):
        day = today - timedelta(days=back)
        if end - datetime.combine(day, time.min, UTC) > MAX_AGE:
            break
        dates.append(day)
    return dates


def last_run_start(attested_on: date) -> datetime:
    """The latest moment a run may start and still accept an attestation of ``attested_on``."""

    return datetime.combine(attested_on, time.min, UTC) + MAX_AGE - RUN_MARGIN


def _accepted_sets() -> list[frozenset[str]]:
    names = sorted(SAFE_METASTORE_PRIVILEGES)
    return [
        frozenset(subset)
        for size in range(len(names) + 1)
        for subset in itertools.combinations(names, size)
    ]


def attested_privileges(
    *,
    ack: str,
    metastore_id: str,
    metastore_owner: str,
    app_application_id: str,
    now: datetime,
) -> frozenset[str]:
    """The privilege set the owner attested, when ``ack`` is exactly one admitted attestation."""

    if _DIGEST.fullmatch(ack) is None:
        raise MetastoreAuthorityUnproven(
            f"{ACK_ENV} must be the 64-lowercase-hex sha256 the owner's attestation printed"
        )
    for day in admissible_dates(now):
        for privileges in _accepted_sets():
            candidate = attestation_digest(
                metastore_id=metastore_id,
                metastore_owner=metastore_owner,
                app_application_id=app_application_id,
                privileges=privileges,
                attested_on=day,
            )
            if hmac.compare_digest(candidate, ack):
                return privileges
    raise MetastoreAuthorityUnproven(
        f"{ACK_ENV} attests no reviewed privilege set for this metastore, its current owner and "
        "this App on any date the 30-day rule admits (spike S0(b′)): it was taken for another "
        "metastore, owner or App, the App held a privilege outside the reviewed set, or it is too "
        "old. Take the attestation again as the metastore owner"
    )


def app_metastore_privileges(
    workspace: Any,
    *,
    metastore: object,
    principal: str,
    effective: EffectiveReader,
    environ: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> AppMetastorePrivileges:
    """The App's effective metastore privileges, read live by an admin or attested by the owner.

    ``metastore`` is ``uc_metastore_read.current_metastore``'s summary;
    ``effective`` is the converger's ``_effective_privileges``.
    """

    env = os.environ if environ is None else environ
    ack = env.get(ACK_ENV, "").strip()
    metastore_id = str(getattr(metastore, "metastore_id", "") or "")
    owner = readable_owner(metastore)
    if not owner or not metastore_id:
        raise MetastoreAuthorityUnproven(
            "the metastore summary returned no canonical owner, so neither a live read nor the "
            "owner's attestation can be judged (spike S0(b′))"
        )
    attested = None
    if ack:
        attested = attested_privileges(
            ack=ack,
            metastore_id=metastore_id,
            metastore_owner=owner,
            app_application_id=principal,
            now=now or datetime.now(UTC),
        )
    if not deployer_is_metastore_admin(workspace, metastore):
        if attested is None:
            raise MetastoreAuthorityUnproven(
                "the deploying identity is not a metastore admin, so it cannot read the App's "
                "metastore privileges (another principal's metastore grants are denied to it, "
                f"spike S0(b′)); the run needs {_ATTEST}"
            )
        return AppMetastorePrivileges(attested, ATTESTED_ROUTE)
    try:
        live = frozenset(
            effective(
                workspace, securable_type="metastore", full_name=metastore_id, principal=principal
            )
        )
    except PermissionDenied as exc:
        raise MetastoreAuthorityUnproven(
            "the App's effective metastore privileges were denied to the deploying identity "
            "(PERMISSION_DENIED), so it is not a metastore admin after all (spike S0(b′)); a denied "
            f"read is never an empty set. The run needs {_ATTEST}"
        ) from exc
    if attested is not None and live != attested:
        raise MetastoreAuthorityUnproven(
            f"the App's live metastore privileges differ from the owner's attestation ({ACK_ENV}): "
            "a grant changed after it was taken; review the grant, then take it again"
        )
    return AppMetastorePrivileges(live, LIVE_ROUTE)


def _refusal_text(exc: BaseException) -> str:
    """A refusal as printed: this tooling's own lines; anything else by class and code only."""

    if isinstance(exc, MetastoreAuthorityUnproven | MetastoreReadRefused):
        return str(exc)
    code = getattr(exc, "error_code", None)
    detail = f" ({code})" if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", code) else ""
    return f"{type(exc).__name__}{detail}: its text is withheld, because it can name a principal or an id"


def attest(workspace: Any, *, app_name: str, now: datetime) -> tuple[int, list[str]]:
    """Read the App's metastore privileges as the metastore owner; ``(exit, lines)``."""

    from tools.clients.redact import emit_mask
    from tools.databricks.converge_campaign_treatment_access import _effective_privileges

    metastore = current_metastore(workspace)
    if not deployer_is_metastore_admin(workspace, metastore):
        return 2, [
            "REFUSED: run this as the metastore's owner (or a member of its owning group): "
            "another principal's metastore grants are denied to anyone else (spike S0(b′))"
        ]
    app = workspace.apps.get(app_name)
    client_id = emit_mask(str(getattr(app, "service_principal_client_id", "") or "").strip())
    if not client_id:
        return 2, ["REFUSED: the App has no service principal application id"]
    privileges = frozenset(
        _effective_privileges(
            workspace,
            securable_type="metastore",
            full_name=str(metastore.metastore_id),
            principal=client_id,
        )
    )
    forbidden = privileges - SAFE_METASTORE_PRIVILEGES
    if forbidden:
        return 2, [
            "REFUSED: the App's service principal holds metastore privileges outside the reviewed "
            f"set: {', '.join(sorted(forbidden))}. The treatment converger refuses them; revoke "
            "them (the owner's decision), then attest again. No digest was printed"
        ]
    day = now.astimezone(UTC).date()
    digest = attestation_digest(
        metastore_id=str(metastore.metastore_id),
        metastore_owner=readable_owner(metastore),
        app_application_id=client_id,
        privileges=privileges,
        attested_on=day,
    )
    held = ", ".join(sorted(privileges)) or "none"
    return 0, [
        f"the App's effective metastore privileges, as the metastore owner reads them (groups "
        f"included): {held}",
        f"attested on {day.isoformat()} (UTC); a run that starts before "
        f"{last_run_start(day).strftime('%Y-%m-%dT%H:%MZ')} accepts it",
        f"{PURPOSE}={digest}",
        f"pass it in operation_acks (or export {ACK_ENV}=<the sha256 above> in the shell) for "
        "each plan, adopt, unadopt or release run while the deployer is not a metastore admin",
    ]


def main(
    argv: list[str] | None = None,
    *,
    workspace_factory: Callable[[], Any] | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--app-name", required=True)
    args = parser.parse_args(argv)
    try:
        if workspace_factory is None:
            from databricks.sdk import WorkspaceClient

            workspace = WorkspaceClient()
        else:
            workspace = workspace_factory()
        code, lines = attest(workspace, app_name=args.app_name, now=now())
    except Exception as exc:  # noqa: BLE001 - the class only: an SDK message can name a principal
        code, lines = 2, [f"REFUSED: the attestation could not be read: {_refusal_text(exc)}"]
    stream = sys.stdout if code == 0 else sys.stderr
    for line in lines:
        print(f"{_PREFIX} {line}", file=stream)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
