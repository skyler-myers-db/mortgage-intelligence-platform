"""The public-output sanitizer and markdown renderer (the repository is PUBLIC).

Every dynamic value is shape-validated and rendered inside a backtick code
span, so a scoped name never becomes an @mention and no value can open a
link, a table cell or raw HTML; anything else renders as '[value dropped]'.
Advisory titles, descriptions and API-supplied URLs are never rendered. The
only links are this run's URL and https://github.com/advisories/<id>. The
body stays within 60,000 characters and ends with a pointer to the
artifact. ``validate_markdown`` is the same check the publish job runs again
before it edits the issue (.github/workflows/deps-report.yml).
"""

from __future__ import annotations

import re
from typing import Any

from tools.deps_report import Signal

MAX_CHARS = 60_000
DROPPED = "[value dropped]"
TITLE = "Weekly dependency and release-signal report"
FOOTER = "Full data: the `deps-report` artifact of this run (`deps-report.json`, `deps-report.md`)."
ADVISORY = r"GHSA(?:-[23456789cfghjmpqrvwx]{4}){3}|CVE-\d{4}-\d{4,}|PYSEC-\d{4}-\d+"
SHAPES: dict[str, re.Pattern[str]] = {
    name: re.compile(rf"(?:{pattern})\Z")
    for name, pattern in {
        "npm": r"(?:@[a-z0-9-~][a-z0-9-._~]*/)?[a-z0-9-~][a-z0-9-._~]*",
        "pypi": r"[a-z0-9]+(?:-[a-z0-9]+)*",
        "version": r"v?\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?|\d+(?:\.\d+)*(?:(?:a|b|rc)\d+)?(?:\.post\d+)?(?:\.dev\d+)?",
        "advisory": ADVISORY,
        "date": r"\d{4}-\d{2}-\d{2}",
        "action": r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+@[A-Za-z0-9._-]+",
        "tag": r"v?[0-9][0-9A-Za-z._-]{0,40}",
        "sha": r"[0-9a-f]{7,40}",
        "tool": r"[A-Za-z0-9-]+(?:/[A-Za-z0-9._-]+)?",
        "path": r"[A-Za-z0-9._/+-]+(?::\d+)?",
        "word": r"[a-z][a-z_ ()-]*",
        "status": r"[A-Z][A-Z_]*",
        "number": r"\d+(?:\.\d+)?",
        "text": r"[A-Za-z0-9 .,;:()'/+=_-]{1,240}",
        "run_url": r"https://github\.com/[A-Za-z0-9-]+/[A-Za-z0-9._-]+/actions/runs/\d+",
    }.items()
}
_CODE_SPAN = re.compile(r"`[^`\n]*`")
_LINK = re.compile(r"\]\(([^)\s]*)\)")
_ADVISORY_LINK = re.compile(rf"https://github\.com/advisories/(?:{ADVISORY})\Z")


def code(value: object, *kinds: str) -> str:
    """A code span when ``value`` matches one of ``kinds``, else '[value dropped]'."""

    text = str(value)
    if any(SHAPES[kind].match(text) for kind in kinds):
        return f"`{text}`"
    return DROPPED


def advisory(identifier: str) -> str:
    if not SHAPES["advisory"].match(identifier):
        return DROPPED
    if identifier.startswith("GHSA-"):
        return f"[`{identifier}`](https://github.com/advisories/{identifier})"
    return f"`{identifier}`"


def run_url(env: dict[str, str]) -> str | None:
    """$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID, validated."""

    url = f"{env.get('GITHUB_SERVER_URL', '')}/{env.get('GITHUB_REPOSITORY', '')}/actions/runs/{env.get('GITHUB_RUN_ID', '')}"
    return url if SHAPES["run_url"].match(url) else None


_SUBJECT_KINDS = ("path", "npm", "pypi", "tool")
_DETAIL_KINDS = ("date", "advisory", "version")


def signal_line(signal: Signal) -> str:
    detail = " ".join(code(part, *_DETAIL_KINDS) for part in signal.detail.split()) if signal.detail else ""
    return f"- {code(signal.code, 'word')} {code(signal.subject, *_SUBJECT_KINDS)} {detail}".rstrip()


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    if not rows:
        return ["None."]
    return [f"| {' | '.join(header)} |", f"|{'---|' * len(header)}", *(f"| {' | '.join(row)} |" for row in rows)]


def _drift(rows: list[dict[str, str]], name_kind: str) -> list[list[str]]:
    out = []
    for row in rows:
        status = code(row["status"], "word")
        if row["status"] == "held":
            status += f" {code(row['hold_reason'], 'text')} until {code(row['hold_review_by'], 'date')}"
        published = code(row["published"], "date") if row["published"] else ""
        out.append([code(row["package"], name_kind), code(row["pinned"], "version"), code(row["latest"], "version"), published, code(row["gap"], "word"), status])
    return out


