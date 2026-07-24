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
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backend.config.runtime_secret_policy import runtime_secret_text  # noqa: E402
from backend.schemas.lender_identity import (  # noqa: E402
    effective_public_tenant_id,
    validate_public_lender_identity,
)
from tools.databricks.lakebase_instance_contract import (  # noqa: E402
    DEFAULT_LAKEBASE_INSTANCE_NAME,
    validated_lakebase_instance_name,
)

ENV_LOCAL = REPO / ".env.local"

APP_ENV_DEFAULT = "sandbox"
CATALOG_DEFAULT = "mip"
SCHEMA_DEFAULT = "gold"
_APP_RESOURCE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,127}$")

SAFE_RUNTIME_DEFAULTS = {
    # Databricks Apps deployment env_vars are a full replacement for app.yaml.
    # Keep this idle-cost control in the deployment payload even when the
    # operator does not export it, otherwise the runtime falls back to code.
    "MIP_LEADS_WARM_INTERVAL_S": "0",
}

NON_SECRET_OPERATOR_VARS = (
    "MIP_LENDER_NAME",
    "MIP_LENDER_NMLS_ID",
    "MIP_TENANT_ID",
    "MIP_GIT_SHA",
    "MIP_APP_DEPLOYMENT_LEASE_ID",
    "MIP_ADMIN_GROUP_NAME",
    "MIP_ADMIN_EMAILS",
    "MIP_ADMIN_IDENTITIES",
    "MIP_APPROVER_GROUP_NAME",
    "MIP_APPROVER_EMAILS",
    "MIP_APPROVER_IDENTITIES",
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
    "MIP_AGENT_SUPERVISOR_ENDPOINT",
    "MIP_AI_GATEWAY_AGENT_MODEL",
    "MIP_AI_GATEWAY_AGENT_MODEL_VERSION",
    "MIP_AI_GATEWAY_AGENT_MODEL_SOURCE",
    "MIP_AI_GATEWAY_AGENT_MODEL_FAMILY",
    "MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE",
    "MIP_AI_GATEWAY_TABLE_PREFIX",
    "MIP_AI_GATEWAY_EXPERIMENT_NAME",
    "MIP_AI_GATEWAY_EXPERIMENT_ID",
    "MIP_AGENT_SUPERVISOR_ID",
    "MIP_AGENT_SUPERVISOR_NAME",
    "MIP_AGENT_RUNTIME_CLIENT_ID",
    "MIP_REVIEWED_FUNCTION_OWNER",
    "MIP_AGENT_PROXY_CLIENT_ID",
    "MIP_AGENT_PROXY_CREDENTIAL_ID",
    "MIP_AGENT_PROXY_SECRET_REFERENCE",
    "MIP_AI_GATEWAY_ENDPOINT",
    "MIP_AI_GATEWAY_INFERENCE_TABLE",
    "MIP_EXPECTED_AGENT_GATEWAY_BINDING_SHA256",
    "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON",
    "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256",
    "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SIGNATURE",
    "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY",
    "MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY",
    "MIP_AI_GATEWAY_PROOF_VERIFY_KEY",
    "MIP_AGENT_EVAL_EXPERIMENT",
    "MIP_AGENT_EVAL_RUN_ID",
    "MIP_LIVE_CAPABILITY_PROBE_TTL_S",
    "MIP_GENIE_ACTION_SECRET_KID",
    "MIP_LAKEBASE_SYNC_CATALOG",
    "MIP_LAKEBASE_SYNC_SCHEMA",
    "MIP_LAKEBASE_SYNC_TABLES",
    "SALESFORCE_INSTANCE_URL",
    "SALESFORCE_CLIENT_ID",
    "SALESFORCE_USERNAME",
    "SALESFORCE_API_VERSION",
    "SALESFORCE_SOBJECT",
    "SALESFORCE_EXTERNAL_ID_FIELD",
    "SALESFORCE_TIMEOUT_S",
)

SECRET_RESOURCE_BINDINGS = {
    "MIP_COTALITY_ID_MASK_SECRET": "cotality_id_mask_secret",
    "MIP_GENIE_ACTION_SECRET_CURRENT": "genie_action_current_secret",
    "MIP_GENIE_ACTION_SECRET_PREVIOUS": "genie_action_previous_secret",
    "SALESFORCE_CLIENT_SECRET": "salesforce_client_secret",
    "SALESFORCE_PASSWORD": "salesforce_password",
    "SALESFORCE_SECURITY_TOKEN": "salesforce_security_token",
}
SALESFORCE_REQUIRED_VARS = (
    "SALESFORCE_INSTANCE_URL",
    "SALESFORCE_CLIENT_ID",
    "SALESFORCE_USERNAME",
    "SALESFORCE_EXTERNAL_ID_FIELD",
    "SALESFORCE_CLIENT_SECRET",
    "SALESFORCE_PASSWORD",
)
SALESFORCE_SECRET_ENVS = frozenset(
    {"SALESFORCE_CLIENT_SECRET", "SALESFORCE_PASSWORD", "SALESFORCE_SECURITY_TOKEN"}
)
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


def validated_otel_endpoint(value: str) -> str:
    """Return a credential-free HTTPS OTLP endpoint or fail closed."""

    endpoint = value.strip()
    try:
        parts = urlsplit(endpoint)
    except ValueError as exc:
        raise ValueError("MIP_OTEL_ENDPOINT must be a valid URL") from exc
    if parts.scheme != "https" or not parts.netloc:
        raise ValueError("MIP_OTEL_ENDPOINT must be an https URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError(
            "MIP_OTEL_ENDPOINT must not contain credentials, query strings, or fragments"
        )
    return endpoint


