"""Gates on the committed frontend/src/types/api.gen.ts (decision D-api-types-a2).

The generated module is only true while four facts hold, so each is a test:

(a) it is byte-identical to what tools/gen_api_types.py renders from the
    committed OpenAPI baseline (operation summaries are never emitted, which
    is why this drift test and the baseline test are both needed);
(b) the only route-level serialization overrides are the ones the generator
    models (exclude_unset / exclude_defaults == ABSENT_KEY_ROOTS); every
    other override would make the presence view lie, so it fails outright;
(c) every possibly-2xx Response built by hand under backend/ is a listed full
    model dump or a listed non-API FileResponse;
(d) the module and every importer are type-only.
"""

from __future__ import annotations

import ast
import json
import re
import typing
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from fastapi.routing import APIRoute
from pydantic import BaseModel

from backend.main import app
from tools import gen_api_types

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
GENERATED = ROOT / "frontend" / "src" / "types" / "api.gen.ts"
STALE = "frontend/src/types/api.gen.ts is stale: run python tools/regen_openapi_baseline.py and commit both files"


# ---------------------------------------------------------------------------
# (a) drift
# ---------------------------------------------------------------------------


def test_generated_api_types_are_current() -> None:
    rendered = gen_api_types.render(json.loads(gen_api_types.BASELINE.read_text(encoding="utf-8")))
    assert GENERATED.is_file(), STALE
    assert GENERATED.read_bytes() == rendered.encode("utf-8"), STALE


# ---------------------------------------------------------------------------
# (b) route serialization overrides
# ---------------------------------------------------------------------------

UNMODELED = "gen_api_types cannot model {flag} on {where}: remove it or extend the generator with a reviewed rule and golden case"


def _model_names(annotation: Any) -> set[str]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return {annotation.__name__}
    origin = typing.get_origin(annotation)
    if origin is None:
        return set()
    names: set[str] = set()
    for arg in typing.get_args(annotation):
        names |= _model_names(arg)
    return names


def _response_routes() -> Iterator[tuple[APIRoute, set[str]]]:
    for route in app.routes:
        if isinstance(route, APIRoute) and route.response_model is not None:
            yield route, _model_names(route.response_model)


def _where(route: APIRoute) -> str:
    return f"{','.join(sorted(route.methods))} {route.path}"


def unmodeled_overrides(route: APIRoute) -> list[str]:
    flags = []
    if route.response_model_exclude_none:
        flags.append("response_model_exclude_none")
    if route.response_model_include is not None:
        flags.append("response_model_include")
    if route.response_model_exclude is not None:
        flags.append("response_model_exclude")
    if route.response_model_by_alias is False:
        flags.append("response_model_by_alias=False")
    return flags


def test_serialization_overrides_match_the_generator() -> None:
    modeled: set[str] = set()
    modeled_paths: set[str] = set()
    problems: list[str] = []
    for route, names in _response_routes():
        flagged = route.response_model_exclude_unset or route.response_model_exclude_defaults
        if flagged:
            modeled |= names
            modeled_paths.add(route.path)
        if names & gen_api_types.ABSENT_KEY_ROOTS and not flagged:
            problems.append(f"{_where(route)} serves {sorted(names)} without exclude_unset/exclude_defaults")
        problems.extend(UNMODELED.format(flag=flag, where=_where(route)) for flag in unmodeled_overrides(route))
    assert problems == [], "\n".join(problems)
    assert modeled, "non-vacuity: at least one route is serialized with exclude_unset"
    assert "HealthResponse" in modeled
    assert {"/api/v1/health", "/api/health"} <= modeled_paths
    assert modeled == set(gen_api_types.ABSENT_KEY_ROOTS), (
        f"routes serialized with exclude_unset/exclude_defaults serve {sorted(modeled)}, "
        f"but gen_api_types.ABSENT_KEY_ROOTS is {sorted(gen_api_types.ABSENT_KEY_ROOTS)}"
    )


# ---------------------------------------------------------------------------
# (c) hand-built 2xx responses
# ---------------------------------------------------------------------------

RESPONSE_MODULES = {"fastapi", "fastapi.responses", "starlette.responses"}
STATUS_MODULES = {"fastapi.status", "starlette.status"}
# Shrink-only. Keyed by (module, enclosing function), never by line.
RAW_2XX_SITES = {("backend/api/genie.py", "genie_message_complete")}
NON_API_2XX_SITES = {
    ("backend/main.py", "_hashed_asset"): "hashed Vite asset FileResponse, not an /api operation",
    ("backend/main.py", "_spa_fallback"): "SPA shell/static FileResponse, not an /api operation",
}
UNLISTED = "a hand-built 2xx body breaks the presence view: serialize the response model or re-prove gen_api_types"
EXCLUSION_KWARGS = {"exclude", "exclude_unset", "exclude_defaults", "exclude_none", "include"}


