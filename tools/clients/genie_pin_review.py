"""The owner's read-only review of the Genie space a pin plan names, in counts (cutover 10B-0).

Before an adoption no pin record and no signed rollback record names the
governed Genie space, so the step-0 pin check refuses the one space that
carries the profile's title and prints its read-only pin plan: the candidate
space, its direct CAN_MANAGE holders and the deploying identity
(tools/databricks/genie_space_pin.py). Under GitHub Actions each of those is
a runner mask (tools/clients/redact.emit_mask), so the job log shows ``***``
in their place, and the plan's digest covers the CI deployer, so the owner
can neither read the plan from the log nor recompute its digest. This is the
review's other half, run by the owner with their own CLI profile, the
profile's ``MIP_GENIE_MANAGER_PRINCIPALS`` exported in that shell for that one
command:

  DATABRICKS_CONFIG_PROFILE=<owner-profile> python -m tools.clients.genie_pin_review --genie-space-name "<genie-space-title>"

It prints how many Genie spaces carry the title (the pin check pins only
exactly one), how many principals hold a direct CAN_MANAGE on that one (the
CI plan's creator line lists one ``***`` per holder), how many of the
reviewed managers (``MIP_GENIE_MANAGER_PRINCIPALS``, read from the shell
only, never ``.env.local``; compared as the pre-stop owner-equivalent probe
compares them, case-folded) are among them, how many holders are outside that
list, and whether the caller is one of them. It prints no space id and no
principal name. Its reads are the pin check's own: the paged space list, one
permissions GET and the caller's identity. Exit 0: exactly one space carries
the title. Exit 2: none, several, or a read that failed (its class only).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from tools.databricks import genie_space_pin
from tools.databricks.uc_owner_policy import parse_approved_owner_principals

_PREFIX = "[genie-pin-review]"
MANAGERS_ENV = "MIP_GENIE_MANAGER_PRINCIPALS"


@dataclass(frozen=True)
class Review:
    """Counts and booleans only: never an id or a principal name."""

    matches: int
    holders: int = 0
    reviewed: int = 0
    reviewed_holding: int = 0
    caller_holds: bool = False


def _workspace() -> Any:
    from tools.databricks.workspace_auth import deployment_workspace_client

    return deployment_workspace_client()


def review(workspace: Any, title: str, reviewed_managers: str = "") -> Review:
    """Count the title matches and, for the one match, its direct CAN_MANAGE holders."""

    matches = genie_space_pin.title_matches(workspace, title)
    if len(matches) != 1:
        return Review(matches=len(matches))
    space = matches[0]
    space_id = str(getattr(space, "space_id", None) or getattr(space, "id", None) or "").strip()
    if not space_id:
        raise ValueError("the Genie space with the governed title has no id")
    holders = {name.casefold() for name in genie_space_pin.direct_managers(workspace, space_id)}
    reviewed = {name.casefold() for name in parse_approved_owner_principals(reviewed_managers)}
    caller = str(getattr(workspace.current_user.me(), "user_name", "") or "").strip().casefold()
    return Review(
        matches=1,
        holders=len(holders),
        reviewed=len(reviewed),
        reviewed_holding=len(reviewed & holders),
        caller_holds=bool(caller) and caller in holders,
    )


def main(
    argv: list[str] | None = None,
    *,
    workspace_factory: Callable[[], Any] = _workspace,
    environ: Mapping[str, str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--genie-space-name", required=True, help="the profile's MIP_GENIE_SPACE_NAME")
    args = parser.parse_args(argv)
    managers = (os.environ if environ is None else environ).get(MANAGERS_ENV, "")
    try:
        found = review(workspace_factory(), args.genie_space_name, managers)
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
    print(
        f"{_PREFIX} the CI pin plan's creator line must list exactly {found.holders} masked entries "
        "before its pin plan sha256 is passed as genie_pin"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