def validated_app_resource_name(value: str) -> str:
    """Return a Databricks App resource name accepted by the payload contract."""

    candidate = value.strip()
    if not _APP_RESOURCE_NAME_RE.fullmatch(candidate):
        raise ValueError(
            "App resource names must start with a letter and contain only letters, "
            "digits, '_' or '-'"
        )
    return candidate


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
    lakebase_instance: str = DEFAULT_LAKEBASE_INSTANCE_NAME,
    mode: str = "SNAPSHOT",
    campaign_treatment_runtime_enabled: bool = False,
    otel_endpoint: str = "",
    otel_header_resource: str = "",
) -> dict[str, object]:
    lakebase_instance = validated_lakebase_instance_name(lakebase_instance)
    dotenv = _dotenv_overlay()
    lender_name, lender_nmls_id = validate_public_lender_identity(
        _env_value("MIP_LENDER_NAME", dotenv) or "Summit Mortgage",
        _env_value("MIP_LENDER_NMLS_ID", dotenv),
    )
    tenant_id = effective_public_tenant_id(
        _env_value("MIP_TENANT_ID", dotenv),
        lender_name=lender_name,
    )
    resolved_lender_values = {
        "MIP_LENDER_NAME": lender_name,
        "MIP_LENDER_NMLS_ID": lender_nmls_id,
        "MIP_TENANT_ID": tenant_id,
    }
    previous_secret_enabled, previous_secret_kid = _previous_secret_grace_configured(dotenv)
    if bool(otel_endpoint.strip()) != bool(otel_header_resource.strip()):
        raise ValueError(
            "MIP_OTEL_ENDPOINT and its App secret resource must be configured together"
        )
    env_vars: list[dict[str, str]] = [
        {"name": "APP_ENV", "value": app_env},
        {"name": "DATABRICKS_WAREHOUSE_ID", "value_from": "sql_warehouse"},
        {"name": "GENIE_SPACE_ID", "value_from": "genie_space"},
        {"name": "PGHOST", "value_from": "database"},
        {"name": "LAKEBASE_HOST", "value_from": "database"},
        {"name": "LAKEBASE_INSTANCE_NAME", "value": lakebase_instance},
        {"name": "MIP_LAKEBASE_INSTANCE", "value": lakebase_instance},
        {"name": "MIP_LIFECYCLE_SYNC_JOB_ID", "value_from": "lifecycle_sync_job"},
        {"name": "MIP_FRED_RATES_JOB_ID", "value_from": "fred_rates_job"},
        {"name": "MIP_SILVER_REFRESH_JOB_ID", "value_from": "silver_refresh_job"},
        {"name": "MIP_GOLD_REFRESH_JOB_ID", "value_from": "gold_refresh_job"},
        *(
            {"name": name, "value_from": resource}
            for name, resource in SECRET_RESOURCE_BINDINGS.items()
            if name != PREVIOUS_SECRET_ENV and name not in SALESFORCE_SECRET_ENVS
        ),
        {"name": "MIP_DEFAULT_CATALOG", "value": catalog},
        {"name": "MIP_DEFAULT_SCHEMA", "value": schema},
        {
            "name": "MIP_CAMPAIGN_TREATMENT_RUNTIME_ENABLED",
            "value": "1" if campaign_treatment_runtime_enabled else "0",
        },
    ]

    if otel_endpoint:
        env_vars.extend(
            (
                {"name": "MIP_OTEL_ENDPOINT", "value": validated_otel_endpoint(otel_endpoint)},
                {
                    "name": "MIP_OTEL_HEADERS",
                    "value_from": validated_app_resource_name(otel_header_resource),
                },
            )
        )

    if previous_secret_enabled:
        env_vars.append(
            {
                "name": PREVIOUS_SECRET_ENV,
                "value_from": SECRET_RESOURCE_BINDINGS[PREVIOUS_SECRET_ENV],
            }
        )
        _append_value(env_vars, PREVIOUS_SECRET_KID_ENV, previous_secret_kid)

    salesforce_configured = all(_env_value(name, dotenv) for name in SALESFORCE_REQUIRED_VARS)
    if salesforce_configured:
        for name in sorted(SALESFORCE_SECRET_ENVS):
            if name == "SALESFORCE_SECURITY_TOKEN" and not _env_value(name, dotenv):
                continue
            env_vars.append({"name": name, "value_from": SECRET_RESOURCE_BINDINGS[name]})

    for name in NON_SECRET_OPERATOR_VARS:
        _append_value(
            env_vars,
            name,
            resolved_lender_values.get(name)
            or _env_value(name, dotenv)
            or SAFE_RUNTIME_DEFAULTS.get(name, ""),
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
    parser.add_argument(
        "--lakebase-instance",
        default=DEFAULT_LAKEBASE_INSTANCE_NAME,
    )
    parser.add_argument("--mode", default="SNAPSHOT", choices=("SNAPSHOT", "AUTO_SYNC"))
    parser.add_argument(
        "--enable-campaign-treatment-runtime",
        action="store_true",
        help=(
            "Set only after treatment constraints/properties are proven while "
            "access remains quiesced; restore runtime MODIFY after promotion."
        ),
    )
    parser.add_argument("--otel-endpoint", default="")
    parser.add_argument("--otel-header-resource", default="")
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
        lakebase_instance=args.lakebase_instance,
        mode=args.mode,
        campaign_treatment_runtime_enabled=args.enable_campaign_treatment_runtime,
        otel_endpoint=args.otel_endpoint,
        otel_header_resource=args.otel_header_resource,
    )
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
