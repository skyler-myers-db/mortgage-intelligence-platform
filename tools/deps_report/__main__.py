"""CLI: ``python -m tools.deps_report --json OUT --markdown OUT [--offline-fixtures DIR] [--today D]``.

Exit 0 green, 1 a gate signal (unignored npm high/critical, any unignored
pip-audit finding, a past ci.yml review date, an allowlist entry expired or
within 7 days, a passed hold review_by, an unheld literal mismatch), 2 when a
source could not be read (named under 'Report error'). Both outputs are
written in every case, so the publish job always has a body to upsert. The
live section never contributes. ``--offline-fixtures`` replaces every network
read and every repository input with the files in DIR (the hermetic dry run
pinned by tests/unit/test_deps_report.py).
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import TypeVar

from tools.deps_report import (
    REPO_ROOT,
    Signal,
    SourceError,
    advisories,
    live,
    load_holds,
    npm,
    pins,
    python,
    render,
    waivers,
)

T = TypeVar("T")


@dataclass(frozen=True)
class Sources:
    package_json: Path
    requirements_in: Path
    uv_lock: Path
    ci_yml: Path
    workflows: Path
    allowlist: Path
    holds: Path
    npm_view: npm.NpmView
    pypi: python.PyPI
    npm_audit: advisories.Runner
    pip_audit: advisories.Runner
    releases: pins.Releases
    live: Callable[[date], dict[str, object]]


def live_sources() -> Sources:
    workflows = REPO_ROOT / ".github" / "workflows"
    return Sources(
        npm.PACKAGE_JSON, python.REQUIREMENTS_IN, python.UV_LOCK, workflows / "ci.yml", workflows,
        waivers.ALLOWLIST, REPO_ROOT / "tools" / "dependency_holds.json",
        npm.npm_view, python.pypi_json, advisories.run_npm_audit, advisories.run_pip_audit,
        pins.github_release, lambda today: live.live_section(),
    )


def offline_sources(directory: Path) -> Sources:
    def table(name: str) -> Callable[[str], dict[str, object]]:
        data = json.loads((directory / name).read_text(encoding="utf-8"))

        def read(key: str) -> dict[str, object]:
            if key not in data:
                raise SourceError(name.removesuffix(".json"))
            return data[key]

        return read

    def document(name: str) -> Callable[[], object]:
        return lambda: json.loads((directory / name).read_text(encoding="utf-8"))

    def live_fixture(today: date) -> dict[str, object]:
        runs = json.loads((directory / "live_runs.json").read_text(encoding="utf-8"))
        now = datetime.combine(today, time(12), tzinfo=UTC)
        return live.live_section(release_sha=runs["release_sha"], now=now, fetch=lambda _path: runs)

    return Sources(
        directory / "frontend_package.json", directory / "requirements_in.txt", directory / "uv_lock.txt",
        directory / "synthetic_ci.yml", directory, directory / "synthetic_allowlist.json", directory / "holds.json",
        table("npm_view.json"), table("pypi.json"), document("npm_audit.json"), document("pip_audit.json"),
        table("gh_releases.json"), live_fixture,
    )


def build(sources: Sources, today: date) -> dict[str, object]:
    errors: list[str] = []

    def guarded(read: Callable[[], T], default: T) -> T:
        try:
            return read()
        except SourceError as exc:
            errors.append(exc.source)
            return default

    holds = guarded(lambda: load_holds(sources.holds), [])
    npm_rows = guarded(lambda: npm.package_rows(sources.npm_view, holds, npm.exact_pins(sources.package_json)), [])
    pypi_rows = guarded(lambda: python.package_rows(sources.pypi, holds, sources.requirements_in, sources.uv_lock), [])
    npm_adv = guarded(lambda: advisories.npm_findings(sources.npm_audit()), [])
    pip_adv = guarded(lambda: advisories.pip_findings(sources.pip_audit(), advisories.ci_ignored_vulns(sources.ci_yml)), [])
    literals = pins.collect(sources.workflows)
    pin_rows = guarded(lambda: pins.pin_rows(literals, sources.releases), [])
    if any(row["status"] == "unavailable" for row in pin_rows):
        errors.append("github releases")
    empty: tuple[list[Signal], list[Signal], list[dict[str, str]]] = ([], [], [])
    gate_waivers, warnings, waiver_rows = guarded(
        lambda: waivers.waiver_signals(today, holds, sources.ci_yml, sources.allowlist), empty
    )
    gate = advisories.gate_signals(npm_adv, pip_adv) + pins.mismatch_signals(literals, holds, today) + gate_waivers
    live_data = sources.live(today)
    return {
        "today": today.isoformat(),
        "release_sha": str(live_data.get("release_sha", "")),
        "exit_code": 2 if errors else (1 if gate else 0),
        "errors": errors,
        "gate": gate,
        "warnings": warnings,
        "npm_advisories": npm_adv,
        "pip_advisories": pip_adv,
        "waivers": waiver_rows,
        "npm": npm_rows,
        "pypi": pypi_rows,
        "pins": pin_rows,
        "live": live_data,
    }


def main(argv: list[str] | None = None, env: dict[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--json", dest="json_out", required=True)
    parser.add_argument("--markdown", dest="markdown_out", required=True)
    parser.add_argument("--offline-fixtures", type=Path, default=None)
    parser.add_argument("--today", type=date.fromisoformat, default=None)
    args = parser.parse_args(argv)
    today = args.today or datetime.now(UTC).date()
    offline = args.offline_fixtures is not None
    report = build(offline_sources(args.offline_fixtures) if offline else live_sources(), today)
    run = None if offline else render.run_url(dict(os.environ if env is None else env))
    markdown = render.render(report, run)
    for path, text in (
        (Path(args.json_out), json.dumps(report, indent=2, sort_keys=True, default=asdict) + "\n"),
        (Path(args.markdown_out), markdown),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    exit_code = int(str(report["exit_code"]))
    print(f"deps-report: exit {exit_code} (written {args.json_out} and {args.markdown_out})")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
