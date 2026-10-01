"""Golden, raising and real-baseline cases for tools/gen_api_types.py.

The emitter turns tests/fixtures/openapi_baseline.json into the type-only
frontend/src/types/api.gen.ts (audit quality-04 / stack-v1, decision
D-api-types-a1). Every mapping rule is closed: a construct outside the
reviewed sets must raise UnsupportedSchema, and every emitted description or
string literal passes a fail-closed content screen. These cases pin each rule
in memory; the drift and serialization gates live in
tests/unit/test_api_types_generated.py.
"""

from __future__ import annotations

import copy
import json
import random
import re
from typing import Any

import pytest

from backend.services.scoring import (
    HIGH_OPPORTUNITY_THRESHOLD,
    SCORE_BAND_HIGH_MIN,
    SCORE_BAND_MED_MIN,
)
from tests.unit.test_score_threshold_guard import _BAND_EDGE, _SCORE_PLUS_COPY
from tools import gen_api_types as gen
from tools.gen_api_types import UnsupportedSchema

JSON = "application/json"


def _ok(schema: dict[str, Any], status: str = "200") -> dict[str, Any]:
    return {status: {"description": "ok", "content": {JSON: {"schema": schema}}}}


def _spec(
    schemas: dict[str, Any],
    operations: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for (method, path), operation in (operations or {}).items():
        paths.setdefault(path, {})[method] = operation
    return {"openapi": "3.1.0", "components": {"schemas": schemas}, "paths": paths}


def _serve(schemas: dict[str, Any], root: str = "Item") -> dict[str, Any]:
    """A spec with one canonical GET serving ``root`` so its closure renders."""

    op = {"responses": _ok({"$ref": f"#/components/schemas/{root}"})}
    return _spec(schemas, {("get", "/api/v1/item"): op})


def _obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties, "title": "T"}
    if required is not None:
        schema["required"] = required
    return schema


def _member(text: str, map_name: str, name: str) -> str:
    """The rendered body of one ``name: {...};`` member of ``map_name``."""

    block = text.split(f"export interface {map_name} {{\n", 1)[1].split("\n}\n", 1)[0]
    match = re.search(rf"^  {re.escape(name)}: (.*?);$(?=\n  [^\s}}]|\Z)", block, re.M | re.S)
    assert match, f"{name} not rendered in {map_name}:\n{block}"
    return match.group(1)


def _operation(text: str, key: str) -> str:
    block = text.split("export interface ApiOperations {\n", 1)[1]
    start = block.index(f"  {json.dumps(key)}: {{\n")
    return block[start : block.index("\n  };", start)]


def _raises(spec: dict[str, Any], keyword: str) -> UnsupportedSchema:
    with pytest.raises(UnsupportedSchema) as info:
        gen.render(spec)
    assert info.value.keyword == keyword, str(info.value)
    assert info.value.pointer, "the pointer must locate the construct"
    return info.value


