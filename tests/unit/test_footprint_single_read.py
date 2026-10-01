"""One footprint snapshot per decision (audit ``delivery-06``, decision record e2).

The footprint resolver serves a stale-while-revalidate snapshot, so two reads
inside one decision can straddle a background refresh: the Genie guards used
to read the degraded flag and the covered codes separately, and a refresh
landing between them let a state question through both checks (fail-open).
``/api/config/*`` could likewise list fallback rows under a live flag and
cache the result.

Two layers pin the fix:

* an AST scan of ``backend/**/*.py`` (minus the resolver itself) proving no
  function reads the resolver twice and every footprint-guard call passes one
  named ``snapshot=``;
* behavioural tests against a resolver double whose ``snapshot()`` alternates
  live, then fallback, on every call, so any second read observes a different
  footprint than the first.
"""
from __future__ import annotations

import ast
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import BackgroundTasks

import backend.api.config as config_api
from backend.services import genie_prompt_guardrails as guards
from backend.services.genie_answers import GenieMessageResponse
from backend.services.genie_deterministic import _deterministic_genie_response
from backend.services.genie_message_policy import GenieMessageRequest
from backend.services.geography_scope import GeographyScope, GeographyScopeCounty
from backend.services.gold_cache import GoldAggregateCache
from backend.services.state_footprint import (
    FootprintSnapshot,
    StateFootprintResolver,
    _reset_state_footprint_resolver_for_tests,
    get_state_footprint_resolver,
)
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore
from tests.unit.footprint_snapshots import (
    fallback_snapshot,
    live_snapshot,
    metadata_only_snapshot,
)

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
_RESOLVER_FACTORY = "get_state_footprint_resolver"
_RESOLVER_READS = frozenset(
    {"using_fallback", "list", "state_codes", "state_name_to_codes", "default_state_code", "snapshot"}
)
_GUARD_FUNCTIONS = frozenset(
    {"footprint_guard_match", "footprint_metadata_gap_match", "outside_footprint_match"}
)


# ---------------------------------------------------------------------------
# AST pins
# ---------------------------------------------------------------------------


def _scanned_files() -> list[Path]:
    return sorted(
        path
        for path in BACKEND.rglob("*.py")
        if path.name != "state_footprint.py" and "__pycache__" not in path.parts
    )


def _callee_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_resolver_factory_call(node: ast.expr) -> bool:
    return isinstance(node, ast.Call) and _callee_name(node.func) == _RESOLVER_FACTORY


def _guard_aliases(trees: dict[Path, ast.Module]) -> frozenset[str]:
    """The guard names plus every module-level name bound to one of them."""
    aliases = set(_GUARD_FUNCTIONS)
    for tree in trees.values():
        for stmt in tree.body:
            if isinstance(stmt, ast.Assign) and _callee_name(stmt.value) in _GUARD_FUNCTIONS:
                aliases.update(t.id for t in stmt.targets if isinstance(t, ast.Name))
    return frozenset(aliases)


@dataclass
class _FunctionReads:
    where: str
    resolver_reads: list[str] = field(default_factory=list)
    guard_snapshot_args: list[ast.expr | None] = field(default_factory=list)


class _Scanner(ast.NodeVisitor):
    """Attribute resolver reads and guard calls to their innermost function."""

    def __init__(self, rel: str, aliases: frozenset[str]) -> None:
        self._rel = rel
        self._aliases = aliases
        self._stack: list[tuple[_FunctionReads, set[str]]] = []
        self.functions: list[_FunctionReads] = []

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        reads = _FunctionReads(where=f"{self._rel}:{node.lineno} {node.name}")
        bound: set[str] = set()
        for inner in ast.walk(node):
            if isinstance(inner, ast.Assign) and _is_resolver_factory_call(inner.value):
                bound.update(t.id for t in inner.targets if isinstance(t, ast.Name))
        self._stack.append((reads, bound))
        self.generic_visit(node)
        self._stack.pop()
        self.functions.append(reads)

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_Call(self, node: ast.Call) -> None:
        if self._stack:
            reads, bound = self._stack[-1]
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in _RESOLVER_READS:
                target = func.value
                if _is_resolver_factory_call(target) or (
                    isinstance(target, ast.Name) and target.id in bound
                ):
                    reads.resolver_reads.append(f"{func.attr}@{node.lineno}")
            if _callee_name(func) in self._aliases:
                snapshot = next((kw.value for kw in node.keywords if kw.arg == "snapshot"), None)
                reads.guard_snapshot_args.append(snapshot)
        self.generic_visit(node)


def _scan() -> list[_FunctionReads]:
    trees = {path: ast.parse(path.read_text(encoding="utf-8")) for path in _scanned_files()}
    aliases = _guard_aliases(trees)
    functions: list[_FunctionReads] = []
    for path, tree in trees.items():
        scanner = _Scanner(path.relative_to(ROOT).as_posix(), aliases)
        scanner.visit(tree)
        functions.extend(scanner.functions)
    return functions