class _Bindings:
    """What each local name means in one module: a Response class or a module."""

    def __init__(self, tree: ast.Module) -> None:
        self.classes: set[str] = set()
        self.modules: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    local = alias.asname or alias.name
                    dotted = f"{node.module}.{alias.name}"
                    if node.module in RESPONSE_MODULES and _is_response_name(alias.name):
                        self.classes.add(local)
                    elif dotted in RESPONSE_MODULES | STATUS_MODULES:
                        self.modules[local] = dotted
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        self.modules[alias.asname] = alias.name
                    else:
                        root = alias.name.split(".")[0]
                        self.modules[root] = root

    def dotted(self, node: ast.expr) -> str | None:
        parts: list[str] = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if not isinstance(node, ast.Name) or node.id not in self.modules:
            return None
        return ".".join([self.modules[node.id], *reversed(parts)])

    def is_response_call(self, call: ast.Call) -> bool:
        func = call.func
        if isinstance(func, ast.Name):
            return func.id in self.classes
        if isinstance(func, ast.Attribute) and _is_response_name(func.attr):
            return self.dotted(func.value) in RESPONSE_MODULES
        return False

    def status_value(self, node: ast.expr) -> int | None:
        if isinstance(node, ast.Constant) and type(node.value) is int:
            return node.value
        if isinstance(node, ast.Attribute):
            owner = self.dotted(node.value)
            match = re.fullmatch(r"HTTP_(\d{3})_\w+", node.attr)
            if owner in STATUS_MODULES and match:
                return int(match.group(1))
        return None


def _is_response_name(name: str) -> bool:
    return name == "Response" or name.endswith("Response")


def _status_arg(call: ast.Call) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == "status_code":
            return keyword.value
    return call.args[1] if len(call.args) > 1 else None


def _content_arg(call: ast.Call) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == "content":
            return keyword.value
    return call.args[0] if call.args else None


def possibly_2xx_sites(source: str, rel: str) -> list[tuple[str, str, ast.Call, int | None]]:
    """(module, enclosing function, call, status or None) for every call that may be 2xx."""

    tree = ast.parse(source)
    bindings = _Bindings(tree)
    sites: list[tuple[str, str, ast.Call, int | None]] = []

    def visit(node: ast.AST, function: str) -> None:
        for child in ast.iter_child_nodes(node):
            scope = child.name if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) else function
            if isinstance(child, ast.Call) and bindings.is_response_call(child):
                arg = _status_arg(child)
                status = None if arg is None else bindings.status_value(arg)
                unprovable = arg is not None and status is None
                if unprovable or status is None or 200 <= status <= 299:
                    sites.append((rel, function, child, status))
            visit(child, scope)

    visit(tree, "<module>")
    return sites


def _backend_sources() -> Iterator[tuple[str, str]]:
    for path in sorted(BACKEND.rglob("*.py")):
        if "__pycache__" not in path.parts:
            yield path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8")


def raw_site_problems(call: ast.Call, status: int | None, route: APIRoute | None) -> list[str]:
    content = _content_arg(call)
    problems: list[str] = []
    if not (
        isinstance(content, ast.Call)
        and isinstance(content.func, ast.Attribute)
        and content.func.attr == "model_dump"
    ):
        return ["the content must be <model>.model_dump(mode='json')"]
    kwargs = {keyword.arg: keyword.value for keyword in content.keywords}
    mode = kwargs.get("mode")
    if not (isinstance(mode, ast.Constant) and mode.value == "json"):
        problems.append("model_dump must pass mode='json'")
    problems.extend(f"model_dump must not pass {name}" for name in sorted(EXCLUSION_KWARGS & set(kwargs)))
    by_alias = kwargs.get("by_alias")
    if isinstance(by_alias, ast.Constant) and by_alias.value is False:
        problems.append("model_dump must not pass by_alias=False")
    if route is None:
        return [*problems, "no APIRoute endpoint matches the enclosing function"]
    if status is None or status not in route.responses:
        return [*problems, f"status {status} is not a declared key of the route's responses"]
    if not (isinstance(by_alias, ast.Constant) and by_alias.value is True):
        model = route.responses[status].get("model")
        problems.extend(alias_problems(model))
    return problems


def alias_problems(model: Any, seen: set[type] | None = None) -> list[str]:
    """Fields whose wire name differs from the attribute name, recursively."""

    seen = set() if seen is None else seen
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        return [f"{model!r} is not a pydantic model"]
    if model in seen:
        return []
    seen.add(model)
    problems = []
    if model.model_config.get("alias_generator") is not None:
        problems.append(f"{model.__name__} sets alias_generator")
    for name, field in model.model_fields.items():
        for alias in (field.alias, field.serialization_alias):
            if alias not in (None, name):
                problems.append(f"{model.__name__}.{name} serializes as {alias!r}")
        for nested in _nested_models(field.annotation):
            problems.extend(alias_problems(nested, seen))
    return problems


