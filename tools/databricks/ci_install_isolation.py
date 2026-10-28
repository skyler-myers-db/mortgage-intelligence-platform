#!/usr/bin/env python3
"""Fail-closed isolation of the CI staging install (D-platform-process-b step 7).

The CI deploy (.github/workflows/deploy-dev.yml) must never upload into,
share bundle state with, or run as the identities of the live ``mip-app``.
Root, name and variable isolation alone cannot separate identities in one
workspace (the seven reserved M2M display names are workspace-wide), so the
CI staging install deploys to its own forked-anchor bundle target,
``pr105-host`` (``CI_TARGET``). Its own ``workspace.host`` separates the
workspace-scoped objects (App, warehouse, endpoints, Genie spaces, secret
scopes, Lakebase instance, the workspace ``users`` group) and binds the
deploy's auth to that workspace, where the reserved identities are resolved
by name. It does NOT separate Unity Catalog: that workspace shares the live
install's metastore, so the catalog-scoped names below are the primary UC
isolation and must never be relaxed. Nor is identity separation structural:
service principals are account-level, so keeping the CI workspace's reserved
principals unassigned in the live workspace is an owner invariant
(docs/deployment.md, "CI staging install"). scripts/deploy.sh runs this pure
check in its preflight (also in DRY_RUN), before the confirmation prompt and
before any mutation, and tools/databricks/bundle_env.py runs it before its
mutating subcommands:

- ``MIP_BUNDLE_ROOT_LEAF`` (default ``dev``) must match ``^dev(-[a-z0-9]{1,20})?$``;
- deploy-dev.yml (GitHub Actions running that workflow) deploys only to
  ``CI_TARGET`` and only with a non-default leaf; it sets ``dev-ci`` as a literal;
- ``CI_TARGET`` is deploy-dev.yml-only, and a non-default leaf is CI-only and
  ``CI_TARGET``-only (the dev target keeps the live root);
- ``CI_TARGET``'s workspace hostname must be set and differ from the dev target's
  (scheme, port, path, query and a trailing dot never make a second workspace);
- with a non-default leaf every install-scoped name must be set and differ from
  its live default, neither UC catalog name (``UC_CATALOG_NAMES``) may name
  either live catalog in any UC spelling, GENIE_SPACE_ID must differ from the
  dev target's pinned live space, an explicit
  MIP_AI_GATEWAY_AGENT_MODEL_FAMILY must sit in MIP_DEFAULT_CATALOG (unset,
  the deploy derives it from that catalog), and the metastore-wide
  foreign-catalog binding remediation (``REMEDIATION_FLAG``) must be off.

The CI rule is keyed on the deploy-dev workflow ref, a present leaf or
``CI_TARGET``, never on GITHUB_ACTIONS alone, so CI's own pytest (which
inherits GITHUB_ACTIONS=true) and its env-less deploy.sh dry runs are
unchanged.

Exit 0 prints ``bundle_root_leaf=<leaf>`` and ``install_suffix=<suffix>``
('' for dev, else '_' + the leaf's suffix with '-' as '_'); exit 2 prints a
fixed reason naming only variables, never a value. Local operator runs (no
leaf) print ``dev`` and ''.

``uc-catalogs`` runs the table's Unity Catalog row on its own: both
``UC_CATALOG_NAMES`` set, and neither naming a live catalog in any UC
spelling. nightly.yml runs it before any other job, because its catalog
names fall back to the live ones when the repository variables are unset and
the CI staging workspace shares the live metastore.

``repair-credential`` gates deploy-dev.yml's normal-credential repair, which
rotates an OAuth secret before, and outside, scripts/deploy.sh: deploy-dev.yml
only, ``DATABRICKS_HOST`` naming ``CI_TARGET``'s workspace host (never the dev
target's), and ``MIP_APP_NAME`` set and not the live App.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEAF = "dev"
DEFAULT_TARGET = "dev"
# The forked-anchor CI staging target in databricks.yml (deploy-dev.yml only).
CI_TARGET = "pr105-host"
_LEAF = re.compile(r"dev(?:-[a-z0-9]{1,20})?\Z")
DEPLOY_DEV_WORKFLOW = "/.github/workflows/deploy-dev.yml@"
# The live install's names: databricks.yml variable defaults, deploy-dev.yml's
# fallbacks and the deploy scripts' AI Gateway defaults
# (tests/unit/test_ci_install_isolation.py keeps them equal).
LIVE_DEFAULTS = {
    "MIP_APP_NAME": "mip-app",
    "MIP_DEFAULT_CATALOG": "mip",
    "MIP_LAKEBASE_INSTANCE": "mip-app-state",
    "MIP_LAKEBASE_SYNC_CATALOG": "mip_app_state",
    "MIP_RUNTIME_SECRET_SCOPE": "mip-runtime",
    "MIP_APP_ROLLBACK_SECRET_SCOPE": "mip-app-rollback",
    "MIP_GENIE_SPACE_NAME": "Mortgage Lead Intelligence",
    "MIP_AI_GATEWAY_TABLE_PREFIX": "mip_agent_gateway_growth_agent",
    "MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE": "mip-agent-runtime-gateway-proxy",
}
# The install-scoped names that are Unity Catalog catalogs. The CI staging
# workspace shares the live install's UC metastore, so these are its primary
# UC isolation: neither may name either live catalog, in any spelling UC
# resolves to the same catalog (case, backticks).
UC_CATALOG_NAMES = ("MIP_DEFAULT_CATALOG", "MIP_LAKEBASE_SYNC_CATALOG")
# deploy-dev.yml's remediate_foreign_catalog_bindings input. The remediation
# sets catalogs ISOLATED and rewrites their workspace bindings for every
# workspace on the metastore, the live one included, so the CI install never
# runs it (only "" or "0" passes).
REMEDIATION_FLAG = "MIP_REMEDIATE_FOREIGN_CATALOG_BINDINGS"


class Refused(Exception):
    """A fixed reason; it names variables, never their values."""


def pinned_live_genie_space(bundle: Path = REPO_ROOT / "databricks.yml") -> str:
    """targets.dev.variables.genie_space_id, read without a YAML dependency."""

    text = bundle.read_text(encoding="utf-8")
    dev = re.search(r"(?ms)^  dev:\n(?P<body>.*?)(?=^  [a-z_]+:\n)", text.split("\ntargets:\n", 1)[-1])
    pinned = re.search(r"^\s+genie_space_id:\s*([0-9A-Za-z]+)\s*$", dev.group("body"), re.M) if dev else None
    return pinned.group(1) if pinned else ""


def target_hosts(bundle: Path = REPO_ROOT / "databricks.yml") -> dict[str, str]:
    """Each bundle target's resolved ``workspace.host`` (YAML anchors followed), normalized."""

    import yaml  # deploy.sh's workspace-auth bind already requires PyYAML in this interpreter

    data = yaml.safe_load(bundle.read_text(encoding="utf-8")) or {}
    top = str((data.get("workspace") or {}).get("host") or "")
    hosts: dict[str, str] = {}
    for name, target in (data.get("targets") or {}).items():
        host = str(((target or {}).get("workspace") or {}).get("host") or top)
        hosts[str(name)] = host.strip().rstrip("/").casefold()
    return hosts


