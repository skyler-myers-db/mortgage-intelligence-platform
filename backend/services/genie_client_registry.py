"""Process-wide Genie client registry and its resilience wrapper.

Single responsibility: vend the one ``ResilientGenieClient`` the app talks
to. That means resolving the Genie space id (env var, then the committed
``genie/space_id.txt``), deciding whether that id is still a bundle
placeholder, composing the Slice-6 breaker + retry policy around the bare
``GenieClient`` transport, and holding the process-lifetime singleton.

The HTTP seam itself stays in ``backend.services.genie_client``; this module
only wraps it. ``genie_client`` re-exports every public name defined here so
existing ``from backend.services.genie_client import get_genie_client``
call sites keep working.
"""
from __future__ import annotations

from threading import Lock
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Resilience wrapper + singleton accessor.
# ---------------------------------------------------------------------------


class ResilientGenieClient:
    """Compose ``Resilient`` (breaker + retry) around ``GenieClient.ask``.

    The breaker key is ``"genie"`` so ``/api/health`` reports the third
    dependency alongside ``warehouse`` and ``lakebase``. Retries are
    bounded at 2 attempts (0.2s -> 0.8s backoff) because Genie is
    latency-sensitive: more retries just compound cold-start delay.
    """

    def __init__(self, client: GenieClient, resilient: Any) -> None:
        self._client = client
        self._resilient = resilient

    @property
    def space_id(self) -> str:
        return self._client.space_id

    @property
    def resilient(self) -> Any:
        return self._resilient

    def ask(
        self,
        question: str,
        conversation_id: str | None = None,
        poll_timeout_s: int | None = None,
    ) -> GenieResponse:
        # R6-18: when the breaker was force-opened at boot because
        # GENIE_SPACE_ID was a placeholder string, we give ourselves a
        # chance to notice a runtime config rotation (Databricks Apps
        # supports env-var rotation without a restart). If the placeholder
        # is gone, close the breaker and let the next probe through --
        # otherwise the breaker flip-flops on placeholder probes until
        # the process dies. Predicate is a cheap module-local read, so
        # this costs almost nothing per request.
        self._resilient.breaker.force_close_if_config_changed(
            lambda: not is_placeholder_space_id(self._client.space_id)
        )
        return self._resilient.call(
            lambda: self._client.ask(
                question,
                conversation_id=conversation_id,
                poll_timeout_s=poll_timeout_s,
            )
        )

    def submit_message(
        self,
        question: str,
        conversation_id: str | None = None,
    ) -> tuple[str, str]:
        # Answer-path call: shares the ask() breaker so a sick Genie fails
        # the submission fast and the router can fall back to the same
        # degraded response the synchronous path produces.
        self._resilient.breaker.force_close_if_config_changed(
            lambda: not is_placeholder_space_id(self._client.space_id)
        )
        return self._resilient.call(
            lambda: self._client.submit_message(question, conversation_id=conversation_id)
        )

    def peek_message(self, conversation_id: str, message_id: str) -> dict[str, Any]:
        # Advisory progress read. Breaker-free (like ping): a transient peek
        # failure must not trip the answer-path breaker mid-question, and an
        # open breaker must not stop the browser from watching an in-flight
        # message finish.
        return self._client.peek_message(conversation_id, message_id)

    def resume_message(self, conversation_id: str, message_id: str) -> GenieResponse:
        # Governed completion of an already-submitted message; answer path,
        # so it rides the breaker exactly like ask().
        return self._resilient.call(
            lambda: self._client.resume_message(conversation_id, message_id)
        )

    def post_message_comment(
        self,
        conversation_id: str,
        message_id: str,
        content: str,
    ) -> bool:
        # Best-effort side effect on the feedback path; not the answer path.
        # Bypasses the breaker so posting feedback never trips or is blocked
        # by the answer-path breaker state.
        return self._client.post_message_comment(conversation_id, message_id, content)

    def send_message_feedback(
        self,
        conversation_id: str,
        message_id: str,
        rating: Literal["POSITIVE", "NEGATIVE"],
    ) -> None:
        # The service durably records and claims an idempotency key before this
        # call. Keep it breaker-free so answer-path health does not discard a
        # user's explicit quality signal.
        self._client.send_message_feedback(conversation_id, message_id, rating)

    def download_native_visualization(
        self,
        conversation_id: str,
        message_id: str,
        attachment_id: str,
    ) -> bool:
        # Capability probe helper; best-effort, breaker-free.
        return self._client.download_native_visualization(
            conversation_id, message_id, attachment_id
        )

    def ping(self) -> bool:
        # Ping is a health probe, not a user-facing call -- it does not
        # go through the breaker so a closed breaker can't mask a sick
        # Genie and an open breaker doesn't block the probe from
        # discovering Genie came back up.
        return self._client.ping()