def _nested_models(annotation: Any) -> Iterator[type[BaseModel]]:
    """BaseModels reachable through list / Optional / Union / Annotated / dict values."""

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation
        return
    origin = typing.get_origin(annotation)
    if origin is None:
        return
    args = typing.get_args(annotation)
    if origin in (dict, Mapping):
        args = args[1:]
    for arg in args:
        yield from _nested_models(arg)


def _routes_for(function: str) -> list[APIRoute]:
    return [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and getattr(route.endpoint, "__name__", "") == function
    ]


def test_raw_2xx_responses_serialize_every_field() -> None:
    found: dict[tuple[str, str], list[tuple[ast.Call, int | None]]] = {}
    for rel, source in _backend_sources():
        for module, function, call, status in possibly_2xx_sites(source, rel):
            found.setdefault((module, function), []).append((call, status))
    listed = RAW_2XX_SITES | set(NON_API_2XX_SITES)
    problems = [f"{module}:{function}: {UNLISTED}" for module, function in sorted(set(found) - listed)]
    problems += [f"{module}:{function}: listed but gone (stale)" for module, function in sorted(listed - set(found))]
    for key in sorted(RAW_2XX_SITES & set(found)):
        routes = [route for route in _routes_for(key[1]) if route.path.startswith("/api/v1/")]
        for call, status in found[key]:
            route = routes[0] if routes else None
            problems += [f"{key[0]}:{key[1]}: {p}" for p in raw_site_problems(call, status, route)]
    assert problems == [], "\n".join(problems)
    assert RAW_2XX_SITES & set(found), "non-vacuity: the genie 202 site is found"


# ---------------------------------------------------------------------------
# (d) type-only module and importers
# ---------------------------------------------------------------------------

_SPECIFIER = re.compile(r"""(?:from|import)\s*\(?\s*["']([^"']+)["']""")
_GEN_SPECIFIER = re.compile(r"(^|/)api\.gen(\.ts|\.js)?$")
_STATEMENT = re.compile(r"(?ms)^\s*(import|export)\b.*?;")
_DYNAMIC = re.compile(r"""import\s*\(\s*["']([^"']+)["']\s*\)""")
_TYPE_ONLY = re.compile(r"^\s*(import\s+type\s*(\{|\*\s+as\s)|export\s+type\s*\{)")
_COLUMN0_OK = ("export interface ", "export type ", "//", "/**", " *", " */", "}")


def column0_problems(text: str) -> list[str]:
    return [
        f"{number}: {line}"
        for number, line in enumerate(text.splitlines(), start=1)
        if line and not line.startswith(" ") and not line.startswith(_COLUMN0_OK)
    ]


def importer_problems(text: str) -> tuple[bool, list[str]]:
    """(imports the module?, violations) for one TS source."""

    problems = [f"dynamic import of {m.group(1)}" for m in _DYNAMIC.finditer(text) if _GEN_SPECIFIER.search(m.group(1))]
    imports = bool(problems)
    for statement in _STATEMENT.finditer(text):
        body = statement.group(0)
        specifier = _SPECIFIER.search(body)
        if not specifier or not _GEN_SPECIFIER.search(specifier.group(1)):
            continue
        imports = True
        if not _TYPE_ONLY.match(body):
            problems.append(f"not a type-only declaration: {' '.join(body.split())}")
    return imports, problems


def test_generated_module_is_type_only() -> None:
    text = GENERATED.read_text(encoding="utf-8")
    assert text.startswith(gen_api_types.BANNER + "\n")
    assert column0_problems(text) == []
    importers = 0
    problems: list[str] = []
    for base in (ROOT / "frontend" / "src", ROOT / "frontend" / "tests"):
        for path in sorted(base.rglob("*")):
            if path.suffix not in {".ts", ".tsx", ".mts"} or {"node_modules", "dist"} & set(path.parts):
                continue
            if path == GENERATED:
                continue
            imports, found = importer_problems(path.read_text(encoding="utf-8"))
            importers += imports
            problems += [f"{path.relative_to(ROOT)}: {p}" for p in found]
    print(f"api.gen.ts importers: {importers}")
    assert problems == [], "\n".join(problems)


def test_type_only_rules_reject_planted_cases() -> None:
    """Non-vacuity for (d), in memory."""

    assert column0_problems("export const X = 1;\n") == ["1: export const X = 1;"]
    assert column0_problems("export interface A {\n  x: string;\n}\n// c\n") == []
    ok = [
        "import type { ApiOk } from '../types/api.gen';",
        'import type * as Api from "./types/api.gen.ts";',
        "export type { ApiResponse } from './api.gen';",
    ]
    for line in ok:
        assert importer_problems(line) == (True, []), line
    bad = [
        "import { type ApiOk } from './types/api.gen';",
        "import { ApiOk } from './types/api.gen';",
        "import './types/api.gen';",
        "export { type ApiOk } from './api.gen';",
        "const m = await import('./types/api.gen');",
    ]
    for line in bad:
        imports, problems = importer_problems(line)
        assert imports and problems, line
    assert importer_problems("import type { X } from './other';") == (False, [])
