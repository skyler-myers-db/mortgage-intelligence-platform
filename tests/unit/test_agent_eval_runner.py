from __future__ import annotations

import json
import re
import sys
from types import SimpleNamespace
from typing import Any

from tools.databricks import run_agent_eval


def test_live_invocation_case_flag_defaults_off(monkeypatch) -> None:
    # Default off so the deploy gate case set is unchanged.
    monkeypatch.delenv("MIP_LIVE_INVOCATION_CASE", raising=False)
    default_off = run_agent_eval._parser().parse_args(
        ["--app-url", "https://x.test", "--token", "t"]
    )
    assert default_off.live_invocation_case is False
    enabled = run_agent_eval._parser().parse_args(
        ["--app-url", "https://x.test", "--token", "t", "--live-invocation-case"]
    )
    assert enabled.live_invocation_case is True


def test_live_invocation_case_clones_first_case_with_distinct_id() -> None:
    cases = [
        {"id": "case-a", "prompt": "Find prime refinance opportunities.", "expected_workflow_id": "daily_refi_brief"},
        {"id": "case-b", "prompt": "Something else."},
    ]
    extra = run_agent_eval.live_invocation_case(cases)
    assert extra is not None
    # Distinct id so score_batch scores it as its own row; prompt/expectation reused.
    assert extra["id"] == "case-a__live_invocation"
    assert extra["prompt"] == "Find prime refinance opportunities."
    assert extra["expected_workflow_id"] == "daily_refi_brief"
    # The template case is not mutated.
    assert cases[0]["id"] == "case-a"
    assert run_agent_eval.live_invocation_case([]) is None


def test_call_growth_agent_uses_json_content_type_and_uuid_request_id(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class _Response:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"workflow": {"id": "daily_refi_brief"}}

    class _Client:
        def __init__(self, *, timeout: float, follow_redirects: bool) -> None:
            captured["timeout"] = timeout
            captured["follow_redirects"] = follow_redirects

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> _Response:
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return _Response()

    monkeypatch.setattr(run_agent_eval.httpx, "Client", _Client)

    response = run_agent_eval._call_growth_agent(
        app_url="https://example.test/",
        token="redacted",
        case={"prompt": "Find prime refinance opportunities."},
        timeout_s=12,
    )

    assert response == {"workflow": {"id": "daily_refi_brief"}}
    assert captured["url"] == "https://example.test/api/growth-agent/agent/run"
    assert captured["headers"]["Content-Type"] == "application/json"
    assert captured["headers"]["Accept"] == "application/json"
    assert captured["json"]["save_monitor"] is False
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        captured["json"]["request_id"],
    )


def test_call_growth_agent_retries_transient_app_warmup(monkeypatch) -> None:
    attempts = 0
    sleeps: list[float] = []

    class _Response:
        def __init__(self, status_code: int, payload: dict[str, Any] | None = None, text: str = "") -> None:
            self.status_code = status_code
            self._payload = payload
            self.text = text

        def json(self) -> dict[str, Any]:
            if self._payload is None:
                raise json.JSONDecodeError("no json", self.text, 0)
            return self._payload

    class _Client:
        def __init__(self, *, timeout: float, follow_redirects: bool) -> None:
            assert timeout == 12
            assert follow_redirects is False

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> _Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return _Response(502, None, "<html>Bad Gateway</html>")
            return _Response(200, {"workflow": {"id": "daily_refi_brief"}})

    monkeypatch.setattr(run_agent_eval.httpx, "Client", _Client)
    monkeypatch.setattr(run_agent_eval.time, "sleep", lambda seconds: sleeps.append(seconds))

    response = run_agent_eval._call_growth_agent(
        app_url="https://example.test/",
        token="redacted",
        case={"prompt": "Find prime refinance opportunities."},
        timeout_s=12,
        max_attempts=2,
        retry_delay_s=0.25,
    )

    assert response == {"workflow": {"id": "daily_refi_brief"}}
    assert attempts == 2
    assert sleeps == [0.25]


def test_call_growth_agent_does_not_retry_validation_error(monkeypatch) -> None:
    attempts = 0

    class _Response:
        status_code = 422
        text = '{"detail":"PII"}'

        def json(self) -> dict[str, Any]:
            return {"detail": "PII"}

    class _Client:
        def __init__(self, *, timeout: float, follow_redirects: bool) -> None:
            assert timeout == 12
            assert follow_redirects is False

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> _Response:
            nonlocal attempts
            attempts += 1
            return _Response()

    monkeypatch.setattr(run_agent_eval.httpx, "Client", _Client)

    response = run_agent_eval._call_growth_agent(
        app_url="https://example.test/",
        token="redacted",
        case={"prompt": "Call John Smith at 555-111-2222."},
        timeout_s=12,
        max_attempts=3,
        retry_delay_s=0,
    )

    assert response == {"error": "PII"}
    assert attempts == 1


