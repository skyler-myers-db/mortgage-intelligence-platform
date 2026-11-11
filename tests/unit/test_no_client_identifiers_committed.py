"""No client identifier is committed: a ratchet held at zero (M1-PR4).

Client identifiers belong in GitHub Environments and ``.env.local``, never
in the tree (tools/clients/schema.py): workspace hosts and ids, App URLs,
Genie space, warehouse, deployment, conversation and job ids, service
principal, account and metastore ids, and personal emails. The identifier
purge (M1-PR4) replaced every one the tree held with a symbolic placeholder
(``<workspace-host>``, ``<app-host>``, ``<genie-space-id>``) or a required
input. This test counts eight identifier shapes in every tracked text file
and fails on any occurrence.

The shapes are generic, so they also catch a new client's identifiers, and
this file never names a client value: each positive control is assembled at
run time, so no literal here matches a shape.

What a shape may still match, and why:

- a synthetic value: an id-shaped value with a run, a sequence or a period
  in it (``11111111-1111-4111-8111-111111111111``, ``0123456789abcdef``) is
  hand-made. ``is_synthetic`` decides, and
  ``test_random_ids_are_almost_never_synthetic`` bounds how often a real,
  random id would pass it;
- an example-domain address (RFC 2606 / 6761; ``.local`` is not one, since
  directory domains use it), or the userinfo of a URL
  (``https://user:pw@host``), which is not an address. A userinfo whose
  authority holds a second ``@`` (``postgresql://<user>@<domain>@<host>``)
  is an address and is counted, as is a URL-encoded one (``%40``);
- a pinned digest in a test: a value labelled as a hash
  (``question_hash``, ``sql_hash``, ``hash:``) or held by a ``GOLDEN_``
  constant is what the code computes from synthetic inputs;
- an entry of ``ALLOWED``, each scoped to its files and with its reason.

Every tracked text file is scanned for every shape, lockfiles and manifests
included: their hashes are 64-hex or base64, which no shape matches, and
none held a match at the purge. A value one of them ever needs goes in
``ALLOWED`` by value and path, never as a file exemption.

When this test fails it lists every file and line holding the shape.
Replace the value with a symbolic placeholder or an example-domain value;
never widen an exemption to keep a real value.
"""

from __future__ import annotations

import os
import random
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Each id-bearing shape names its id ``id`` so the synthetic test can judge it.
SHAPES: dict[str, re.Pattern[bytes]] = {
    # An AWS dbc- host, an Azure adb- host or a GCP numeric host. The AWS and
    # Azure deployment labels (dbc-<8hex>-<4hex>, adb-<id>) name the workspace
    # without their domain too.
    "workspace_host": re.compile(
        rb"(?i)(?<![a-z0-9-])(?:dbc-(?P<id>[0-9a-f]{8}(?:-[0-9a-f]{4})?)(?:\.cloud\.databricks\.com)?"
        rb"|adb-(?P<azure>[0-9]{6,19})(?:\.[0-9]{1,2}\.azuredatabricks\.net)?"
        rb"|(?P<gcp>[0-9]{6,19})\.[0-9]\.gcp\.databricks\.com)(?![a-z0-9-])"
    ),
    # A Databricks App URL: <app>-<workspace id>.<cloud>.databricksapps.com.
    "app_url": re.compile(
        rb"(?i)(?<![a-z0-9-])[a-z0-9-]+-(?P<id>[0-9]{6,19})\.(?:[0-9]{1,2}\.)?(?:aws|azure|gcp)"
        rb"\.databricksapps\.com"
    ),
    # A workspace id in a workspace URL's ``?o=`` query.
    "workspace_query": re.compile(rb"[?&]o=(?P<id>[0-9]{6,19})(?![0-9])"),
    # An email-shaped address, plain or URL-encoded (``%40``, as a deep link's
    # query carries it); reserved example domains are dropped below.
    "email": re.compile(
        rb"[A-Za-z0-9._%+-]+(?:@|%40)(?P<domain>[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})"
    ),
    # A bare 16-digit id (a workspace, SCIM or run id); not a decimal's fraction.
    "digits16": re.compile(rb"(?<![0-9A-Za-z])(?<![0-9]\.)(?P<id>[0-9]{16})(?![0-9A-Za-z])"),
    # A 16-hex id (a warehouse id) with at least one letter, so digits16 keeps
    # the rest; either case, as Genie and the UI both print it.
    "hex16": re.compile(rb"(?i)(?<![0-9a-z])(?P<id>(?=[0-9a-f]{0,15}[a-f])[0-9a-f]{16})(?![0-9a-z])"),
    # A 32-hex id, either case: a Genie space, conversation, message or App deployment id.
    "hex32": re.compile(rb"(?i)(?<![0-9a-z])(?P<id>(?=[0-9a-f]{0,31}[a-f])[0-9a-f]{32})(?![0-9a-z])"),
    # A UUID: a service principal's application id, an account or a metastore id.
    "uuid": re.compile(
        rb"(?i)(?<![0-9a-z])(?P<id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?![0-9a-z])"
    ),
}
# Zero everywhere since the purge. Never raise one.
BASELINE = dict.fromkeys(SHAPES, 0)

