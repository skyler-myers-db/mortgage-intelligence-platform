"""The authority each Databricks call needs, as the portability proof's stubs record it (M3-PR4).

Attack G15: a first install must never need account-admin authority in CI.
The account admin's one-time work is human-run (``bootstrap_account``, its
``--post-first-install`` included), so every call a CI step makes, or plans
to make, needs at most the deployer's workspace authority or the account
principal's Service Principal Manager role on the identities it was given.

Four ledgers are classed here:

* each ``databricks`` CLI invocation the stub CLI answers
  (tests/fixtures/role_recording_cli.py), by its argv;
* each command a dry run plans (its ``$ <command>`` lines), the calls a live
  run would make;
* each ``-m`` module the dry run's interpreter really ran;
* each account or workspace SDK call the stub account API answers
  (``FakeEstate.calls`` in tests/fixtures/fake_databricks_account.py, which
  records every call the bootstrap makes).

Every class is read from a reviewed table, never from what a command looks
like or says about itself (review of #330): the deploy makes its account
calls through Python tools, so a planned tool this file has not reviewed is
``unclassified``, and so is a reviewed workspace tool planned under the
account identity's wrapper, a tool reviewed for that wrapper planned without
it, an unknown executable or an unknown CLI command group or global flag.
``account_admin_needs`` reports ``unclassified`` like an account-admin need:
the map fails closed. tests/unit/test_client_portability_integrity.py checks
the table against the code: no tool reviewed as ``workspace`` reaches an
account API through its imports.

A read is ``read``: the human bootstrap reads the account under the account
admin's own fresh login (tools/clients/bootstrap_session.py), and no CI step
reads the account but the account principal's bounded SCIM reads.

Standard library only: the stub CLI imports it from the sandbox clone.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence

NONE = "none"
READ = "read"
WORKSPACE = "workspace"  # at most the deployer's workspace authority
ACCOUNT_PRINCIPAL = "account_principal"  # Service Principal Manager on the enumerated identities only
SECURABLE_OWNER = "securable_owner"  # the owner of a Unity Catalog securable
SP_MANAGER = "service_principal_manager"
ACCOUNT_ADMIN = "account_admin"
UNCLASSIFIED = "unclassified"
NEEDS = frozenset({ACCOUNT_ADMIN, UNCLASSIFIED})

# The tools a core-tier dispatch plans or runs, reviewed: none reaches an
# account API through its imports (the integrity module checks it). A tool
# not listed here, or in the two sets below, is unclassified.
WORKSPACE_MODULES = frozenset(
    {
        "tools.branding.lender_mark",
        "tools.clients",
        "tools.databricks.active_deployment_pin",
        "tools.databricks.app_deploy_payload",
        "tools.databricks.app_release_prune",
        "tools.databricks.app_release_source",
        "tools.databricks.app_runtime_closure",
        "tools.databricks.blue_restore_proof",
        "tools.databricks.bundle_env",
        "tools.databricks.bundle_plan_gate",
        "tools.databricks.converge_app_lakebase_sync_access",
        "tools.databricks.converge_static_app_source",
        "tools.databricks.ensure_asset_lineage_view",
        "tools.databricks.ensure_campaign_treatment_table",
        "tools.databricks.jobs_identity_acl",
        "tools.databricks.monitor_scheduler_gate",
        "tools.databricks.phase_budget",
        "tools.databricks.prebuilt_release",
        "tools.databricks.provision_genie_space",
        "tools.databricks.provision_m2m_oauth",
        "tools.databricks.provision_runtime_secrets",
        "tools.databricks.quiesce_window_budget",
        "tools.databricks.verify_reviewed_function_execute_grants",
        "tools.databricks.verify_scheduled_refresh_state",
        "tools.python_deps_check",
        "tools.render_sql",
        "tools.requirements_digest",
    }
)
# Reviewed for run_with_account_identity only: the account principal's
# bounded SCIM reads of the identities it was given.
ACCOUNT_IDENTITY_MODULES = frozenset(
    {
        "tools.databricks.converge_campaign_treatment_access",
        "tools.databricks.converge_lakebase_oauth_role",
        "tools.databricks.ensure_pipeline_namespace",
    }
)
# Reviewed to authenticate as the profile's account principal itself
# (tools/clients/install_checks.py ``account_client``): SCIM reads only.
ACCOUNT_PRINCIPAL_MODULES = frozenset({"tools.clients.live_checks"})
# The account admin's own work, human-run under a fresh login.
HUMAN_ONLY_MODULES = frozenset({"tools.clients.bootstrap_account", "tools.clients.bootstrap_session"})
REVIEWED_MODULES = WORKSPACE_MODULES | ACCOUNT_IDENTITY_MODULES | ACCOUNT_PRINCIPAL_MODULES

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

# The CLI: the global flags it may carry before its command group (with or
# without a value), and the workspace command groups the deploy uses.
CLI_VALUE_FLAGS = frozenset({"-p", "--profile", "-o", "--output", "-t", "--target", "--log-level", "--log-file", "--log-format"})
CLI_BARE_FLAGS = frozenset({"--debug"})
WORKSPACE_CLI_GROUPS = frozenset({"apps", "bundle"})
_WORKSPACE_API_PATH = re.compile(r"/api/2\.[0-9]/(?!accounts(?:/|$))\S+")

# The planned-command label of run_with_account_identity
# (scripts/lib/deploy_identity_lifecycle.sh); a module is read from the front
# of a command, so no other label needs reading.
ACCOUNT_IDENTITY_LABEL = "(bounded account-SCIM identity)"
# Shell wrappers that only add the deployer's own authority or a retry.
WRAPPERS = frozenset({"run_with_proof_signing_authority", "run_job_with_retry"})
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
# The deploy interpreter: a python, or the proof sandbox's recording wrapper.
_PYTHON = re.compile(r"(?:deploy-)?python(?:[0-9]+(?:\.[0-9]+)*)?")
_PY_FLAGS = frozenset({"-P", "-I", "-B", "-E", "-s", "-S", "-u", "-O"})
_TOOL_PATH = re.compile(r"(?:.*/)?(tools(?:/\w+)*/\w+)\.py")


def cli_role(argv: Sequence[str]) -> str:
    """The class of one ``databricks`` CLI invocation, by its arguments; unknown is unclassified."""

    args = list(argv)
    if args in (["--version"], ["version"]):
        return NONE
    index = 0
    while index < len(args) and args[index].startswith("-"):
        flag, valued = args[index].split("=", 1)[0], "=" in args[index]
        if flag in CLI_BARE_FLAGS and not valued:
            index += 1
        elif flag in CLI_VALUE_FLAGS:
            index += 1 if valued else 2
        else:
            return UNCLASSIFIED
    rest = args[index:]
    if not rest:
        return UNCLASSIFIED
    if rest[0] == "account":
        return ACCOUNT_ADMIN
    if rest[0] == "api":
        # ``api <verb> <path>``: an accounts path, or any path but a workspace API, is no workspace call.
        if len(rest) >= 3 and _WORKSPACE_API_PATH.fullmatch(rest[2]):
            return WORKSPACE
        return ACCOUNT_ADMIN if any("/accounts" in token for token in rest[1:]) else UNCLASSIFIED
    return WORKSPACE if rest[0] in WORKSPACE_CLI_GROUPS else UNCLASSIFIED


def _module(tokens: Sequence[str]) -> str | None:
    """The tool a Python invocation runs (``-m tools.x`` or ``tools/x.py``), or ``None``."""

    index = 1
    while index < len(tokens) and tokens[index] in _PY_FLAGS:
        index += 1
    if index + 1 < len(tokens) and tokens[index] == "-m":
        return tokens[index + 1]
    if index < len(tokens) and (match := _TOOL_PATH.fullmatch(tokens[index])) is not None:
        return match.group(1).replace("/", ".")
    return None


def module_role(module: str, *, account_identity: bool = False) -> str:
    """The class of one tool, by the reviewed table and the wrapper it runs under."""

    if module in HUMAN_ONLY_MODULES:
        return ACCOUNT_ADMIN
    if account_identity:
        return ACCOUNT_PRINCIPAL if module in ACCOUNT_IDENTITY_MODULES else UNCLASSIFIED
    if module in ACCOUNT_PRINCIPAL_MODULES:
        return ACCOUNT_PRINCIPAL
    return WORKSPACE if module in WORKSPACE_MODULES else UNCLASSIFIED


def planned_role(command: str) -> str:
    """The class of one planned command, as a live run would execute it."""

    account_identity = command.rstrip().endswith(ACCOUNT_IDENTITY_LABEL)
    tokens = command.split()
    while tokens and (tokens[0] in WRAPPERS or _ASSIGNMENT.match(tokens[0])):
        tokens = tokens[1:]
    if not tokens:
        return UNCLASSIFIED
    executable = tokens[0].rsplit("/", 1)[-1]
    if executable == "databricks":
        return UNCLASSIFIED if account_identity else cli_role(tokens[1:])
    module = _module(tokens) if _PYTHON.fullmatch(executable) else None
    return UNCLASSIFIED if module is None else module_role(module, account_identity=account_identity)


def sdk_role(plane: str, api: str, method: str) -> str:
    """The class of one SDK call the stub account API answered."""

    if method in READ_METHODS:
        return READ
    return SDK_ROLES.get((plane, api, method), UNCLASSIFIED)


def account_admin_needs(
    *,
    cli: Iterable[Sequence[str]] = (),
    planned: Iterable[str] = (),
    executed: Iterable[str] = (),
    sdk: Iterable[tuple[str, str, str]] = (),
) -> list[str]:
    """Every call that needs account-admin authority or that the map cannot class."""

    needs = [f"cli ({role}): databricks {' '.join(argv)}" for argv in cli if (role := cli_role(argv)) in NEEDS]
    needs += [f"planned ({role}): {command}" for command in planned if (role := planned_role(command)) in NEEDS]
    needs += [f"executed ({role}): -m {module}" for module in executed if (role := module_role(module)) in NEEDS]
    for plane, api, method in sdk:
        role = sdk_role(plane, api, method)
        if role in NEEDS:
            needs.append(f"sdk ({role}): {plane}.{api}.{method}")
    return needs
