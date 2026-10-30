"""``bundle_env plan --allow-unprovisioned-genie``: the pre-lease plan gate's only relaxation.

The deploy plans before the signed lease (M0-PR4), which on a first install is
before the governed Genie space exists. Only ``plan``, and only with this
wrapper flag, may then proceed with the placeholder binding, and only when no
space holds the governed title. A duplicate title, any other resolution error,
the flag on another subcommand, or the flag absent still refuse, and the flag
never reaches the Databricks CLI.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools.databricks import bundle_env

FLAG = bundle_env.UNPROVISIONED_GENIE_FLAG


def _space_list(*titles: str) -> SimpleNamespace:
    spaces = [
        SimpleNamespace(title=title, space_id=f"space-{index}")
        for index, title in enumerate(titles)
    ]

    class Genie:
        def list_spaces(self, *, page_token: str | None) -> SimpleNamespace:
            return SimpleNamespace(spaces=spaces, next_page_token=None)

        def get_space(self, space_id: str) -> SimpleNamespace:
            return next(SimpleNamespace(title=s.title) for s in spaces if s.space_id == space_id)

    return SimpleNamespace(genie=Genie())


def _run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    argv: list[str],
    *,
    titles: tuple[str, ...],
    ambient_genie: str = "stale-cross-workspace-id",
) -> tuple[int, dict[str, Any]]:
    env_local = tmp_path / ".env.local"
    env_local.write_text(
        "DATABRICKS_WAREHOUSE_ID=warehouse-id\n"
        f"GENIE_SPACE_ID={ambient_genie}\n"
        "MIP_AI_GATEWAY_VERIFIER_CLIENT_ID=verifier-client-id\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    monkeypatch.setattr(bundle_env, "REPO", tmp_path)
    for name in ("DATABRICKS_WAREHOUSE_ID", "GENIE_SPACE_ID", "MIP_GENIE_SPACE_NAME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(bundle_env, "_genie_workspace_client", lambda _env: _space_list(*titles))
    monkeypatch.setattr(bundle_env.render_sql, "render", lambda **_kwargs: (1, 0, 0))
    monkeypatch.setattr(
        bundle_env.scheduled_refresh_posture, "apply", lambda env, **_kw: "refresh: test"
    )
    captured: dict[str, Any] = {}

    def fake_run(command: list[str], *, env: dict[str, str], check: bool) -> Any:
        captured["command"] = command
        captured["env"] = env
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(bundle_env.subprocess, "run", fake_run)
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", *argv])
    return bundle_env.main(), captured


def test_the_flag_plans_with_the_placeholder_when_no_space_has_the_title(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, captured = _run(
        monkeypatch, tmp_path, ["plan", "-t", "dev", "-o", "json", FLAG], titles=("Other",)
    )

    assert rc == 0
    assert captured["command"] == ["databricks", "bundle", "plan", "-t", "dev", "-o", "json"]
    assert captured["env"]["BUNDLE_VAR_genie_space_id"] == bundle_env.PLACEHOLDER
    assert "stale-cross-workspace-id" not in captured["env"].values(), "a stale id never binds"
    assert "binds the placeholder" in capsys.readouterr().err


def test_the_flag_still_resolves_the_governed_space_when_it_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rc, captured = _run(
        monkeypatch,
        tmp_path,
        ["plan", "-t", "dev", "-o", "json", FLAG],
        titles=("Mortgage Lead Intelligence",),
    )

    assert rc == 0
    assert captured["env"]["BUNDLE_VAR_genie_space_id"] == "space-0"


def test_a_duplicate_title_refuses_even_with_the_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rc, captured = _run(
        monkeypatch,
        tmp_path,
        ["plan", "-t", "dev", "-o", "json", FLAG],
        titles=("Mortgage Lead Intelligence", "Mortgage Lead Intelligence"),
    )

    assert rc == 2 and captured == {}


def test_without_the_flag_an_unprovisioned_space_still_refuses_the_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rc, captured = _run(monkeypatch, tmp_path, ["plan", "-t", "dev", "-o", "json"], titles=())

    assert rc == 2 and captured == {}


@pytest.mark.parametrize(
    "argv",
    (
        ["validate", "-t", "dev", FLAG],
        ["summary", "-t", "dev", "-o", "json", FLAG],
        ["deploy", "-t", "dev", "--select", "jobs.mip_ref_seed", FLAG],
        ["deployment", "bind", "mip_app", "mip-app", "-t", "dev", FLAG],
        ["plan", "-t", "dev", FLAG, FLAG],
    ),
)
def test_the_flag_is_refused_outside_one_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, argv: list[str]
) -> None:
    rc, captured = _run(monkeypatch, tmp_path, argv, titles=())

    assert rc == 2 and captured == {}


def test_a_genie_read_error_refuses_even_with_the_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def broken(_env: dict[str, str]) -> Any:
        raise RuntimeError("Genie listing failed")

    monkeypatch.setattr(bundle_env, "_genie_workspace_client", broken)
    env_local = tmp_path / ".env.local"
    env_local.write_text(
        "DATABRICKS_WAREHOUSE_ID=w\nMIP_AI_GATEWAY_VERIFIER_CLIENT_ID=v\n", encoding="utf-8"
    )
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    monkeypatch.setattr(bundle_env, "REPO", tmp_path)
    called: list[Any] = []
    monkeypatch.setattr(bundle_env.subprocess, "run", lambda *a, **k: called.append(a))
    monkeypatch.setattr(
        bundle_env.sys, "argv", ["bundle_env.py", "plan", "-t", "dev", "-o", "json", FLAG]
    )

    assert bundle_env.main() == 2
    assert called == []


def test_an_absent_title_is_its_own_error_class_with_the_same_message() -> None:
    with pytest.raises(bundle_env.GovernedGenieSpaceAbsent, match="found 0"):
        bundle_env._resolve_governed_genie_space_id(
            {}, space_name="Governed", client=_space_list("Other")
        )
    with pytest.raises(ValueError, match="found 2") as raised:
        bundle_env._resolve_governed_genie_space_id(
            {}, space_name="G", client=_space_list("G", "G")
        )
    assert not isinstance(raised.value, bundle_env.GovernedGenieSpaceAbsent)
