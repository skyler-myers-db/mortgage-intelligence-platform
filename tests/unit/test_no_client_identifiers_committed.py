"""No new client identifier is committed: a ratchet at the 2026-10-29 counts.

Client identifiers belong in GitHub Environments and ``.env.local``, never
in the tree (tools/clients/schema.py). The tree still holds the first client's
workspace host, workspace id and App URL, plus personal emails, from before
that rule. This test counts four identifier shapes in every tracked text file
and fails when a count rises above its baseline. The identifier purge (M1-PR4)
drives each count to zero and adds more shapes.

The shapes are generic, so they also catch a new client's identifiers, and
this file never names a client value: each positive control is assembled at
run time, so no literal here matches a shape.

When a count rises, the failure lists every file holding that shape. Replace
the new value with a symbolic placeholder (``<workspace-host>``) or an
example-domain address. When a count falls, lower its baseline in the same
change, so the ratchet keeps the gain.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

SHAPES: dict[str, re.Pattern[bytes]] = {
    # An AWS dbc- host, an Azure adb- host or a GCP numeric host.
    "workspace_host": re.compile(
        rb"(?i)(?<![a-z0-9-])(?:dbc-[0-9a-f]{8}(?:-[0-9a-f]{4})?\.cloud\.databricks\.com"
        rb"|adb-[0-9]{6,19}\.[0-9]{1,2}\.azuredatabricks\.net"
        rb"|[0-9]{6,19}\.[0-9]\.gcp\.databricks\.com)(?![a-z0-9-])"
    ),
    # A Databricks App URL: <app>-<workspace id>.<cloud>.databricksapps.com.
    "app_url": re.compile(
        rb"(?i)(?<![a-z0-9-])[a-z0-9-]+-[0-9]{6,19}\.(?:aws|azure|gcp)\.databricksapps\.com"
    ),
    # A bare 16-digit workspace id, or any workspace id in an ``?o=`` query.
    "workspace_id": re.compile(rb"(?<![0-9A-Za-z])[0-9]{16}(?![0-9A-Za-z])|[?&]o=[0-9]{6,19}(?![0-9])"),
    # An email-shaped address; reserved example domains are dropped below.
    "email": re.compile(rb"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})"),
}
# RFC 2606 / 6761 reserved names: an address there names nobody.
_EXAMPLE_DOMAIN = re.compile(
    rb"(?i)(?:^|\.)(?:example|invalid|test|localhost|local)$|(?:^|\.)example\.(?:com|net|org)$"
)

# Occurrences in tracked text files at origin/main 6c363903, measured with
# ``git grep -I -o -P`` and this file's scan (they agree). Lower, never raise.
BASELINE = {
    "workspace_host": 33,
    "app_url": 83,
    "workspace_id": 146,
    "email": 77,
}

_BINARY_SUFFIXES = frozenset(
    {".png", ".webp", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".woff", ".woff2", ".ttf", ".zip"}
)
_WALK_SKIP = {".git", "node_modules", "dist", ".venv", "__pycache__", "test-results", "playwright-report", "worktrees"}


def _tracked_files() -> list[str]:
    """Every tracked path; a plain walk outside a git checkout."""

    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True, timeout=120
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        files: list[str] = []
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [name for name in dirnames if name not in _WALK_SKIP]
            base = Path(dirpath).relative_to(ROOT)
            files.extend((base / name).as_posix() for name in filenames)
        return sorted(files)
    return sorted(item for item in listed.stdout.decode("utf-8").split("\0") if item)


def shape_counts(data: bytes) -> Counter[str]:
    """Occurrences of each shape in one file's bytes."""

    counts: Counter[str] = Counter()
    for shape, pattern in SHAPES.items():
        if shape == "email":
            counts[shape] = sum(
                1 for match in pattern.finditer(data) if not _EXAMPLE_DOMAIN.search(match.group(1))
            )
        else:
            counts[shape] = sum(1 for _ in pattern.finditer(data))
    return counts


