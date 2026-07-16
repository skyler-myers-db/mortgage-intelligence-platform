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
import math
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config
from pydantic import SecretStr

from backend.config.settings import get_settings
from backend.services.ai_gateway_proof_attestation import (
    derive_gateway_proof_verify_key,
    sign_gateway_proof,
)
from backend.services.ai_gateway_proof_ledger import (
    AI_GATEWAY_PROOF_CLOCK_SKEW_S,
    AiGatewayVerifiedProof,
    insert_pending_proof,
    latest_verified_proof,
    list_pending_proofs,
    mark_expired_pending_proofs,
    normalize_gateway_sha,
)
from backend.services.capability_serving_probes import (
    _inference_table_columns,
    _split_three_part_relation,
    inference_log_table_names,
    query_serving_endpoint,
    query_serving_endpoint_with_proof,
    serving_response_has_payload,
    serving_response_is_terminal_completed,
)
from backend.services.databricks_sql import DatabricksSqlClient
from backend.services.lakebase import get_lakebase_client


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("verify-pending", "send"))
    parser.add_argument("--git-sha", default=os.environ.get("MIP_GIT_SHA"))
    parser.add_argument("--endpoint", default=os.environ.get("MIP_AI_GATEWAY_ENDPOINT"))
    parser.add_argument(
        "--inference-table", default=os.environ.get("MIP_AI_GATEWAY_INFERENCE_TABLE")
    )
    parser.add_argument(
        "--timeout-s",
        type=int,
        default=int(os.environ.get("MIP_AI_GATEWAY_VERIFY_TIMEOUT_S", "1200")),
    )
    parser.add_argument(
        "--interval-s",
        type=int,
        default=int(os.environ.get("MIP_AI_GATEWAY_VERIFY_INTERVAL_S", "45")),
    )
    parser.add_argument(
        "--expiry-s",
        type=int,
        default=int(os.environ.get("MIP_AI_GATEWAY_VERIFY_EXPIRY_S", "21600")),
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="After send, poll the exact id until verified or timeout.",
    )
    parser.add_argument(
        "--require-verified",
        action="store_true",
        help="Exit non-zero when no current-SHA proof is verified.",
    )
    parser.add_argument(
        "--require-verifier-derived-auth",
        action="store_true",
        help=(
            "Derive endpoint, inference-table, and proof-ledger access only from the "
            "dedicated verifier OAuth identity. Required for claimable proof."
        ),
    )
    parser.add_argument(
        "--warehouse-id",
        default=os.environ.get("DATABRICKS_WAREHOUSE_ID"),
        help="SQL warehouse used by the dedicated verifier identity.",
    )
    parser.add_argument("--json", action="store_true", help="Emit a machine-readable summary.")
    return parser


def _attestation_signing_key() -> str:
    signing_key = os.environ.get("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", "").strip()
    if not signing_key:
        raise ValueError("MIP_AI_GATEWAY_PROOF_SIGNING_KEY is required")
    derive_gateway_proof_verify_key(signing_key)
    return signing_key


def _workspace_client() -> Any:
    """Late-bound client factory — the test seam.

    Scale-to-zero gateway endpoints hold cold-start requests longer than the
    SDK's 60s default read timeout, so the real client gets a 300s HTTP timeout.
    SDK retries are disabled: warmup retries explicitly with fresh non-proof
    ids, while the exact proof id must be submitted at most once. Both Config
    and the client resolve credentials at construction, so tests monkeypatch
    this factory rather than WorkspaceClient.
    """
    host = os.environ.get("DATABRICKS_HOST", "").strip()
    client_id = os.environ.get("DATABRICKS_CLIENT_ID", "").strip()
    client_secret = os.environ.get("DATABRICKS_CLIENT_SECRET", "").strip()
    if not host or not client_id or not client_secret:
        raise ValueError("dedicated verifier DATABRICKS_HOST/CLIENT_ID/CLIENT_SECRET are required")
    return WorkspaceClient(
        config=Config(
            host=host,
            client_id=client_id,
            client_secret=client_secret,
            auth_type="oauth-m2m",
            http_timeout_seconds=300,
            retry_timeout_seconds=0,
        )
    )


