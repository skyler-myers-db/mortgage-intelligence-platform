"""A small git repo and a governed dist for ``app_runtime_closure stage`` tests.

The repo tracks every closure entry (TRACKED) plus files outside it
(OUTSIDE); the dist is a governed build of it: the shell, a hashed asset with
.br and .gz variants that decode to it, and frontend/public copied verbatim.
Used by tests/unit/test_app_runtime_closure_stage.py (what a stage writes),
test_app_runtime_closure_stage_dist.py (dist refusals) and
test_app_runtime_closure_stage_git.py (git-side refusals).
"""

from __future__ import annotations

import gzip
import subprocess
from pathlib import Path

from tools import requirements_digest as digest

ROOT = Path(__file__).resolve().parents[2]
INPUTS = {"requirements.in": "fastapi\n", "uv.lock": "fastapi==1.0\n"}
INCLUDES = "-c uv.lock\n-r requirements.in\n"
STAMP = digest.digest_line_for({name: text.encode() for name, text in INPUTS.items()})
TRACKED = {
    "app.yaml": "command: ['python', '-m', 'backend.runtime']\n",
    "requirements.txt": f"{STAMP}\n{INCLUDES}",
    **INPUTS,
    "backend/__init__.py": "",
    "backend/services/reader.py": "X = 1\n",
    "jobs/sync_lifecycle_state.py": "Y = 2\n",
    "lakebase/schema.sql": "select 1;\n",
    "frontend/public/us-counties.json": "{}\n",
    "genie/sample_questions.md": "- a question\n",
    "sql/metric_views/view.sql": "-- view\n",
    "sql/uc_functions/fn_a.sql": "-- fn\n",
    "tests/eval/golden_agent_cases.jsonl": "{}\n",
    "tests/eval/scorers.py": "",
    "sql/transformations/gold_funnel_snapshot_daily.sql": "-- funnel\n",
}
OUTSIDE = {
    "docs/guide.md": "x\n",
    "frontend/src/main.tsx": "x\n",
    "frontend/public/other.json": "{}\n",
    "genie/other.md": "x\n",
    "sql/transformations/other.sql": "x\n",
    "tests/unit/test_x.py": "x\n",
    "tools/x.py": "x\n",
}
APP_JS = "app();\n" * 200
BROTLI_MAGIC = "fake-br:"
# A governed build: the shell, hashed assets with compressed variants that
# decode to them, and frontend/public copied verbatim.
DIST = {
    "index.html": "<html></html>\n",
    "assets/app-DnQ3_k-1.js": APP_JS,
    "assets/app-DnQ3_k-1.js.br": BROTLI_MAGIC + APP_JS,
    "us-counties.json": TRACKED["frontend/public/us-counties.json"],
    "other.json": OUTSIDE["frontend/public/other.json"],
}
GZ_NAME = "assets/app-DnQ3_k-1.js.gz"


def fake_brotli(data: bytes) -> bytes:
    """A stand-in decoder: tests never need the optional brotli module."""

    if not data.startswith(BROTLI_MAGIC.encode()):
        raise ValueError("not a fake brotli stream")
    return data.removeprefix(BROTLI_MAGIC.encode())


def git(repo: Path, *args: str) -> str:
    identity = ["-c", "user.email=closure@example.invalid", "-c", "user.name=closure-test",
                "-c", "commit.gpgsign=false"]
    result = subprocess.run(["git", "-C", str(repo), *identity, *args],
                            capture_output=True, text=True, check=True)
    return result.stdout.strip()


def write_files(base: Path, files: dict[str, str]) -> None:
    for relpath, text in files.items():
        path = base / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def files_under(base: Path) -> set[str]:
    return {p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file()}


def make_repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q")
    write_files(path, {**TRACKED, **OUTSIDE})
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "fixture")
    return path


def make_dist(tmp_path: Path) -> Path:
    path = tmp_path / "dist"
    write_files(path, DIST)
    (path / GZ_NAME).write_bytes(gzip.compress(APP_JS.encode(), mtime=0))
    return path


def dist_names() -> set[str]:
    return {f"frontend/dist/{name}" for name in (*DIST, GZ_NAME)}


def nothing_written(tmp_path: Path, out: Path) -> bool:
    staging = [p.name for p in tmp_path.iterdir() if p.name.startswith(f".{out.name}.staging-")]
    return not out.exists() and staging == []
