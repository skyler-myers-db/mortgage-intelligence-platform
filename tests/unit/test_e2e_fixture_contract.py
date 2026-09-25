"""Every e2e fixture body validates against the real API response model.

2026-09-21 UI/UX audit, finding ``quality-09`` (step 3). The credential-free
fixture harness (frontend/tests/e2e/fixture) answers every API call from a
typed registry. TypeScript checks those payloads against the frontend's own
hand-written types, which can drift from the backend; this test checks them
against the backend itself, so a fixture UI cannot pass while the real wire
contract has moved.

``tools/export_e2e_fixtures.mjs`` (bare Node, no node_modules) exports every
body the harness serves: each defaultFixtures() handler, invoked with
contractSamples.ts's PARAM_SAMPLES / BODY_SAMPLES / QUERY_SAMPLES, plus every
exported ``contractSamples()`` (contractSamples.ts and any data/*.ts module).
Each sample is then resolved exactly the way Starlette dispatches it: the
path is version-normalized to ``/api/v1``, and the FIRST canonical APIRoute in
``backend.main.app.routes`` whose ``matches()`` is ``Match.FULL`` for the
method and path owns it (so ``/borrowers/search`` beats ``/borrowers/{id}``).
A 2xx body must validate against that route's model in JSON mode, which runs
the model validators (score-band canon, governed identifiers, name-shaped
text, vocabularies). Non-2xx samples are counted and skipped. Every body must
also stay synthetic: masked borrower ids, ``.example`` email domains and the
Summit Mortgage sample lender.

The exporter needs Node >= 22.18. Where Node is missing or older the module
SKIPS, unless ``MIP_REQUIRE_FIXTURE_CONTRACT=1`` (set in CI's backend job),
in which case it FAILS.
"""
from __future__ import annotations

import functools
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import APIRouter
from fastapi.routing import APIRoute
from pydantic import BaseModel, TypeAdapter, ValidationError
from starlette.routing import Match

from backend.main import API_VERSION, app

ROOT = Path(__file__).resolve().parents[2]
EXPORTER = ROOT / "tools" / "export_e2e_fixtures.mjs"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
REQUIRE_FLAG = "MIP_REQUIRE_FIXTURE_CONTRACT"
MIN_NODE = (22, 18)
CANONICAL_PREFIX = f"/api/{API_VERSION}/"

MASKED_BORROWER_ID = re.compile(r"^B-[0-9A-Z]{13}$")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
SAMPLE_LENDER = "Summit Mortgage"

# Samples whose drift is known and owned elsewhere, keyed by exported source.
# SHRINK-ONLY: an entry must still fail validation (test below), so a fix
# retires its entry in the same change. Add one only when fixing the drift
# would change text asserted by a spec another lane owns and no equally valid
# value keeps that text. Shape: {"finding", "owner", "recorded", "why"}.
KNOWN_DRIFT: dict[str, dict[str, str]] = {}


class ContractError(LookupError):
    """A sample the contract cannot place on a route or a model."""


def _canonical_path(path: str) -> str:
    """``/api/x`` and ``/api/v2/x`` both dispatch as ``/api/<API_VERSION>/x``."""
    return re.sub(r"^/api(?:/v\d+)?(?=/|$)", f"/api/{API_VERSION}", path)


def _canonical_routes() -> list[APIRoute]:
    return [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith(CANONICAL_PREFIX)
    ]


def resolve_route(method: str, path: str, routes: list[APIRoute] | None = None) -> APIRoute:
    """The first canonical APIRoute that FULLY matches, in dispatch order."""
    canonical = _canonical_path(path)
    scope = {"type": "http", "method": method, "path": canonical, "root_path": ""}
    for route in routes if routes is not None else _canonical_routes():
        match, _child = route.matches(scope)
        if match == Match.FULL:
            return route
    raise ContractError(f"no API route answers {method} {canonical}")


def response_model_for(route: APIRoute, status: int) -> Any:
    """The model a 2xx ``status`` must satisfy on ``route``.

    A status declared in ``responses={status: {"model": ...}}`` (for example a
    202 job receipt) uses that model; otherwise the status must be the route's
    declared ``status_code`` (200 when unset) and the route's response_model.
    """
    declared = route.responses.get(status) or route.responses.get(str(status)) or {}
    if isinstance(declared, dict) and declared.get("model") is not None:
        return declared["model"]
    expected = route.status_code or 200
    if status != expected:
        raise ContractError(
            f"status {status} is not declared by {route.path} (declared status {expected})"
        )
    if route.response_model is None:
        raise ContractError(f"{route.path} declares no response model for {status}")
    return route.response_model


