"""Stdlib-only Databricks Genie Conversation API client.

Module 0 Slice 7 flips ``/ask-genie`` onto the real `mortgage_lead_
intelligence` Genie space provisioned at space id
``01f13d4968af1b249dc388fd5b18b195`` in the DEFAULT workspace. This
module is the HTTP seam; ``backend.services.repositories.databricks_repo
.DatabricksGenieRepository`` wraps it with a safe-corpus fallback that
only activates when the ``genie`` circuit breaker is OPEN.

Design notes:

- ``urllib`` only, matching the pattern in ``backend.services.databricks
  _sql`` so ``app.yaml`` doesn't pull a new wheel into the serverless
  runtime.
- Genie conversations are asynchronous: POST creates a message in
  ``IN_PROGRESS``, the client polls until ``COMPLETED`` (or a terminal
  state like ``FAILED`` / ``CANCELED``). Polling is bounded by
  ``timeout_s`` so a cold-start Genie doesn't hang the FastAPI handler.
- A single process-lifetime client is vended through ``get_genie_client
  ()``; each call through ``ask()`` creates its own ``urllib.request.
  Request`` so concurrent FastAPI handlers are safe.
- Non-2xx / malformed JSON / terminal non-success states raise
  ``GenieClientError`` carrying the status code, space id, and
  conversation id so the resilience wrapper's breaker telemetry and the
  Slice-6 degraded banner have operator-facing context.
- Slice-6 resilience primitives (``Resilient`` + ``CircuitBreaker``)
  compose over this bare client in ``get_genie_client()``; the
  repository layer sees the same ``ask()`` surface regardless of
  whether resilience is wrapped.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Literal

from backend.services.observability import emit

log = logging.getLogger(__name__)


def _question_hash(q: str) -> str:
    """SHA1 prefix of the question text.

    Genie questions are user-authored natural language and may contain
    borrower or property details; we log the hash, not the text, so
    operators can group "all instances of the same question" for
    latency analysis without leaking content.
    """
    return hashlib.sha1(q.encode("utf-8")).hexdigest()[:16]  # noqa: S324 -- not a secret

# Terminal Genie message states. The API docs list COMPLETED as the
# happy path; FAILED / CANCELED / EXPIRED are terminal errors; SUBMITTED
# / IN_PROGRESS / EXECUTING_QUERY are transitional states we poll past.
_GENIE_SUCCESS_STATES: frozenset[str] = frozenset({"COMPLETED"})
_GENIE_TERMINAL_ERROR_STATES: frozenset[str] = frozenset(
    {"FAILED", "CANCELED", "CANCELLED", "EXPIRED", "TIMEOUT"}
)


class GenieClientError(RuntimeError):
    """Raised when the Genie API returns a non-success response.

    Attributes are surfaced verbatim so the resilience wrapper, health
    probe, and /ask-genie error translation can present operator-facing
    context without regex-parsing the message.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        space_id: str | None = None,
        conversation_id: str | None = None,
        message_id: str | None = None,
        state: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.space_id = space_id
        self.conversation_id = conversation_id
        self.message_id = message_id
        self.state = state


@dataclass
class GenieResponse:
    """Structured result of a single ``ask()`` turn.

    ``answer_text`` is the natural-language response string Genie
    produced. When the space compiled a SQL query, ``sql_query`` and
    ``sql_result_rows`` are populated (rows decoded from the
    ``data_array`` disposition). ``conversation_id`` lets the caller
    continue a multi-turn thread on the next ``ask()`` call.
    """

    answer_text: str
    sql_query: str | None
    sql_result_rows: list[dict[str, Any]] | None
    conversation_id: str
    message_id: str
    source: Literal["genie"] = "genie"
    elapsed_ms: int = 0
    trusted_assets: list[str] = field(default_factory=list)


