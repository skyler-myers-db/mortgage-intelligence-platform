"""An offline sandbox for running ``scripts/deploy.sh --dry-run`` end to end.

A clone of HEAD plus the working tree's uncommitted tracked edits, a stub
``databricks`` that answers only ``--version`` and fails on anything else, an
``.env.local`` holding a full synthetic client profile (the paychex-shaped one
in tests/fixtures/client_profiles.py, M1-PR2) and a base environment stripped
of every ambient GitHub, Databricks and MIP name. Moved from the CI staging
target's dry-run test, which left with that target (M0-PR3).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.client_profiles import IDENTITY_IDS, env_file_text, paychex_shaped_profile

REPO = Path(__file__).resolve().parents[2]
ANSI = re.compile(r"\x1b\[[0-9;]*m")
STUB = (
    "#!/usr/bin/env bash\n"
    'printf \'%s\\n\' "$*" >> "$DATABRICKS_STUB_LOG"\n'
    'if [[ "$1" == "--version" ]]; then\n'
    "  echo 'Databricks CLI v-test'\n"
    "  exit 0\n"
    "fi\n"
    'echo "unexpected Databricks dry-run invocation: $*" >&2\n'
    "exit 97\n"
)
# Ambient names a dry run must never inherit from the developer's or the runner's shell.
INHERITED_PREFIXES = ("COV_CORE", "COVERAGE", "GITHUB_", "MIP_", "DATABRICKS_", "BUNDLE_VAR_", "GENIE_")


def venv_prefix() -> Path:
    """A prefix whose python imports the deploy tooling baseline (as in the first-install dry-run test)."""

    for candidate in (Path(sys.prefix), REPO / ".venv"):
        python = candidate / "bin" / "python"
        if python.exists() and subprocess.run([str(python), "-c", "import dotenv, pydantic"], capture_output=True).returncode == 0:
            return candidate
    pytest.fail("no interpreter prefix with the deploy tooling deps; run the suite from the project venv")


def sandbox(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    """(checkout, base env, stub CLI log) for one offline dry run."""

    checkout = tmp_path / "checkout"
    subprocess.run(["git", "clone", "--quiet", "--shared", str(REPO), str(checkout)], check=True, timeout=120)
    changed = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.split()
    for rel in changed:
        if (REPO / rel).is_file():
            shutil.copy2(REPO / rel, checkout / rel)
        else:
            (checkout / rel).unlink(missing_ok=True)  # an uncommitted deletion
    # deploy.sh refuses a dirty source, so the overlay is committed in the clone.
    subprocess.run(["git", "add", "--all"], cwd=checkout, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Dry Run", "-c", "user.email=dry-run@example.invalid", "commit", "--allow-empty", "-qm", "overlay"],
        cwd=checkout,
        check=True,
    )
    with (checkout / ".git" / "info" / "exclude").open("a", encoding="utf-8") as exclude:
        exclude.write(".venv\n")
    (checkout / ".venv").symlink_to(venv_prefix(), target_is_directory=True)
    (checkout / ".env.local").write_text(
        env_file_text(
            {
                **paychex_shaped_profile(),
                "DATABRICKS_WAREHOUSE_ID": "warehouse-1",
                "GENIE_SPACE_ID": "space-1",
            }
        ),
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "databricks").write_text(STUB, encoding="utf-8")
    (bin_dir / "databricks").chmod(0o755)
    log = tmp_path / "databricks.log"
    env = {name: value for name, value in os.environ.items() if not name.startswith(INHERITED_PREFIXES)}
    env.update(
        {
            # A Python SDK client the dry run builds by mistake must fail
            # offline, never resolve the developer's ~/.databrickscfg.
            "DATABRICKS_CONFIG_FILE": str(tmp_path / "no-databrickscfg"),
            "DATABRICKS_STUB_LOG": str(log),
            "GENIE_SPACE_ID": "space-1",
            # An identity alias must equal its MIP_IDENTITY_IDS_JSON role.
            "DATABRICKS_AGENT_RUNTIME_CLIENT_ID": IDENTITY_IDS["runtime"],
            "MIP_COTALITY_ID_MASK_SECRET": "stable-local-mask-secret",
            "MIP_GENIE_ACTION_SECRET_CURRENT": "stable-local-action-secret",
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        }
    )
    return checkout, env, log


def dry_run(checkout: Path, env: dict[str, str], target: str = "client") -> subprocess.CompletedProcess[str]:
    """``scripts/deploy.sh -t <target> --dry-run`` without silver or smoke."""

    return subprocess.run(
        ["bash", "scripts/deploy.sh", "-t", target, "--dry-run", "--no-confirm", "--skip-silver", "--skip-smoke"],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=300,
    )


def planned_commands(stdout: str) -> list[str]:
    """Every ``$ <command>`` line the dry run printed, ANSI codes stripped."""

    return [line[2:] for line in ANSI.sub("", stdout).splitlines() if line.startswith("$ ")]