@functools.cache
def _adapter(model: Any) -> TypeAdapter[Any]:
    return TypeAdapter(model)


def synthetic_problems(node: Any, where: str, key: str | None = None) -> list[str]:
    """Fixture data stays synthetic: masked ids, .example emails, the sample lender."""
    problems: list[str] = []
    if isinstance(node, dict):
        for child_key, child in node.items():
            problems.extend(synthetic_problems(child, where, str(child_key)))
        return problems
    if isinstance(node, list):
        for child in node:
            problems.extend(synthetic_problems(child, where, key))
        return problems
    if not isinstance(node, str):
        return problems
    is_borrower_id = key is not None and (key.endswith("borrower_id") or key == "borrower_ids")
    if is_borrower_id and not MASKED_BORROWER_ID.match(node):
        problems.append(f"{where}: {key} {node!r} is not a masked B-[0-9A-Z]{{13}} id")
    if key == "lender_name" and node != SAMPLE_LENDER:
        problems.append(f"{where}: lender_name {node!r} is not the sample lender {SAMPLE_LENDER!r}")
    for email in EMAIL.findall(node):
        if not email.lower().endswith(".example"):
            problems.append(f"{where}: email {email!r} is not on a reserved .example domain")
    return problems


def contract_problems(sample: dict[str, Any], routes: list[APIRoute] | None = None) -> list[str]:
    """Why ``sample`` breaks the contract; empty when it holds (or is non-2xx)."""
    method, path, status = sample["method"], sample["path"], int(sample["status"])
    where = f"{sample['source']} [{method} {path}{'?' + sample['query'] if sample.get('query') else ''} -> {status}]"
    problems = synthetic_problems(sample["body"], where)
    try:
        route = resolve_route(method, path, routes)
    except ContractError as exc:
        return [*problems, f"{where}: {exc}"]
    if not 200 <= status < 300:
        return problems
    try:
        model = response_model_for(route, status)
    except ContractError as exc:
        return [*problems, f"{where}: {exc}"]
    try:
        _adapter(model).validate_json(json.dumps(sample["body"]))
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
            for error in exc.errors()[:6]
        )
        problems.append(f"{where}: fails {getattr(model, '__name__', model)}: {details}")
    return problems


def _node_version(node: str) -> tuple[int, int] | None:
    completed = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=30, check=False)
    match = re.match(r"v(\d+)\.(\d+)", completed.stdout.strip())
    return (int(match.group(1)), int(match.group(2))) if match else None


def _node_or_skip() -> str:
    required = os.environ.get(REQUIRE_FLAG) == "1"
    node = shutil.which("node")
    version = _node_version(node) if node else None
    if node and version and version >= MIN_NODE:
        return node
    reason = (
        f"Node >= {MIN_NODE[0]}.{MIN_NODE[1]} is required to export the e2e fixtures "
        f"(found {'none' if not node else version})"
    )
    if required:
        pytest.fail(f"{reason}; {REQUIRE_FLAG}=1 makes this a failure, not a skip")
    pytest.skip(reason)


@pytest.fixture(scope="module")
def node() -> str:
    return _node_or_skip()


