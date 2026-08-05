"""Build and verify source-free Databricks App resource-binding updates.

The DAB App resource cannot participate in the pre-migration deployment: doing
so also deploys ``source_code_path`` and can start an unverified candidate. This
helper resolves only the App's resource bindings from a post-deploy bundle
summary. The resulting payload is safe for ``databricks apps create
--no-compute`` or ``databricks apps update`` and deliberately contains no
source deployment fields.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

_RESOURCE_REFERENCE_RE = re.compile(
    r"\$\{resources\.(?P<kind>[a-z_]+)\.(?P<key>[A-Za-z0-9_-]+)\."
    r"(?P<field>[A-Za-z0-9_]+)\}\Z"
)
_SECRET_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _resolve_value(value: Any, resources: dict[str, Any]) -> Any:
    if isinstance(value, list):
        return [_resolve_value(item, resources) for item in value]
    if isinstance(value, dict):
        return {key: _resolve_value(item, resources) for key, item in value.items()}
    if not isinstance(value, str):
        return value
    match = _RESOURCE_REFERENCE_RE.fullmatch(value)
    if match is None:
        if "${" in value:
            raise ValueError(f"unsupported unresolved bundle reference: {value!r}")
        return value
    kind = match.group("kind")
    key = match.group("key")
    field = match.group("field")
    entry = (resources.get(kind) or {}).get(key)
    if not isinstance(entry, dict):
        raise ValueError(f"bundle summary has no resource {kind}.{key}")
    resolved = entry.get(field)
    if not isinstance(resolved, str | int) or str(resolved).strip() == "":
        raise ValueError(f"bundle summary has no concrete {kind}.{key}.{field}")
    if "${" in str(resolved):
        raise ValueError(f"bundle resource {kind}.{key}.{field} remains unresolved")
    return resolved


def build_resource_binding_payload(
    summary: dict[str, Any],
    *,
    app_name: str,
    otel_header_secret_scope: str = "",
    otel_header_secret_key: str = "",
) -> dict[str, Any]:
    """Return the exact source-free App create/update body."""

    resources = summary.get("resources")
    if not isinstance(resources, dict):
        raise ValueError("bundle summary exposes no resources object")
    app = ((resources.get("apps") or {}).get("mip_app"))
    if not isinstance(app, dict):
        raise ValueError("bundle summary exposes no resources.apps.mip_app")
    configured_name = str(app.get("name") or "").strip()
    if configured_name != app_name:
        raise ValueError(
            f"bundle App name {configured_name!r} does not match target {app_name!r}"
        )
    configured_bindings = app.get("resources")
    if not isinstance(configured_bindings, list) or not configured_bindings:
        raise ValueError("bundle App exposes no resource bindings")
    bindings = _resolve_value(configured_bindings, resources)
    if not isinstance(bindings, list):  # defensive: recursive resolver preserves lists
        raise ValueError("resolved App resource bindings are invalid")
    if bool(otel_header_secret_scope.strip()) != bool(otel_header_secret_key.strip()):
        raise ValueError("OTLP header secret scope and key must be configured together")
    if otel_header_secret_scope:
        scope = otel_header_secret_scope.strip()
        key = otel_header_secret_key.strip()
        if not _SECRET_COMPONENT_RE.fullmatch(scope) or not _SECRET_COMPONENT_RE.fullmatch(key):
            raise ValueError("OTLP header secret scope and key are invalid")
        bindings.append(
            {
                "name": "otel_headers",
                "description": "Customer-owned OTLP collector authorization headers.",
                "secret": {"scope": scope, "key": key, "permission": "READ"},
            }
        )
    names = [str(item.get("name") or "") for item in bindings if isinstance(item, dict)]
    if len(names) != len(bindings) or len(names) != len(set(names)) or any(not name for name in names):
        raise ValueError("App resource binding names must be non-empty and unique")
    database_bindings = [
        item for item in bindings if isinstance(item, dict) and item.get("database") is not None
    ]
    if len(database_bindings) != 1:
        raise ValueError("App must expose exactly one Lakebase database binding")
    payload: dict[str, Any] = {
        "name": app_name,
        "resources": bindings,
    }
    description = str(app.get("description") or "").strip()
    if description:
        payload["description"] = description
    forbidden = {"source_code_path", "env_vars", "mode"} & payload.keys()
    if forbidden:
        raise ValueError(f"source-free App payload contains forbidden fields: {sorted(forbidden)}")
    return payload


def _assert_exact_transition(
    *,
    expected: dict[str, Any],
    after: dict[str, Any],
    before: dict[str, Any] | None,
    require_stopped_without_deployment: bool,
) -> None:
    app_name = str(expected.get("name") or "")
    if str(after.get("name") or "") != app_name:
        raise ValueError("updated App identity does not match the binding payload")
    if str(after.get("description") or "") != str(expected.get("description") or ""):
        raise ValueError("updated App description does not exactly match the binding payload")
    if after.get("resources") != expected.get("resources"):
        raise ValueError("updated App resource bindings do not exactly match the bundle")
    if before is not None:
        for field in ("active_deployment", "pending_deployment"):
            if after.get(field) != before.get(field):
                raise ValueError(f"App resource update changed {field}")
        before_compute = (before.get("compute_status") or {}).get("state")
        after_compute = (after.get("compute_status") or {}).get("state")
        if before_compute != after_compute:
            raise ValueError("App resource update changed compute state")
    if require_stopped_without_deployment:
        if after.get("active_deployment") is not None or after.get("pending_deployment") is not None:
            raise ValueError("first-install App acquired a source deployment before migration")
        state = str((after.get("compute_status") or {}).get("state") or "")
        if state != "STOPPED":
            raise ValueError(f"first-install App compute is not stopped: {state!r}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--bundle-summary", type=Path, required=True)
    build.add_argument("--app-name", required=True)
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--otel-header-secret-scope", default="")
    build.add_argument("--otel-header-secret-key", default="")

    verify = subparsers.add_parser("verify")
    verify.add_argument("--expected", type=Path, required=True)
    verify.add_argument("--after", type=Path, required=True)
    verify.add_argument("--before", type=Path)
    verify.add_argument("--require-stopped-without-deployment", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "build":
        payload = build_resource_binding_payload(
            _load_object(args.bundle_summary),
            app_name=args.app_name,
            otel_header_secret_scope=args.otel_header_secret_scope,
            otel_header_secret_key=args.otel_header_secret_key,
        )
        args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return 0
    _assert_exact_transition(
        expected=_load_object(args.expected),
        after=_load_object(args.after),
        before=_load_object(args.before) if args.before else None,
        require_stopped_without_deployment=args.require_stopped_without_deployment,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
