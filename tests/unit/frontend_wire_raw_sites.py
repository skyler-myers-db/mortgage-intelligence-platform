"""Raw network sites for the wire-contract parser (quality-04 item 4(b)).

The other half of tests/unit/frontend_wire_contract.py's site walk: raw
``fetch`` / ``navigator.sendBeacon`` calls (bare, ``window.`` /
``globalThis.`` / ``self.``-qualified, or bracket-qualified such as
``window['fetch'](``), ``new EventSource(`` / ``new WebSocket(`` (GET), a
same-module wrapper's call sites, their ``.json() as H`` casts and typed
``.json()`` assignments, and the boot module's ``BOOT_READS``.

A raw call is a site when its URL argument mentions ``apiPath(``, holds a
string or template literal naming an ``/api`` path, or names a path const
that does (function-local consts resolve within their function first). A
same-module function that forwards a parameter into one of those calls is a
wrapper, and each of its call sites whose argument names the API is a raw
site. Every other ``apiPath(`` call in scope (a URL built into a local first)
is a site of its own, and takes its method from the raw call in the same
function that consumes the local. Each site needs the ``// wire:`` line
directly above it; the annotation must equal the key its URL resolves to
and the method the call sends. A URL or method the static rules cannot
resolve fails the site ("raw URL not statically resolvable", "method not
statically known"): nothing that names the API is skipped. A bare variable
URL (``fetch(url)`` with a parameter) is not a site.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from tests.unit.frontend_wire_contract import (
    KEYWORDS,
    WIRE_ANNOTATION,
    Operation,
    Part,
    Site,
    Span,
    _string_literal,
    _template_end,
    const_scope,
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
    ConstScope,
    Module,
    Project,
    Unresolvable,
    classify,
    line_of,
    match_bracket,
    parse_call_args,
)

# A callee: identifiers joined by `.` or by a bracketed string literal (`window['fetch']`).
_CALLEE = re.compile(
    r"(?<![\w$.\]])(?P<new>new\s+)?"
    r"(?P<chain>[A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*|\s*\[\s*(?:'[\w$]+'|\"[\w$]+\")\s*\])*)\s*\("
)
_GLOBALS = ("window.", "globalThis.", "self.")
RAW_HELPERS = frozenset({"fetch", "navigator.sendBeacon"})
CONSTRUCTED = frozenset({"EventSource", "WebSocket"})
API_PATH_CALL = re.compile(r"(?<![\w$.])apiPath\s*\(")
CAST = re.compile(r"\.json\(\)\s*\)?\s*as\s+([A-Za-z_$][\w$]*(?:\[\])?)")
# `const x: H = await res.json()`, and `x = await res.json()` for a local declared `let x: H`.
TYPED_JSON = re.compile(
    r"\b(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*:\s*([A-Za-z_$][\w$]*(?:\[\])?)\s*=\s*\(?\s*await\s+[A-Za-z_$][\w$.]*\.json\(\)"
)
ASSIGNED_JSON = re.compile(r"(?<![\w$.:])([A-Za-z_$][\w$]*)\s*=\s*\(?\s*await\s+[A-Za-z_$][\w$.]*\.json\(\)")
UNRESOLVABLE = "raw URL not statically resolvable"
UNKNOWN_METHOD = "method not statically known"
# `/api` closing a literal or followed by `/`, `?` or `#`: an API path, never `/apiary`.
_API_TEXT = re.compile(r"/api(?=[/?#'\"`]|$)")
_IDENTIFIER = re.compile(r"(?<![\w$.])[A-Za-z_$][\w$]*")
_SCAN = re.compile(
    r"getJson|postJson|putJson|patchJson|deleteJson|fetch|sendBeacon|EventSource|WebSocket|apiPath|BOOT_READS|\.json\(\)"
)


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
# Raw calls: who sends a request.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RawCall:
    start: int
    helper: str  # "fetch" | "navigator.sendBeacon" | "EventSource" | "WebSocket" | a wrapper's name
    lparen: int


def callee_name(chain: str) -> str:
    """``window['fetch']`` -> ``fetch``; ``self.navigator["sendBeacon"]`` -> ``navigator.sendBeacon``."""

    name = re.sub(r"\[(['\"])([\w$]+)\1\]", r".\2", re.sub(r"\s+", "", chain))
    for prefix in _GLOBALS:
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def raw_calls(module: Module) -> list[RawCall]:
    """Every fetch / sendBeacon call and EventSource / WebSocket construction, in source order."""

    code, kinds = module.code, module.kinds
    found: list[RawCall] = []
    for m in _CALLEE.finditer(code):
        chain = m.group("chain")
        if kinds[m.start()] != "c" or not re.search(r"fetch|sendBeacon|EventSource|WebSocket", chain):
            continue
        helper = callee_name(chain)
        constructed = m.group("new") is not None
        if (helper in RAW_HELPERS and not constructed) or (helper in CONSTRUCTED and constructed):
            found.append(RawCall(m.start(), helper, m.end() - 1))
    return found


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
    """True when ``name`` is a const (local or imported by name) holding an /api literal."""

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


def url_candidates(
    project: Project, path: Path, expr: str, depth: int = 0, scope: ConstScope | None = None
) -> list[list[Part]]:
    """Every client path a raw URL expression can produce, apiPath( unwrapped."""

    if depth > 4:
        raise Unresolvable("URL expression nests too deeply")
    expr = _unparen(expr)
    ternary = split_ternary(expr)
    if ternary is not None:
        return url_candidates(project, path, ternary[1], depth + 1, scope) + url_candidates(
            project, path, ternary[2], depth + 1, scope
        )
    operands = plus_operands(expr)
    if len(operands) > 1:
        suffix = _literal_head(operands[1])
        if suffix is None or not suffix.startswith(("?", "#")):
            raise Unresolvable("a `+` may only append a '?' or '#' literal to the path")
        return url_candidates(project, path, operands[0], depth + 1, scope)
    inner = api_path_argument(expr)
    if inner is not None:
        return path_candidates(project, path, inner, scope=scope)
    if expr.startswith("`") and expr.endswith("`"):
        parts = template_parts(expr)
        head = api_path_argument(parts[0][1]) if parts and parts[0][0] == "ph" else None
        if head is not None:
            return [candidate + parts[1:] for candidate in path_candidates(project, path, head, scope=scope)]
    return path_candidates(project, path, expr, scope=scope)


def call_method(helper: str, args: list[str]) -> str:
    """The HTTP method a raw call sends: sendBeacon POSTs, EventSource / WebSocket GET; fetch reads its init."""

    if helper == "navigator.sendBeacon":
        return "POST"
    if helper in CONSTRUCTED:
        return "GET"
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
# Wrappers and apiPath-built locals: where the method comes from.
# --------------------------------------------------------------------------


def _parameters(module: Module, span: Span) -> list[str]:
    """The parameter names of the function at ``span`` (destructured parameters skipped)."""

    code, kinds = module.code, module.kinds
    lparen = next((k for k in range(span.start, span.end) if code[k] == "(" and kinds[k] == "c"), None)
    if lparen is None:
        return []
    params, _ = parse_call_args(code, kinds, lparen)
    names: list[str] = []
    for param in params:
        m = re.match(r"\s*(?:\.\.\.)?([A-Za-z_$][\w$]*)", param)
        names.append(m.group(1) if m else "")
    return names


@dataclass(frozen=True)
class Wrapper:
    index: int  # which argument is the URL
    method: str | None  # None: not statically known
    start: int
    end: int


def wrappers(module: Module, spans: list[Span], calls: list[RawCall]) -> dict[str, Wrapper]:
    """Same-module functions that forward a parameter into a raw call, by name."""

    code, kinds = module.code, module.kinds
    found: dict[str, Wrapper] = {}
    for call in calls:
        span = enclosing_span(spans, call.start)
        if span is None or span.name in KEYWORDS:
            continue
        args, _ = parse_call_args(code, kinds, call.lparen)
        params = _parameters(module, span)
        first = _unparen(args[0]) if args else ""
        if first and first in params:
            try:
                method: str | None = call_method(call.helper, args)
            except Unresolvable:
                method = None
            found.setdefault(span.name, Wrapper(params.index(first), method, span.start, span.end))
    return found


def wrapper_calls(module: Module, found: dict[str, Wrapper]) -> list[tuple[RawCall, Wrapper]]:
    """Every call of a same-module wrapper outside its own body."""

    code, kinds = module.code, module.kinds
    out: list[tuple[RawCall, Wrapper]] = []
    for name, wrapper in found.items():
        for m in re.finditer(rf"(?<![\w$.]){re.escape(name)}\s*\(", code):
            if kinds[m.start()] != "c" or wrapper.start <= m.start() <= wrapper.end:
                continue
            if re.search(r"\bfunction\s*$", code[: m.start()]):
                continue
            out.append((RawCall(m.start(), name, m.end() - 1), wrapper))
    return sorted(out, key=lambda pair: pair[0].start)


def consumed_method(module: Module, spans: list[Span], pos: int, calls: list[RawCall]) -> str:
    """The method of the raw call, in the same function, that sends the local this apiPath( builds."""

    code, kinds = module.code, module.kinds
    assigned = re.search(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=;]+)?=\s*(?:`\$\{\s*)?$", code[:pos])
    if assigned is None:
        raise Unresolvable(f"{UNKNOWN_METHOD}: the apiPath( URL is not held in a local")
    name = assigned.group(1)
    span = enclosing_span(spans, pos)
    low, high = (span.start, span.end) if span else (0, len(code))
    methods: set[str] = set()
    for call in calls:
        if not low <= call.start <= high or enclosing_span(spans, call.start) is not span:
            continue
        args, _ = parse_call_args(code, kinds, call.lparen)
        if args and _unparen(args[0]) == name:
            try:
                methods.add(call_method(call.helper, args))
            except Unresolvable as exc:
                raise Unresolvable(f"{UNKNOWN_METHOD}: {exc}") from exc
    if len(methods) != 1:
        raise Unresolvable(f"{UNKNOWN_METHOD}: {len(methods)} raw calls send `{name}` in this function")
    return methods.pop()


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
    scope: ConstScope | None = None,
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
        candidates = url_candidates(project, module.path, url, scope=scope)
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


def _url_sites(
    project: Project,
    module: Module,
    spans: list[Span],
    operations: dict[str, Operation],
    calls: list[RawCall],
    urls: list[tuple[int, int]],
    annotated: dict[int, str],
) -> Iterator[Site]:
    """Raw calls and wrapper calls whose URL argument names the API."""

    code, kinds = module.code, module.kinds
    targets = [(call, None) for call in calls] + wrapper_calls(module, wrappers(module, spans, calls))
    for call, wrapper in sorted(targets, key=lambda pair: pair[0].start):
        args, _ = parse_call_args(code, kinds, call.lparen)
        index = wrapper.index if wrapper else 0
        url = args[index] if len(args) > index else ""
        if not is_api_url(project, module.path, url):
            continue
        # A wrapper call's whole argument list is its URL span (an apiPath( inside is this site).
        url_end = match_bracket(code, kinds, call.lparen) if wrapper else _first_argument_end(code, kinds, call.lparen)
        urls.append((call.lparen, url_end))
        site, span = _site(module, spans, call.start, "raw", call.helper)
        method: str | None = None
        try:
            if wrapper is None:
                method = call_method(call.helper, args)
            elif wrapper.method is None:
                raise Unresolvable(f"{UNKNOWN_METHOD}: the wrapper's own call does not name one")
            else:
                method = wrapper.method
        except Unresolvable as exc:
            site.errors.append(f"method: {exc}")
        scope = const_scope(spans, call.start)
        site.operation = check_annotation(project, module, call.start, url, method, operations, site.errors, scope)
        if site.operation and span is not None:
            annotated[span.start] = site.operation
        yield site


def _cast_sites(module: Module, spans: list[Span], annotated: dict[int, str]) -> Iterator[Site]:
    """``.json() as H`` casts and typed ``.json()`` assignments, bound through their function's raw site."""

    code, kinds = module.code, module.kinds
    found: list[tuple[int, str]] = [(m.start(), m.group(1)) for m in CAST.finditer(code) if kinds[m.start()] == "c"]
    typed = [m for m in TYPED_JSON.finditer(code) if kinds[m.start()] == "c" and not _cast_follows(code, m.end())]
    found += [(m.start(), m.group(1)) for m in typed]
    for m in ASSIGNED_JSON.finditer(code):
        if kinds[m.start()] != "c" or _cast_follows(code, m.end()) or any(t.start() <= m.start() < t.end() for t in typed):
            continue
        span = enclosing_span(spans, m.start())
        low, high = (span.start, span.end) if span else (0, len(code))
        declared = re.search(rf"\b(?:let|var)\s+{re.escape(m.group(1))}\s*:\s*([A-Za-z_$][\w$]*(?:\[\])?)", code[low:high])
        if declared is not None:
            found.append((m.start(), declared.group(1)))
    for pos, type_name in sorted(found):
        site, span = _site(module, spans, pos, "cast", "json")
        site.type_args = [type_name]
        site.operation = annotated.get(span.start) if span else None
        if site.operation is None:
            site.errors.append("cast: no annotated raw site in the same function")
        yield site


def _cast_follows(code: str, end: int) -> bool:
    return re.match(r"\s*\)?\s*as\b", code[end:]) is not None


def raw_sites(project: Project, module: Module, spans: list[Span], operations: dict[str, Operation]) -> Iterator[Site]:
    code, kinds = module.code, module.kinds
    annotated: dict[int, str] = {}
    urls: list[tuple[int, int]] = []
    calls = raw_calls(module)
    yield from _url_sites(project, module, spans, operations, calls, urls, annotated)
    for m in API_PATH_CALL.finditer(code):
        inside_url = any(start < m.start() < end for start, end in urls)
        if kinds[m.start()] != "c" or inside_url or re.search(r"function\s*$", code[: m.start()]):
            continue
        site, span = _site(module, spans, m.start(), "raw", "apiPath")
        call = code[m.start() : match_bracket(code, kinds, m.end() - 1) + 1]
        method: str | None = None
        try:
            method = consumed_method(module, spans, m.start(), calls)
        except Unresolvable as exc:
            site.errors.append(f"method: {exc}")
        scope = const_scope(spans, m.start())
        site.operation = check_annotation(project, module, m.start(), call, method, operations, site.errors, scope)
        if site.operation and span is not None:
            annotated[span.start] = site.operation
        yield site
    yield from _cast_sites(module, spans, annotated)
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