@pytest.fixture(scope="module")
def samples(node: str, tmp_path_factory: pytest.TempPathFactory) -> list[dict[str, Any]]:
    out = tmp_path_factory.mktemp("fixture-contract") / "samples.json"
    completed = subprocess.run(
        [node, str(EXPORTER), "--out", str(out)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, f"exporter failed:\n{completed.stderr}"
    exported = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(exported, list) and exported, "the exporter wrote no samples"
    return exported


def test_every_fixture_body_matches_the_backend_contract(samples: list[dict[str, Any]]) -> None:
    problems = [
        problem
        for sample in samples
        if sample["source"] not in KNOWN_DRIFT
        for problem in contract_problems(sample)
    ]
    assert problems == [], "fixture bodies drifted from the backend contract:\n" + "\n".join(problems)


def test_every_default_fixture_yields_a_validated_2xx_sample(samples: list[dict[str, Any]]) -> None:
    registered = {
        (sample["method"], sample["pattern"]) for sample in samples if sample["source"].startswith("registry:")
    }
    validated = {
        (sample["method"], sample["pattern"])
        for sample in samples
        if 200 <= int(sample["status"]) < 300 and not contract_problems(sample)
    }
    assert registered, "no defaultFixtures() entries were exported"
    assert sorted(registered - validated) == []


def test_non_2xx_samples_are_counted_not_validated(samples: list[dict[str, Any]]) -> None:
    validated = [sample for sample in samples if 200 <= int(sample["status"]) < 300]
    skipped = [sample for sample in samples if not 200 <= int(sample["status"]) < 300]
    assert len(validated) + len(skipped) == len(samples)
    assert len(validated) > len(skipped)


def test_known_drift_is_shrink_only(samples: list[dict[str, Any]]) -> None:
    by_source = {sample["source"]: sample for sample in samples}
    for source, entry in KNOWN_DRIFT.items():
        assert {"finding", "owner", "recorded", "why"} <= entry.keys(), source
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry["recorded"]), source
        assert source in by_source, f"KNOWN_DRIFT names {source!r}, which the exporter no longer emits"
        assert contract_problems(by_source[source]), f"KNOWN_DRIFT {source!r} no longer reproduces: remove it"


# --- Non-vacuity: the validator rejects each kind of break -------------------


def _valid_session_sample() -> dict[str, Any]:
    return {
        "source": "non-vacuity",
        "method": "GET",
        "pattern": "/api/genie/sessions",
        "path": "/api/genie/sessions",
        "query": "",
        "status": 200,
        "body": {
            "sessions": [
                {"conversation_id": "c-1", "title": "t", "last_activity_at": None, "turn_count": 1}
            ]
        },
    }


def test_the_validator_accepts_a_valid_sample() -> None:
    assert contract_problems(_valid_session_sample()) == []


def test_the_validator_rejects_a_missing_required_field() -> None:
    sample = _valid_session_sample()
    del sample["body"]["sessions"][0]["conversation_id"]
    assert any("conversation_id" in problem for problem in contract_problems(sample))


def test_the_validator_rejects_an_unknown_path() -> None:
    sample = {**_valid_session_sample(), "path": "/api/genie/no-such-endpoint"}
    assert contract_problems(sample) == [
        f"non-vacuity [GET /api/genie/no-such-endpoint -> 200]: no API route answers GET /api/{API_VERSION}/genie/no-such-endpoint"
    ]


def test_the_validator_rejects_a_wrong_method() -> None:
    sample = {**_valid_session_sample(), "method": "DELETE"}
    assert any("no API route answers DELETE" in problem for problem in contract_problems(sample))


def test_the_validator_rejects_an_undeclared_2xx_status() -> None:
    sample = {**_valid_session_sample(), "status": 201}
    assert any("status 201 is not declared" in problem for problem in contract_problems(sample))


def test_the_validator_rejects_an_unmasked_borrower_id() -> None:
    sample = {
        **_valid_session_sample(),
        "body": {"sessions": [], "borrower_id": "borrower-42"},
    }
    assert any("is not a masked" in problem for problem in contract_problems(sample))


def test_the_synthetic_guard_rejects_real_emails_and_other_lenders() -> None:
    problems = synthetic_problems(
        {"owner": "Reach jane@realbank.com", "lender_name": "Another Bank", "ok": "a@summit.example"},
        "non-vacuity",
    )
    assert problems == [
        "non-vacuity: email 'jane@realbank.com' is not on a reserved .example domain",
        "non-vacuity: lender_name 'Another Bank' is not the sample lender 'Summit Mortgage'",
    ]


def test_dispatch_order_resolves_the_static_segment_first() -> None:
    assert resolve_route("GET", "/api/borrowers/search").path == f"/api/{API_VERSION}/borrowers/search"
    assert resolve_route("GET", "/api/borrowers/B-0000000000000").path == f"/api/{API_VERSION}/borrowers/{{borrower_id}}"


class _JobReceipt(BaseModel):
    job_id: str


class _Answer(BaseModel):
    answer: str


def _declared_202_route() -> APIRoute:
    router = APIRouter()

    @router.post(
        f"/api/{API_VERSION}/non-vacuity/jobs",
        response_model=_Answer,
        responses={202: {"model": _JobReceipt}},
    )
    def _jobs() -> _Answer:  # pragma: no cover - never served
        return _Answer(answer="")

    route = router.routes[0]
    assert isinstance(route, APIRoute)
    return route


def test_a_non_default_2xx_status_validates_against_its_declared_model() -> None:
    route = _declared_202_route()
    base = {"source": "non-vacuity", "method": "POST", "pattern": "/api/non-vacuity/jobs", "path": "/api/non-vacuity/jobs", "query": ""}

    assert contract_problems({**base, "status": 202, "body": {"job_id": "j-1"}}, [route]) == []
    assert contract_problems({**base, "status": 200, "body": {"answer": "ok"}}, [route]) == []
    wrong_model = contract_problems({**base, "status": 202, "body": {"answer": "ok"}}, [route])
    assert any("fails _JobReceipt" in problem and "job_id" in problem for problem in wrong_model)
    undeclared = contract_problems({**base, "status": 203, "body": {"job_id": "j-1"}}, [route])
    assert any("status 203 is not declared" in problem for problem in undeclared)


def test_a_route_without_a_response_model_fails_closed() -> None:
    router = APIRouter()

    @router.get(f"/api/{API_VERSION}/non-vacuity/untyped", response_model=None)
    def _untyped() -> dict[str, str]:  # pragma: no cover - never served
        return {}

    route = router.routes[0]
    assert isinstance(route, APIRoute)
    sample = {"source": "non-vacuity", "method": "GET", "pattern": "/api/non-vacuity/untyped", "path": "/api/non-vacuity/untyped", "query": "", "status": 200, "body": {}}
    assert any("declares no response model" in problem for problem in contract_problems(sample, [route]))


def test_the_exporter_names_the_pattern_of_an_unknown_param(node: str) -> None:
    script = (
        f"import {{ substitutePattern }} from {json.dumps(EXPORTER.as_uri())};"
        "try { substitutePattern('/api/things/:nope', {}); process.exit(0); }"
        "catch (error) { console.error(error.message); process.exit(3); }"
    )
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60, check=False
    )
    assert completed.returncode == 3
    assert ":nope" in completed.stderr and "/api/things/:nope" in completed.stderr


