"""Run a Databricks CLI command with `.env.local` sourced via python-dotenv.

The Makefile target that sources `.env.local` directly in bash broke on
unquoted spaces (e.g. `MIP_LENDER_NAME=Summit Mortgage`) and angle-bracket
placeholder values (`GENIE_SPACE_ID=<genie-space-id>`). This helper uses
python-dotenv's parser, which handles both correctly, and then launches the
Databricks CLI in a subprocess with the resolved env so operator workflow
stays `make bundle-validate-env` / `make bundle-deploy-dev`.

Mapping (env -> BUNDLE_VAR_*):
  DATABRICKS_WAREHOUSE_ID -> BUNDLE_VAR_sql_warehouse_id
  GENIE_SPACE_ID          -> BUNDLE_VAR_genie_space_id

Usage:
  python tools/databricks/bundle_env.py validate -t dev
  python tools/databricks/bundle_env.py plan     -t dev
  python tools/databricks/bundle_env.py deploy   -t dev

The dev target defaults to Summit demo first-party feeds so the public demo
keeps governed contactability / Lead Queue data after a refresh. For any
non-dev target, this wrapper refuses that flag unless
``MIP_ALLOW_DEMO_FIRST_PARTY_IN_PROD=1`` is also set.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools import render_sql  # noqa: E402

ENV_LOCAL = REPO / ".env.local"

PLACEHOLDER = "00000000PLACEHOLDER"


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
    """Resolve the DAB target from wrapper args with Databricks' dev default."""
    target = "dev"
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
    return target or "dev"


def _truthy(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _demo_feeds_allowed_for_target(
    *, target: str, enabled: bool, env: dict[str, str]
) -> bool:
    if not enabled:
        return True
    if target == "dev":
        return True
    return _truthy(env.get("MIP_ALLOW_DEMO_FIRST_PARTY_IN_PROD"))


def _demo_first_party_flag_for_target(env: dict[str, str], *, target: str) -> str | None:
    """Resolve the SQL render flag, mirroring scripts/deploy.sh.

    A bare render remains fail-closed; this deployment wrapper is target-aware.
    The Summit dev demo needs synthetic first-party feeds enabled so governed
    lead/contactability surfaces are populated after each gold refresh.
    """
    raw = env.get("MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS")
    if raw:
        return raw
    return "1" if target == "dev" else "0"


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "usage: bundle_env.py <validate|plan|deploy> [databricks bundle args...]",
            file=sys.stderr,
        )
        return 2

    subcmd, *rest = sys.argv[1:]
    if subcmd not in {"validate", "plan", "deploy"}:
        print(
            "usage: bundle_env.py <validate|plan|deploy> [databricks bundle args...]",
            file=sys.stderr,
        )
        return 2

    # Start from the current process env so PATH, HOME, DATABRICKS_CONFIG_*
    # all propagate, then overlay dotenv values.
    env = dict(os.environ)
    if ENV_LOCAL.exists():
        for k, v in dotenv_values(ENV_LOCAL).items():
            if v is None:
                continue
            # deploy.sh may pre-provision Genie and export GENIE_SPACE_ID
            # before calling this helper. Preserve that real value if
            # .env.local still carries the first-run blank/template value.
            # Catalog and synthetic-feed switches are deployment controls, not
            # ambient local defaults: stale .env.local values have previously
            # redirected SQL render to mip_demo while the Databricks App stayed
            # pinned to mip. Honour them only when explicitly exported by the
            # caller (scripts/deploy.sh does this after target-aware checks).
            if k in {"MIP_DEFAULT_CATALOG", "MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS"}:
                continue
            if k in {"DATABRICKS_WAREHOUSE_ID", "GENIE_SPACE_ID"} and _is_real(
                env.get(k)
            ):
                continue
            env[k] = v

    target = _target_from_args(rest)
    warehouse = env.get("DATABRICKS_WAREHOUSE_ID")
    genie = env.get("GENIE_SPACE_ID")
    if not _is_real(genie):
        space_id_file = REPO / "genie" / "space_id.txt"
        if space_id_file.exists():
            genie = space_id_file.read_text(encoding="utf-8").strip()
            if _is_real(genie):
                env["GENIE_SPACE_ID"] = genie

    if subcmd in {"plan", "deploy"}:
        missing = []
        if not _is_real(warehouse):
            missing.append("DATABRICKS_WAREHOUSE_ID")
        if not _is_real(genie):
            missing.append("GENIE_SPACE_ID")
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

    env["BUNDLE_VAR_sql_warehouse_id"] = warehouse if _is_real(warehouse) else PLACEHOLDER
    env["BUNDLE_VAR_genie_space_id"] = genie if _is_real(genie) else PLACEHOLDER

    catalog = env.get("MIP_DEFAULT_CATALOG") or "mip"
    env["BUNDLE_VAR_uc_catalog"] = catalog
    try:
        demo_first_party_enabled = render_sql._parse_bool(
            _demo_first_party_flag_for_target(env, target=target),
            default=False,
        )
    except ValueError as exc:
        print(f"[bundle_env] {exc}", file=sys.stderr)
        return 2

    if not _demo_feeds_allowed_for_target(
        target=target,
        enabled=demo_first_party_enabled,
        env=env,
    ):
        print(
            "[bundle_env] refusing to enable Summit demo first-party feeds "
            f"for target {target}. Set MIP_ALLOW_DEMO_FIRST_PARTY_IN_PROD=1 "
            "only for an approved demo workspace; never for customer production.",
            file=sys.stderr,
        )
        return 2

    processed, written, subs = render_sql.render(
        catalog=catalog,
        demo_first_party_enabled=demo_first_party_enabled,
    )

    # Operator feedback (never print the full value; just confirm resolution).
    def status(name: str, value: str | None) -> str:
        if _is_real(value):
            return f"{name}=set (…{value[-4:]})"
        return f"{name}=not-set (will use placeholder)"

    print(f"[bundle_env] {status('DATABRICKS_WAREHOUSE_ID', warehouse)}", file=sys.stderr)
    print(f"[bundle_env] {status('GENIE_SPACE_ID', genie)}", file=sys.stderr)
    print(
        "[bundle_env] "
        f"render_sql catalog={catalog} processed={processed} written={written} "
        f"substitutions={subs} "
        f"demo_first_party_feeds={'enabled' if demo_first_party_enabled else 'disabled'}",
        file=sys.stderr,
    )

    cli = ["databricks", "bundle", subcmd, *rest]
    result = subprocess.run(cli, env=env, check=False)
    if demo_first_party_enabled:
        render_sql.render(catalog=catalog, demo_first_party_enabled=False)
        print(
            "[bundle_env] restored sql/_rendered with "
            "demo_first_party_feeds=disabled after CLI exit",
            file=sys.stderr,
        )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
