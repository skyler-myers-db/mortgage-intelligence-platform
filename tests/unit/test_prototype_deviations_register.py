"""The declared-deviation register (docs/prototype-deviations.md) is enforced.

CLAUDE.md asks every warranted departure from the design contract in
``design_files/`` to be called out with the prototype line it departs from.
This test makes that call-out durable (2026-09-21 audit, report section 10
item 10; D-shell-deviations-d):

(i)   the three tables parse; ids are kebab-case and unique across ALL
      sections; every cite starts with ``design_files/`` and names a real file
      (and, with ``:line``, a real line);
(ii)  every Adopted row's code paths exist and carry the literal
      ``deviation:<id>`` inside a comment; its pinning test exists;
(iii) a Not-adopted row's pin is ``—``, an existing path, or
      ``pending: <path>`` (existence skipped only there);
(iv)  a Pending row has a unique id, a ``w5-<key>`` lane and existing code
      paths; its token is not required until the integrator promotes it;
(v)   the marker ratchet: prose that declares a deviation in frontend/src is
      either registered (a token within 5 lines) or counted in
      tests/fixtures/prototype_deviation_baseline.json, which only shrinks;
(vi)  the borrower peek sheet stays not adopted: neither ``unstable_mask``
      nor ``backgroundLocation`` appears under frontend/src
      (D-shell-deviations-c1).

Stdlib only.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REGISTER = REPO / "docs" / "prototype-deviations.md"
BASELINE = REPO / "tests" / "fixtures" / "prototype_deviation_baseline.json"
FRONTEND_SRC = REPO / "frontend" / "src"
DESIGN_FILES = REPO / "design_files"

MARKER_RE = re.compile(
    r"\b(declared|additive)\s+(prototype\s+)?(deviation|departure|extension)\b"
    r"|\b(deviation|departure)\s+from\b"
    r"|\bapp-added\b",
    re.IGNORECASE,
)
TOKEN_RE = re.compile(r"deviation:([a-z0-9]+(?:-[a-z0-9]+)*)")
ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LANE_RE = re.compile(r"^w5-[a-z0-9-]+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CLASSES = {"accessibility", "performance", "product", "safety", "architecture"}
NONE_CELLS = {"—", "-", ""}
PROXIMITY = 5
SCANNED_SUFFIXES = {".ts", ".tsx", ".css"}
RATCHET_HINT = "register it (row + token) or lower/raise nothing: the baseline only shrinks"

COLUMNS = (
    "id",
    "deviation",
    "prototype cite",
    "class",
    "code",
    "pinning test",
    "finding ids",
    "ruling date",
)


@dataclass(frozen=True)
class Row:
    section: str
    line: int
    cells: dict[str, str]

    @property
    def id(self) -> str:
        return self.cells["id"]

    def where(self) -> str:
        return f"docs/prototype-deviations.md:{self.line} ({self.section} row '{self.id}')"


def _section_for(heading: str) -> str | None:
    title = heading.strip().lower()
    if title.startswith("pending rows"):
        return "pending"
    if title.startswith("not adopted"):
        return "not_adopted"
    if title.startswith("adopted"):
        return "adopted"
    return None


def _split_row(line: str) -> list[str]:
    inner = line.strip()
    inner = inner[1:] if inner.startswith("|") else inner
    inner = inner[:-1] if inner.endswith("|") else inner
    return [cell.strip() for cell in inner.split("|")]


def parse_register(text: str) -> tuple[list[Row], set[str]]:
    rows: list[Row] = []
    sections_seen: set[str] = set()
    section: str | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        if line.startswith("## "):
            section = _section_for(line[3:])
            if section:
                sections_seen.add(section)
            continue
        if section is None or not line.lstrip().startswith("|"):
            continue
        cells = _split_row(line)
        if cells and (cells[0].lower() == "id" or all(re.fullmatch(r":?-{3,}:?", c) for c in cells)):
            continue
        columns = COLUMNS + (("lane",) if section == "pending" else ())
        assert len(cells) == len(columns), (
            f"docs/prototype-deviations.md:{number}: {section} rows have {len(columns)} "
            f"cells ({', '.join(columns)}), found {len(cells)} (a '|' inside a cell?)"
        )
        rows.append(Row(section, number, dict(zip(columns, cells, strict=True))))
    return rows, sections_seen


def _register() -> list[Row]:
    rows, _ = parse_register(REGISTER.read_text(encoding="utf-8"))
    return rows


def _entries(cell: str) -> list[str]:
    parts = re.split(r"<br\s*/?>|;", cell)
    return [p.strip().strip("`").strip() for p in parts if p.strip().strip("`").strip() not in NONE_CELLS]


def _code_paths(cell: str) -> list[str]:
    return [entry.split("::", 1)[0].strip() for entry in _entries(cell)]


def _comment_spans(text: str, suffix: str) -> list[tuple[int, int]]:
    patterns = [r"/\*.*?\*/", r"<!--.*?-->"]
    if suffix in {".ts", ".tsx", ".js", ".mjs", ".cjs"}:
        patterns.append(r"//[^\n]*")
    if suffix in {".py", ".sh", ".yml", ".yaml", ".toml"}:
        patterns.append(r"#[^\n]*")
    if suffix == ".sql":
        patterns.append(r"--[^\n]*")
    spans: list[tuple[int, int]] = []
    for pattern in patterns:
        spans.extend(m.span() for m in re.finditer(pattern, text, re.DOTALL))
    return spans


def token_in_comment(text: str, suffix: str, row_id: str) -> bool:
    spans = _comment_spans(text, suffix)
    for match in re.finditer(rf"deviation:{re.escape(row_id)}(?![a-z0-9-])", text):
        if any(start <= match.start() < end for start, end in spans):
            return True
    return False


def _design_file_names() -> list[str]:
    names = [p.relative_to(REPO).as_posix() for p in DESIGN_FILES.rglob("*") if p.is_file()]
    return sorted(names, key=len, reverse=True)


def check_cite(cite: str, design_names: list[str]) -> list[str]:
    problems: list[str] = []
    parts = _entries(cite)
    if not parts:
        return ["prototype cite is empty"]
    for part in parts:
        if not part.startswith("design_files/"):
            problems.append(f"cite '{part}' must start with design_files/")
            continue
        name = next((n for n in design_names if part.startswith(n)), None)
        if name is None:
            problems.append(f"cite '{part}' names no file under design_files/")
            continue
        span = re.match(r":(\d+)(?:-(\d+))?", part[len(name) :])
        if span:
            last = int(span.group(2) or span.group(1))
            length = len((REPO / name).read_text(encoding="utf-8", errors="replace").splitlines())
            if int(span.group(1)) < 1 or last > length or last < int(span.group(1)):
                problems.append(f"cite '{part}' is outside {name} (1-{length})")
    return problems


def _scanned_sources() -> list[Path]:
    return sorted(
        p
        for p in FRONTEND_SRC.rglob("*")
        if p.is_file() and p.suffix in SCANNED_SUFFIXES and ".test." not in p.name
    )


def unregistered_markers(text: str, registered_ids: set[str]) -> list[int]:
    lines = text.splitlines()
    token_lines = [
        index
        for index, line in enumerate(lines, start=1)
        if any(m.group(1) in registered_ids for m in TOKEN_RE.finditer(line))
    ]
    return [
        index
        for index, line in enumerate(lines, start=1)
        if MARKER_RE.search(line) and not any(abs(index - t) <= PROXIMITY for t in token_lines)
    ]


def ratchet_problems(actual: dict[str, list[int]], baseline: dict[str, int]) -> list[str]:
    problems: list[str] = []
    for path, lines in sorted(actual.items()):
        count = len(lines)
        pinned = baseline.get(path)
        sites = ", ".join(f"{path}:{n}" for n in lines)
        if pinned is None:
            problems.append(f"{sites}: unregistered deviation marker in an unlisted file; {RATCHET_HINT}")
        elif count > pinned:
            problems.append(f"{sites}: {count} unregistered markers, baseline {pinned}; {RATCHET_HINT}")
        elif count < pinned:
            problems.append(
                f"{path}: baseline {pinned} is stale (actual {count}); lower it to {count} "
                f"in tests/fixtures/prototype_deviation_baseline.json"
            )
    for path, pinned in sorted(baseline.items()):
        if path not in actual:
            problems.append(f"{path}: baseline {pinned} is stale (actual 0); delete the entry")
    return problems


# --------------------------------------------------------------------------
# (i) shape
# --------------------------------------------------------------------------


def test_register_has_the_three_sections_and_parses() -> None:
    rows, sections = parse_register(REGISTER.read_text(encoding="utf-8"))
    assert sections == {"adopted", "not_adopted", "pending"}, sections
    assert any(row.section == "adopted" for row in rows)
    assert any(row.section == "not_adopted" for row in rows)


def test_ids_are_kebab_case_and_unique_across_every_section() -> None:
    seen: dict[str, str] = {}
    problems: list[str] = []
    for row in _register():
        if not ID_RE.fullmatch(row.id):
            problems.append(f"{row.where()}: id must be kebab-case")
        if row.id in seen:
            problems.append(f"{row.where()}: id already used at {seen[row.id]}")
        seen[row.id] = row.where()
    assert problems == []


def test_every_row_cites_a_real_design_file_line_and_is_complete() -> None:
    names = _design_file_names()
    problems: list[str] = []
    for row in _register():
        problems.extend(f"{row.where()}: {p}" for p in check_cite(row.cells["prototype cite"], names))
        if row.cells["class"] not in CLASSES:
            problems.append(f"{row.where()}: class must be one of {sorted(CLASSES)}")
        if not DATE_RE.fullmatch(row.cells["ruling date"]):
            problems.append(f"{row.where()}: ruling date must be YYYY-MM-DD")
        if not row.cells["deviation"] or not row.cells["finding ids"]:
            problems.append(f"{row.where()}: deviation and finding ids are required")
    assert problems == []


# --------------------------------------------------------------------------
# (ii)-(iv) per-section rules
# --------------------------------------------------------------------------


def test_adopted_rows_name_existing_code_carrying_their_token_and_a_real_pin() -> None:
    problems: list[str] = []
    for row in (r for r in _register() if r.section == "adopted"):
        paths = _code_paths(row.cells["code"])
        if not paths:
            problems.append(f"{row.where()}: an adopted deviation names the code that implements it")
        for rel in paths:
            path = REPO / rel
            if not path.is_file():
                problems.append(f"{row.where()}: code path {rel} does not exist")
            elif not token_in_comment(path.read_text(encoding="utf-8"), path.suffix, row.id):
                problems.append(f"{row.where()}: {rel} has no 'deviation:{row.id}' comment token")
        pin = row.cells["pinning test"].strip("`")
        if pin in NONE_CELLS or pin.startswith("pending:") or not (REPO / pin).is_file():
            problems.append(f"{row.where()}: pinning test '{pin}' must be an existing path")
    assert problems == []


def _pin_problems(row: Row) -> list[str]:
    pin = row.cells["pinning test"].strip("`")
    if pin in NONE_CELLS:
        return []
    if pin.startswith("pending:"):
        return [] if pin[len("pending:") :].strip() else [f"{row.where()}: 'pending:' names no path"]
    return [] if (REPO / pin).is_file() else [f"{row.where()}: pinning test {pin} does not exist"]


def test_not_adopted_and_pending_rows_pin_with_a_dash_a_path_or_pending() -> None:
    problems: list[str] = []
    for row in (r for r in _register() if r.section != "adopted"):
        problems.extend(_pin_problems(row))
        for rel in _code_paths(row.cells["code"]):
            if not (REPO / rel).is_file():
                problems.append(f"{row.where()}: code path {rel} does not exist")
        if row.section == "pending" and not LANE_RE.fullmatch(row.cells["lane"]):
            problems.append(f"{row.where()}: lane must match w5-<key>")
    assert problems == []


def test_every_token_in_frontend_src_names_a_registered_row() -> None:
    ids = {row.id for row in _register()}
    problems = [
        f"{path.relative_to(REPO).as_posix()}:{number}: deviation:{m.group(1)} has no register row"
        for path in _scanned_sources()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        for m in TOKEN_RE.finditer(line)
        if m.group(1) not in ids
    ]
    assert problems == []


# --------------------------------------------------------------------------
# (v) marker ratchet
# --------------------------------------------------------------------------


def test_unregistered_deviation_markers_only_shrink() -> None:
    ids = {row.id for row in _register()}
    actual: dict[str, list[int]] = {}
    for path in _scanned_sources():
        lines = unregistered_markers(path.read_text(encoding="utf-8"), ids)
        if lines:
            actual[path.relative_to(REPO).as_posix()] = lines
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert all(isinstance(v, int) and v > 0 for v in baseline.values()), "counts are positive ints"
    assert ratchet_problems(actual, baseline) == []


def test_the_ratchet_catches_new_stale_and_registered_markers() -> None:
    # Self-test on synthetic text: the regex is case-insensitive, a token
    # within 5 lines registers a marker, and a stale baseline fails.
    text = "a\n/* Declared departure here */\nb\n/* APP-ADDED ELEMENT */\n"
    assert unregistered_markers(text, set()) == [2, 4]
    registered = "/* deviation:demo-row */\n" + "x\n" * 4 + "/* additive prototype extension */\n"
    assert unregistered_markers(registered, {"demo-row"}) == []
    assert unregistered_markers(registered, set()) == [6]
    assert unregistered_markers("\n" * 6 + registered, {"demo-row"}) == []
    far = "/* deviation:demo-row */\n" + "x\n" * 5 + "/* deviation from the prototype */\n"
    assert unregistered_markers(far, {"demo-row"}) == [7]
    assert ratchet_problems({"f.css": [3]}, {}) and ratchet_problems({"f.css": [3, 9]}, {"f.css": 1})
    assert ratchet_problems({"f.css": [3]}, {"f.css": 2}) and ratchet_problems({}, {"f.css": 1})
    assert ratchet_problems({"f.css": [3]}, {"f.css": 1}) == []


def test_the_token_must_sit_in_a_comment() -> None:
    assert token_in_comment("a { color: red; } /* deviation:x-y */", ".css", "x-y")
    assert token_in_comment("const a = 1; // deviation:x-y", ".ts", "x-y")
    assert token_in_comment("{/* deviation:x-y */}", ".tsx", "x-y")
    assert not token_in_comment("const deviation = 'deviation:x-y';", ".css", "x-y")
    assert not token_in_comment("/* deviation:x-yz */", ".css", "x-y")


# --------------------------------------------------------------------------
# (vi) borrower peek sheet stays not adopted (D-shell-deviations-c1)
# --------------------------------------------------------------------------


def test_no_masked_url_peek_sheet_under_frontend_src() -> None:
    hits = [
        f"{path.relative_to(REPO).as_posix()}:{number}"
        for path in sorted(FRONTEND_SRC.rglob("*"))
        if path.is_file() and path.suffix in {".ts", ".tsx", ".js", ".css", ".json"}
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if "unstable_mask" in line or "backgroundLocation" in line
    ]
    assert hits == [], (
        "the borrower peek sheet is not adopted (docs/prototype-deviations.md, "
        f"borrower-peek-sheet): {hits}"
    )
