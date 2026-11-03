"""A run-now with parameters the bundle did not define exits before any SQL (M2-PR5).

* jobs/_install_pins.py reads exactly the reviewed pins shape from its own
  folder; a missing or malformed file refuses.
* Each jobs-identity Python entrypoint's ``__main__`` path (the App-triggerable
  jobs', and the KPI snapshot's, which the weekly refresh starts) goes
  through its ``run``, which judges the parameters against the pins first (the lifecycle
  sync's is jobs/sync_lifecycle_state_job.py, since the App imports
  jobs/sync_lifecycle_state.py and that module stays as the App runs it): an overridden
  ``--catalog``, ``--lakebase-instance``, ``--lakebase-database`` or
  ``--table``, or an option the bundle does not pass, exits 5 before a Spark
  session, a Lakebase credential or a psycopg connection exists (fakes record
  every one). The bundle's own parameters pass and reach ``main``.
* The loader finds the module beside the script by exact path, with or
  without ``__file__``, and refuses anywhere else.
* The App-imported jobs/sync_lifecycle_state.py is unchanged by the gate.
* tools/databricks/bundle_env.py renders the pins into the bundle root before
  the CLI runs, from the profile, and databricks.yml syncs the gitignored file.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest
import yaml

from jobs import _install_pins as pins_module
from jobs import fred_rates_ingest as fred
from jobs import kpi_snapshot as kpi
from jobs import sync_lifecycle_state as sync
from jobs import sync_lifecycle_state_job as sync_job
from tests.fixtures.client_profiles import env_file_text, paychex_shaped_profile
from tools.databricks import bundle_env, install_pins

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = yaml.safe_load((ROOT / "databricks.yml").read_text(encoding="utf-8"))
PINS = install_pins.document(
    catalog="mip",
    lakebase_instance="mip-app-state",
    lakebase_database="mip_app_state",
    data_owner_group="mip-data-owners",
)
SYNC_ARGS = ["--catalog=mip", "--lakebase-instance=mip-app-state", "--lakebase-database=mip_app_state"]
FRED_ARGS = ["--mode=fred", "--table=mip.silver.market_rates_weekly"]
_AMBIENT_PREFIXES = ("MIP_", "DATABRICKS_", "BUNDLE_VAR_", "GENIE_", "LAKEBASE_", "APP_ENV")


def _job_folder(tmp_path: Path, pins: dict[str, Any] | None = PINS) -> Path:
    """A bundle files root with jobs/_install_pins.py and, unless None, the pins."""

    folder = tmp_path / "files" / "jobs"
    folder.mkdir(parents=True)
    for name in ("_install_pins.py", "sync_lifecycle_state.py"):
        shutil.copy(ROOT / "jobs" / name, folder / name)
    if pins is not None:
        install_pins.render(tmp_path / "files", pins)
    return folder


class _Recorder:
    """Every way a job could reach SQL or Lakebase, recorded instead of done."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[str] = []
        psycopg = types.ModuleType("psycopg")
        psycopg.connect = lambda *a, **k: self._record("psycopg.connect")  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "psycopg", psycopg)
        pyspark, pyspark_sql = types.ModuleType("pyspark"), types.ModuleType("pyspark.sql")

        class SparkSession:
            builder = types.SimpleNamespace(getOrCreate=lambda: self._record("spark.builder"))

            @staticmethod
            def getActiveSession() -> Any:  # noqa: N802 - pyspark's name
                return self._record("spark.getActiveSession")

        pyspark_sql.SparkSession = SparkSession  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "pyspark", pyspark)
        monkeypatch.setitem(sys.modules, "pyspark.sql", pyspark_sql)
        monkeypatch.setattr(sync, "_resolve_connection", lambda **k: self._record("lakebase.credential"))
        monkeypatch.setattr(sync, "_fetch_lakebase_rows", lambda *a: self._record("lakebase.read"))
        monkeypatch.setattr(sync, "_get_spark", lambda: self._record("spark"))
        monkeypatch.setattr(fred, "main", lambda argv=None: self._record("fred.main") or 0)
        monkeypatch.setattr(fred, "_get_spark", lambda: self._record("spark"), raising=False)

    def _record(self, name: str) -> Any:
        self.calls.append(name)
        return None


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    return _Recorder(monkeypatch)


def _beside(monkeypatch: pytest.MonkeyPatch, module: types.ModuleType, folder: Path) -> None:
    """Run ``module``'s job entrypoint from ``folder``; the lifecycle sync it loads is the recorded one."""

    entry = sync_job if module is sync else module
    monkeypatch.setattr(entry, "__file__", str(folder / Path(entry.__file__ or "").name))
    if entry is sync_job:
        real = sync_job._beside
        monkeypatch.setattr(
            sync_job,
            "_beside",
            lambda filename, name: sync if filename == "sync_lifecycle_state.py" else real(filename, name),
        )


