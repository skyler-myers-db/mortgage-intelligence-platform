"""The cold-cache Locust profile and its pure analysis (audit delivery-09).

``cold_cache_analysis`` is pure (no Locust import) and decides the verdict
that gates W5d's single-flight slot release. ``locust_cold_cache`` is loaded
here against a stub ``locust`` module (Locust is operator-only and not in the
repo venv) and scanned: audit-free reads only, /leads opt-in, no
/borrowers/{id}, templated names, and the bearer never printed or logged.
"""
from __future__ import annotations

import ast
import importlib
import sys
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tools.load_test.cold_cache_analysis import (
    STALL_P95_MS,
    Sample,
    classify_429,
    percentile,
    summarize,
    verdict,
)

ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "tools" / "load_test" / "locust_cold_cache.py"


# -- the pure analysis --------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"reason": "dependency_saturated", "dependency": "warehouse", "scope": "warehouse-read"}, "dependency_saturated:warehouse"),
        ({"reason": "dependency_saturated", "dependency": "lakebase"}, "dependency_saturated:lakebase"),
        ({"reason": "rate_limited", "scope": "warehouse-read", "dependency": "warehouse"}, "rate_limited:warehouse-read"),
        ({"reason": "dependency_saturated"}, "dependency_saturated:unknown"),
        ({"reason": "something_else"}, "other"),
        (None, "other"),
        ("not json", "other"),
    ],
)
def test_classify_429(body: object, expected: str) -> None:
    assert classify_429(body) == expected


def test_percentiles_are_nearest_rank() -> None:
    values = [float(v) for v in range(1, 101)]
    assert percentile(values, 50) == 50.0
    assert percentile(values, 95) == 95.0
    assert percentile(values, 99) == 99.0
    assert percentile([], 95) is None


def _samples() -> list[Sample]:
    rows = [Sample("GET /api/v1/health", float(i), 100.0 + i, 200) for i in range(10)]
    rows += [Sample("GET /api/v1/session", 1.0, 80.0, 200)]
    rows += [Sample("GET /api/v1/segments", 2.0, 2500.0, 200), Sample("GET /api/v1/segments", 90.0, 300.0, 200)]
    rows += [
        Sample("POST /api/v1/portfolio/preview", 3.0, 50.0, 429, {"reason": "rate_limited", "scope": "warehouse-read"}),
        Sample("GET /api/v1/home/summary", 95.0, 40.0, 429, {"reason": "dependency_saturated", "dependency": "warehouse"}),
        Sample("GET /api/v1/geo/state-rollups", 4.0, 30000.0, 503, {"reason": "retries_exhausted"}),
        Sample("GET /api/v1/geo/state-rollups", 5.0, 900.0, 503, {"reason": "warming_up"}),
    ]
    return rows


def test_summary_reports_both_windows_and_counts_by_class() -> None:
    summary = summarize(_samples(), cold_window_s=60)

    assert summary["requests"] == {"cold": 15, "all": 17}
    segments = summary["routes"]["GET /api/v1/segments"]
    assert segments["cold"]["count"] == 1 and segments["cold"]["p95_ms"] == 2500.0
    assert segments["all"]["count"] == 2 and segments["all"]["max_ms"] == 2500.0
    assert summary["status_429"]["cold"] == {"rate_limited:warehouse-read": 1}
    assert summary["status_429"]["all"] == {
        "dependency_saturated:warehouse": 1,
        "rate_limited:warehouse-read": 1,
    }
    assert summary["status_503"]["cold"] == {"retries_exhausted": 1, "warming_up": 1}
    assert summary["stall_p95_ms_cold"] == {"GET /api/v1/health": 109.0, "GET /api/v1/session": 80.0}


def test_rate_limited_429s_are_reported_but_never_counted() -> None:
    summary = summarize(_samples(), cold_window_s=60)
    assert summary["verdict"] == "not_reproduced", "the only saturation lands after the cold window"


def test_a_cold_dependency_saturated_429_reproduces() -> None:
    rows = _samples() + [
        Sample("GET /api/v1/segments", 10.0, 5.0, 429, {"reason": "dependency_saturated", "dependency": "lakebase"})
    ]
    assert summarize(rows)["verdict"] == "reproduced"


@pytest.mark.parametrize("route", ["GET /api/v1/health", "GET /api/v1/session"])
def test_a_cold_shell_stall_reproduces(route: str) -> None:
    rows = [Sample(route, 1.0, STALL_P95_MS + 1, 200) for _ in range(20)]
    assert summarize(rows)["verdict"] == "reproduced"
    late = [Sample(route, 61.0, STALL_P95_MS + 1, 200) for _ in range(20)]
    assert summarize(late)["verdict"] == "not_reproduced", "a stall outside the cold window"