def test_log_eval_run_uses_positional_set_tag(monkeypatch) -> None:
    no_output_calls: list[list[str]] = []

    monkeypatch.setattr(run_agent_eval, "_experiment_id", lambda _name: "exp-1")
    monkeypatch.setattr(run_agent_eval, "_git_sha", lambda: "abcdef123456")

    def fake_run(args: list[str], *, input_json: dict[str, Any] | None = None) -> dict[str, Any]:
        assert input_json is not None
        if args == ["experiments", "create-run"]:
            return {"run": {"info": {"run_id": "run-1"}}}
        raise AssertionError(f"unexpected _run call: {args}")

    def fake_run_no_output(args: list[str], *, input_json: dict[str, Any] | None = None) -> None:
        no_output_calls.append(args)
        if args[:2] == ["experiments", "set-tag"]:
            assert args[2] == "mip_eval_failures"
            assert json.loads(args[3])[0]["case_id"] == "case-a"
            assert args[4:] == ["--run-id", "run-1"]

    monkeypatch.setattr(run_agent_eval, "_run", fake_run)
    monkeypatch.setattr(run_agent_eval, "_run_no_output", fake_run_no_output)

    run_id = run_agent_eval._log_eval_run(
        experiment_name="/Shared/mip-agent-eval",
        app_url="https://mip.example.test",
        summary={
            "score": 0.0,
            "passed": 0,
            "total": 1,
            "results": [{"case_id": "case-a", "passed": False}],
        },
        responses_by_case_id={"case-a": {"error": "bad"}},
        genai_evaluate={
            "used": True,
            "reason": "mlflow.genai.evaluate completed",
            "run_id": "genai-run-1",
        },
    )

    assert run_id == "run-1"
    assert any(call[:2] == ["experiments", "log-batch"] for call in no_output_calls)
    assert any(call[:3] == ["experiments", "set-tag", "mip_eval_failures"] for call in no_output_calls)
    assert any(call[:2] == ["experiments", "update-run"] for call in no_output_calls)


def test_log_eval_run_tags_mlflow_genai_evaluate(monkeypatch) -> None:
    log_batch_payload: dict[str, Any] = {}

    monkeypatch.setattr(run_agent_eval, "_experiment_id", lambda _name: "exp-1")
    monkeypatch.setattr(run_agent_eval, "_git_sha", lambda: "abcdef123456")

    def fake_run(args: list[str], *, input_json: dict[str, Any] | None = None) -> dict[str, Any]:
        assert input_json is not None
        if args == ["experiments", "create-run"]:
            return {"run": {"info": {"run_id": "run-1"}}}
        raise AssertionError(f"unexpected _run call: {args}")

    def fake_run_no_output(args: list[str], *, input_json: dict[str, Any] | None = None) -> None:
        if args[:2] == ["experiments", "log-batch"]:
            assert input_json is not None
            log_batch_payload.update(input_json)

    monkeypatch.setattr(run_agent_eval, "_run", fake_run)
    monkeypatch.setattr(run_agent_eval, "_run_no_output", fake_run_no_output)

    run_agent_eval._log_eval_run(
        experiment_name="/Shared/mip-agent-eval",
        app_url="https://mip.example.test",
        summary={
            "score": 1.0,
            "passed": 1,
            "total": 1,
            "results": [
                {
                    "case_id": "case-a",
                    "passed": True,
                    "count_reconciliation": {"passed": True},
                }
            ],
        },
        responses_by_case_id={"case-a": {"workflow": {"id": "daily_refi_brief"}}},
        genai_evaluate={
            "used": True,
            "reason": "ok",
            "run_id": "genai-run-1",
            "count_reconciles_score": 1.0,
        },
    )

    tags = {item["key"]: item["value"] for item in log_batch_payload["tags"]}
    metrics = {item["key"]: item["value"] for item in log_batch_payload["metrics"]}
    params = {item["key"]: item["value"] for item in log_batch_payload["params"]}
    assert tags["mip_mlflow_genai_evaluate"] == "true"
    assert metrics["count_reconciles_passed"] == 1.0
    assert metrics["mlflow_genai_count_reconciles_score"] == 1.0
    assert params["mlflow_genai_evaluate_run_id"] == "genai-run-1"


