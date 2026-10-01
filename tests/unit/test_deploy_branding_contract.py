"""Deploy wiring for the optional lender co-branding (audit responsive-10, 12.4 #9).

``scripts/deploy.sh`` preflight validates ``MIP_LENDER_MARK_FILE`` against the
reviewed registry before any workspace mutation and derives
``MIP_LENDER_MARK_SHA256``; Step 1 stages the validated mark right before
``npm run build``. The preflight block is also EXECUTED here, extracted from
its step file, both with the branding tool present and as the isolated
shell-contract copy that has none.
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from dotenv import dotenv_values

from tests.fixtures.deploy_script import deploy_entrypoint_text

ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = ROOT / "scripts" / "lib" / "deploy_step_preflight_gates.sh"
BLOCK_START = "# Optional lender co-branding (audit responsive-10"
BLOCK_END = "export MIP_LENDER_MARK_FILE MIP_DEFAULT_THEME MIP_DEFAULT_ACCENT MIP_LENDER_MARK_SHA256\n"
VALIDATE = '"$PYTHON" -m tools.branding.lender_mark validate'
STAGE = 'run "$PYTHON" -m tools.branding.lender_mark stage'

# The lines the integrator appends to .env.example (agents cannot edit .env.*):
# each comment on its own line, the variable bare.
PROPOSED_ENV_LINES = """\
# optional deploy-only: absolute path (outside the checkout) to the reviewed lender mark PNG/WebP; validated at preflight against backend/schemas/lender_branding.py; empty = no mark; never commit the image
MIP_LENDER_MARK_FILE=
# optional deploy-only: tenant-default theme for users who have not chosen (dark | light | system); dark emits nothing
MIP_DEFAULT_THEME=dark
# optional deploy-only: tenant-default accent for users who have not chosen (bright | teal | navy | red); bright emits nothing
MIP_DEFAULT_ACCENT=bright
"""


def _block() -> str:
    text = PREFLIGHT.read_text(encoding="utf-8")
    start = text.index(BLOCK_START)
    end = text.index(BLOCK_END, start) + len(BLOCK_END)
    return text[start:end]


def _run_block(cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the preflight branding block with the helpers it relies on stubbed."""
    harness = (
        "set -euo pipefail\n"
        "RED=''; RST=''\n"
        "deployment_control_value() { local value=\"${!1:-}\"; printf '%s' \"${value:-${2:-}}\"; }\n"
        'MIP_LENDER_NAME="Summit Mortgage"; MIP_LENDER_NMLS_ID=123456\n'
        f"{_block()}"
        'echo "sha=[${MIP_LENDER_MARK_SHA256}] theme=${MIP_DEFAULT_THEME} accent=${MIP_DEFAULT_ACCENT}"\n'
    )
    base = {key: value for key, value in os.environ.items() if not key.startswith("MIP_")}
    return subprocess.run(
        ["bash", "-c", harness],
        cwd=cwd,
        env={**base, "PYTHON": sys.executable, "PYTHONPATH": str(ROOT), **env},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_validate_runs_in_preflight_before_any_mutation() -> None:
    deploy = deploy_entrypoint_text()
    identity_read = deploy.index("IFS=$'\\t' read -r MIP_LENDER_NAME MIP_LENDER_NMLS_ID MIP_TENANT_ID")
    validate = deploy.index(VALIDATE)
    assert deploy.count(VALIDATE) == 1
    assert identity_read < validate
    assert validate < deploy.index("tools.databricks.app_deployment_lease acquire")
    assert validate < deploy.index('step "build frontend')


def test_stage_runs_inside_step_1_right_before_the_build_with_no_new_step() -> None:
    deploy = deploy_entrypoint_text()
    step_1 = deploy.index('step "build frontend')
    build = deploy.index("run npm --prefix frontend run build")
    stage = deploy.index(STAGE)
    assert deploy.count(STAGE) == 1
    assert step_1 < stage < build
    assert "step " not in deploy[step_1 + len('step "build frontend') : build].replace("--expect-sha256", "")
    # run echoes its arguments, so the stage names the variable, never the path.
    stage_call = deploy[stage : deploy.index("--out frontend/.branding-stage", stage)]
    assert "--file-env MIP_LENDER_MARK_FILE" in stage_call
    assert "$MIP_LENDER_MARK_FILE" not in stage_call


def test_only_the_hash_prefix_is_ever_logged() -> None:
    block = _block()
    echoes = [line for line in block.splitlines() if line.strip().startswith("echo ")]
    assert echoes, "the block logs its outcome"
    for line in echoes:
        assert "MIP_LENDER_MARK_FILE}" not in line and "$MIP_LENDER_MARK_FILE" not in line, line
    assert re.findall(r"\$\{?MIP_LENDER_MARK_SHA256[^}]*\}?", " ".join(echoes)) == ["${MIP_LENDER_MARK_SHA256:0:12}"]


def test_the_block_exports_its_controls_and_derives_the_hash() -> None:
    block = _block()
    assert block.endswith(BLOCK_END)
    assert 'MIP_LENDER_MARK_SHA256="$(' in block
    assert "deployment_control_value MIP_LENDER_MARK_SHA256" not in block, "derived, never operator-set"


def test_no_mark_resolves_to_an_empty_hash_with_the_tool_present() -> None:
    proc = _run_block(ROOT, {})
    assert proc.returncode == 0, proc.stderr
    assert "[deploy] lender mark: none" in proc.stdout
    assert proc.stdout.rstrip().endswith("sha=[] theme=dark accent=bright")


def test_a_mark_file_for_a_lender_without_a_reviewed_mark_stops_preflight_path_free(tmp_path: Path) -> None:
    mark = tmp_path / "operator-private-dir" / "mark.png"
    mark.parent.mkdir()
    mark.write_bytes(b"\x89PNG\r\n\x1a\n")
    proc = _run_block(ROOT, {"MIP_LENDER_MARK_FILE": str(mark)})
    assert proc.returncode == 2
    assert "lender mark did not pass the reviewed lender-mark registry" in proc.stderr
    assert "operator-private-dir" not in proc.stdout + proc.stderr


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"MIP_DEFAULT_THEME": "sepia"}, "MIP_DEFAULT_THEME must be dark, light or system"),
        ({"MIP_DEFAULT_ACCENT": "magenta"}, "MIP_DEFAULT_ACCENT must be bright, teal, navy or red"),
    ],
)
def test_theme_and_accent_are_validated(env: dict[str, str], message: str) -> None:
    proc = _run_block(ROOT, env)
    assert proc.returncode == 2
    assert message in proc.stderr


