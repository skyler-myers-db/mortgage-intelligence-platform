"""Emit a complete Databricks Apps deployment payload for Module 0.

Databricks Apps treats deployment ``env_vars`` as a full replacement for the
``app.yaml`` env list. This helper keeps the source-controlled safe baseline
and overlays non-secret operator settings from the process environment /
``.env.local`` so customer-specific configuration reaches the app runtime
without baking unsafe defaults into code.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import dotenv_values

REPO = Path(__file__).resolve().parents[2]
ENV_LOCAL = REPO / ".env.local"

APP_ENV_DEFAULT = "sandbox"
CATALOG_DEFAULT = "mip"
SCHEMA_DEFAULT = "gold"

NON_SECRET_OPERATOR_VARS = (
    "MIP_LENDER_NAME",
    "MIP_TENANT_ID",
    "MIP_ADMIN_GROUP_NAME",
    "MIP_ADMIN_EMAILS",
    "MIP_DEFAULT_ACTOR",
    "MIP_TRUST_FORWARDED_HEADERS",
    "MIP_RUM_ENABLED",
    "MIP_EXPOSE_OPENAPI",
    "MIP_CACHE_TTL_S",
    "MIP_SALES_STATE_CACHE_TTL_S",
    "MIP_PORTFOLIO_PREVIEW_TTL_S",
    "MIP_BACKPRESSURE_ENABLED",
    "MIP_RATE_LIMIT_DEFAULT_PER_MINUTE",
    "MIP_RATE_LIMIT_EXPENSIVE_PER_MINUTE",
    "MIP_RATE_LIMIT_MUTATION_PER_MINUTE",
    "MIP_RATE_LIMIT_GENIE_PER_MINUTE",
    "MIP_RATE_LIMIT_TELEMETRY_PER_MINUTE",
    "MIP_WAREHOUSE_CONCURRENCY_LIMIT",
    "MIP_LAKEBASE_CONCURRENCY_LIMIT",
    "MIP_GENIE_CONCURRENCY_LIMIT",
    "MIP_LAKEBASE_POOL_MAX_SIZE",
    "MIP_LAKEBASE_POOL_TIMEOUT_S",
    "MIP_LAKEBASE_POOL_MAX_LIFETIME_S",
    "DATABRICKS_TIMEOUT_S",
)


def _dotenv_overlay() -> dict[str, str]:
    values: dict[str, str] = {}
    if ENV_LOCAL.exists():
        for key, value in dotenv_values(ENV_LOCAL).items():
            if value is not None:
                values[key] = value
    return values


def _env_value(key: str, dotenv: dict[str, str]) -> str:
    return (os.environ.get(key) or dotenv.get(key) or "").strip()


def _append_value(env_vars: list[dict[str, str]], name: str, value: str) -> None:
    if value != "":
        env_vars.append({"name": name, "value": value})


def build_payload(
    *,
    source_code_path: str,
    target: str,
    current_user_email: str = "",
    app_env: str = APP_ENV_DEFAULT,
    catalog: str = CATALOG_DEFAULT,
    schema: str = SCHEMA_DEFAULT,
    mode: str = "SNAPSHOT",
) -> dict[str, object]:
    dotenv = _dotenv_overlay()
    env_vars: list[dict[str, str]] = [
        {"name": "APP_ENV", "value": app_env},
        {"name": "DATABRICKS_WAREHOUSE_ID", "value_from": "sql_warehouse"},
        {"name": "GENIE_SPACE_ID", "value_from": "genie_space"},
        {"name": "PGHOST", "value_from": "database"},
        {"name": "LAKEBASE_HOST", "value_from": "database"},
        {"name": "MIP_LIFECYCLE_SYNC_JOB_ID", "value_from": "lifecycle_sync_job"},
        {"name": "MIP_FRED_RATES_JOB_ID", "value_from": "fred_rates_job"},
        {"name": "MIP_SILVER_REFRESH_JOB_ID", "value_from": "silver_refresh_job"},
        {"name": "MIP_GOLD_REFRESH_JOB_ID", "value_from": "gold_refresh_job"},
        {"name": "MIP_DEFAULT_CATALOG", "value": catalog},
        {"name": "MIP_DEFAULT_SCHEMA", "value": schema},
    ]

    for name in NON_SECRET_OPERATOR_VARS:
        _append_value(env_vars, name, _env_value(name, dotenv))

    return {
        "source_code_path": source_code_path,
        "mode": mode,
        "env_vars": env_vars,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Emit a Databricks Apps deploy JSON payload for Module 0."
    )
    parser.add_argument("--source-code-path", required=True)
    parser.add_argument("--target", default="dev")
    parser.add_argument("--current-user-email", default="")
    parser.add_argument("--app-env", default=APP_ENV_DEFAULT)
    parser.add_argument("--catalog", default=CATALOG_DEFAULT)
    parser.add_argument("--schema", default=SCHEMA_DEFAULT)
    parser.add_argument("--mode", default="SNAPSHOT", choices=("SNAPSHOT", "AUTO_SYNC"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    payload = build_payload(
        source_code_path=args.source_code_path,
        target=args.target,
        current_user_email=args.current_user_email,
        app_env=args.app_env,
        catalog=args.catalog,
        schema=args.schema,
        mode=args.mode,
    )
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
