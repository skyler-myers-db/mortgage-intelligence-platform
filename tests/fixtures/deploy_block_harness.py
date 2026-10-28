"""Drive one marker block of the deploy command-of-record in a small bash harness.

The blocks (``# >>> name >>>`` .. ``# <<< name <<<``) are read from
``deploy_entrypoint_text()`` (tests/fixtures/deploy_script.py), so a test runs
exactly the reviewed shell. The harness is stricter than deploy.sh: it adds
``set -E`` and an ERR trap that exits 99, so a failure that deploy.sh's
``on_error`` would turn into a stop is visible as exit 99. ``run`` is the real
function from the entrypoint; ``step`` only echoes. Stub tools are written as
``tools.databricks.<module>`` under the harness directory (``python -m``
imports from the working directory); each refuses any other argument vector
and leaves a ``<module>.called`` file behind when it runs.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

from tests.fixtures.deploy_script import deploy_entrypoint_text

# A deploy commit, a manifest digest and a deploy principal; none is live.
SHA = "0123456789ab" + "c" * 28
DIGEST = "fedcba987654"
RELEASE_ID = f"{SHA[:12]}-{DIGEST}"
PRINCIPAL = "deployer@example.invalid"
BUNDLE = f"/Workspace/Users/{PRINCIPAL}/.bundle/mortgage-intelligence-platform"


def block(start: str, end: str) -> str:
    """The one block between ``start`` and ``end``, markers included."""

    text = deploy_entrypoint_text()
    assert text.count(start) == 1 and text.count(end) == 1
    return text[text.index(start) : text.index(end) + len(end)]


def code(text: str) -> str:
    """``text`` without its comment lines."""

    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def function(name: str) -> str:
    """A top-level shell function of the entrypoint, with its closing brace."""

    text = deploy_entrypoint_text()
    start = text.index(f"\n{name}() {{\n") + 1
    return text[start : text.index("\n}\n", start) + 3]


def drive(
    repo: Path, shell: str, *, dry_run: int, env: dict[str, str], epilogue: str
) -> subprocess.CompletedProcess[str]:
    """Run ``shell`` under strict mode with the real ``run``; ``env`` are shell variables."""

    preamble = "".join(f"{name}={shlex.quote(value)}\n" for name, value in env.items())
    script = repo / "block.sh"
    script.write_text(
        "set -Eeuo pipefail\ntrap 'exit 99' ERR\n"
        f'RED=""\nYLW=""\nDIM=""\nRST=""\nDRY_RUN={dry_run}\nPYTHON={shlex.quote(sys.executable)}\n'
        f'step() {{ echo "step: $*"; }}\n{function("run")}{preamble}{shell}\n{epilogue}'
        "echo reached-after-block\n",
        encoding="utf-8",
    )
    return subprocess.run(
        ["bash", str(script)], cwd=repo, text=True, capture_output=True, check=False,
        env={"PATH": os.environ.get("PATH", ""), "HOME": str(repo)},
    )


def stub_tool(repo: Path, module: str, *, argv: list[str], body: str) -> Path:
    """A stub ``tools.databricks.<module>``; returns the file its call leaves behind."""

    package = repo / "tools" / "databricks"
    package.mkdir(parents=True, exist_ok=True)
    for init in (repo / "tools" / "__init__.py", package / "__init__.py"):
        init.write_text("", encoding="utf-8")
    called = repo / f"{module}.called"
    (package / f"{module}.py").write_text(
        "import os, sys\n"
        f"assert sys.argv[1:] == {argv!r}, sys.argv\n"
        f"open({str(called)!r}, 'a').write('called\\n')\n{body}",
        encoding="utf-8",
    )
    return called
