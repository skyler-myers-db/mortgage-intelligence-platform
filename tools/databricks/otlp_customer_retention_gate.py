"""Validate customer-owned OTLP retention evidence.

This tool is intentionally evidence-driven. The app can prove OTLP
transport, but it cannot certify a customer's collector retention policy
without collector-side evidence. The gate returns ``passed`` only when a
fresh deployed-app correlation id is found in a customer-owned collector
and retention/ACL proof references are present.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_CORRELATION_RE = re.compile(
    r"^(?:[a-f0-9]{32}|[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})$",
    re.IGNORECASE,
)
_TEMP_COLLECTOR_HOST_PARTS = (
    "webhook.site",
    "requestcatcher",
    "pipedream",
    "beeceptor",
    "ngrok",
    "localhost",
    "127.0.0.1",
)
_SECRET_VALUE_PATTERNS = (
    re.compile(r"\bMIP_OTEL_HEADERS\s*=", re.IGNORECASE),
    re.compile(r"\bAuthorization\s*[:=]", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"\b(?:x-)?api[-_ ]?key\s*[:=]", re.IGNORECASE),
    re.compile(r"\bdd-api-key\s*[:=]", re.IGNORECASE),
)
_SECRET_KEY_NAMES = {
    "mip_otel_headers",
    "otel_headers",
    "authorization",
    "headers",
    "api_key",
    "apikey",
    "token",
}


def _get(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _require_str(data: dict[str, Any], path: str, errors: list[str]) -> str:
    value = _get(data, path)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path} is required")
        return ""
    return value.strip()


def _normalise_correlation_id(value: str) -> str:
    return value.strip().lower()


def _parse_utc(value: str, path: str, errors: list[str]) -> datetime | None:
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{path} must be an ISO-8601 timestamp")
        return None
    if parsed.tzinfo is None:
        errors.append(f"{path} must include a timezone")
        return None
    return parsed.astimezone(UTC)


def _validate_endpoint(endpoint: str, errors: list[str]) -> None:
    try:
        parts = urlsplit(endpoint)
    except ValueError:
        errors.append("collector.endpoint must be a valid URL")
        return
    if parts.scheme != "https" or not parts.netloc:
        errors.append("collector.endpoint must be an https URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        errors.append(
            "collector.endpoint must not include credentials, query strings, or fragments"
        )
    host = (parts.hostname or "").lower()
    if any(part in host for part in _TEMP_COLLECTOR_HOST_PARTS):
        errors.append("collector.endpoint must be customer-owned, not a temporary proof collector")


def _walk_values(value: Any, path: str = "") -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        items: list[tuple[str, Any]] = []
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            items.append((child_path, child))
            items.extend(_walk_values(child, child_path))
        return items
    if isinstance(value, list):
        items = []
        for index, child in enumerate(value):
            child_path = f"{path}[{index}]"
            items.append((child_path, child))
            items.extend(_walk_values(child, child_path))
        return items
    return []


def _validate_no_secret_values(data: dict[str, Any], errors: list[str]) -> None:
    for path, value in _walk_values(data):
        key = path.rsplit(".", 1)[-1].lower()
        if key in _SECRET_KEY_NAMES:
            errors.append(f"{path} must not contain plaintext collector headers or tokens")
            continue
        if not isinstance(value, str):
            continue
        for pattern in _SECRET_VALUE_PATTERNS:
            if pattern.search(value):
                errors.append(f"{path} appears to contain a collector secret")
                break


def validate_evidence(
    data: dict[str, Any],
    *,
    min_retention_days: int = 30,
    max_age_minutes: int = 120,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a structured pass/block result for customer retention evidence."""
    errors: list[str] = []
    now = (now or datetime.now(UTC)).astimezone(UTC)

    _validate_no_secret_values(data, errors)

    app_name = _require_str(data, "app.name", errors)
    deployment_id = _require_str(data, "app.active_deployment_id", errors)
    header_resource = _require_str(data, "app.otel_header_resource", errors)
    secret_ref = _require_str(data, "app.otel_header_secret_ref", errors)
    log_export = _require_str(data, "health.log_export", errors)
    collector_owner = _require_str(data, "collector.owner", errors)
    endpoint = _require_str(data, "collector.endpoint", errors)
    retention_policy_ref = _require_str(data, "collector.retention_policy_ref", errors)
    acl_proof_ref = _require_str(data, "collector.acl_proof_ref", errors)
    query_proof_ref = _require_str(data, "collector.query_proof_ref", errors)
    probe_correlation_id = _require_str(data, "probe.correlation_id", errors)
    collector_correlation_id = _require_str(data, "collector.query_correlation_id", errors)
    probe_sent_at = _require_str(data, "probe.sent_at_utc", errors)
    collector_observed_at = _require_str(data, "collector.query_observed_at_utc", errors)

    if app_name and app_name == "mip-app":
        pass
    if deployment_id and len(deployment_id) < 12:
        errors.append("app.active_deployment_id must identify the deployed app snapshot")
    if header_resource and header_resource != "otel_headers":
        errors.append("app.otel_header_resource must be the Databricks App secret resource")
    if secret_ref and not secret_ref.startswith("databricks://secrets/"):
        errors.append("app.otel_header_secret_ref must be a Databricks Secrets reference")
    if log_export and log_export != "otlp":
        errors.append("health.log_export must be otlp")
    if endpoint:
        _validate_endpoint(endpoint, errors)

    customer_owned = _get(data, "collector.customer_owned")
    if customer_owned is not True:
        errors.append("collector.customer_owned must be true")
    if collector_owner and "entrada" in collector_owner.lower():
        errors.append("collector.owner must name the customer collector owner, not Entrada")

    retention_days = _get(data, "collector.retention_days")
    if not isinstance(retention_days, int):
        errors.append("collector.retention_days must be an integer")
    elif retention_days < min_retention_days:
        errors.append(
            f"collector.retention_days must be >= {min_retention_days} for this gate"
        )

    if probe_correlation_id and not _CORRELATION_RE.fullmatch(probe_correlation_id):
        errors.append("probe.correlation_id must be a deployed-app correlation id")
    if collector_correlation_id and not _CORRELATION_RE.fullmatch(collector_correlation_id):
        errors.append("collector.query_correlation_id must be a collector correlation id")
    if (
        probe_correlation_id
        and collector_correlation_id
        and _normalise_correlation_id(probe_correlation_id)
        != _normalise_correlation_id(collector_correlation_id)
    ):
        errors.append("collector.query_correlation_id must match probe.correlation_id")

    sent_at = _parse_utc(probe_sent_at, "probe.sent_at_utc", errors) if probe_sent_at else None
    observed_at = (
        _parse_utc(collector_observed_at, "collector.query_observed_at_utc", errors)
        if collector_observed_at
        else None
    )
    if sent_at is not None:
        if sent_at > now + timedelta(minutes=5):
            errors.append("probe.sent_at_utc cannot be in the future")
        if now - sent_at > timedelta(minutes=max_age_minutes):
            errors.append(
                f"probe.sent_at_utc must be within {max_age_minutes} minutes of validation"
            )
    if sent_at is not None and observed_at is not None:
        if observed_at + timedelta(minutes=5) < sent_at:
            errors.append("collector.query_observed_at_utc cannot predate the probe")
        if now - observed_at > timedelta(minutes=max_age_minutes):
            errors.append(
                "collector.query_observed_at_utc must be fresh enough to prove current delivery"
            )

    status = "passed" if not errors else "blocked"
    return {
        "status": status,
        "errors": errors,
        "summary": {
            "app": app_name or None,
            "deployment_id": deployment_id or None,
            "log_export": log_export or None,
            "header_resource": header_resource or None,
            "secret_ref": secret_ref or None,
            "collector_owner": collector_owner or None,
            "collector_endpoint": endpoint or None,
            "retention_days": retention_days if isinstance(retention_days, int) else None,
            "retention_policy_ref": retention_policy_ref or None,
            "acl_proof_ref": acl_proof_ref or None,
            "query_proof_ref": query_proof_ref or None,
            "correlation_id": _normalise_correlation_id(probe_correlation_id)
            if probe_correlation_id
            else None,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate evidence before claiming customer-owned OTLP retention."
    )
    parser.add_argument("evidence_json", type=Path, help="Structured customer proof JSON file.")
    parser.add_argument("--min-retention-days", type=int, default=30)
    parser.add_argument("--max-age-minutes", type=int, default=120)
    parser.add_argument(
        "--now-utc",
        help="Override validation clock for tests, as ISO-8601 UTC timestamp.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        data = json.loads(args.evidence_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"could not read evidence JSON: {exc}\n")
        return 2
    if not isinstance(data, dict):
        raise SystemExit("evidence JSON must be an object")
    now_errors: list[str] = []
    now = _parse_utc(args.now_utc, "--now-utc", now_errors) if args.now_utc else None
    if now_errors:
        for error in now_errors:
            sys.stderr.write(f"{error}\n")
        return 2
    result = validate_evidence(
        data,
        min_retention_days=args.min_retention_days,
        max_age_minutes=args.max_age_minutes,
        now=now,
    )
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
