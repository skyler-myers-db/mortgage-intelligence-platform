"""Drive ``python -m tools.clients.github_environment`` against the fake gh.

``run_env_tool`` calls ``main`` with a real ``fresh_gh_session`` whose child
processes find the fake ``gh`` first on PATH (tests/fixtures/fake_gh.py),
under an operator environment that holds ambient GitHub tokens to scrub.
Session directories are made under ``<root>/sessions`` so a test can prove
each one is gone afterwards. Inputs come from a dotenv file holding the
synthetic demo profile (tests/fixtures/client_profiles.py).
"""

from __future__ import annotations

import io
import tempfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any

from tests.fixtures.bootstrap_harness import TTY, Result
from tests.fixtures.fake_gh import REPOSITORY, FakeGitHub
from tools.clients.github_environment import Dependencies, ed25519_signing_key, main
from tools.clients.github_session import GhSession, fresh_gh_session


def write_profile(path: Path, values: Mapping[str, str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    for name, value in values.items():
        assert "'" not in value, name
    path.write_text("".join(f"{name}='{value}'\n" for name, value in values.items()), encoding="utf-8")
    return path


def session_opener(fake: FakeGitHub, sessions: Path) -> Callable[[str], AbstractContextManager[GhSession]]:
    sessions.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def open_session(repository: str) -> Iterator[GhSession]:
        with fresh_gh_session(
            repository, environ=fake.environ(), make_root=lambda: Path(tempfile.mkdtemp(dir=sessions))
        ) as session:
            yield session

    return open_session


def run_env_tool(
    fake: FakeGitHub,
    *argv: str,
    env_file: Path,
    environ: Mapping[str, str] | None = None,
    stdin: Any = None,
    open_session: Callable[[str], AbstractContextManager[GhSession]] | None = None,
    generate_key: Callable[[], bytes] = ed25519_signing_key,
) -> Result:
    out, err = io.StringIO(), io.StringIO()
    deps = Dependencies(
        open_session=open_session or session_opener(fake, fake.root / "sessions"),
        environ=dict(environ or {}),
        stdin=stdin if stdin is not None else TTY(),
        stdout=out,
        stderr=err,
        infer_repository=lambda: REPOSITORY,
        generate_key=generate_key,
    )
    code = main([*argv, "--env-file", str(env_file), "--env-file-only"], deps)
    return Result(code, out.getvalue(), err.getvalue())


def plan_and_apply(fake: FakeGitHub, env_file: Path, *extra: str, **kwargs: Any) -> tuple[Result, Result]:
    planned = run_env_tool(fake, "--plan", *extra, env_file=env_file, **kwargs)
    assert planned.code == 0, planned.err
    applied = run_env_tool(fake, "--apply", "--ack-plan", planned.digest, *extra, env_file=env_file, **kwargs)
    return planned, applied