def test_no_function_reads_the_footprint_resolver_twice() -> None:
    functions = _scan()
    offenders = [
        f"{fn.where}: {fn.resolver_reads}" for fn in functions if len(fn.resolver_reads) > 1
    ]
    assert offenders == [], (
        "A decision that needs two footprint facts must take ONE snapshot() and "
        "read both from it:\n" + "\n".join(offenders)
    )
    # Non-vacuity: config (2), Genie, both repositories and the canonical scope
    # label all read the resolver.
    assert sum(len(fn.resolver_reads) for fn in functions) >= 6


def test_every_footprint_guard_call_passes_a_snapshot() -> None:
    functions = _scan()
    calls = [(fn.where, arg) for fn in functions for arg in fn.guard_snapshot_args]
    missing = [where for where, arg in calls if arg is None]
    assert missing == [], f"footprint guard called without snapshot=: {missing}"
    # Non-vacuity: genie_deterministic runs both footprint guards.
    assert len(calls) >= 2


def test_footprint_guard_calls_in_one_function_share_one_named_snapshot() -> None:
    offenders: list[str] = []
    for fn in _scan():
        if not fn.guard_snapshot_args:
            continue
        args = fn.guard_snapshot_args
        names = {ast.unparse(arg) for arg in args if arg is not None}
        # A Name, not an inline ``resolver.snapshot()`` per call (two reads).
        if len(names) != 1 or not all(isinstance(arg, ast.Name) for arg in args):
            offenders.append(f"{fn.where}: {sorted(names)}")
    assert offenders == []


def test_guard_functions_require_the_snapshot_keyword() -> None:
    """``snapshot`` is keyword-only with NO default: a bare call is a TypeError."""
    for name in sorted(_GUARD_FUNCTIONS):
        with pytest.raises(TypeError):
            getattr(guards, name)("How many borrowers in Georgia?")


# ---------------------------------------------------------------------------
# Behaviour under a resolver whose snapshot() alternates on every call
# ---------------------------------------------------------------------------

_LIVE = live_snapshot("IL", "CA", "TX")
_FALLBACK = fallback_snapshot()


class _AlternatingResolver(StateFootprintResolver):
    """``snapshot()`` alternates between two footprints on every call.

    Every resolver wrapper (``list``, ``using_fallback`` ...) goes through
    ``snapshot()``, so a decision that reads twice sees two footprints.
    """

    def __init__(self, first: FootprintSnapshot, second: FootprintSnapshot) -> None:
        super().__init__()
        self._sequence: Iterator[FootprintSnapshot] = iter(self._cycle(first, second))
        self.served: list[tuple[str, FootprintSnapshot]] = []

    @staticmethod
    def _cycle(
        first: FootprintSnapshot, second: FootprintSnapshot
    ) -> Iterator[FootprintSnapshot]:
        while True:
            yield first
            yield second

    def snapshot(self) -> FootprintSnapshot:
        served = next(self._sequence)
        caller = sys._getframe(1).f_globals.get("__name__", "?")
        self.served.append((str(caller), served))
        return served


@pytest.fixture
def alternating() -> Iterator[list[_AlternatingResolver]]:
    installed: list[_AlternatingResolver] = []
    previous = get_state_footprint_resolver()
    yield installed
    _reset_state_footprint_resolver_for_tests(previous)


def _install(
    installed: list[_AlternatingResolver],
    first: FootprintSnapshot,
    second: FootprintSnapshot,
) -> _AlternatingResolver:
    resolver = _AlternatingResolver(first, second)
    _reset_state_footprint_resolver_for_tests(resolver)
    installed.append(resolver)
    return resolver


def _scope() -> GeographyScope:
    return GeographyScope(
        state_count=1,
        county_count=1,
        zip_count=2,
        snapshot_date="2026-05-13",
        counties=(
            GeographyScopeCounty(
                state="IL", fips_5="17031", county_name="Cook County", addressable_borrowers=100
            ),
        ),
        source_table="mip.gold.county_rollup",
    )


