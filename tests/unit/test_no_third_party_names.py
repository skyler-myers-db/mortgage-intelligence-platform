"""No tracked file names a third party (post-W5 review G1/B2, 2026-10-27).

An earlier lane wrote two catalog owners' workspace handles and three other
teams' catalog names into the docs, tests and agent memory, and its history
had to be rebuilt to remove them. This scan keeps them out of every tracked
text file. It stores only sha256 digests, so it never names them either.
"""

from __future__ import annotations

import hashlib
import itertools
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
# The sha256 of the first part of each underscore-joined name above, so a
# hyphen-, space- or line-separated spelling is re-joined only where it can
# start one (post-W5 review: those spellings escaped the token scan).
THIRD_PARTY_LEAD_PART_DIGESTS = frozenset(
    {
        "0a4e4c29432ecb01b95f87d87ffaff9cb4f36ce4b89ff1a1a136ad1c7392ae07",
        "8270f2824111e04d9278c01a92b388147d9d02e0b50d946d25d00db375ff1282",
    }
)
_TOKEN = re.compile(rb"[a-z0-9_]{3,64}")
# A lower-to-upper boundary, so a CamelCase spelling splits into its parts.
_CAMEL = re.compile(rb"(?<=[a-z0-9])(?=[A-Z])")
_SEPARATOR = re.compile(rb"[-_\s]+")
_MAX_EXTRA_PARTS = 3
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


def _tokens(view: bytes) -> set[bytes]:
    tokens = set(_TOKEN.findall(view))
    tokens.update(part for token in tuple(tokens) if b"_" in token for part in token.split(b"_"))
    return tokens


def _names_a_third_party(
    data: bytes,
    digest: Callable[[bytes], str],
    digests: frozenset[str],
    lead_digests: frozenset[str],
) -> bool:
    lower = data.lower()
    camel = _CAMEL.sub(b"_", data).lower()
    plain = _tokens(lower)
    leads: set[bytes] = set()
    # The CamelCase view adds only the tokens the plain view lacks.
    for token in itertools.chain(plain, _tokens(camel) - plain):
        hexdigest = digest(token)
        if hexdigest in digests:
            return True
        if hexdigest in lead_digests:
            leads.add(token)
    for lead in leads:
        run = re.compile(rb"(?<![a-z0-9])" + re.escape(lead) + rb"(?:[-_\s]+[a-z0-9]+){1,%d}" % _MAX_EXTRA_PARTS)
        for view in (lower, camel):
            for match in run.finditer(view):
                parts = _SEPARATOR.split(match.group())
                if any(digest(b"_".join(parts[:count])) in digests for count in range(2, len(parts) + 1)):
                    return True
    return False


def third_party_token_files(
    paths: list[str],
    read: Callable[[str], bytes | None],
    digests: frozenset[str] = THIRD_PARTY_TOKEN_DIGESTS,
    lead_digests: frozenset[str] = THIRD_PARTY_LEAD_PART_DIGESTS,
) -> tuple[list[str], int]:
    """(files holding a token whose digest is listed, text files scanned).

    Case-folded, so a capitalized handle matches; split on non-word
    characters, so a dotted handle's surname is its own token; and each
    underscore-joined token's parts are checked too, so a name inside a longer
    identifier still matches. A CamelCase spelling is split at its case
    boundaries, and a hyphen-, space- or line-separated run that starts with a
    listed lead part is re-joined with underscores, so those spellings of an
    underscore name match too. A name embedded in a longer identifier with no
    separator at all is out of reach: matching it would need every substring
    of the tree hashed, and such an identifier does not name the catalog.
    """

    hashed: dict[bytes, str] = {}  # each distinct token is hashed once

    def digest(token: bytes) -> str:
        value = hashed.get(token)
        if value is None:
            value = hashed[token] = hashlib.sha256(token).hexdigest()
        return value

    hits: list[str] = []
    scanned = 0
    for rel in paths:
        if Path(rel).suffix.lower() in _BINARY_SUFFIXES:
            continue
        data = read(rel)
        if data is None or b"\0" in data:
            continue
        scanned += 1
        if _names_a_third_party(data, digest, digests, lead_digests):
            hits.append(rel)
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


def test_the_digest_scan_rejoins_a_spelled_apart_underscore_name() -> None:
    """Non-vacuity control for the hyphen, space, line-break and CamelCase spellings, on a made-up name."""

    planted = frozenset({hashlib.sha256(b"zyr_plax_vex").hexdigest(), hashlib.sha256(b"qor_tun").hexdigest()})
    leads = frozenset({hashlib.sha256(b"zyr").hexdigest(), hashlib.sha256(b"qor").hexdigest()})
    corpus = {
        "a.md": b"the Zyr-Plax-Vex catalog",
        "b.md": b"read zyr plax vex daily",
        "c.md": b"wrapped zyr\n  plax\n  vex here",
        "d.ts": b"const catalog = ZyrPlaxVex;",
        "e.md": b"(`QOR-TUN`)",
        "f.md": b"qor -- tun",
        "clean_order.md": b"plax zyr vex",
        "clean_partial.md": b"zyr plax and vex",
        "clean_prefix.md": b"azyr plax vex",
        "clean_joined.md": b"zyrplaxvex and qortun",
    }
    hits, scanned = third_party_token_files(sorted(corpus), corpus.get, planted, leads)

    assert hits == ["a.md", "b.md", "c.md", "d.ts", "e.md", "f.md"]
    assert scanned == len(corpus)
    no_leads, _ = third_party_token_files(sorted(corpus), corpus.get, planted, frozenset())
    assert no_leads == ["d.ts"], "without the lead digests only the CamelCase view finds one"


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
        ".github/workflows/nightly.yml",
        "tests/unit/test_no_third_party_names.py",
        ".claude/agent-memory/principal-architect/project_forked_anchor_ci_target.md",
    ):
        assert required in paths, required
    hits, scanned = third_party_token_files(paths, _read_tracked)

    assert scanned > 1000, "the scan reads the whole tree"
    assert hits == [], hits
