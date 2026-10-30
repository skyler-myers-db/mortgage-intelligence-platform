"""``python -m tools.clients``: check, resolve, mask and digest, value-free on stdout."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from tests.fixtures.client_profiles import DEMO_PROFILE, demo_profile
from tools.clients import profile as views
from tools.clients.__main__ import main
from tools.clients.environment import RawProfile, resolve
from tools.clients.schema import FIELD_CLASS, FieldClass

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "deploy" / "clients" / "example.env"


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    path = tmp_path / "client.env"
    path.write_text(
        "\n".join(f"{name}='{value}'" for name, value in demo_profile().items()) + "\n", encoding="utf-8"
    )
    return path


@pytest.fixture(autouse=True)
def _outside_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


def _identifying_values() -> list[str]:
    return [value for name, value in DEMO_PROFILE.items() if FIELD_CLASS[name] is not FieldClass.PUBLIC]


def test_check_accepts_the_template_only_with_allow_placeholders(capsys: pytest.CaptureFixture[str]) -> None:
    template = ["--env-file-only", "--env-file", str(TEMPLATE)]
    assert main(["check", *template, "--allow-placeholders"]) == 0
    assert capsys.readouterr().out.startswith("client profile ok (template): client=<client-key>")
    assert main(["check", *template]) == 2
    assert "holds a <placeholder>" in capsys.readouterr().err


def test_check_and_digest_print_no_identifying_value(env_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    profile = resolve(RawProfile(values=demo_profile()))
    assert main(["check", "--env-file-only", "--env-file", str(env_file)]) == 0
    assert main(["digest", "--env-file-only", "--env-file", str(env_file)]) == 0
    out = capsys.readouterr()
    assert out.out.splitlines() == [
        f"client profile ok: client=fictional-demo class=demo tier=on digest={views.digest(profile)}",
        views.digest(profile),
    ]
    assert not any(value in out.out + out.err for value in _identifying_values())


@pytest.mark.parametrize(
    ("emit", "view"),
    [("controls", views.deploy_controls), ("bundle-vars", views.bundle_vars), ("app-env", views.app_payload_env)],
)
def test_resolve_writes_one_private_view_and_nothing_to_stdout(
    emit: str, view, env_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_path = tmp_path / f"{emit}.json"
    assert main(["resolve", "--env-file-only", "--env-file", str(env_file), "--emit", emit, "--out", str(out_path)]) == 0
    assert stat.S_IMODE(out_path.stat().st_mode) == 0o600
    assert json.loads(out_path.read_text(encoding="utf-8")) == view(resolve(RawProfile(values=demo_profile())))
    out = capsys.readouterr()
    assert out.out == ""
    assert not any(value in out.err for value in _identifying_values())


def test_resolve_env_format_refuses_a_value_it_cannot_quote(
    env_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_path = tmp_path / "controls.env"
    args = ["resolve", "--env-file-only", "--env-file", str(env_file), "--emit", "app-env", "--out", str(out_path)]
    assert main([*args, "--format", "env"]) == 0
    assert "MIP_GENIE_SPACE_NAME='Mortgage Lead Intelligence'\n" in out_path.read_text(encoding="utf-8")
    quoted = env_file.parent / "quoted.env"
    quoted.write_text(
        env_file.read_text(encoding="utf-8").replace("'Mortgage Lead Intelligence'", '"Lender\'s Space"'),
        encoding="utf-8",
    )
    assert main([*args[:3], str(quoted), *args[4:], "--format", "env"]) == 2
    assert "MIP_GENIE_SPACE_NAME: cannot be written in env format" in capsys.readouterr().err


def test_resolve_registers_every_mask_on_stderr_before_writing(
    env_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    out_path = tmp_path / "controls.json"
    assert main(["resolve", "--env-file-only", "--env-file", str(env_file), "--emit", "controls", "--out", str(out_path)]) == 0
    out = capsys.readouterr()
    assert out.out == ""
    registered = [line.removeprefix("::add-mask::") for line in out.err.splitlines() if line.startswith("::add-mask::")]
    assert registered == list(views.masks(resolve(RawProfile(values=demo_profile()))))


def test_mask_prints_runner_commands_only_inside_actions(
    env_file: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    masks = views.masks(resolve(RawProfile(values=demo_profile())))
    assert main(["mask", "--env-file-only", "--env-file", str(env_file)]) == 0
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == f"not in GitHub Actions: would register {len(masks)} masks\n"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert main(["mask", "--env-file-only", "--env-file", str(env_file)]) == 0
    assert capsys.readouterr().out.splitlines() == [f"::add-mask::{mask}" for mask in masks]


@pytest.mark.parametrize("command", ["resolve", "mask"])
def test_a_template_cannot_be_resolved_or_masked(command: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([command, "--env-file-only", "--env-file", str(TEMPLATE), "--allow-placeholders"]) == 2
    assert f"a template cannot be used for {command}" in capsys.readouterr().err


def test_resolve_needs_a_view_and_a_path(env_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["resolve", "--env-file-only", "--env-file", str(env_file)]) == 2
    assert "--emit and --out are both required" in capsys.readouterr().err


def test_contexts_and_an_env_file_are_exclusive(env_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", "--vars-json", "env:UNSET", "--env-file", str(env_file)]) == 2
    assert "not both" in capsys.readouterr().err


def test_an_unexpected_failure_prints_its_class_only(
    env_file: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(_profile: object) -> str:
        raise RuntimeError(DEMO_PROFILE["DATABRICKS_HOST"])

    monkeypatch.setattr(views, "digest", explode)
    assert main(["digest", "--env-file-only", "--env-file", str(env_file)]) == 2
    out = capsys.readouterr()
    assert out.err == "client profile tool failed: RuntimeError\n"