def _host_key(url: str) -> str:
    """The workspace hostname a host URL names: scheme, port, path and query are not a second workspace.

    Nor is the absolute (trailing-dot) spelling of a hostname: DNS and TLS
    resolve ``host.`` to ``host``, so the dot is dropped before comparing.
    """

    value = url.strip()
    if not value:
        return ""
    try:
        hostname = urlsplit(value if "://" in value else f"https://{value}").hostname or ""
    except ValueError:  # an unparseable host names no workspace; the caller refuses it
        return ""
    return hostname.rstrip(".").casefold()


def _uc_name(part: str) -> str:
    """A UC catalog name as Unity Catalog compares it: unquoted, case-insensitive."""

    return part.strip().strip("`").casefold()


def ci_workspace_host(hosts: Mapping[str, str] | None = None) -> str:
    """``CI_TARGET``'s workspace hostname, refused unless it is set and differs from the dev target's."""

    resolved = target_hosts() if hosts is None else hosts
    ci_host = _host_key(resolved.get(CI_TARGET, ""))
    dev_host = _host_key(resolved.get(DEFAULT_TARGET, ""))
    if not ci_host or not dev_host or ci_host == dev_host:
        raise Refused(f"the {CI_TARGET} target must anchor its own workspace host, never the dev target's")
    return ci_host


def check_credential_repair(env: Mapping[str, str], hosts: Mapping[str, str] | None = None) -> None:
    """deploy-dev.yml's normal-credential repair touches only the CI staging install's workspace and App."""

    in_actions = env.get("GITHUB_ACTIONS") == "true"
    if not (in_actions and DEPLOY_DEV_WORKFLOW in (env.get("GITHUB_WORKFLOW_REF") or "")):
        raise Refused("the normal-credential repair runs only in deploy-dev.yml")
    if _host_key(env.get("DATABRICKS_HOST") or "") != ci_workspace_host(hosts):
        raise Refused(f"DATABRICKS_HOST must be the {CI_TARGET} target's workspace host, never the live workspace")
    app = (env.get("MIP_APP_NAME") or "").strip()
    if not app or app == LIVE_DEFAULTS["MIP_APP_NAME"]:
        raise Refused("MIP_APP_NAME must name the CI staging install, not the live default")


