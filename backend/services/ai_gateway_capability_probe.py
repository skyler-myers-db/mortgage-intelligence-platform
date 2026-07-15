"""AI Gateway live capability proof."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from backend.config.settings import Settings
from backend.services.ai_gateway_proof_ledger import (
    bounded_gateway_proof_freshness_s,
    latest_verified_proof,
    normalize_gateway_sha,
)
from backend.services.capability_serving_probes import (
    count_inference_log_rows_by_prefixes,
    inference_log_table_names,
    query_serving_endpoint_with_proof,
)


def probe_ai_gateway(
    workspace_client: Any,
    settings: Settings,
    *,
    sql_client: Any | None,
    lakebase: Any | None,
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
    if lakebase is None:
        return make_status(
            False, "Lakebase proof ledger is required to verify AI Gateway exact rows."
        )
    verify_key = (settings.mip_ai_gateway_proof_verify_key or "").strip()
    if not verify_key:
        return make_status(
            False,
            "AI Gateway proof verification key is not configured for this deployment.",
        )
    try:
        details = workspace_client.serving_endpoints.get(endpoint)
        state = _enum_value(getattr(getattr(details, "state", None), "ready", None))
        task = getattr(details, "task", None)
        gateway = getattr(details, "ai_gateway", None)
        inference = getattr(gateway, "inference_table_config", None)
        if state != "READY":
            return make_status(False, f"Gateway endpoint {endpoint} is not READY ({state}).")
        if not bool(getattr(inference, "enabled", False)):
            return make_status(
                False, f"Gateway endpoint {endpoint} inference table is not enabled."
            )
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
            return make_status(
                False, f"Gateway inference table is {actual}, expected {expected_table}."
            )
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
        _ = exact_log_wait_s, exact_log_attempts
        live_request_id = f"{request_prefix}{sha}-{uuid4().hex[:16]}"
        execution = query_serving_endpoint_with_proof(
            workspace_client,
            endpoint,
            task=str(task or ""),
            prompt=(
                "Capability readiness check. Reply with a one-sentence acknowledgement "
                "for Mortgage Intelligence Platform AI Gateway logging."
            ),
            client_request_id=live_request_id,
        )
        if not execution.proves_agent_response:
            return make_status(
                False,
                f"Gateway endpoint {endpoint} did not return a terminal completed Responses payload.",
            )
        proof = latest_verified_proof(
            lakebase,
            git_sha=sha,
            endpoint_name=endpoint,
            inference_table=expected_table,
            freshness_s=bounded_gateway_proof_freshness_s(
                settings.mip_ai_gateway_proof_freshness_s
            ),
            attestation_verify_key=verify_key,
        )
        current_sha_rows = count_inference_log_rows_by_prefixes(
            sql_client,
            expected_table,
            client_request_prefixes=[
                f"{request_prefix}{sha}-",
                f"mip-agent-run-{sha}-",
            ],
        )
        if proof is not None:
            return make_status(
                True,
                (
                    "Live AI Gateway endpoint accepted a bounded query now; independently signed "
                    "exact inference-row round-trip verified for deployment "
                    f"{sha} at {proof.verified_at.isoformat()} "
                    f"(delivery {proof.verify_latency_s:.1f}s). Current deployment inference rows "
                    f"visible: {current_sha_rows}."
                ),
            )
        return make_status(
            False,
            (
                "Live AI Gateway endpoint accepted a bounded query now and inference logging "
                f"is enabled/queryable at {actual}; no fresh ledger-verified exact row exists "
                f"for deployment {sha}, or its signature did not verify, so this capability is "
                "not claimable yet. Current "
                f"deployment inference rows visible: {current_sha_rows}."
            ),
        )
    except Exception as exc:  # noqa: BLE001
        return make_status(False, f"AI Gateway probe failed ({type(exc).__name__}).")


def ai_gateway_probe_sha(settings: Settings) -> str | None:
    return normalize_gateway_sha(settings.mip_git_sha)


def _enum_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "")