def _verifier_sql_client(workspace: Any, *, warehouse_id: str) -> DatabricksSqlClient:
    """Build inference-table access from the exact verifier SDK client."""

    host = str(workspace.config.host or "").strip()
    if not host or not warehouse_id.strip():
        raise ValueError("verifier workspace host and --warehouse-id are required")

    def token_provider() -> str:
        headers = workspace.config.authenticate()
        authorization = str(headers.get("Authorization") or "")
        if not authorization.startswith("Bearer "):
            raise RuntimeError("verifier workspace auth returned no bearer token")
        token = authorization.removeprefix("Bearer ").strip()
        if not token:
            raise RuntimeError("verifier workspace auth returned an empty bearer token")
        return token

    return DatabricksSqlClient(
        host=host,
        token=token_provider,
        warehouse_id=warehouse_id.strip(),
    )


def ensure_lakebase_env(
    workspace_factory: Any = None,
    *,
    force_refresh: bool = False,
) -> bool:
    """Mint Lakebase connection env from the Databricks identity when absent.

    ``workspace_factory`` late-binds to the module's ``WorkspaceClient`` at
    call time: a def-time default froze the real class, so monkeypatched
    fakes never reached the mint path and unit tests silently performed
    LIVE workspace calls wherever ambient CLI auth existed — red in CI
    (no credentials), green-by-accident locally (CI run 28945210525,
    2026-07-08).

    The proof ledger lives in Lakebase. On Databricks Apps the platform
    injects connection env; on a deploy laptop or a CI runner nothing does,
    and the settings default (localhost:5432) fails closed with a confusing
    connection error (observed 2026-07-07, deploy step 18). When
    ``LAKEBASE_HOST`` is unset, resolve the instance DNS and a short-lived
    OAuth database credential via the same workspace identity every other
    deploy step already uses — no .env.local hand-editing required. Explicit
    env pointing at a real host always wins; localhost/127.0.0.1 values
    (stale local-postgres leftovers in .env.local) are treated as absent,
    because the proof ledger can only live in the real Lakebase instance.
    Returns True when env was minted here.
    """

    def _is_real_host(value: str | None) -> bool:
        host = (value or "").strip().lower()
        return bool(host) and host not in {"localhost", "127.0.0.1", "::1"}

    if not force_refresh and _is_real_host(os.environ.get("LAKEBASE_HOST")):
        return False
    stale = get_settings()
    if not force_refresh and _is_real_host(stale.lakebase_host):
        # A real host from .env.local is fine; only absent or localhost-ish
        # resolution needs minting.
        return False
    if (stale.lakebase_host or "").strip():
        print(
            "[ai-gateway-verify] ignoring localhost Lakebase config from .env.local — "
            "the proof ledger must live in the real mip-app-state instance."
        )
    instance = (os.environ.get("MIP_LAKEBASE_INSTANCE") or "mip-app-state").strip()
    factory = workspace_factory if workspace_factory is not None else WorkspaceClient
    workspace = factory()
    dns = workspace.database.get_database_instance(instance).read_write_dns
    credential = workspace.database.generate_database_credential(
        instance_names=[instance],
        request_id=str(uuid4()),
    )
    user_name = workspace.current_user.me().user_name
    # PG* is the load-bearing set: backend.services.lakebase resolves
    # ``settings.lakebase_host or PGHOST`` (etc.) against a module-level
    # settings singleton bound at import time, so freshly-minted LAKEBASE_*
    # values are invisible to it (observed 2026-07-07: minting succeeded but
    # psycopg still dialed localhost). The PG* fallbacks read live os.environ
    # through that same stale object, which is exactly the Apps pathway.
    database = "mip_app_state"
    os.environ.update(
        {
            "PGHOST": str(dns),
            "PGPORT": "5432",
            "PGDATABASE": database,
            "PGUSER": str(user_name),
            "PGPASSWORD": str(credential.token),
            "PGSSLMODE": "require",
            # LAKEBASE_* kept for subprocesses and freshly-imported settings.
            "LAKEBASE_HOST": str(dns),
            "LAKEBASE_PORT": "5432",
            "LAKEBASE_DATABASE": database,
            "LAKEBASE_USER": str(user_name),
            "LAKEBASE_PASSWORD": str(credential.token),
            "LAKEBASE_SSLMODE": "require",
        }
    )
    # The import-time settings singleton (bound by backend.services.lakebase
    # at module import) may carry truthy stale .env.local values such as
    # localhost/mip/mip, which shadow every PG* fallback in the resolver.
    # cache_clear() only affects future get_settings() calls, so overwrite
    # the live object the resolver actually reads.
    stale.lakebase_host = str(dns)
    stale.lakebase_port = 5432
    stale.lakebase_database = database
    stale.lakebase_user = str(user_name)
    stale.lakebase_password = SecretStr(str(credential.token))
    stale.lakebase_sslmode = os.environ["LAKEBASE_SSLMODE"]
    get_settings.cache_clear()
    print(f"[ai-gateway-verify] minted Lakebase credentials for {instance} ({user_name})")
    return True


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.timeout_s > 3600:
        raise ValueError("MIP_AI_GATEWAY_VERIFY_TIMEOUT_S must not exceed 3600 seconds")
    if args.interval_s <= 0:
        raise ValueError("MIP_AI_GATEWAY_VERIFY_INTERVAL_S must be positive")
    if not args.require_verifier_derived_auth:
        raise ValueError("exact proof requires --require-verifier-derived-auth for every mode")
    attestation_verify_key = derive_gateway_proof_verify_key(_attestation_signing_key())
    workspace = _workspace_client()
    if not (args.warehouse_id or "").strip():
        raise ValueError("--warehouse-id is required for verifier-derived proof")
    ensure_lakebase_env(lambda: workspace, force_refresh=True)
    settings = get_settings()
    git_sha = _resolved_sha(args.git_sha or settings.mip_git_sha)
    endpoint = (args.endpoint or settings.mip_ai_gateway_endpoint or "").strip()
    inference_table = (
        args.inference_table or settings.mip_ai_gateway_inference_table or ""
    ).strip()
    if not endpoint:
        raise ValueError("AI Gateway endpoint is required")
    if not inference_table:
        raise ValueError("AI Gateway inference table is required")

    lakebase = get_lakebase_client()
    sql_client = _verifier_sql_client(workspace, warehouse_id=args.warehouse_id)
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
        attestation_verify_key=attestation_verify_key,
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
    sent_verified = sent is not None and any(
        proof.proof_id == sent.proof_id and proof.status == "verified" for proof in verified
    )
    summary = {
        "mode": args.mode,
        "git_sha": git_sha,
        "sent": _proof_json(sent) if sent else None,
        "verified": [_proof_json(proof) for proof in verified],
        "latest_verified": _proof_json(latest_current),
        "expired_pending": expired,
        "current_config_verified": bool(verified_current),
        "sent_verified": sent_verified if sent else None,
    }
    _emit(summary, as_json=args.json)
    if args.require_verified:
        if sent is not None and args.wait:
            return 0 if sent_verified else 1
        if not verified_current:
            return 1
    return 0