# RFC 2606 / 6761 reserved names: an address there names nobody. Not
# ``.local``: directory (Active Directory) domains use it for real people.
_EXAMPLE_DOMAIN = re.compile(
    rb"(?i)(?:^|\.)(?:example|invalid|test|localhost)$|(?:^|\.)example\.(?:com|net|org)$"
)
# ``scheme://user@`` or ``scheme://user:password@``: a URL's userinfo, not an address.
_URL_USERINFO = re.compile(rb"://(?:[A-Za-z0-9._%+-]+:)?$")
# ...unless the authority goes on to another ``@``: then the match was the
# userinfo's own address (``postgresql://<user>@<domain>[:<secret>]@<host>``).
_USERINFO_CONTINUES = re.compile(rb"[^\s/?#@]*@")
_HEX = "0123456789abcdef"

# The shapes a hex digest takes; a pinned digest never relaxes a UUID or a number.
_DIGEST_SHAPES = frozenset({"hex16", "hex32"})
# Test sources, where a labelled hash or a GOLDEN_ pin is a computed digest.
_TEST_PATH = re.compile(r"^(?:tests/|frontend/tests/|frontend/src/.*\.test(?:-support)?\.tsx?$)")
_PINNED_DIGEST = re.compile(
    rb"(?:\b_?GOLDEN_[A-Z0-9_]*\s*=\s*[\"'][A-Za-z_-]*"
    rb"|\b(?:question|sql|criteria|statement)_hash\W{0,8}"
    rb"|\bhash\W{1,4}"
    rb"|\btrusted-sql-)$"
)


@dataclass(frozen=True)
class Allowed:
    """One reviewed value a shape matches, allowed only in ``paths`` (prefixes)."""

    shape: str
    parts: tuple[str, ...]  # joined at run time, so this file matches no shape
    paths: tuple[str, ...]
    reason: str

    @property
    def value(self) -> bytes:
        return "".join(self.parts).encode()


ALLOWED: tuple[Allowed, ...] = (
    Allowed(
        "email",
        ("security", "@", "entrada", ".ai"),
        ("",),
        "the published vulnerability-disclosure mailbox (SECURITY.md): a role address",
    ),
    Allowed(
        "email",
        ("git", "@", "github", ".com"),
        ("",),
        "the SSH user of GitHub's public remote (git@host:owner/repo), not an address",
    ),
    Allowed(
        "email",
        ("jane", "@", "realbank", ".com"),
        ("tests/unit/test_e2e_fixture_contract.py",),
        "the synthetic-data guard's non-vacuity probe: it must be a non-example domain",
    ),
    Allowed(
        "uuid",
        ("df508632", "-9ce2-4a17-bce1-", "eb3991fbcdf5"),
        ("backend/services/genie_feedback.py",),
        "the uuid5 namespace of Genie feedback request ids: a constant of this "
        "product, and changing it would change every live request id",
    ),
    Allowed(
        "uuid",
        ("0f8fad5b", "-d9cb-469f-a165-", "70867728950e"),
        ("frontend/tests/e2e/fixture/",),
        "a published documentation example GUID in fixture wire data the "
        "fixture VRT baselines render",
    ),
    Allowed(
        "uuid",
        ("16fd2706", "-8baf-433b-82eb-", "8c7fada847da"),
        ("frontend/tests/e2e/fixture/data/approvalRequests.ts",),
        "the Python uuid documentation's example, in fixture wire data the VRT baselines render",
    ),
    Allowed(
        "uuid",
        ("7c9e6679", "-7425-40de-944b-", "e07cc4c9a0a1"),
        ("frontend/tests/e2e/fixture/data/approvalRequests.ts",),
        "a hand-made audit id in fixture wire data the VRT baselines render",
    ),
    *(
        Allowed(
            "uuid",
            ("3b7c9f2e", "-5a41-4d8e-9c06-", "2f1a8b7d4e9", last),
            ("frontend/tests/e2e/fixture/",),
            "a hand-made audit id in fixture wire data the VRT baselines render",
        )
        for last in ("0", "1")
    ),
)


