"""Run the Lakeflow pipeline source with stand-in ``dlt`` and ``pyspark`` modules.

``pipelines/lakeflow/mip_feature_pipeline.py`` imports only inside a Lakeflow
runtime, where ``dlt`` and ``pyspark`` exist. ``exec_pipeline`` executes the
module text with stand-ins: ``dlt.table`` records its keyword arguments, every
other ``dlt`` decorator is a no-op, and the active Spark session carries the
given pipeline configuration. Table bodies never run, so nothing reads data.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

REPO = Path(__file__).resolve().parents[2]
PIPELINE = REPO / "pipelines" / "lakeflow" / "mip_feature_pipeline.py"
_MISSING = object()


class _Conf:
    """``spark.conf``: ``get(key)`` raises when unset, ``get(key, default)`` does not."""

    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = dict(values)
        self.reads: list[str] = []

    def get(self, key: str, default: Any = _MISSING) -> Any:
        self.reads.append(key)
        if key in self._values:
            return self._values[key]
        if default is _MISSING:
            raise KeyError(key)
        return default


@dataclass
class PipelineRun:
    namespace: dict[str, Any]
    tables: list[dict[str, Any]] = field(default_factory=list)
    conf_reads: list[str] = field(default_factory=list)

    def table(self, name: str) -> dict[str, Any]:
        return next(kwargs for kwargs in self.tables if kwargs.get("name") == name)


def _decorator(*_args: Any, **_kwargs: Any) -> Any:
    return lambda function: function


@contextmanager
def _stub_modules(run: PipelineRun, conf: _Conf) -> Iterator[None]:
    def table(**kwargs: Any) -> Any:
        run.tables.append(kwargs)
        return lambda function: function

    dlt = ModuleType("dlt")
    dlt.table = table  # type: ignore[attr-defined]
    dlt.__getattr__ = lambda _name: _decorator  # type: ignore[attr-defined]
    session = type("Session", (), {"conf": conf})()
    spark_session = type(
        "SparkSession", (), {"getActiveSession": staticmethod(lambda: session), "builder": None}
    )
    pyspark = ModuleType("pyspark")
    sql = ModuleType("pyspark.sql")
    functions = ModuleType("pyspark.sql.functions")
    window = ModuleType("pyspark.sql.window")
    sql.DataFrame = object  # type: ignore[attr-defined]
    sql.SparkSession = spark_session  # type: ignore[attr-defined]
    sql.functions = functions  # type: ignore[attr-defined]
    sql.window = window  # type: ignore[attr-defined]
    window.Window = object  # type: ignore[attr-defined]
    pyspark.sql = sql  # type: ignore[attr-defined]
    stubs = {
        "dlt": dlt,
        "pyspark": pyspark,
        "pyspark.sql": sql,
        "pyspark.sql.functions": functions,
        "pyspark.sql.window": window,
    }
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        yield
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def exec_pipeline(conf: Mapping[str, str], *, path: Path = PIPELINE) -> PipelineRun:
    """Execute the pipeline module under ``conf``; its exceptions propagate."""

    run = PipelineRun(namespace={"__name__": "mip_feature_pipeline", "__file__": str(path)})
    spark_conf = _Conf(conf)
    with _stub_modules(run, spark_conf):
        try:
            exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), run.namespace)
        finally:
            run.conf_reads = list(spark_conf.reads)
    return run