def _real() -> dict[str, Any]:
    return json.loads(gen.BASELINE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Rule cases
# ---------------------------------------------------------------------------


def test_anyof_enum_const_and_property_names_render() -> None:
    text = gen.render(
        _serve(
            {
                "Item": _obj(
                    {
                        "maybe": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                        "kind": {"enum": ["a", "b", None]},
                        "fixed": {"const": "only", "type": "string"},
                        "counts": {
                            "type": "object",
                            "additionalProperties": {"type": "integer"},
                            "propertyNames": {"enum": ["x", "y"]},
                        },
                    }
                )
            }
        )
    )
    body = _member(text, "ResponseSchemas", "Item")
    assert "    maybe: string | null;" in body
    assert '    kind: "a" | "b" | null;' in body
    assert '    fixed: "only";' in body
    assert '    counts: { [K in "x" | "y"]?: number };' in body


def test_additional_properties_forms_render() -> None:
    text = gen.render(
        _serve(
            {
                "Item": _obj(
                    {
                        "closed": _obj({"a": {"type": "string"}}) | {"additionalProperties": False},
                        "mapped": {"type": "object", "additionalProperties": {"type": "string"}},
                        "open": {"type": "object", "additionalProperties": True},
                        "bare": {"type": "object"},
                        "empty": {"type": "object", "properties": {}, "additionalProperties": False},
                    }
                )
            }
        )
    )
    body = _member(text, "ResponseSchemas", "Item")
    assert "    closed: {\n      a: string;\n    };" in body
    assert "    mapped: { [key: string]: string };" in body
    assert "    open: { [key: string]: unknown };" in body
    assert "    bare: { [key: string]: unknown };" in body
    assert "    empty: Record<string, never>;" in body


def test_nested_arrays_and_quoted_names_render() -> None:
    schemas = {
        "Weird-Name": _obj(
            {
                "grid": {
                    "type": "array",
                    "items": {"type": "array", "items": {"$ref": "#/components/schemas/Leaf"}},
                },
                "a b": {"type": "boolean"},
                "either": {"type": "array", "items": {"anyOf": [{"type": "string"}, {"type": "integer"}]}},
            }
        ),
        "Leaf": _obj({"id": {"type": "string"}}, ["id"]),
    }
    text = gen.render(_serve(schemas, root="Weird-Name"))
    body = _member(text, "ResponseSchemas", '"Weird-Name"')
    assert "    grid: ResponseSchemas['Leaf'][][];" in body
    assert '    "a b": boolean;' in body
    assert "    either: (string | number)[];" in body
    assert "ok: ResponseSchemas['Weird-Name'];" in text


def test_descriptions_and_literals_are_escaped() -> None:
    schemas = {
        "Item": _obj(
            {
                "note": {"type": "string", "description": "ends */ early and more"},
                "quote": {"enum": ['say "hi"', "back\\slash", "sep x"]},
            }
        )
    }
    text = gen.render(_serve(schemas))
    assert "/** ends *\\/ early\\u2028and\\u2029more */" in text
    assert " " not in text and " " not in text
    assert '"say \\"hi\\"" | "back\\\\slash" | "sep\\u2028x"' in text


def test_multi_line_description_is_a_jsdoc_block() -> None:
    schemas = {"Item": _obj({"x": {"type": "string"}}) | {"description": "First.\n\nSecond."}}
    text = gen.render(_serve(schemas))
    assert "  /**\n   * First.\n   *\n   * Second.\n   */\n  Item: {" in text


def test_self_referencing_ref_terminates() -> None:
    schemas = {
        "Item": _obj(
            {"children": {"type": "array", "items": {"$ref": "#/components/schemas/Item"}}}
        )
    }
    spec = _serve(schemas)
    assert gen.response_closure(spec) == {"Item"}
    assert "    children: ResponseSchemas['Item'][];" in gen.render(spec)


def test_annotation_only_schema_is_unknown() -> None:
    text = gen.render(_serve({"Item": _obj({"anything": {"title": "Anything"}})}))
    assert "    anything: unknown;" in text


# ---------------------------------------------------------------------------
# Presence view
# ---------------------------------------------------------------------------

_NULLABLE = {"anyOf": [{"type": "string"}, {"type": "null"}]}


def test_presence_view_marks_every_declared_field_present() -> None:
    schemas = {"Item": _obj({"x": _NULLABLE, "y": {"type": "integer"}}, ["y"])}
    body = _member(gen.render(_serve(schemas)), "ResponseSchemas", "Item")
    assert "    x: string | null;" in body
    assert "    y: number;" in body
    assert "?:" not in body


def test_absent_key_roots_keep_their_declared_required_list() -> None:
    schemas = {"HealthResponse": _obj({"x": _NULLABLE, "status": {"type": "string"}}, ["status"])}
    body = _member(gen.render(_serve(schemas, root="HealthResponse")), "ResponseSchemas", "HealthResponse")
    assert "    x?: string | null;" in body
    assert "    status: string;" in body


def test_request_view_keeps_the_declared_required_list() -> None:
    schemas = {
        "Body": _obj({"x": _NULLABLE, "y": {"type": "integer"}}, ["y"]),
        "Out": _obj({"ok": {"type": "boolean"}}, ["ok"]),
    }
    op = {
        "requestBody": {"required": True, "content": {JSON: {"schema": {"$ref": "#/components/schemas/Body"}}}},
        "responses": _ok({"$ref": "#/components/schemas/Out"}),
    }
    text = gen.render(_spec(schemas, {("post", "/api/v1/thing"): op}))
    body = _member(text, "RequestSchemas", "Body")
    assert "    x?: string | null;" in body
    assert "    y: number;" in body


def _shared(required: list[str]) -> dict[str, Any]:
    schemas = {
        "HealthResponse": _obj({"info": {"$ref": "#/components/schemas/Shared"}}),
        "AdminHealth": _obj({"info": {"$ref": "#/components/schemas/Shared"}}),
        "Shared": _obj({"a": {"type": "string"}, "b": {"type": "string"}}, required),
    }
    ops = {
        ("get", "/api/v1/health"): {"responses": _ok({"$ref": "#/components/schemas/HealthResponse"})},
        ("get", "/api/v1/admin/health"): {"responses": _ok({"$ref": "#/components/schemas/AdminHealth"})},
    }
    return _spec(schemas, ops)


def test_shared_schema_rule_raises_on_a_non_required_property() -> None:
    error = _raises(_shared(["a"]), "required")
    assert error.pointer == "#/components/schemas/Shared"
    assert "b" in str(error)


def test_shared_schema_rule_passes_when_every_property_is_required() -> None:
    text = gen.render(_shared(["a", "b"]))
    assert "    a: string;\n    b: string;" in _member(text, "ResponseSchemas", "Shared")


def test_keyword_named_properties_are_names_not_keywords() -> None:
    names = ["title", "description", "items", "type", "properties", "oneOf"]
    schemas = {"Item": _obj({name: {"type": "string"} for name in names})}
    body = _member(gen.render(_serve(schemas)), "ResponseSchemas", "Item")
    for name in names:
        assert f"    {name}: string;" in body


# ---------------------------------------------------------------------------
# Operation map
# ---------------------------------------------------------------------------


def _ops_spec() -> dict[str, Any]:
    schemas = {
        "Body": _obj({"x": {"type": "string"}}, ["x"]),
        "Out": _obj({"ok": {"type": "boolean"}}, ["ok"]),
    }
    out = {"$ref": "#/components/schemas/Out"}
    ops = {
        ("post", "/api/v1/optional"): {
            "requestBody": {
                "content": {JSON: {"schema": {"anyOf": [{"$ref": "#/components/schemas/Body"}, {"type": "null"}]}}}
            },
            "responses": _ok(out),
        },
        ("get", "/api/v1/plain"): {"responses": _ok({"type": "array", "items": out})},
        ("post", "/api/v1/create/{item_id}"): {
            "parameters": [
                {"in": "path", "name": "item_id", "required": True, "schema": {"type": "string"}},
                {"in": "query", "name": "limit", "required": False, "schema": {"type": "integer"}, "description": "Page size."},
                {
                    "in": "header",
                    "name": "Idempotency-Key",
                    "required": False,
                    "schema": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
            ],
            "requestBody": {"required": True, "content": {JSON: {"schema": {"$ref": "#/components/schemas/Body"}}}},
            "responses": _ok(out),
        },
    }
    return _spec(schemas, ops)


def test_operation_map_bodies_and_empty_parameter_groups() -> None:
    text = gen.render(_ops_spec())
    optional = _operation(text, "POST /api/v1/optional")
    assert "    body?: RequestSchemas['Body'] | null;" in optional
    assert "    pathParams: Record<string, never>;" in optional
    assert "    query: Record<string, never>;" in optional
    assert "    headers: Record<string, never>;" in optional
    plain = _operation(text, "GET /api/v1/plain")
    assert "    body: never;" in plain
    assert "    ok: ResponseSchemas['Out'][];" in plain


def test_operation_map_parameters_and_reviewed_header() -> None:
    op = _operation(gen.render(_ops_spec()), "POST /api/v1/create/{item_id}")
    assert "    pathParams: {\n      item_id: string;\n    };" in op
    assert "    query: {\n      /** Page size. */\n      limit?: number;\n    };" in op
    assert '    headers: {\n      "Idempotency-Key"?: string | null;\n    };' in op
    assert "    body: RequestSchemas['Body'];" in op
    assert "    ok: ResponseSchemas['Out'];" in op


def test_real_baseline_pins_the_multi_2xx_union_and_inline_arrays() -> None:
    text = gen.render(_real())
    complete = _operation(text, "POST /api/v1/genie/message/complete")
    assert (
        "    ok: ResponseSchemas['GenieMessageResponse'] | ResponseSchemas['GenieCompletionJobStatus'];"
        in complete
    )
    assert "    ok: ResponseSchemas['LeadSummary'][];" in _operation(text, "GET /api/v1/leads")
    create = _operation(text, "POST /api/v1/portfolio/create")
    assert '"Idempotency-Key"?: string | null;' in create
    assert "    body?:" in _operation(text, "POST /api/v1/genie/start")
    assert "    body?:" in _operation(text, "POST /api/v1/portfolio/preview")


def test_helper_types_are_emitted() -> None:
    text = gen.render(_ops_spec())
    for line in (
        "export type ApiResponse<K extends keyof ResponseSchemas> = ResponseSchemas[K];",
        "export type ApiRequest<K extends keyof RequestSchemas> = RequestSchemas[K];",
        "export type ApiOk<K extends keyof ApiOperations> = ApiOperations[K]['ok'];",
        "export type ApiBody<K extends keyof ApiOperations> = ApiOperations[K]['body'];",
    ):
        assert line in text
    assert text.splitlines()[0] == gen.BANNER
    assert gen.BANNER.startswith("// GENERATED by tools/gen_api_types.py")


# ---------------------------------------------------------------------------
# Raising cases (closed keyword, format, parameter and body sets)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("schema", "keyword"),
    [
        ({"oneOf": [{"type": "string"}]}, "oneOf"),
        ({"allOf": [{"type": "string"}]}, "allOf"),
        ({"type": "array", "prefixItems": [{"type": "string"}]}, "prefixItems"),
        ({"type": "object", "patternProperties": {"^x": {"type": "string"}}}, "patternProperties"),
        ({"anyOf": [{"type": "string"}], "discriminator": {"propertyName": "k"}}, "discriminator"),
        ({"type": ["string", "null"]}, "type"),
        ({"type": "string", "format": "binary"}, "format"),
        ({"type": "string", "format": "email"}, "format"),
        ({"type": "integer", "exclusiveMaximum": 5}, "exclusiveMaximum"),
        ({"type": "string", "examples": ["x"]}, "examples"),
        ({"type": "array", "items": {"type": "string"}, "uniqueItems": True}, "uniqueItems"),
        ({"type": "integer", "multipleOf": 2}, "multipleOf"),
        (
            {"type": "object", "properties": {"a": {"type": "string"}}, "additionalProperties": {"type": "string"}},
            "additionalProperties",
        ),
        ({"$ref": "https://elsewhere.example/schema.json"}, "$ref"),
    ],
)
def test_unsupported_schema_constructs_raise(schema: dict[str, Any], keyword: str) -> None:
    _raises(_serve({"Item": _obj({"field": schema})}), keyword)


def _with_parameter(parameter: dict[str, Any]) -> dict[str, Any]:
    spec = _ops_spec()
    spec["paths"]["/api/v1/plain"]["get"]["parameters"] = [parameter]
    return spec


@pytest.mark.parametrize(
    ("parameter", "keyword"),
    [
        ({"in": "header", "name": "X-Forwarded-Email", "required": False, "schema": {"type": "string"}}, "header"),
        ({"in": "cookie", "name": "mip_force_degraded", "required": False, "schema": {"type": "string"}}, "in"),
        ({"in": "querystring", "name": "q", "required": False, "schema": {"type": "string"}}, "in"),
        ({"in": "query", "name": "q", "required": False, "schema": {"type": "string"}, "example": "x"}, "example"),
    ],
)
def test_unreviewed_parameters_raise(parameter: dict[str, Any], keyword: str) -> None:
    error = _raises(_with_parameter(parameter), keyword)
    assert "GET /api/v1/plain" in str(error)


def test_multipart_request_body_raises() -> None:
    spec = _ops_spec()
    spec["paths"]["/api/v1/optional"]["post"]["requestBody"]["content"] = {
        "multipart/form-data": {"schema": {"type": "object"}}
    }
    _raises(spec, "content")


def test_2xx_without_content_raises() -> None:
    spec = _ops_spec()
    spec["paths"]["/api/v1/plain"]["get"]["responses"] = {"200": {"description": "No body"}}
    _raises(spec, "content")


# ---------------------------------------------------------------------------
# Content screen
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Contact a@b.com for access.",
        "Example borrower B-ABCDEFGHJKLMN.",
        "Call 555-867-5309.",
        "See https://x for details.",
        "High band is >= 85.",
        "75+ are the strongest candidates.",
        "Loaded from /mocks/fixtureData.",
        "Served by mockServiceWorker.",
    ],
)
def test_content_screen_rejects_pii_egress_and_literal_gate_shapes(text: str) -> None:
    with pytest.raises(UnsupportedSchema) as info:
        gen.screen_text(text, "#/x/description")
    assert info.value.keyword == "content-screen"
    assert info.value.pointer == "#/x/description"
    spec = _serve({"Item": _obj({"x": {"type": "string", "description": text}})})
    error = _raises(spec, "content-screen")
    assert error.pointer == "#/components/schemas/Item/properties/x/description"
    enum_spec = _serve({"Item": _obj({"x": {"enum": [text]}})})
    assert _raises(enum_spec, "content-screen").pointer.endswith("/properties/x/enum/0")