_CLIENT: ResilientGenieClient | None = None
_CLIENT_LOCK = Lock()


def _load_space_id_from_file() -> str | None:
    """Read the committed ``genie/space_id.txt`` as a fallback.

    The file is version-controlled so a fresh ``pip install`` + ``uv run``
    outside a bundle deploy still gets the right space id without env
    plumbing. Env var overrides this for per-environment Genie spaces.
    """
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    space_id_file = repo_root / "genie" / "space_id.txt"
    if not space_id_file.exists():
        return None
    try:
        content = space_id_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return content or None


# Placeholder values defined by the bundle's default var (docs/runbook §2).
# When the space id is still one of these strings we deliberately trip the
# breaker at boot so /api/genie/message returns the explicit degraded response
# instead of the live client 500-ing on the first request (hole-finder round 3
# #18, 2026-04-23).
_PLACEHOLDER_SPACE_IDS: frozenset[str] = frozenset({
    "00000000PLACEHOLDER",
    "PLACEHOLDER",
    "REPLACE_ME",
})


def is_placeholder_space_id(space_id: str | None) -> bool:
    if not space_id:
        return True
    return space_id.strip().upper() in _PLACEHOLDER_SPACE_IDS


def _resolve_space_id() -> str:
    from backend.config.settings import settings

    space_id = settings.genie_space_id or _load_space_id_from_file()
    if not space_id:
        raise RuntimeError(
            "Mortgage Intelligence Platform refuses to start without a Genie "
            "space id. Set GENIE_SPACE_ID in .env.local (see .env.example) or "
            "commit the space id to genie/space_id.txt."
        )
    return space_id


def get_genie_client() -> ResilientGenieClient:
    """Return the process-wide Genie client, constructing it on first use.

    Raises at call-time (not import-time) so test processes that inject
    stub repositories via FastAPI ``dependency_overrides`` never touch
    this function and never need real credentials.
    """
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    from backend.config.settings import settings
    from backend.services.resilience import Resilient, get_breaker

    with _CLIENT_LOCK:
        if _CLIENT is not None:
            return _CLIENT
        host, token_provider, _warehouse_id = settings.require_databricks_creds()
        space_id = _resolve_space_id()
        bare = GenieClient(
            host=host,
            token=token_provider,
            space_id=space_id,
            timeout_s=settings.databricks_timeout_s + 15,
        )
        breaker = get_breaker("genie", failure_threshold=3, cooldown_s=20.0)
        # Round-3 hole-finder #18: when GENIE_SPACE_ID is still one of the
        # bundle-default placeholder strings, the upstream Databricks call
        # will 500 on the first query. Trip the breaker open at boot so the
        # repository returns the explicit degraded response immediately.
        if is_placeholder_space_id(space_id):
            breaker.force_open_for_placeholder_config()
        resilient = Resilient[Any](
            breaker=breaker,
            dependency_name="genie",
            attempts=2,
            backoff_base=0.2,
            backoff_max=0.8,
            retry_on=(GenieClientError, OSError),
        )
        _CLIENT = ResilientGenieClient(bare, resilient)
        return _CLIENT


def _reset_genie_client_for_tests() -> None:
    """Test helper -- drops the singleton so a fresh stub installs
    cleanly. NOT for production use."""
    global _CLIENT
    with _CLIENT_LOCK:
        _CLIENT = None


# ---------------------------------------------------------------------------
# The transport seam this module wraps. Imported at the bottom, after every
# definition above is bound, because ``genie_client`` re-exports the names
# defined here from its own bottom import; deferring both ends means either
# module can be imported first. Nothing above runs at module-execution time:
# annotations are lazy (``from __future__ import annotations``) and
# ``GenieClient`` / ``GenieClientError`` are only read inside
# ``get_genie_client()`` when it is called.
# ---------------------------------------------------------------------------
from backend.services.genie_client import (  # noqa: E402
    GenieClient,
    GenieClientError,
    GenieResponse,
)