def check_uc_catalogs(env: Mapping[str, str]) -> None:
    """The rule table's primary UC isolation: each catalog name set, and neither a live catalog."""

    live_catalogs = {_uc_name(LIVE_DEFAULTS[name]) for name in UC_CATALOG_NAMES}
    for name in UC_CATALOG_NAMES:
        value = _uc_name(env.get(name) or "")
        if not value:
            raise Refused(f"{name} must name the CI staging install's catalog")
        if value in live_catalogs:
            raise Refused(f"{name} names a live install catalog in the shared metastore")


def install_suffix(leaf: str) -> str:
    return "" if leaf == DEFAULT_LEAF else "_" + leaf.removeprefix(f"{DEFAULT_LEAF}-").replace("-", "_")


def resolve(
    env: Mapping[str, str],
    live_genie_space: str | None = None,
    hosts: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    """(bundle_root_leaf, install_suffix) or Refused."""

    raw = (env.get("MIP_BUNDLE_ROOT_LEAF") or "").strip()
    if raw and not _LEAF.match(raw):
        raise Refused("MIP_BUNDLE_ROOT_LEAF must match ^dev(-[a-z0-9]{1,20})?$")
    leaf = raw or DEFAULT_LEAF
    target = (env.get("MIP_DEPLOY_TARGET") or "").strip() or DEFAULT_TARGET
    in_actions = env.get("GITHUB_ACTIONS") == "true"
    deploy_dev = in_actions and DEPLOY_DEV_WORKFLOW in (env.get("GITHUB_WORKFLOW_REF") or "")
    if deploy_dev and target != CI_TARGET:
        raise Refused(f"deploy-dev.yml deploys only to the forked-anchor CI target: use -t {CI_TARGET}")
    if deploy_dev and leaf == DEFAULT_LEAF:
        raise Refused("deploy-dev.yml must deploy into an isolated bundle root: set MIP_BUNDLE_ROOT_LEAF")
    if target == CI_TARGET and not deploy_dev:
        raise Refused(f"the forked-anchor CI target {CI_TARGET} is deploy-dev.yml only; an operator deploys -t dev")
    if leaf == DEFAULT_LEAF:
        return leaf, ""
    if not in_actions:
        raise Refused("a non-default MIP_BUNDLE_ROOT_LEAF is CI-only (deploy-dev.yml); unset it for an operator deploy")
    if target != CI_TARGET:
        raise Refused(f"the isolated CI staging install is {CI_TARGET}-target only: unset MIP_BUNDLE_ROOT_LEAF")
    if (env.get(REMEDIATION_FLAG) or "").strip() not in {"", "0"}:
        raise Refused(
            f"{REMEDIATION_FLAG} rewrites catalog bindings metastore-wide and the CI staging install shares the "
            "live metastore: never remediate from deploy-dev.yml"
        )
    ci_workspace_host(hosts)
    for name, live in LIVE_DEFAULTS.items():
        value = (env.get(name) or "").strip()
        if not value or value == live:
            raise Refused(f"{name} must name the CI staging install, not the live default")
    check_uc_catalogs(env)
    family = (env.get("MIP_AI_GATEWAY_AGENT_MODEL_FAMILY") or "").strip()
    if family and _uc_name(family.split(".", 1)[0]) != _uc_name(env.get("MIP_DEFAULT_CATALOG") or ""):
        raise Refused("MIP_AI_GATEWAY_AGENT_MODEL_FAMILY must sit in MIP_DEFAULT_CATALOG; unset derives it from there")
    genie = (env.get("GENIE_SPACE_ID") or "").strip()
    pinned = pinned_live_genie_space() if live_genie_space is None else live_genie_space
    if not genie or genie == pinned:
        raise Refused("GENIE_SPACE_ID must name the CI staging Genie space, not the dev target's pinned live space")
    return leaf, install_suffix(leaf)


# Single-row checks for callers outside scripts/deploy.sh's preflight.
CHECKS: dict[str, Callable[[Mapping[str, str]], None]] = {
    "uc-catalogs": check_uc_catalogs,
    "repair-credential": check_credential_repair,
}


def main(env: Mapping[str, str] | None = None, argv: Sequence[str] = ()) -> int:
    source = os.environ if env is None else env
    if argv:
        check = CHECKS.get(argv[0]) if len(argv) == 1 else None
        if check is None:
            print(f"[ci-install-isolation] usage: ci_install_isolation.py [{'|'.join(CHECKS)}]", file=sys.stderr)
            return 2
        try:
            check(source)
        except Refused as refusal:
            print(f"[ci-install-isolation] refused: {refusal}", file=sys.stderr)
            return 2
        # stderr, like a refusal: a workflow step's stdout stays its own.
        print(f"[ci-install-isolation] {argv[0]}: ok", file=sys.stderr)
        return 0
    try:
        leaf, suffix = resolve(source)
    except Refused as refusal:
        print(f"[ci-install-isolation] refused: {refusal}", file=sys.stderr)
        return 2
    print(f"bundle_root_leaf={leaf}")
    print(f"install_suffix={suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(argv=sys.argv[1:]))
