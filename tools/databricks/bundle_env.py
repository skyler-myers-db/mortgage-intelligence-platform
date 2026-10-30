"""Run a Databricks CLI command with `.env.local` sourced via python-dotenv.

The Makefile target that sources `.env.local` directly in bash broke on
unquoted spaces (e.g. `MIP_LENDER_NAME=Summit Mortgage`) and angle-bracket
placeholder values (`GENIE_SPACE_ID=<genie-space-id>`). This helper uses
python-dotenv's parser, which handles both correctly, and then launches the
Databricks CLI in a subprocess with the resolved env so operator workflow
stays `make bundle-validate-env`. Mutable deployment is intentionally reserved
for `scripts/deploy.sh`, which supplies exact non-App resource selectors and
then performs the signed App promotion.

Targets (M1-PR2): ``-t client`` is the one deployable target and needs the
resolved client profile (tools/clients); ``-t ci`` is validate-only, with
placeholder principals and no profile. Every other target, and a missing one,
is refused.

Mapping, ``-t client`` (the profile -> BUNDLE_VAR_*, tools/clients/profile.py):
  MIP_CLIENT              -> BUNDLE_VAR_client_key (the root leaf, the mip_client tag)
  MIP_IDENTITY_IDS_JSON.deployer -> BUNDLE_VAR_deployer_principal (root owner, deploy-time run_as)
  MIP_IDENTITY_IDS_JSON.jobs -> BUNDLE_VAR_jobs_principal (run_as of the App-triggerable
                             and refresh jobs and the pipeline, M2-PR5)
  MIP_DASHBOARD_PARENT_PATH -> BUNDLE_VAR_dashboard_parent_path (when set)
  MIP_LAKEBASE_READABLE_SECONDARIES -> BUNDLE_VAR_lakebase_readable_secondaries (when set)
  MIP_DEFAULT_CATALOG     -> BUNDLE_VAR_uc_catalog
  MIP_APP_NAME            -> BUNDLE_VAR_app_name
  MIP_LAKEBASE_INSTANCE   -> BUNDLE_VAR_lakebase_instance_name
  MIP_LAKEBASE_SYNC_CATALOG -> BUNDLE_VAR_lakebase_catalog_name
  LAKEBASE_DATABASE       -> BUNDLE_VAR_lakebase_database_name
  MIP_RUNTIME_SECRET_SCOPE -> BUNDLE_VAR_runtime_secret_scope
  MIP_IDENTITY_IDS_JSON.verifier -> BUNDLE_VAR_ai_gateway_verifier_client_id
  MIP_GENIE_SPACE_NAME    -> the governed Genie title resolved below
For ``-t ci`` the same names come from the environment and ``.env.local``
with their defaults, as before the client target.

Mapping, every target (env -> BUNDLE_VAR_*):
  DATABRICKS_WAREHOUSE_ID -> BUNDLE_VAR_sql_warehouse_id
  GENIE_SPACE_ID          -> BUNDLE_VAR_genie_space_id
  MIP_LENDER_NAME         -> BUNDLE_VAR_lender_name
  MIP_LENDER_NMLS_ID      -> BUNDLE_VAR_lender_nmls_id
  MIP_TENANT_ID           -> BUNDLE_VAR_tenant_id
  MIP_COTALITY_SOURCE_CATALOG / _SCHEMA / _TABLE_PREFIX
                          -> BUNDLE_VAR_cotality_source_catalog / _schema /
                             _table_prefix (canonical when unset; '-' is no
                             table prefix; the same names render sql/_rendered;
                             the client profile holds the same fields)
  catalog, Lakebase names, MIP_IDENTITY_PREFIX
                          -> jobs/install_pins.json (gitignored, synced): the
                             parameters the App-triggerable jobs accept
                             (tools/databricks/install_pins.py, M2-PR5)
  (demo install only)     -> scheduled_refresh_failure_recipients, written to
                             .databricks/bundle/<target>/variable-overrides.json
                             (tools/databricks/scheduled_refresh_posture.py)

Usage:
  python tools/databricks/bundle_env.py validate -t ci
  python tools/databricks/bundle_env.py validate -t client
  python tools/databricks/bundle_env.py plan     -t client
  python tools/databricks/bundle_env.py summary  -t client -o json
  # The deploy's pre-lease plan gate (tools/databricks/bundle_plan_gate.py)
  # may plan before a first install provisions the governed Genie space:
  # with no space of that title, and only for plan, the placeholder binds.
  python tools/databricks/bundle_env.py plan -t client -o json --allow-unprovisioned-genie
  # Mutable use is command-of-record internal only and requires one or more
  # exact, non-App selectors:
  python tools/databricks/bundle_env.py deploy -t client --select jobs.example
  # Deployment-state use is restricted to the configured App binding:
  python tools/databricks/bundle_env.py deployment bind mip_app mip-app -t client

A demo install (the profile's MIP_INSTALL_CLASS) defaults to Summit demo
first-party feeds so the public demo keeps governed contactability / Lead
Queue data after a refresh. Any other install renders them off, and this
wrapper refuses the flag there unless ``MIP_ALLOW_DEMO_FIRST_PARTY_IN_PROD=1``
is also set (the profile already refuses it for a customer install).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backend.config import source_names  # noqa: E402
from backend.schemas.lender_identity import (  # noqa: E402
    effective_public_tenant_id,
    validate_public_lender_identity,
)
from tools import render_sql  # noqa: E402
from tools.clients import profile as client_views  # noqa: E402
from tools.clients.bootstrap_roster import data_owners_group  # noqa: E402
from tools.clients.environment import from_local as client_profile_from_local  # noqa: E402
from tools.clients.environment import resolve as resolve_client_profile  # noqa: E402
from tools.clients.errors import ProfileRefusal  # noqa: E402
from tools.clients.schema import ClientProfile  # noqa: E402
from tools.databricks import (  # noqa: E402
    install_pins,
    monitor_scheduler_gate,
    scheduled_refresh_posture,
)
from tools.databricks.workspace_auth import (  # noqa: E402
    strip_app_facing_workspace_auth,
)

ENV_LOCAL = REPO / ".env.local"
# The bundle root the CLI loads (it holds .databricks/bundle/<target>/
# variable-overrides.json). Like render_sql's output, it is this checkout
# unless DATABRICKS_BUNDLE_ROOT names another root.
BUNDLE_ROOT = REPO

PLACEHOLDER = "00000000PLACEHOLDER"
_APP_OR_INSTANCE_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_UC_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]{0,254}\Z")
_EXACT_RESOURCE_SELECTOR = re.compile(
    r"(?P<kind>[a-z_][a-z0-9_]*)\.(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*)\Z"
)
# The one deployable target, and the targets a validate may name.
_MUTABLE_BUNDLE_TARGETS = frozenset({"client"})
_VALIDATE_TARGETS = frozenset({"client", "ci"})
# The profile's bundle variables this wrapper exports for -t client: the
# target's own variables and the resource names. The lender and the refresh
# pause status keep their own channels (below and scripts/deploy.sh).
_RESOURCE_NAME_VARIABLES = (
    "app_name",
    "lakebase_instance_name",
    "lakebase_catalog_name",
    "lakebase_database_name",
)
_PROFILE_NAME_VARIABLES = (*_RESOURCE_NAME_VARIABLES, "uc_catalog", "runtime_secret_scope")
# The client target's own variables (databricks.yml): set from the profile for
# -t client, never from an ambient export.
_CLIENT_TARGET_VARIABLES = (
    "client_key",
    "deployer_principal",
    "jobs_principal",
    "dashboard_parent_path",
    "lakebase_readable_secondaries",
)
UNPROVISIONED_GENIE_FLAG = "--allow-unprovisioned-genie"


def _is_real(value: str | None) -> bool:
    """Treat empty / placeholder / angle-bracket-template values as not-set."""
    if not value:
        return False
    v = value.strip()
    if not v:
        return False
    if v.startswith("<") and v.endswith(">"):
        return False
    return v != PLACEHOLDER


def _target_from_args(args: list[str]) -> str:
    """The DAB target the wrapper args name; "" when none is named.

    No target is the default: databricks.yml declares none, so a call without
    one is refused below rather than left to the CLI.
    """
    target = ""
    idx = 0
    while idx < len(args):
        arg = args[idx]
        if arg in {"-t", "--target"} and idx + 1 < len(args):
            target = args[idx + 1]
            idx += 2
            continue
        if arg.startswith("--target="):
            target = arg.split("=", 1)[1]
        idx += 1
    return target


def _target_error(subcmd: str, target: str) -> str | None:
    """``-t client`` for every subcommand; ``-t ci`` for validate only."""

    allowed = _VALIDATE_TARGETS if subcmd == "validate" else _MUTABLE_BUNDLE_TARGETS
    if target in allowed:
        return None
    names = " or ".join(sorted(allowed))
    return f"bundle {subcmd} target must be exactly {names}"


def _validate_non_app_deploy_selectors(args: list[str]) -> str | None:
    """Allow only one governed target plus exact non-App selectors."""

    selectors: list[str] = []
    target: str | None = None
    index = 0
    while index < len(args):
        argument = args[index]
        if argument in {"-t", "--target"}:
            if target is not None or index + 1 >= len(args) or not args[index + 1].strip():
                return "bundle deploy requires exactly one nonempty governed target"
            target = args[index + 1]
            index += 2
            continue
        if argument.startswith("--target="):
            if target is not None or not argument.split("=", 1)[1].strip():
                return "bundle deploy requires exactly one nonempty governed target"
            target = argument.split("=", 1)[1]
            index += 1
            continue
        if argument == "--select":
            if index + 1 >= len(args):
                return "bundle deploy --select requires an exact resource selector"
            selectors.append(args[index + 1])
            index += 2
            continue
        if argument.startswith("--select="):
            selectors.append(argument.split("=", 1)[1])
            index += 1
            continue
        if argument == "--plan" or argument.startswith("--plan="):
            return "precomputed bundle deploy plans are forbidden; use scripts/deploy.sh"
        return (
            f"unsupported bundle deploy argument {argument!r}; only one governed "
            "target and exact --select values are allowed"
        )
    if target not in _MUTABLE_BUNDLE_TARGETS:
        return "bundle deploy target must be exactly client"
    if not selectors:
        return (
            "unrestricted bundle deploy is forbidden because it can activate "
            "apps.mip_app; use scripts/deploy.sh"
        )
    for selector in selectors:
        match = _EXACT_RESOURCE_SELECTOR.fullmatch(selector)
        if match is None or match.group("kind") == "apps":
            return (
                f"unsafe bundle deploy selector {selector!r}; only exact non-App "
                "resource selectors are allowed"
            )
    return None


def _validate_app_deployment_command(
    args: list[str],
    *,
    expected_app_name: str,
) -> str | None:
    """Allow only the exact App bind/unbind used by the signed deploy flow."""

    if not args or args[0] not in {"bind", "unbind"}:
        return "bundle deployment permits only the governed App bind or unbind operation"
    action = args[0]
    positional: list[str] = []
    boolean_flags: set[str] = set()
    target: str | None = None
    index = 1
    while index < len(args):
        argument = args[index]
        if argument in {"-t", "--target"}:
            if target is not None or index + 1 >= len(args) or not args[index + 1].strip():
                return "bundle deployment target must be supplied exactly"
            target = args[index + 1]
            index += 2
            continue
        if argument.startswith("--target="):
            if target is not None or not argument.split("=", 1)[1].strip():
                return "bundle deployment target must be supplied exactly"
            target = argument.split("=", 1)[1]
            index += 1
            continue
        allowed_flags = {"--force-lock", "--auto-approve"} if action == "bind" else {"--force-lock"}
        if argument in allowed_flags:
            if argument in boolean_flags:
                return f"duplicate bundle deployment flag {argument!r} is forbidden"
            boolean_flags.add(argument)
            index += 1
            continue
        if argument.startswith("-"):
            return f"unsafe bundle deployment option {argument!r} is forbidden"
        positional.append(argument)
        index += 1
    if target not in _MUTABLE_BUNDLE_TARGETS:
        return "bundle deployment target must be exactly client"
    expected = ["mip_app", expected_app_name] if action == "bind" else ["mip_app"]
    if positional != expected:
        return (
            f"bundle deployment {action} must target only the configured "
            f"App binding {expected!r}"
        )
    return None


def _truthy(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _demo_feeds_allowed(*, demo_install: bool, enabled: bool, env: dict[str, str]) -> bool:
    if not enabled:
        return True
    if demo_install:
        return True
    return _truthy(env.get("MIP_ALLOW_DEMO_FIRST_PARTY_IN_PROD"))


def _demo_first_party_flag(env: dict[str, str], *, demo_install: bool) -> str | None:
    """Resolve the SQL render flag, mirroring scripts/deploy.sh.

    A bare render remains fail-closed; this deployment wrapper is install-aware.
    The Summit demo install needs synthetic first-party feeds enabled so
    governed lead/contactability surfaces are populated after each gold refresh.
    """
    raw = env.get("MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS")
    if raw:
        return raw
    return "1" if demo_install else "0"


def _client_profile() -> ClientProfile:
    """The client profile from the shell and ``.env.local`` (a non-empty shell value wins)."""

    return resolve_client_profile(client_profile_from_local(os.environ, ENV_LOCAL))


def _profile_bundle_variables(profile: ClientProfile) -> dict[str, str]:
    """The ``client`` target's variables and resource names, from the profile."""

    variables = client_views.bundle_vars(profile)
    names = {name: variables[name] for name in _PROFILE_NAME_VARIABLES}
    return {**client_views.client_target_vars(profile), **names}


