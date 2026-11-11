"""The live checks take the App URL as a required input, never a default (M1-PR4).

An App URL names one client's workspace, so tools/verify_live.py and
scripts/e2e_live.sh refuse without it instead of probing a committed install,
and nothing they or the load test write puts it back into the tracked tree:
verify_live's report goes outside it, and the load baseline records the
placeholder host.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest

from tools import verify_live
from tools.load_test import baseline

ROOT = Path(__file__).resolve().parents[2]


def test_verify_live_refuses_without_a_base_url(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv(verify_live.BASE_URL_ENV, raising=False)
    monkeypatch.setattr(verify_live, "get_oauth_token", lambda profile: pytest.fail("no probe without a URL"))
    monkeypatch.setattr("sys.argv", ["verify_live.py"])

    with pytest.raises(SystemExit) as refused:
        verify_live.main()

    assert refused.value.code == 2
    assert "--base-url (or MIP_APP_URL) is required" in capsys.readouterr().err


def _no_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any token mint or probe fails the test before it reaches the CLI or the network."""

    monkeypatch.setattr(verify_live, "get_oauth_token", lambda profile: pytest.fail("no token without a URL"))
    monkeypatch.setattr(verify_live, "run_probes", lambda base, token: pytest.fail("no probe without a URL"))
    monkeypatch.setattr(
        verify_live, "resolve_max_source_age_days", lambda profile: pytest.fail("no job read without a URL")
    )


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_environment_url_is_no_url(monkeypatch: pytest.MonkeyPatch, blank: str) -> None:
    _no_probe(monkeypatch)
    monkeypatch.setenv(verify_live.BASE_URL_ENV, blank)
    monkeypatch.setattr("sys.argv", ["verify_live.py"])

    with pytest.raises(SystemExit) as refused:
        verify_live.main()
    assert refused.value.code == 2


def test_verify_live_reads_the_url_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, str] = {}
    monkeypatch.setenv(verify_live.BASE_URL_ENV, " https://app.example ")
    monkeypatch.setattr(verify_live, "get_oauth_token", lambda profile: "test-token")
    monkeypatch.setattr(verify_live, "run_probes", lambda base, token: seen.update(base=base) or [])
    monkeypatch.setattr(verify_live, "collect_red_flags", lambda results, **_kwargs: [])
    out = tmp_path / "report.md"
    monkeypatch.setattr("sys.argv", ["verify_live.py", "--out", str(out), "--max-source-age-days", "8"])

    assert verify_live.main() == 0
    assert seen["base"] == "https://app.example"


def _run_with_the_default_report(monkeypatch: pytest.MonkeyPatch, temp_root: Path) -> None:
    """main() with no --out, the system temp directory at ``temp_root`` and every probe stubbed."""

    monkeypatch.setattr(tempfile, "tempdir", str(temp_root))
    monkeypatch.setattr(verify_live, "get_oauth_token", lambda profile: "test-token")
    monkeypatch.setattr(verify_live, "run_probes", lambda base, token: [])
    monkeypatch.setattr(verify_live, "collect_red_flags", lambda results, **_kwargs: [])
    monkeypatch.setattr(
        "sys.argv", ["verify_live.py", "--base-url", "https://app.example", "--max-source-age-days", "8"]
    )
    assert verify_live.main() == 0


