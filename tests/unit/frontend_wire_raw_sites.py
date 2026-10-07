"""Raw network sites for the wire-contract parser (quality-04 item 4(b)).

The other half of tests/unit/frontend_wire_contract.py's site walk: raw
``fetch`` / ``navigator.sendBeacon`` calls (bare, or qualified by
``window.`` / ``globalThis.`` / ``self.``), their ``.json() as H`` casts and
the boot module's ``BOOT_READS``.

A raw call is a site when its URL argument mentions ``apiPath(``, holds a
string or template literal naming an ``/api`` path, or names a module-level
const that does. Every other ``apiPath(`` call in scope (a URL built into a
local first, an EventSource) is a site of its own. Each site needs the
``// wire:`` line directly above it; the annotation must equal the key its
URL resolves to and, for fetch / sendBeacon, the method the call sends. A URL
or method the static rules cannot resolve fails the site ("raw URL not
statically resolvable"): nothing that names the API is skipped. A bare
variable URL (``fetch(url)`` with a parameter) is not a site.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from pathlib import Path

from tests.unit.frontend_wire_contract import (
    WIRE_ANNOTATION,
    Operation,
    Part,
    Site,
    Span,
    _string_literal,
    _template_end,
    enclosing_span,
    function_spans,
    match_operation,
    normalize,
    path_candidates,
    split_ternary,
    template_parts,
    transport_sites,
)
from tests.unit.frontend_wire_source import (
    Module,
    Project,
    Unresolvable,
    classify,
    line_of,
    match_bracket,
    parse_call_args,
)

RAW_CALL = re.compile(r"(?<![\w$.])(?:(?:window|globalThis|self)\.)?(fetch|navigator\.sendBeacon)\s*\(")
API_PATH_CALL = re.compile(r"(?<![\w$.])apiPath\s*\(")
CAST = re.compile(r"\.json\(\)\s*\)?\s*as\s+([A-Za-z_$][\w$]*(?:\[\])?)")
UNRESOLVABLE = "raw URL not statically resolvable"
# `/api` closing a literal or followed by `/`, `?` or `#`: an API path, never `/apiary`.
_API_TEXT = re.compile(r"/api(?=[/?#'\"`]|$)")
_IDENTIFIER = re.compile(r"(?<![\w$.])[A-Za-z_$][\w$]*")
_SCAN = re.compile(r"getJson|postJson|putJson|patchJson|deleteJson|fetch|sendBeacon|apiPath|BOOT_READS|\.json\(\)")


def find_sites(project: Project, operations: dict[str, Operation], files: Iterable[Path]) -> list[Site]:
    """Every transport, raw, cast and boot site in ``files``, keyed by function and ordinal."""

    sites: list[Site] = []
    for path in files:
        module = project.module(path)
        if not _SCAN.search(module.code):
            continue
        spans = function_spans(module)
        sites += list(transport_sites(project, module, spans, operations))
        sites += list(raw_sites(project, module, spans, operations))
    counts: dict[tuple[str, str], int] = {}
    for site in sites:
        if site.kind == "boot":
            site.ordinal = 1
            continue
        counts[(site.file, site.name)] = counts.get((site.file, site.name), 0) + 1
        site.ordinal = counts[(site.file, site.name)]
    return sites


# --------------------------------------------------------------------------
# Is this URL an API URL, and which client paths can it produce?
# --------------------------------------------------------------------------


def _unparen(expr: str) -> str:
    expr = expr.strip()
    while expr.startswith("(") and expr.endswith(")"):
        if match_bracket(expr, classify(expr), 0) != len(expr) - 1:
            break
        expr = expr[1:-1].strip()
    return expr


def api_const(project: Project, path: Path, name: str) -> bool:
    """True when ``name`` is a module-level const (local or imported by name) holding an /api literal."""

    homes: list[tuple[Path, str]] = [(path, name)]
    imported = project.module(path).imports().get(name)
    if imported is not None:
        target = project.resolve_specifier(path, imported[0])
        if target is not None:
            homes.append((target, imported[1]))
    for home, declared in homes:
        pattern = rf"(?m)^\s*(?:export\s+)?const\s+{re.escape(declared)}\s*(?::[^=\n]+)?=\s*['\"`]/api(?=[/?#'\"`])"
        if re.search(pattern, project.module(home).code):
            return True
    return False


def is_api_url(project: Project, path: Path, url: str) -> bool:
    """Does the URL argument name the API: apiPath(, an /api literal, or an /api const?"""

    if not url:
        return False
    kinds = classify(url)
    if any(kinds[m.start()] == "c" for m in API_PATH_CALL.finditer(url)):
        return True
    if any(kinds[m.start()] in {"s", "t"} for m in _API_TEXT.finditer(url)):
        return True
    return any(kinds[m.start()] == "c" and api_const(project, path, m.group(0)) for m in _IDENTIFIER.finditer(url))


def api_path_argument(expr: str) -> str | None:
    """The single argument of a whole-expression ``apiPath(<arg>)``, else None."""

    expr = _unparen(expr)
    m = re.match(r"apiPath\s*\(", expr)
    if m is None:
        return None
    kinds = classify(expr)
    lparen = m.end() - 1
    if match_bracket(expr, kinds, lparen) != len(expr) - 1:
        return None
    args, _ = parse_call_args(expr, kinds, lparen)
    if len(args) != 1:
        raise Unresolvable("apiPath takes exactly one argument")
    return args[0]


def plus_operands(expr: str) -> list[str]:
    """The operands of a top-level binary ``+`` chain (template literals skipped whole)."""

    kinds = classify(expr)
    depth = 0
    cuts: list[int] = []
    k = 0
    while k < len(expr):
        ch = expr[k]
        if kinds[k] == "t" and ch == "`":
            k = _template_end(expr, kinds, k) + 1
            continue
        if kinds[k] == "c":
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif (
                ch == "+"
                and depth == 0
                and expr[k + 1 : k + 2] not in ("+", "=")
                and expr[k - 1 : k] != "+"
                and re.search(r"[\w$)\]'\"`]\s*$", expr[:k])
            ):
                cuts.append(k)
        k += 1
    bounds = [-1, *cuts, len(expr)]
    return [expr[start + 1 : end].strip() for start, end in zip(bounds, bounds[1:], strict=False)]


def _literal_head(operand: str) -> str | None:
    """The leading text of a string or template literal operand, else None."""

    literal = _string_literal(operand)
    if literal is not None:
        return literal
    operand = operand.strip()
    if operand.startswith("`") and operand.endswith("`"):
        parts = template_parts(operand)
        return parts[0][1] if parts and parts[0][0] == "lit" else ""
    return None


def url_candidates(project: Project, path: Path, expr: str, depth: int = 0) -> list[list[Part]]:
    """Every client path a raw URL expression can produce, apiPath( unwrapped."""

    if depth > 4:
        raise Unresolvable("URL expression nests too deeply")
    expr = _unparen(expr)
    ternary = split_ternary(expr)
    if ternary is not None:
        return url_candidates(project, path, ternary[1], depth + 1) + url_candidates(
            project, path, ternary[2], depth + 1
        )
    operands = plus_operands(expr)
    if len(operands) > 1:
        suffix = _literal_head(operands[1])
        if suffix is None or not suffix.startswith(("?", "#")):
            raise Unresolvable("a `+` may only append a '?' or '#' literal to the path")
        return url_candidates(project, path, operands[0], depth + 1)
    inner = api_path_argument(expr)
    if inner is not None:
        return path_candidates(project, path, inner)
    if expr.startswith("`") and expr.endswith("`"):
        parts = template_parts(expr)
        head = api_path_argument(parts[0][1]) if parts and parts[0][0] == "ph" else None
        if head is not None:
            return [candidate + parts[1:] for candidate in path_candidates(project, path, head)]
    return path_candidates(project, path, expr)


def call_method(helper: str, args: list[str]) -> str:
    """The HTTP method a raw call sends: sendBeacon POSTs; fetch reads its init literal."""

    if helper == "navigator.sendBeacon":
        return "POST"
    if len(args) < 2:
        return "GET"
    init = _unparen(args[1])
    kinds = classify(init)
    if not init.startswith("{") or match_bracket(init, kinds, 0) != len(init) - 1:
        raise Unresolvable("the fetch init is not an object literal")
    entries, _ = parse_call_args(init, kinds, 0)
    method = "GET"
    for entry in entries:
        if entry.startswith("..."):
            raise Unresolvable("a spread in the fetch init can carry a method")
        m = re.fullmatch(r"(['\"]?)method\1\s*(?::\s*([\s\S]+))?", entry)
        if m is None:
            continue
        value = _string_literal(m.group(2) or "")
        if value is None:
            raise Unresolvable("the fetch init method is not a string literal")
        method = value.upper()
    return method


# --------------------------------------------------------------------------
# Sites.
# --------------------------------------------------------------------------


def annotation_above(module: Module, pos: int) -> tuple[str, str] | None:
    lines = module.text.splitlines()
    index = line_of(module.text, pos) - 2
    if index < 0:
        return None
    m = WIRE_ANNOTATION.match(lines[index])
    return (m.group(1), m.group(2)) if m else None


def check_annotation(
    project: Project,
    module: Module,
    pos: int,
    url: str,
    method: str | None,
    operations: dict[str, Operation],
    errors: list[str],
) -> str | None:
    """The annotated key when it is an operation, matches the sent method and equals the URL's key."""

    annotation = annotation_above(module, pos)
    if annotation is None:
        errors.append("raw site without a `// wire:` annotation on the line above")
        return None
    key = f"{annotation[0]} {annotation[1]}"
    if key not in operations:
        errors.append(f"annotation {key!r} is not an ApiOperations key")
        return None
    if method is not None and method != annotation[0]:
        errors.append(f"annotation method {annotation[0]} but the call sends {method}")
        return None
    try:
        candidates = url_candidates(project, module.path, url)
        keys = {match_operation(operations, annotation[0], normalize(parts)) for parts in candidates}
    except Unresolvable as exc:
        errors.append(f"{UNRESOLVABLE}: {exc}")
        return None
    if keys != {key}:
        errors.append(f"annotation {key!r} but the URL resolves to {sorted(keys)!r}")
        return None
    return key


def _site(module: Module, spans: list[Span], pos: int, kind: str, helper: str) -> tuple[Site, Span | None]:
    span = enclosing_span(spans, pos)
    return Site(module.rel, line_of(module.code, pos), kind, span.name if span else "<module>", helper=helper), span


def _first_argument_end(code: str, kinds: list[str], lparen: int) -> int:
    rparen = match_bracket(code, kinds, lparen)
    depth = 0
    for k in range(lparen + 1, rparen):
        if kinds[k] != "c":
            continue
        if code[k] in "([{":
            depth += 1
        elif code[k] in ")]}":
            depth -= 1
        elif code[k] == "," and depth == 0:
            return k
    return rparen


def raw_sites(project: Project, module: Module, spans: list[Span], operations: dict[str, Operation]) -> Iterator[Site]:
    code, kinds = module.code, module.kinds
    annotated: dict[int, str] = {}
    urls: list[tuple[int, int]] = []
    for m in RAW_CALL.finditer(code):
        if kinds[m.start()] != "c":
            continue
        lparen = m.end() - 1
        args, _ = parse_call_args(code, kinds, lparen)
        url = args[0] if args else ""
        if not is_api_url(project, module.path, url):
            continue
        urls.append((lparen, _first_argument_end(code, kinds, lparen)))
        site, span = _site(module, spans, m.start(), "raw", m.group(1))
        method: str | None = None
        try:
            method = call_method(m.group(1), args)
        except Unresolvable as exc:
            site.errors.append(f"method: {exc}")
        site.operation = check_annotation(project, module, m.start(), url, method, operations, site.errors)
        if site.operation and span is not None:
            annotated[span.start] = site.operation
        yield site
    for m in API_PATH_CALL.finditer(code):
        inside_url = any(start < m.start() < end for start, end in urls)
        if kinds[m.start()] != "c" or inside_url or re.search(r"function\s*$", code[: m.start()]):
            continue
        site, span = _site(module, spans, m.start(), "raw", "apiPath")
        call = code[m.start() : match_bracket(code, kinds, m.end() - 1) + 1]
        site.operation = check_annotation(project, module, m.start(), call, None, operations, site.errors)
        if site.operation and span is not None:
            annotated[span.start] = site.operation
        yield site
    for m in CAST.finditer(code):
        if kinds[m.start()] != "c":
            continue
        site, span = _site(module, spans, m.start(), "cast", "json")
        site.type_args = [m.group(1)]
        site.operation = annotated.get(span.start) if span else None
        if site.operation is None:
            site.errors.append("cast: no annotated raw site in the same function")
        yield site
    yield from _boot_sites(project, module, operations)


def _boot_sites(project: Project, module: Module, operations: dict[str, Operation]) -> Iterator[Site]:
    code, kinds = module.code, module.kinds
    boot = re.search(r"\bconst\s+BOOT_READS\s*=\s*\{", code)
    if boot is None:
        return
    end = match_bracket(code, kinds, boot.end() - 1)
    block_start = boot.end()
    for m in re.finditer(r"(?m)^\s*([A-Za-z_$][\w$]*)\s*:\s*\{\s*url:\s*'([^']*)'", code[block_start:end]):
        pos = block_start + m.start(1)
        site = Site(module.rel, line_of(code, pos), "boot", f"BOOT_READS.{m.group(1)}", helper="fetch")
        site.operation = check_annotation(project, module, pos, f"'{m.group(2)}'", None, operations, site.errors)
        yield site