def _deployment_resource_names(env: dict[str, str]) -> dict[str, str]:
    """Resolve one coherent App/Lakebase resource namespace.

    These names are deliberately deployment controls. They never come from an
    ambient dotenv file when the caller already exported a reviewed value.
    Exact alias agreement prevents a bundle resource from being created under
    one name while post-deploy grants and live checks target another.
    """

    app_name = (env.get("MIP_APP_NAME") or "mip-app").strip()
    instance_name = (env.get("LAKEBASE_INSTANCE_NAME") or "").strip()
    proof_instance_name = (env.get("MIP_LAKEBASE_INSTANCE") or "").strip()
    if instance_name and proof_instance_name and instance_name != proof_instance_name:
        raise ValueError("LAKEBASE_INSTANCE_NAME and MIP_LAKEBASE_INSTANCE must match")
    instance_name = instance_name or proof_instance_name or "mip-app-state"

    database_name = (env.get("LAKEBASE_DATABASE") or "").strip()
    agent_database_name = (env.get("MIP_LAKEBASE_DATABASE_NAME") or "").strip()
    if database_name and agent_database_name and database_name != agent_database_name:
        raise ValueError("LAKEBASE_DATABASE and MIP_LAKEBASE_DATABASE_NAME must match")
    database_name = database_name or agent_database_name or "mip_app_state"
    catalog_name = (env.get("MIP_LAKEBASE_SYNC_CATALOG") or "mip_app_state").strip()

    for label, value in (("MIP_APP_NAME", app_name), ("LAKEBASE_INSTANCE_NAME", instance_name)):
        if _APP_OR_INSTANCE_NAME.fullmatch(value) is None:
            raise ValueError(f"{label} must be a lowercase DNS-style name")
    for label, value in (
        ("MIP_LAKEBASE_SYNC_CATALOG", catalog_name),
        ("LAKEBASE_DATABASE", database_name),
    ):
        if _UC_IDENTIFIER.fullmatch(value) is None:
            raise ValueError(f"{label} must be a lowercase unquoted identifier")

    return {
        "app_name": app_name,
        "lakebase_instance_name": instance_name,
        "lakebase_catalog_name": catalog_name,
        "lakebase_database_name": database_name,
    }