def test_the_default_report_is_outside_the_tree(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    system_temp = Path(tempfile.gettempdir())
    assert not system_temp.resolve().is_relative_to(ROOT)
    assert verify_live.report_path_refusal(system_temp / verify_live.REPORT_NAME) is None

    # Each call makes a new private directory, so no one can have planted it.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "calls"))
    (tmp_path / "calls").mkdir()
    first, second = verify_live.default_report(), verify_live.default_report()
    assert first.parent != second.parent
    for report in (first, second):
        assert report.parent.parent == tmp_path / "calls" and not report.exists()
        assert stat.S_IMODE(report.parent.stat().st_mode) == 0o700

    # With no --out the report lands in a new private directory, and it holds the URL.
    _run_with_the_default_report(monkeypatch, tmp_path)
    [made] = tmp_path.glob(f"{verify_live.REPORT_DIR_PREFIX}*")
    assert stat.S_IMODE(made.stat().st_mode) == 0o700
    assert "https://app.example" in (made / verify_live.REPORT_NAME).read_text(encoding="utf-8")


def test_a_planted_symlink_in_the_shared_temp_directory_is_never_followed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Another local user can pre-create any fixed path in /tmp; the default is never one."""

    victim = tmp_path / "victim.txt"
    victim.write_text("VICTIM ORIGINAL CONTENT", encoding="utf-8")
    # The fixed path the default once was, pre-created by someone else.
    temp_root = tmp_path / "tmp"
    (temp_root / "mip-verify-live").mkdir(parents=True)
    (temp_root / "mip-verify-live" / verify_live.REPORT_NAME).symlink_to(victim)

    _run_with_the_default_report(monkeypatch, temp_root)

    assert victim.read_text(encoding="utf-8") == "VICTIM ORIGINAL CONTENT"
    [made] = temp_root.glob(f"{verify_live.REPORT_DIR_PREFIX}*")
    report = made / verify_live.REPORT_NAME
    assert not report.is_symlink() and "https://app.example" in report.read_text(encoding="utf-8")


@pytest.mark.parametrize("git", ["oserror", "timeout", "fatal_128", "ignored_0"])
def test_an_inside_path_is_refused_when_git_cannot_answer(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], git: str
) -> None:
    """git exits 128 in a checkout it calls dubious, or in a source copy that is no repo."""

    def fake_git(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        assert command[:3] == ["git", "check-ignore", "-q"]
        if git == "oserror":
            raise FileNotFoundError("git")
        if git == "timeout":
            raise subprocess.TimeoutExpired(command, 30)
        return subprocess.CompletedProcess(command, 128 if git == "fatal_128" else 0)

    monkeypatch.setattr(verify_live.subprocess, "run", fake_git)
    inside = ROOT / "docs" / "verify-live.md"
    if git == "ignored_0":  # the control: the fake alone decides
        assert verify_live.report_path_refusal(inside) is None
        return
    assert verify_live.report_path_refusal(inside) is not None

    _no_probe(monkeypatch)
    monkeypatch.setattr("sys.argv", ["verify_live.py", "--base-url", "https://app.example", "--out", str(inside)])
    with pytest.raises(SystemExit) as refused:
        verify_live.main()
    assert refused.value.code == 2
    assert "not git-ignored" in capsys.readouterr().err
    assert not inside.exists()


@pytest.mark.parametrize(
    "out",
    [
        "docs/validation/e2e-verification-2026-04-23.md",  # the tracked report it once wrote
        "docs/validation/e2e-verification-new.md",  # untracked, but one git add from a commit
        "e2e-verification.md",
    ],
)
def test_a_report_inside_the_tree_is_refused_before_any_probe(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], out: str
) -> None:
    _no_probe(monkeypatch)
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr("sys.argv", ["verify_live.py", "--base-url", "https://app.example", "--out", out])

    with pytest.raises(SystemExit) as refused:
        verify_live.main()

    assert refused.value.code == 2
    assert "not git-ignored" in capsys.readouterr().err
    assert not (ROOT / out).exists() or out.endswith("2026-04-23.md")


def test_a_git_ignored_report_inside_the_tree_is_allowed() -> None:
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    assert verify_live.report_path_refusal(ROOT / "test-results" / "verify-live.md") is None
    assert verify_live.report_path_refusal(ROOT / "docs" / "verify-live.md") is not None


@pytest.mark.parametrize(
    ("target", "label"),
    [
        ("https://app-1.example/api/v1", "https://<app-host>/api/v1"),
        ("https://user:pw@app-1.example:8443/api/v1?o=42#frag", "https://<app-host>/api/v1"),
        ("http://127.0.0.1:8000/api/v1", "http://<app-host>/api/v1"),
        ("app-1.example/api/v1", "https://<app-host>/api/v1"),
        ("unknown", "unknown"),
        ("  ", "unknown"),
    ],
)
def test_the_load_baseline_records_only_the_placeholder_host(tmp_path: Path, target: str, label: str) -> None:
    path = tmp_path / "baseline.json"
    baseline.write_baseline(path=path, stats={}, target=target, write_enabled=False)

    assert json.loads(path.read_text(encoding="utf-8"))["target"] == label


def test_the_committed_load_baseline_names_no_host() -> None:
    committed = json.loads((ROOT / "tools" / "load_test" / "baseline.json").read_text(encoding="utf-8"))
    assert committed["target"] == baseline.target_label(committed["target"])


def test_no_live_check_names_a_default_app_url() -> None:
    source = (ROOT / "tools" / "verify_live.py").read_text(encoding="utf-8")
    assert "DEFAULT_BASE" not in source and "databricksapps.com" not in source
    script = (ROOT / "scripts" / "e2e_live.sh").read_text(encoding="utf-8")
    assert 'APP_URL="${MIP_APP_URL:-}"' in script and "databricksapps.com" not in script


def test_e2e_live_refuses_without_mip_app_url(tmp_path: Path) -> None:
    env = {name: value for name, value in os.environ.items() if name not in {"MIP_APP_URL", "MIP_API_URL"}}
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "e2e_live.sh")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 2
    assert "MIP_APP_URL is required" in result.stderr
