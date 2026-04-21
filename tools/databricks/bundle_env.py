"""Run a Databricks CLI command with `.env.local` sourced via python-dotenv.

The Makefile target that sources `.env.local` directly in bash broke on
unquoted spaces (e.g. `MIP_DEMO_LENDER=Summit Mortgage`) and angle-bracket
placeholder values (`GENIE_SPACE_ID=<genie-space-id>`). This helper uses
python-dotenv's parser, which handles both correctly, and then launches the
Databricks CLI in a subprocess with the resolved env so operator workflow
stays `make bundle-validate-env` / `make bundle-deploy-dev`.

Mapping (env -> BUNDLE_VAR_*):
  DATABRICKS_WAREHOUSE_ID -> BUNDLE_VAR_sql_warehouse_id
  GENIE_SPACE_ID          -> BUNDLE_VAR_genie_space_id

Usage:
  python tools/databricks/bundle_env.py validate -t dev
  python tools/databricks/bundle_env.py deploy   -t dev
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

REPO = Path(__file__).resolve().parents[2]
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


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "usage: bundle_env.py <validate|deploy> [databricks bundle args...]",
            file=sys.stderr,
        )
        return 2

    subcmd, *rest = sys.argv[1:]

    # Start from the current process env so PATH, HOME, DATABRICKS_CONFIG_*
    # all propagate, then overlay dotenv values.
    env = dict(os.environ)
    if ENV_LOCAL.exists():
        for k, v in dotenv_values(ENV_LOCAL).items():
            if v is not None:
                env[k] = v

    warehouse = env.get("DATABRICKS_WAREHOUSE_ID")
    genie = env.get("GENIE_SPACE_ID")

    env["BUNDLE_VAR_sql_warehouse_id"] = warehouse if _is_real(warehouse) else PLACEHOLDER
    env["BUNDLE_VAR_genie_space_id"] = genie if _is_real(genie) else PLACEHOLDER

    # Operator feedback (never print the full value; just confirm resolution).
    def status(name: str, value: str | None) -> str:
        if _is_real(value):
            return f"{name}=set (…{value[-4:]})"
        return f"{name}=not-set (will use placeholder)"

    print(f"[bundle_env] {status('DATABRICKS_WAREHOUSE_ID', warehouse)}", file=sys.stderr)
    print(f"[bundle_env] {status('GENIE_SPACE_ID', genie)}", file=sys.stderr)

    cli = ["databricks", "bundle", subcmd, *rest]
    return subprocess.run(cli, env=env, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