def is_synthetic(value: str) -> bool:
    """A hand-made id: few distinct characters, a run, a period or a sequence.

    Sequences count in the whole value and in its even and odd characters
    (``0a1b2c3d``), stepping up or down through the hex digits.
    """

    s = value.lower().replace("-", "")
    if len(set(s)) <= 4 or re.search(r"(.)\1{5,}", s):
        return True
    for period in range(1, 9):
        if len(s) >= 2 * period and all(s[i] == s[i % period] for i in range(len(s))):
            return True
    for step in (1, 15):
        for sub, need in ((s, 6), (s[0::2], 5), (s[1::2], 5)):
            run = best = 1
            for a, b in zip(sub, sub[1:], strict=False):
                run = run + 1 if (_HEX.index(b) - _HEX.index(a)) % 16 == step else 1
                best = max(best, run)
            if best >= need:
                return True
    return False


def _id_of(match: re.Match[bytes]) -> str | None:
    for group in ("id", "azure", "gcp"):
        if group in match.re.groupindex and match.group(group) is not None:
            return match.group(group).decode()
    return None


def _allowed(rel: str, shape: str, value: bytes) -> bool:
    return any(
        entry.shape == shape and value == entry.value and rel.startswith(entry.paths)
        for entry in ALLOWED
    )


def shape_hits(data: bytes, rel: str = "") -> list[tuple[str, int, bytes]]:
    """(shape, line, value) for every client-identifier-shaped value in ``data``."""

    hits: list[tuple[str, int, bytes]] = []
    in_tests = bool(_TEST_PATH.match(rel))
    for shape, pattern in SHAPES.items():
        for match in pattern.finditer(data):
            value = match.group(0)
            line_start = data.rfind(b"\n", 0, match.start()) + 1
            before = data[line_start : match.start()]
            if shape == "email" and (
                _EXAMPLE_DOMAIN.search(match.group("domain"))
                or (_URL_USERINFO.search(before) and not _USERINFO_CONTINUES.match(data, match.end()))
            ):
                continue
            ident = _id_of(match)
            if ident is not None and is_synthetic(ident):
                continue
            if in_tests and shape in _DIGEST_SHAPES and _PINNED_DIGEST.search(before):
                continue
            if _allowed(rel, shape, value):
                continue
            hits.append((shape, data.count(b"\n", 0, match.start()) + 1, value))
    return hits


def shape_counts(data: bytes, rel: str = "") -> Counter[str]:
    """Occurrences of each shape in one file's bytes."""

    return Counter(shape for shape, _line, _value in shape_hits(data, rel))


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


@pytest.fixture(scope="module")
def tree_hits() -> dict[str, list[str]]:
    """Per shape, ``path:line`` of every occurrence in the tracked tree."""

    per_shape: dict[str, list[str]] = {shape: [] for shape in SHAPES}
    for rel in _tracked_files():
        if Path(rel).suffix.lower() in _BINARY_SUFFIXES:
            continue
        try:
            data = (ROOT / rel).read_bytes()
        except OSError:
            continue
        if b"\0" in data:
            continue
        for shape, line, _value in shape_hits(data, rel):
            per_shape[shape].append(f"{rel}:{line}")
    return per_shape


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_no_shape_occurs_in_the_tree(shape: str, tree_hits: dict[str, list[str]]) -> None:
    places = tree_hits[shape]
    listing = "\n".join(f"  {place}" for place in places)
    assert len(places) <= BASELINE[shape], (
        f"{len(places)} {shape} occurrences against a baseline of {BASELINE[shape]}: a client "
        "identifier was committed. Replace it with a symbolic placeholder or an "
        f"example-domain value. Where:\n{listing}"
    )


def test_baselines_are_zero_and_cover_exactly_the_shapes() -> None:
    assert set(BASELINE) == set(SHAPES)
    assert set(BASELINE.values()) == {0}


def _j(*parts: str) -> bytes:
    return "".join(parts).encode()


# Random ids drawn at run time from a fixed seed, so no literal here matches
# a shape and none names a real object.
_RNG = random.Random(41)
_WS = "".join(_RNG.choice("123456789") for _ in range(16))
_HEX16, _HEX32, _HOST8, _HOST4 = ("".join(_RNG.choice(_HEX) for _ in range(n)) for n in (16, 32, 8, 4))
_UUID = "-".join((_HEX32[:8], _HEX32[8:12], "4" + _HEX32[13:16], "9" + _HEX32[17:20], _HEX32[20:]))


