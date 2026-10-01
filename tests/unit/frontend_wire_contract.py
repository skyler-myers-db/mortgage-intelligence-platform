"""Static parser behind tests/unit/test_frontend_wire_contract.py.

quality-04 items 1-2 / stack-v1, decision D-api-types-a3 P1. Pure Python, no
Node and no git: it walks frontend/src with Path.rglob, so a ``git archive``
tree works. It answers three questions for the wire-contract pytest:

1. Which hand types are schema-named (a ResponseSchemas/RequestSchemas key)
   and therefore need a pair in a ``wireContract.<domain>.check.ts`` file.
2. Which ApiOperations key every frontend network call site resolves to, and
   whether its type arguments are bound to that operation's ``ok`` / ``body``
   (transport calls here; raw fetch / sendBeacon / apiPath sites, their casts
   and BOOT_READS in tests/unit/frontend_wire_raw_sites.py, whose
   ``find_sites`` walks both).
3. Which pairs the check files declare, resolved through their own imports.

Schema keys come from tools.gen_api_types over the committed OpenAPI
baseline; operation keys come from the ApiOperations block of the committed
api.gen.ts (kept current by test_generated_api_types_are_current), never from
hand types.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from tests.unit.frontend_wire_source import (
    EXTENSIONS,
    ROOT,
    SRC,
    Module,
    Project,
    Unresolvable,
    classify,
    line_of,
    match_bracket,
    parse_call_args,
    parse_type_args,
    rel_path,
)
from tools.gen_api_types import request_closure, response_closure

API_GEN = SRC / "types" / "api.gen.ts"
BASELINE = ROOT / "tests" / "fixtures" / "openapi_baseline.json"
CHECK_HELPER = SRC / "types" / "wireContract.check.ts"

TRANSPORT = {
    "getJson": "GET",
    "getJsonWithHeaders": "GET",
    "postJson": "POST",
    "putJson": "PUT",
    "patchJson": "PATCH",
    "deleteJson": "DELETE",
}
BODY_HELPERS = frozenset({"postJson", "putJson", "patchJson"})
KEYWORDS = frozenset(
    {"if", "for", "while", "switch", "catch", "return", "typeof", "function", "new", "await", "with", "do", "else"}
)
WIRE_ANNOTATION = re.compile(r"^\s*//\s*wire:\s*'([A-Z]+) (/api/v1/[^']*)'\s*$")
IDENT_ARG = re.compile(r"^([A-Za-z_$][\w$]*)(\[\])?$")


# --------------------------------------------------------------------------
# Wire vocabulary: schema keys and ApiOperations.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Operation:
    key: str
    method: str
    path: str
    ok: tuple[tuple[str, bool], ...]
    body: tuple[str, ...]

    @property
    def segments(self) -> tuple[str, ...]:
        return tuple(self.path.strip("/").split("/"))

    def single_ok(self) -> tuple[str, bool] | None:
        return self.ok[0] if len(self.ok) == 1 else None

    def single_body(self) -> str | None:
        return self.body[0] if len(self.body) == 1 else None


_OP_HEAD = re.compile(r'^  "([A-Z]+) (/[^"]*)": \{$', re.M)
_OK_LINE = re.compile(r"^    ok: (.*);$", re.M)
_BODY_LINE = re.compile(r"^    body\??: (.*);$", re.M)
_RESPONSE_REF = re.compile(r"ResponseSchemas\['([A-Za-z0-9_]+)'\](\[\])?")
_REQUEST_REF = re.compile(r"RequestSchemas\['([A-Za-z0-9_]+)'\]")


def parse_operations(api_gen_text: str) -> dict[str, Operation]:
    """Every ``ApiOperations`` key with its ok and body schema names."""

    start = api_gen_text.index("export interface ApiOperations {")
    block = api_gen_text[start:]
    heads = list(_OP_HEAD.finditer(block))
    operations: dict[str, Operation] = {}
    for index, head in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(block)
        chunk = block[head.start() : end]
        ok = _OK_LINE.search(chunk)
        body = _BODY_LINE.search(chunk)
        ok_alts = tuple((m.group(1), bool(m.group(2))) for m in _RESPONSE_REF.finditer(ok.group(1) if ok else ""))
        body_names = tuple(m.group(1) for m in _REQUEST_REF.finditer(body.group(1) if body else ""))
        key = f"{head.group(1)} {head.group(2)}"
        operations[key] = Operation(key, head.group(1), head.group(2), ok_alts, body_names)
    return operations


def schema_keys() -> tuple[set[str], set[str]]:
    spec = json.loads(BASELINE.read_text(encoding="utf-8"))
    return response_closure(spec), request_closure(spec)


# --------------------------------------------------------------------------
# Paths: expression -> template parts -> normalized client path -> operation.
# --------------------------------------------------------------------------

Part = tuple[str, str]  # ("lit", text) | ("ph", expression)


def template_parts(literal: str) -> list[Part]:
    """Split a template literal (with its backticks) into literal and ${} parts."""

    kinds = classify(literal)
    parts: list[Part] = []
    buf = ""
    k = 1
    last = len(literal) - 1
    while k < last:
        if literal.startswith("${", k) and kinds[k] == "t":
            if buf:
                parts.append(("lit", buf))
                buf = ""
            close = _placeholder_close(literal, kinds, k + 2)
            parts.append(("ph", literal[k + 2 : close].strip()))
            k = close + 1
            continue
        buf += literal[k]
        k += 1
    if buf:
        parts.append(("lit", buf))
    return parts


def _placeholder_close(text: str, kinds: list[str], k: int) -> int:
    """Index of the ``}`` closing the ``${`` whose code starts at ``k``."""

    while k < len(text):
        if kinds[k] == "t" and text[k] == "`":
            k = _template_end(text, kinds, k) + 1
            continue
        if kinds[k] == "t" and text[k] == "}":
            return k
        k += 1
    raise Unresolvable("unterminated ${} placeholder")


def _template_end(text: str, kinds: list[str], k: int) -> int:
    """Index of the backtick closing the template opened at ``k``."""

    k += 1
    while k < len(text):
        if text.startswith("${", k) and kinds[k] == "t":
            k = _placeholder_close(text, kinds, k + 2) + 1
            continue
        if text[k] == "`" and kinds[k] == "t":
            return k
        k += 1
    raise Unresolvable("unterminated template literal")


def split_ternary(expr: str) -> tuple[str, str, str] | None:
    """``cond ? a : b`` at top level, or None."""

    kinds = classify(expr)
    depth = 0
    question = None
    pending = 0
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
            elif depth == 0 and ch == "?" and expr[k + 1 : k + 2] not in ("?", ".") and expr[k - 1 : k] != "?":
                question = k if question is None else question
                pending += 1
            elif depth == 0 and ch == ":" and pending:
                pending -= 1
                if pending == 0 and question is not None:
                    return expr[:question].strip(), expr[question + 1 : k].strip(), expr[k + 1 :].strip()
        k += 1
    return None


def _string_literal(expr: str) -> str | None:
    m = re.fullmatch(r"'([^'\\]*)'|\"([^\"\\]*)\"", expr.strip())
    if m is None:
        return None
    return m.group(1) if m.group(1) is not None else m.group(2)


def _return_expressions(module: Module, name: str) -> list[str]:
    """Every ``return <expr>`` of the same-module function ``name``."""

    m = re.search(rf"\bfunction\s+{re.escape(name)}\s*\(", module.code)
    if m is None:
        raise Unresolvable(f"no same-module function {name}")
    lparen = m.end() - 1
    rparen = match_bracket(module.code, module.kinds, lparen)
    brace = module.code.index("{", rparen)
    end = match_bracket(module.code, module.kinds, brace)
    body = module.code[brace + 1 : end]
    returns = [r.group(1) for r in re.finditer(r"\breturn\s+([\s\S]*?);", body)]
    if not returns:
        raise Unresolvable(f"{name} has no return")
    return returns


def path_candidates(project: Project, path: Path, expr: str, depth: int = 0) -> list[list[Part]]:
    """Every client path ``expr`` can produce, as template parts."""

    if depth > 4:
        raise Unresolvable("path expression nests too deeply")
    expr = expr.strip()
    while expr.startswith("(") and expr.endswith(")"):
        kinds = classify(expr)
        if match_bracket(expr, kinds, 0) != len(expr) - 1:
            break
        expr = expr[1:-1].strip()
    literal = _string_literal(expr)
    if literal is not None:
        return [[("lit", literal)]]
    ternary = split_ternary(expr)
    if ternary is not None:
        return path_candidates(project, path, ternary[1], depth + 1) + path_candidates(
            project, path, ternary[2], depth + 1
        )
    if expr.startswith("`") and expr.endswith("`"):
        return [_substitute_consts(project, path, template_parts(expr))]
    call = re.fullmatch(r"([A-Za-z_$][\w$]*)\s*\(([\s\S]*)\)", expr)
    if call is not None:
        if call.group(1) == "analyticsPath":
            kinds = classify(expr)
            args, _ = parse_call_args(expr, kinds, expr.index("("))
            scope = _string_literal(args[0]) if args else None
            if scope is None:
                raise Unresolvable("analyticsPath needs a literal scope")
            return [[("lit", f"/api/analytics/{scope}")]]
        module = project.module(path)
        found: list[list[Part]] = []
        for returned in _return_expressions(module, call.group(1)):
            found += path_candidates(project, path, returned, depth + 1)
        return found
    if re.fullmatch(r"[A-Za-z_$][\w$]*", expr):
        const = project.resolve_const(path, expr)
        if const is None:
            raise Unresolvable(f"{expr} is not a module-level path const")
        return [[("lit", const)]]
    raise Unresolvable(f"unsupported path expression: {expr[:60]}")


def _substitute_consts(project: Project, path: Path, parts: list[Part]) -> list[Part]:
    out: list[Part] = []
    for kind, value in parts:
        if kind == "ph" and re.fullmatch(r"[A-Za-z_$][\w$]*", value):
            const = project.resolve_const(path, value)
            if const is not None:
                out.append(("lit", const))
                continue
        out.append((kind, value))
    return out


def normalize(parts: list[Part]) -> str:
    """Mirror lib/apiPaths.ts apiPath() over template parts; ${} becomes {param}."""

    trimmed: list[Part] = []
    for kind, value in parts:
        if kind == "lit":
            cut = min((i for i in (value.find("?"), value.find("#")) if i >= 0), default=-1)
            if cut >= 0:
                if value[:cut]:
                    trimmed.append(("lit", value[:cut]))
                break
        trimmed.append((kind, value))
    segments: list[list[Part]] = [[]]
    for kind, value in trimmed:
        if kind == "ph":
            segments[-1].append(("ph", value))
            continue
        pieces = value.split("/")
        for index, piece in enumerate(pieces):
            if index > 0:
                segments.append([])
            if piece:
                segments[-1].append(("lit", piece))
    if segments[0]:
        raise Unresolvable("a client path must start with '/'")
    segments = segments[1:]
    while segments and not segments[-1]:
        segments.pop()
    out: list[str] = []
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        if last and len(segment) > 1 and all(kind == "ph" for kind, _ in segment[1:]):
            segment = segment[:1]
        if len(segment) != 1:
            raise Unresolvable("a ${} inside a path segment")
        kind, value = segment[0]
        out.append("{param}" if kind == "ph" else value)
    joined = "/" + "/".join(out)
    if joined == "/api/v1" or joined.startswith("/api/v1/"):
        return joined
    if joined.startswith("/api/"):
        return "/api/v1" + joined[len("/api") :]
    return "/api/v1" + joined


def match_operation(operations: dict[str, Operation], method: str, client_path: str) -> str:
    """Starlette-style match with static preference; exactly one key must remain."""

    client = tuple(client_path.strip("/").split("/"))
    candidates = [op for op in operations.values() if op.method == method and len(op.segments) == len(client)]
    for index, segment in enumerate(client):
        if segment == "{param}":
            candidates = [op for op in candidates if _is_param(op.segments[index])]
            continue
        static = [op for op in candidates if op.segments[index] == segment]
        candidates = static or [op for op in candidates if _is_param(op.segments[index])]
    if len(candidates) != 1:
        found = ", ".join(sorted(op.key for op in candidates)) or "none"
        raise Unresolvable(f"{method} {client_path} matches {found}")
    return candidates[0].key


def _is_param(segment: str) -> bool:
    return segment.startswith("{") and segment.endswith("}")


def resolve_path(project: Project, path: Path, expr: str, method: str, operations: dict[str, Operation]) -> str:
    keys = {match_operation(operations, method, normalize(parts)) for parts in path_candidates(project, path, expr)}
    if len(keys) != 1:
        raise Unresolvable(f"branches resolve to {sorted(keys)}")
    return keys.pop()


# --------------------------------------------------------------------------
# Enclosing function / property names, for BINDING_EXEMPT keys.
# --------------------------------------------------------------------------

_HEADER = re.compile(
    r"(?:\bfunction\s*\*?\s*([A-Za-z_$][\w$]*)\s*(?:<[^>()]*>)?\s*\()"
    r"|(?:\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s+)?(?:<[^>()]*>\s*)?\()"
    r"|(?:(?<![\w$.?])([A-Za-z_$][\w$]*)\s*:\s*(?:async\s+)?(?:<[^>()]*>\s*)?\()"
)


@dataclass(frozen=True)
class Span:
    name: str
    start: int
    end: int


def function_spans(module: Module) -> list[Span]:
    """Named functions, arrow consts and arrow/method properties with their extents."""

    code, kinds = module.code, module.kinds
    spans: list[Span] = []
    for m in _HEADER.finditer(code):
        if kinds[m.start()] != "c":
            continue
        name = m.group(1) or m.group(2) or m.group(3)
        if name in KEYWORDS:
            continue
        # JSX text (an apostrophe in copy) can unbalance a component body;
        # such a header is skipped. Sites live in plain functions, and the
        # named-site assertions prove their spans resolve.
        try:
            end = _span_end(code, kinds, m.end() - 1, is_function=bool(m.group(1)))
        except Unresolvable:
            continue
        if end is not None:
            spans.append(Span(name, m.start(), end))
    return spans


def _span_end(code: str, kinds: list[str], lparen: int, is_function: bool) -> int | None:
    rparen = match_bracket(code, kinds, lparen)
    k = _skip_return_type(code, kinds, rparen + 1)
    if code.startswith("=>", k):
        k = _skip_space(code, k + 2)
        if k < len(code) and code[k] == "{":
            return match_bracket(code, kinds, k)
        return _expression_end(code, kinds, k)
    if k < len(code) and code[k] == "{" and is_function:
        return match_bracket(code, kinds, k)
    return None


def _skip_space(code: str, k: int) -> int:
    while k < len(code) and code[k].isspace():
        k += 1
    return k


def _skip_return_type(code: str, kinds: list[str], k: int) -> int:
    k = _skip_space(code, k)
    if k >= len(code) or code[k] != ":":
        return k
    depth = 0
    k += 1
    while k < len(code):
        ch = code[k]
        if kinds[k] == "c":
            if depth == 0 and (code.startswith("=>", k) or ch == "{" and code[k - 1 : k] != ":" and _brace_is_body(code, k)):
                return k
            if ch in "<([{":
                depth += 1
            elif ch in ")]}" or (ch == ">" and code[k - 1] != "="):
                depth -= 1
        k += 1
    return k


def _brace_is_body(code: str, k: int) -> bool:
    before = code[:k].rstrip()
    return not before.endswith(":")


def _expression_end(code: str, kinds: list[str], k: int) -> int:
    depth = 0
    while k < len(code):
        if kinds[k] == "c":
            ch = code[k]
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                if depth == 0:
                    return k
                depth -= 1
            elif depth == 0 and ch in ",;":
                return k
        k += 1
    return k


def enclosing_name(spans: list[Span], pos: int) -> str:
    inner = [span for span in spans if span.start <= pos <= span.end]
    return max(inner, key=lambda span: span.start).name if inner else "<module>"


def enclosing_span(spans: list[Span], pos: int) -> Span | None:
    inner = [span for span in spans if span.start <= pos <= span.end]
    return max(inner, key=lambda span: span.start) if inner else None


# --------------------------------------------------------------------------
# Check files: the declared pairs.
# --------------------------------------------------------------------------

_PAIR_LINES = {
    "response_fits": re.compile(r"Expect<WireFits<(ApiResponse|ApiOk)<'([^']+)'>,\s*([A-Za-z_$][\w$]*)>>"),
    "response_phantom": re.compile(r"Expect<NoPhantomKeys<([A-Za-z_$][\w$]*),\s*(ApiResponse|ApiOk)<'([^']+)'>>>"),
    "request_fits": re.compile(r"Expect<WireFits<([A-Za-z_$][\w$]*),\s*(ApiRequest|ApiBody)<'([^']+)'>>>"),
    "request_phantom": re.compile(r"Expect<DeepNoPhantomKeys<([A-Za-z_$][\w$]*),\s*(ApiRequest|ApiBody)<'([^']+)'>>>"),
}


@dataclass(frozen=True)
class Pair:
    side: str  # "response" | "request"
    hand: tuple[str, str]  # (declaring module rel path, declared name)
    wire: str  # "ApiResponse" | "ApiOk" | "ApiRequest" | "ApiBody"
    key: str
    check_file: str


@dataclass
class CheckFiles:
    pairs: list[Pair] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)


def check_files(src: Path = SRC) -> list[Path]:
    return sorted(p for p in (src / "types").glob("wireContract.*.check.ts"))


def parse_check_files(project: Project) -> CheckFiles:
    out = CheckFiles(files=check_files(project.src))
    for path in out.files:
        module = project.module(path)
        rel = module.rel
        halves: dict[tuple[str, tuple[str, str], str, str], set[str]] = {}
        for line in module.code.splitlines():
            for kind, pattern in _PAIR_LINES.items():
                m = pattern.search(line)
                if m is None:
                    continue
                if kind == "response_fits":
                    wire, key, hand = m.group(1), m.group(2), m.group(3)
                else:
                    hand, wire, key = m.group(1), m.group(2), m.group(3)
                declared = project.resolve_type(path, hand)
                if declared is None:
                    out.unresolved.append(f"{rel}: {hand}")
                    continue
                hand_key = (rel_path(declared[0]), declared[1])
                side = "response" if kind.startswith("response") else "request"
                halves.setdefault((side, hand_key, wire, key), set()).add(kind)
        for (side, hand_key, wire, key), kinds in halves.items():
            if len(kinds) == 2:
                out.pairs.append(Pair(side, hand_key, wire, key, rel))
    return out


# --------------------------------------------------------------------------
# Call sites.
# --------------------------------------------------------------------------


@dataclass
class Site:
    file: str
    line: int
    kind: str  # "transport" | "raw" | "cast" | "boot"
    name: str  # enclosing function/property, or the BOOT_READS entry
    ordinal: int = 0
    helper: str = ""
    operation: str | None = None
    type_args: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.file}:{self.name}:{self.ordinal}"

    @property
    def bound(self) -> bool:
        return not self.errors


def scope_files(src: Path = SRC) -> list[Path]:
    """Non-test .ts/.tsx under frontend/src, minus the transport, api.gen and check files."""

    found: list[Path] = []
    for path in sorted(src.rglob("*")):
        if path.suffix not in EXTENSIONS or {"node_modules", "dist"} & set(path.parts):
            continue
        rel = path.relative_to(src).as_posix()
        name = path.name
        if rel.startswith(("test/", "mocks/")) or ".test." in name or ".stories." in name:
            continue
        if rel in {"lib/apiTransport.ts", "types/api.gen.ts"} or re.fullmatch(r"wireContract(\.[\w]+)?\.check\.ts", name):
            continue
        found.append(path)
    return found


_TRANSPORT_CALL = re.compile(r"(?<![\w$.])(getJson|getJsonWithHeaders|postJson|putJson|patchJson|deleteJson)\s*([<(])")


def transport_sites(
    project: Project, module: Module, spans: list[Span], operations: dict[str, Operation]
) -> Iterator[Site]:
    code, kinds = module.code, module.kinds
    for m in _TRANSPORT_CALL.finditer(code):
        if kinds[m.start()] != "c" or re.search(r"function\s*$", code[: m.start()]):
            continue
        helper = m.group(1)
        site = Site(module.rel, line_of(code, m.start()), "transport", enclosing_name(spans, m.start()), helper=helper)
        try:
            if m.group(2) == "<":
                type_args, after = parse_type_args(code, kinds, m.end() - 1)
            else:
                type_args, after = [], m.end() - 1
            site.type_args = type_args
            lparen = _skip_space(code, after)
            if lparen >= len(code) or code[lparen] != "(":
                raise Unresolvable("no call parentheses")
            args, _ = parse_call_args(code, kinds, lparen)
            if not args:
                raise Unresolvable("no path argument")
            site.operation = resolve_path(project, module.path, args[0], TRANSPORT[helper], operations)
        except Unresolvable as exc:
            site.errors.append(f"path: {exc}")
        yield site


# --------------------------------------------------------------------------
# Binding.
# --------------------------------------------------------------------------


def bind_sites(
    project: Project,
    sites: list[Site],
    operations: dict[str, Operation],
    pairs: list[Pair],
) -> None:
    """Append a binding error to every site whose type arguments are not bound."""

    index: dict[tuple[str, tuple[str, str]], set[tuple[str, str]]] = {}
    for pair in pairs:
        index.setdefault((pair.side, pair.hand), set()).add((pair.wire, pair.key))
    for site in sites:
        if site.errors or site.operation is None or site.kind in {"raw", "boot"}:
            continue
        op = operations[site.operation]
        path = Path(site.file) if Path(site.file).is_absolute() else ROOT / site.file
        if site.kind == "transport":
            needs_body = site.helper in BODY_HELPERS
            expected = 2 if needs_body else 1
            if len(site.type_args) != expected:
                site.errors.append(f"expected {expected} type argument(s), found {len(site.type_args)}")
                continue
        response = site.type_args[0]
        error = _bind_response(project, path, response, op, index)
        if error:
            site.errors.append(error)
        if site.kind == "transport" and site.helper in BODY_HELPERS:
            error = _bind_body(project, path, site.type_args[1], op, index)
            if error:
                site.errors.append(error)


def _declared(project: Project, path: Path, arg: str) -> tuple[tuple[str, str], bool] | str:
    m = IDENT_ARG.match(arg.strip())
    if m is None:
        return f"non-identifier type argument {' '.join(arg.split())[:60]!r}"
    declared = project.resolve_type(path, m.group(1))
    if declared is None:
        return f"{m.group(1)} does not resolve to a declared type"
    return (rel_path(declared[0]), declared[1]), bool(m.group(2))


def _bind_response(
    project: Project,
    path: Path,
    arg: str,
    op: Operation,
    index: dict[tuple[str, tuple[str, str]], set[tuple[str, str]]],
) -> str | None:
    found = _declared(project, path, arg)
    if isinstance(found, str):
        return f"T: {found}"
    hand, is_array = found
    single = op.single_ok()
    if single is not None and single == (hand[1], is_array):
        return None
    paired = index.get(("response", hand), set())
    if not is_array and ("ApiOk", op.key) in paired:
        return None
    if single is not None and single[1] == is_array and ("ApiResponse", single[0]) in paired:
        return None
    return f"T: {arg} is not bound to {op.key} (ok {op.ok})"


def _bind_body(
    project: Project,
    path: Path,
    arg: str,
    op: Operation,
    index: dict[tuple[str, tuple[str, str]], set[tuple[str, str]]],
) -> str | None:
    found = _declared(project, path, arg)
    if isinstance(found, str):
        return f"B: {found}"
    hand, is_array = found
    body = op.single_body()
    if is_array:
        return f"B: {arg} is an array"
    if body is not None and body == hand[1]:
        return None
    paired = index.get(("request", hand), set())
    if ("ApiBody", op.key) in paired or (body is not None and ("ApiRequest", body) in paired):
        return None
    return f"B: {arg} is not bound to {op.key} (body {op.body})"


# --------------------------------------------------------------------------
# Coverage (i): schema-named hand types.
# --------------------------------------------------------------------------


def coverage_files(src: Path = SRC) -> list[Path]:
    files = [src / "types.ts", src / "lib" / "apiTypes.ts"]
    files += sorted((src / "types").glob("*.ts"))
    files += sorted((src / "lib" / "apiClients").glob("*.ts"))
    return [
        path
        for path in files
        if path.name != "api.gen.ts" and ".check." not in path.name and ".test." not in path.name
    ]


def schema_named(project: Project, responses: set[str], requests: set[str]) -> list[tuple[str, str, str]]:
    """(side, module rel, name) for every exported hand type named after a schema."""

    found: list[tuple[str, str, str]] = []
    for path in coverage_files(project.src):
        module = project.module(path)
        for name, exported in module.declarations().items():
            if not exported:
                continue
            if name in responses:
                found.append(("response", module.rel, name))
            if name in requests:
                found.append(("request", module.rel, name))
    return found


def missing_coverage(named: list[tuple[str, str, str]], pairs: list[Pair]) -> list[str]:
    have = {(p.side, p.hand, p.wire, p.key) for p in pairs}
    missing: list[str] = []
    for side, rel, name in named:
        wire = "ApiResponse" if side == "response" else "ApiRequest"
        if (side, (rel, name), wire, name) not in have:
            missing.append(f"{side} pair {rel}:{name} vs {wire}<'{name}'>")
    return missing


# --------------------------------------------------------------------------
# Accounting: bound, exempt, failing and stale.
# --------------------------------------------------------------------------


@dataclass
class Ledger:
    bound: list[Site] = field(default_factory=list)
    exempt: list[Site] = field(default_factory=list)
    failing: list[Site] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)


def account(sites: list[Site], exempt: dict[str, str]) -> Ledger:
    """Every site is bound, exempt or failing; an exemption that binds or matches no site is stale."""

    ledger = Ledger()
    keys = {site.key for site in sites}
    for site in sites:
        if site.key in exempt:
            if site.bound or site.kind in {"raw", "boot"}:
                ledger.stale.append(site.key)
            else:
                ledger.exempt.append(site)
        elif site.bound:
            ledger.bound.append(site)
        else:
            ledger.failing.append(site)
    ledger.stale += sorted(key for key in exempt if key not in keys)
    return ledger
