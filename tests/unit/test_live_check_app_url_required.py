"""The live checks take the App URL as a required input, never a default (M1-PR4).

An App URL names one client's workspace, so tools/verify_live.py and
scripts/e2e_live.sh refuse without it instead of probing a committed install.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tools import verify_live

ROOT = Path(__file__).resolve().parents[2]


def test_verify_live_refuses_without_a_base_url(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv(verify_live.BASE_URL_ENV, raising=False)
    monkeypatch.setattr(verify_live, "get_oauth_token", lambda profile: pytest.fail("no probe without a URL"))
    monkeypatch.setattr("sys.argv", ["verify_live.py"])

    with pytest.raises(SystemExit) as refused:
        verify_live.main()

    assert refused.value.code == 2
    assert "--base-url (or MIP_APP_URL) is required" in capsys.readouterr().err


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_environment_url_is_no_url(monkeypatch: pytest.MonkeyPatch, blank: str) -> None:
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
