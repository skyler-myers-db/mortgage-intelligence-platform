#!/usr/bin/env python3
"""Run live Mortgage Growth Agent golden eval and log it to Databricks MLflow.

This is an operator validation tool, not an app runtime fallback. It exercises
the deployed `/api/growth-agent/agent/run` endpoint with the same JSON content
type a browser client sends, scores the returned governed workflow payloads
with `tests/eval/scorers.py`, and records the result in a Databricks experiment
that the app capability probe can later verify.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tests.eval.scorers import count_reconciles, load_cases, score_batch  # noqa: E402


def _run(args: list[str], *, input_json: dict[str, Any] | None = None) -> dict[str, Any]:
    cmd = ["databricks", *args, "-o", "json"]
    if input_json is not None:
        cmd = ["databricks", *args, "--json", json.dumps(input_json), "-o", "json"]
    proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return json.loads(proc.stdout or "{}")


def _run_no_output(args: list[str], *, input_json: dict[str, Any] | None = None) -> None:
    cmd = ["databricks", *args]
    if input_json is not None:
        cmd = ["databricks", *args, "--json", json.dumps(input_json)]
    proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {proc.stderr.strip() or proc.stdout.strip()}")


def _experiment_id(name: str) -> str:
    try:
        existing = _run(["experiments", "get-by-name", name])
    except RuntimeError:
        created = _run(["experiments", "create-experiment", name])
        return str(created.get("experiment_id") or created.get("experiment", {}).get("experiment_id") or "")
    experiment = existing.get("experiment") or existing
    return str(experiment.get("experiment_id") or "")


def _git_sha() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=True,
    )
    return proc.stdout.strip()


def _call_growth_agent(
    *,
    app_url: str,
    token: str,
    case: dict[str, Any],
    timeout_s: float,
    max_attempts: int = 8,
    retry_delay_s: float = 10.0,
) -> dict[str, Any]:
    payload = {
        "prompt": str(case["prompt"]),
        "request_id": str(uuid.uuid4()),
        "save_monitor": False,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    url = f"{app_url.rstrip('/')}/api/growth-agent/agent/run"
    attempts = max(1, max_attempts)
    with httpx.Client(timeout=timeout_s, follow_redirects=False) as client:
        for attempt in range(1, attempts + 1):
            try:
                response = client.post(url, headers=headers, json=payload)
            except httpx.HTTPError as exc:
                if attempt < attempts:
                    print(f"[agent-eval] transient request error ({exc}); retrying {attempt}/{attempts - 1}")
                    time.sleep(retry_delay_s)
                    continue
                return {"error": f"request failed after {attempts} attempts: {exc}"}
            try:
                body = response.json()
            except json.JSONDecodeError:
                body = {"detail": response.text[:500]}
            transient = response.status_code in {429, 502, 503, 504}
            if response.status_code >= 400:
                detail = body.get("detail") if isinstance(body, dict) else body
                if transient and attempt < attempts:
                    print(
                        f"[agent-eval] app returned HTTP {response.status_code} "
                        f"({str(detail)[:120]}); retrying {attempt}/{attempts - 1}"
                    )
                    time.sleep(retry_delay_s)
                    continue
                if isinstance(body, dict):
                    return {"error": str(body.get("detail") or body)}
                return {"error": str(body)}
            if not isinstance(body, dict):
                return {"error": f"expected JSON object, got {type(body).__name__}"}
            return body
    return {"error": "growth-agent request loop exited unexpectedly"}


def _log_eval_run(
    *,
    experiment_name: str,
    app_url: str,
    summary: dict[str, Any],
    responses_by_case_id: dict[str, dict[str, Any]],
    genai_evaluate: dict[str, Any],
) -> str:
    experiment_id = _experiment_id(experiment_name)
    if not experiment_id:
        raise RuntimeError(f"Could not create or resolve experiment {experiment_name}")
    now_ms = int(time.time() * 1000)
    run = _run(
        ["experiments", "create-run"],
        input_json={
            "experiment_id": experiment_id,
            "run_name": f"mip-growth-agent-golden-{now_ms}",
            "start_time": now_ms,
        },
    )
    run_info = run.get("run", {}).get("info") or run.get("info") or run
    run_id = str(run_info.get("run_id") or "")
    if not run_id:
        raise RuntimeError(f"Experiment run response missing run_id: {run}")
    git_sha = _git_sha()
    _run_no_output(
        ["experiments", "log-batch", "--run-id", run_id],
        input_json={
            "metrics": [
                {"key": "score", "value": float(summary["score"]), "timestamp": now_ms, "step": 0},
                {"key": "passed", "value": float(summary["passed"]), "timestamp": now_ms, "step": 0},
                {"key": "total", "value": float(summary["total"]), "timestamp": now_ms, "step": 0},
                {
                    "key": "count_reconciles_passed",
                    "value": float(
                        sum(
                            1
                            for row in summary["results"]
                            if row.get("count_reconciliation", {}).get("passed")
                            or row.get("checks", {}).get("expected_error")
                        )
                    ),
                    "timestamp": now_ms,
                    "step": 0,
                },
                {
                    "key": "mlflow_genai_count_reconciles_score",
                    "value": float(genai_evaluate.get("count_reconciles_score") or 0.0),
                    "timestamp": now_ms,
                    "step": 0,
                },
            ],
            "params": [
                {"key": "git_sha", "value": git_sha[:250]},
                {"key": "app_url", "value": app_url[:250]},
                {"key": "case_count", "value": str(summary["total"])},
                {
                    "key": "mlflow_genai_evaluate_run_id",
                    "value": str(genai_evaluate.get("run_id") or "")[:250],
                },
                {
                    "key": "mlflow_genai_evaluate_tracking_uri",
                    "value": str(genai_evaluate.get("tracking_uri") or "")[:250],
                },
            ],
            "tags": [
                {"key": "mip_eval_type", "value": "growth_agent_golden"},
                {"key": "mip_git_sha", "value": git_sha[:250]},
                {
                    "key": "mip_mlflow_genai_evaluate",
                    "value": "true" if genai_evaluate.get("used") is True else "false",
                },
                {
                    "key": "mip_mlflow_genai_evaluate_reason",
                    "value": str(genai_evaluate.get("reason") or "")[:250],
                },
                {
                    "key": "mip_mlflow_genai_tracking_uri",
                    "value": str(genai_evaluate.get("tracking_uri") or "")[:250],
                },
                {
                    "key": "mip_mlflow_genai_databricks_run_verified",
                    "value": "true" if genai_evaluate.get("verified_databricks_run") is True else "false",
                },
            ],
        },
    )
    # Keep the full payload local and concise in MLflow. The app capability
    # probe only needs the metrics and params above to prove the latest run.
    failures = [row for row in summary["results"] if not row["passed"]]
    _run_no_output(["experiments", "set-tag", "mip_eval_failures", json.dumps(failures)[:250], "--run-id", run_id])
    _run_no_output(
        ["experiments", "update-run", "--run-id", run_id, "--status", "FINISHED", "--end-time", str(int(time.time() * 1000))],
    )
    _ = responses_by_case_id
    return run_id


def _mlflow_genai_eval_data(
    *,
    cases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        case_id = str(case["id"])
        rows.append(
            {
                "inputs": {
                    "prompt": str(case["prompt"]),
                    "case_id": case_id,
                    "case": dict(case),
                },
            }
        )
    return rows


def _traced_replay_predict_fn(
    *,
    mlflow_module: Any,
    responses_by_case_id: dict[str, dict[str, Any]],
):
    """Return a traced predict function for MLflow GenAI eval over live responses.

    The app calls happen before MLflow evaluation so the retry/timeout behavior is
    controlled in one place. MLflow still receives a normal traced predict_fn and
    runs the same scorer over the returned payloads, avoiding the no-trace static
    row path that is broken in some MLflow releases.
    """

    def replay_growth_agent_response(
        prompt: str,
        case_id: str,
        case: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _ = prompt, case
        response = responses_by_case_id.get(str(case_id))
        if response is None:
            return {"error": f"missing precomputed Growth Agent response for case {case_id}"}
        return response

    trace_decorator = getattr(mlflow_module, "trace", None)
    if not callable(trace_decorator):
        raise RuntimeError("mlflow.trace is required to run MLflow GenAI Evaluation replay")
    return trace_decorator(
        replay_growth_agent_response,
        name="mip_growth_agent_eval_replay",
        attributes={"mip_eval": "growth_agent_golden"},
    )


def _is_databricks_tracking_uri(uri: str) -> bool:
    normalized = uri.strip().lower()
    return normalized == "databricks" or normalized.startswith("databricks://")


def _metric_value_from_run_payload(payload: dict[str, Any], metric_key: str) -> float | None:
    metrics = (((payload.get("run") or {}).get("data") or {}).get("metrics") or [])
    for metric in metrics:
        if not isinstance(metric, dict):
            continue
        if str(metric.get("key") or "").strip() != metric_key:
            continue
        try:
            return float(metric.get("value"))
        except (TypeError, ValueError):
            return None
    return None


def _count_reconciles_metric_from_payload(payload: dict[str, Any]) -> float | None:
    metrics = (((payload.get("run") or {}).get("data") or {}).get("metrics") or [])
    for metric in metrics:
        if not isinstance(metric, dict):
            continue
        key = str(metric.get("key") or "").strip().lower()
        if "count_reconciles" not in key or "error" in key:
            continue
        try:
            return float(metric.get("value"))
        except (TypeError, ValueError):
            continue
    return None


def _count_reconciles_metric_from_result(result: Any) -> float | None:
    seen: set[int] = set()

    def visit(value: Any) -> float | None:
        if value is None or isinstance(value, str | bytes | bool):
            return None
        if isinstance(value, int | float):
            return None
        value_id = id(value)
        if value_id in seen:
            return None
        seen.add(value_id)
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = str(key).strip().lower()
                if "count_reconciles" in normalized and "error" not in normalized:
                    try:
                        return float(item)
                    except (TypeError, ValueError):
                        pass
                found = visit(item)
                if found is not None:
                    return found
            return None
        if isinstance(value, list | tuple):
            for item in value:
                found = visit(item)
                if found is not None:
                    return found
            return None
        for attr in (
            "metrics",
            "aggregate_results",
            "scorer_results",
            "scores",
            "results",
            "__dict__",
        ):
            if not hasattr(value, attr):
                continue
            try:
                found = visit(getattr(value, attr))
            except Exception:  # noqa: BLE001 - best-effort shape extraction.
                continue
            if found is not None:
                return found
        return None

    return visit(result)


def _run_mlflow_genai_evaluate(
    *,
    experiment_name: str,
    cases: list[dict[str, Any]],
    responses_by_case_id: dict[str, dict[str, Any]],
    require: bool,
    tracking_uri: str | None = None,
) -> dict[str, Any]:
    """Run MLflow GenAI Evaluation with the reviewed custom scorer if present."""

    try:
        import mlflow  # type: ignore[import-untyped]
        from mlflow.genai.scorers import scorer  # type: ignore[import-untyped]
    except Exception as exc:  # noqa: BLE001 - absence is reflected honestly.
        if require:
            raise RuntimeError("mlflow.genai.evaluate is required but mlflow is unavailable") from exc
        return {"used": False, "reason": f"mlflow unavailable: {type(exc).__name__}"}

    evaluate = getattr(getattr(mlflow, "genai", None), "evaluate", None)
    if not callable(evaluate):
        if require:
            raise RuntimeError("mlflow.genai.evaluate is required but not available")
        return {"used": False, "reason": "mlflow.genai.evaluate unavailable"}

    desired_tracking_uri = (tracking_uri or os.environ.get("MLFLOW_TRACKING_URI") or "databricks").strip()
    if hasattr(mlflow, "set_tracking_uri"):
        mlflow.set_tracking_uri(desired_tracking_uri)
    actual_tracking_uri = str(
        mlflow.get_tracking_uri() if hasattr(mlflow, "get_tracking_uri") else desired_tracking_uri
    )
    if not _is_databricks_tracking_uri(actual_tracking_uri):
        message = f"mlflow.genai.evaluate tracking URI must be Databricks, got {actual_tracking_uri!r}"
        if require:
            raise RuntimeError(message)
        return {
            "used": False,
            "reason": message,
            "tracking_uri": actual_tracking_uri,
        }

    decorated_count_reconciles = scorer(
        count_reconciles,
        name="count_reconciles",
        description="Broad and actionable Growth Agent counts reconcile with trace proof.",
    )
    if hasattr(mlflow, "set_experiment"):
        mlflow.set_experiment(experiment_name)
    result = evaluate(
        data=_mlflow_genai_eval_data(cases=cases),
        predict_fn=_traced_replay_predict_fn(
            mlflow_module=mlflow,
            responses_by_case_id=responses_by_case_id,
        ),
        scorers=[decorated_count_reconciles],
    )
    run_id = str(getattr(result, "run_id", "") or getattr(result, "mlflow_run_id", "") or "")
    if not run_id:
        if require:
            raise RuntimeError("mlflow.genai.evaluate completed without a run id")
        return {
            "used": False,
            "reason": "mlflow.genai.evaluate completed without a run id",
            "tracking_uri": actual_tracking_uri,
        }
    try:
        run_payload = _run(["experiments", "get-run", run_id])
    except RuntimeError as exc:
        if require:
            raise RuntimeError(
                f"mlflow.genai.evaluate run {run_id} is not resolvable in Databricks"
            ) from exc
        return {
            "used": False,
            "reason": f"GenAI eval run not resolvable in Databricks: {type(exc).__name__}",
            "run_id": run_id,
            "tracking_uri": actual_tracking_uri,
        }
    count_metric = _count_reconciles_metric_from_payload(run_payload)
    if count_metric is None:
        count_metric = _count_reconciles_metric_from_result(result)
    if count_metric is None or count_metric < 1.0:
        message = "mlflow.genai.evaluate did not produce a passing count_reconciles scorer metric"
        if require:
            raise RuntimeError(message)
        return {
            "used": False,
            "reason": message,
            "run_id": run_id,
            "tracking_uri": actual_tracking_uri,
            "count_reconciles_score": count_metric,
        }
    return {
        "used": True,
        "reason": "mlflow.genai.evaluate completed",
        "run_id": run_id,
        "tracking_uri": actual_tracking_uri,
        "verified_databricks_run": True,
        "count_reconciles_score": count_metric,
        "result_type": type(result).__name__,
    }


def _write_env(path: Path, *, experiment_name: str, run_id: str) -> None:
    def assignment(key: str, value: str) -> str:
        return f"{key}={shlex.quote(value)}"

    path.write_text(
        "\n".join(
            [
                assignment("MIP_AGENT_EVAL_EXPERIMENT", experiment_name),
                assignment("MIP_AGENT_EVAL_RUN_ID", run_id),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"[agent-eval] wrote env file: {path}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-url", default=os.environ.get("MIP_APP_URL", ""))
    parser.add_argument("--token", default=os.environ.get("MIP_BEARER_TOKEN", ""))
    parser.add_argument("--experiment", default=os.environ.get("MIP_AGENT_EVAL_EXPERIMENT", "/Shared/mip-agent-eval"))
    parser.add_argument("--cases", type=Path, default=REPO / "tests" / "eval" / "golden_agent_cases.jsonl")
    parser.add_argument("--timeout-s", type=float, default=60.0)
    parser.add_argument("--max-attempts", type=int, default=8)
    parser.add_argument("--retry-delay-s", type=float, default=10.0)
    parser.add_argument("--out-env", type=Path)
    parser.add_argument("--out-json", type=Path)
    parser.add_argument("--mlflow-tracking-uri", default=os.environ.get("MLFLOW_TRACKING_URI", "databricks"))
    parser.add_argument(
        "--require-mlflow-genai-evaluate",
        action="store_true",
        default=os.environ.get("MIP_REQUIRE_MLFLOW_GENAI_EVALUATE", "").lower()
        in {"1", "true", "yes"},
        help="Fail if mlflow.genai.evaluate cannot run.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.app_url:
        raise ValueError("--app-url or MIP_APP_URL is required")
    if not args.token:
        raise ValueError("--token or MIP_BEARER_TOKEN is required")
    cases = load_cases(args.cases)
    responses: dict[str, dict[str, Any]] = {}
    for case in cases:
        case_id = str(case["id"])
        print(f"[agent-eval] running {case_id}: {case['prompt']}")
        responses[case_id] = _call_growth_agent(
            app_url=args.app_url,
            token=args.token,
            case=case,
            timeout_s=args.timeout_s,
            max_attempts=args.max_attempts,
            retry_delay_s=args.retry_delay_s,
        )
    summary = score_batch(responses, cases)
    genai_evaluate = _run_mlflow_genai_evaluate(
        experiment_name=args.experiment,
        cases=cases,
        responses_by_case_id=responses,
        require=bool(args.require_mlflow_genai_evaluate),
        tracking_uri=args.mlflow_tracking_uri,
    )
    if args.out_json:
        args.out_json.write_text(
            json.dumps(
                {
                    "run_id": None,
                    "experiment": args.experiment,
                    "genai_evaluate": genai_evaluate,
                    "responses": responses,
                    **summary,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    run_id = _log_eval_run(
        experiment_name=args.experiment,
        app_url=args.app_url,
        summary=summary,
        responses_by_case_id=responses,
        genai_evaluate=genai_evaluate,
    )
    result = {
        "run_id": run_id,
        "experiment": args.experiment,
        "genai_evaluate": genai_evaluate,
        "responses": responses,
        **summary,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.out_json:
        args.out_json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.out_env:
        _write_env(args.out_env, experiment_name=args.experiment, run_id=run_id)
    if summary["passed"] != summary["total"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