def test_mlflow_genai_evaluate_runs_custom_scorer(monkeypatch) -> None:
    calls: dict[str, Any] = {}

    def fake_scorer(func=None, **kwargs):
        calls["scorer_kwargs"] = kwargs
        return func

    def fake_evaluate(*, data, predict_fn, scorers):
        calls["data"] = data
        calls["scorers"] = scorers
        calls["predict_fn"] = predict_fn
        assert "outputs" not in data[0]
        assert "expectations" not in data[0]
        assert data[0]["inputs"]["case"]["id"] == "case-a"
        outputs = predict_fn(**data[0]["inputs"])
        assert scorers[0](
            inputs=data[0]["inputs"],
            outputs=outputs,
            expectations=None,
        )
        return SimpleNamespace(run_id="genai-run-1")

    def fake_trace(func=None, **kwargs):
        calls["trace_kwargs"] = kwargs
        if func is None:
            return lambda wrapped: wrapped
        return func

    fake_mlflow = SimpleNamespace(
        genai=SimpleNamespace(evaluate=fake_evaluate),
        trace=fake_trace,
        set_tracking_uri=lambda uri: calls.setdefault("tracking_uri", uri),
        get_tracking_uri=lambda: calls["tracking_uri"],
        set_experiment=lambda name: calls.setdefault("experiment", name),
    )
    monkeypatch.setattr(
        run_agent_eval,
        "_run",
        lambda args: {
            "run": {
                "info": {"run_id": args[-1]},
                "data": {
                    "metrics": [
                        {"key": "count_reconciles/mean", "value": 1.0},
                    ]
                },
            }
        },
    )
    monkeypatch.setitem(sys.modules, "mlflow", fake_mlflow)
    monkeypatch.setitem(
        sys.modules,
        "mlflow.genai.scorers",
        SimpleNamespace(scorer=fake_scorer),
    )

    result = run_agent_eval._run_mlflow_genai_evaluate(
        experiment_name="/Shared/mip-agent-eval",
        cases=[
            {
                "id": "case-a",
                "prompt": "Find prime refinance opportunities.",
                "expected_workflow_id": "daily_refi_brief",
            }
        ],
        responses_by_case_id={
            "case-a": {
                "broad_total": 10,
                "actionable_total": 5,
                "route": "/lead-queue?segment=itm&marketing_eligibility=Eligible+only",
                "source_assets": ["mip.gold.borrower_360"],
                "trace_id": "agent-trace-test",
                "tool_result_hash": "c" * 64,
                "policy_checks": [
                    {"label": "Broad vs actionable reconciliation", "status": "passed"}
                ],
            }
        },
        require=True,
        tracking_uri="databricks",
    )

    assert result["used"] is True
    assert result["run_id"] == "genai-run-1"
    assert result["tracking_uri"] == "databricks"
    assert result["verified_databricks_run"] is True
    assert result["count_reconciles_score"] == 1.0
    assert calls["experiment"] == "/Shared/mip-agent-eval"
    assert calls["scorer_kwargs"]["name"] == "count_reconciles"
    assert calls["trace_kwargs"]["name"] == "mip_growth_agent_eval_replay"
    assert callable(calls["predict_fn"])


def test_mlflow_genai_evaluate_requires_count_reconciles_metric(monkeypatch) -> None:
    fake_mlflow = SimpleNamespace(
        genai=SimpleNamespace(evaluate=lambda **_kwargs: SimpleNamespace(run_id="genai-run-1")),
        trace=lambda func=None, **_kwargs: func if func is not None else (lambda wrapped: wrapped),
        set_tracking_uri=lambda _uri: None,
        get_tracking_uri=lambda: "databricks",
        set_experiment=lambda _name: None,
    )
    monkeypatch.setitem(sys.modules, "mlflow", fake_mlflow)
    monkeypatch.setitem(
        sys.modules,
        "mlflow.genai.scorers",
        SimpleNamespace(scorer=lambda func=None, **_kwargs: func),
    )
    monkeypatch.setattr(
        run_agent_eval,
        "_run",
        lambda _args: {"run": {"info": {"run_id": "genai-run-1"}, "data": {"metrics": []}}},
    )

    try:
        run_agent_eval._run_mlflow_genai_evaluate(
            experiment_name="/Shared/mip-agent-eval",
            cases=[],
            responses_by_case_id={},
            require=True,
            tracking_uri="databricks",
        )
    except RuntimeError as exc:
        assert "count_reconciles scorer metric" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected missing count_reconciles metric to fail closed")


