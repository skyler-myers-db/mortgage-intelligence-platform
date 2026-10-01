"""Planted cases for the wire-contract parser (tests/unit/frontend_wire_contract.py
and tests/unit/frontend_wire_raw_sites.py).

Every normalizer rule and every failure class of the binding pytest, on
in-memory strings or a planted module tree, so a parser change that silently
widens what counts as "bound" turns red here first.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit import frontend_wire_contract as wire
from tests.unit import frontend_wire_raw_sites as wire_raw
from tests.unit import frontend_wire_source as source

API_GEN = """\
export interface ApiOperations {
  "GET /api/v1/borrowers/search": {
    body: never;
    ok: ResponseSchemas['LeadSummary'][];
  };
  "GET /api/v1/borrowers/{borrower_id}": {
    body: never;
    ok: ResponseSchemas['Borrower360'];
  };
  "GET /api/v1/health": {
    body: never;
    ok: ResponseSchemas['HealthResponse'];
  };
  "GET /api/v1/analytics/economics/points": {
    body: never;
    ok: ResponseSchemas['EquitySpreadPointsResponse'];
  };
  "POST /api/v1/genie/message/complete": {
    body: RequestSchemas['GenieCompleteAsyncRequest'];
    ok: ResponseSchemas['GenieMessageResponse'] | ResponseSchemas['GenieCompletionJobStatus'];
  };
  "POST /api/v1/genie/start": {
    body?: { [key: string]: unknown } | null;
    ok: ResponseSchemas['GenieStartResponse'];
  };
  "POST /api/v1/offers/recommend": {
    body: RequestSchemas['OfferRecommendRequest'];
    ok: ResponseSchemas['OfferRecommendation'];
  };
  "GET /api/v1/telemetry/rum": {
    body: never;
    ok: ResponseSchemas['RumStatus'];
  };
  "POST /api/v1/telemetry/rum": {
    body: RequestSchemas['RumBatch'];
    ok: ResponseSchemas['RumAccepted'];
  };
  "GET /api/v1/x/{a}/y": {
    body: never;
    ok: ResponseSchemas['X'];
  };
  "GET /api/v1/x/{b}/y": {
    body: never;
    ok: ResponseSchemas['X'];
  };
}
"""

OPS = wire.parse_operations(API_GEN)


def lit(text: str) -> list[wire.Part]:
    return [("lit", text)]


def template(text: str) -> list[wire.Part]:
    return wire.template_parts(text)


# --------------------------------------------------------------- operations


def test_operations_carry_ok_alternatives_arrays_and_optional_bodies() -> None:
    assert OPS["GET /api/v1/borrowers/search"].ok == (("LeadSummary", True),)
    assert OPS["POST /api/v1/genie/message/complete"].single_ok() is None
    assert OPS["POST /api/v1/genie/start"].body == ()
    assert OPS["POST /api/v1/offers/recommend"].single_body() == "OfferRecommendRequest"


# ---------------------------------------------------------------- normalize


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        (lit("/health"), "/api/v1/health"),
        (lit("/api/health"), "/api/v1/health"),
        (lit("/api/v1/health"), "/api/v1/health"),
        (lit("/api/health?idle_s=0"), "/api/v1/health"),
        (lit("/api/health#frag"), "/api/v1/health"),
        (template("`/api/borrowers/${id}`"), "/api/v1/borrowers/{param}"),
        (template("`/api/borrowers/${id}${fresh ? '?fresh=true' : ''}`"), "/api/v1/borrowers/{param}"),
        (template("`/api/loan-officers/assignments${query ? `?${query}` : ''}`"), "/api/v1/loan-officers/assignments"),
        (template("`/api/activation/outbox?${qs}`"), "/api/v1/activation/outbox"),
        (template("`/api/x/${a}/y?limit=${n}&z=${m}`"), "/api/v1/x/{param}/y"),
    ],
)
def test_normalize_mirrors_api_path(parts: list[wire.Part], expected: str) -> None:
    assert wire.normalize(parts) == expected


@pytest.mark.parametrize(
    "parts",
    [
        template("`/api/x/a${b}c`"),
        template("`/api/x/${b}c/y`"),
        template("`/api/${a}-${b}/y`"),
        lit("api/health"),
        template("`${base}/health`"),
    ],
)
def test_normalize_refuses_a_placeholder_inside_a_segment(parts: list[wire.Part]) -> None:
    with pytest.raises(wire.Unresolvable):
        wire.normalize(parts)


def test_nested_templates_split_into_literal_and_placeholder_parts() -> None:
    parts = template("`/api/a/${f(`x${y}`)}/b`")

    assert parts == [("lit", "/api/a/"), ("ph", "f(`x${y}`)"), ("lit", "/b")]


# ------------------------------------------------------------------ matching


def test_a_static_segment_beats_a_placeholder_template() -> None:
    assert wire.match_operation(OPS, "GET", "/api/v1/borrowers/search") == "GET /api/v1/borrowers/search"
    assert wire.match_operation(OPS, "GET", "/api/v1/borrowers/B-0000000000001") == "GET /api/v1/borrowers/{borrower_id}"


def test_a_placeholder_client_segment_matches_only_a_placeholder() -> None:
    assert wire.match_operation(OPS, "GET", "/api/v1/borrowers/{param}") == "GET /api/v1/borrowers/{borrower_id}"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/nowhere"),
        ("POST", "/api/v1/health"),
        ("GET", "/api/v1/x/{param}/y"),
    ],
)
def test_matching_requires_exactly_one_operation(method: str, path: str) -> None:
    with pytest.raises(wire.Unresolvable):
        wire.match_operation(OPS, method, path)


# ---------------------------------------------------------- path expressions


def _tree(tmp_path: Path, files: dict[str, str]) -> wire.Project:
    for name, text in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return wire.Project(tmp_path)


def test_path_helpers_resolve_statically(tmp_path: Path) -> None:
    project = _tree(
        tmp_path,
        {
            "paths.ts": "export const HEALTH_PATH = '/api/health';\n",
            "client.ts": (
                "import { HEALTH_PATH } from './paths';\n"
                "const BORROWERS = '/api/borrowers';\n"
                "export function healthPath(hint?: number): string {\n"
                "  return hint === undefined ? '/api/health' : `/api/health?idle_s=${hint}`;\n"
                "}\n"
            ),
        },
    )
    client = tmp_path / "client.ts"

    def resolve(expr: str, method: str = "GET") -> str:
        return wire.resolve_path(project, client, expr, method, OPS)

    assert resolve("healthPath(hint)") == "GET /api/v1/health"
    assert resolve("HEALTH_PATH") == "GET /api/v1/health"
    assert resolve("`${BORROWERS}/${id}`") == "GET /api/v1/borrowers/{borrower_id}"
    assert resolve("qs ? `/api/health?${qs}` : '/api/health'") == "GET /api/v1/health"
    assert resolve("analyticsPath(\n  'economics/points',\n  opts,\n)") == "GET /api/v1/analytics/economics/points"


@pytest.mark.parametrize(
    "expr",
    [
        "analyticsPath(scope, opts)",
        "UNKNOWN_CONST",
        "missingHelper(x)",
        "cond ? '/api/health' : '/api/borrowers/search'",
        "base + '/health'",
    ],
)
def test_unresolvable_path_expressions_fail(tmp_path: Path, expr: str) -> None:
    project = _tree(tmp_path, {"client.ts": "export const x = 1;\n"})

    with pytest.raises(wire.Unresolvable):
        wire.resolve_path(project, tmp_path / "client.ts", expr, "GET", OPS)


# ----------------------------------------------------- declarations, re-exports


def test_type_resolution_follows_every_re_export_form(tmp_path: Path) -> None:
    project = _tree(
        tmp_path,
        {
            "types.ts": "export type * from './types/a';\nexport * from './types/star';\n",
            "types/a.ts": "export type { Hop } from './b';\nexport { Renamed as Shown } from './b';\n",
            "types/b.ts": "export interface Hop { x: string }\nexport interface Renamed { y: string }\n",
            "types/star.ts": "export interface Starred { z: string }\n",
            "site.ts": "import type { Hop, Shown, Starred } from './types';\ninterface Local { a: 1 }\n",
        },
    )
    site = tmp_path / "site.ts"

    assert project.resolve_type(site, "Hop") == (tmp_path / "types" / "b.ts", "Hop")
    assert project.resolve_type(site, "Shown") == (tmp_path / "types" / "b.ts", "Renamed")
    assert project.resolve_type(site, "Starred") == (tmp_path / "types" / "star.ts", "Starred")
    assert project.resolve_type(site, "Local") == (site, "Local")
    assert project.resolve_type(site, "T") is None


def test_the_real_types_ts_type_star_re_exports_resolve() -> None:
    """types.ts:884-888 re-export five modules with `export type * from`."""

    project = wire.Project()
    sales = wire.SRC / "lib" / "apiClients" / "sales.ts"

    assert project.resolve_type(sales, "LoanOfficer") == (wire.SRC / "types" / "loanOfficer.ts", "LoanOfficer")
    assert project.resolve_type(sales, "ApprovalFunnelResponse") == (
        wire.SRC / "types" / "approvalFunnel.ts",
        "ApprovalFunnelResponse",
    )


# ------------------------------------------------------------ sites, binding


SITE_MODULE = """\
import type { Borrower360, OfferRecommendation, OfferRecommendRequest, Health } from './types';
import { apiPath } from './apiPaths';
export const api = {
  borrower: (id: string) => getJson<Borrower360>(`/api/borrowers/${id}`),
  wrong: (id: string) => getJson<OfferRecommendation>(`/api/borrowers/${id}`),
  inline: (id: string) => postJson<OfferRecommendation, { borrower_id: string }>('/api/offers/recommend', { borrower_id: id }),
  named: (id: string) => postJson<OfferRecommendation, OfferRecommendRequest>('/api/offers/recommend', { borrower_id: id }),
  union: () => getJson<Borrower360 | null>('/api/borrowers/x'),
  generic: <T>() => getJson<T>('/api/health'),
  untyped: () => getJson('/api/health'),
};
async function annotated(): Promise<Health> {
  // wire: 'GET /api/v1/health'
  const res = await fetch(apiPath('/health'));
  return (await res.json()) as Health;
}
async function bare(): Promise<void> {
  await fetch('/api/v1/health');
}
async function mislabelled(): Promise<void> {
  // wire: 'GET /api/v1/borrowers/search'
  await fetch(apiPath('/health'));
}
async function orphanCast<T>(res: Response): Promise<T> {
  return (await res.json()) as T;
}
const notApi = (url: string) => fetch(url);
"""

TYPES_MODULE = """\
export interface Borrower360 { borrower_id: string }
export interface OfferRecommendation { offer: string }
export interface OfferRecommendRequest { borrower_id: string }
export interface Health { status: string }
"""


@pytest.fixture()
def planted(tmp_path: Path) -> dict[str, wire.Site]:
    project = _tree(tmp_path, {"client.ts": SITE_MODULE, "types.ts": TYPES_MODULE})
    pairs = [wire.Pair("response", (str(tmp_path / "types.ts"), "Health"), "ApiResponse", "HealthResponse", "x")]
    sites = wire_raw.find_sites(project, OPS, [tmp_path / "client.ts"])
    wire.bind_sites(project, sites, OPS, pairs)
    return {f"{site.name}:{site.kind}": site for site in sites}


def test_a_schema_named_type_argument_binds(planted: dict[str, wire.Site]) -> None:
    assert planted["borrower:transport"].bound
    assert planted["named:transport"].bound


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("wrong:transport", "is not bound to GET /api/v1/borrowers/{borrower_id}"),
        ("inline:transport", "B: non-identifier type argument"),
        ("union:transport", "T: non-identifier type argument"),
        ("generic:transport", "T does not resolve"),
        ("untyped:transport", "expected 1 type argument(s), found 0"),
        ("bare:raw", "without a `// wire:` annotation"),
        ("mislabelled:raw", "but the URL resolves to"),
        ("orphanCast:cast", "no annotated raw site"),
    ],
)
def test_every_failure_class_is_reported(planted: dict[str, wire.Site], name: str, message: str) -> None:
    site = planted[name]

    assert not site.bound
    assert any(message in error for error in site.errors), site.errors


def test_an_annotated_raw_site_binds_its_cast_through_a_pair(planted: dict[str, wire.Site]) -> None:
    assert planted["annotated:raw"].operation == "GET /api/v1/health"
    assert planted["annotated:cast"].bound


def test_a_variable_url_fetch_is_not_a_wire_site(planted: dict[str, wire.Site]) -> None:
    assert "notApi:raw" not in planted


# Every in-spec raw URL shape (review of the W5b lane: these were skipped).
# Each shape is planted twice: `bare_<n>` without an annotation, which must be
# a failing site, and `bound_<n>` with the right one, which must bind.
RAW_SHAPES = {
    "api_path_template": ("fetch(apiPath(`/borrowers/${id}`))", "GET /api/v1/borrowers/{borrower_id}"),
    "backtick_api_template": ("fetch(`/api/borrowers/${id}`)", "GET /api/v1/borrowers/{borrower_id}"),
    "api_path_const": ("fetch(apiPath(SEARCH))", "GET /api/v1/borrowers/search"),
    "concatenated_query": ("fetch(apiPath('/health') + '?idle_s=' + idle)", "GET /api/v1/health"),
    "template_wrapped_api_path": ("fetch(`${apiPath('/health')}?idle_s=${idle}`)", "GET /api/v1/health"),
    "api_path_ternary": ("fetch(apiPath(id ? `/borrowers/${id}` : `/borrowers/${'x'}`))", "GET /api/v1/borrowers/{borrower_id}"),
    "window_fetch": ("window.fetch(apiPath('/health'))", "GET /api/v1/health"),
    "global_this_fetch": ("globalThis.fetch(apiPath('/health'), { signal })", "GET /api/v1/health"),
    "self_fetch": ("self.fetch('/api/v1/health')", "GET /api/v1/health"),
    "const_url": ("fetch(HEALTH_URL)", "GET /api/v1/health"),
    "post_init": ("fetch(apiPath('/telemetry/rum'), { method: 'POST', body })", "POST /api/v1/telemetry/rum"),
    "beacon": ("window.navigator.sendBeacon(apiPath('/telemetry/rum'), blob)", "POST /api/v1/telemetry/rum"),
    "local_api_path": ("apiPath('/health')", "GET /api/v1/health"),
}


def _raw_module() -> str:
    lines = [
        "import { apiPath } from './apiPaths';",
        "import { HEALTH_URL } from './paths';",
        "const SEARCH = '/borrowers/search';",
    ]
    for name, (call, key) in RAW_SHAPES.items():
        for prefix, annotation in (("bare", None), ("bound", key)):
            lines.append(f"async function {prefix}_{name}(id: string, idle: number, signal: AbortSignal, blob: Blob, body: string) {{")
            if annotation:
                lines.append(f"  // wire: '{annotation}'")
            lines.append(f"  const res = {call};")
            lines.append("  return res;")
            lines.append("}")
    return "\n".join(lines) + "\n"


@pytest.fixture()
def raw_planted(tmp_path: Path) -> dict[str, list[wire.Site]]:
    project = _tree(tmp_path, {"client.ts": _raw_module(), "paths.ts": "export const HEALTH_URL = '/api/health';\n"})
    sites = wire_raw.find_sites(project, OPS, [tmp_path / "client.ts"])
    grouped: dict[str, list[wire.Site]] = {}
    for site in sites:
        grouped.setdefault(site.name, []).append(site)
    return grouped


@pytest.mark.parametrize("name", sorted(RAW_SHAPES))
def test_every_raw_url_shape_is_a_site_that_needs_its_annotation(raw_planted: dict[str, list[wire.Site]], name: str) -> None:
    bare = raw_planted.get(f"bare_{name}", [])
    bound = raw_planted.get(f"bound_{name}", [])

    assert [s.kind for s in bare] == ["raw"], f"bare_{name}: the URL shape must be a raw site"
    assert not bare[0].bound and "without a `// wire:` annotation" in bare[0].errors[0]
    assert [s.kind for s in bound] == ["raw"] and bound[0].bound, bound[0].errors if bound else "no site"
    assert bound[0].operation == RAW_SHAPES[name][1]


UNRESOLVABLE = wire_raw.UNRESOLVABLE
UNRESOLVABLE_URLS = {
    "placeholder_inside_segment": ("fetch(apiPath(`/x/${a}-${b}`))", "GET /api/v1/health", UNRESOLVABLE),
    "path_concatenation": ("fetch(apiPath('/borrowers/') + id)", "GET /api/v1/borrowers/{borrower_id}", UNRESOLVABLE),
    "base_plus_path": ("fetch(apiPath(base + '/health'))", "GET /api/v1/health", UNRESOLVABLE),
    "absolute_url": ("fetch(`${origin}/api/health`)", "GET /api/v1/health", UNRESOLVABLE),
    "unknown_const": ("fetch(apiPath(NOT_A_CONST))", "GET /api/v1/health", UNRESOLVABLE),
    "split_ternary": (
        "fetch(flag ? apiPath('/health') : apiPath('/borrowers/search'))",
        "GET /api/v1/health",
        "but the URL resolves to ['GET /api/v1/borrowers/search', 'GET /api/v1/health']",
    ),
}


@pytest.mark.parametrize("name", sorted(UNRESOLVABLE_URLS))
def test_an_unresolvable_raw_url_fails_even_when_annotated(tmp_path: Path, name: str) -> None:
    call, key, message = UNRESOLVABLE_URLS[name]
    module = f"import {{ apiPath }} from './apiPaths';\nasync function site(id: string) {{\n  // wire: '{key}'\n  return {call};\n}}\n"
    project = _tree(tmp_path, {"client.ts": module})

    sites = wire_raw.find_sites(project, OPS, [tmp_path / "client.ts"])

    assert [s.kind for s in sites] == ["raw"], name
    assert not sites[0].bound
    assert any(message in error for error in sites[0].errors), sites[0].errors


@pytest.mark.parametrize(
    ("call", "annotation", "message"),
    [
        ("fetch(apiPath('/telemetry/rum'), { method: 'POST' })", "GET /api/v1/telemetry/rum", "the call sends POST"),
        ("fetch(apiPath('/telemetry/rum'))", "POST /api/v1/telemetry/rum", "the call sends GET"),
        ("navigator.sendBeacon(apiPath('/telemetry/rum'), blob)", "GET /api/v1/telemetry/rum", "the call sends POST"),
        ("fetch(apiPath('/telemetry/rum'), init)", "GET /api/v1/telemetry/rum", "not an object literal"),
        ("fetch(apiPath('/telemetry/rum'), { ...init })", "GET /api/v1/telemetry/rum", "a spread"),
        ("fetch(apiPath('/telemetry/rum'), { method })", "GET /api/v1/telemetry/rum", "not a string literal"),
    ],
)
def test_the_annotated_method_must_be_the_method_the_call_sends(
    tmp_path: Path, call: str, annotation: str, message: str
) -> None:
    module = f"async function site() {{\n  // wire: '{annotation}'\n  return {call};\n}}\n"
    project = _tree(tmp_path, {"client.ts": module})

    sites = wire_raw.find_sites(project, OPS, [tmp_path / "client.ts"])

    assert len(sites) == 1 and not sites[0].bound
    assert any(message in error for error in sites[0].errors), sites[0].errors


def test_a_local_api_path_url_binds_its_cast(tmp_path: Path) -> None:
    module = (
        "import type { Health } from './types';\n"
        "async function read(): Promise<Health> {\n"
        "  // wire: 'GET /api/v1/health'\n"
        "  const url = apiPath('/health');\n"
        "  const res = await fetch(url);\n"
        "  return (await res.json()) as Health;\n"
        "}\n"
        "export function apiPath(path: string): string {\n  return path;\n}\n"
    )
    project = _tree(tmp_path, {"client.ts": module, "types.ts": TYPES_MODULE})
    pairs = [wire.Pair("response", (str(tmp_path / "types.ts"), "Health"), "ApiResponse", "HealthResponse", "x")]

    sites = wire_raw.find_sites(project, OPS, [tmp_path / "client.ts"])
    wire.bind_sites(project, sites, OPS, pairs)

    assert [(s.kind, s.helper) for s in sites] == [("raw", "apiPath"), ("cast", "json")]
    assert all(s.bound and s.operation == "GET /api/v1/health" for s in sites), [s.errors for s in sites]


def test_a_schema_name_binds_alone_only_from_a_coverage_file(tmp_path: Path) -> None:
    module = (
        "import type { Borrower360 as Covered } from './types';\n"
        "interface Borrower360 { phantom: string }\n"
        "export const api = {\n"
        "  local: (id: string) => getJson<Borrower360>(`/api/borrowers/${id}`),\n"
        "  covered: (id: string) => getJson<Covered>(`/api/borrowers/${id}`),\n"
        "};\n"
    )
    project = _tree(tmp_path, {"route.ts": module, "types.ts": TYPES_MODULE})

    sites = wire_raw.find_sites(project, OPS, [tmp_path / "route.ts"])
    wire.bind_sites(project, sites, OPS, [])
    by_name = {site.name: site for site in sites}

    assert by_name["covered"].bound
    assert not by_name["local"].bound
    assert "is not bound to GET /api/v1/borrowers/{borrower_id}" in by_name["local"].errors[0]


def test_site_keys_name_the_enclosing_property_with_an_ordinal(planted: dict[str, wire.Site]) -> None:
    assert planted["borrower:transport"].key.endswith("client.ts:borrower:1")
    assert planted["annotated:cast"].key.endswith("client.ts:annotated:2")


def test_exemptions_are_shrink_only(planted: dict[str, wire.Site]) -> None:
    sites = list(planted.values())
    generic = planted["generic:transport"].key
    bound = planted["borrower:transport"].key
    raw = planted["bare:raw"].key

    ledger = wire.account(sites, {generic: "reason", bound: "stale", raw: "never", "nowhere.ts:x:1": "gone"})

    assert [s.key for s in ledger.exempt] == [generic]
    assert sorted(ledger.stale) == sorted([bound, raw, "nowhere.ts:x:1"])


# ------------------------------------------------------------- source scanning


def test_classify_separates_comments_strings_templates_and_regexes() -> None:
    text = "const a = '//x'; // c\nconst r = /[/]+/g; const t = `a${b ? '}' : `c${d}`}e`;\n"
    kinds = wire.classify(text)
    code = source.code_only(text, kinds)

    assert "// c" not in code and "'//x'" in code
    assert kinds[text.index("[/]")] == "r"
    assert kinds[text.index("`a")] == "t" and kinds[text.index("b ?")] == "c"
    assert kinds[text.rindex("e`")] == "t"


def test_the_generic_parser_balances_nested_brackets_and_arrows() -> None:
    text = "postJson<Array<{ a: () => void }>, B>("
    args, after = wire.parse_type_args(text, wire.classify(text), text.index("<"))

    assert args == ["Array<{ a: () => void }>", "B"]
    assert text[after] == "("
