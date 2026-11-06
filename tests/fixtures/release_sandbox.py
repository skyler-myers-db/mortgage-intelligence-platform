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
stands in front of. The block must exist exactly once; the real checkout is
never touched.

``smoke`` runs the live smoke step against a committed stand-in for
``scripts/smoke_live.sh`` that passes and records the expectations it was
handed (M2-PR7b): without a passing smoke a release never signs a capture.
Without it the run passes ``--skip-smoke``. ``fail`` injects one failure:
``{"smoke": 1}`` fails the smoke, ``{"capture": 2}`` the core capture.
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
from tests.fixtures.deploy_sandbox import inherited, sandbox
from tests.fixtures.prestop_estate import DEPLOYER, clean_estate, write_estate

# The deploy's identity is the client profile's deployer, as in a real install:
# the workspace's current user (the inventory principal), the bundle's
# current user and the App release principal are one service principal.
SANDBOX_DEPLOYER = IDENTITY_IDS["deployer"]


def _as_profile_deployer(estate: dict[str, Any]) -> dict[str, Any]:
    swapped: dict[str, Any] = json.loads(json.dumps(estate).replace(json.dumps(DEPLOYER), json.dumps(SANDBOX_DEPLOYER)))
    return swapped


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


SMOKE_STUB = """#!/usr/bin/env bash
printf '%s\\n' "agentic=${MIP_EXPECT_AGENTIC_CAPABILITIES:-} gateway=${MIP_REQUIRE_AI_GATEWAY_CLAIMABLE:-} sha=${MIP_EXPECT_GIT_SHA:-}" >> "$SANDBOX_SMOKE_LOG"
exit "${SANDBOX_SMOKE_EXIT:-0}"
"""


def _commit(checkout: Path, message: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=Sandbox", "-c", "user.email=sandbox@example.invalid", "commit", "-qam", message],
        cwd=checkout,
        check=True,
    )


def _stub_smoke(checkout: Path) -> None:
    smoke = checkout / "scripts" / "smoke_live.sh"
    smoke.write_text(SMOKE_STUB, encoding="utf-8")
    smoke.chmod(0o755)
    _commit(checkout, "stand-in smoke")


def _excise(checkout: Path, marker: str) -> None:
    start, end = f"# >>> {marker} >>>\n", f"# <<< {marker} <<<\n"
    hits = [path for path in sorted((checkout / "scripts").rglob("*.sh")) if start in path.read_text(encoding="utf-8")]
    assert len(hits) == 1, (marker, hits)
    text = hits[0].read_text(encoding="utf-8")
    assert text.count(start) == 1 and text.count(end) == 1, marker
    head, rest = text.split(start)
    hits[0].write_text(head + rest.split(end)[1], encoding="utf-8")
    _commit(checkout, f"excise {marker}")


def release(
    tmp_path: Path,
    profile: dict[str, str],
    estate: dict[str, Any],
    *,
    client_ids: dict[str, str],
    excise: str | None = None,
    smoke: bool = False,
    fail: dict[str, int] | None = None,
    agentic_env_cache: str | None = None,
    flags: tuple[str, ...] | None = None,
    env: dict[str, str] | None = None,
) -> Release:
    """``scripts/deploy.sh -t client --no-confirm`` with DRY_RUN=0, in the sandbox, with only ``client_ids``.

    ``agentic_env_cache`` is a ``.databricks/mip-agentic.env`` an earlier
    agentic run left in the checkout (gitignored, so the tree stays clean).
    ``flags`` replaces the default ``--skip-silver`` (and ``--skip-smoke``
    unless ``smoke``), and ``env`` adds names after the profile's are stripped
    (``GITHUB_ACTIONS=true`` for a live ``--phase``, which is CI-only).
    """

    checkout, base_env, cli_log = sandbox(tmp_path)
    if excise is not None:
        _excise(checkout, excise)
    if agentic_env_cache is not None:
        (checkout / ".databricks").mkdir(exist_ok=True)
        (checkout / ".databricks" / "mip-agentic.env").write_text(agentic_env_cache, encoding="utf-8")
    if smoke:
        _stub_smoke(checkout)
    # As in the refusal matrix: the fake estate's metastore is the deployer's,
    # and it accepts no shared-estate exposure (M2-PR4).
    estate_facts = {"MIP_UC_METASTORE_OWNER": SANDBOX_DEPLOYER, "MIP_ACCEPTED_EXPOSURE_JSON": "[]"}
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
    estate_path = write_estate(tmp_path / "estate.json", _as_profile_deployer(estate))
    state_path, py_log = tmp_path / "sandbox-state.json", tmp_path / "python.log"
    run_env = {name: value for name, value in base_env.items() if not inherited(name)}
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
            "SANDBOX_SMOKE_LOG": str(tmp_path / "smoke.log"),
            # A failure to inject: the smoke's exit, or a scripted tool's (``fail``).
            "SANDBOX_SMOKE_EXIT": str((fail or {}).get("smoke", 0)),
            "SANDBOX_FAIL_JSON": json.dumps(fail or {}),
            "MLFLOW_DISABLE_AGENT_HINT": "1",
            "MIP_TEST_DEPLOYER_JSON": json.dumps(SANDBOX_DEPLOYER),
            **client_ids,
            **(env or {}),
        }
    )
    default = ("--skip-silver", *(() if smoke else ("--skip-smoke",)))
    argv = ["--no-confirm", *(default if flags is None else flags)]
    result = subprocess.run(
        ["bash", "scripts/deploy.sh", "-t", "client", *argv],
        cwd=checkout,
        env=run_env,
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
        timeout=900,
    )
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    smoke_log = tmp_path / "smoke.log"
    if smoke_log.exists():
        state["smoke"] = smoke_log.read_text(encoding="utf-8").splitlines()
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