def _run(module: types.ModuleType, argv: list[str]) -> Any:
    return (sync_job if module is sync else module).run(argv)


# --------------------------------------------------------------- the pins file


def test_the_rendered_pins_load_and_nothing_else_does(tmp_path: Path) -> None:
    folder = _job_folder(tmp_path)
    loaded = pins_module.load(folder / "install_pins.json")
    assert loaded == pins_module.InstallPins("mip", "mip-app-state", "mip_app_state", "mip-data-owners")
    for broken, reason in (
        ({**PINS, "extra": "x"}, "exactly the fields"),
        ({k: v for k, v in PINS.items() if k != "catalog"}, "exactly the fields"),
        ({**PINS, "version": 2}, "version must be 1"),
        ({**PINS, "version": True}, "version must be 1"),
        ({**PINS, "catalog": "Mip"}, "field catalog is malformed"),
        ({**PINS, "lakebase_instance": "mip_app"}, "field lakebase_instance is malformed"),
        ({**PINS, "data_owner_group": ""}, "field data_owner_group is malformed"),
    ):
        (folder / "install_pins.json").write_text(json.dumps(broken), encoding="utf-8")
        with pytest.raises(pins_module.InstallPinsRefused) as caught:
            pins_module.load(folder / "install_pins.json")
        assert reason in str(caught.value)
    (folder / "install_pins.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(pins_module.InstallPinsRefused, match="unreadable"):
        pins_module.load(folder / "install_pins.json")
    (folder / "install_pins.json").unlink()
    with pytest.raises(pins_module.InstallPinsRefused, match="missing"):
        pins_module.load(folder / "install_pins.json")


# ------------------------------------------- run-now overrides exit before SQL


@pytest.mark.parametrize(
    ("override", "line"),
    [
        ("--catalog=other_catalog", "--catalog differs from this install's pin"),
        ("--lakebase-instance=another-instance", "--lakebase-instance differs from this install's pin"),
        ("--lakebase-database=another_db", "--lakebase-database differs from this install's pin"),
        ("--prune-legacy-defaults", "--prune-legacy-defaults is not a parameter the bundle passes"),
    ],
)
def test_an_overridden_lifecycle_run_exits_before_any_sql_or_connection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    recorder: _Recorder,
    capsys: pytest.CaptureFixture[str],
    override: str,
    line: str,
) -> None:
    _beside(monkeypatch, sync, _job_folder(tmp_path))
    name = override.split("=", 1)[0]
    argv = [arg for arg in SYNC_ARGS if not arg.startswith(f"{name}=")] + [override]

    with pytest.raises(SystemExit) as exited:
        _run(sync, argv)

    assert exited.value.code == pins_module.EXIT_REFUSED
    assert recorder.calls == [], "no Spark session, Lakebase credential or connection"
    err = capsys.readouterr().err
    assert f"[install-pins] sync_lifecycle_state REFUSED: {line}" in err
    assert override.split("=", 1)[-1] not in err.replace(name, ""), "refusals never echo a value"


def test_the_bundles_own_lifecycle_parameters_pass_the_pins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, recorder: _Recorder
) -> None:
    _beside(monkeypatch, sync, _job_folder(tmp_path))
    reached: list[list[str] | None] = []
    monkeypatch.setattr(sync, "main", lambda argv=None: reached.append(argv))

    _run(sync, SYNC_ARGS)

    assert reached == [SYNC_ARGS]


@pytest.mark.parametrize(
    ("argv", "line"),
    [
        (["--mode=fred", "--table=other_catalog.silver.market_rates_weekly"], "--table differs"),
        (["--mode=seed", "--table=mip.silver.another_table"], "--table differs"),
        ([*FRED_ARGS, "--seed-path=/Workspace/Users/someone/rates.csv"], "--seed-path is not a parameter"),
        ([*FRED_ARGS, "--staleness-days=9999"], "--staleness-days is not a parameter"),
    ],
)
def test_an_overridden_fred_run_exits_before_any_spark_or_fred_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    recorder: _Recorder,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    line: str,
) -> None:
    _beside(monkeypatch, fred, _job_folder(tmp_path))

    with pytest.raises(SystemExit) as exited:
        fred.run(argv)

    assert exited.value.code == pins_module.EXIT_REFUSED
    assert recorder.calls == []
    assert line in capsys.readouterr().err


