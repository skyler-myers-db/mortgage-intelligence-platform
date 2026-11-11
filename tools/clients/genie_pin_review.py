"""The owner's read-only review of the Genie space a pin plan names (cutover 10B-0).

Before an adoption no pin record and no signed rollback record names the
governed Genie space, so the step-0 pin check refuses the one space that
carries the profile's title and prints its read-only pin plan: the candidate
space, its direct CAN_MANAGE holders, the deploying identity and the plan's
sha256 (tools/databricks/genie_space_pin.py). Under GitHub Actions the first
three are runner masks (tools/clients/redact.emit_mask), so the job log shows
``***`` in their place; the digest is printed as it is. This is the review's
other half, run by the owner with their own CLI profile, the profile's
``MIP_IDENTITY_IDS_JSON``, ``MIP_UC_APPROVED_OWNER_PRINCIPALS`` and
``MIP_GENIE_MANAGER_PRINCIPALS`` exported in that shell for that one command
(read from the shell only, never ``.env.local``):

  DATABRICKS_CONFIG_PROFILE=<owner-profile> python -m tools.clients.genie_pin_review --app-name <app-name> --genie-space-name "<genie-space-title>"

For the one space that carries the title it prints:

* the pin plan's sha256, computed as the pin check computes it
  (``genie_space_pin.pin_plan``) from the profile's App name and title, that
  space, its direct CAN_MANAGE holders and the profile's deployer
  (``MIP_IDENTITY_IDS_JSON.deployer``, the principal the deploy's live check
  proves the CI run is). It equals the CI log's ``pin plan sha256`` only when
  CI saw this space, these holders and that deployer, so the counts below
  describe the plan a ``genie_pin`` ack would name;
* how many principals hold a direct CAN_MANAGE (the CI plan's creator line
  lists one ``***`` per holder) and whether the caller is one of them;
* how many of the reviewed managers (``MIP_GENIE_MANAGER_PRINCIPALS``) are
  among them, and how many holders are outside that list;
* how many principals hold CAN_EDIT or CAN_MANAGE on the space, direct or
  inherited, that the pre-stop owner-equivalent probe does not approve
  (tools/databricks/prestop_owner_equivalent.py, its own functions: the
  approved owners, the deployer and the reviewed Genie managers, compared
  case-folded, and the platform's ``admins`` group). 10B-1 refuses each one
  (``owner_equivalent``) before the lease.

Counts never say who a holder is: an expected creator and a stranger in its
place count the same, so the profile's approval lists, not this review, name
who may hold the space. It prints no space id and no principal name. Its reads
are the pin check's own: the paged space list, one permissions GET and the
caller's identity. Exit 0: exactly one space carries the title. Exit 2: none
or several do, a read failed (its class only), or ``MIP_IDENTITY_IDS_JSON`` is
unset, invalid or names no deployer.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from tools.clients.json_fields import IdentityPins
from tools.databricks import genie_space_pin
from tools.databricks.prestop_owner_equivalent import (
    APPROVAL_GENIE,
    APPROVAL_UC,
    OwnerEquivalentRefusal,
    approved_principals,
    assert_owner_equivalent_grantees,
    genie_owner_equivalent_grants,
)
from tools.databricks.uc_owner_policy import parse_approved_owner_principals

_PREFIX = "[genie-pin-review]"
MANAGERS_ENV = APPROVAL_GENIE
OWNERS_ENV = APPROVAL_UC
IDENTITY_IDS_ENV = "MIP_IDENTITY_IDS_JSON"


@dataclass(frozen=True)
class Review:
    """Counts, booleans and a digest only: never an id or a principal name."""

    matches: int
    holders: int = 0
    reviewed: int = 0
    reviewed_holding: int = 0
    caller_holds: bool = False
    plan_sha256: str = ""
    unapproved: int = 0


class ReviewInputError(ValueError):
    """A shell input the review cannot use; the message names the field, never a value."""


def profile_deployer(raw: str) -> str:
    """``MIP_IDENTITY_IDS_JSON.deployer`` as the profile pins it; "" when the shell has none."""

    if not raw.strip():
        return ""
    try:
        pins = IdentityPins.model_validate_json(raw)
    except ValueError:  # a validation message can quote the value
        raise ReviewInputError(f"{IDENTITY_IDS_ENV} in this shell is not a valid identity map") from None
    return pins.deployer or ""


def _workspace() -> Any:
    from tools.databricks.workspace_auth import deployment_workspace_client

    return deployment_workspace_client()


def _unapproved(acl: Mapping[str, Any], *, space_id: str, approved: Any) -> int:
    """How many grantees 10B-1's owner-equivalent probe refuses on this space."""

    grants = genie_owner_equivalent_grants([(space_id, acl)], space_id=space_id)
    try:
        assert_owner_equivalent_grantees(grants, approved=approved)
    except OwnerEquivalentRefusal as refusal:
        return len(refusal.grants)
    return 0