class GenieClient:
    """Stdlib-only Genie Conversation API client.

    The public method ``ask(question, conversation_id=None)`` drives the
    full ``create conversation -> add message -> poll -> fetch
    result`` sequence. Polling uses exponential backoff bounded by
    ``timeout_s`` (wall-clock) so a cold-start space (first query after
    idle can take 5-15s) doesn't hang forever but a healthy space
    returns under ~3s.
    """

    # Conservative poll schedule: starts fast (so a warm Genie returns
    # snappily) and grows slowly (so a cold-start Genie doesn't get
    # hammered). Values tuned against the Slice-7 scope -- single
    # interactive questions; not a bulk workload.
    _POLL_INITIAL_S: float = 0.5
    _POLL_MAX_S: float = 2.0
    _POLL_BACKOFF: float = 1.35

    def __init__(
        self,
        host: str,
        token: str | Callable[[], str],
        space_id: str,
        timeout_s: int = 45,
    ) -> None:
        if not host.startswith("http"):
            host = "https://" + host
        self._host = host.rstrip("/")
        # See DatabricksSqlClient: store a token PROVIDER, not a string.
        # Caching the literal at construction caused the warehouse path
        # to 403 forever once the SDK-minted OAuth token expired (~1h).
        # The same pattern protects the Genie client.
        if callable(token):
            self._token_provider: Callable[[], str] = token
        else:
            literal = token
            self._token_provider = lambda: literal
        self._space_id = space_id
        self._timeout_s = timeout_s

    @property
    def space_id(self) -> str:
        return self._space_id

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ask(
        self,
        question: str,
        conversation_id: str | None = None,
    ) -> GenieResponse:
        """Ask Genie one question; poll to completion; return the answer.

        If ``conversation_id`` is ``None``, starts a new conversation
        (``POST /conversations``) whose initial message IS the question.
        Otherwise appends a message to the existing conversation
        (``POST /conversations/{id}/messages``). Either way, the
        response contains the initial/new message id which we poll for
        completion.
        """
        q_hash = _question_hash(question)
        emit(
            log,
            "genie_query_start",
            dependency="genie",
            operation="ask",
            statement_hash=q_hash,
            conversation_id=conversation_id,
        )
        start = time.monotonic()
        try:
            if conversation_id is None:
                conv_id, msg_id = self._start_conversation(question)
            else:
                conv_id = conversation_id
                msg_id = self._append_message(conv_id, question)

            message = self._poll_message(conv_id, msg_id)
            answer_text, sql_query = self._extract_text_and_sql(message)
            sql_rows: list[dict[str, Any]] | None = None
            if sql_query:
                sql_rows = self._fetch_query_result(conv_id, msg_id)
        except BaseException as exc:
            emit(
                log,
                "genie_query_error",
                level=logging.WARNING,
                dependency="genie",
                operation="ask",
                statement_hash=q_hash,
                duration_ms=round((time.monotonic() - start) * 1000.0, 2),
                outcome="error",
                exc_type=type(exc).__name__,
                exc_msg=str(exc)[:500],
            )
            raise

        elapsed_ms = int((time.monotonic() - start) * 1000)
        emit(
            log,
            "genie_query_end",
            dependency="genie",
            operation="ask",
            statement_hash=q_hash,
            duration_ms=elapsed_ms,
            outcome="ok",
            has_sql=sql_query is not None,
            rows_returned=len(sql_rows) if sql_rows else 0,
        )
        return GenieResponse(
            answer_text=answer_text,
            sql_query=sql_query,
            sql_result_rows=sql_rows,
            conversation_id=conv_id,
            message_id=msg_id,
            source="genie",
            elapsed_ms=elapsed_ms,
        )

    def ping(self) -> bool:
        """Lightweight liveness probe.

        Issues a GET against the space URL and reports whether it
        responded 2xx. Used by ``/api/health`` to show ``genie: up`` /
        ``genie: down`` without burning a conversation slot. A 404 is
        NOT up -- it means the space id is wrong or the caller lost
        access.
        """
        url = f"{self._host}/api/2.0/genie/spaces/{self._space_id}"
        try:
            self._get(url, timeout_s=1.0)
            return True
        except GenieClientError:
            return False
        except (urllib.error.URLError, OSError):
            return False

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _start_conversation(self, question: str) -> tuple[str, str]:
        url = f"{self._host}/api/2.0/genie/spaces/{self._space_id}/start-conversation"
        resp = self._post(url, {"content": question})
        conv_id = resp.get("conversation_id") or (resp.get("conversation") or {}).get("id")
        message = resp.get("message") or {}
        msg_id = message.get("id") or message.get("message_id") or resp.get("message_id")
        if not conv_id or not msg_id:
            raise GenieClientError(
                "Genie start-conversation response missing conversation_id/message_id",
                space_id=self._space_id,
            )
        return conv_id, msg_id

    def _append_message(self, conversation_id: str, question: str) -> str:
        url = (
            f"{self._host}/api/2.0/genie/spaces/{self._space_id}"
            f"/conversations/{conversation_id}/messages"
        )
        resp = self._post(url, {"content": question})
        msg_id = resp.get("id") or resp.get("message_id")
        if not msg_id:
            raise GenieClientError(
                "Genie append-message response missing message id",
                space_id=self._space_id,
                conversation_id=conversation_id,
            )
        return msg_id

    def _poll_message(self, conversation_id: str, message_id: str) -> dict[str, Any]:
        """Poll the message until terminal state or timeout."""
        url = (
            f"{self._host}/api/2.0/genie/spaces/{self._space_id}"
            f"/conversations/{conversation_id}/messages/{message_id}"
        )
        deadline = time.monotonic() + self._timeout_s
        sleep_s = self._POLL_INITIAL_S
        last_state: str | None = None
        while True:
            body = self._get(url)
            state = body.get("status") or body.get("state") or "UNKNOWN"
            last_state = state
            if state in _GENIE_SUCCESS_STATES:
                return body
            if state in _GENIE_TERMINAL_ERROR_STATES:
                err = (body.get("error") or {}).get("message") or state
                raise GenieClientError(
                    f"Genie message terminated in state {state!r}: {err}",
                    space_id=self._space_id,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    state=state,
                )
            if time.monotonic() >= deadline:
                raise GenieClientError(
                    f"Genie message polling timed out after {self._timeout_s}s "
                    f"(last_state={last_state!r})",
                    space_id=self._space_id,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    state=last_state,
                )
            time.sleep(sleep_s)
            sleep_s = min(self._POLL_MAX_S, sleep_s * self._POLL_BACKOFF)

    def _fetch_query_result(
        self,
        conversation_id: str,
        message_id: str,
    ) -> list[dict[str, Any]] | None:
        """Fetch the SQL result rows for a completed message, if any.

        The Genie API returns rows under
        ``statement_response.result.data_array`` with columns in
        ``statement_response.manifest.schema.columns``. Missing /
        empty result is not an error -- some messages are
        text-only and that's fine.
        """
        url = (
            f"{self._host}/api/2.0/genie/spaces/{self._space_id}"
            f"/conversations/{conversation_id}/messages/{message_id}/query-result"
        )
        try:
            body = self._get(url)
        except GenieClientError:
            # 404 on query-result is normal for text-only responses.
            return None
        sr = body.get("statement_response") or {}
        result = sr.get("result") or {}
        data_array = result.get("data_array") or []
        manifest = sr.get("manifest") or {}
        schema = manifest.get("schema") or {}
        columns = schema.get("columns") or []
        if not data_array or not columns:
            return None
        column_names = [c.get("name") for c in columns]
        rows: list[dict[str, Any]] = []
        for raw_row in data_array:
            rows.append(
                {
                    column_names[i]: raw_row[i]
                    for i in range(min(len(column_names), len(raw_row)))
                }
            )
        return rows

    @staticmethod
    def _extract_text_and_sql(message: dict[str, Any]) -> tuple[str, str | None]:
        """Pull the answer text + optional SQL from a completed message.

        Genie's message shape contains an ``attachments`` array; each
        attachment is either ``{"text": {"content": "..."}}`` or
        ``{"query": {"query": "SELECT ...", "description": "..."}}``.
        We concatenate all text attachments (preserving order) and
        surface the first SQL attachment.
        """
        attachments = message.get("attachments") or []
        text_parts: list[str] = []
        sql_query: str | None = None
        for att in attachments:
            text = att.get("text") or {}
            if isinstance(text, dict) and text.get("content"):
                text_parts.append(str(text["content"]).strip())
            query = att.get("query") or {}
            if isinstance(query, dict) and query.get("query") and sql_query is None:
                sql_query = str(query["query"]).strip()
                desc = query.get("description")
                if desc and not text_parts:
                    text_parts.append(str(desc).strip())
        # Fallback: some responses put free text under message.content
        # directly (older API shape). Honour it if attachments were
        # empty so no response bubbles up as empty string.
        if not text_parts:
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                text_parts.append(content.strip())
        answer_text = "\n\n".join(p for p in text_parts if p) or ""
        return answer_text, sql_query

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps(body).encode("utf-8")
        bearer = self._token_provider()
        req = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {bearer}",
                "Content-Type": "application/json",
            },
        )
        return self._send(req)

    def _get(self, url: str, *, timeout_s: float | None = None) -> dict[str, Any]:
        bearer = self._token_provider()
        req = urllib.request.Request(
            url,
            method="GET",
            headers={"Authorization": f"Bearer {bearer}"},
        )
        return self._send(req, timeout_s=timeout_s)

    def _send(
        self,
        req: urllib.request.Request,
        *,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        effective_timeout = timeout_s if timeout_s is not None else float(self._timeout_s + 5)
        try:
            with urllib.request.urlopen(req, timeout=effective_timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise GenieClientError(
                f"HTTP {exc.code} from Genie API: {detail[:500]}",
                status_code=exc.code,
                space_id=self._space_id,
            ) from exc
        except urllib.error.URLError as exc:
            raise GenieClientError(
                f"Network error reaching Genie API: {exc.reason!r}",
                space_id=self._space_id,
            ) from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GenieClientError(
                f"Malformed JSON from Genie API (first 200 chars): {raw[:200]!r}",
                space_id=self._space_id,
            ) from exc


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
            lambda: self._client.ask(question, conversation_id=conversation_id)
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
# breaker at boot so the safe-corpus fallback answers /api/genie/message
# instead of the live client 500-ing on the first request (hole-finder
# round 3 #18, 2026-04-23).
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
        # repository's safe-corpus fallback answers immediately; no user-
        # visible "warming up" message on day 0 of a fresh customer deploy.
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


__all__ = [
    "GenieClient",
    "GenieClientError",
    "GenieResponse",
    "ResilientGenieClient",
    "get_genie_client",
]
