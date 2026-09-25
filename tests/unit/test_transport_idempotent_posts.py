"""The frontend transport's re-sendable unkeyed POSTs are reviewed reads.

``frontend/src/lib/apiTransport.ts`` gives every unkeyed POST, PUT, PATCH
and DELETE the 'rejected-only' retry policy (audit 2026-09-21 delivery-v2):
a 503 from a handler that may already have acted is never re-sent. The one
exception is ``IDEMPOTENT_UNKEYED_POSTS``, whose members keep the inner
warming re-send. This pins that list from the server side: each member is a
mounted POST route that is either a backpressure read-only POST or one of
the reviewed audit-exempt / join handlers in the mutation audit manifest,
so a mutating route can never slip into the list.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.main import app
from backend.services.backpressure import BackpressureController
from tests.unit.test_audit_store_contract import _MUTATION_AUDIT_EXPECTATIONS

REPO_ROOT = Path(__file__).resolve().parents[2]
TRANSPORT_TS = REPO_ROOT / "frontend" / "src" / "lib" / "apiTransport.ts"

# Handlers whose POST is safe to re-send, and why. Every one is an entry of
# the mutation audit manifest, so its evidence tokens are checked there.
REVIEWED_IDEMPOTENT_HANDLERS = {
    "preview_portfolio": "backpressure read-only POST: an aggregate preview read",
    "campaign_recommendation": "AUDIT EXEMPT read-only aggregate recommendation",
    "genie_start": "reads the caller's latest conversation; writes nothing",
    "genie_message_progress": "AUDIT EXEMPT token-authorized progress peek",
    "genie_message_status": "AUDIT EXEMPT poll of the caller's own completion job",
    "genie_message_complete": "create_or_join: one completion job per turn, a re-send joins it",
}


def _transport_idempotent_posts() -> set[str]:
    source = TRANSPORT_TS.read_text(encoding="utf-8")
    match = re.search(
        r"export const IDEMPOTENT_UNKEYED_POSTS: ReadonlySet<string> = new Set\(\[(.*?)\]\);",
        source,
        re.DOTALL,
    )
    assert match, "IDEMPOTENT_UNKEYED_POSTS not found in apiTransport.ts"
    return set(re.findall(r"'([^']+)'", match.group(1)))


def _post_handler_names() -> dict[str, str]:
    handlers: dict[str, str] = {}
    for route in app.routes:
        methods = getattr(route, "methods", set()) or set()
        path = getattr(route, "path", "")
        endpoint = getattr(route, "endpoint", None)
        if "POST" not in methods or endpoint is None or not path.startswith("/api/"):
            continue
        logical = re.sub(r"^/api/v1/", "/api/", path)
        handlers[logical] = getattr(endpoint, "__name__", "")
    return handlers


def test_every_idempotent_unkeyed_post_is_a_mounted_reviewed_read() -> None:
    paths = _transport_idempotent_posts()
    assert paths, "the transport allowlist parsed empty"
    handlers = _post_handler_names()
    read_only_posts = BackpressureController._READ_ONLY_POSTS
    for path in sorted(paths):
        assert path in handlers, f"{path} is not a mounted POST route"
        name = handlers[path]
        assert name in _MUTATION_AUDIT_EXPECTATIONS, f"{path} -> {name} is not in the audit manifest"
        assert path in read_only_posts or name in REVIEWED_IDEMPOTENT_HANDLERS, (
            f"{path} -> {name} is neither a backpressure read-only POST nor a reviewed "
            "audit-exempt / join handler; an unkeyed POST that may act must stay rejected-only"
        )


def test_the_allowlist_is_exactly_the_reviewed_set() -> None:
    handlers = _post_handler_names()
    names = {handlers[path] for path in _transport_idempotent_posts() if path in handlers}
    assert names == set(REVIEWED_IDEMPOTENT_HANDLERS)


def test_audit_exempt_members_say_so_in_the_manifest() -> None:
    for name, reason in REVIEWED_IDEMPOTENT_HANDLERS.items():
        tokens = _MUTATION_AUDIT_EXPECTATIONS[name]
        if reason.startswith("AUDIT EXEMPT"):
            assert any(token.startswith("AUDIT EXEMPT") for token in tokens), name
