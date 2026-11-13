"""bundle_env's stdout is exactly the CLI's plan document under GitHub Actions (wave 7f).

install_state_adopt reads ``bundle_env plan -t client -o json
--adopt-acked-genie-pin``'s stdout and hands it to ``parse_plan`` whole
(BundleEnvCli.plan). 10B-1 is the first governed run of that call, so its
stdout must be the CLI's document byte for byte: every line bundle_env prints
and every runner-mask registration (``::add-mask::``, which GITHUB_ACTIONS=true
switches on) belongs on stderr, or the parser refuses a valid plan.

Spike S10 could not measure this (it called the CLI directly), so this runs
bundle_env itself offline: client #1's synthetic core-tier profile (the tier
the cutover adopts), the acked Genie title match over the in-memory
PinWorkspace, and a stub ``databricks`` first on PATH that prints a canned
plan (tests/fixtures/bundle_plans/fresh_root_create.json) to stdout and a
warning to stderr. ``capfd`` reads the process's fd 1, which
is what the subprocess pipe of BundleEnvCli carries.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from tests.fixtures import bundle_plans as shapes
from tests.fixtures.client_profiles import core_tier_profile, env_file_text
from tests.fixtures.genie_pin_workspace import (
    APP,
    DEPLOYER,
    TITLE,
    PinWorkspace,
    Space,
    patch_rollback_scope,
)
from tools.clients.environment import KNOWN_NAMES
from tools.databricks import bundle_env, bundle_env_adopt
from tools.databricks import genie_space_pin as pin
from tools.databricks.bundle_plan_shape import parse_plan

CANDIDATE = "acked-candidate-space-0001"
MANAGERS = ("creator@lender.example", "genie-workbench-sp")
ARGV = ["plan", "-t", "client", "-o", "json", bundle_env_adopt.ADOPT_GENIE_FLAG]


def _stub_cli(folder: Path, plan_text: str) -> Path:
    """A ``databricks`` that records its argv, prints ``plan_text`` to stdout and a warning to stderr."""

    folder.mkdir()
    (folder / "plan.json").write_text(plan_text, encoding="utf-8")
    script = folder / "databricks"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" > "{folder}/argv"\n'
        'echo "Warning: a CLI warning goes to stderr" >&2\n'
        f'cat "{folder}/plan.json"\n',
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return folder


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bundle_env, "BUNDLE_ROOT", tmp_path / "bundle-root")
    monkeypatch.delenv("DATABRICKS_BUNDLE_ROOT", raising=False)
    for name in (*KNOWN_NAMES, bundle_env_adopt.GENIE_PIN_ACK_ENV, "GENIE_SPACE_ID", "DATABRICKS_WAREHOUSE_ID"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("actions", ("true", ""), ids=("github-actions", "local"))
def test_the_adopt_plan_call_prints_the_clis_document_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str], actions: str
) -> None:
    canned = shapes.text("fresh_root_create")
    stub = _stub_cli(tmp_path / "stub", canned)
    monkeypatch.setenv("PATH", f"{stub}:{__import__('os').environ.get('PATH', '')}")
    monkeypatch.setenv("GITHUB_ACTIONS", actions)
    plan = pin.pin_plan(app_name=APP, title=TITLE, space_id=CANDIDATE, managers=tuple(sorted(MANAGERS)), deployer=DEPLOYER)
    monkeypatch.setenv(bundle_env_adopt.GENIE_PIN_ACK_ENV, pin.plan_sha256(plan))
    workspace = PinWorkspace(spaces=[Space(CANDIDATE, title=TITLE, managers=MANAGERS)])
    env_local = tmp_path / ".env.local"
    env_local.write_text(env_file_text(core_tier_profile()), encoding="utf-8")
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    monkeypatch.setattr(bundle_env, "REPO", tmp_path)
    patch_rollback_scope(monkeypatch)
    monkeypatch.setattr(bundle_env, "_genie_workspace_client", lambda _env: workspace)
    monkeypatch.setattr(bundle_env.render_sql, "render", lambda **_kwargs: (1, 0, 0))
    monkeypatch.setattr(bundle_env.scheduled_refresh_posture, "apply", lambda env, **_kw: "refresh: test")
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", *ARGV])

    rc = bundle_env.main()

    out, err = capfd.readouterr()
    assert rc == 0, err
    assert (stub / "argv").read_text(encoding="utf-8") == "bundle plan -t client -o json\n", "the flag is wrapper-only"
    assert out == canned, "bundle_env's stdout is the CLI's document, byte for byte"
    assert "Warning: a CLI warning goes to stderr" in err and "[bundle_env]" in err
    masks = [line for line in err.splitlines() if line.startswith("::add-mask::")]
    assert bool(masks) == (actions == "true"), "the runner masks go to stderr, under GitHub Actions only"
    assert len(parse_plan(out).entries) == 18
    assert json.loads(out) == shapes.load("fresh_root_create")
