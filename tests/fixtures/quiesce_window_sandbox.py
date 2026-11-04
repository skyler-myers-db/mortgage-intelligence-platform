"""A multi-process fake estate for the window library's kill test (M0-PR9).

``tests/unit/test_quiesce_window_shell.py``'s harness sources the real window
and EXIT-trap libraries with a fake ``$PYTHON``. For the kill test the fake
must survive the deploy process: this module is that ``$PYTHON``. Each call
loads the fake estate (``tests/fixtures/quiesce_window_estate.py``) from a
pickle, runs the REAL tool's ``main`` against it (``quiesce_window`` and the
rollback CLI, with the same stand-ins the in-process tests use for the lease,
health and the stored Gateway proof), and saves it back. Every other tool
call is recorded and answered like the shell harness's fake.

    PYTHON=<wrapper> where the wrapper runs: python -m tests.fixtures.quiesce_window_sandbox "$@"

The estate path is ``$MIP_SANDBOX_ESTATE``; the call log is ``$CALLS``.
"""

from __future__ import annotations

import os
import pickle
import sys
from pathlib import Path

from tests.fixtures.quiesce_window_estate import Estate

# Answered without running anything: a failing build leaves no binding payload.
_EXIT_CODES = {"tools.databricks.app_resource_bindings": 1}


def save(estate: Estate, path: Path) -> None:
    path.write_bytes(pickle.dumps(estate))


def load(path: Path) -> Estate:
    estate = pickle.loads(path.read_bytes())  # noqa: S301 - the test's own fake estate
    if not isinstance(estate, Estate):
        raise TypeError("the sandbox estate is not a fake Estate")
    return estate


def _stand_ins() -> None:
    """The lease, health, the stored Gateway proof and waits, as the in-process tests stub them."""

    from tests.unit.test_app_deployment_rollback import GIT_SHA, LEASE_ID, _binding
    from tools.databricks import app_deployment_lease, stop_app_fail_closed
    from tools.databricks import app_deployment_rollback as rollback
    from tools.databricks import app_rollback_record_contract as record_contract

    app_deployment_lease.assert_held = lambda *_a, **_k: None  # type: ignore[assignment]
    record_contract.assert_owned_app_rollback_scope = lambda *_a, **_k: None  # type: ignore[assignment]
    rollback.held_assertion = lambda *_a, **_k: lambda: None  # type: ignore[assignment]
    rollback._health = lambda *_a, **_k: (GIT_SHA, _binding(), LEASE_ID)  # type: ignore[assignment]
    rollback._stored_resource_proof = lambda *_a, **_k: None  # type: ignore[assignment]
    stop_app_fail_closed.time.sleep = lambda _s: None  # type: ignore[attr-defined]


def _run_tool(module: str, argv: list[str], estate: Estate) -> int:
    _stand_ins()
    if module == "tools.databricks.quiesce_window":
        from tools.databricks import quiesce_window

        return int(quiesce_window.main(argv, workspace_factory=lambda: estate.workspace))
    from tools.databricks import app_deployment_rollback_cli as cli

    cli.WorkspaceClient = lambda: estate.workspace  # type: ignore[assignment,misc]
    return int(cli.main(argv))


def main(argv: list[str]) -> int:
    with open(os.environ["CALLS"], "a", encoding="utf-8") as calls:
        calls.write(" ".join(argv) + "\n")
    if argv[:1] == ["-c"]:
        os.execv(sys.executable, [sys.executable, *argv])
    module = argv[1] if argv[:1] == ["-m"] and len(argv) > 1 else ""
    if module not in {"tools.databricks.quiesce_window", "tools.databricks.app_deployment_rollback"}:
        return _EXIT_CODES.get(module, 0)
    path = Path(os.environ["MIP_SANDBOX_ESTATE"])
    estate = load(path)
    try:
        return _run_tool(module, argv[2:], estate)
    except SystemExit as exc:
        return int(exc.code or 0) if isinstance(exc.code, int) else 2
    except Exception as exc:  # noqa: BLE001 - a tool failure is its exit code, as in a real run
        print(f"[sandbox] {module} raised {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        save(estate, path)


def wrapper(path: Path, repo: Path) -> Path:
    """An executable ``$PYTHON`` that runs this module with the repo on the path."""

    path.write_text(
        "#!/usr/bin/env bash\n"
        f'PYTHONPATH={str(repo)!r} exec {sys.executable!r} -m tests.fixtures.quiesce_window_sandbox "$@"\n',
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
