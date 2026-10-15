#!/usr/bin/env python3
"""Fail-closed isolation of the CI staging install (D-platform-process-b step 7).

The CI deploy (.github/workflows/deploy-dev.yml) lands in the bundle's one
anchor workspace beside the live ``mip-app``. Without this gate it would
upload into, and share bundle state with, the live install at
``~/.bundle/<bundle>/dev`` and fall back to the live Lakebase instance,
sync catalog and secret scopes. scripts/deploy.sh runs this pure check in
its preflight (also in DRY_RUN), before the confirmation prompt and before
any mutation:

- ``MIP_BUNDLE_ROOT_LEAF`` (default ``dev``) must match ``^dev(-[a-z0-9]{1,20})?$``;
- deploy-dev.yml (GitHub Actions running that workflow) must use a non-default
  leaf; it sets ``dev-ci`` as a literal;
- a non-default leaf is CI-only, and dev-target only;
- with a non-default leaf every install-scoped name must be set and differ from
  its live default, GENIE_SPACE_ID must differ from the dev target's pinned
  live space, and an explicit MIP_AI_GATEWAY_AGENT_MODEL_FAMILY must sit in
  MIP_DEFAULT_CATALOG (unset, the deploy derives it from that catalog).

The CI rule is keyed on the deploy-dev workflow ref or a present leaf, never
on GITHUB_ACTIONS alone, so CI's own pytest (which inherits
GITHUB_ACTIONS=true) and its env-less deploy.sh dry runs are unchanged.

Exit 0 prints ``bundle_root_leaf=<leaf>`` and ``install_suffix=<suffix>``
('' for dev, else '_' + the leaf's suffix with '-' as '_'); exit 2 prints a
fixed reason naming only variables, never a value. Local operator runs (no
leaf) print ``dev`` and ''.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEAF = "dev"
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


class Refused(Exception):
    """A fixed reason; it names variables, never their values."""


def pinned_live_genie_space(bundle: Path = REPO_ROOT / "databricks.yml") -> str:
    """targets.dev.variables.genie_space_id, read without a YAML dependency."""

    text = bundle.read_text(encoding="utf-8")
    dev = re.search(r"(?ms)^  dev:\n(?P<body>.*?)(?=^  [a-z_]+:\n)", text.split("\ntargets:\n", 1)[-1])
    pinned = re.search(r"^\s+genie_space_id:\s*([0-9A-Za-z]+)\s*$", dev.group("body"), re.M) if dev else None
    return pinned.group(1) if pinned else ""


def _uc_name(part: str) -> str:
    """A UC catalog name as Unity Catalog compares it: unquoted, case-insensitive."""

    return part.strip().strip("`").casefold()


def install_suffix(leaf: str) -> str:
    return "" if leaf == DEFAULT_LEAF else "_" + leaf.removeprefix(f"{DEFAULT_LEAF}-").replace("-", "_")


def resolve(env: Mapping[str, str], live_genie_space: str | None = None) -> tuple[str, str]:
    """(bundle_root_leaf, install_suffix) or Refused."""

    raw = (env.get("MIP_BUNDLE_ROOT_LEAF") or "").strip()
    if raw and not _LEAF.match(raw):
        raise Refused("MIP_BUNDLE_ROOT_LEAF must match ^dev(-[a-z0-9]{1,20})?$")
    leaf = raw or DEFAULT_LEAF
    in_actions = env.get("GITHUB_ACTIONS") == "true"
    deploy_dev = in_actions and DEPLOY_DEV_WORKFLOW in (env.get("GITHUB_WORKFLOW_REF") or "")
    if deploy_dev and leaf == DEFAULT_LEAF:
        raise Refused("deploy-dev.yml must deploy into an isolated bundle root: set MIP_BUNDLE_ROOT_LEAF")
    if leaf == DEFAULT_LEAF:
        return leaf, ""
    if not in_actions:
        raise Refused("a non-default MIP_BUNDLE_ROOT_LEAF is CI-only (deploy-dev.yml); unset it for an operator deploy")
    if (env.get("MIP_DEPLOY_TARGET") or DEFAULT_LEAF) != DEFAULT_LEAF:
        raise Refused("the isolated CI staging install is dev-target only: unset MIP_BUNDLE_ROOT_LEAF")
    for name, live in LIVE_DEFAULTS.items():
        value = (env.get(name) or "").strip()
        if not value or value == live:
            raise Refused(f"{name} must name the CI staging install, not the live default")
    family = (env.get("MIP_AI_GATEWAY_AGENT_MODEL_FAMILY") or "").strip()
    if family and _uc_name(family.split(".", 1)[0]) != _uc_name(env.get("MIP_DEFAULT_CATALOG") or ""):
        raise Refused("MIP_AI_GATEWAY_AGENT_MODEL_FAMILY must sit in MIP_DEFAULT_CATALOG; unset derives it from there")
    genie = (env.get("GENIE_SPACE_ID") or "").strip()
    pinned = pinned_live_genie_space() if live_genie_space is None else live_genie_space
    if not genie or genie == pinned:
        raise Refused("GENIE_SPACE_ID must name the CI staging Genie space, not the dev target's pinned live space")
    return leaf, install_suffix(leaf)


def main(env: Mapping[str, str] | None = None) -> int:
    try:
        leaf, suffix = resolve(os.environ if env is None else env)
    except Refused as refusal:
        print(f"[ci-install-isolation] refused: {refusal}", file=sys.stderr)
        return 2
    print(f"bundle_root_leaf={leaf}")
    print(f"install_suffix={suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