def _genie_workspace_client(env: dict[str, str]) -> Any:
    """Build a read-only deployer client from the exact child environment."""

    from databricks.sdk import WorkspaceClient

    bound_host = str(env.get("MIP_DEPLOYER_DATABRICKS_HOST") or "").strip()
    bound_token = str(env.get("MIP_DEPLOYER_DATABRICKS_TOKEN") or "").strip()
    bound_profile = str(env.get("MIP_DEPLOYER_DATABRICKS_PROFILE") or "").strip()
    if bound_host or bound_token:
        if not bound_host or not bound_token or bound_profile:
            raise ValueError("invalid deployer workspace auth binding")
        client = WorkspaceClient(host=bound_host, token=bound_token, auth_type="pat")
    elif bound_profile:
        client = WorkspaceClient(profile=bound_profile)
    else:
        profile = str(env.get("DATABRICKS_CONFIG_PROFILE") or "").strip()
        host = str(env.get("DATABRICKS_HOST") or "").strip()
        token = str(env.get("DATABRICKS_TOKEN") or "").strip()
        if profile:
            client = WorkspaceClient(profile=profile)
        elif host or token:
            if not host or not token:
                raise ValueError("DATABRICKS_HOST and DATABRICKS_TOKEN must be set together")
            client = WorkspaceClient(host=host, token=token, auth_type="pat")
        else:
            client = WorkspaceClient()

    expected_host = str(
        env.get("MIP_DEPLOYER_DATABRICKS_HOST") or env.get("DATABRICKS_HOST") or ""
    ).strip()
    actual_host = str(getattr(client.config, "host", "") or "").strip()
    if expected_host and actual_host.rstrip("/") != expected_host.rstrip("/"):
        raise ValueError(
            "Genie resolver workspace host does not match the reviewed deployment host"
        )
    return client