def test_tenant_defaults_pass_through() -> None:
    proc = _run_block(ROOT, {"MIP_DEFAULT_THEME": "system", "MIP_DEFAULT_ACCENT": "navy"})
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.rstrip().endswith("sha=[] theme=system accent=navy")


def test_the_isolated_copy_without_the_tool_allows_only_no_mark(tmp_path: Path) -> None:
    # The isolated shell-contract harnesses copy only deploy.sh: no tools/branding.
    proc = _run_block(tmp_path, {"MIP_LENDER_MARK_FILE": "/elsewhere/mark.png"})
    assert proc.returncode == 2
    assert "lender mark tool is unavailable; unset MIP_LENDER_MARK_FILE" in proc.stderr
    assert "/elsewhere" not in proc.stdout + proc.stderr

    proc = _run_block(tmp_path, {})
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.rstrip().endswith("sha=[] theme=dark accent=bright")


def test_the_stage_dir_is_ignored_and_no_mark_is_tracked() -> None:
    ignored = {
        line.strip()
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert "frontend/.branding-stage/" in ignored
    tracked = subprocess.run(
        ["git", "ls-files", "frontend/.branding-stage", "frontend/public/branding"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert tracked.stdout == ""


def test_the_proposed_env_lines_parse_to_the_product_defaults() -> None:
    assert dotenv_values(stream=io.StringIO(PROPOSED_ENV_LINES)) == {
        "MIP_LENDER_MARK_FILE": "",
        "MIP_DEFAULT_THEME": "dark",
        "MIP_DEFAULT_ACCENT": "bright",
    }


def test_env_example_documents_the_branding_controls() -> None:
    """EXPECTED RED until the integrator appends PROPOSED_ENV_LINES to .env.example
    (W5b: agents cannot edit .env.*; test_settings_contract.py belongs to another lane)."""
    documented = dotenv_values(ROOT / ".env.example")
    missing = [name for name in ("MIP_LENDER_MARK_FILE", "MIP_DEFAULT_THEME", "MIP_DEFAULT_ACCENT") if name not in documented]
    assert missing == []
