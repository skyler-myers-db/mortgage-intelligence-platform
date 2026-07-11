"""Static architecture guardrails for Module 0 layering."""
from __future__ import annotations

import ast
import json
from datetime import date
from pathlib import Path

from fastapi.routing import APIRoute

from backend.main import app

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
FRONTEND_SRC = ROOT / "frontend" / "src"
FILE_SIZE_ALLOWLIST = ROOT / "tools" / "file_size_allowlist.json"


ROUTE_TEST_MANIFEST: dict[tuple[str, str], str] = {
    ("GET", "/api/admin/health"): "tests/unit/test_health_endpoint.py",
    ("GET", "/api/admin/capabilities"): "tests/unit/test_capabilities.py",
    ("GET", "/api/admin/rules"): "tests/unit/test_admin_rules.py",
    ("GET", "/api/admin/assets/{asset_key}/metadata"): "tests/unit/test_asset_metadata.py",
    ("POST", "/api/admin/force-degraded"): "tests/unit/test_health_endpoint.py",
    ("GET", "/api/admin/operations"): "tests/unit/test_admin_operations.py",
    ("POST", "/api/admin/operations/run"): "tests/unit/test_admin_operations.py",
    ("PUT", "/api/admin/rules"): "tests/unit/test_admin_rules.py",
    ("GET", "/api/admin/settings"): "tests/unit/test_admin_rules.py",
    ("GET", "/api/admin/sources"): "tests/unit/test_admin_rules.py",
    ("GET", "/api/analytics/economics"): "tests/unit/test_api_routes.py",
    ("GET", "/api/analytics/executive"): "tests/unit/test_api_routes.py",
    ("GET", "/api/analytics/geography"): "tests/unit/test_api_routes.py",
    ("GET", "/api/analytics/segments"): "tests/unit/test_api_routes.py",
    ("GET", "/api/analytics/signals"): "tests/unit/test_api_routes.py",
    ("GET", "/api/activation/destinations"): "tests/unit/test_activation_api.py",
    ("GET", "/api/activation/outbox"): "tests/unit/test_activation_api.py",
    ("GET", "/api/activation/summary"): "tests/unit/test_activation_api.py",
    ("POST", "/api/activation/stage"): "tests/unit/test_activation_api.py",
    ("POST", "/api/audit/event"): "tests/unit/test_api_routes.py",
    ("GET", "/api/audit/events"): "tests/unit/test_api_routes.py",
    ("GET", "/api/audit/rollups"): "tests/unit/test_sales_manager_api.py",
    ("GET", "/api/borrowers/search"): "tests/unit/test_borrowers_router.py",
    ("GET", "/api/borrowers/{borrower_id}"): "tests/unit/test_borrowers_router.py",
    ("GET", "/api/borrowers/{borrower_id}/evidence"): "tests/unit/test_borrowers_router.py",
    ("GET", "/api/borrowers/{borrower_id}/proof"): "tests/unit/test_borrowers_router.py",
    ("GET", "/api/borrowers/{borrower_id}/lifecycle"): "tests/unit/test_sales_manager_api.py",
    ("GET", "/api/campaigns"): "tests/unit/test_campaigns_router.py",
    ("GET", "/api/campaigns/{campaign_id}"): "tests/unit/test_campaigns_router.py",
    ("PATCH", "/api/campaigns/{campaign_id}"): "tests/unit/test_campaigns_router.py",
    ("GET", "/api/config/footprint"): "tests/unit/test_state_footprint.py",
    ("GET", "/api/config/options"): "tests/unit/test_api_routes.py",
    ("GET", "/api/data-estate"): "tests/unit/test_data_estate.py",
    ("POST", "/api/genie/actions"): "tests/unit/test_genie_actions_api.py",
    ("POST", "/api/genie/feedback"): "tests/unit/test_genie_feedback_api.py",
    ("POST", "/api/genie/message"): "tests/unit/test_api_routes.py",
    ("POST", "/api/genie/start"): "tests/unit/test_api_routes.py",
    ("GET", "/api/geo/county-rollups"): "tests/unit/test_geo_state_rollups.py",
    ("GET", "/api/geo/state-rollups"): "tests/unit/test_geo_state_rollups.py",
    ("GET", "/api/geo/zip-rollups"): "tests/unit/test_geo_state_rollups.py",
    ("GET", "/api/growth-agent"): "tests/unit/test_growth_agent_api.py",
    ("GET", "/api/growth-agent/monitors"): "tests/unit/test_growth_agent_api.py",
    ("POST", "/api/growth-agent/agent/run"): "tests/unit/test_growth_agent_api.py",
    ("POST", "/api/growth-agent/agent/compose"): "tests/unit/test_growth_agent_api.py",
    ("POST", "/api/growth-agent/custom/run"): "tests/unit/test_growth_agent_api.py",
    ("POST", "/api/growth-agent/monitors/run-due"): "tests/unit/test_growth_agent_api.py",
    ("POST", "/api/growth-agent/monitors/run-due-all"): "tests/unit/test_growth_agent_api.py",
    ("POST", "/api/growth-agent/monitors/{monitor_id}/notification-drafts"): "tests/unit/test_growth_agent_api.py",
    ("POST", "/api/growth-agent/monitors/{monitor_id}/run"): "tests/unit/test_growth_agent_api.py",
    ("POST", "/api/growth-agent/workflows/{workflow_id}/run"): "tests/unit/test_growth_agent_api.py",
    ("GET", "/api/health"): "tests/unit/test_health_endpoint.py",
    ("GET", "/api/home/summary"): "tests/unit/test_home_api.py",
    ("GET", "/api/leads"): "tests/unit/test_api_routes.py",
    ("POST", "/api/leads/{borrower_id}/assign"): "tests/unit/test_sales_manager_api.py",
    ("GET", "/api/leads/{borrower_id}/assignment"): "tests/unit/test_sales_manager_api.py",
    ("POST", "/api/leads/{borrower_id}/disposition"): "tests/unit/test_sales_manager_api.py",
    ("POST", "/api/leads/{borrower_id}/outcome"): "tests/unit/test_sales_manager_api.py",
    ("POST", "/api/lookup/property-loan"): "tests/unit/test_lookup_router.py",
    ("POST", "/api/offers/recommend"): "tests/unit/test_offers_router.py",
    ("POST", "/api/outreach/approve"): "tests/unit/test_api_routes.py",
    ("POST", "/api/outreach/draft"): "tests/unit/test_api_routes.py",
    ("POST", "/api/outreach/reject"): "tests/unit/test_outreach_reject.py",
    ("GET", "/api/portfolio"): "tests/unit/test_portfolio_repo_timezone.py",
    ("POST", "/api/portfolio/create"): "tests/unit/test_api_routes.py",
    ("POST", "/api/portfolio/preview"): "tests/unit/test_api_routes.py",
    ("GET", "/api/portfolio/{portfolio_id}"): "tests/unit/test_api_routes.py",
    ("PATCH", "/api/portfolio/{portfolio_id}"): "tests/unit/test_api_routes.py",
    ("GET", "/api/sales/aging"): "tests/unit/test_sales_manager_api.py",
    ("GET", "/api/sales/conversion"): "tests/unit/test_sales_manager_api.py",
    ("POST", "/api/sales/distribute"): "tests/unit/test_sales_manager_api.py",
    ("GET", "/api/sales/outcomes/summary"): "tests/unit/test_sales_manager_api.py",
    ("GET", "/api/sales/standup"): "tests/unit/test_sales_manager_api.py",
    ("GET", "/api/sales/team"): "tests/unit/test_sales_manager_api.py",
    ("GET", "/api/segments"): "tests/unit/test_api_routes.py",
    ("POST", "/api/telemetry/rum"): "tests/unit/test_rum_telemetry.py",
    ("GET", "/api/workspace"): "tests/unit/test_workspace_api.py",
    ("DELETE", "/api/workspace/drafts/{borrower_id}"): "tests/unit/test_workspace_api.py",
    ("PUT", "/api/workspace/drafts/{borrower_id}"): "tests/unit/test_workspace_api.py",
    ("DELETE", "/api/workspace/leads/{borrower_id}"): "tests/unit/test_workspace_api.py",
    ("PUT", "/api/workspace/leads/{borrower_id}"): "tests/unit/test_workspace_api.py",
}


