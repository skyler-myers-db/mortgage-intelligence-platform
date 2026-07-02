#!/usr/bin/env python
"""Send and verify exact AI Gateway inference-row proof.

This tool is intentionally the only writer for
``mip_app.ai_gateway_proof_ledger``. Runtime app probes read verified rows from
the ledger but never write them, so users cannot mint capability proof through
public API traffic.
"""

# ruff: noqa: E402,I001

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from databricks.sdk import WorkspaceClient

from backend.config.settings import get_settings
from backend.services.ai_gateway_proof_ledger import (
    AiGatewayVerifiedProof,
    insert_pending_proof,
    latest_verified_proof,
    list_pending_proofs,
    mark_expired_pending_proofs,
    mark_proof_verified,
    normalize_gateway_sha,
)
from backend.services.capability_serving_probes import (
    count_inference_log_rows,
    query_serving_endpoint,
    serving_response_has_payload,
)
from backend.services.databricks_sql import get_sql_client
from backend.services.lakebase import get_lakebase_client


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("verify-pending", "send"))
    parser.add_argument("--git-sha", default=os.environ.get("MIP_GIT_SHA"))
    parser.add_argument("--endpoint", default=os.environ.get("MIP_AI_GATEWAY_ENDPOINT"))
    parser.add_argument("--inference-table", default=os.environ.get("MIP_AI_GATEWAY_INFERENCE_TABLE"))
    parser.add_argument("--timeout-s", type=int, default=int(os.environ.get("MIP_AI_GATEWAY_VERIFY_TIMEOUT_S", "1200")))
    parser.add_argument("--interval-s", type=int, default=int(os.environ.get("MIP_AI_GATEWAY_VERIFY_INTERVAL_S", "45")))
    parser.add_argument("--expiry-s", type=int, default=int(os.environ.get("MIP_AI_GATEWAY_VERIFY_EXPIRY_S", "21600")))
    parser.add_argument("--wait", action="store_true", help="After send, poll the exact id until verified or timeout.")
    parser.add_argument("--require-verified", action="store_true", help="Exit non-zero when no current-SHA proof is verified.")
    parser.add_argument("--json", action="store_true", help="Emit a machine-readable summary.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.timeout_s > 3600:
        raise ValueError("MIP_AI_GATEWAY_VERIFY_TIMEOUT_S must not exceed 3600 seconds")
    if args.interval_s <= 0:
        raise ValueError("MIP_AI_GATEWAY_VERIFY_INTERVAL_S must be positive")
    settings = get_settings()
    git_sha = _resolved_sha(args.git_sha or settings.mip_git_sha)
    endpoint = (args.endpoint or settings.mip_ai_gateway_endpoint or "").strip()
    inference_table = (args.inference_table or settings.mip_ai_gateway_inference_table or "").strip()
    if not endpoint:
        raise ValueError("AI Gateway endpoint is required")
    if not inference_table:
        raise ValueError("AI Gateway inference table is required")

    lakebase = get_lakebase_client()
    sql_client = get_sql_client()
    workspace = WorkspaceClient()
    expired = mark_expired_pending_proofs(
        lakebase,
        older_than=datetime.now(UTC) - timedelta(seconds=max(1, args.expiry_s)),
        git_sha=git_sha,
    )
    verified = verify_pending(
        lakebase=lakebase,
        sql_client=sql_client,
        git_sha=git_sha,
        endpoint=endpoint,
        inference_table=inference_table,
        limit=100,
    )
    sent: AiGatewayVerifiedProof | None = None
    if args.mode == "send":
        sent = send_probe(
            lakebase=lakebase,
            workspace=workspace,
            endpoint=endpoint,
            inference_table=inference_table,
            git_sha=git_sha,
        )
        if args.wait:
            verified.extend(
                wait_for_exact_row(
                    lakebase=lakebase,
                    sql_client=sql_client,
                    proof=sent,
                    timeout_s=args.timeout_s,
                    interval_s=args.interval_s,
                )
            )

    latest_current = latest_verified_proof(
        lakebase,
        git_sha=git_sha,
        endpoint_name=endpoint,
        inference_table=inference_table,
        freshness_s=max(1.0, float(settings.mip_ai_gateway_proof_freshness_s or args.expiry_s)),
    )
    verified_current = [
        proof
        for proof in verified
        if proof.git_sha == git_sha
        and proof.endpoint_name == endpoint
        and proof.inference_table == inference_table
    ]
    if latest_current is not None and all(
        proof.proof_id != latest_current.proof_id for proof in verified_current
    ):
        verified_current.append(latest_current)
    summary = {
        "mode": args.mode,
        "git_sha": git_sha,
        "sent": _proof_json(sent) if sent else None,
        "verified": [_proof_json(proof) for proof in verified],
        "latest_verified": _proof_json(latest_current),
        "expired_pending": expired,
        "current_config_verified": bool(verified_current),
    }
    _emit(summary, as_json=args.json)
    if args.require_verified and not verified_current:
        return 1
    return 0


def send_probe(
    *,
    lakebase: Any,
    workspace: WorkspaceClient,
    endpoint: str,
    inference_table: str,
    git_sha: str,
) -> AiGatewayVerifiedProof:
    details = workspace.serving_endpoints.get(endpoint)
    task = getattr(details, "task", None)
    client_request_id = f"mip-capability-{git_sha}-{uuid4().hex[:16]}"
    response = query_serving_endpoint(
        workspace,
        endpoint,
        task=str(task or ""),
        prompt=(
            "Capability exact-proof check. Reply with a one-sentence acknowledgement "
            "for Mortgage Intelligence Platform AI Gateway logging."
        ),
        client_request_id=client_request_id,
    )
    if not serving_response_has_payload(response):
        raise RuntimeError(f"Gateway endpoint {endpoint} returned no response payload")
    proof = insert_pending_proof(
        lakebase,
        git_sha=git_sha,
        client_request_id=client_request_id,
        endpoint_name=endpoint,
        inference_table=inference_table,
    )
    print(
        "[ai-gateway-proof] sent probe "
        f"client_request_id={client_request_id} endpoint={endpoint} table={inference_table}"
    )
    return proof


def verify_pending(
    *,
    lakebase: Any,
    sql_client: Any,
    git_sha: str,
    endpoint: str,
    inference_table: str,
    limit: int,
) -> list[AiGatewayVerifiedProof]:
    verified: list[AiGatewayVerifiedProof] = []
    for proof in list_pending_proofs(lakebase, git_sha=git_sha, limit=limit):
        if proof.endpoint_name != endpoint or proof.inference_table != inference_table:
            continue
        if _exact_row_count(sql_client, proof) > 0:
            updated = mark_proof_verified(
                lakebase,
                proof_id=proof.proof_id,
                sent_at=proof.sent_at,
            )
            print(
                "[ai-gateway-proof] verified pending "
                f"client_request_id={updated.client_request_id} latency_s={updated.verify_latency_s:.1f}"
            )
            verified.append(updated)
    return verified


def wait_for_exact_row(
    *,
    lakebase: Any,
    sql_client: Any,
    proof: AiGatewayVerifiedProof,
    timeout_s: int,
    interval_s: int,
) -> list[AiGatewayVerifiedProof]:
    deadline = time.monotonic() + timeout_s
    while True:
        if _exact_row_count(sql_client, proof) > 0:
            updated = mark_proof_verified(
                lakebase,
                proof_id=proof.proof_id,
                sent_at=proof.sent_at,
            )
            print(
                "[ai-gateway-proof] verified sent "
                f"client_request_id={updated.client_request_id} latency_s={updated.verify_latency_s:.1f}"
            )
            return [updated]
        if time.monotonic() >= deadline:
            print(
                "[ai-gateway-proof] exact row not visible before timeout; "
                f"left pending client_request_id={proof.client_request_id}"
            )
            return []
        time.sleep(interval_s)


def _exact_row_count(sql_client: Any, proof: AiGatewayVerifiedProof) -> int:
    return count_inference_log_rows(
        sql_client,
        proof.inference_table,
        client_request_id=proof.client_request_id,
    )


def _resolved_sha(raw: str | None) -> str:
    sha = normalize_gateway_sha(raw)
    if sha is None:
        raise ValueError("A valid MIP_GIT_SHA/--git-sha is required")
    return sha


def _proof_json(proof: AiGatewayVerifiedProof | None) -> dict[str, object] | None:
    if proof is None:
        return None
    return {
        "proof_id": proof.proof_id,
        "git_sha": proof.git_sha,
        "client_request_id": proof.client_request_id,
        "endpoint_name": proof.endpoint_name,
        "inference_table": proof.inference_table,
        "sent_at": proof.sent_at.isoformat(),
        "verified_at": proof.verified_at.isoformat() if proof.status == "verified" else None,
        "verify_latency_s": proof.verify_latency_s if proof.status == "verified" else None,
        "status": proof.status,
    }


def _emit(summary: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(
            "[ai-gateway-proof] summary "
            f"mode={summary['mode']} git_sha={summary['git_sha']} "
            f"current_config_verified={summary['current_config_verified']}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
