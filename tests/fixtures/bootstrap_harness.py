"""Drive ``python -m tools.clients.bootstrap_account`` against a ``FakeEstate``.

``run_tool`` calls ``main`` with every outside dependency replaced: the
session yields the estate's fake clients, the sink is a ``FakeSink``, the
probe answers yes, stdin is a terminal, the environment is empty and output
is captured. Inputs come from an env file holding the synthetic ``acme``
profile subset (``write_env``).
"""

from __future__ import annotations

import io
import json
import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from tests.fixtures.fake_databricks_account import (
    ACCOUNT_ID,
    METASTORE_ID,
    REPOSITORY,
    SOURCE_CATALOG,
    SOURCE_SCHEMA,
    WORKSPACE_HOST,
    WORKSPACE_ID,
    FakeEstate,
)
from tools.clients.bootstrap_account import Dependencies, main

ACME_ENV = {
    "MIP_CLIENT": "acme",
    "MIP_CLOUD": "aws",
    "MIP_IDENTITY_PREFIX": "acme",
    "MIP_APP_NAME": "acme-app",
    "MIP_COTALITY_SOURCE_CATALOG": SOURCE_CATALOG,
    "MIP_COTALITY_SOURCE_SCHEMA": SOURCE_SCHEMA,
    "DATABRICKS_HOST": WORKSPACE_HOST,
    "MIP_EXPECT_WORKSPACE_ID": str(WORKSPACE_ID),
    "MIP_EXPECT_METASTORE_ID": METASTORE_ID,
    "DATABRICKS_ACCOUNT_ID": ACCOUNT_ID,
}
CORE_ROLES = ("deployer", "jobs", "validator", "normal", "operator2", "admin", "release_probe", "account_principal")
RESERVED_CORE = ("normal", "operator2", "admin", "release_probe")
_DIGEST_LINE = re.compile(r"^plan sha256: ([0-9a-f]{64})$", re.M)


class TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


@dataclass
class FakeSink:
    pins: dict[str, str] | None = None
    pin_writes: int = 0
    secrets: dict[str, tuple[str, SecretStr]] = field(default_factory=dict)
    fail_put: Exception | None = None
    confirm: bool = True

    def ready(self) -> None:
        return None

    def put_identity_ids(self, pins: Mapping[str, str]) -> None:
        self.pins = dict(pins)
        self.pin_writes += 1

    def put_secret(self, field_name: str, application_id: str, secret: SecretStr) -> None:
        if self.fail_put is not None:
            raise self.fail_put
        self.secrets[field_name] = (application_id, secret)

    def confirm_secret(self, field_name: str) -> bool:
        return self.confirm and field_name in self.secrets


@dataclass
class Result:
    code: int
    out: str
    err: str

    @property
    def digest(self) -> str:
        match = _DIGEST_LINE.search(self.out)
        assert match, self.out + self.err
        return match.group(1)

    def steps(self) -> list[str]:
        return [line.split(". ", 1)[1] for line in self.out.splitlines() if re.match(r"^\s+\d+\. ", line)]

    def document(self) -> dict[str, Any]:
        line = next(line for line in self.out.splitlines() if line.startswith("canonical plan: "))
        return json.loads(line.removeprefix("canonical plan: "))


def write_env(path: Path, *, pins: Mapping[str, str] | None = None, **overrides: str | None) -> Path:
    values: dict[str, str | None] = {**ACME_ENV, **overrides}
    if pins:
        values["MIP_IDENTITY_IDS_JSON"] = json.dumps(dict(pins))
    path.write_text(
        "".join(f"{name}='{value}'\n" for name, value in values.items() if value is not None), encoding="utf-8"
    )
    return path


def estate_session(estate: FakeEstate) -> Callable[[Any], Any]:
    @contextmanager
    def open_session(inputs: Any) -> Iterator[Any]:
        yield estate.clients()

    return open_session


def run_tool(
    estate: FakeEstate,
    *argv: str,
    env_file: Path,
    sink: FakeSink | None = None,
    environ: Mapping[str, str] | None = None,
    stdin: Any = None,
    probe: Callable[[str, SecretStr], bool] | None = None,
    session: Callable[[Any], Any] | None = None,
    deps_sink: Any = None,
) -> Result:
    out, err = io.StringIO(), io.StringIO()
    answer = probe or (lambda application_id, secret: True)
    deps = Dependencies(
        open_session=session or estate_session(estate),
        sink=deps_sink if deps_sink is not None else (sink if sink is not None else FakeSink()),
        probe_for=lambda inputs: answer,
        environ=dict(environ or {}),
        stdin=stdin if stdin is not None else TTY(),
        stdout=out,
        stderr=err,
        infer_repository=lambda: REPOSITORY,
        sleep=lambda seconds: None,
    )
    code = main([*argv, "--env-file", str(env_file), "--env-file-only"], deps)
    return Result(code, out.getvalue(), err.getvalue())


def plan_and_apply(estate: FakeEstate, env_file: Path, sink: FakeSink, *extra: str) -> tuple[Result, Result]:
    planned = run_tool(estate, "--plan", *extra, env_file=env_file, sink=sink)
    assert planned.code == 0, planned.err
    applied = run_tool(estate, "--apply", "--ack-plan", planned.digest, *extra, env_file=env_file, sink=sink)
    return planned, applied
