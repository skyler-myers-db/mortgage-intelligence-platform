from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.databricks import stop_app_fail_closed as stop_module
from tools.databricks.stop_app_fail_closed import stop_app_fail_closed


def _app(name: str = "mip-app", state: str = "RUNNING") -> object:
    return SimpleNamespace(
        name=name,
        compute_status=SimpleNamespace(state=state),
    )


def test_stop_compensation_accepts_authoritative_absence() -> None:
    workspace = SimpleNamespace(apps=SimpleNamespace(list=lambda: iter([])))

    assert (
        stop_app_fail_closed(
            app_name="mip-app",
            workspace=workspace,  # type: ignore[arg-type]
            sleep=lambda _: None,
        )
        == "absent"
    )


def test_stop_compensation_retries_transition_conflict_and_proves_stopped() -> None:
    states = iter([_app(state="DEPLOYING"), _app(state="RUNNING"), _app(state="STOPPED")])
    stop_calls: list[str] = []

    def stop(name: str) -> None:
        stop_calls.append(name)
        if len(stop_calls) == 1:
            raise RuntimeError("deployment is in progress")

    workspace = SimpleNamespace(
        apps=SimpleNamespace(
            list=lambda: iter([_app()]),
            get=lambda name: next(states),
            stop=stop,
        )
    )

    assert (
        stop_app_fail_closed(
            app_name="mip-app",
            workspace=workspace,  # type: ignore[arg-type]
            attempts=3,
            interval_s=0,
        )
        == "stopped"
    )
    assert stop_calls == ["mip-app", "mip-app"]


def test_stop_compensation_fails_when_terminal_state_is_unproven() -> None:
    workspace = SimpleNamespace(
        apps=SimpleNamespace(
            list=lambda: iter([_app()]),
            get=lambda name: _app(state="RUNNING"),
            stop=lambda name: (_ for _ in ()).throw(RuntimeError("conflict")),
        )
    )

    with pytest.raises(RuntimeError, match="Could not prove.*stopped"):
        stop_app_fail_closed(
            app_name="mip-app",
            workspace=workspace,  # type: ignore[arg-type]
            attempts=2,
            interval_s=0,
        )


def test_cli_writes_machine_readable_proven_outcome(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    outcome = tmp_path / "stop.env"
    workspace = SimpleNamespace(apps=SimpleNamespace(list=lambda: iter([])))
    monkeypatch.setattr(stop_module, "deployment_workspace_client", lambda: workspace)

    assert stop_module.main(["--app-name", "mip-app", "--out-env", str(outcome)]) == 0
    assert outcome.read_text(encoding="utf-8") == "MIP_APP_STOP_OUTCOME=absent\n"
    assert outcome.stat().st_mode & 0o777 == 0o600
