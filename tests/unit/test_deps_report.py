"""The weekly dependency and release-signal report (D-platform-process-a, W5e).

Offline only: every registry, advisory and GitHub read comes from
tests/fixtures/deps_report/ or an in-test fake, and every date is an
injected ``today``, so nothing here depends on the network or the wall clock.
The REAL ci.yml, file-size allowlist and holds file are read where the
contract is about them (the pip-audit ignores, the gitleaks literal, the
review dates); the SYNTHETIC fixtures carry the boundary cases.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from tools import live_validation_gate as gate
from tools.deps_report import (
    HOLDS_FILE,
    REPO_ROOT,
    Signal,
    SourceError,
    advisories,
    classify_gap,
    drift_row,
    live,
    load_holds,
    pins,
    python,
    render,
    waivers,
)
from tools.deps_report import __main__ as cli

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "deps_report"
REAL_CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
REAL_WORKFLOWS = REPO_ROOT / ".github" / "workflows"
TODAY = date(2026, 10, 14)


def _offline(**changes: object) -> cli.Sources:
    return dataclasses.replace(cli.offline_sources(FIXTURES), **changes)


def _fixture_holds() -> list[dict[str, object]]:
    return load_holds(FIXTURES / "holds.json")


# --------------------------------------------------------------- classification


@pytest.mark.parametrize(
    ("pinned", "latest", "gap"),
    [
        ("1.2.3", "1.2.3", "current"),
        ("1.2.3", "1.2.4", "patch"),
        ("1.2.3", "1.3.0", "minor"),
        ("6.0.3", "7.0.2", "major"),
        ("0.2.4", "0.2.5", "patch"),
        ("0.2.4", "0.3.0", "major"),  # a 0.y.z pin: a change of y is major
        ("0.136.1", "0.143.0", "major"),
        ("2.0.0", "1.9.0", "current"),
        ("v1.7.0", "v1.8.0", "minor"),
        ("main", "1.0.0", "unknown"),
    ],
)
def test_gaps_are_classified_with_the_zero_major_rule(pinned: str, latest: str, gap: str) -> None:
    assert classify_gap(pinned, latest) == gap


def test_a_held_package_renders_held_and_never_pending() -> None:
    hold = {"ecosystem": "npm", "package": "vitest", "below": "5.0.0", "reason": "r", "review_by": "2026-11-15"}

    assert drift_row("vitest", "4.1.11", "5.0.4", "2026-10-12", hold)["status"] == "held"
    assert drift_row("vitest", "4.1.11", "5.0.4", "2026-10-12", None)["status"] == "pending"
    assert drift_row("vitest", "4.1.11", "4.1.11", "", None)["status"] == "current"


def test_range_pinned_direct_requirements_drift_at_their_locked_versions(tmp_path: Path) -> None:
    requirements, lock = tmp_path / "requirements.in", tmp_path / "uv.lock"
    requirements.write_text("databricks-sdk==0.103.0\npsycopg[binary]>=3.2.10,<3.4\nGitPython>=3.1.59,<4  # floor\nunresolved>=1\n# comment>=1\n")
    lock.write_text("databricks-sdk==0.103.0\ngitpython==3.1.60\noauthlib==3.3.1\npsycopg==3.3.2\n")
    fetched: list[str] = []

    def fetch(name: str) -> dict[str, object]:
        fetched.append(name)
        return {"info": {"version": "9.9.9"}, "urls": []}

    rows = python.package_rows(fetch, [], requirements, lock)

    assert [(row["package"], row["pinned"]) for row in rows] == [
        ("databricks-sdk", "0.103.0"), ("gitpython", "3.1.60"), ("psycopg", "3.3.2"),
    ], "a transitive lock entry (oauthlib) and an unresolved range are never listed"
    assert fetched == ["databricks-sdk", "gitpython", "psycopg"]
    real = python.ranged_names(python.REQUIREMENTS_IN) - set(python.pins(python.REQUIREMENTS_IN))
    assert {"gitpython", "pyasn1", "aiohttp", "pyarrow", "sqlparse", "thrift", "psycopg"} <= real


def test_the_holds_file_is_schema_checked(tmp_path: Path) -> None:
    assert load_holds(HOLDS_FILE), "the real holds file parses"
    bad = tmp_path / "holds.json"
    bad.write_text(json.dumps({"holds": [{"ecosystem": "npm", "package": "x", "below": "1.0.0", "reason": "r", "review_by": "2026-11-15", "extra": 1}]}))
    with pytest.raises(SourceError):
        load_holds(bad)
    bad.write_text(json.dumps({"holds": [{"ecosystem": "npm", "package": "x", "below": "1.0.0", "reason": "r", "review_by": "soon"}]}))
    with pytest.raises(SourceError):
        load_holds(bad)


# ------------------------------------------------------------------- advisories


def test_the_ignore_vulns_are_parsed_from_the_real_ci_yml() -> None:
    assert advisories.ci_ignored_vulns(REAL_CI) == [
        "GHSA-h7x2-h6g9-p789",
        "GHSA-xpv3-w29h-x7cv",
        "GHSA-hj66-6f7g-4r5v",
    ]


def test_pip_findings_match_ignores_by_id_or_alias_and_unignored_is_the_gate() -> None:
    payload = json.loads((FIXTURES / "pip_audit.json").read_text())
    ignored = advisories.pip_findings(payload, ["GHSA-xpv3-w29h-x7cv"])
    unignored = advisories.pip_findings(payload, [])

    assert [f["ignored"] for f in ignored] == ["yes"]
    assert advisories.gate_signals([], ignored) == []
    assert advisories.gate_signals([], unignored) == [Signal("pip_advisory", "oauthlib", "PYSEC-2026-4114")]


def test_npm_high_and_critical_gate_while_moderate_informs() -> None:
    payload = json.loads((FIXTURES / "npm_audit.json").read_text())
    moderate = advisories.npm_findings(payload)
    assert moderate == [{"package": "example-parser", "id": "GHSA-2234-5678-9cfg", "severity": "moderate"}]
    assert advisories.gate_signals(moderate, []) == []

    payload["vulnerabilities"]["example-parser"]["via"][0]["severity"] = "high"
    assert advisories.gate_signals(advisories.npm_findings(payload), []) == [
        Signal("npm_advisory", "example-parser", "GHSA-2234-5678-9cfg")
    ]


# ---------------------------------------------------------------------- waivers


def test_the_allowlist_window_is_seven_days_red_eight_green() -> None:
    synthetic = FIXTURES / "synthetic_allowlist.json"

    def allowlist_gate(today: date) -> list[Signal]:
        gate_signals, _warnings, _rows = waivers.waiver_signals(today, [], FIXTURES / "synthetic_ci.yml", synthetic)
        return [s for s in gate_signals if s.code == "allowlist_expiring"]

    assert allowlist_gate(date(2026, 10, 14)) == [], "8 days left is green"
    assert allowlist_gate(date(2026, 10, 15)) == [
        Signal("allowlist_expiring", "backend/services/example_split_pending.py", "2026-10-22")
    ], "7 days left is red"


def test_the_real_empty_allowlist_parses_green() -> None:
    assert waivers.allowlist_expiries(waivers.ALLOWLIST) == {}
    gate_signals, _warnings, _rows = waivers.waiver_signals(TODAY, [], REAL_CI, waivers.ALLOWLIST)
    assert not [s for s in gate_signals if s.code == "allowlist_expiring"]


def test_the_real_ci_review_dates_are_red_after_their_dates_and_warn_within_a_week() -> None:
    # The W5e integration's security re-review (2026-10-15) re-checked both
    # ignores and moved their dates (mlflow 2026-11-15, oauthlib 2026-11-01).
    late, _warn, _rows = waivers.waiver_signals(date(2026, 11, 16), [], REAL_CI, waivers.ALLOWLIST)
    early, warnings, _rows = waivers.waiver_signals(date(2026, 10, 31), [], REAL_CI, waivers.ALLOWLIST)

    assert [(s.code, s.detail) for s in late] == [("review_date_past", "2026-11-15"), ("review_date_past", "2026-11-01")]
    assert all(s.subject.startswith(".github/workflows/ci.yml:") for s in late)
    assert early == []
    assert [(s.code, s.detail) for s in warnings] == [("review_date_soon", "2026-11-01")], "within 7 days warns"


def test_a_passed_hold_review_by_is_the_gate() -> None:
    holds = _fixture_holds()
    on_day, _w, _r = waivers.waiver_signals(date(2026, 11, 15), holds, FIXTURES / "synthetic_ci.yml", FIXTURES / "synthetic_allowlist.json")
    after, _w, _r = waivers.waiver_signals(date(2026, 11, 16), holds, FIXTURES / "synthetic_ci.yml", FIXTURES / "synthetic_allowlist.json")

    assert not [s for s in on_day if s.code == "hold_review_passed"]
    assert {s.subject for s in after if s.code == "hold_review_passed"} == {"@babel/core", "@tanstack/react-query", "databricks-sdk", "gitleaks"}


# ------------------------------------------------------------------------- pins


def test_the_real_ci_yml_has_one_gitleaks_literal_and_is_green() -> None:
    literals = pins.collect(REAL_WORKFLOWS)

    assert {item["literal"] for item in literals if item["tool"] == "gitleaks"} == {"8.30.1"}
    assert pins.mismatch_signals(literals, load_holds(), TODAY) == []


def test_the_pip_audit_pin_is_one_literal_across_every_workflow() -> None:
    literals = [item for item in pins.collect(REAL_WORKFLOWS) if item["tool"] == "pip-audit"]

    assert {item["literal"] for item in literals} == {"2.9.0"}
    assert {item["where"].split(":")[0] for item in literals} >= {".github/workflows/ci.yml", ".github/workflows/deps-report.yml"}


def test_a_synthetic_gitleaks_split_is_red_unless_an_unexpired_hold_names_it() -> None:
    literals = pins.collect(FIXTURES)
    holds = _fixture_holds()
    split = Signal("literal_mismatch", "gitleaks", "8.28.0 8.30.1")

    assert pins.mismatch_signals(literals, [], TODAY) == [split], "no hold: red"
    assert pins.mismatch_signals(literals, holds, TODAY) == [], "an unexpired tool-literal hold: green"
    assert pins.mismatch_signals(literals, holds, date(2026, 11, 16)) == [split], "past its review_by: red again"


def test_a_floating_major_tag_tracks_its_major_and_a_digest_is_never_looked_up() -> None:
    def releases(repo: str) -> dict[str, object]:
        return json.loads((FIXTURES / "gh_releases.json").read_text())[repo]

    rows = {(row["tool"], row["literal"]): row for row in pins.pin_rows(pins.collect(FIXTURES), releases)}

    assert rows[("actions/upload-artifact", "v7")]["status"] == "current"
    assert rows[("actions/checkout", "v6")]["status"] == "major"
    assert rows[("postgres-image", "16.15-bookworm")]["status"] == "pinned (digest)"
    assert rows[("databricks/setup-cli", "v1.7.0")]["status"] == "no release"


# -------------------------------------------------------------------- sanitizer


def test_the_sanitizer_drops_any_value_outside_its_shape() -> None:
    assert render.code("@tanstack/react-query", "npm") == "`@tanstack/react-query`"
    assert render.code("bad`name", "npm") == render.DROPPED
    assert render.code("bad\nname", "npm") == render.DROPPED
    assert render.code("[x](http://evil)", "npm", "pypi", "tool", "text") == render.DROPPED
    assert render.advisory("GHSA-2234-5678-9cfg") == "[`GHSA-2234-5678-9cfg`](https://github.com/advisories/GHSA-2234-5678-9cfg)"
    assert render.advisory("GHSA-1111-1111-1111") == render.DROPPED, "1 is outside the GHSA alphabet"
    assert render.run_url({"GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "42"}) == "https://github.com/o/r/actions/runs/42"
    assert render.run_url({"GITHUB_SERVER_URL": "https://evil.example", "GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "42"}) is None


def test_every_real_hold_reason_renders_as_text_not_dropped() -> None:
    """A reason with '@' or '<' would render silently as '[value dropped]'."""

    for hold in load_holds(HOLDS_FILE):
        assert render.code(hold["reason"], "text") != render.DROPPED, hold["package"]


def test_an_injected_advisory_title_never_reaches_the_report() -> None:
    markdown = render.render(cli.build(_offline(), TODAY))

    assert "someone" not in markdown and "evil" not in markdown and "script" not in markdown
    assert render.validate_markdown(markdown, None) == []
    outside_code = render._CODE_SPAN.sub("", markdown)
    assert "@" not in outside_code, "every scoped npm name sits inside a code span"


def test_the_validator_refuses_mentions_links_html_and_size() -> None:
    run = "https://github.com/o/r/actions/runs/42"
    assert render.validate_markdown("hi @someone", run) == ["'@' outside a code span"]
    assert render.validate_markdown("[x](http://evil)", run) == ["link target not allowed"]
    assert render.validate_markdown("<b>x</b>", run) == ["contains '<'"]
    assert render.validate_markdown("x" * (render.MAX_CHARS + 1), run) == ["too long"]
    assert render.validate_markdown(f"[this run]({run}) `@ok`", run) == []


# ------------------------------------------------------------ live (never gates)


def _verdict(reason: str, **fields: object) -> gate.Verdict:
    return gate.Verdict("FAIL", reason, "a" * 40, **fields)


@pytest.mark.parametrize(
    "verdict",
    [
        _verdict("no_green_run"),
        _verdict("runtime_changed", run_url="https://github.com/o/r/actions/runs/7", run_sha="b" * 40, age_days=120.5, paths=["backend/main.py"]),
        _verdict("stale", run_url="https://github.com/o/r/actions/runs/8", run_sha="a" * 40, age_days=15.0),
    ],
)
def test_a_failing_live_verdict_never_turns_a_green_report_red(verdict: gate.Verdict) -> None:
    section = live.live_section(release_sha="a" * 40, evaluate=lambda *args, **kwargs: verdict)
    report = cli.build(_offline(live=lambda today: section), date(2026, 10, 1))

    assert report["exit_code"] == 0, "a green report stays green whatever the live verdict"
    markdown = render.render(report)
    assert f"would FAIL the gate: `{verdict.reason}`" in markdown
    if verdict.age_days and verdict.age_days >= 7:
        assert "7 days old or more" in markdown


def test_a_raising_live_gate_renders_unavailable_and_exits_zero() -> None:
    def broken(*args: object, **kwargs: object) -> gate.Verdict:
        raise RuntimeError("boom: https://evil.example")

    section = live.live_section(release_sha="a" * 40, evaluate=broken)
    report = cli.build(_offline(live=lambda today: section), date(2026, 10, 1))

    assert report["exit_code"] == 0
    markdown = render.render(report)
    assert "- live gate unavailable: `RuntimeError`" in markdown
    assert "evil" not in markdown


def test_the_offline_live_section_runs_the_real_gate_over_the_fixture_runs() -> None:
    section = cli.offline_sources(FIXTURES).live(TODAY)

    assert section["available"] is True and section["reason"] == "no_green_run"
    assert section["release_sha"] == "0927bb22ab1cc8c58b1ccdbb5e73b80a3fdd488f"


# ------------------------------------------------------------------- exit codes


def test_the_exit_codes(tmp_path: Path) -> None:
    assert cli.build(_offline(), TODAY)["exit_code"] == 1, "a past review date is the gate"
    assert cli.build(_offline(), date(2026, 10, 1))["exit_code"] == 0, "drift and moderate advisories alone are green"

    broken = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, broken)
    (broken / "pypi.json").write_text("{}")
    out_json, out_md = tmp_path / "r.json", tmp_path / "r.md"
    code = cli.main(["--offline-fixtures", str(broken), "--today", "2026-10-01", "--json", str(out_json), "--markdown", str(out_md)], env={})

    assert code == 2, "a failed registry read is report_error"
    assert json.loads(out_json.read_text())["errors"] == ["pypi"]
    assert "## Report error" in out_md.read_text() and "could not read `pypi`" in out_md.read_text()


def test_an_unexpected_failure_is_a_report_error_with_both_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(*_args: object) -> dict[str, object]:
        raise KeyError("boom-detail-https://evil.example")

    monkeypatch.setattr(cli, "build", broken)
    out_json, out_md = tmp_path / "r.json", tmp_path / "r.md"
    code = cli.main(["--offline-fixtures", str(FIXTURES), "--today", "2026-10-01", "--json", str(out_json), "--markdown", str(out_md)], env={})

    assert code == 2, "never 1: an internal failure is not a gate signal"
    report = json.loads(out_json.read_text())
    assert report["errors"] == [cli.INTERNAL_ERROR] and report["gate"] == [] and report["exit_code"] == 2
    markdown = out_md.read_text()
    assert "could not read `deps-report internal error`" in markdown
    assert render.validate_markdown(markdown, None) == []
    err = capsys.readouterr().err
    assert "KeyError" in err and "evil" not in err and "evil" not in markdown


def test_the_offline_dry_run_reproduces_the_pinned_markdown(tmp_path: Path) -> None:
    out_json, out_md = tmp_path / "deps.json", tmp_path / "deps.md"
    env = {"GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "1"}
    code = cli.main(
        ["--offline-fixtures", str(FIXTURES), "--today", "2026-10-14", "--json", str(out_json), "--markdown", str(out_md)],
        env=env,
    )

    assert code == 1
    assert out_md.read_text(encoding="utf-8") == (FIXTURES / "expected_report.md").read_text(encoding="utf-8")
    assert json.loads(out_json.read_text())["exit_code"] == 1
