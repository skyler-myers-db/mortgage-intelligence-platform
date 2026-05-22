"""Build a Databricks Apps deployment payload for secret-backed OTLP.

The Apps deploy API treats ``env_vars`` as a full replacement for the
``app.yaml`` environment list. This helper emits the complete safe list
used by Module 0 plus ``MIP_OTEL_ENDPOINT`` and ``MIP_OTEL_HEADERS`` wired
from the ``otel_headers`` app secret resource. It never accepts or prints
collector header values.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from urllib.parse import urlsplit

_RESOURCE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,127}$")


def _valid_resource_name(value: str) -> str:
    candidate = value.strip()
    if not _RESOURCE_NAME_RE.fullmatch(candidate):
        raise argparse.ArgumentTypeError(
            "resource names must start with a letter and contain only letters, digits, '_' or '-'"
        )
    return candidate


def _valid_otel_endpoint(value: str) -> str:
    endpoint = value.strip()
    try:
        parts = urlsplit(endpoint)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("MIP_OTEL_ENDPOINT must be a valid URL") from exc
    if parts.scheme != "https" or not parts.netloc:
        raise argparse.ArgumentTypeError("MIP_OTEL_ENDPOINT must be an https URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise argparse.ArgumentTypeError(
            "MIP_OTEL_ENDPOINT must not contain userinfo, query strings, or fragments; "
            "put credentials in MIP_OTEL_HEADERS"
        )
    return endpoint


def build_payload(
    *,
    source_code_path: str,
    endpoint: str,
    header_resource: str = "otel_headers",
    mode: str = "SNAPSHOT",
    app_env: str = "sandbox",
    catalog: str = "mip",
    schema: str = "gold",
) -> dict[str, object]:
    return {
        "source_code_path": source_code_path,
        "mode": mode,
        "env_vars": [
            {"name": "APP_ENV", "value": app_env},
            {"name": "DATABRICKS_WAREHOUSE_ID", "value_from": "sql_warehouse"},
            {"name": "GENIE_SPACE_ID", "value_from": "genie_space"},
            {"name": "PGHOST", "value_from": "database"},
            {"name": "LAKEBASE_HOST", "value_from": "database"},
            {"name": "MIP_LIFECYCLE_SYNC_JOB_ID", "value_from": "lifecycle_sync_job"},
            {"name": "MIP_DEFAULT_CATALOG", "value": catalog},
            {"name": "MIP_DEFAULT_SCHEMA", "value": schema},
            {"name": "MIP_OTEL_ENDPOINT", "value": endpoint},
            {"name": "MIP_OTEL_HEADERS", "value_from": header_resource},
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Emit a Databricks Apps deploy JSON payload for secret-backed OTLP."
    )
    parser.add_argument(
        "--source-code-path",
        required=True,
        help="Workspace source path uploaded by databricks bundle deploy.",
    )
    parser.add_argument(
        "--endpoint",
        required=True,
        type=_valid_otel_endpoint,
        help="Customer-owned HTTPS OTLP /v1/logs endpoint. Must not include credentials.",
    )
    parser.add_argument(
        "--header-resource",
        default="otel_headers",
        type=_valid_resource_name,
        help="Databricks Apps secret resource name that resolves MIP_OTEL_HEADERS.",
    )
    parser.add_argument("--mode", default="SNAPSHOT", choices=("SNAPSHOT", "AUTO_SYNC"))
    parser.add_argument("--app-env", default="sandbox")
    parser.add_argument("--catalog", default="mip")
    parser.add_argument("--schema", default="gold")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    payload = build_payload(
        source_code_path=args.source_code_path,
        endpoint=args.endpoint,
        header_resource=args.header_resource,
        mode=args.mode,
        app_env=args.app_env,
        catalog=args.catalog,
        schema=args.schema,
    )
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
