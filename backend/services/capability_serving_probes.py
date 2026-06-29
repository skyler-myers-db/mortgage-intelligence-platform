"""Bounded serving-endpoint probes for capability readiness."""

from __future__ import annotations

import time
from typing import Any

from backend.services.databricks_sql_helpers import _validate_identifier


def query_serving_endpoint(
    workspace_client: Any,
    endpoint: str,
    *,
    prompt: str,
    client_request_id: str | None = None,
) -> Any:
    try:
        from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

        messages: list[Any] = [ChatMessage(role=ChatMessageRole.USER, content=prompt)]
    except Exception:  # noqa: BLE001 - keep tests/lightweight clients decoupled from SDK internals
        messages = [{"role": "user", "content": prompt}]
    kwargs: dict[str, Any] = {
        "messages": messages,
        "max_tokens": 64,
        "temperature": 0.0,
    }
    if client_request_id:
        kwargs["client_request_id"] = client_request_id
    return workspace_client.serving_endpoints.query(endpoint, **kwargs)


def serving_response_has_payload(response: Any) -> bool:
    if response is None:
        return False
    for method in ("as_dict", "to_dict"):
        converter = getattr(response, method, None)
        if callable(converter):
            try:
                response = converter()
                break
            except Exception:  # noqa: BLE001 - fall back to attribute checks
                pass
    if isinstance(response, dict):
        return any(
            bool(response.get(key))
            for key in ("choices", "predictions", "outputs", "output", "response", "messages")
        )
    return any(
        bool(getattr(response, key, None))
        for key in ("choices", "predictions", "outputs", "output", "response", "messages")
    )


def count_inference_log_rows(
    sql_client: Any,
    expected_table_prefix: str,
    *,
    client_request_id: str,
) -> int:
    catalog, schema, table_prefix = _split_three_part_relation(expected_table_prefix)
    table_rows = sql_client.execute(
        """
        SELECT table_name
        FROM system.information_schema.tables
        WHERE table_catalog = :catalog
          AND table_schema = :schema
          AND (table_name = :prefix OR table_name LIKE :prefix_like)
        ORDER BY table_name
        """,
        {
            "catalog": catalog,
            "schema": schema,
            "prefix": table_prefix,
            "prefix_like": f"{table_prefix}%",
        },
    )
    total = 0
    for row in table_rows:
        table_name = str(row.get("table_name") or "").strip()
        if not table_name:
            continue
        _validate_identifier("table", table_name)
        rows = sql_client.execute(
            f"SELECT COUNT(*) AS row_count FROM {catalog}.{schema}.{table_name} WHERE client_request_id = :client_request_id",
            {"client_request_id": client_request_id},
        )
        if rows:
            total += int(rows[0].get("row_count") or rows[0].get("n") or 0)
    return total


def wait_for_inference_log_increment(
    sql_client: Any,
    expected_table_prefix: str,
    *,
    previous_count: int,
    client_request_id: str,
    timeout_s: float = 10.0,
    interval_s: float = 1.0,
) -> int:
    deadline = time.monotonic() + timeout_s
    latest = previous_count
    while True:
        latest = count_inference_log_rows(
            sql_client,
            expected_table_prefix,
            client_request_id=client_request_id,
        )
        if latest > previous_count or time.monotonic() >= deadline:
            return latest
        time.sleep(interval_s)


def _split_three_part_relation(relation: str) -> tuple[str, str, str]:
    parts = [part.strip() for part in relation.split(".")]
    if len(parts) != 3 or any(not part for part in parts):
        raise ValueError(f"Expected a three-part Unity Catalog relation, got {relation!r}.")
    catalog, schema, table = parts
    _validate_identifier("catalog", catalog)
    _validate_identifier("schema", schema)
    _validate_identifier("table", table)
    return catalog, schema, table