def test_mlflow_genai_evaluate_missing_can_fail_closed(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "mlflow", SimpleNamespace(genai=SimpleNamespace()))
    monkeypatch.delitem(sys.modules, "mlflow.genai.scorers", raising=False)

    try:
        run_agent_eval._run_mlflow_genai_evaluate(
            experiment_name="/Shared/mip-agent-eval",
            cases=[],
            responses_by_case_id={},
            require=True,
            tracking_uri="databricks",
        )
    except RuntimeError as exc:
        assert "mlflow.genai.evaluate is required" in str(exc)
    else:  # pragma: no cover - protects the fail-closed contract.
        raise AssertionError("expected missing mlflow.genai.evaluate to raise")


def test_mlflow_genai_evaluate_rejects_local_tracking_when_required(monkeypatch) -> None:
    fake_mlflow = SimpleNamespace(
        genai=SimpleNamespace(evaluate=lambda **_kwargs: SimpleNamespace(run_id="local-run")),
        set_tracking_uri=lambda _uri: None,
        get_tracking_uri=lambda: "sqlite:////tmp/mlflow.db",
        set_experiment=lambda _name: None,
    )
    monkeypatch.setitem(sys.modules, "mlflow", fake_mlflow)
    monkeypatch.setitem(
        sys.modules,
        "mlflow.genai.scorers",
        SimpleNamespace(scorer=lambda func=None, **_kwargs: func),
    )

    try:
        run_agent_eval._run_mlflow_genai_evaluate(
            experiment_name="/Shared/mip-agent-eval",
            cases=[],
            responses_by_case_id={},
            require=True,
            tracking_uri="sqlite:////tmp/mlflow.db",
        )
    except RuntimeError as exc:
        assert "tracking URI must be Databricks" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected local MLflow tracking URI to fail closed")


def test_mlflow_genai_evaluate_requires_databricks_run_lookup(monkeypatch) -> None:
    fake_mlflow = SimpleNamespace(
        genai=SimpleNamespace(evaluate=lambda **_kwargs: SimpleNamespace(run_id="missing-run")),
        trace=lambda func=None, **_kwargs: func if func is not None else (lambda wrapped: wrapped),
        set_tracking_uri=lambda _uri: None,
        get_tracking_uri=lambda: "databricks",
        set_experiment=lambda _name: None,
    )
    monkeypatch.setitem(sys.modules, "mlflow", fake_mlflow)
    monkeypatch.setitem(
        sys.modules,
        "mlflow.genai.scorers",
        SimpleNamespace(scorer=lambda func=None, **_kwargs: func),
    )
    monkeypatch.setattr(
        run_agent_eval,
        "_run",
        lambda _args: (_ for _ in ()).throw(RuntimeError("not found")),
    )

    try:
        run_agent_eval._run_mlflow_genai_evaluate(
            experiment_name="/Shared/mip-agent-eval",
            cases=[],
            responses_by_case_id={},
            require=True,
            tracking_uri="databricks",
        )
    except RuntimeError as exc:
        assert "not resolvable in Databricks" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected unresolved Databricks GenAI eval run to fail closed")


def test_verifier_mints_lakebase_env_when_absent(monkeypatch) -> None:
    """Deploy laptops / CI runners have no Lakebase env; the verifier mints it
    from the workspace identity (observed failure: deploy step 18, 2026-07-07)."""
    from types import SimpleNamespace

    from tools.databricks import verify_ai_gateway_exact_proof as verifier

    for key in ("LAKEBASE_HOST", "LAKEBASE_USER", "LAKEBASE_PASSWORD", "PGHOST", "PGUSER", "PGPASSWORD"):
        monkeypatch.delenv(key, raising=False)

    fake = SimpleNamespace(
        database=SimpleNamespace(
            get_database_instance=lambda name: SimpleNamespace(
                read_write_dns=f"{name}.db.example"
            ),
            generate_database_credential=lambda instance_names, request_id: SimpleNamespace(
                token="short-lived-token"
            ),
        ),
        current_user=SimpleNamespace(me=lambda: SimpleNamespace(user_name="op@entrada.ai")),
    )
    assert verifier.ensure_lakebase_env(workspace_factory=lambda: fake) is True
    import os as _os

    assert _os.environ["PGHOST"] == "mip-app-state.db.example"
    assert _os.environ["PGPASSWORD"] == "short-lived-token"
    assert _os.environ["LAKEBASE_HOST"] == "mip-app-state.db.example"
    assert _os.environ["LAKEBASE_USER"] == "op@entrada.ai"
    assert _os.environ["LAKEBASE_PASSWORD"] == "short-lived-token"
    # Explicit env wins: second call is a no-op.
    assert verifier.ensure_lakebase_env(workspace_factory=lambda: fake) is False
