"""A non-dry-run release sandbox that runs the real deploy.sh past the lease (M2-PR3).

The pre-stop refusal matrix (tests/unit/test_prestop_refusal_matrix.py) runs
the real ``scripts/deploy.sh`` with ``DRY_RUN=0`` and ends at the signed
lease. This extends that harness past it: the same offline sandbox checkout
(tests/fixtures/deploy_sandbox.py), the same fake estate and launcher for
the pre-stop probes (tests/fixtures/prestop_estate.py), and, for every later
step, a deploy interpreter (tests/fixtures/release_sandbox_python.py) and a
``databricks`` CLI (tests/fixtures/release_sandbox_cli.py) that answer what
a first install reads, through the Lakebase migration and the grant and
reconcile steps. ``npm`` is a stub that succeeds.

``release`` returns what the run did: its result, every interpreter and CLI
call with the identity names each was handed, and the sandbox state (the
lease writer, the SQL statements, the bundle job runs, the first-install
journal). Nothing can reach Databricks.

``excise`` removes one marked block (``# >>> name >>>`` to ``# <<< name <<<``)
from the clone's deploy scripts and commits that, to run the steps a refusal
stands in front of: the core tier's sites past the lease, which its release
refusal keeps unreachable until the core activation path exists. The block
must exist exactly once; the real checkout is never touched.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.fixtures.client_profiles import IDENTITY_IDS, env_file_text
from tests.fixtures.deploy_sandbox import INHERITED_PREFIXES, sandbox
from tests.fixtures.prestop_estate import DEPLOYER, clean_estate, write_estate


def first_install_estate() -> dict[str, Any]:
    """A workspace with no App yet: no Supervisor, no serving endpoint, no rollback record."""

    estate = clean_estate()
    estate["app"] = None
    estate["agents"] = []
    estate["serving"] = {}
    estate["record"] = "no server-owned last-good App rollback contract exists for mip-app"
    estate["principals"] = {
        name: row
        for name, row in estate["principals"].items()
        if name not in (IDENTITY_IDS["runtime"], IDENTITY_IDS["verifier"])
    }
    return estate


@dataclass(frozen=True)
class Release:
    result: subprocess.CompletedProcess[str]
    python_calls: list[dict[str, Any]]
    cli_calls: list[dict[str, Any]]
    state: dict[str, Any]

    @property
    def transcript(self) -> str:
        return self.result.stdout + self.result.stderr

    def modules(self) -> list[list[str]]:
        """The argv of every ``-m`` interpreter call, module first."""

        return [call["argv"][1:] for call in self.python_calls if call["argv"][:1] == ["-m"]]


def _wrapper(path: Path, program: Path, checkout: Path) -> Path:
    path.write_text(
        "#!/usr/bin/env bash\n"
        f"PYTHONPATH={shlex.quote(str(checkout))} exec {shlex.quote(sys.executable)} "
        f'{shlex.quote(str(program))} "$@"\n',
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _excise(checkout: Path, marker: str) -> None:
    start, end = f"# >>> {marker} >>>\n", f"# <<< {marker} <<<\n"
    hits = [path for path in sorted((checkout / "scripts").rglob("*.sh")) if start in path.read_text(encoding="utf-8")]
    assert len(hits) == 1, (marker, hits)
    text = hits[0].read_text(encoding="utf-8")
    assert text.count(start) == 1 and text.count(end) == 1, marker
    head, rest = text.split(start)
    hits[0].write_text(head + rest.split(end)[1], encoding="utf-8")
    subprocess.run(
        ["git", "-c", "user.name=Sandbox", "-c", "user.email=sandbox@example.invalid", "commit", "-qam", f"excise {marker}"],
        cwd=checkout,
        check=True,
    )


def release(
    tmp_path: Path,
    profile: dict[str, str],
    estate: dict[str, Any],
    *,
    client_ids: dict[str, str],
    excise: str | None = None,
) -> Release:
    """``scripts/deploy.sh -t client`` with DRY_RUN=0, in the sandbox, with only ``client_ids``."""

    checkout, base_env, cli_log = sandbox(tmp_path)
    if excise is not None:
        _excise(checkout, excise)
    # As in the refusal matrix: the fake estate's metastore is the deployer's,
    # and it accepts no shared-estate exposure (M2-PR4).
    estate_facts = {"MIP_UC_METASTORE_OWNER": DEPLOYER, "MIP_ACCEPTED_EXPOSURE_JSON": "[]"}
    (checkout / ".env.local").write_text(
        env_file_text({**profile, **estate_facts, "DATABRICKS_WAREHOUSE_ID": "warehouse-1"}),
        encoding="utf-8",
    )
    fixtures = checkout / "tests" / "fixtures"
    bin_dir = tmp_path / "bin"
    _wrapper(bin_dir / "databricks", fixtures / "release_sandbox_cli.py", checkout)
    (bin_dir / "npm").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (bin_dir / "npm").chmod(0o755)
    deploy_python = _wrapper(tmp_path / "deploy-python.sh", fixtures / "release_sandbox_python.py", checkout)
    estate["deployer"] = DEPLOYER
    estate_path = write_estate(tmp_path / "estate.json", estate)
    state_path, py_log = tmp_path / "sandbox-state.json", tmp_path / "python.log"
    # Also the profile names a shell value would set over .env.local.
    stripped = (*INHERITED_PREFIXES, "LAKEBASE_", "APP_ENV")
    run_env = {name: value for name, value in base_env.items() if not name.startswith(stripped)}
    run_env.update(
        {
            "PATH": base_env["PATH"],
            "HOME": str(tmp_path),
            "DATABRICKS_CONFIG_FILE": str(tmp_path / "no-databrickscfg"),
            "DATABRICKS_STUB_LOG": str(cli_log),
            "DATABRICKS_HOST": profile["DATABRICKS_HOST"],
            "DATABRICKS_TOKEN": "token-PLACEHOLDER",
            "MIP_DEPLOY_PYTHON": str(deploy_python),
            "MIP_TEST_PRESTOP_ESTATE": str(estate_path),
            "SANDBOX_STATE": str(state_path),
            "SANDBOX_PY_LOG": str(py_log),
            "MLFLOW_DISABLE_AGENT_HINT": "1",
            **client_ids,
        }
    )
    result = subprocess.run(
        ["bash", "scripts/deploy.sh", "-t", "client", "--no-confirm", "--skip-silver", "--skip-smoke"],
        cwd=checkout,
        env=run_env,
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
        timeout=900,
    )
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    return Release(result, _lines(py_log), _lines(cli_log), state)


def core_client_ids() -> dict[str, str]:
    """The shell's client ids for the four core identities and the account principal."""

    return {
        "DATABRICKS_CLIENT_ID": IDENTITY_IDS["normal"],
        "DATABRICKS_OPERATOR2_CLIENT_ID": IDENTITY_IDS["operator2"],
        "DATABRICKS_ADMIN_CLIENT_ID": IDENTITY_IDS["admin"],
        "DATABRICKS_RELEASE_PROBE_CLIENT_ID": IDENTITY_IDS["release_probe"],
        "DATABRICKS_ACCOUNT_CLIENT_ID": IDENTITY_IDS["account_principal"],
    }
