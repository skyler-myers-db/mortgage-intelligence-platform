"""Drive the deploy shell's runner-mask helpers on one merged stream (test only).

``scripts/deploy.sh`` registers every id it resolves at run time as a GitHub
runner mask before a command is handed it (``mask_runtime_ids``,
``mask_deployment_ids``). These helpers run the reviewed shell
(tests/fixtures/deploy_block_harness.py) with the real
``tools.clients.redact`` module copied beside it, stdout and stderr merged, so
the order of a mask and the first use of a value is the job log's own.
"""

from __future__ import annotations

import shlex
import shutil
from pathlib import Path

from tests.fixtures.deploy_block_harness import drive, function
from tests.fixtures.deploy_script import deploy_contract_text, deploy_entrypoint_text

REPO = Path(__file__).resolve().parents[2]
ACTIONS = "export GITHUB_ACTIONS=true\n"
# The bounded-authority wrappers echo their command as the real ones do.
WRAPPERS = (
    'run_with_account_identity() { "$@"; }\n'
    'run_with_proof_signing_authority() { echo "\\$ $*"; "$@"; }\n'
)


def mask_helpers() -> str:
    """The shipped mask helpers, as the entrypoint defines them."""

    return function("mask_runtime_ids") + function("mask_deployment_ids") + function("mask_app_url")


def redact_module(repo: Path) -> None:
    """The real mask module, importable from the harness directory."""

    clients = repo / "tools" / "clients"
    clients.mkdir(parents=True, exist_ok=True)
    for init in (repo / "tools" / "__init__.py", clients / "__init__.py"):
        init.write_text("", encoding="utf-8")
    shutil.copyfile(REPO / "tools" / "clients" / "redact.py", clients / "redact.py")


def env_writer(repo: Path, module: str, values: dict[str, str]) -> None:
    """A ``tools.databricks.<module>`` that writes ``values`` to its ``--out-env`` and prints nothing."""

    package = repo / "tools" / "databricks"
    package.mkdir(parents=True, exist_ok=True)
    for init in (repo / "tools" / "__init__.py", package / "__init__.py"):
        init.write_text("", encoding="utf-8")
    text = "".join(f"{name}={shlex.quote(value)}\n" for name, value in values.items())
    (package / f"{module}.py").write_text(
        "import sys\n"
        "args = sys.argv[1:]\n"
        "if '--out-env' in args:\n"
        f"    open(args[args.index('--out-env') + 1], 'w', encoding='utf-8').write({text!r})\n",
        encoding="utf-8",
    )


def merged_log(
    repo: Path, shell: str, *, actions: bool = True, redact: bool = True, env: dict[str, str] | None = None
) -> tuple[int, list[str]]:
    """(exit code, the merged log's lines) of ``shell`` with the two mask helpers defined.

    ``redact=False`` leaves the mask module out, so a mask cannot be registered.
    """

    if redact:
        redact_module(repo)
    result = drive(
        repo,
        (ACTIONS if actions else "") + mask_helpers() + "{\n" + shell + "\n} 2>&1\n",
        dry_run=0,
        env=env or {},
        epilogue="",
    )
    assert result.stderr == "", result.stderr
    return result.returncode, result.stdout.splitlines()


def shell_slice(start: str, end: str) -> str:
    """The reviewed shell from ``start`` through ``end``, each present exactly once."""

    text = deploy_entrypoint_text()
    assert text.count(start) == 1 and text.count(end) == 1, (start, end)
    return text[text.index(start) : text.index(end) + len(end)]


def shell_function(name: str) -> str:
    """A top-level shell function of any sourced file, the reviewed libraries included."""

    text = deploy_contract_text()
    head = f"\n{name}() {{\n"
    assert text.count(head) == 1, name
    start = text.index(head) + 1
    return text[start : text.index("\n}\n", start) + 3]


def carrying(lines: list[str], value: str) -> list[str]:
    return [line for line in lines if value in line]
