"""AI Gateway live capability proof."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from backend.config.settings import Settings
from backend.services.capability_serving_probes import (
    count_inference_log_rows,
    inference_log_table_names,
    query_serving_endpoint,
    serving_response_has_payload,
    wait_for_inference_log_increment,
)


def probe_ai_gateway(
    workspace_client: Any,
    settings: Settings,
    *,
    sql_client: Any | None,
    make_status: Callable[[bool, str], Any],
    request_prefix: str,
    exact_log_wait_s: float,
    exact_log_attempts: int,
) -> Any:
    endpoint = (settings.mip_ai_gateway_endpoint or "").strip()
    expected_table = (settings.mip_ai_gateway_inference_table or "").strip()
    if not endpoint or not expected_table:
        return make_status(False, "AI Gateway endpoint or inference table is not configured.")
    if sql_client is None:
        return make_status(False, "SQL client is required to verify AI Gateway inference log rows.")
    try:
        details = workspace_client.serving_endpoints.get(endpoint)
        state = _enum_value(getattr(getattr(details, "state", None), "ready", None))
        gateway = getattr(details, "ai_gateway", None)
        inference = getattr(gateway, "inference_table_config", None)
        if state != "READY":
            return make_status(False, f"Gateway endpoint {endpoint} is not READY ({state}).")
        if not bool(getattr(inference, "enabled", False)):
            return make_status(False, f"Gateway endpoint {endpoint} inference table is not enabled.")
        actual = ".".join(
            part
            for part in (
                getattr(inference, "catalog_name", None),
                getattr(inference, "schema_name", None),
                getattr(inference, "table_name_prefix", None),
            )
            if part
        )
        if expected_table and actual != expected_table:
            return make_status(False, f"Gateway inference table is {actual}, expected {expected_table}.")
        table_names = inference_log_table_names(sql_client, expected_table)
        if not table_names:
            return make_status(
                False,
                f"No AI Gateway inference tables matching {expected_table} were visible to SQL.",
            )
        sha = ai_gateway_probe_sha(settings)
        if not sha:
            return make_status(
                False,
                "MIP_GIT_SHA is required to prove AI Gateway logging matches this deployment.",
            )
        attempted_ids: list[str] = []
        for _attempt in range(exact_log_attempts):
            client_request_id = f"{request_prefix}{sha}-{uuid4().hex[:16]}"
            attempted_ids.append(client_request_id)
            before_rows = count_inference_log_rows(
                sql_client,
                expected_table,
                client_request_id=client_request_id,
            )
            response = query_serving_endpoint(
                workspace_client,
                endpoint,
                prompt=(
                    "Capability readiness check. Reply with a one-sentence acknowledgement "
                    "for Mortgage Intelligence Platform AI Gateway logging."
                ),
                client_request_id=client_request_id,
            )
            if not serving_response_has_payload(response):
                return make_status(False, f"Gateway endpoint {endpoint} returned no response payload.")
            log_rows = wait_for_inference_log_increment(
                sql_client,
                expected_table,
                previous_count=before_rows,
                client_request_id=client_request_id,
                timeout_s=exact_log_wait_s,
            )
            if log_rows > before_rows:
                return make_status(
                    True,
                    (
                        "Live AI Gateway endpoint accepted a bounded query and exposed an exact "
                        f"inference log row ({client_request_id}) at {actual}."
                    ),
                )
        return make_status(
            False,
            (
                "Live AI Gateway endpoint accepted bounded queries and inference logging "
                f"is enabled/queryable at {actual}; exact probe rows {', '.join(attempted_ids)} "
                "were not visible inside the bounded window, so row-level delivery remains "
                "unproven and this capability is not claimable yet."
            ),
        )
    except Exception as exc:  # noqa: BLE001
        return make_status(False, f"AI Gateway probe failed ({type(exc).__name__}).")


def ai_gateway_probe_sha(settings: Settings) -> str | None:
    raw = (settings.mip_git_sha or "").strip().lower()
    if len(raw) < 7:
        return None
    if not all(ch in "0123456789abcdef" for ch in raw):
        return None
    return raw[:12]


def _enum_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "")