def test_each_shape_has_a_positive_and_a_negative_control() -> None:
    assert not any(map(is_synthetic, (_WS, _HEX16, _HEX32, _UUID, _HOST8 + _HOST4)))
    positives = {
        "workspace_host": [
            _j("dbc-", _HOST8, "-", _HOST4, ".cloud.databricks.com"),
            _j("adb-", _WS, ".", "7", ".azuredatabricks.net"),
            _j(_WS, ".", "2", ".gcp.databricks.com"),
            # The bare deployment labels, as a deploy log or a report names them.
            _j("deployed to dev (`dbc-", _HOST8, "-", _HOST4, "`)"),
            _j("dbc-", _HOST8.upper(), " "),
            _j("workspace adb-", _WS, ","),
        ],
        "app_url": [_j("mip-app-", _WS, ".aws.databricksapps.com"), _j("x-", _WS, ".11.azure.databricksapps.com")],
        "workspace_query": [_j("/?o=", _WS), _j("&o=", _WS[:15])],
        "email": [
            _j("someone", "@", "corp-alpha", ".com"),
            _j("mailto:", "a.b", "@", "lender", ".ai"),
            # A directory domain, a URL-encoded address and an address as a URL's userinfo.
            _j("first.last", "@", "corp", ".local"),
            _j("/audit-ledger?audit_actor=", "first.last", "%40", "corp", ".com&x=1"),
            _j("postgresql://", "first.last", "@", "corp", ".com", "@", "inst", ".database.example.com:5432/db"),
            _j("postgresql://", "first.last", "@", "corp", ".com", ":token", "@", "inst", ".example.com/db"),
            _j("postgresql://", "first.last", "%40", "corp", ".com", ":token", "@", "inst", ".example.com/db"),
        ],
        "digits16": [_j("workspace ", _WS), _j("id=", _WS, ";")],
        "hex16": [_j("warehouses/", _HEX16), _j('"', _HEX16, '"'), _j('id="', _HEX16.upper(), '"')],
        "hex32": [_j("space ", _HEX32), _j("/rooms/", _HEX32, "?"), _j('conversation_id="', _HEX32.upper(), '"')],
        "uuid": [_j("sp ", _UUID), _j("agent-trace-", _UUID)],
    }
    negatives = {
        "workspace_host": [
            b"https://dbc-test.cloud.databricks.com",
            b"accounts.cloud.databricks.com",
            _j("dbc-", "1234", "5678", "-abcd", ".cloud.databricks.com"),
            _j("dbc-", "1234", "5678", "-abcd"),
            _j("adb-", "1234567890", "123456", ".7.azuredatabricks.net"),
            _j("dbc-", _HOST8, "-", _HOST4, "-extra"),
        ],
        "app_url": [b"https://mip-app-test.aws.databricksapps.com", b"mip-app.databricksapps.com", b"<app-host>"],
        "workspace_query": [b"?o=12345", b"?o=<workspace-id>"],
        "email": [
            b"admin@summit.example",
            b"user@example.com",
            b"jane@ops.example.org",
            b"x@y.invalid",
            _j("https://user", ":pw", "@", "dbc-x", ".cloud.databricks.com"),
            _j("https://user", "@", "workspace", ".cloud.databricks.com"),
            _j("postgresql://user", ":pw", "@", "inst", ".database.cloud.databricks.com:5432/db"),
            _j("?audit_actor=", "jane", "%40", "summit", ".example"),
            _j("http://", "dev", "@", "app", ".localhost:8000"),
        ],
        "digits16": [_j("x", _WS), _j(_WS, "1"), _j("0.", _WS), _j("1", "0" * 15), b"<workspace-id>"],
        "hex16": [
            _j("0123", "4567", "89ab", "cdef"),
            _j("a0b1", "c2d3", "e4f5", "a6b7"),
            _j(_HEX16, "00"),
            _j("1234", "5678", "90AB", "CDEF"),
        ],
        "hex32": [
            _j("0123456789abcdef", "0123456789abcdef"),
            _j("01f1", "0" * 27, "1"),
            _j(_HEX32, "a"),
            _j("01F1C0DE", "0" * 23, "1"),
        ],
        "uuid": [
            _j("11111111", "-1111-4111-8111-", "111111111111"),
            _j("0a1b2c3d", "-4e5f-4a6b-8c7d-", "9e0f1a2b3c4d"),
            _j("00000000", "-0000-4000-8000-", "0000000000aa"),
        ],
    }
    assert set(positives) == set(negatives) == set(SHAPES)
    for shape, samples in positives.items():
        for sample in samples:
            assert shape_counts(sample)[shape] == 1, (shape, sample)
    for shape, samples in negatives.items():
        for sample in samples:
            assert shape_counts(sample)[shape] == 0, (shape, sample)


