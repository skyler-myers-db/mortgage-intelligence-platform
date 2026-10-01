"""The deploy preflight's auditor segregation-of-duties check (D-audit-reads-c3).

``scripts/lib/deploy_step_preflight_gates.sh`` (step 0 of
``./scripts/deploy.sh -t dev``) counts the configured auditors and how many
of them also hold the administrator or approver role, and warns -- never
refuses -- when that overlap is non-zero. It prints counts only: no identity
may reach stdout or stderr. Each list resolves from the shell env first, else
``.env.local``, the precedence the admin-allowlist check above it uses.

The block is sliced from the step file between its marker comments and run
in a scratch cwd, so the test exercises the shipped text, not a copy.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STEP = ROOT / "scripts" / "lib" / "deploy_step_preflight_gates.sh"
START = "# >>> auditor-sod-check"
END = "# <<< auditor-sod-check"
_ROLE_VARS = (
    "MIP_AUDITOR_EMAILS",
    "MIP_AUDITOR_IDENTITIES",
    "MIP_ADMIN_EMAILS",
    "MIP_ADMIN_IDENTITIES",
    "MIP_APPROVER_EMAILS",
    "MIP_APPROVER_IDENTITIES",
)
_ENV_LOCAL = "\n".join(
    (
        "MIP_AUDITOR_EMAILS=a1@example.com,A2@Example.com",
        "MIP_AUDITOR_IDENTITIES=audit-client",
        "MIP_ADMIN_EMAILS=a2@example.com",
        "MIP_APPROVER_EMAILS=someone@example.com",
        "",
    )
)


def _block() -> str:
    text = STEP.read_text(encoding="utf-8")
    assert text.count(START) == 1 and text.count(END) == 1, "the marker comments bound one block"
    start = text.index(START)
    return text[start : text.index(END) + len(END)]


def _heredoc() -> str:
    block = _block()
    body = block.split("<<'PYEOF'\n", 1)[1]
    return body.split("\nPYEOF\n", 1)[0] + "\n"


def _env(**overrides: str) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in _ROLE_VARS}
    env.update(overrides)
    return env


def _run_block(cwd: Path, **overrides: str) -> subprocess.CompletedProcess[str]:
    script = "\n".join(("set -euo pipefail", 'YLW=""', 'RST=""', _block(), ""))
    return subprocess.run(
        ["bash", "-c", script],
        cwd=cwd,
        env=_env(PYTHON=sys.executable, **overrides),
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    (tmp_path / ".env.local").write_text(_ENV_LOCAL, encoding="utf-8")
    return tmp_path


def test_the_heredoc_prints_counts_for_a_crafted_overlap(workdir: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-"],
        input=_heredoc(),
        cwd=workdir,
        env=_env(),
        capture_output=True,
        text=True,
        check=True,
    )

    # Three auditors (case-folded); one of them (a2) is also an administrator.
    assert result.stdout == "3\t1\n"
    assert "@" not in result.stdout + result.stderr


def test_the_shell_env_beats_env_local(workdir: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-"],
        input=_heredoc(),
        cwd=workdir,
        env=_env(MIP_ADMIN_EMAILS="nobody@example.com", MIP_AUDITOR_IDENTITIES="x,y"),
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout == "4\t0\n"


def test_the_block_warns_on_overlap_and_never_prints_an_identity(workdir: Path) -> None:
    result = _run_block(workdir)

    assert "  auditor allowlist: 3 configured" in result.stdout
    assert (
        "WARNING: 1 auditor identity also holds the administrator or approver role; "
        "auditors should hold neither (segregation of duties)." in result.stderr
    )
    combined = result.stdout + result.stderr
    assert "@" not in combined
    for identity in ("a1", "a2", "audit-client", "someone"):
        assert identity not in combined.lower()


def test_the_block_is_quiet_without_overlap_and_never_refuses(tmp_path: Path) -> None:
    result = _run_block(tmp_path, MIP_AUDITOR_EMAILS="solo@example.com")

    assert "  auditor allowlist: 1 configured" in result.stdout
    assert "WARNING" not in result.stderr

    plural = _run_block(
        tmp_path,
        MIP_AUDITOR_EMAILS="p@example.com,q@example.com",
        MIP_APPROVER_IDENTITIES="P@example.com,q@example.com",
    )
    assert "2 auditor identities also hold the administrator or approver role" in plural.stderr


def test_the_check_sits_after_the_admin_allowlist_check() -> None:
    text = STEP.read_text(encoding="utf-8")

    assert text.index('echo "  admin allowlist: configured') < text.index(START)
    assert "Warn, never refuse; counts only." in _block()
    assert "exit" not in _block(), "the SoD check warns; it never stops the deploy"
