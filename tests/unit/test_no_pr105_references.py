"""Ratchet: the retired CI staging install leaves no trace outside the allowlist (M0-PR3).

Its workspace was deleted on 2026-10-29, and with it went its bundle target,
the CI isolation rule table, the install-scope probes, the bundle-root leaf
and display-name suffix, and deploy-dev.yml's credential repair. This scan
holds every tracked file to zero mentions of them, except the dated records
and the one template listed below.

The scan is ``git grep -P``. ``git grep -E`` has no ``\\b`` on every platform
(it silently matches nothing on macOS), so the positive control below proves
the scan finds this file's own pattern before any absence is trusted.
"""

from __future__ import annotations

import re
import subprocess
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SELF = "tests/unit/test_no_pr105_references.py"
RETIRED = (
    r"(?i)pr105"
    r"|\binstall_suffix\b"
    r"|ci[-_]install[-_]isolation"
    r"|\binstall_scope\b"
    r"|\bdev-ci\b"
    r"|bundle_root_leaf"
    r"|ci_staging_host"
    r"|MIP_GITHUB_CREDENTIAL_SINK_TOKEN"
    r"|repair_normal_credential"
    r"|repair-credential"
    r"|forked[- ]anchor"
)
# Positive-control spellings, one per alternative above (the scan must find
# each here): pr105-host install_suffix ci_install_isolation install_scope
# dev-ci bundle_root_leaf ci_staging_host MIP_GITHUB_CREDENTIAL_SINK_TOKEN
# repair_normal_credential repair-credential forked-anchor
# Each allowed path, why, and the most matching lines it may keep.
ALLOWED_FILES = {
    SELF: ("this ratchet's own pattern: the positive control", None),
    "docs/validation/lifecycle-replay-sql-2026-07-29.json": (
        "a reviewed SQL record whose sha256 MIP_LIFECYCLE_REPLAY_REVIEW_SHA256 pins; an edit voids the review",
        None,
    ),
    ".env.example": (
        "agents cannot read this template under the repository's permission settings; the owner removes "
        "its one known mention",
        1,
    ),
}
ALLOWED_PREFIXES = {
    ".claude/agent-memory/": "dated agent-memory records, not code, tests, workflows or docs",
    "docs/audits/": "dated audit reports",
}
# Allowlist entries only the owner can clear, each tracked by an owner
# follow-up paragraph in docs/deployment.md until it is cleared.
DEPLOYMENT_DOC = ROOT / "docs" / "deployment.md"
OWNER_FOLLOW_UPS = frozenset({".env.example"})


def _matching_lines() -> Counter[str]:
    """Matching line count per tracked path; paths only, never a matched line."""

    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout: the scan reads tracked files through git grep")
    result = subprocess.run(
        ["git", "grep", "-I", "-c", "-P", "-e", RETIRED, "--", "."],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode in (0, 1), f"git grep -P failed (exit {result.returncode}); is git built with PCRE?"
    counts: Counter[str] = Counter()
    for line in result.stdout.splitlines():
        path, _, count = line.rpartition(":")
        counts[path] = int(count)
    return counts


def _allowed(path: str) -> bool:
    return path in ALLOWED_FILES or path.startswith(tuple(ALLOWED_PREFIXES))


def test_the_scan_finds_every_alternative_in_its_own_file() -> None:
    """The positive control: an absence below means nothing unless each alternative matches here."""

    assert _matching_lines()[SELF] > 0
    alternatives = RETIRED.removeprefix("(?i)").split("|")
    assert len(alternatives) == 11
    for alternative in alternatives:
        found = subprocess.run(
            ["git", "grep", "-c", "-P", "-e", f"(?i){alternative}", "--", SELF],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert found.returncode == 0 and found.stdout.strip(), f"git grep -P found no {alternative!r} here"


def test_no_tracked_file_outside_the_allowlist_names_the_retired_install() -> None:
    counts = _matching_lines()
    offenders = sorted(f"{path} ({count} lines)" for path, count in counts.items() if not _allowed(path))

    assert offenders == [], "remove these mentions of the retired CI staging install: " + ", ".join(offenders)


def test_capped_allowlist_entries_never_grow() -> None:
    counts = _matching_lines()

    for path, (_reason, cap) in ALLOWED_FILES.items():
        if cap is not None:
            assert counts[path] <= cap, f"{path}: {counts[path]} matching lines, at most {cap} allowed"


def test_every_allowlist_entry_is_still_needed_and_tracked() -> None:
    """A stale entry would silently widen the allowlist; prune it once its file is clean or gone."""

    counts = _matching_lines()
    tracked = set(
        subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True, timeout=120).stdout.split()
    )

    for path in ALLOWED_FILES:
        assert path in tracked, f"{path} is no longer tracked; drop its allowlist entry"
        assert counts[path] > 0, f"{path} no longer matches; drop its allowlist entry"
    for prefix in ALLOWED_PREFIXES:
        assert any(path.startswith(prefix) for path in counts), f"no {prefix} file matches; drop the prefix"


def test_every_owner_cleared_entry_is_a_tracked_owner_follow_up() -> None:
    """Review of #293: an entry only the owner can clear must not become a permanent cap.

    Each one is capped, and docs/deployment.md carries its owner follow-up,
    which names the file, the edit and this test; the paragraph goes when the
    entry does, so the doc never lists a follow-up that is already done.
    """

    owner_cleared = {path for path, (reason, _cap) in ALLOWED_FILES.items() if "the owner" in reason}
    assert owner_cleared == OWNER_FOLLOW_UPS, "an owner-cleared entry needs an owner follow-up, and only it"
    paragraphs = [
        (match.group(1), " ".join(paragraph.split()))
        for paragraph in DEPLOYMENT_DOC.read_text(encoding="utf-8").split("\n\n")
        if (match := re.match(r"\*\*Owner follow-up: `([^`]+)`\.\*\*", paragraph))
    ]
    assert sorted(path for path, _body in paragraphs) == sorted(OWNER_FOLLOW_UPS)
    for path, body in paragraphs:
        assert ALLOWED_FILES[path][1] is not None, f"{path}: an owner-cleared entry is always capped"
        assert "`ALLOWED_FILES`" in body and "this paragraph" in body, path
        assert "`test_every_allowlist_entry_is_still_needed_and_tracked`" in body, path