def _py_files(path: Path) -> list[Path]:
    return sorted(p for p in path.rglob("*.py") if "__pycache__" not in p.parts)


def _registered_api_routes() -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for route in app.routes:
        if (
            not isinstance(route, APIRoute)
            or not route.include_in_schema
            or not route.path.startswith("/api/")
        ):
            continue
        methods = (route.methods or set()) - {"HEAD", "OPTIONS"}
        routes.update((method, route.path) for method in methods)
    return routes


def _canonical_manifest_key(route: tuple[str, str]) -> tuple[str, str] | None:
    method, path = route
    if path.startswith("/api/v1/"):
        return method, "/api" + path[len("/api/v1") :]
    return None


def _compat_manifest_key(route: tuple[str, str]) -> tuple[str, str] | None:
    method, path = route
    if path.startswith("/api/") and not path.startswith("/api/v1/"):
        return method, path
    return None


def _route_literal_candidates(path_template: str) -> set[str]:
    replacements = {
        "{borrower_id}": "B-48291",
        "{campaign_id}": "11111111-1111-4111-8111-111111111111",
        "{portfolio_id}": "11111111-1111-4111-8111-111111111111",
        "{asset_key}": "lead_population",
        "{workflow_id}": "daily_refi_brief",
    }
    concrete = path_template
    for placeholder, value in replacements.items():
        concrete = concrete.replace(placeholder, value)
    return {path_template, concrete}