def review(
    workspace: Any,
    title: str,
    reviewed_managers: str = "",
    *,
    app_name: str = "",
    deployer: str = "",
    approved_owners: str = "",
) -> Review:
    """Count the title matches and, for the one match, recompute its pin plan and count its holders."""

    matches = genie_space_pin.title_matches(workspace, title)
    if len(matches) != 1:
        return Review(matches=len(matches))
    space = matches[0]
    space_id = str(getattr(space, "space_id", None) or getattr(space, "id", None) or "").strip()
    if not space_id:
        raise ValueError("the Genie space with the governed title has no id")
    acl = genie_space_pin.space_acl(workspace, space_id)
    managers = genie_space_pin.managers_in_acl(acl)
    plan = genie_space_pin.pin_plan(
        app_name=app_name, title=title, space_id=space_id, managers=managers, deployer=deployer
    )
    holders = {name.casefold() for name in managers}
    reviewed = {name.casefold() for name in parse_approved_owner_principals(reviewed_managers)}
    caller = str(getattr(workspace.current_user.me(), "user_name", "") or "").strip().casefold()
    approved = approved_principals(
        approved_owners=approved_owners, deployer=deployer, genie_managers=reviewed_managers
    )
    return Review(
        matches=1,
        holders=len(holders),
        reviewed=len(reviewed),
        reviewed_holding=len(reviewed & holders),
        caller_holds=bool(caller) and caller in holders,
        plan_sha256=genie_space_pin.plan_sha256(plan),
        unapproved=_unapproved(acl, space_id=space_id, approved=approved),
    )


def _report(found: Review, *, owners_set: bool) -> None:
    print(
        f"{_PREFIX} direct CAN_MANAGE holders of that space: {found.holders} "
        f"(the caller is one: {'yes' if found.caller_holds else 'no'})"
    )
    if found.reviewed:
        print(
            f"{_PREFIX} reviewed Genie managers ({MANAGERS_ENV}) among them: "
            f"{found.reviewed_holding} of {found.reviewed}; holders outside that list: "
            f"{found.holders - found.reviewed_holding}"
        )
    else:
        print(
            f"{_PREFIX} {MANAGERS_ENV} is unset in this shell: export the profile's value for this "
            "command to compare the holders with the reviewed managers"
        )
    if not owners_set:
        print(f"{_PREFIX} {OWNERS_ENV} is unset in this shell: the count below approves no owner")
    print(
        f"{_PREFIX} principals with CAN_EDIT or CAN_MANAGE on that space (direct or inherited) that "
        f"the pre-stop owner-equivalent probe does not approve ({OWNERS_ENV}, {MANAGERS_ENV}, the "
        f"deployer, and the platform's admins group): {found.unapproved}"
    )
    if found.unapproved:
        print(
            f"{_PREFIX} 10B-1 refuses each of them (owner_equivalent) before the lease: approve that "
            "exact principal in the profile, or remove its grant with its owner's consent, then "
            "review again"
        )
    print(
        f"{_PREFIX} pin plan sha256 for that space, its holders and the profile's deployer "
        f"({IDENTITY_IDS_ENV}.deployer): {found.plan_sha256}"
    )
    print(
        f"{_PREFIX} the CI pin plan's creator line must list exactly {found.holders} masked entries "
        "and its pin plan sha256 must equal this one before it is passed as genie_pin"
    )


def main(
    argv: list[str] | None = None,
    *,
    workspace_factory: Callable[[], Any] = _workspace,
    environ: Mapping[str, str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--app-name", required=True, help="the profile's MIP_APP_NAME")
    parser.add_argument("--genie-space-name", required=True, help="the profile's MIP_GENIE_SPACE_NAME")
    args = parser.parse_args(argv)
    env = os.environ if environ is None else environ
    try:
        deployer = profile_deployer(env.get(IDENTITY_IDS_ENV, ""))
    except ReviewInputError as exc:
        print(f"{_PREFIX} REFUSED: {exc}", file=sys.stderr)
        return 2
    owners = env.get(OWNERS_ENV, "")
    try:
        workspace = workspace_factory()
        if not deployer:
            print(
                f"{_PREFIX} REFUSED: {IDENTITY_IDS_ENV} is unset in this shell or names no deployer: "
                "the pin plan digest and the probe's approved principals both cover the CI "
                "deployer, so export the profile's value for this command",
                file=sys.stderr,
            )
            return 2
        found = review(
            workspace,
            args.genie_space_name.strip(),
            env.get(MANAGERS_ENV, ""),
            app_name=args.app_name.strip(),
            deployer=deployer,
            approved_owners=owners,
        )
    except Exception as exc:  # the class only: a message could carry an id
        print(f"{_PREFIX} REFUSED: the read did not complete ({type(exc).__name__})", file=sys.stderr)
        return 2
    print(f"{_PREFIX} Genie spaces carrying the governed title: {found.matches}")
    if found.matches != 1:
        print(
            f"{_PREFIX} REFUSED: the pin check pins exactly one title match; with {found.matches} it "
            "refuses too, and no genie_pin ack can change that",
            file=sys.stderr,
        )
        return 2
    _report(found, owners_set=bool(owners.strip()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