class GovernedGenieSpaceAbsent(ValueError):
    """No Genie space holds the governed title yet (a first install provisions it)."""


def _resolve_governed_genie_space_id(
    env: dict[str, str],
    *,
    space_name: str,
    client: Any | None = None,
) -> str:
    """Resolve exactly one live Genie space by governed title and round-trip it."""

    if not space_name.strip():
        raise ValueError("MIP_GENIE_SPACE_NAME must not be empty")
    workspace = client or _genie_workspace_client(env)
    matches: list[Any] = []
    page_token: str | None = None
    while True:
        response = workspace.genie.list_spaces(page_token=page_token)
        for space in getattr(response, "spaces", None) or []:
            title = str(getattr(space, "title", None) or getattr(space, "name", None) or "")
            if title.strip() == space_name:
                matches.append(space)
        page_token = getattr(response, "next_page_token", None)
        if not page_token:
            break
    if not matches:
        raise GovernedGenieSpaceAbsent(
            f"expected exactly one Genie space named {space_name!r}; found 0"
        )
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one Genie space named {space_name!r}; found {len(matches)}"
        )
    space_id = str(
        getattr(matches[0], "space_id", None) or getattr(matches[0], "id", None) or ""
    ).strip()
    if not _is_real(space_id):
        raise ValueError(f"Genie space {space_name!r} returned no valid space id")
    confirmed = workspace.genie.get_space(space_id)
    confirmed_title = str(
        getattr(confirmed, "title", None) or getattr(confirmed, "name", None) or ""
    ).strip()
    if confirmed_title != space_name:
        raise ValueError(f"Genie space {space_id!r} round-trip title does not match {space_name!r}")
    return space_id


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "usage: bundle_env.py <validate|plan|summary|deploy|deployment> "
            "[databricks bundle args...]",
            file=sys.stderr,
        )
        return 2

    subcmd, *rest = sys.argv[1:]
    if subcmd not in {"validate", "plan", "summary", "deploy", "deployment"}:
        print(
            "usage: bundle_env.py <validate|plan|summary|deploy|deployment> "
            "[databricks bundle args...]",
            file=sys.stderr,
        )
        return 2
    target = _target_from_args(rest)
    target_error = _target_error(subcmd, target)
    if target_error is not None:
        print(f"[bundle_env] {target_error}", file=sys.stderr)
        return 2
    allow_unprovisioned_genie = UNPROVISIONED_GENIE_FLAG in rest
    if allow_unprovisioned_genie:
        # Wrapper-only flag, never passed to the CLI: the pre-lease plan gate
        # plans before a first install has provisioned the governed space.
        if subcmd != "plan" or rest.count(UNPROVISIONED_GENIE_FLAG) != 1:
            print(
                f"[bundle_env] {UNPROVISIONED_GENIE_FLAG} is accepted once, by plan only",
                file=sys.stderr,
            )
            return 2
        rest = [arg for arg in rest if arg != UNPROVISIONED_GENIE_FLAG]
    if subcmd == "deploy":
        selector_error = _validate_non_app_deploy_selectors(rest)
        if selector_error is not None:
            print(f"[bundle_env] {selector_error}", file=sys.stderr)
            return 2
    # The monitor scheduler stays PAUSED until its scheduler role exists (M2-PR5).
    monitor_refusals = monitor_scheduler_gate.validator_refusals(
        Path(os.environ.get("DATABRICKS_BUNDLE_ROOT") or REPO)
    )
    for reason in monitor_refusals:
        print(f"[bundle_env] refusing jobs.{monitor_scheduler_gate.JOB_KEY}: {reason}", file=sys.stderr)
    if monitor_refusals:
        return 2
    # -t client is the profile's install: resolve it before anything else, so
    # a refused profile never reaches a render, a lookup or the CLI. The
    # refusal lines name fields and rules, never values.
    profile: ClientProfile | None = None
    if target == "client":
        try:
            profile = _client_profile()
        except ProfileRefusal as refusal:
            print("[bundle_env] client profile refused:", file=sys.stderr)
            for line in refusal.lines:
                print(f"  - {line}", file=sys.stderr)
            return 2
    profile_variables = _profile_bundle_variables(profile) if profile is not None else {}

    # Start from the current process env so PATH, HOME, DATABRICKS_CONFIG_*
    # all propagate, then overlay dotenv values.
    env = dict(os.environ)
    strip_app_facing_workspace_auth(env)
    deployer_auth_bound = any(
        env.get(name, "").strip()
        for name in (
            "MIP_DEPLOYER_DATABRICKS_HOST",
            "MIP_DEPLOYER_DATABRICKS_TOKEN",
            "MIP_DEPLOYER_DATABRICKS_PROFILE",
        )
    )
    immutable_workspace_auth = {
        "DATABRICKS_HOST",
        "DATABRICKS_TOKEN",
        "DATABRICKS_AUTH_TYPE",
        "DATABRICKS_CONFIG_PROFILE",
        "DATABRICKS_CLIENT_ID",
        "DATABRICKS_CLIENT_SECRET",
    }
    if ENV_LOCAL.exists():
        for k, v in dotenv_values(ENV_LOCAL).items():
            if v is None:
                continue
            # deploy.sh may pre-provision Genie and export GENIE_SPACE_ID
            # before calling this helper. Preserve that real value if
            # .env.local still carries the first-run blank/template value.
            # Deployment controls follow shell > dotenv > default. Preserve a
            # reviewed shell export, but make the documented one-.env.local
            # path work when no shell value exists. Alias agreement and target
            # safety are validated below before the CLI can mutate anything.
            if k in {
                "MIP_DEFAULT_CATALOG",
                "MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS",
                "MIP_APP_NAME",
                "MIP_LENDER_NAME",
                "MIP_LENDER_NMLS_ID",
                "MIP_TENANT_ID",
                "MIP_AI_GATEWAY_VERIFIER_CLIENT_ID",
                "LAKEBASE_INSTANCE_NAME",
                "MIP_LAKEBASE_INSTANCE",
                "LAKEBASE_DATABASE",
                "MIP_LAKEBASE_DATABASE_NAME",
                "MIP_LAKEBASE_SYNC_CATALOG",
                "MIP_GENIE_SPACE_NAME",
                "MIP_RUNTIME_SECRET_SCOPE",
                *source_names.ENV_NAMES,
            }:
                if str(env.get(k) or "").strip():
                    continue
                env[k] = v
                continue
            if k in {"DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET"}:
                continue
            if deployer_auth_bound and k in immutable_workspace_auth:
                continue
            if k in {"DATABRICKS_WAREHOUSE_ID", "GENIE_SPACE_ID"} and _is_real(env.get(k)):
                continue
            env[k] = v

    if subcmd == "deployment":
        deployment_error = _validate_app_deployment_command(
            rest,
            expected_app_name=(
                profile_variables["app_name"]
                if profile is not None
                else str(env.get("MIP_APP_NAME") or "mip-app").strip()
            ),
        )
        if deployment_error is not None:
            print(f"[bundle_env] {deployment_error}", file=sys.stderr)
            return 2
    warehouse = env.get("DATABRICKS_WAREHOUSE_ID")
    genie = env.get("GENIE_SPACE_ID")
    verifier_client_id = (
        profile.identity_ids.verifier
        if profile is not None
        else env.get("MIP_AI_GATEWAY_VERIFIER_CLIENT_ID")
    )
    if not _is_real(genie):
        space_id_file = REPO / "genie" / "space_id.txt"
        if space_id_file.exists():
            genie = space_id_file.read_text(encoding="utf-8").strip()
            if _is_real(genie):
                env["GENIE_SPACE_ID"] = genie

    # Plan/summary/deploy can influence a real deployment decision, and deploy
    # mutates the App binding. Never trust a syntactically valid env/dotenv id:
    # resolve the exact governed title in the authenticated workspace and
    # overwrite the child value. `scripts/deploy.sh` provisions that named
    # space first, so a missing or duplicate title here is always a hard stop.
    genie_unprovisioned = False
    if subcmd in {"plan", "summary", "deploy", "deployment"}:
        space_name = (
            profile.genie_space_name
            if profile is not None
            else str(env.get("MIP_GENIE_SPACE_NAME") or "Mortgage Lead Intelligence").strip()
        )
        try:
            governed_genie = _resolve_governed_genie_space_id(env, space_name=space_name)
        except GovernedGenieSpaceAbsent as exc:
            if not allow_unprovisioned_genie:
                print(
                    f"[bundle_env] governed Genie binding verification failed: {exc}",
                    file=sys.stderr,
                )
                return 2
            # Read-only plan only: no space holds the governed title, so no id
            # in env or .env.local can be the governed one. Plan with the
            # placeholder; scripts/deploy.sh plans again, without this flag,
            # after it provisions the space and before it applies anything.
            print(
                f"[bundle_env] no Genie space is named {space_name!r} yet; this read-only "
                "plan binds the placeholder (the deploy re-plans after provisioning)",
                file=sys.stderr,
            )
            genie_unprovisioned = True
            genie = PLACEHOLDER
            env["GENIE_SPACE_ID"] = PLACEHOLDER
        except Exception as exc:  # noqa: BLE001 - CLI boundary must fail closed
            print(
                f"[bundle_env] governed Genie binding verification failed: {exc}", file=sys.stderr
            )
            return 2
        else:
            if _is_real(genie) and str(genie).strip() != governed_genie:
                print(
                    "[bundle_env] replacing stale GENIE_SPACE_ID with the id resolved "
                    f"from MIP_GENIE_SPACE_NAME={space_name!r}",
                    file=sys.stderr,
                )
            genie = governed_genie
            env["GENIE_SPACE_ID"] = governed_genie

    if subcmd in {"plan", "summary", "deploy", "deployment"}:
        missing = []
        if not _is_real(warehouse):
            missing.append("DATABRICKS_WAREHOUSE_ID")
        if not _is_real(genie) and not genie_unprovisioned:
            missing.append("GENIE_SPACE_ID")
        if not _is_real(verifier_client_id):
            missing.append("MIP_AI_GATEWAY_VERIFIER_CLIENT_ID")
        if missing:
            print(
                f"[bundle_env] refusing bundle {subcmd} with placeholder bundle variables: "
                + ", ".join(missing),
                file=sys.stderr,
            )
            print(
                "[bundle_env] run tools/databricks/provision_genie_space.py or "
                "./scripts/deploy.sh so a real Genie space id is available before "
                "databricks_app.mip_app is updated.",
                file=sys.stderr,
            )
            return 2

    env["BUNDLE_VAR_sql_warehouse_id"] = str(warehouse) if _is_real(warehouse) else PLACEHOLDER
    env["BUNDLE_VAR_genie_space_id"] = str(genie) if _is_real(genie) else PLACEHOLDER
    env["BUNDLE_VAR_ai_gateway_verifier_client_id"] = (
        str(verifier_client_id) if _is_real(verifier_client_id) else PLACEHOLDER
    )

    try:
        lender_name, lender_nmls_id = validate_public_lender_identity(
            env.get("MIP_LENDER_NAME") or "Summit Mortgage",
            env.get("MIP_LENDER_NMLS_ID"),
        )
        tenant_id = effective_public_tenant_id(
            env.get("MIP_TENANT_ID"),
            lender_name=lender_name,
        )
    except ValueError as exc:
        print(f"[bundle_env] invalid lender disclosure identity: {exc}", file=sys.stderr)
        return 2
    env["MIP_LENDER_NAME"] = lender_name
    env["MIP_LENDER_NMLS_ID"] = lender_nmls_id
    env["MIP_TENANT_ID"] = tenant_id
    env["BUNDLE_VAR_lender_name"] = lender_name
    env["BUNDLE_VAR_lender_nmls_id"] = lender_nmls_id
    env["BUNDLE_VAR_tenant_id"] = tenant_id

    # -t client: the names are the profile's, which validated their shapes and
    # alias agreement; -t ci keeps the environment's names and defaults.
    try:
        resource_names = (
            {name: profile_variables[name] for name in _RESOURCE_NAME_VARIABLES}
            if profile is not None
            else _deployment_resource_names(env)
        )
    except ValueError as exc:
        print(f"[bundle_env] {exc}", file=sys.stderr)
        return 2
    for variable, value in resource_names.items():
        env[f"BUNDLE_VAR_{variable}"] = value
    env["MIP_APP_NAME"] = resource_names["app_name"]
    env["LAKEBASE_INSTANCE_NAME"] = resource_names["lakebase_instance_name"]
    env["MIP_LAKEBASE_INSTANCE"] = resource_names["lakebase_instance_name"]
    env["LAKEBASE_DATABASE"] = resource_names["lakebase_database_name"]
    env["MIP_LAKEBASE_DATABASE_NAME"] = resource_names["lakebase_database_name"]
    env["MIP_LAKEBASE_SYNC_CATALOG"] = resource_names["lakebase_catalog_name"]
    runtime_secret_scope = (
        profile_variables["runtime_secret_scope"]
        if profile is not None
        else str(env.get("MIP_RUNTIME_SECRET_SCOPE") or "mip-runtime").strip()
    )
    if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", runtime_secret_scope) is None:
        print("[bundle_env] MIP_RUNTIME_SECRET_SCOPE is invalid", file=sys.stderr)
        return 2
    env["MIP_RUNTIME_SECRET_SCOPE"] = runtime_secret_scope
    env["BUNDLE_VAR_runtime_secret_scope"] = runtime_secret_scope
    # The client target's own variables (its root, run_as and overrides) come
    # from the profile alone: an ambient export of one never survives, and the
    # ci target keeps the placeholders databricks.yml gives it.
    for variable in _CLIENT_TARGET_VARIABLES:
        env.pop(f"BUNDLE_VAR_{variable}", None)
    if profile is not None:
        for variable, value in client_views.client_target_vars(profile).items():
            env[f"BUNDLE_VAR_{variable}"] = value
    install_class = profile.install_class if profile is not None else None
    demo_install = install_class == "demo"
    # mip_scheduled_refresh runs and mails only on a demo install: refuse an
    # UNPAUSED export anywhere else, and write the failure recipients (a
    # complex variable, which no BUNDLE_VAR_ export can carry) into the
    # bundle's variable-overrides.json before every CLI call.
    try:
        scheduled_refresh_status = scheduled_refresh_posture.apply(
            env,
            target=target,
            install_class=install_class,
            bundle_root=Path(env.get("DATABRICKS_BUNDLE_ROOT") or BUNDLE_ROOT),
        )
    except (OSError, ValueError) as exc:
        print(f"[bundle_env] {exc}", file=sys.stderr)
        return 2

    catalog = (
        profile_variables["uc_catalog"]
        if profile is not None
        else str(env.get("MIP_DEFAULT_CATALOG") or "mip").strip()
    )
    if _UC_IDENTIFIER.fullmatch(catalog) is None:
        print(
            "[bundle_env] MIP_DEFAULT_CATALOG must be a lowercase unquoted identifier",
            file=sys.stderr,
        )
        return 2
    env["BUNDLE_VAR_uc_catalog"] = catalog
    # The Cotality source share: one resolution renders the SQL and sets the
    # variables the feature pipeline's configuration reads.
    try:
        cotality = source_names.from_env(env)
    except ValueError as exc:
        print(f"[bundle_env] {exc}", file=sys.stderr)
        return 2
    env["BUNDLE_VAR_cotality_source_catalog"] = cotality.catalog
    env["BUNDLE_VAR_cotality_source_schema"] = cotality.schema
    # The setting's spelling: "-" for a share with no table prefix, since an
    # empty bundle variable would not read as one.
    env["BUNDLE_VAR_cotality_source_table_prefix"] = cotality.table_prefix_setting
    try:
        # The ci target validates placeholders only and never deploys, so it
        # renders the fail-closed default whatever an operator's .env.local
        # says; only the client target renders an install's feed switch.
        demo_first_party_enabled = target != "ci" and render_sql._parse_bool(
            _demo_first_party_flag(env, demo_install=demo_install),
            default=False,
        )
    except ValueError as exc:
        print(f"[bundle_env] {exc}", file=sys.stderr)
        return 2

    if not _demo_feeds_allowed(
        demo_install=demo_install,
        enabled=demo_first_party_enabled,
        env=env,
    ):
        print(
            "[bundle_env] refusing to enable Summit demo first-party feeds "
            f"for a {install_class or 'non-demo'} install (target {target}). Set "
            "MIP_ALLOW_DEMO_FIRST_PARTY_IN_PROD=1 only for an approved demo "
            "workspace; never for customer production.",
            file=sys.stderr,
        )
        return 2

    try:
        processed, written, subs = render_sql.render(
            catalog=catalog,
            demo_first_party_enabled=demo_first_party_enabled,
            cotality=cotality,
        )
    except render_sql.CanonicalSourceLeft as exc:
        print(f"[bundle_env] {exc}", file=sys.stderr)
        return 2
    # The install pins the App-triggerable jobs check their parameters against
    # (M2-PR5): the same catalog and Lakebase names as the bundle variables.
    identity_prefix = (
        profile.identity_prefix
        if profile is not None
        else str(env.get("MIP_IDENTITY_PREFIX") or "mip").strip()
    )
    try:
        install_pins.render(
            Path(env.get("DATABRICKS_BUNDLE_ROOT") or BUNDLE_ROOT),
            install_pins.document(
                catalog=catalog,
                lakebase_instance=resource_names["lakebase_instance_name"],
                lakebase_database=resource_names["lakebase_database_name"],
                data_owner_group=data_owners_group(identity_prefix),
            ),
        )
    except OSError as exc:
        print(f"[bundle_env] could not render the install pins: {exc}", file=sys.stderr)
        return 2

    # Operator feedback (never print the full value; just confirm resolution).
    def status(name: str, value: str | None) -> str:
        if _is_real(value):
            return f"{name}=set (…{str(value)[-4:]})"
        return f"{name}=not-set (will use placeholder)"

    if profile is not None:
        print(
            f"[bundle_env] client target client_key={profile.client} "
            f"install_class={profile.install_class} (root, run_as and names from the profile)",
            file=sys.stderr,
        )
    print(f"[bundle_env] {status('DATABRICKS_WAREHOUSE_ID', warehouse)}", file=sys.stderr)
    print(f"[bundle_env] {status('GENIE_SPACE_ID', genie)}", file=sys.stderr)
    print(
        "[bundle_env] deployment resources "
        f"app={resource_names['app_name']} "
        f"lakebase_instance={resource_names['lakebase_instance_name']} "
        f"lakebase_catalog={resource_names['lakebase_catalog_name']} "
        f"lakebase_database={resource_names['lakebase_database_name']}",
        file=sys.stderr,
    )
    print(f"[bundle_env] {scheduled_refresh_status}", file=sys.stderr)
    print(f"[bundle_env] install pins rendered to {install_pins.PINS_RELPATH}", file=sys.stderr)
    print(
        "[bundle_env] "
        f"render_sql catalog={catalog} processed={processed} written={written} "
        f"substitutions={subs} cotality_source={cotality.describe()} "
        f"demo_first_party_feeds={'enabled' if demo_first_party_enabled else 'disabled'}",
        file=sys.stderr,
    )

    cli = ["databricks", "bundle", subcmd, *rest]
    result = subprocess.run(cli, env=env, check=False)
    if demo_first_party_enabled:
        render_sql.render(catalog=catalog, demo_first_party_enabled=False, cotality=cotality)
        print(
            "[bundle_env] restored sql/_rendered with "
            "demo_first_party_feeds=disabled after CLI exit",
            file=sys.stderr,
        )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
