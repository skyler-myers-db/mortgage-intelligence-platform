"""The frontend's reviewed audit-free GET list is audit-free on the server.

``frontend/src/lib/auditFreeReads.ts`` lists the GET routes a recovery may
re-read after the network lost the body of their answer (PR #326): a 200
whose connection dropped mid-transfer. The server answered that read, so an
audited one has already written its VIEW_* row, and re-reading it on a
reconnect would write a second with no act behind it. A path missing from the
list counts as audited (fail-closed), so this pins only that nothing audited
is ever ON it:

- every entry is a mounted GET route, in its ``/api/`` form;
- no mount of an entry takes the audit store or a background task anywhere in
  its dependency tree (every audited GET here writes through one or both);
- no audited GET route matches an entry under the frontend's own matcher.
"""

from __future__ import annotations

import re
import typing
from pathlib import Path

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from backend.main import app
from backend.services.audit_store import AuditStore, get_audit_store

REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_FREE_TS = REPO_ROOT / "frontend" / "src" / "lib" / "auditFreeReads.ts"

# The audited GET reads the app makes (visual.ts AUDITED_READS), which the
# dependency walk must classify as audited: a control that it is not vacuous.
KNOWN_AUDITED_GETS = {
    "/api/leads",
    "/api/borrowers/{borrower_id}",
    "/api/borrowers/{borrower_id}/proof",
    "/api/audit/events",
    "/api/audit/events/page",
    "/api/audit/rollups",
    "/api/audit/facets",
    "/api/audit/count",
    "/api/audit/refusal-reports",
    "/api/audit/refusal-reports/{report_id}/question",
}


def _registry() -> list[str]:
    source = AUDIT_FREE_TS.read_text(encoding="utf-8")
    match = re.search(
        r"export const AUDIT_FREE_GET_ROUTES: readonly string\[\] = \[(.*?)\];",
        source,
        re.DOTALL,
    )
    assert match, "AUDIT_FREE_GET_ROUTES not found in auditFreeReads.ts"
    return re.findall(r"'([^']+)'", match.group(1))


def _logical(path: str) -> str:
    return re.sub(r"^/api/v1(?=/|$)", "/api", path)


def _pattern(template: str) -> re.Pattern[str]:
    """The frontend's matcher: each ``{name}`` is one path segment."""

    parts = [
        "[^/]+" if re.fullmatch(r"\{[a-z_]+\}", part) else re.escape(part)
        for part in template.split("/")
    ]
    return re.compile("^" + "/".join(parts) + "$")


def _returns_audit_store(call: typing.Callable[..., object]) -> bool:
    if call is get_audit_store:
        return True
    try:
        hints = typing.get_type_hints(call)
    except Exception:  # noqa: BLE001 - an unresolvable hint is not the store
        return False
    return hints.get("return") is AuditStore


def _audit_capable(dependant: Dependant) -> bool:
    """True when the route can write a row: an audit store or a background task."""

    if dependant.background_tasks_param_name:
        return True
    for sub in dependant.dependencies:
        if sub.call is not None and _returns_audit_store(sub.call):
            return True
        if _audit_capable(sub):
            return True
    return False


def _get_routes() -> dict[str, list[APIRoute]]:
    routes: dict[str, list[APIRoute]] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute) or "GET" not in route.methods:
            continue
        if not route.path.startswith("/api/") or "{full_path:path}" in route.path:
            continue
        routes.setdefault(_logical(route.path), []).append(route)
    return routes


def test_the_registry_parses_to_unique_api_paths() -> None:
    entries = _registry()
    assert len(entries) > 20, "the audit-free list parsed (nearly) empty"
    assert len(entries) == len(set(entries))
    assert all(entry.startswith("/api/") and not entry.startswith("/api/v1/") for entry in entries)


def test_the_walk_classifies_every_known_audited_read_as_audited() -> None:
    routes = _get_routes()
    for path in sorted(KNOWN_AUDITED_GETS):
        assert path in routes, f"{path} is not a mounted GET route"
        assert all(_audit_capable(route.dependant) for route in routes[path]), path


def test_every_entry_is_a_mounted_get_that_can_write_no_audit_row() -> None:
    routes = _get_routes()
    for entry in _registry():
        assert entry in routes, f"{entry} is not a mounted GET route"
        for route in routes[entry]:
            assert not _audit_capable(route.dependant), (
                f"{route.path} takes the audit store or a background task: it can write "
                "an audit row, so a recovery must never re-read its answer on its own"
            )
            assert entry not in KNOWN_AUDITED_GETS


def test_no_audited_get_matches_an_entry() -> None:
    patterns = [_pattern(entry) for entry in _registry()]
    for path, mounted in sorted(_get_routes().items()):
        if not any(_audit_capable(route.dependant) for route in mounted):
            continue
        sample = re.sub(r"\{[^}]+\}", "B-P5YP9ESW32R7Z", path)
        hits = [pattern.pattern for pattern in patterns if pattern.match(sample)]
        assert not hits, f"audited {path} matches audit-free {hits}"