@pytest.mark.parametrize("live_first", [True, False], ids=["live-first", "fallback-first"])
def test_config_footprint_payload_is_internally_consistent(
    live_first: bool, alternating: list[_AlternatingResolver], monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = GoldAggregateCache()
    monkeypatch.setattr(config_api, "_CONFIG_CACHE", cache)
    monkeypatch.setattr(config_api, "_live_geography_scope", _scope)
    first, second = (_LIVE, _FALLBACK) if live_first else (_FALLBACK, _LIVE)
    _install(alternating, first, second)

    for _ in range(4):
        cache.clear()
        payload = config_api.get_config_footprint()
        codes = [row["state_code"] for row in payload["states"]]  # type: ignore[union-attr]
        if payload["using_fallback"]:
            assert codes == _FALLBACK.codes()
            # A degraded payload is never stored.
            assert cache._entries == {}
        else:
            assert codes == _LIVE.codes()
            assert list(cache._entries) == ["config.footprint.v1"]


@pytest.mark.parametrize("live_first", [True, False], ids=["live-first", "fallback-first"])
def test_config_options_geographies_match_their_status(
    live_first: bool, alternating: list[_AlternatingResolver], monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = GoldAggregateCache()
    monkeypatch.setattr(config_api, "_CONFIG_CACHE", cache)
    monkeypatch.setattr(config_api, "_live_geography_scope", _scope)
    monkeypatch.setattr(config_api, "_target_lender_options", lambda: (["All"], "live"))
    first, second = (_LIVE, _FALLBACK) if live_first else (_FALLBACK, _LIVE)
    _install(alternating, first, second)

    for _ in range(4):
        cache.clear()
        payload = config_api.get_config_options()
        if payload["geographies_status"] == "metadata_only":
            assert payload["geographies"] == ["All"]
        else:
            assert payload["geographies_status"] == "live"
            assert payload["geographies"] == [
                "All 3 states",
                *(row.state_name for row in _LIVE.rows),
            ]


def _genie(question: str) -> GenieMessageResponse | None:
    return _deterministic_genie_response(
        GenieMessageRequest(question=question),
        actor="lo@example.com",
        audit=InMemoryAuditStore(),
        background=BackgroundTasks(),
        lakebase=MagicMock(),
        borrower_repo=MagicMock(),
    )


@pytest.mark.parametrize("live_first", [True, False], ids=["live-first", "fallback-first"])
def test_state_outside_live_coverage_is_always_refused(
    live_first: bool, alternating: list[_AlternatingResolver]
) -> None:
    question = "How many in-the-money borrowers are in Georgia?"
    first, second = (_LIVE, _FALLBACK) if live_first else (_FALLBACK, _LIVE)
    resolver = _install(alternating, first, second)

    response = _genie(question)

    guard_reads = [
        served
        for caller, served in resolver.served
        if caller in {"backend.services.genie_deterministic", guards.__name__}
    ]
    assert len(guard_reads) == 1, "both footprint guards must read ONE snapshot"
    assert response is not None, "a state outside live coverage reached Genie"
    expected = "data_gap" if guard_reads[0].degraded else "out_of_footprint"
    assert response.source == expected


def test_live_snapshot_refuses_a_state_outside_coverage_as_outside_footprint() -> None:
    verdict = guards.footprint_guard_match(
        "How many in-the-money borrowers are in Georgia?", snapshot=_LIVE
    )
    assert verdict == guards.FootprintGuardMatch(
        "outside_footprint", "Georgia", "GA", ("IL", "CA", "TX")
    )
    assert guards.footprint_guard_match("How many borrowers in Illinois?", snapshot=_LIVE) is None


@pytest.mark.parametrize(
    "degraded",
    [fallback_snapshot(), metadata_only_snapshot("IL", "CA", "TX")],
    ids=["fallback", "metadata_only"],
)
def test_degraded_snapshot_verdicts(degraded: FootprintSnapshot) -> None:
    def kind(question: str) -> str | None:
        verdict = guards.footprint_guard_match(question, snapshot=degraded)
        return verdict.kind if verdict else None

    # An explicit state: metadata gap (unchanged).
    assert kind("How many in-the-money borrowers are in Illinois?") == "metadata_gap"
    assert guards.footprint_metadata_gap_match(
        "How many in-the-money borrowers are in Illinois?", snapshot=degraded
    ) == ("Illinois", "IL")
    # A US geography hint: metadata gap (new; it used to fail open under the
    # 50-state fallback, which lists GA).
    assert kind("How many in-the-money borrowers are in Atlanta?") == "metadata_gap"
    assert guards.footprint_metadata_gap_match(
        "How many in-the-money borrowers are in Atlanta?", snapshot=degraded
    ) == ("Atlanta, Georgia", "GA")
    # A non-US hint stays outside_footprint whether or not degraded.
    assert kind("How many in-the-money borrowers are in Toronto?") == "outside_footprint"
    # Puerto Rico (PR) is not one of the 50 state codes: outside_footprint.
    assert kind("How many in-the-money borrowers are in Puerto Rico?") == "outside_footprint"
    assert guards.footprint_metadata_gap_match(
        "How many in-the-money borrowers are in Puerto Rico?", snapshot=degraded
    ) is None
    # No geography at all: no footprint verdict.
    assert kind("How many in-the-money borrowers are there?") is None


def test_legacy_return_shapes_are_unchanged() -> None:
    snapshot = live_snapshot("NY", "NJ")
    assert guards.outside_footprint_match("How many borrowers in Boston?", snapshot=snapshot) == (
        "Boston, Massachusetts",
        "MA",
        ["NY", "NJ"],
    )
    assert (
        guards.footprint_metadata_gap_match("How many borrowers in Boston?", snapshot=snapshot)
        is None
    )