_COLD_START_MARKERS = (
    "timed out",
    "timeout",
    "temporarily unavailable",
    "service unavailable",
    "503",
    "scaling from zero",
    "no server available",
)


def _is_cold_start_error(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    message = str(exc).lower()
    return any(marker in message for marker in _COLD_START_MARKERS)


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
    """Warm the endpoint without ever reusing an exact-proof request id.

    A cold llama endpoint can take minutes to warm; the platform holds the
    request until the SDK read timeout trips (observed 2026-07-07, deploy
    step 18). Each retry gets a fresh non-proof id because a timed-out request
    may still execute server-side. Non-timeout errors raise immediately.
    """
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
            if not _is_cold_start_error(exc) or time.monotonic() >= deadline:
                raise
            print(
                "[ai-gateway-verify] configured endpoint looks cold "
                f"(attempt {attempt}: {type(exc).__name__}); retrying in {int(interval_s)}s"
            )
            sleep(interval_s)


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
    try:
        warm_endpoint_with_cold_start_patience(
            workspace,
            endpoint,
            task=str(task or ""),
            prompt="AI Gateway warmup check.",
        )
    except Exception as exc:  # noqa: BLE001 - only cold-start failures may proceed
        if not _is_cold_start_error(exc):
            raise
        print(
            "[ai-gateway-verify] warmup remained unresolved; sending the exact proof request once"
        )

    client_request_id = f"mip-capability-{git_sha}-{uuid4().hex[:16]}"
    proof = insert_pending_proof(
        lakebase,
        git_sha=git_sha,
        client_request_id=client_request_id,
        endpoint_name=endpoint,
        inference_table=inference_table,
    )
    try:
        execution = query_serving_endpoint_with_proof(
            workspace,
            endpoint,
            task=str(task or ""),
            prompt=(
                "Capability exact-proof check. Reply with a one-sentence acknowledgement "
                "for Mortgage Intelligence Platform AI Gateway logging."
            ),
            client_request_id=client_request_id,
        )
    except Exception as exc:  # noqa: BLE001 - timeout/503 is an ambiguous submission result
        if _is_cold_start_error(exc):
            print(
                "[ai-gateway-proof] exact probe submission unresolved after one request; "
                f"left pending proof_id={proof.proof_id}"
            )
            return proof
        _mark_proof_failed_if_pending(lakebase, proof)
        raise

    if not execution.proves_agent_response:
        _mark_proof_failed_if_pending(lakebase, proof)
        raise RuntimeError(
            "Configured AI Gateway Responses endpoint did not return a terminal completed payload"
        )
    print(f"[ai-gateway-proof] sent probe proof_id={proof.proof_id}")
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
        check = _check_exact_inference_row(sql_client, proof)
        if check.outcome == "verified":
            updated = _mark_proof_verified_if_pending(lakebase, proof)
            if updated is None:
                continue
            print(
                "[ai-gateway-proof] verified pending "
                f"proof_id={updated.proof_id} latency_s={updated.verify_latency_s:.1f}"
            )
            verified.append(updated)
        elif check.outcome == "failed":
            if _mark_proof_failed_if_pending(lakebase, proof):
                print(
                    "[ai-gateway-proof] rejected deterministic inference evidence; "
                    f"marked failed proof_id={proof.proof_id} reason={check.reason}"
                )
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
        check = _check_exact_inference_row(sql_client, proof)
        if check.outcome == "verified":
            updated = _mark_proof_verified_if_pending(lakebase, proof)
            if updated is None:
                return []
            print(
                "[ai-gateway-proof] verified sent "
                f"proof_id={updated.proof_id} latency_s={updated.verify_latency_s:.1f}"
            )
            return [updated]
        if check.outcome == "failed":
            _mark_proof_failed_if_pending(lakebase, proof)
            print(
                "[ai-gateway-proof] rejected deterministic inference evidence; "
                f"marked failed proof_id={proof.proof_id} reason={check.reason}"
            )
            return []
        if time.monotonic() >= deadline:
            print(
                "[ai-gateway-proof] exact row not visible before timeout; "
                f"left pending proof_id={proof.proof_id}"
            )
            return []
        time.sleep(interval_s)


_ExactRowOutcome = Literal["pending", "verified", "failed"]
_ExactRowReason = Literal[
    "not_visible",
    "schema_unsubstantiated",
    "duplicate_rows",
    "non_success_status",
    "nonterminal_response",
    "timestamp_out_of_bounds",
    "verified",
]


@dataclass(frozen=True)
class _ExactRowCheck:
    outcome: _ExactRowOutcome
    reason: _ExactRowReason


def _check_exact_inference_row(
    sql_client: Any,
    proof: AiGatewayVerifiedProof,
) -> _ExactRowCheck:
    """Require one successful, timely terminal Responses row for the exact proof id."""

    catalog, schema, table_prefix = _split_three_part_relation(proof.inference_table)
    observed_now = datetime.now(UTC)
    clock_tolerance = timedelta(seconds=AI_GATEWAY_PROOF_CLOCK_SKEW_S)
    if proof.sent_at > observed_now + clock_tolerance:
        return _ExactRowCheck("failed", "timestamp_out_of_bounds")
    event_time_lower_bound = proof.sent_at - clock_tolerance
    event_time_upper_bound = min(
        proof.sent_at + clock_tolerance,
        observed_now + clock_tolerance,
    )
    substantiated_rows: list[tuple[dict[str, Any], str]] = []
    unsubstantiated_matches = 0
    for table_name in inference_log_table_names(sql_client, proof.inference_table):
        if not table_name.startswith(table_prefix):
            continue
        columns = _inference_table_columns(sql_client, catalog, schema, table_name)
        predicate_and_params = _exact_row_predicate(columns, proof.client_request_id)
        if predicate_and_params is None:
            continue
        predicate, params = predicate_and_params
        timestamp_column = _inference_timestamp_column(columns)
        if not {"status_code", "response"}.issubset(columns) or timestamp_column is None:
            unsubstantiated_matches += _count_matching_rows(
                sql_client,
                relation=f"{catalog}.{schema}.{table_name}",
                predicate=predicate,
                params=params,
            )
            continue
        timestamp_projection, timestamp_params = _bounded_timestamp_projection(
            timestamp_column,
            lower_bound=event_time_lower_bound,
            upper_bound=event_time_upper_bound,
        )
        params.update(timestamp_params)
        rows = sql_client.execute(
            f"""
            SELECT status_code, response, {timestamp_projection}
            FROM {catalog}.{schema}.{table_name}
            WHERE {predicate}
            """,
            params,
        )
        substantiated_rows.extend((row, timestamp_column) for row in rows)

    match_count = len(substantiated_rows) + unsubstantiated_matches
    if match_count == 0:
        return _ExactRowCheck("pending", "not_visible")
    if match_count > 1:
        return _ExactRowCheck("failed", "duplicate_rows")
    if unsubstantiated_matches:
        return _ExactRowCheck("pending", "schema_unsubstantiated")

    row, timestamp_column = substantiated_rows[0]
    event_time = _inference_event_time(row.get(timestamp_column), timestamp_column)
    if (
        row.get("proof_timestamp_in_bounds") is not True
        or event_time is None
        or not event_time_lower_bound <= event_time <= event_time_upper_bound
    ):
        return _ExactRowCheck("failed", "timestamp_out_of_bounds")
    if not _successful_status_code(row.get("status_code")):
        return _ExactRowCheck("failed", "non_success_status")
    response = _logged_response(row.get("response"))
    if not (
        serving_response_is_terminal_completed(response) and serving_response_has_payload(response)
    ):
        return _ExactRowCheck("failed", "nonterminal_response")
    return _ExactRowCheck("verified", "verified")


def _exact_row_predicate(
    columns: set[str],
    client_request_id: str,
) -> tuple[str, dict[str, Any]] | None:
    if "client_request_id" in columns:
        return "client_request_id = :client_request_id", {"client_request_id": client_request_id}
    if "request" in columns:
        return "request LIKE :client_request_marker", {
            "client_request_marker": f"%{client_request_id}%"
        }
    return None


def _inference_timestamp_column(columns: set[str]) -> str | None:
    """Resolve current AI Gateway and legacy serving inference timestamps."""
    for column in ("event_time", "timestamp_ms", "request_time"):
        if column in columns:
            return column
    return None


def _bounded_timestamp_projection(
    timestamp_column: str,
    *,
    lower_bound: datetime,
    upper_bound: datetime,
) -> tuple[str, dict[str, Any]]:
    if timestamp_column == "timestamp_ms":
        lower: Any = int(lower_bound.timestamp() * 1000)
        upper: Any = int(upper_bound.timestamp() * 1000)
    else:
        lower = lower_bound
        upper = upper_bound
    return (
        f"{timestamp_column}, "
        f"({timestamp_column} >= :proof_time_lower_bound "
        f"AND {timestamp_column} <= :proof_time_upper_bound) "
        "AS proof_timestamp_in_bounds",
        {
            "proof_time_lower_bound": lower,
            "proof_time_upper_bound": upper,
        },
    )


def _inference_event_time(value: Any, timestamp_column: str) -> datetime | None:
    try:
        if timestamp_column == "timestamp_ms":
            if isinstance(value, bool):
                return None
            timestamp_ms = float(value)
            if not math.isfinite(timestamp_ms):
                return None
            return datetime.fromtimestamp(timestamp_ms / 1000.0, tz=UTC)
        if isinstance(value, datetime):
            return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    except (OSError, OverflowError, TypeError, ValueError):
        return None


def _count_matching_rows(
    sql_client: Any,
    *,
    relation: str,
    predicate: str,
    params: dict[str, Any],
) -> int:
    rows = sql_client.execute(
        f"SELECT COUNT(*) AS row_count FROM {relation} WHERE {predicate}",
        params,
    )
    if not rows:
        return 0
    return int(rows[0].get("row_count") or rows[0].get("n") or 0)


def _successful_status_code(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        status_code = int(value)
    except (TypeError, ValueError):
        return False
    return 200 <= status_code < 300


def _logged_response(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _mark_proof_verified_if_pending(
    lakebase: Any,
    proof: AiGatewayVerifiedProof,
) -> AiGatewayVerifiedProof | None:
    verified_at = datetime.now(UTC)
    if proof.sent_at > verified_at + timedelta(seconds=AI_GATEWAY_PROOF_CLOCK_SKEW_S):
        return None
    verify_latency_s = max(0.0, (verified_at - proof.sent_at).total_seconds())
    attestation_alg, attestation_key_id, attestation_signature = sign_gateway_proof(
        signing_key=_attestation_signing_key(),
        proof_id=proof.proof_id,
        git_sha=proof.git_sha,
        client_request_id=proof.client_request_id,
        endpoint_name=proof.endpoint_name,
        inference_table=proof.inference_table,
        sent_at=proof.sent_at,
        verified_at=verified_at,
    )
    row = lakebase.fetchone(
        """
        UPDATE mip_app.ai_gateway_proof_ledger
        SET status = 'verified',
            verified_at = %(verified_at)s,
            verify_latency_s = %(verify_latency_s)s,
            attestation_alg = %(attestation_alg)s,
            attestation_key_id = %(attestation_key_id)s,
            attestation_signature = %(attestation_signature)s
        WHERE proof_id = %(proof_id)s
          AND status = 'pending'
          AND sent_at <= %(sent_at_upper_bound)s
        RETURNING proof_id
        """,
        {
            "proof_id": proof.proof_id,
            "verified_at": verified_at,
            "verify_latency_s": verify_latency_s,
            "attestation_alg": attestation_alg,
            "attestation_key_id": attestation_key_id,
            "attestation_signature": attestation_signature,
            "sent_at_upper_bound": verified_at + timedelta(seconds=AI_GATEWAY_PROOF_CLOCK_SKEW_S),
        },
    )
    if not row:
        return None
    return AiGatewayVerifiedProof(
        proof_id=proof.proof_id,
        git_sha=proof.git_sha,
        client_request_id=proof.client_request_id,
        endpoint_name=proof.endpoint_name,
        inference_table=proof.inference_table,
        sent_at=proof.sent_at,
        verified_at=verified_at,
        verify_latency_s=verify_latency_s,
        status="verified",
        attestation_alg=attestation_alg,
        attestation_key_id=attestation_key_id,
        attestation_signature=attestation_signature,
    )


def _mark_proof_failed_if_pending(
    lakebase: Any,
    proof: AiGatewayVerifiedProof,
) -> bool:
    row = lakebase.fetchone(
        """
        UPDATE mip_app.ai_gateway_proof_ledger
        SET status = 'failed',
            verified_at = NULL,
            verify_latency_s = NULL,
            attestation_alg = NULL,
            attestation_key_id = NULL,
            attestation_signature = NULL
        WHERE proof_id = %(proof_id)s
          AND status = 'pending'
        RETURNING proof_id
        """,
        {"proof_id": proof.proof_id},
    )
    return row is not None


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
        "client_request_id": _redacted_ref(proof.client_request_id),
        "endpoint_name": "<redacted>",
        "inference_table": "<redacted>",
        "sent_at": proof.sent_at.isoformat(),
        "verified_at": proof.verified_at.isoformat() if proof.status == "verified" else None,
        "verify_latency_s": proof.verify_latency_s if proof.status == "verified" else None,
        "status": proof.status,
    }


def _redacted_ref(value: str) -> str:
    _ = value
    return "<redacted>"


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
