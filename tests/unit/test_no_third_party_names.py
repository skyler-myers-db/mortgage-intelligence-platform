"""No tracked file names a third party (post-W5 review G1/B2, 2026-10-27).

An earlier lane wrote two catalog owners' workspace handles and three other
teams' catalog names into the docs, tests and agent memory, and its history
had to be rebuilt to remove them. This scan keeps them out of every tracked
text file. It stores only sha256 digests, so it never names them either.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# The sha256 of each lower-cased third-party token an earlier lane wrote into
# the docs, tests and agent memory: the surnames in two catalog owners'
# workspace handles and three other teams' catalog names. Only digests live
# here, so this file never names them either. Add a digest, never a name.
THIRD_PARTY_TOKEN_DIGESTS = frozenset(
    {
        "424347f8f9692b06bee717470792bb7116d29d4e1add74742dd83999edde2874",
        "50df79aaa0d0b5fb849a6797c8b286681444e5e306b2dba53ddeadf02c1c368d",
        "81015345852680be0b3b211a34ada437002277e68ff41933bccc3aae17ce2c9f",
        "9ce8487d4f4e96cdef5598919efe8b879a2ed30c3ec50bd32edaf13ec101a057",
        "bdd563e204e752e77f7660604b596d545e1488e33c8e63b36087aec7f995eedb",
    }
)
_TOKEN = re.compile(rb"[a-z0-9_]{3,64}")
_BINARY_SUFFIXES = frozenset({".png", ".webp", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".woff", ".woff2", ".ttf", ".zip"})
_WALK_SKIP = {".git", "node_modules", "dist", ".venv", "__pycache__", "test-results", "playwright-report", "worktrees"}


def _tracked_files() -> list[str]:
    """Every tracked path (force-added agent memory included); a plain walk outside a git checkout."""

    try:
        listed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True, timeout=120)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        files: list[str] = []
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [name for name in dirnames if name not in _WALK_SKIP]
            base = Path(dirpath).relative_to(ROOT)
            files.extend((base / name).as_posix() for name in filenames)
        return sorted(files)
    return sorted(item for item in listed.stdout.decode("utf-8").split("\0") if item)


def third_party_token_files(
    paths: list[str],
    read: Callable[[str], bytes | None],
    digests: frozenset[str] = THIRD_PARTY_TOKEN_DIGESTS,
) -> tuple[list[str], int]:
    """(files holding a token whose digest is listed, text files scanned).

    Case-folded, so a capitalized handle matches; split on non-word
    characters, so a dotted handle's surname is its own token; and each
    underscore-joined token's parts are checked too, so a name inside a longer
    identifier still matches.
    """

    listed: dict[bytes, bool] = {}  # each distinct token is hashed once
    hits: list[str] = []
    scanned = 0
    for rel in paths:
        if Path(rel).suffix.lower() in _BINARY_SUFFIXES:
            continue
        data = read(rel)
        if data is None or b"\0" in data:
            continue
        scanned += 1
        tokens = set(_TOKEN.findall(data.lower()))
        tokens.update(part for token in tuple(tokens) if b"_" in token for part in token.split(b"_"))
        for token in tokens:
            flagged = listed.get(token)
            if flagged is None:
                flagged = listed[token] = hashlib.sha256(token).hexdigest() in digests
            if flagged:
                hits.append(rel)
                break
    return sorted(hits), scanned


def _read_tracked(rel: str) -> bytes | None:
    try:
        return (ROOT / rel).read_bytes()
    except OSError:
        return None


def test_the_digest_scan_finds_a_planted_token_in_any_spelling() -> None:
    """Non-vacuity control for the repo-wide scan, on a made-up token."""

    planted = frozenset({hashlib.sha256(b"zyrplax").hexdigest()})
    corpus = {
        "docs/a.md": b"owner Ann.Zyrplax approved it",
        "docs/b.md": b"(`ZYRPLAX`, `other`)",
        "tests/c.py": b"catalog = 'team_zyrplax_prod'",
        "docs/clean.md": b"nothing to see; zyrplaxian is another word",
        "img.png": b"zyrplax",
    }
    hits, scanned = third_party_token_files(sorted(corpus), corpus.get, planted)

    assert hits == ["docs/a.md", "docs/b.md", "tests/c.py"]
    assert scanned == 4, "binary suffixes are skipped"


def test_no_tracked_file_names_a_third_party_handle_or_catalog() -> None:
    """The third-party names stay out of every tracked text file.

    Repo-wide, so the docs, the tests, the force-added agent memory and the
    workflows are all covered, whatever the case or punctuation. The commit
    history is the integrator's check (`git log -p` over the lane), since no
    test can read it.
    """

    paths = _tracked_files()
    for required in (
        "docs/deployment.md",
        "docs/runbook.md",
        "docs/security/GRANTS.md",
        ".github/workflows/deploy-dev.yml",
        "tests/unit/test_ci_install_isolation.py",
        "tests/unit/test_no_third_party_names.py",
        ".claude/agent-memory/principal-architect/project_forked_anchor_ci_target.md",
    ):
        assert required in paths, required
    hits, scanned = third_party_token_files(paths, _read_tracked)

    assert scanned > 1000, "the scan reads the whole tree"
    assert hits == [], hits