FIXTURE_REL = Path("frontend", "tests", "e2e", "fixture")
# What the exporter loads, and the package.json that makes the .ts modules ESM.
EXPORTER_CLOSURE = (
    FIXTURE_REL / "registry.ts",
    FIXTURE_REL / "mockApi.ts",
    FIXTURE_REL / "contractSamples.ts",
    FIXTURE_REL / "data",
    Path("frontend", "package.json"),
)


def test_the_exporter_runs_on_the_fixture_modules_alone(
    node: str, samples: list[dict[str, Any]], tmp_path: Path
) -> None:
    """CI's backend job has no node_modules, and the exporter must not load
    frontend/src: run it on a copy of the modules it loads and nothing else.
    A package or src module that survives type stripping (for example an
    all-inline ``import { type X } from '@playwright/test'``) fails here with
    ERR_MODULE_NOT_FOUND even where a developer's node_modules would hide it."""
    isolated = tmp_path / "isolated"
    for relative in (Path("tools", EXPORTER.name), *EXPORTER_CLOSURE):
        source, target = ROOT / relative, isolated / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    reachable = [parent / "node_modules" for parent in (isolated, *isolated.parents) if (parent / "node_modules").exists()]
    assert reachable == [], f"the isolated copy can still resolve packages from {reachable}"
    assert not (isolated / "frontend" / "src").exists()

    out = tmp_path / "isolated.json"
    completed = subprocess.run(
        [node, str(isolated / "tools" / EXPORTER.name), "--out", str(out)],
        cwd=isolated,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, (
        "the exporter needs more than the fixture modules (a runtime import of a package or of "
        f"frontend/src survived type stripping):\n{completed.stderr}"
    )
    assert json.loads(out.read_text(encoding="utf-8")) == samples


def test_the_exporter_collects_data_module_contract_samples(samples: list[dict[str, Any]]) -> None:
    """Step 3 of the exporter (a data/*.ts module's own contractSamples()) is exercised by real data."""
    sources = {sample["source"] for sample in samples}
    assert "data/genie.ts:GENIE_HISTORY_SESSIONS" in sources


# --- CI pin -------------------------------------------------------------------


def test_ci_runs_this_contract_with_node_and_the_require_flag() -> None:
    jobs = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    steps = jobs["backend-tests"]["steps"]
    assert any(str(step.get("uses", "")).startswith("actions/setup-node@") for step in steps), (
        "backend-tests must install Node so the fixture exporter runs"
    )
    pytest_steps = [step for step in steps if "pytest" in str(step.get("run", ""))]
    assert pytest_steps, "backend-tests has no pytest step"
    assert any(step.get("env", {}).get(REQUIRE_FLAG) == "1" for step in pytest_steps), (
        f"backend-tests' pytest step must set {REQUIRE_FLAG}=1 so a missing Node fails, not skips"
    )
