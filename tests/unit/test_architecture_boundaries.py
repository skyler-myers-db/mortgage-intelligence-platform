"""Static architecture guardrails for Module 0 layering."""
from __future__ import annotations

import ast
from pathlib import Path

from fastapi.routing import APIRoute

from backend.main import app

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
FRONTEND_SRC = ROOT / "frontend" / "src"


ROUTE_TEST_MANIFEST: dict[tuple[str, str], str] = {
    ("GET", "/api/admin/health"): "tests/unit/test_health_endpoint.py",
    ("GET", "/api/admin/rules"): "tests/unit/test_admin_rules.py",
    ("PUT", "/api/admin/rules"): "tests/unit/test_admin_rules.py",
    ("GET", "/api/admin/settings"): "tests/unit/test_admin_rules.py",
    ("GET", "/api/admin/sources"): "tests/unit/test_admin_rules.py",
    ("POST", "/api/audit/event"): "tests/unit/test_api_routes.py",
    ("GET", "/api/audit/events"): "tests/unit/test_api_routes.py",
    ("GET", "/api/audit/rollups"): "tests/unit/test_sales_manager_api.py",
    ("GET", "/api/borrowers/search"): "tests/unit/test_borrowers_router.py",
    ("GET", "/api/borrowers/{borrower_id}"): "tests/unit/test_borrowers_router.py",
    ("GET", "/api/borrowers/{borrower_id}/evidence"): "tests/unit/test_borrowers_router.py",
    ("GET", "/api/borrowers/{borrower_id}/lifecycle"): "tests/unit/test_sales_manager_api.py",
    ("GET", "/api/campaigns"): "tests/unit/test_campaigns_router.py",
    ("GET", "/api/campaigns/{campaign_id}"): "tests/unit/test_campaigns_router.py",
    ("PATCH", "/api/campaigns/{campaign_id}"): "tests/unit/test_campaigns_router.py",
    ("GET", "/api/config/footprint"): "tests/unit/test_state_footprint.py",
    ("GET", "/api/config/options"): "tests/unit/test_api_routes.py",
    ("GET", "/api/data-estate"): "tests/unit/test_data_estate.py",
    ("POST", "/api/genie/actions"): "tests/unit/test_genie_actions_api.py",
    ("POST", "/api/genie/message"): "tests/unit/test_api_routes.py",
    ("POST", "/api/genie/start"): "tests/unit/test_api_routes.py",
    ("GET", "/api/geo/county-rollups"): "tests/unit/test_geo_state_rollups.py",
    ("GET", "/api/geo/state-rollups"): "tests/unit/test_geo_state_rollups.py",
    ("GET", "/api/geo/zip-rollups"): "tests/unit/test_geo_state_rollups.py",
    ("GET", "/api/health"): "tests/unit/test_health_endpoint.py",
    ("GET", "/api/leads"): "tests/unit/test_api_routes.py",
    ("POST", "/api/leads/{borrower_id}/assign"): "tests/unit/test_sales_manager_api.py",
    ("GET", "/api/leads/{borrower_id}/assignment"): "tests/unit/test_sales_manager_api.py",
    ("POST", "/api/leads/{borrower_id}/disposition"): "tests/unit/test_sales_manager_api.py",
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
    ("GET", "/api/sales/standup"): "tests/unit/test_sales_manager_api.py",
    ("GET", "/api/sales/team"): "tests/unit/test_sales_manager_api.py",
    ("GET", "/api/segments"): "tests/unit/test_api_routes.py",
    ("POST", "/api/telemetry/rum"): "tests/unit/test_rum_telemetry.py",
    ("GET", "/api/workspace"): "tests/unit/test_workspace_api.py",
    ("DELETE", "/api/workspace/drafts/{borrower_id}"): "tests/unit/test_workspace_api.py",
    ("PUT", "/api/workspace/drafts/{borrower_id}"): "tests/unit/test_workspace_api.py",
    ("DELETE", "/api/workspace/leads/{borrower_id}"): "tests/unit/test_workspace_api.py",
    ("PUT", "/api/workspace/leads/{borrower_id}"): "tests/unit/test_workspace_api.py",
    ("GET", "/api/{full_path:path}"): "tests/unit/test_api_routes.py",
}


def _py_files(path: Path) -> list[Path]:
    return sorted(p for p in path.rglob("*.py") if "__pycache__" not in p.parts)


def _registered_api_routes() -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/"):
            continue
        methods = (route.methods or set()) - {"HEAD", "OPTIONS"}
        routes.update((method, route.path) for method in methods)
    return routes


def _route_literal_candidates(path_template: str) -> set[str]:
    replacements = {
        "{borrower_id}": "B-48291",
        "{campaign_id}": "11111111-1111-4111-8111-111111111111",
        "{portfolio_id}": "11111111-1111-4111-8111-111111111111",
        "{full_path:path}": "not-a-real-route",
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
    oversize: list[str] = []
    for path in _py_files(BACKEND):
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        if line_count > 1000:
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

    missing = sorted(registered - manifested)
    stale = sorted(manifested - registered)

    assert missing == []
    assert stale == []

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
