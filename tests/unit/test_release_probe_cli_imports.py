"""Regression coverage for release probes invoked by file path.

The repository's ``tools/databricks`` package must not shadow the installed
``databricks`` SDK when an operator uses the scripts' executable entrypoints.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "script",
    (
        "tools/verify_deployed_app_contract.py",
        "tools/verify_app_agent_green_path.py",
    ),
)
def test_release_probe_direct_invocation_imports_sdk(script: str) -> None:
    completed = subprocess.run(
        [sys.executable, script, "--help"],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert "No module named 'databricks.sdk'" not in completed.stderr
