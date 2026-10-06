"""wow-ai-5 ``--live`` probe for tools/genie_tiles_spike.py (W5c w5-genie-stop-context).

READ calls of the Databricks SDK only, plus at most ``--max-executions``
executions of a Genie message attachment's own query per age bucket:

* ``genie.get_space(S).warehouse_id``, then ``genie.list_conversations`` and
  ``genie.list_conversation_messages``: COMPLETED messages with a query
  attachment, bucketed by age (<=1 d, 1-7 d, 7-30 d, >30 d);
* per bucket, ``warehouses.get(id)`` (state and size) before each execute; a
  warehouse that is not RUNNING is never woken without ``--allow-wake``
  (recorded ``skipped_cold``); ``genie.get_message`` right before the call,
  which runs only while the message is COMPLETED or EXECUTING_QUERY;
* with ``--other-profile``, ONE execute of the first identity's attachment
  under the other identity (isolation).

It records ids, states, HTTP status or exception class, durations, row counts
and the warehouse state and size. NEVER question text, conversation titles,
SQL text, row values, emails or headers. Verdicts for pass criteria 2-4 of
docs/genie-tiles-spike.md; exit 0 PASS, 1 FAIL, 2 INCONCLUSIVE (auth or
network, or nothing to measure). Nothing in backend/ or frontend/ imports it.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import Any

AGE_BUCKETS: tuple[tuple[str, float | None], ...] = (("le_1d", 1.0), ("1_7d", 7.0), ("7_30d", 30.0), ("gt_30d", None))
EXECUTABLE = frozenset({"COMPLETED", "EXECUTING_QUERY"})
WARM_P95_MS = 15_000  # docs/load-baseline.md: Genie p95
MAX_CONVERSATIONS = 200
PAGE_SIZE = 50
REFUSED_CLASSES = frozenset({"PermissionDenied", "NotFound", "ResourceDoesNotExist", "Unauthenticated"})
PASS, FAIL, INCONCLUSIVE = "PASS", "FAIL", "INCONCLUSIVE"

ClientFactory = Callable[[str], Any]


def _value(item: Any) -> str:
    return str(getattr(item, "value", item) or "")


def _bucket(age_days: float) -> str:
    for name, limit in AGE_BUCKETS:
        if limit is None or age_days <= limit:
            return name
    return AGE_BUCKETS[-1][0]


def _attachment_id(message: Any) -> str | None:
    for attachment in getattr(message, "attachments", None) or []:
        if getattr(attachment, "query", None) is not None and getattr(attachment, "attachment_id", None):
            return str(attachment.attachment_id)
    return None


def _http_status(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    return int(status) if isinstance(status, int) else None


def _row_count(response: Any) -> int | None:
    statement = getattr(response, "statement_response", None)
    manifest = getattr(statement, "manifest", None)
    total = getattr(manifest, "total_row_count", None)
    if isinstance(total, int):
        return total
    rows = getattr(getattr(statement, "result", None), "data_array", None)
    return len(rows) if isinstance(rows, list) else None


def _paged(fetch: Callable[[str | None], Any], items: str, limit: int) -> Iterable[Any]:
    token: str | None = None
    seen = 0
    while seen < limit:
        page = fetch(token)
        for item in getattr(page, items, None) or []:
            seen += 1
            yield item
            if seen >= limit:
                return
        token = getattr(page, "next_page_token", None)
        if not token:
            return


def _candidates(client: Any, space_id: str, now_ms: float) -> dict[str, list[dict[str, str]]]:
    """COMPLETED messages with a query attachment, by age bucket (ids only)."""

    buckets: dict[str, list[dict[str, str]]] = {name: [] for name, _ in AGE_BUCKETS}
    conversations = _paged(
        lambda token: client.genie.list_conversations(space_id, page_size=PAGE_SIZE, page_token=token),
        "conversations",
        MAX_CONVERSATIONS,
    )
    for conversation in conversations:
        conversation_id = str(conversation.conversation_id)
        messages = _paged(
            lambda token, cid=conversation_id: client.genie.list_conversation_messages(
                space_id, cid, page_size=PAGE_SIZE, page_token=token
            ),
            "messages",
            PAGE_SIZE * 4,
        )
        for message in messages:
            attachment_id = _attachment_id(message)
            if _value(getattr(message, "status", None)) != "COMPLETED" or attachment_id is None:
                continue
            created = getattr(message, "created_timestamp", None) or 0
            age_days = max(0.0, (now_ms - float(created)) / 86_400_000)
            buckets[_bucket(age_days)].append(
                {
                    "conversation_id": conversation_id,
                    "message_id": str(getattr(message, "message_id", None) or getattr(message, "id", "")),
                    "attachment_id": attachment_id,
                }
            )
    return buckets


def _execute(client: Any, space_id: str, ids: dict[str, str], monotonic: Callable[[], float]) -> dict[str, Any]:
    started = monotonic()
    try:
        response = client.genie.execute_message_attachment_query(
            space_id, ids["conversation_id"], ids["message_id"], ids["attachment_id"]
        )
    except Exception as exc:  # noqa: BLE001 - recorded as a class and an HTTP status only
        return {
            "outcome": "refused" if type(exc).__name__ in REFUSED_CLASSES else "error",
            "error_class": type(exc).__name__,
            "http_status": _http_status(exc),
            "duration_ms": round((monotonic() - started) * 1000, 1),
        }
    return {
        "outcome": "ok",
        "http_status": 200,
        "duration_ms": round((monotonic() - started) * 1000, 1),
        "row_count": _row_count(response),
    }


def _attempt(
    client: Any, space_id: str, warehouse_id: str, ids: dict[str, str], *, allow_wake: bool,
    monotonic: Callable[[], float],
) -> dict[str, Any]:
    warehouse = client.warehouses.get(warehouse_id)
    record: dict[str, Any] = {
        **ids,
        "warehouse_state": _value(getattr(warehouse, "state", None)),
        "warehouse_size": str(getattr(warehouse, "cluster_size", None) or ""),
    }
    if record["warehouse_state"] != "RUNNING" and not allow_wake:
        return {**record, "outcome": "skipped_cold"}
    status = _value(getattr(client.genie.get_message(space_id, ids["conversation_id"], ids["message_id"]), "status", None))
    record["message_status"] = status
    if status not in EXECUTABLE:
        return {**record, "outcome": "skipped_state"}
    return {**record, **_execute(client, space_id, ids, monotonic)}


def _verdict(*parts: str) -> str:
    if FAIL in parts:
        return FAIL
    return INCONCLUSIVE if INCONCLUSIVE in parts else PASS


def _criteria(executions: list[dict[str, Any]], isolation: dict[str, Any] | None, *, app_identity: bool) -> dict[str, Any]:
    tried = [e for e in executions if e["bucket"] == "7_30d" and e["outcome"] in {"ok", "refused", "error"}]
    c2 = PASS if any(e["outcome"] == "ok" for e in tried) else (FAIL if tried else INCONCLUSIVE)
    ran = [e for e in executions if e["outcome"] in {"ok", "refused", "error"}]
    same = PASS if any(e["outcome"] == "ok" for e in ran) else (FAIL if ran else INCONCLUSIVE)
    if isolation is None:
        other = INCONCLUSIVE
    else:
        other = PASS if isolation.get("outcome") == "refused" else (FAIL if isolation.get("outcome") == "ok" else INCONCLUSIVE)
    app = PASS if app_identity and same == PASS else (FAIL if app_identity and same == FAIL else INCONCLUSIVE)
    warm = [e for e in ran if e.get("warehouse_state") == "RUNNING"]
    fast = [e for e in warm if e["outcome"] == "ok" and e["duration_ms"] <= WARM_P95_MS]
    c4 = PASS if fast else (FAIL if warm else INCONCLUSIVE)
    return {
        "criterion_2": {"verdict": c2, "executed_7_30d": len(tried)},
        "criterion_3": {
            "verdict": _verdict(same, other, app),
            "same_identity": same,
            "other_identity_refused": other,
            "app_identity": app,
            "reason": None if app_identity else "run with a user profile: the App service principal half needs the App's identity",
        },
        "criterion_4": {
            "verdict": c4,
            "warm_executes": len(warm),
            "warm_within_p95": len(fast),
            "genie_budget_calls_per_refresh": 1,
        },
    }


def measure_live(
    client_factory: ClientFactory,
    *,
    profile: str,
    space_id: str,
    other_profile: str | None = None,
    max_executions: int = 2,
    allow_wake: bool = False,
    app_identity: bool = False,
    clock: Callable[[], float] = time.time,
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[dict[str, Any], int]:
    """Run the live probe; returns the JSON-safe result and the exit code."""

    try:
        client = client_factory(profile)
        warehouse_id = str(client.genie.get_space(space_id).warehouse_id)
        buckets = _candidates(client, space_id, clock() * 1000)
    except Exception as exc:  # noqa: BLE001 - auth or network: nothing was measured
        verdicts = {f"criterion_{n}": {"verdict": INCONCLUSIVE} for n in (2, 3, 4)}
        return {"mode": "live", "space_id": space_id, "error_class": type(exc).__name__, "criteria": verdicts}, 2
    executions: list[dict[str, Any]] = []
    for bucket, candidates in buckets.items():
        for ids in candidates[: max(0, max_executions)]:
            executions.append(
                {"bucket": bucket, **_attempt(client, space_id, warehouse_id, ids, allow_wake=allow_wake, monotonic=monotonic)}
            )
    isolation: dict[str, Any] | None = None
    first = next((e for e in executions if e["outcome"] == "ok"), None)
    if other_profile and first is not None:
        ids = {key: first[key] for key in ("conversation_id", "message_id", "attachment_id")}
        try:
            other = client_factory(other_profile)
            isolation = {**ids, **_execute(other, space_id, ids, monotonic)}
        except Exception as exc:  # noqa: BLE001 - the other identity could not even sign in
            isolation = {**ids, "outcome": "error", "error_class": type(exc).__name__, "http_status": _http_status(exc)}
    criteria = _criteria(executions, isolation, app_identity=app_identity)
    result = {
        "mode": "live",
        "space_id": space_id,
        "warehouse_id": warehouse_id,
        "buckets": {name: {"completed_with_query": len(items)} for name, items in buckets.items()},
        "executions": executions,
        "isolation": isolation,
        "criteria": criteria,
    }
    overall = _verdict(*(c["verdict"] for c in criteria.values()))
    return result, {PASS: 0, FAIL: 1, INCONCLUSIVE: 2}[overall]


def sdk_client_factory(profile: str) -> Any:
    """A WorkspaceClient for a CLI profile (imported here, never at tool load)."""

    from databricks.sdk import WorkspaceClient

    return WorkspaceClient(profile=profile)


__all__ = ["measure_live", "sdk_client_factory"]
