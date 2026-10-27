"""docs/accessibility/VPAT.md stays a complete, honest WCAG 2.2 report.

The report (audit a11y-08) rates every WCAG 2.2 Level A and AA success
criterion, plus 4.1.1 with its WCAG 2.2 note, with one of the four VPAT
terms; its summary table is the count of those rows; every file it cites
exists, so evidence cannot rot into a dangling claim; every virtual
screen-reader transcript is cited; and the Evaluation Methods section keeps
saying which assistive-technology passes were NOT done.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VPAT = ROOT / "docs" / "accessibility" / "VPAT.md"
TRANSCRIPTS = ROOT / "frontend" / "src" / "test" / "screenReader" / "transcripts"

TERMS = ("Supports", "Partially Supports", "Does Not Support", "Not Applicable")

LEVEL_A = {
    "1.1.1": "Non-text Content",
    "1.2.1": "Audio-only and Video-only (Prerecorded)",
    "1.2.2": "Captions (Prerecorded)",
    "1.2.3": "Audio Description or Media Alternative (Prerecorded)",
    "1.3.1": "Info and Relationships",
    "1.3.2": "Meaningful Sequence",
    "1.3.3": "Sensory Characteristics",
    "1.4.1": "Use of Color",
    "1.4.2": "Audio Control",
    "2.1.1": "Keyboard",
    "2.1.2": "No Keyboard Trap",
    "2.1.4": "Character Key Shortcuts",
    "2.2.1": "Timing Adjustable",
    "2.2.2": "Pause, Stop, Hide",
    "2.3.1": "Three Flashes or Below Threshold",
    "2.4.1": "Bypass Blocks",
    "2.4.2": "Page Titled",
    "2.4.3": "Focus Order",
    "2.4.4": "Link Purpose (In Context)",
    "2.5.1": "Pointer Gestures",
    "2.5.2": "Pointer Cancellation",
    "2.5.3": "Label in Name",
    "2.5.4": "Motion Actuation",
    "3.1.1": "Language of Page",
    "3.2.1": "On Focus",
    "3.2.2": "On Input",
    "3.2.6": "Consistent Help",
    "3.3.1": "Error Identification",
    "3.3.2": "Labels or Instructions",
    "3.3.7": "Redundant Entry",
    "4.1.1": "Parsing",
    "4.1.2": "Name, Role, Value",
}
LEVEL_AA = {
    "1.2.4": "Captions (Live)",
    "1.2.5": "Audio Description (Prerecorded)",
    "1.3.4": "Orientation",
    "1.3.5": "Identify Input Purpose",
    "1.4.3": "Contrast (Minimum)",
    "1.4.4": "Resize Text",
    "1.4.5": "Images of Text",
    "1.4.10": "Reflow",
    "1.4.11": "Non-text Contrast",
    "1.4.12": "Text Spacing",
    "1.4.13": "Content on Hover or Focus",
    "2.4.5": "Multiple Ways",
    "2.4.6": "Headings and Labels",
    "2.4.7": "Focus Visible",
    "2.4.11": "Focus Not Obscured (Minimum)",
    "2.5.7": "Dragging Movements",
    "2.5.8": "Target Size (Minimum)",
    "3.1.2": "Language of Parts",
    "3.2.3": "Consistent Navigation",
    "3.2.4": "Consistent Identification",
    "3.3.3": "Error Suggestion",
    "3.3.4": "Error Prevention (Legal, Financial, Data)",
    "3.3.8": "Accessible Authentication (Minimum)",
    "4.1.3": "Status Messages",
}

_ROW = re.compile(r"^\| (\d+\.\d+\.\d+) (.+?) \(Level (AA|A)\) \| ([^|]+?) \| (.+) \|$")
_CITED_FILE = re.compile(r"`([A-Za-z0-9_./*-]+\.(?:tsx?|txt|md|json|mjs|css|ya?ml|html|py))`")
_SEARCH_ROOTS = ("frontend/src", "frontend/tests", "docs", "tools", "tests", ".github")


def _text() -> str:
    return VPAT.read_text(encoding="utf-8")


def rows(text: str) -> list[tuple[str, str, str, str, str]]:
    """(number, title, level, term, remarks) for every criterion row."""
    found: list[tuple[str, str, str, str, str]] = []
    for line in text.splitlines():
        match = _ROW.match(line)
        if match:
            number, title, level, term, remarks = match.groups()
            found.append((number, title, level, term, remarks))
    return found


def _section(text: str, heading: str) -> str:
    start = text.index(f"## {heading}")
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else len(text)]


def test_every_wcag_22_a_and_aa_criterion_has_exactly_one_rated_row() -> None:
    found = rows(_text())

    by_level: dict[str, dict[str, str]] = {"A": {}, "AA": {}}
    for number, title, level, term, remarks in found:
        assert number not in by_level[level], f"{number} is rated twice"
        by_level[level][number] = title
        assert term in TERMS, f"{number}: {term!r} is not a VPAT term for Level A/AA"
        assert len(remarks.strip()) >= 30, f"{number}: every rating carries its evidence"
    assert by_level["A"] == LEVEL_A
    assert by_level["AA"] == LEVEL_AA


def test_the_summary_table_is_the_count_of_the_rows() -> None:
    text = _text()
    counts = {level: Counter(term for _, _, row_level, term, _ in rows(text) if row_level == level) for level in ("A", "AA")}

    summary = _section(text, "Summary")
    for level, label in (("A", r"A \(32 rows, 4\.1\.1 included\)"), ("AA", r"AA \(24 rows\)")):
        match = re.search(rf"^\| {label} \| (\d+) \| (\d+) \| (\d+) \| (\d+) \|$", summary, re.MULTILINE)
        assert match, f"no summary row for Level {level}"
        assert tuple(int(n) for n in match.groups()) == tuple(counts[level][term] for term in TERMS)


def _resolves(cited: str) -> bool:
    if "/" in cited:
        return (ROOT / cited).exists() or any(ROOT.glob(cited))
    return any(any((ROOT / base).rglob(cited)) for base in _SEARCH_ROOTS)


def test_every_cited_file_exists() -> None:
    cited = sorted(set(_CITED_FILE.findall(_text())))

    assert len(cited) > 30, "non-vacuity: the report cites its evidence"
    assert [path for path in cited if not _resolves(path)] == []


def test_every_screen_reader_transcript_is_cited() -> None:
    goldens = sorted(path.name for path in TRANSCRIPTS.glob("*.txt"))
    text = _text()

    assert len(goldens) >= 7
    assert [name for name in goldens if f"`{name}`" not in text] == []


def test_the_evaluation_methods_say_what_was_not_done() -> None:
    methods = _section(_text(), "Evaluation methods used")
    not_done = methods[methods.index("What was **not** done") :]

    # Each is its own bullet, so deleting one is caught even where the word
    # survives in another bullet's prose.
    for bullet in (
        "- **No NVDA or JAWS pass on Windows.**",
        "- **No live VoiceOver pass.**",
        "- **No testing with people who use assistive technology.**",
    ):
        assert bullet in not_done, f"the not-done list must keep {bullet!r}"
    assert "U+2212" in methods and "minus" in methods
    assert "self-assessment" in _text()
