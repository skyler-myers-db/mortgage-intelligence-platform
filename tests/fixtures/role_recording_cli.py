"""A stub ``databricks`` CLI that records the authority each call needs (M3-PR4).

The portability proof's sandbox puts this on ``PATH`` in place of the plain
stub in tests/fixtures/deploy_sandbox.py, behind a wrapper that runs it with
the deploy interpreter. For every invocation it appends one JSON line to
``$PROOF_CLI_LEDGER``: the argv, its class (tests/fixtures/role_ledger.py),
the ``BUNDLE_VAR_*`` values it was handed and the sha256 of the
``sql/_rendered`` tree in its working directory at that moment (what a bundle
command would have uploaded). It also appends the argv to
``$DATABRICKS_STUB_LOG``, as the plain stub does.

It answers ``--version`` and ``bundle validate``, which change nothing; any
other call fails with exit 97, so nothing can reach Databricks.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.fixtures.role_ledger import cli_role  # noqa: E402


def tree_sha256(root: Path) -> str | None:
    """sha256 over each file's relative path and sha256, in path order; ``None`` when absent."""

    if not root.is_dir():
        return None
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(f"{path.relative_to(root).as_posix()}\t{hashlib.sha256(path.read_bytes()).hexdigest()}\n".encode())
    return digest.hexdigest()


def main(argv: list[str]) -> int:
    record = {
        "argv": argv,
        "role": cli_role(argv),
        "bundle_vars": {name: value for name, value in sorted(os.environ.items()) if name.startswith("BUNDLE_VAR_")},
        "rendered_sha256": tree_sha256(Path.cwd() / "sql" / "_rendered"),
    }
    with open(os.environ["PROOF_CLI_LEDGER"], "a", encoding="utf-8") as ledger:
        ledger.write(json.dumps(record, sort_keys=True) + "\n")
    with open(os.environ["DATABRICKS_STUB_LOG"], "a", encoding="utf-8") as log:
        log.write(" ".join(argv) + "\n")
    if argv == ["--version"]:
        print("Databricks CLI v-test")
        return 0
    if argv[:2] == ["bundle", "validate"]:
        return 0
    print(f"unexpected Databricks dry-run invocation: {' '.join(argv)}", file=sys.stderr)
    return 97


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
