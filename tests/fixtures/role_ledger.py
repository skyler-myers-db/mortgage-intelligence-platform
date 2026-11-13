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
* each call the dry run's interpreter really ran (``Execution``): a tool, run
  with ``-m`` or by path behind any interpreter flag, by the table below, and
  inline code, ``-c`` or a script on stdin (a heredoc), by what it reaches;
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
account API through its imports (``account_api_reach``).

Inline code has no table entry, so it is held to that same rule directly
(review of #330): it is ``account_admin`` when it imports the human
bootstrap, and ``workspace`` only when its text names no account API, none
of the repository modules it imports reaches an account API, and everything
it names is on the reviewed allowlist of tests/fixtures/inline_code_review.py
(rounds 3 and 4: the modules and attributes, the repository modules and the
builtins the dry runs' own inline code uses, and no ``match``). That module
says what it does not model: a returned value's own public attributes, and
what the code writes for a later process to load. Otherwise inline code is
``unclassified``. A ``databricks api`` call is ``workspace`` only on a
reviewed workspace path, and any path with an ``accounts`` segment is an
account call.

A read is ``read``: the human bootstrap reads the account under the account
admin's own fresh login (tools/clients/bootstrap_session.py), and no CI step
reads the account but the account principal's bounded SCIM reads.

Standard library only: the stub CLI imports it from the sandbox clone.
"""

from __future__ import annotations

import ast
import functools
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from tests.fixtures.inline_code_review import Repository, inline_refusals

REPO = Path(__file__).resolve().parents[2]
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
        "tools.databricks.activation_prediction",
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
# Named by a core-tier deploy file, but neither planned nor run by the plan
# and release dry runs the proof replays: a live-only branch (``DRY_RUN -eq
# 0``), another phase, or a mention in a message. Outside the proof (review
# of #330), and unclassified should a dry run ever reach one; listed so that
# no tool joins a deploy file unclassed (the integrity module checks it).
OUTSIDE_PROOF_MODULES = frozenset(
    {
        "tools.clients.estate_inventory",
        "tools.clients.install_checks",
        "tools.clients.lakebase_handover",
        "tools.clients.policy",
        "tools.databricks.adopt_live",
        "tools.databricks.agent_proxy_credential_bundle",
        "tools.databricks.app_core_rollback",
        "tools.databricks.app_deployment_freeze",
        "tools.databricks.app_deployment_lease",
        "tools.databricks.app_deployment_rollback",
        "tools.databricks.app_description_fixup",
        "tools.databricks.app_first_install_journal",
        "tools.databricks.app_release_contract",
        "tools.databricks.app_resource_bindings",
        "tools.databricks.app_rollback_bootstrap_gate",
        "tools.databricks.app_rollback_secret_scope",
        "tools.databricks.audit_agent_runtime_foreign_uc_access",
        "tools.databricks.audit_global_m2m_access",
        "tools.databricks.bundle_state",
        "tools.databricks.catalog_bootstrap_skip_rule",
        "tools.databricks.converge_app_release_access",
        "tools.databricks.core_blue",
        "tools.databricks.core_blue_capture",
        "tools.databricks.cutover_agent_runtime_supervisor",
        "tools.databricks.data_phase_app_identity",
        "tools.databricks.deploy_prestop_probes",
        "tools.databricks.estate.inventory",
        "tools.databricks.estate.inventory_check",
        "tools.databricks.first_install_bootstrap",
        "tools.databricks.gateway_model_archival_cli",
        "tools.databricks.genie_space_pin",
        "tools.databricks.install_state_adopt",
        "tools.databricks.job_phase_runner",
        "tools.databricks.m2m_identity_contract",
        "tools.databricks.oauth_credential_recovery_cli",
        "tools.databricks.pii_salt_scope",
        "tools.databricks.plan_report_publish",
        "tools.databricks.provision_agent_proxy_secret",
        "tools.databricks.quiesce_window",
        "tools.databricks.quiesce_window_plan",
        "tools.databricks.quiesce_window_watchdog",
        "tools.databricks.stop_app_fail_closed",
        "tools.databricks.stop_primitive_map",
        "tools.databricks.verify_agent_runtime_uc_boundary_dual_authority",
        "tools.databricks.verify_market_rate_alignment",
        "tools.frontend_deps_check",
        "tools.oauth_m2m_mint",
        "tools.sync_lifecycle_warehouse",
    }
)
# The ones that reach an account API through their imports: their live
# authority is unproven here, and a tool joins this list only by review.
OUTSIDE_PROOF_ACCOUNT_REACH = frozenset(
    {
        "tools.clients.estate_inventory",
        "tools.clients.install_checks",
        "tools.databricks.adopt_live",
        "tools.databricks.app_core_rollback",
        "tools.databricks.app_deployment_rollback",
        "tools.databricks.audit_agent_runtime_foreign_uc_access",
        "tools.databricks.audit_global_m2m_access",
        "tools.databricks.core_blue_capture",
        "tools.databricks.deploy_prestop_probes",
        "tools.databricks.first_install_bootstrap",
        "tools.databricks.oauth_credential_recovery_cli",
        "tools.databricks.provision_agent_proxy_secret",
        "tools.databricks.verify_agent_runtime_uc_boundary_dual_authority",
    }
)

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
# ``databricks api <verb> <path>``: the workspace REST paths the deploy calls,
# reviewed (the SQL Statement Execution API). Any other path is unclassified,
# and a path or argument with an ``accounts`` segment anywhere
# (``/api/2.0/accounts/...``, ``/api/2.0/preview/accounts/...``) is an
# account call (review of #330).
WORKSPACE_API_PATHS = (re.compile(r"/api/2\.0/sql/statements(?:/[A-Za-z0-9_-]+)*/?"),)
_ACCOUNTS_SEGMENT = re.compile(r"(?:^|/)accounts(?:/|$)")

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
# A tool by module name, and by path behind any directory. The deploy-file
# reader (tests/fixtures/portability_checks.py) names tools with both, and the
# call classifier reads a path with the same TOOL_PATH (review of #330, round
# 3), so a path one of them reads as a tool the other does too.
TOOL_MODULE = re.compile(r"tools(?:\.\w+)+")
TOOL_PATH = re.compile(r"(?:.*/)?(tools(?:/\w+)*/\w+)\.py")


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
        if any(_ACCOUNTS_SEGMENT.search(token) for token in rest[1:]):
            return ACCOUNT_ADMIN
        reviewed = len(rest) >= 3 and any(path.fullmatch(rest[2]) for path in WORKSPACE_API_PATHS)
        return WORKSPACE if reviewed else UNCLASSIFIED
    return WORKSPACE if rest[0] in WORKSPACE_CLI_GROUPS else UNCLASSIFIED


def _target(args: Sequence[str]) -> tuple[str, str | None]:
    """What an interpreter's arguments run, past the reviewed flags.

    ``("module", name)``, ``("path", script)``, ``("code", text)`` for ``-c``,
    ``("stdin", None)`` for ``-`` or no script at all, or ``("unknown", flag)``
    for a flag the map does not know.
    """

    index = 0
    while index < len(args) and args[index] in _PY_FLAGS:
        index += 1
    if index >= len(args) or args[index] == "-":
        return "stdin", None
    head, value = args[index], args[index + 1] if index + 1 < len(args) else None
    if head == "-m":
        return "module", value
    if head == "-c":
        return "code", value
    return ("unknown", head) if head.startswith("-") else ("path", head)


def _tool(kind: str, value: str | None) -> str | None:
    """The tool a target runs (``-m tools.x`` or ``tools/x.py``), or ``None``."""

    if kind == "module":
        return value
    if kind == "path" and value is not None and (match := TOOL_PATH.fullmatch(value)) is not None:
        return match.group(1).replace("/", ".")
    return None


def _module(tokens: Sequence[str]) -> str | None:
    """The tool a planned Python invocation runs, its interpreter first, or ``None``."""

    return _tool(*_target(tokens[1:]))


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


# --- what a module reaches through its imports ----------------------------------------

# An account API in source: the SDK's account client, its account-level
# service classes or an accounts REST path (``/api/2.0/accounts/...`` and
# the workspace proxies under ``/api/2.0/preview/accounts/...``).
ACCOUNT_API = re.compile(r"\bAccountClient\b|\bAccount[A-Z]\w*API\b|/api/2\.[0-9]/(?:[\w.-]+/)*accounts\b")
REPO_PACKAGES = frozenset({"tools", "backend"})


def module_files(module: str, repo: Path = REPO) -> tuple[Path, ...]:
    """A module's source files; a package's ``__init__`` and ``__main__`` (``-m`` runs both)."""

    base = repo.joinpath(*module.split("."))
    if base.with_suffix(".py").is_file():
        return (base.with_suffix(".py"),)
    return tuple(path for path in (base / "__init__.py", base / "__main__.py") if path.is_file())


def _import_names(tree: ast.AST) -> set[str]:
    """Every module name a tree imports, at any depth (lazy imports too), with each ``from`` name."""

    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


@functools.cache
def _imports(module: str, repo: Path) -> frozenset[str]:
    """The repository modules one module imports."""

    found: set[str] = set()
    for path in module_files(module, repo):
        found |= _import_names(ast.parse(path.read_text(encoding="utf-8")))
    return frozenset(name for name in found if name.split(".")[0] in REPO_PACKAGES and module_files(name, repo))


def account_api_reach(module: str, repo: Path = REPO) -> list[str]:
    """The modules in ``module``'s import closure whose source names an account API."""

    return list(_reach(module, repo))


@functools.cache
def _reach(module: str, repo: Path) -> tuple[str, ...]:
    seen: set[str] = set()
    todo = [module]
    while todo:
        name = todo.pop()
        if name not in seen:
            seen.add(name)
            todo.extend(_imports(name, repo))
    return tuple(
        sorted(name for name in seen if any(ACCOUNT_API.search(path.read_text(encoding="utf-8")) for path in module_files(name, repo)))
    )


# --- what the deploy interpreter really ran -----------------------------------------------


@dataclass(frozen=True)
class Execution:
    """One call the deploy interpreter really ran: its arguments and, for a script read from stdin, that script."""

    argv: tuple[str, ...]
    stdin: str | None = None

    def code(self) -> str | None:
        """The inline code it ran (``-c`` or stdin), or ``None`` for a tool or an unknown shape."""

        kind, value = _target(self.argv)
        return value if kind == "code" else self.stdin if kind == "stdin" else None

    def tool(self) -> str | None:
        """The tool it ran (``-m`` or by path, behind any reviewed flag), or ``None``."""

        return _tool(*_target(self.argv))

    def shown(self) -> str:
        return " ".join(self.argv) + (" (script on stdin)" if _target(self.argv)[0] == "stdin" else "")


def repository(repo: Path = REPO) -> Repository:
    """The repository's modules, as the inline-code review reads them."""

    return Repository(packages=REPO_PACKAGES, sources=lambda name: module_files(name, repo))


def inline_imports(code: str | None, repo: Path = REPO) -> frozenset[str]:
    """The repository modules inline code imports (none when it does not parse)."""

    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return frozenset()
    names = _import_names(tree)
    return frozenset(name for name in names if name.split(".")[0] in REPO_PACKAGES and module_files(name, repo))


def inline_role(code: str | None, repo: Path = REPO) -> str:
    """The class of inline code: ``workspace`` only when it names nothing unreviewed and nothing it imports reaches an account API."""

    if code is None:
        return UNCLASSIFIED
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return UNCLASSIFIED
    modules = inline_imports(code, repo)
    # The human bootstrap is no reviewed inline import: read it first, so it is named as the need it is.
    if modules & HUMAN_ONLY_MODULES:
        return ACCOUNT_ADMIN
    if ACCOUNT_API.search(code) or inline_refusals(tree, repository(repo)):
        return UNCLASSIFIED
    return UNCLASSIFIED if any(account_api_reach(module, repo) for module in modules) else WORKSPACE


def executed_role(call: Execution, repo: Path = REPO) -> str:
    """The class of one call the deploy interpreter ran: a tool by the table, inline code by what it reaches."""

    if _target(call.argv)[0] in ("code", "stdin"):
        return inline_role(call.code(), repo)
    tool = call.tool()
    return UNCLASSIFIED if tool is None else module_role(tool)


def sdk_role(plane: str, api: str, method: str) -> str:
    """The class of one SDK call the stub account API answered."""

    if method in READ_METHODS:
        return READ
    return SDK_ROLES.get((plane, api, method), UNCLASSIFIED)


def account_admin_needs(
    *,
    cli: Iterable[Sequence[str]] = (),
    planned: Iterable[str] = (),
    executed: Iterable[Execution] = (),
    sdk: Iterable[tuple[str, str, str]] = (),
) -> list[str]:
    """Every call that needs account-admin authority or that the map cannot class."""

    needs = [f"cli ({role}): databricks {' '.join(argv)}" for argv in cli if (role := cli_role(argv)) in NEEDS]
    needs += [f"planned ({role}): {command}" for command in planned if (role := planned_role(command)) in NEEDS]
    needs += [f"executed ({role}): {call.shown()}" for call in executed if (role := executed_role(call)) in NEEDS]
    for plane, api, method in sdk:
        role = sdk_role(plane, api, method)
        if role in NEEDS:
            needs.append(f"sdk ({role}): {plane}.{api}.{method}")
    return needs
