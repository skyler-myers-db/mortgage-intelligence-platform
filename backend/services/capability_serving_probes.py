"""Bounded serving-endpoint probes for capability readiness."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Literal

from backend.services.databricks_sql_helpers import _validate_identifier

ServingTransport = Literal["responses_api", "endpoint_invocation"]

_LIKE_ESCAPE = "\\"


@dataclass(frozen=True)
class ServingEndpointExecution:
    """Runtime proof for the exact endpoint call that produced a response."""

    endpoint: str
    task: str | None
    transport: ServingTransport
    response: Any
    client_request_id: str | None = None

    @property
    def response_id(self) -> str | None:
        return serving_response_id(self.response)

    @property
    def proves_agent_response(self) -> bool:
        return (
            self.transport == "responses_api"
            and _is_agent_responses_task(self.task)
            and serving_response_is_terminal_completed(self.response)
            and serving_response_has_payload(self.response)
        )


def query_serving_endpoint(
    workspace_client: Any,
    endpoint: str,
    *,
    prompt: str,
    client_request_id: str | None = None,
    task: str | None = None,
    max_tokens: int = 64,
) -> Any:
    return query_serving_endpoint_with_proof(
        workspace_client,
        endpoint,
        prompt=prompt,
        client_request_id=client_request_id,
        task=task,
        max_tokens=max_tokens,
    ).response


def query_serving_endpoint_with_proof(
    workspace_client: Any,
    endpoint: str,
    *,
    prompt: str,
    client_request_id: str | None = None,
    task: str | None = None,
    max_tokens: int = 64,
    return_trace: bool = False,
) -> ServingEndpointExecution:
    if _is_agent_responses_task(task):
        input_messages = [{"role": "user", "content": prompt}]
        body: dict[str, Any] = {
            "model": endpoint,
            "input": input_messages,
            "stream": False,
            "max_output_tokens": max_tokens,
        }
        if client_request_id:
            body["client_request_id"] = client_request_id
        if return_trace:
            body["custom_inputs"] = {
                "databricks_options": {"return_trace": True},
            }
        response = workspace_client.api_client.do("POST", "/serving-endpoints/responses", body=body)
        return ServingEndpointExecution(
            endpoint=endpoint,
            task=task,
            transport="responses_api",
            response=response,
            client_request_id=client_request_id,
        )

    # Use one untyped REST transport. Retrying after an SDK deserialization
    # error can execute the same model request twice because the endpoint may
    # already have returned a successful payload before local parsing failed.
    body = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }
    if client_request_id:
        body["client_request_id"] = client_request_id
    response = workspace_client.api_client.do(
        "POST", f"/serving-endpoints/{endpoint}/invocations", body=body
    )
    return ServingEndpointExecution(
        endpoint=endpoint,
        task=task,
        transport="endpoint_invocation",
        response=response,
        client_request_id=client_request_id,
    )


def serving_response_has_payload(response: Any) -> bool:
    response = _serving_response_value(response)
    if response is None:
        return False
    if isinstance(response, str):
        return bool(response.strip())
    if isinstance(response, list):
        return any(serving_response_has_payload(item) for item in response)
    if isinstance(response, dict):
        status = str(response.get("status") or response.get("state") or "").strip().lower()
        if status in {"cancelled", "canceled", "error", "failed"}:
            return False
        for key in ("text", "content", "output_text", "generated_text", "message"):
            if key in response and serving_response_has_payload(response[key]):
                return True
        for key in ("choices", "predictions", "outputs", "output", "response", "messages"):
            if key in response and serving_response_has_payload(response[key]):
                return True
        return False
    for key in (
        "text",
        "content",
        "output_text",
        "generated_text",
        "message",
        "choices",
        "predictions",
        "outputs",
        "output",
        "response",
        "messages",
    ):
        value = getattr(response, key, None)
        if value is not None and serving_response_has_payload(value):
            return True
    return False


def serving_response_is_terminal_completed(response: Any) -> bool:
    """Return whether a Responses API payload is terminal and successful."""
    value = _serving_response_value(response)
    if isinstance(value, dict):
        status = value.get("status") or value.get("state")
    else:
        status = getattr(value, "status", None) or getattr(value, "state", None)
    raw = getattr(status, "value", status)
    normalized = str(raw or "").strip().lower().replace("-", "_").replace("/", "_")
    return normalized == "completed"


def serving_response_id(response: Any) -> str | None:
    value = _serving_response_value(response)
    if isinstance(value, dict):
        response_id = str(value.get("id") or value.get("response_id") or "").strip()
        return response_id or None
    response_id = str(
        getattr(value, "id", None) or getattr(value, "response_id", None) or ""
    ).strip()
    return response_id or None


def _serving_response_value(response: Any) -> Any:
    for method in ("as_dict", "to_dict"):
        converter = getattr(response, method, None)
        if callable(converter):
            try:
                return converter()
            except Exception:  # noqa: BLE001 - fall back to attribute checks
                pass
    return response


def _is_agent_responses_task(task: object) -> bool:
    raw = getattr(task, "value", task)
    normalized = str(raw or "").strip().lower()
    return normalized.replace("-", "_").replace("/", "_") == "agent_v1_responses"


def _inference_table_columns(
    sql_client: Any, catalog: str, schema: str, table_name: str
) -> set[str]:
    rows = sql_client.execute(
        """
        SELECT column_name
        FROM system.information_schema.columns
        WHERE table_catalog = :catalog
          AND table_schema = :schema
          AND table_name = :table_name
        """,
        {"catalog": catalog, "schema": schema, "table_name": table_name},
    )
    return {str(row.get("column_name") or "").strip().lower() for row in rows}


def count_inference_log_rows(
    sql_client: Any,
    expected_table_prefix: str,
    *,
    client_request_id: str,
) -> int:
    catalog, schema, _table_prefix = _split_three_part_relation(expected_table_prefix)
    total = 0
    for table_name in inference_log_table_names(sql_client, expected_table_prefix):
        # Gateway payload tables are created as a stub and evolve their real
        # schema on the first payload flush (observed live 2026-07-07:
        # mip_agent_gateway_llama_payload had only databricks_request_id and
        # zero rows minutes after a successful call). Introspect columns per
        # poll: equality on client_request_id when the evolved schema has
        # it, else content-match the UUID-grade nonce inside the logged
        # request body, else the stub has logged nothing yet — count zero
        # and let callers keep polling.
        columns = _inference_table_columns(sql_client, catalog, schema, table_name)
        if "client_request_id" in columns:
            predicate = "client_request_id = :client_request_id"
            params: dict[str, str] = {"client_request_id": client_request_id}
        elif "request" in columns:
            predicate = "request LIKE :client_request_marker"
            params = {"client_request_marker": f"%{client_request_id}%"}
        else:
            continue
        rows = sql_client.execute(
            f"SELECT COUNT(*) AS row_count FROM {catalog}.{schema}.{table_name} WHERE {predicate}",
            params,
        )
        if rows:
            total += int(rows[0].get("row_count") or rows[0].get("n") or 0)
    return total


def count_inference_log_rows_by_prefixes(
    sql_client: Any,
    expected_table_prefix: str,
    *,
    client_request_prefixes: list[str],
) -> int:
    if not client_request_prefixes:
        return 0
    catalog, schema, _table_prefix = _split_three_part_relation(expected_table_prefix)
    total = 0
    for table_name in inference_log_table_names(sql_client, expected_table_prefix):
        columns = _inference_table_columns(sql_client, catalog, schema, table_name)
        if "client_request_id" in columns:
            column, value_template = "client_request_id", "{prefix}%"
        elif "request" in columns:
            column, value_template = "request", "%{prefix}%"
        else:
            continue
        parts: list[str] = []
        params: dict[str, str] = {}
        for index, prefix in enumerate(client_request_prefixes):
            key = f"prefix_{index}"
            parts.append(f"{column} LIKE :{key}")
            params[key] = value_template.format(prefix=prefix)
        rows = sql_client.execute(
            f"""
            SELECT COUNT(*) AS row_count
            FROM {catalog}.{schema}.{table_name}
            WHERE {" OR ".join(parts)}
            """,
            params,
        )
        if rows:
            total += int(rows[0].get("row_count") or rows[0].get("n") or 0)
    return total


def inference_log_table_names(sql_client: Any, expected_table_prefix: str) -> list[str]:
    catalog, schema, table_prefix = _split_three_part_relation(expected_table_prefix)
    escaped_table_prefix = _escape_like_literal(table_prefix)
    table_rows = sql_client.execute(
        """
        SELECT table_name
        FROM system.information_schema.tables
        WHERE table_catalog = :catalog
          AND table_schema = :schema
          AND (table_name = :prefix OR table_name LIKE :prefix_like ESCAPE '\\\\')
        ORDER BY table_name
        """,
        {
            "catalog": catalog,
            "schema": schema,
            "prefix": table_prefix,
            "prefix_like": f"{escaped_table_prefix}%",
        },
    )
    names: list[str] = []
    for row in table_rows:
        table_name = str(row.get("table_name") or "").strip()
        if not table_name:
            continue
        if not table_name.startswith(table_prefix):
            continue
        _validate_identifier("table", table_name)
        names.append(table_name)
    return names


def _escape_like_literal(value: str) -> str:
    return (
        value.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", f"{_LIKE_ESCAPE}%")
        .replace("_", f"{_LIKE_ESCAPE}_")
    )


def wait_for_inference_log_increment(
    sql_client: Any,
    expected_table_prefix: str,
    *,
    previous_count: int,
    client_request_id: str,
    timeout_s: float = 90.0,
    interval_s: float = 5.0,
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