@pytest.mark.parametrize("mode", ["seed", "fred"])
def test_the_bundles_own_fred_parameters_pass_the_pins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, recorder: _Recorder, mode: str
) -> None:
    _beside(monkeypatch, fred, _job_folder(tmp_path))
    assert fred.run([f"--mode={mode}", "--table=mip.silver.market_rates_weekly"]) == 0
    assert recorder.calls == ["fred.main"]


@pytest.mark.parametrize("module", [sync, fred], ids=["lifecycle", "fred"])
def test_a_missing_pins_file_refuses_before_any_sql(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, recorder: _Recorder, module: types.ModuleType
) -> None:
    _beside(monkeypatch, module, _job_folder(tmp_path, pins=None))
    with pytest.raises(SystemExit) as exited:
        _run(module, SYNC_ARGS if module is sync else FRED_ARGS)
    assert exited.value.code == pins_module.EXIT_REFUSED
    assert recorder.calls == []


KPI_ARGS = ["--catalog=mip", "--lakebase-instance=mip-app-state", "--lakebase-database=mip_app_state"]


@pytest.fixture
def kpi_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every way the KPI snapshot could reach Spark or Lakebase, recorded instead of done."""

    calls: list[str] = []
    monkeypatch.setattr(kpi, "_get_spark", lambda: calls.append("spark"))
    monkeypatch.setattr(kpi, "_read_headline_aggregates", lambda catalog: calls.append("spark.read") or {})
    monkeypatch.setattr(kpi, "_resolve_connection", lambda **k: calls.append("lakebase.credential"))
    monkeypatch.setattr(kpi, "_upsert_snapshot", lambda *a: calls.append("lakebase.upsert"))
    return calls


@pytest.mark.parametrize(
    "override",
    ["--catalog=other_catalog", "--lakebase-instance=another-instance", "--lakebase-database=another_db"],
)
def test_an_overridden_kpi_snapshot_run_exits_before_any_spark_or_lakebase_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kpi_calls: list[str],
    capsys: pytest.CaptureFixture[str],
    override: str,
) -> None:
    monkeypatch.setattr(kpi, "__file__", str(_job_folder(tmp_path) / "kpi_snapshot.py"))
    name = override.split("=", 1)[0]
    argv = [arg for arg in KPI_ARGS if not arg.startswith(f"{name}=")] + [override]

    with pytest.raises(SystemExit) as exited:
        kpi.run(argv)

    assert exited.value.code == pins_module.EXIT_REFUSED
    assert kpi_calls == []
    assert f"[install-pins] kpi_snapshot REFUSED: {name} differs from this install's pin" in capsys.readouterr().err


def test_the_bundles_own_kpi_snapshot_parameters_pass_the_pins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kpi_calls: list[str]
) -> None:
    monkeypatch.setattr(kpi, "__file__", str(_job_folder(tmp_path) / "kpi_snapshot.py"))
    bundle_args = BUNDLE["resources"]["jobs"]["mip_kpi_snapshot"]["tasks"][0]["spark_python_task"]["parameters"]
    assert bundle_args == [
        "--catalog=${var.uc_catalog}",
        "--lakebase-instance=${var.lakebase_instance_name}",
        "--lakebase-database=${var.lakebase_database_name}",
    ]
    kpi.run(KPI_ARGS)
    assert kpi_calls == ["spark.read", "lakebase.credential", "lakebase.upsert"]


# ----------------------------------------------------------- the module loader


LOADERS = {
    "jobs/sync_lifecycle_state_job.py": "_beside",
    "jobs/fred_rates_ingest.py": "_install_pins",
    "jobs/kpi_snapshot.py": "_install_pins",
}


def _exec_loader(relpath: str, cwd: Path) -> Any:
    """The entrypoint's loader exec'd the way Databricks runs a script: no ``__file__``."""

    tree = ast.parse((ROOT / relpath).read_text(encoding="utf-8"))
    name = LOADERS[relpath]
    loader = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    namespace: dict[str, Any] = {"Path": Path, "importlib": __import__("importlib.util"), "sys": sys}
    namespace["ModuleType"] = types.ModuleType
    exec(compile(ast.Module(body=[loader], type_ignores=[]), "<job>", "exec"), namespace)  # noqa: S102
    previous = Path.cwd()
    os.chdir(cwd)
    try:
        call = namespace[name]
        return call("_install_pins.py", "mip_install_pins") if name == "_beside" else call()
    finally:
        os.chdir(previous)


@pytest.mark.parametrize("relpath", sorted(LOADERS))
def test_the_loader_finds_the_module_beside_the_script_without_file(tmp_path: Path, relpath: str) -> None:
    folder = _job_folder(tmp_path)
    (folder / Path(relpath).name).write_text("# the job script\n", encoding="utf-8")
    for cwd in (folder, folder.parent):
        loaded = _exec_loader(relpath, cwd)
        assert Path(loaded.__file__) == folder / "_install_pins.py"
        assert loaded.load() == pins_module.InstallPins("mip", "mip-app-state", "mip_app_state", "mip-data-owners")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with pytest.raises(SystemExit) as exited:
        _exec_loader(relpath, elsewhere)
    assert exited.value.code == pins_module.EXIT_REFUSED


def test_the_lifecycle_entrypoint_loads_the_sync_beside_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    folder = _job_folder(tmp_path)
    monkeypatch.setattr(sync_job, "__file__", str(folder / "sync_lifecycle_state_job.py"))
    loaded = sync_job._beside("sync_lifecycle_state.py", "mip_sync_lifecycle_state_job")
    assert Path(loaded.__file__) == folder / "sync_lifecycle_state.py"
    assert loaded.build_parser().prog == "sync_lifecycle_state"


def test_the_app_imported_sync_module_carries_no_job_gate() -> None:
    """The App runs jobs/sync_lifecycle_state.py; the gate is only in the job's own entrypoint."""

    text = (ROOT / "jobs" / "sync_lifecycle_state.py").read_text(encoding="utf-8")
    assert "_install_pins" not in text and "importlib" not in text