def test_content_screen_allows_reserved_example_addresses() -> None:
    gen.screen_text("Write to ops@lender.example for access.", "#/x")
    text = gen.render(_serve({"Item": _obj({"x": {"type": "string", "description": "ops@lender.example"}})}))
    assert "ops@lender.example" in text


def test_screen_patterns_stay_in_parity_with_the_repo_literal_gates() -> None:
    """The copied patterns must not drift from the scans the file is subject to."""

    assert _BAND_EDGE.pattern == gen.BAND_EDGE_PATTERN
    assert {int(n) for n in re.findall(r"\d+", gen.BAND_EDGE_PATTERN)} == {
        SCORE_BAND_HIGH_MIN,
        SCORE_BAND_MED_MIN,
    }
    expected_plus = _SCORE_PLUS_COPY.pattern.replace(r"(\d{2,3})", str(HIGH_OPPORTUNITY_THRESHOLD))
    assert expected_plus == gen.PLUS_COPY_PATTERN


# ---------------------------------------------------------------------------
# Scope: non-2xx bodies and operation text are never emitted
# ---------------------------------------------------------------------------


def test_non_2xx_schemas_are_never_emitted() -> None:
    """HTTPValidationError/ValidationError declare the input/ctx fields that the
    422 handler strips (backend/main.py:684, _request_validation_handler)
    so a 422 never reflects typed PII: they must never become a frontend type."""

    spec = _real()
    text = gen.render(spec)
    for name in ("HTTPValidationError", "ValidationError"):
        assert name in spec["components"]["schemas"], "non-vacuity: the baseline declares it"
        assert name not in gen.response_closure(spec)
        assert name not in gen.request_closure(spec)
        # A backend description may mention FastAPI's RequestValidationError
        # handler; the schema name itself must never appear as a word.
        assert not re.search(rf"\b{name}\b", text), name


