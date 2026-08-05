"""Exact hosted-tool trace validation and cold-start-safe endpoint warmup."""

from __future__ import annotations

import json
import os
import time
from typing import Any
from uuid import uuid4

from mlflow.entities.trace_data import TraceData

from backend.services.capability_serving_probes import query_serving_endpoint

COLD_START_MARKERS = (
    "timed out",
    "timeout",
    "temporarily unavailable",
    "service unavailable",
    "503",
    "scaling from zero",
    "no server available",
)
TOOL_PROBE_PROMPT = (
    "You must invoke the reviewed build_cohort Unity Catalog tool with segment_codes=[itm], "
    "segment_mode=any, and states=[CA]. Return compact JSON only with "
    "tool=fn_build_cohort and cohort_count equal "
    "to the governed aggregate returned by that tool. Do not estimate or acknowledge."
)


def response_proves_build_cohort_tool(
    response: object,
    *,
    expected_count: int,
    expected_tool_name: str = "mip__gold__fn_build_cohort",
) -> bool:
    """Require one successful returned MLflow trace span with exact args and result."""

    for method in ("as_dict", "to_dict"):
        converter = getattr(response, method, None)
        if callable(converter):
            try:
                response = converter()
                break
            except Exception:  # noqa: BLE001 - validate the remaining representation
                pass

    def decoded(value: object) -> object:
        if not isinstance(value, str):
            return value
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value

    def has_exact_args(value: object) -> bool:
        value = decoded(value)
        expected = {
            "segment_codes": ["itm"],
            "segment_mode": "any",
            "states": ["CA"],
        }
        if value == expected:
            return True
        if isinstance(value, dict) and len(value) == 1:
            key, nested = next(iter(value.items()))
            if str(key).casefold() in {"arguments", "inputs", "parameters"}:
                return has_exact_args(nested)
        return False

    def has_expected_result(value: object) -> bool:
        value = decoded(value)
        if not isinstance(value, dict) or set(value) != {"result"}:
            return False
        result = decoded(value["result"])
        if not isinstance(result, dict) or set(result) != {
            "is_truncated",
            "columns",
            "rows",
        }:
            return False
        if result["is_truncated"] is not False or result["columns"] != ["output"]:
            return False
        rows = result["rows"]
        if not isinstance(rows, list) or len(rows) != 1:
            return False
        row = rows[0]
        if not isinstance(row, list) or len(row) != 1:
            return False
        count = row[0]
        return isinstance(count, int) and not isinstance(count, bool) and count == expected_count

    def exact_success_status(value: object) -> bool:
        value = decoded(value)
        value = getattr(value, "value", value)
        if isinstance(value, str):
            return value.upper() in {"OK", "STATUS_CODE_OK"}
        if isinstance(value, dict) and set(value).issubset({"code", "message"}):
            return "code" in value and exact_success_status(value["code"])
        return False

    def successful_span(value: object) -> bool:
        name = str(getattr(value, "name", None) or "")
        span_type = str(getattr(value, "span_type", None) or "").upper()
        status = getattr(getattr(value, "status", None), "status_code", None)
        inputs = getattr(value, "inputs", None)
        outputs = getattr(value, "outputs", None)
        return (
            name == expected_tool_name
            and span_type == "TOOL"
            and exact_success_status(status)
            and has_exact_args(inputs)
            and has_expected_result(outputs)
        )

    if not isinstance(response, dict):
        return False
    custom_outputs = response.get("custom_outputs")
    if not isinstance(custom_outputs, dict) or set(custom_outputs) != {
        "upstream_databricks_output"
    }:
        return False
    upstream_output = custom_outputs["upstream_databricks_output"]
    if not isinstance(upstream_output, dict) or set(upstream_output) != {"trace"}:
        return False
    trace = upstream_output["trace"]
    if not isinstance(trace, dict) or set(trace) != {"info", "data"}:
        return False
    data = trace["data"]
    if not isinstance(data, dict) or set(data) != {"spans"}:
        return False
    try:
        spans = TraceData.from_dict(data).spans
    except Exception:  # noqa: BLE001 - malformed platform trace is not proof
        return False
    target_spans = [
        span for span in spans if str(getattr(span, "name", None) or "") == expected_tool_name
    ]
    return len(target_spans) == 1 and successful_span(target_spans[0])


def is_cold_start_error(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    message = str(exc).lower()
    return any(marker in message for marker in COLD_START_MARKERS)


def warm_endpoint_with_cold_start_patience(
    workspace: Any,
    endpoint: str,
    *,
    prompt: str,
    task: str,
    warmup_timeout_s: float | None = None,
    interval_s: float = 20.0,
    sleep: Any = time.sleep,
) -> Any:
    """Warm an endpoint with unique non-proof IDs until its cold-start budget ends."""

    if warmup_timeout_s is None:
        warmup_timeout_s = float(os.environ.get("MIP_AI_GATEWAY_WARMUP_TIMEOUT_S", "600"))
    deadline = time.monotonic() + max(0.0, warmup_timeout_s)
    attempt = 0
    while True:
        attempt += 1
        warmup_request_id = f"mip-warmup-{uuid4().hex}"
        try:
            return query_serving_endpoint(
                workspace,
                endpoint,
                task=task,
                prompt=prompt,
                client_request_id=warmup_request_id,
            )
        except Exception as exc:  # noqa: BLE001 - classified below, re-raised when not cold-start
            if not is_cold_start_error(exc) or time.monotonic() >= deadline:
                raise
            print(
                "[ai-gateway-verify] configured endpoint looks cold "
                f"(attempt {attempt}: {type(exc).__name__}); retrying in {int(interval_s)}s"
            )
            sleep(interval_s)