def test_pinned_digests_are_exempt_only_in_tests() -> None:
    golden = _j('GOLDEN_KEY = "actor_', _HEX16, '"')
    labelled = _j('"question_hash": "', _HEX16, '"')
    for sample in (golden, labelled, _j("hash: '", _HEX16, "'"), _j('"trusted-sql-', _HEX16, '"')):
        assert shape_counts(sample, "tests/unit/test_x.py")["hex16"] == 0, sample
        assert shape_counts(sample, "frontend/src/lib/x.test.ts")["hex16"] == 0, sample
        assert shape_counts(sample, "docs/audit.md")["hex16"] == 1, sample
        assert shape_counts(sample, "backend/services/x.py")["hex16"] == 1, sample
    # A label never relaxes the other shapes, nor an unlabelled id beside it.
    assert shape_counts(_j('question_hash = "', _UUID, '"'), "tests/unit/test_x.py")["uuid"] == 1
    assert shape_counts(_j('id = "', _HEX16, '"'), "tests/unit/test_x.py")["hex16"] == 1


@pytest.mark.parametrize(
    "rel",
    [
        "uv.lock",
        "requirements-dev.lock",
        "frontend/package-lock.json",
        "backend/resources/lineage_manifest.json",
        "frontend/src/geo/zcta/manifest.json",
        "docs/release-manifest.json",
        "docs/notes.md",
    ],
)
def test_lockfiles_and_manifests_are_scanned_for_every_shape(rel: str) -> None:
    """No file is exempt by name: a release manifest is where deployment ids would sit."""

    data = _j(_HEX32, " ", _UUID, " ", _HEX16, " ", _WS, " dbc-", _HOST8, ".cloud.databricks.com")
    every = Counter({"workspace_host": 1, "hex32": 1, "uuid": 1, "hex16": 1, "digits16": 1})
    assert shape_counts(data, rel) == every


def test_each_allowed_value_is_scoped_and_still_matches_its_shape() -> None:
    for entry in ALLOWED:
        assert entry.reason and entry.paths, entry
        assert shape_counts(entry.value, "elsewhere/x.txt")[entry.shape] == (
            0 if entry.paths == ("",) else 1
        ), entry
        assert shape_counts(entry.value, entry.paths[0] or "x.txt")[entry.shape] == 0, entry
    # Exact values only: a longer lookalike of an allowed address is never allowed.
    lookalike = _j("git", "@", "github", ".com", ".evil")
    assert shape_counts(lookalike, "tests/unit/test_x.py")["email"] == 1


def test_random_ids_are_almost_never_synthetic() -> None:
    """The synthetic test lets fewer than 1 in 200 random ids through."""

    rng = random.Random(20261029)
    for width, alphabet in ((16, "0123456789"), (16, _HEX), (32, _HEX)):
        values = ["".join(rng.choice(alphabet) for _ in range(width)) for _ in range(4000)]
        assert sum(map(is_synthetic, values)) < 20, (width, alphabet)
    uuids = ["-".join((h[:8], h[8:12], h[12:16], h[16:20], h[20:])) for h in (f"{rng.getrandbits(128):032x}" for _ in range(4000))]
    assert sum(map(is_synthetic, uuids)) < 20


def test_this_file_matches_no_shape() -> None:
    assert not shape_hits(Path(__file__).read_bytes(), "tests/unit/test_no_client_identifiers_committed.py")


def _git(*args: str) -> subprocess.CompletedProcess[bytes] | None:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None


def test_a_profile_copied_beside_the_template_is_ignored() -> None:
    """A real profile holds identifying values; only the template is tracked."""

    listed = _git("ls-files", "-z", "--", "deploy/clients")
    if listed is None or listed.returncode != 0:
        pytest.skip("not a git checkout")
    assert listed.stdout.decode("utf-8").split("\0") == ["deploy/clients/example.env", ""]
    for name in ("client-alpha.env", "client-alpha.env.local", "client-alpha.json"):
        ignored = _git("check-ignore", "-q", "--no-index", f"deploy/clients/{name}")
        assert ignored is not None and ignored.returncode == 0, name
    template = _git("check-ignore", "-q", "--no-index", "deploy/clients/example.env")
    assert template is not None and template.returncode == 1