def test_operation_summary_and_description_are_never_emitted() -> None:
    spec = _ops_spec()
    op = spec["paths"]["/api/v1/plain"]["get"]
    op["summary"] = "ZQXJ-summary-token"
    op["description"] = "ZQXJ-description-token"
    op["operationId"] = "ZQXJ_operation_id"
    op["tags"] = ["ZQXJ-tag"]
    assert "ZQXJ" not in gen.render(spec)


# ---------------------------------------------------------------------------
# Determinism and the real baseline
# ---------------------------------------------------------------------------


def _shuffled(value: Any, rng: random.Random) -> Any:
    if isinstance(value, dict):
        items = list(value.items())
        rng.shuffle(items)
        return {key: _shuffled(item, rng) for key, item in items}
    if isinstance(value, list):
        return [_shuffled(item, rng) for item in value]
    return value


def test_rendering_is_deterministic_and_key_order_independent() -> None:
    spec = _real()
    first = gen.render(spec)
    assert gen.render(copy.deepcopy(spec)) == first
    assert gen.render(_shuffled(spec, random.Random(20260930))) == first
    assert first.endswith("\n") and "\r" not in first


def test_real_baseline_renders_with_absent_roots_in_the_response_closure() -> None:
    spec = _real()
    text = gen.render(spec)
    assert gen.response_closure(spec) >= gen.ABSENT_KEY_ROOTS
    health = _member(text, "ResponseSchemas", "HealthResponse")
    members = re.findall(r"^    (\w+)(\??): ", health, re.M)
    assert members, "non-vacuity: HealthResponse renders members"
    required = {name for name, optional in members if not optional}
    assert required == {"status", "mode"}
    assert all(optional == "?" for name, optional in members if name not in required)


def test_real_baseline_needs_every_reviewed_keyword(monkeypatch: pytest.MonkeyPatch) -> None:
    assert len(gen.ALLOWED_KEYWORDS) == 22
    assert gen.IGNORED_KEYWORDS < gen.ALLOWED_KEYWORDS
    assert {"date-time", "date", "uuid"} == gen.ALLOWED_FORMATS
    assert {"in", "name", "required", "schema", "description"} == gen.PARAMETER_KEYS
    assert {"Idempotency-Key"} == gen.REVIEWED_HEADER_PARAMS
    assert {"HealthResponse"} == gen.ABSENT_KEY_ROOTS
    monkeypatch.setattr(gen, "ALLOWED_KEYWORDS", gen.ALLOWED_KEYWORDS - {"anyOf"})
    with pytest.raises(UnsupportedSchema) as info:
        gen.render(_real())
    assert info.value.keyword == "anyOf"
