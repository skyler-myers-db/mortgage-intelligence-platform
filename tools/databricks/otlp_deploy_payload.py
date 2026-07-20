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
import sys
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.databricks.app_deploy_payload import (  # noqa: E402
    build_payload as build_app_deploy_payload,
)
from tools.databricks.app_deploy_payload import validated_app_resource_name  # noqa: E402


def _valid_resource_name(value: str) -> str:
    try:
        return validated_app_resource_name(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "resource names must start with a letter and contain only letters, digits, '_' or '-'"
        ) from exc


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
    return build_app_deploy_payload(
        source_code_path=source_code_path,
        target="prod",
        app_env=app_env,
        catalog=catalog,
        schema=schema,
        mode=mode,
        campaign_treatment_runtime_enabled=True,
        otel_endpoint=endpoint,
        otel_header_resource=header_resource,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Emit the canonical Module 0 App payload with secret-backed OTLP. "
            "Operators must deploy it through scripts/deploy.sh."
        )
    )
    parser.add_argument(
        "--source-code-path",
        required=True,
        help="Workspace source path uploaded by the signed deploy resource phase.",
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