def _jobs_identity_python_files() -> set[str]:
    """Every Python file a job running as the jobs identity runs (the App-triggerable four included)."""

    bound = {
        resource["job"]["id"].split(".")[2]
        for resource in BUNDLE["resources"]["apps"]["mip_app"]["resources"]
        if "job" in resource and resource["job"]["permission"] == "CAN_MANAGE_RUN"
    }
    on_jobs = {
        key
        for key, job in BUNDLE["resources"]["jobs"].items()
        if job["run_as"] == {"service_principal_name": "${var.jobs_principal}"}
    }
    assert bound <= on_jobs, "every App-triggerable job runs as the jobs identity"
    return {
        task["spark_python_task"]["python_file"]
        for key in on_jobs
        for task in BUNDLE["resources"]["jobs"][key]["tasks"]
        if "spark_python_task" in task
    }


def test_every_jobs_identity_python_entrypoint_runs_through_the_pins() -> None:
    files = _jobs_identity_python_files()
    assert {
        "jobs/sync_lifecycle_state_job.py",
        "jobs/fred_rates_ingest.py",
        "jobs/converge_governed_owner.py",
        "jobs/kpi_snapshot.py",
    } <= files
    for relpath in sorted(files):
        tree = ast.parse((ROOT / relpath).read_text(encoding="utf-8"))
        guards = [
            node
            for node in tree.body
            if isinstance(node, ast.If) and "__main__" in ast.unparse(node.test)
        ]
        assert len(guards) == 1, relpath
        called = {
            node.func.id
            for node in ast.walk(guards[0])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "run" in called and "main" not in called, f"{relpath}: __main__ must call run()"
        run = ast.unparse(next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run"))
        assert ".enforce(" in run and "main(" in run, relpath
        assert run.index(".enforce(") < run.index("main("), f"{relpath}: the pins come before main"


# ------------------------------------------------------ the render and the sync


def test_bundle_env_renders_the_profiles_pins_before_the_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in [name for name in os.environ if name.startswith(_AMBIENT_PREFIXES)]:
        monkeypatch.delenv(name)
    env_local = tmp_path / ".env.local"
    env_local.write_text(env_file_text(paychex_shaped_profile()), encoding="utf-8")
    root = tmp_path / "bundle-root"
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    monkeypatch.setattr(bundle_env, "BUNDLE_ROOT", root)
    monkeypatch.setattr(bundle_env.render_sql, "render", lambda **_kwargs: (1, 0, 0))
    seen: list[dict[str, Any]] = []

    def fake_cli(_command: list[str], *, env: dict[str, str], check: bool) -> Any:
        seen.append(json.loads((root / "jobs" / "install_pins.json").read_text(encoding="utf-8")))
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(bundle_env.subprocess, "run", fake_cli)
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", "validate", "-t", "client"])
    assert bundle_env.main() == 0
    assert seen == [PINS]


def test_the_pins_file_is_gitignored_and_synced() -> None:
    assert "jobs/install_pins.json" in BUNDLE["sync"]["include"]
    ignored = subprocess.run(
        ["git", "check-ignore", "--quiet", "jobs/install_pins.json"], cwd=ROOT, check=False
    )
    assert ignored.returncode == 0
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "jobs/install_pins.json"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    assert tracked.returncode != 0, "never committed"