def test_routers_do_not_import_other_routers() -> None:
    violations: list[str] = []
    for path in _py_files(BACKEND / "api"):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "from backend.api." in line or "import backend.api." in line:
                violations.append(f"{path.relative_to(ROOT)}: {line.strip()}")
    assert violations == []


def test_schemas_do_not_import_runtime_services() -> None:
    violations: list[str] = []
    for path in _py_files(BACKEND / "schemas"):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "backend.services." in stripped:
                violations.append(f"{path.relative_to(ROOT)}: {line.strip()}")
    assert violations == []


def test_runtime_modules_use_structured_warning_events() -> None:
    violations: list[str] = []
    for root in ("api", "services", "config"):
        for path in _py_files(BACKEND / root):
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if any(
                    token in stripped
                    for token in (
                        "log.warning(",
                        "log.error(",
                        "log.exception(",
                        ".warning(",
                        ".error(",
                        ".exception(",
                    )
                ):
                    violations.append(f"{path.relative_to(ROOT)}: {stripped}")
    main_text = (BACKEND / "main.py").read_text(encoding="utf-8")
    for line in main_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if any(
            token in stripped
            for token in (
                "log.warning(",
                "log.error(",
                "log.exception(",
                ".warning(",
                ".error(",
                ".exception(",
            )
        ):
            violations.append(f"backend/main.py: {stripped}")
    assert violations == []


def test_backend_python_files_stay_below_monolith_threshold() -> None:
    allowlist = json.loads(FILE_SIZE_ALLOWLIST.read_text(encoding="utf-8")).get("expires", {})
    today = date.today()
    oversize: list[str] = []
    for path in _py_files(BACKEND):
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        if line_count > 1000:
            rel = path.relative_to(ROOT).as_posix()
            expiry = allowlist.get(rel)
            if isinstance(expiry, str) and date.fromisoformat(expiry) >= today:
                continue
            oversize.append(f"{path.relative_to(ROOT)}: {line_count}")
    assert oversize == []


def test_in_memory_reference_stores_stay_in_test_fixtures() -> None:
    violations: list[str] = []
    for root in ("api", "services"):
        for path in _py_files(BACKEND / root):
            text = path.read_text(encoding="utf-8")
            if "class InMemory" in text or "InMemoryAuditStore" in text or "InMemoryWorkspaceStore" in text:
                violations.append(str(path.relative_to(ROOT)))
    assert violations == []