@pytest.fixture(scope="module")
def tree_counts() -> dict[str, Counter[str]]:
    """Per shape, the files holding it and how often."""

    per_shape: dict[str, Counter[str]] = {shape: Counter() for shape in SHAPES}
    for rel in _tracked_files():
        if Path(rel).suffix.lower() in _BINARY_SUFFIXES:
            continue
        try:
            data = (ROOT / rel).read_bytes()
        except OSError:
            continue
        if b"\0" in data:
            continue
        for shape, count in shape_counts(data).items():
            if count:
                per_shape[shape][rel] = count
    return per_shape


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_no_shape_count_rises_above_its_baseline(shape: str, tree_counts: dict[str, Counter[str]]) -> None:
    files = tree_counts[shape]
    total = sum(files.values())
    listing = "\n".join(f"  {count:4d}  {rel}" for rel, count in files.most_common())
    assert total <= BASELINE[shape], (
        f"{total} {shape} occurrences against a baseline of {BASELINE[shape]}: a client "
        "identifier was committed. Replace it with a symbolic placeholder or an "
        f"example-domain value. Files holding the shape:\n{listing}"
    )


def test_baselines_cover_exactly_the_shapes() -> None:
    assert set(BASELINE) == set(SHAPES)


def _host(prefix: str, *parts: str) -> bytes:
    return ("".join((prefix, *parts))).encode()


def test_each_shape_has_a_positive_and_a_negative_control() -> None:
    sixteen = "7" * 16
    positives = {
        "workspace_host": [
            _host("dbc-", "0a1b2c3d", "-", "4e5f", ".cloud.databricks.com"),
            _host("adb-", sixteen, ".", "7", ".azuredatabricks.net"),
            _host(sixteen, ".", "2", ".gcp.databricks.com"),
        ],
        "app_url": [_host("mip-app-", sixteen, ".aws.databricksapps.com")],
        "workspace_id": [_host("workspace ", sixteen), _host("/?o=", "1" * 15)],
        "email": [_host("someone", "@", "corp-alpha", ".com")],
    }
    negatives = {
        "workspace_host": [b"https://dbc-test.cloud.databricks.com", b"accounts.cloud.databricks.com"],
        "app_url": [b"https://mip-app-test.aws.databricksapps.com", b"mip-app.databricksapps.com"],
        "workspace_id": [_host("x", sixteen), _host("1" * 17), b"<workspace-id>"],
        "email": [b"admin@summit.example", b"user@example.com", b"jane@ops.example.org", b"x@y.invalid"],
    }
    for shape, samples in positives.items():
        for sample in samples:
            assert shape_counts(sample)[shape] == 1, (shape, sample)
    for shape, samples in negatives.items():
        for sample in samples:
            assert shape_counts(sample)[shape] == 0, (shape, sample)


def test_this_file_matches_no_shape() -> None:
    assert not +shape_counts(Path(__file__).read_bytes())


def _git(*args: str) -> subprocess.CompletedProcess[bytes] | None:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None


def test_a_profile_copied_beside_the_template_is_ignored() -> None:
    """A real profile holds UUIDs and custom hosts no shape above catches yet."""

    listed = _git("ls-files", "-z", "--", "deploy/clients")
    if listed is None or listed.returncode != 0:
        pytest.skip("not a git checkout")
    assert listed.stdout.decode("utf-8").split("\0") == ["deploy/clients/example.env", ""]
    for name in ("client-alpha.env", "client-alpha.env.local", "client-alpha.json"):
        ignored = _git("check-ignore", "-q", "--no-index", f"deploy/clients/{name}")
        assert ignored is not None and ignored.returncode == 0, name
    template = _git("check-ignore", "-q", "--no-index", "deploy/clients/example.env")
    assert template is not None and template.returncode == 1