def test_verdict_reads_only_the_summary() -> None:
    assert verdict({"status_429": {"cold": {"dependency_saturated:genie": 1}}}) == "reproduced"
    assert verdict({"status_429": {"cold": {"rate_limited:genie": 50}}, "stall_p95_ms_cold": {}}) == "not_reproduced"


def test_the_analysis_module_never_imports_locust() -> None:
    source = (ROOT / "tools" / "load_test" / "cold_cache_analysis.py").read_text(encoding="utf-8")
    assert "locust" not in "\n".join(
        line for line in source.splitlines() if line.startswith(("import", "from"))
    )


# -- the profile, loaded against a stub locust --------------------------------


def _stub_locust() -> types.ModuleType:
    module = types.ModuleType("locust")

    class _Hook:
        def add_listener(self, fn: Any) -> Any:
            return fn

    class _Events:
        test_start = _Hook()
        request = _Hook()
        quitting = _Hook()

    class HttpUser:
        tasks: Any = None

    module.HttpUser = HttpUser  # type: ignore[attr-defined]
    module.between = lambda low, high: (low, high)  # type: ignore[attr-defined]
    module.events = _Events()  # type: ignore[attr-defined]
    return module


@pytest.fixture
def load_profile(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    monkeypatch.setitem(sys.modules, "locust", _stub_locust())

    def load(**env: str) -> Any:
        for key in ("MIP_COLD_CACHE_INCLUDE_AUDITED", "MIP_API_PREFIX"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        sys.modules.pop("tools.load_test.locust_cold_cache", None)
        return importlib.import_module("tools.load_test.locust_cold_cache")

    yield load
    sys.modules.pop("tools.load_test.locust_cold_cache", None)


def _task_names(module: Any) -> set[str]:
    return {task.__name__ for task in module.ColdCacheUser.tasks}


def test_the_default_profile_is_audit_free(load_profile: Any) -> None:
    module = load_profile()
    assert _task_names(module) == {
        "get_health",
        "get_session",
        "get_home_summary",
        "get_segments",
        "get_geo_state_rollups",
        "get_geo_rate_sensitivity",
        "get_config_options",
        "get_analytics_rate_window",
        "post_portfolio_preview",
        "post_genie_start",
    }
    assert [f"{m} {p}" for m, p in module.FIRST_LOAD if m == "POST"] == [
        "POST portfolio/preview",
        "POST genie/start",
    ]
    assert module.ColdCacheUser.wait_time == (1, 3)


def test_leads_are_opt_in_because_each_page_writes_view_leads(load_profile: Any) -> None:
    module = load_profile(MIP_COLD_CACHE_INCLUDE_AUDITED="1")
    assert "get_leads" in _task_names(module)


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, Any]] = []
        self.headers: dict[str, str] = {}

    def get(self, url: str, *, name: str) -> None:
        self.calls.append(("GET", url, name, None))

    def post(self, url: str, *, json: Any, name: str) -> None:
        self.calls.append(("POST", url, name, json))


def test_the_first_load_burst_is_named_by_route_template(load_profile: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_profile()
    monkeypatch.setenv("MIP_BEARER_TOKEN", "dapi-not-a-real-token")
    user = module.ColdCacheUser.__new__(module.ColdCacheUser)
    user.client = _FakeClient()
    user.on_start()

    assert user.client.headers == {"Authorization": "Bearer dapi-not-a-real-token"}
    assert [(m, url) for m, url, _n, _b in user.client.calls] == [
        (m, f"/api/v1/{p}") for m, p in module.FIRST_LOAD
    ]
    assert all(name == url for _m, url, name, _b in user.client.calls)
    assert all(body == {} for m, _u, _n, body in user.client.calls if m == "POST")
    with pytest.raises(ValueError):
        user._call("POST", "outreach/approve")


def test_the_profile_source_sends_only_reads_and_never_shows_the_token() -> None:
    source = PROFILE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    client_methods = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "client"
    }
    assert client_methods <= {"get", "post"}
    docstrings = {ast.get_docstring(tree, clean=False)} | {
        ast.get_docstring(node, clean=False)
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.ClassDef)
    }
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value not in docstrings
    ]
    assert not [text for text in literals if "borrowers" in text], "never /borrowers/{id}"
    assert not [text for text in literals if "B-" in text and any(c.isdigit() for c in text)]
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
            printed = ast.unparse(node)
            assert "bearer" not in printed.lower() and "MIP_BEARER_TOKEN" not in printed
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {
            "info", "warning", "error", "debug", "exception",
        }:
            assert "bearer" not in ast.unparse(node).lower()
    names = [
        keyword.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "client"
        for keyword in node.keywords
        if keyword.arg == "name"
    ]
    assert names and all(isinstance(value, ast.Name) and value.id == "url" for value in names)