def test_production_runtime_has_no_test_import_or_mock_mode() -> None:
    violations: list[str] = []
    banned_backend_tokens = (
        "from tests.",
        "import tests.",
        "MIP_MOCK_MODE",
        "USE_MOCKS",
        "mock_fallback",
        "use_mocks=True",
        "use_mocks = True",
    )
    for path in _py_files(BACKEND):
        text = path.read_text(encoding="utf-8")
        for token in banned_backend_tokens:
            if token in text:
                violations.append(f"{path.relative_to(ROOT)}: {token}")

    frontend_files = [
        p
        for p in FRONTEND_SRC.rglob("*")
        if p.suffix in {".ts", ".tsx"} and ".test." not in p.name
    ]
    for path in frontend_files:
        text = path.read_text(encoding="utf-8")
        if "/mocks/" in text or "mockServiceWorker" in text:
            violations.append(f"{path.relative_to(ROOT)}: frontend mock import")

    assert violations == []


def test_api_route_smoke_contract_stays_registered() -> None:
    smoke_path = ROOT / "tests" / "unit" / "test_api_routes.py"
    assert smoke_path.exists(), "TestClient route-smoke coverage must stay in tests/unit/test_api_routes.py"

    tree = ast.parse(smoke_path.read_text(encoding="utf-8"), filename=str(smoke_path))
    route_literals: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if value.startswith("/api/"):
                route_literals.add(value.split("?", 1)[0])

    required_routes = {
        "/api/health",
        "/api/config/options",
        "/api/analytics/executive",
        "/api/portfolio/preview",
        "/api/portfolio/create",
        "/api/segments",
        "/api/leads",
        "/api/geo/state-rollups",
        "/api/borrowers/B-48291",
        "/api/borrowers/B-48291/evidence",
        "/api/offers/recommend",
        "/api/outreach/draft",
        "/api/outreach/approve",
        "/api/genie/start",
        "/api/genie/message",
        "/api/audit/events",
        "/api/audit/event",
        "/api/admin/rules",
    }

    assert required_routes <= route_literals
    assert len(route_literals) >= len(required_routes)


def test_registered_api_routes_have_explicit_test_manifest() -> None:
    registered = _registered_api_routes()
    manifested = set(ROUTE_TEST_MANIFEST)
    expected_canonical = manifested - {("GET", "/api/{full_path:path}")}
    canonical = {
        key for route in registered if (key := _canonical_manifest_key(route)) is not None
    }
    compat = {
        key for route in registered if (key := _compat_manifest_key(route)) is not None
    }

    missing_canonical = sorted(expected_canonical - canonical)
    stale_canonical = sorted(canonical - expected_canonical)
    missing_compat = sorted(manifested - compat)
    stale_compat = sorted(compat - manifested)

    assert missing_canonical == []
    assert stale_canonical == []
    assert missing_compat == []
    assert stale_compat == []

    missing_files = sorted(
        {
            test_path
            for test_path in ROUTE_TEST_MANIFEST.values()
            if not (ROOT / test_path).exists()
        }
    )
    assert missing_files == []

    missing_route_literals: list[str] = []
    for route, test_path in sorted(ROUTE_TEST_MANIFEST.items()):
        text = (ROOT / test_path).read_text(encoding="utf-8")
        candidates = _route_literal_candidates(route[1])
        if not any(candidate in text for candidate in candidates):
            missing_route_literals.append(f"{route[0]} {route[1]} -> {test_path}")
    assert missing_route_literals == []


def test_unversioned_api_routes_are_marked_deprecated_aliases() -> None:
    violations: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.include_in_schema:
            continue
        if not route.path.startswith("/api/") or route.path.startswith("/api/v1/"):
            continue
        if route.deprecated is not True:
            methods = ",".join(sorted((route.methods or set()) - {"HEAD", "OPTIONS"}))
            violations.append(f"{methods} {route.path}")
    assert violations == []
