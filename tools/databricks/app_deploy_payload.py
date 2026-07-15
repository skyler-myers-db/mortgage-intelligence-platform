"""Emit a complete Databricks Apps deployment payload for Module 0.

Databricks Apps treats deployment ``env_vars`` as a full replacement for the
``app.yaml`` env list. This helper keeps the source-controlled safe baseline
and overlays operator settings from the process environment / ``.env.local``
so customer-specific configuration reaches the app runtime without baking
unsafe defaults into code.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import dotenv_values

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backend.config.runtime_secret_policy import runtime_secret_text  # noqa: E402

ENV_LOCAL = REPO / ".env.local"

APP_ENV_DEFAULT = "sandbox"
CATALOG_DEFAULT = "mip"
SCHEMA_DEFAULT = "gold"

SAFE_RUNTIME_DEFAULTS = {
    # Databricks Apps deployment env_vars are a full replacement for app.yaml.
    # Keep this idle-cost control in the deployment payload even when the
    # operator does not export it, otherwise the runtime falls back to code.
    "MIP_LEADS_WARM_INTERVAL_S": "0",
}

NON_SECRET_OPERATOR_VARS = (
    "MIP_LENDER_NAME",
    "MIP_TENANT_ID",
    "MIP_GIT_SHA",
    "MIP_ADMIN_GROUP_NAME",
    "MIP_ADMIN_EMAILS",
    "MIP_APPROVER_EMAILS",
    "MIP_DEFAULT_ACTOR",
    "MIP_TRUST_FORWARDED_HEADERS",
    "MIP_RUM_ENABLED",
    "MIP_EXPOSE_OPENAPI",
    "MIP_CACHE_TTL_S",
    "MIP_LEADS_WARM_INTERVAL_S",
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
    "MIP_AGENT_ORCHESTRATOR",
    "MIP_AI_GATEWAY",
    "MIP_LAKEBASE_SYNC",
    "MIP_AGENT_MODEL",
    "MIP_AGENT_SERVING_ENDPOINT",
    "MIP_AGENT_SUPERVISOR_ID",
    "MIP_AGENT_SUPERVISOR_NAME",
    "MIP_AI_GATEWAY_ENDPOINT",
    "MIP_AI_GATEWAY_INFERENCE_TABLE",
    "MIP_AI_GATEWAY_PROOF_VERIFY_KEY",
    "MIP_AGENT_EVAL_EXPERIMENT",
    "MIP_AGENT_EVAL_RUN_ID",
    "MIP_LIVE_CAPABILITY_PROBE_TTL_S",
    "MIP_GENIE_ACTION_SECRET_KID",
    "MIP_LAKEBASE_SYNC_CATALOG",
    "MIP_LAKEBASE_SYNC_SCHEMA",
    "MIP_LAKEBASE_SYNC_TABLES",
)

SECRET_RESOURCE_BINDINGS = {
    "MIP_COTALITY_ID_MASK_SECRET": "cotality_id_mask_secret",
    "MIP_GENIE_ACTION_SECRET_CURRENT": "genie_action_current_secret",
    "MIP_GENIE_ACTION_SECRET_PREVIOUS": "genie_action_previous_secret",
}
PREVIOUS_SECRET_ENV = "MIP_GENIE_ACTION_SECRET_PREVIOUS"
PREVIOUS_SECRET_KID_ENV = "MIP_GENIE_ACTION_SECRET_PREVIOUS_KID"


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


def _previous_secret_grace_configured(dotenv: dict[str, str]) -> tuple[bool, str]:
    previous = runtime_secret_text(_env_value(PREVIOUS_SECRET_ENV, dotenv))
    previous_kid = _env_value(PREVIOUS_SECRET_KID_ENV, dotenv)
    if previous and not previous_kid:
        raise ValueError(
            f"{PREVIOUS_SECRET_KID_ENV} is required when {PREVIOUS_SECRET_ENV} "
            "is retained during a rotation grace period"
        )
    return previous is not None, previous_kid


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
    previous_secret_enabled, previous_secret_kid = _previous_secret_grace_configured(dotenv)
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
        *(
            {"name": name, "value_from": resource}
            for name, resource in SECRET_RESOURCE_BINDINGS.items()
            if name != PREVIOUS_SECRET_ENV
        ),
        {"name": "MIP_DEFAULT_CATALOG", "value": catalog},
        {"name": "MIP_DEFAULT_SCHEMA", "value": schema},
    ]

    if previous_secret_enabled:
        env_vars.append(
            {
                "name": PREVIOUS_SECRET_ENV,
                "value_from": SECRET_RESOURCE_BINDINGS[PREVIOUS_SECRET_ENV],
            }
        )
        _append_value(env_vars, PREVIOUS_SECRET_KID_ENV, previous_secret_kid)

    for name in NON_SECRET_OPERATOR_VARS:
        _append_value(
            env_vars,
            name,
            _env_value(name, dotenv) or SAFE_RUNTIME_DEFAULTS.get(name, ""),
        )
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
