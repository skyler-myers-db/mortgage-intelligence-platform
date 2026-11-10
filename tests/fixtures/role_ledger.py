"""The authority each Databricks call needs, as the portability proof's stubs record it (M3-PR4).

Attack G15: a first install must never need account-admin authority in CI.
The account admin's one-time work is human-run (``bootstrap_account``, its
``--post-first-install`` included), so every call a CI step makes, or plans
to make, needs at most the deployer's workspace authority or the account
principal's Service Principal Manager role on the identities it was given.

Three ledgers are classed here:

* each ``databricks`` CLI invocation the stub CLI answers
  (tests/fixtures/role_recording_cli.py), by its argv;
* each command a dry run plans (its ``$ <command>`` lines), the calls a live
  run would make;
* each account or workspace SDK call the stub account API answers
  (``FakeEstate.calls`` in tests/fixtures/fake_databricks_account.py, which
  records every call the bootstrap makes).

A class names the authority a call needs to make its change. A read is
``read``: the human bootstrap reads the account under the account admin's own
fresh login (tools/clients/bootstrap_session.py), and no CI step reads the
account but the account principal's bounded SCIM reads. An SDK call this map
does not know is ``unclassified``, which ``account_admin_needs`` reports like an
account-admin need: the map fails closed.

Standard library only: the stub CLI imports it from the sandbox clone.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence

NONE = "none"
READ = "read"
WORKSPACE = "workspace"  # the deployer's workspace authority
ACCOUNT_PRINCIPAL = "account_principal"  # Service Principal Manager on the enumerated identities only
SECURABLE_OWNER = "securable_owner"  # the owner of a Unity Catalog securable
SP_MANAGER = "service_principal_manager"
ACCOUNT_ADMIN = "account_admin"
UNCLASSIFIED = "unclassified"

# The account API's changes, by (plane, api, method), as the fake account
# names them. Anything else that is not a read is unclassified.
SDK_ROLES: Mapping[tuple[str, str, str], str] = {
    ("account", "service_principals", "create"): ACCOUNT_ADMIN,
    ("account", "service_principal_federation_policy", "create"): ACCOUNT_ADMIN,
    ("account", "service_principal_federation_policy", "update"): ACCOUNT_ADMIN,
    ("account", "workspace_assignment", "update"): ACCOUNT_ADMIN,
    ("account", "groups", "create"): ACCOUNT_ADMIN,
    ("account", "groups", "patch"): ACCOUNT_ADMIN,
    # A role grant on a service principal: on the App's own identity, which
    # the workspace created, only an account admin holds the manager role.
    ("account", "access_control", "update_rule_set"): ACCOUNT_ADMIN,
    ("account", "service_principal_secrets", "create"): SP_MANAGER,
    ("account", "service_principal_secrets", "delete"): SP_MANAGER,
    ("workspace", "grants", "update"): SECURABLE_OWNER,
}
READ_METHODS = frozenset({"list", "get", "get_rule_set", "current", "get_workspace_id", "me"})

_ACCOUNT_CLI = re.compile(r"(?:^|\s)(?:\S*/)?databricks\s+account(?:\s|$)")
_ACCOUNT_API = re.compile(r"(?:^|\s)(?:\S*/)?databricks\s+api\s+\S+\s+/?api/2\.[0-9]/accounts/")
_HUMAN_BOOTSTRAP = re.compile(r"tools\.clients\.bootstrap_account\b.*\s--(?:apply|rotate-adopted|post-first-install)\b")
_ACCOUNT_IDENTITY = "(bounded account-SCIM identity)"


def cli_role(argv: Sequence[str]) -> str:
    """The class of one ``databricks`` CLI invocation, by its arguments."""

    if list(argv[:1]) == ["--version"]:
        return NONE
    if argv[:1] and argv[0] == "account":
        return ACCOUNT_ADMIN
    if len(argv) >= 3 and argv[0] == "api" and "/accounts/" in argv[2]:
        return ACCOUNT_ADMIN
    return WORKSPACE


def planned_role(command: str) -> str:
    """The class of one planned command, as a live run would execute it."""

    if _ACCOUNT_CLI.search(command) or _ACCOUNT_API.search(command) or _HUMAN_BOOTSTRAP.search(command):
        return ACCOUNT_ADMIN
    if command.rstrip().endswith(_ACCOUNT_IDENTITY):
        return ACCOUNT_PRINCIPAL
    return WORKSPACE


def sdk_role(plane: str, api: str, method: str) -> str:
    """The class of one SDK call the stub account API answered."""

    if method in READ_METHODS:
        return READ
    return SDK_ROLES.get((plane, api, method), UNCLASSIFIED)


def account_admin_needs(
    *,
    cli: Iterable[Sequence[str]] = (),
    planned: Iterable[str] = (),
    sdk: Iterable[tuple[str, str, str]] = (),
) -> list[str]:
    """Every call that needs account-admin authority or that the map cannot class."""

    needs = [f"cli: databricks {' '.join(argv)}" for argv in cli if cli_role(argv) == ACCOUNT_ADMIN]
    needs += [f"planned: {command}" for command in planned if planned_role(command) == ACCOUNT_ADMIN]
    for plane, api, method in sdk:
        role = sdk_role(plane, api, method)
        if role in (ACCOUNT_ADMIN, UNCLASSIFIED):
            needs.append(f"sdk ({role}): {plane}.{api}.{method}")
    return needs
