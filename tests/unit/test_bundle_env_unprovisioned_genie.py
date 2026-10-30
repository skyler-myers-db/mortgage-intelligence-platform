"""``bundle_env plan --allow-unprovisioned-genie``: the pre-lease plan gate's only relaxation.

The deploy plans before the signed lease (M0-PR4), which on a first install is
before the governed Genie space exists. bundle_env binds the governed space by
its pin only (tools/databricks/genie_space_pin.py, M1-PR3): never a title and
never an ambient ``GENIE_SPACE_ID``. Only ``plan``, and only with this wrapper
flag, may proceed with the placeholder binding, and only when nothing pins a
space yet. A pin refusal, any other read error, the flag on another
subcommand, or the flag absent still refuse, and the flag never reaches the
Databricks CLI. Every call names ``-t client`` (M1-PR2) with the
paychex-shaped client profile in ``.env.local``
(tests/fixtures/client_profiles.py).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.fixtures.client_profiles import env_file_text, paychex_shaped_profile
from tools.clients.environment import KNOWN_NAMES
from tools.databricks import bundle_env

FLAG = bundle_env.UNPROVISIONED_GENIE_FLAG


@pytest.fixture(autouse=True)
def _no_ambient_profile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """-t client resolves the profile from the shell and .env.local: only the test's reaches it."""

    monkeypatch.setattr(bundle_env, "BUNDLE_ROOT", tmp_path / "bundle-root")
    monkeypatch.delenv("DATABRICKS_BUNDLE_ROOT", raising=False)
    for name in KNOWN_NAMES:
        monkeypatch.delenv(name, raising=False)


def _client_env_local(tmp_path: Path, extra: str) -> Path:
    """An .env.local holding the paychex-shaped client profile, then ``extra``."""

    env_local = tmp_path / ".env.local"
    env_local.write_text(env_file_text(paychex_shaped_profile()) + extra, encoding="utf-8")
    return env_local


def _pins(pinned: str | None) -> Any:
    """``_pinned_genie_space_id`` answering ``pinned`` (``None``: nothing pins a space yet)."""

    return lambda _env, _profile: pinned


def _run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    argv: list[str],
    *,
    pinned: str | None,
    ambient_genie: str = "stale-cross-workspace-id",
) -> tuple[int, dict[str, Any]]:
    # -t client: the verifier is the profile's MIP_IDENTITY_IDS_JSON.verifier.
    env_local = _client_env_local(
        tmp_path, f"DATABRICKS_WAREHOUSE_ID=warehouse-id\nGENIE_SPACE_ID={ambient_genie}\n"
    )
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    monkeypatch.setattr(bundle_env, "REPO", tmp_path)
    for name in ("DATABRICKS_WAREHOUSE_ID", "GENIE_SPACE_ID", "MIP_GENIE_SPACE_NAME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(bundle_env, "_pinned_genie_space_id", _pins(pinned))
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


def test_the_flag_plans_with_the_placeholder_when_nothing_pins_a_space(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, captured = _run(monkeypatch, tmp_path, ["plan", "-t", "client", "-o", "json", FLAG], pinned=None)

    assert rc == 0
    assert captured["command"] == ["databricks", "bundle", "plan", "-t", "client", "-o", "json"]
    assert captured["env"]["BUNDLE_VAR_genie_space_id"] == bundle_env.PLACEHOLDER
    assert "stale-cross-workspace-id" not in captured["env"].values(), "a stale id never binds"
    assert "GENIE_SPACE_ID" not in captured["env"], "GENIE_SPACE_ID is no input (M1-PR3)"
    assert "binds the placeholder" in capsys.readouterr().err


def test_the_flag_still_binds_the_pinned_space_when_one_is_pinned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rc, captured = _run(
        monkeypatch, tmp_path, ["plan", "-t", "client", "-o", "json", FLAG], pinned="pinned-space"
    )

    assert rc == 0
    assert captured["env"]["BUNDLE_VAR_genie_space_id"] == "pinned-space"


def test_a_pin_refusal_refuses_even_with_the_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from tools.databricks.genie_space_pin import GeniePinRefusal

    def refused(_env: dict[str, str], _profile: Any) -> str:
        raise GeniePinRefusal("the pinned governed Genie space no longer exists")

    rc, captured = _run(monkeypatch, tmp_path, ["plan", "-t", "client", "-o", "json", FLAG], pinned=None)
    assert rc == 0
    monkeypatch.setattr(bundle_env, "_pinned_genie_space_id", refused)
    captured.clear()

    assert bundle_env.main() == 2 and captured == {}
    assert "the pinned governed Genie space no longer exists" in capsys.readouterr().err


def test_without_the_flag_an_unprovisioned_space_still_refuses_the_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rc, captured = _run(monkeypatch, tmp_path, ["plan", "-t", "client", "-o", "json"], pinned=None)

    assert rc == 2 and captured == {}


@pytest.mark.parametrize(
    "argv",
    (
        ["validate", "-t", "client", FLAG],
        ["validate", "-t", "ci", FLAG],
        ["summary", "-t", "client", "-o", "json", FLAG],
        ["deploy", "-t", "client", "--select", "jobs.mip_ref_seed", FLAG],
        ["deployment", "bind", "mip_app", "mip-app", "-t", "client", FLAG],
        ["plan", "-t", "client", FLAG, FLAG],
    ),
)
def test_the_flag_is_refused_outside_one_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
) -> None:
    rc, captured = _run(monkeypatch, tmp_path, argv, pinned=None)

    assert rc == 2 and captured == {}
    # The flag's own refusal, not the target check before it.
    assert f"{FLAG} is accepted once, by plan only" in capsys.readouterr().err


def test_a_genie_read_error_refuses_even_with_the_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(_env: dict[str, str]) -> Any:
        raise RuntimeError(f"Genie listing failed for {tmp_path}")

    monkeypatch.setattr(bundle_env, "_genie_workspace_client", broken)
    env_local = _client_env_local(tmp_path, "DATABRICKS_WAREHOUSE_ID=w\n")
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    monkeypatch.setattr(bundle_env, "REPO", tmp_path)
    called: list[Any] = []
    monkeypatch.setattr(bundle_env.subprocess, "run", lambda *a, **k: called.append(a))
    monkeypatch.setattr(
        bundle_env.sys, "argv", ["bundle_env.py", "plan", "-t", "client", "-o", "json", FLAG]
    )

    assert bundle_env.main() == 2
    assert called == []
    err = capsys.readouterr().err
    assert "governed Genie binding verification failed: RuntimeError" in err
    assert str(tmp_path) not in err, "a non-pin error prints its class only"


def test_bundle_env_no_longer_resolves_a_space_by_its_title() -> None:
    for retired in ("_resolve_governed_genie_space_id", "GovernedGenieSpaceAbsent"):
        assert not hasattr(bundle_env, retired), retired