def _live(live: dict[str, Any]) -> list[str]:
    if not live.get("available"):
        return [f"- live gate unavailable: {code(live.get('error', ''), 'tool')}"]
    reason = f" {code(live['reason'], 'word')}" if live["reason"] else ""
    lines = [f"- Verdict: {code(live['status'], 'status')}{reason} for {code(live['release_sha'], 'sha')}"]
    if live["status"] != "PASS":
        lines.append(f"- A release from this sha would FAIL the gate: {code(live['reason'] or 'api_error', 'word')}")
    if live.get("run_url"):
        lines.append(f"- Deciding run: {code(live['run_url'], 'run_url')} on {code(live['run_sha'], 'sha')}, {code(live['age_days'], 'number')} days old")
    if live.get("stale_warning"):
        lines.append("- Warning: the deciding run is 7 days old or more.")
    if live.get("paths"):
        listed = ", ".join(code(path, "path") for path in live["paths"])
        lines.append(f"- Changed runtime paths: {code(live['changed_runtime_paths'], 'number')}; first {len(live['paths'])}: {listed}")
    return lines


def render(report: dict[str, Any], run: str | None = None) -> str:
    exit_code = report["exit_code"]
    lines = [f"# {TITLE}", ""]
    lines.append(f"- Date: {code(report['today'], 'date')}; commit {code(report['release_sha'], 'sha')}")
    if run:
        lines.append(f"- Run: [this run]({run})")
    lines.append(f"- Result: {code(f'exit {exit_code}', 'word', 'text')} ({'report error' if exit_code == 2 else 'gate red' if exit_code == 1 else 'green'})")
    if report["errors"]:
        lines += ["", "## Report error", "", *(f"- could not read {code(source, 'text')}" for source in report["errors"])]
    lines += ["", "## Gate (turns the run red)", "", *([signal_line(s) for s in report["gate"]] or ["None."])]
    lines += ["", "## Warnings", "", *([signal_line(s) for s in report["warnings"]] or ["None."])]
    npm_adv = [[code(a["package"], "npm"), advisory(a["id"]), code(a["severity"], "word"), "gate" if a["severity"] in ("high", "critical") else "informational"] for a in report["npm_advisories"]]
    pip_adv = [[code(a["package"], "pypi"), code(a["version"], "version"), advisory(a["id"]), "ignored in ci.yml" if a["ignored"] == "yes" else "gate"] for a in report["pip_advisories"]]
    lines += ["", "## Advisories", "", "npm audit over the lock (high and critical gate):", "", *_table(["Package", "Advisory", "Severity", "Effect"], npm_adv)]
    lines += ["", "pip-audit over uv.lock:", "", *_table(["Package", "Version", "Advisory", "Effect"], pip_adv)]
    waiver_rows = [[code(w["kind"], "text"), code(w["subject"], *_SUBJECT_KINDS), code(w["date"], "date")] for w in report["waivers"]]
    lines += ["", "## Dated waivers and holds", "", *_table(["Kind", "Subject", "Date"], waiver_rows)]
    header = ["Package", "Pinned", "Latest", "Published", "Gap", "Status"]
    lines += ["", "## npm drift (never gates)", "", *_table(header, _drift(report["npm"], "npm"))]
    lines += ["", "## PyPI drift (never gates)", "", *_table(header, _drift(report["pypi"], "pypi"))]
    pin_rows = [[code(p["tool"], "tool"), code(p["literal"], "tag"), code(p["latest"], "tag") if p["latest"] else "", code(p["status"], "word"), ", ".join(code(w, "path") for w in p["where"].split(", "))] for p in report["pins"]]
    lines += ["", "## Workflow tool pins", "", *_table(["Tool", "Literal", "Latest release", "Status", "Where"], pin_rows)]
    lines += ["", "## Live validation gate (informational, never gates)", "", *_live(report["live"])]
    return _bounded(lines)


def _bounded(lines: list[str]) -> str:
    text = "\n".join([*lines, "", FOOTER, ""])
    while len(text) > MAX_CHARS and len(lines) > 1:
        lines = lines[: max(1, len(lines) - 50)]
        text = "\n".join([*lines, "", "Truncated to the size limit.", "", FOOTER, ""])
    return text


def validate_markdown(markdown: str, run: str | None) -> list[str]:
    """Problems the publish job would refuse; empty means publishable."""

    problems = []
    if len(markdown) > MAX_CHARS:
        problems.append("too long")
    if "<" in markdown:
        problems.append("contains '<'")
    for target in _LINK.findall(_CODE_SPAN.sub("", markdown)):
        if target != run and not _ADVISORY_LINK.match(target):
            problems.append("link target not allowed")
    bare = _LINK.sub("]", _CODE_SPAN.sub("", markdown))
    if "@" in bare:
        problems.append("'@' outside a code span")
    if "://" in bare or "www." in bare:
        problems.append("URL outside a link or code span")
    return problems
