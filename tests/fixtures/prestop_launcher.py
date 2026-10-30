"""Run one deploy-interpreter invocation against the fake estate (test only).

The refusal matrix sets ``MIP_DEPLOY_PYTHON`` to a shell stub that hands each
real Python invocation of ``scripts/deploy.sh`` to this launcher with the
original arguments: ``-m <module> ...``, ``-c <code> ...``, ``- ...`` (a
heredoc on stdin) or ``<script> ...``. The launcher installs the estate named
by ``MIP_TEST_PRESTOP_ESTATE`` (tests/fixtures/prestop_estate.py) and then
runs the program exactly as the interpreter would.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

# The probe module also gets the estate's scripted seams (prestop_estate.install).
PROBES = "tools.databricks.deploy_prestop_probes"


def _install(*, probes: bool) -> None:
    from tests.fixtures.prestop_estate import estate_from_env, install

    install(estate_from_env(), probes=probes)


def main() -> None:
    sys.path.insert(0, str(Path.cwd()))
    args = sys.argv[1:]
    if args[0] == "-m":
        # Any module may build an SDK client, so every one gets the fake.
        _install(probes=args[1] == PROBES)
        sys.argv = [args[1], *args[2:]]
        runpy.run_module(args[1], run_name="__main__", alter_sys=True)
        return
    if args[0] == "-c":
        code, sys.argv = args[1], ["-c", *args[2:]]
    elif args[0] == "-":
        code, sys.argv = sys.stdin.read(), ["-", *args[1:]]
    else:
        code, sys.argv = Path(args[0]).read_text(encoding="utf-8"), list(args)
    # The deploy's many pure heredocs (.env.local reads, identity checks) skip
    # the fake's import cost; one that can build an SDK client gets it.
    if "databricks" in code:
        _install(probes=False)
    exec(compile(code, args[0], "exec"), {"__name__": "__main__"})  # noqa: S102


if __name__ == "__main__":
    main()
