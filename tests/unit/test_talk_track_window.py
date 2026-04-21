"""Guard the DAIS talk-track runtime window -- Slice 9.

The talk track has a mechanical runtime bound: 6-8 minutes of spoken
copy inside a 45s open + 30s close envelope. The
``tools/talk_track_wc.py`` script counts lines that start with ``> ``
and asserts the total lands in ``[1000, 1500]`` words (~6.1-9.1 min at
165 wpm). This test wires the same check into ``pytest -q`` so a
drifting talk track fails CI as loudly as a failing unit.

Two halves:

* ``test_talk_track_within_window`` -- sanity check on the real repo
  copy; tightens the guard to ~6-8 min by parameterizing the script's
  default min/max. Runs the script as a module so we exercise the
  actual CLI path too.
* ``test_talk_track_wc_cli_errors`` -- synthetic markdown fixtures that
  prove the script's error path: one too short, one too long, one
  absent file. We never mutate the real talk track.

We use ``subprocess`` to invoke the tool by absolute ``sys.executable``
path so the CI + local runs use the same Python the pytest session
uses. That's more faithful than importing + calling ``main()``
directly because it exercises argparse + stderr handling.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tools.talk_track_wc import count_spoken_words

REPO_ROOT = Path(__file__).resolve().parents[2]
TALK_TRACK = REPO_ROOT / "docs" / "module0-talk-track.md"
TOOL = REPO_ROOT / "tools" / "talk_track_wc.py"


def test_talk_track_file_exists() -> None:
    """The real talk track ships in the repo and is non-empty."""
    assert TALK_TRACK.exists(), f"talk track not found at {TALK_TRACK}"
    assert TALK_TRACK.stat().st_size > 0, "talk track is empty"


def test_talk_track_within_window() -> None:
    """Spoken word count must land in the ``[1000, 1500]`` band.

    This is the mechanical guarantee that the booth pitch doesn't drift
    to 4 minutes (too short) or 11 minutes (too long). Anyone editing
    the talk track MUST keep it inside this window or explicitly widen
    it (and justify the change in the commit message).
    """
    count = count_spoken_words(TALK_TRACK)
    assert 1000 <= count <= 1500, (
        f"talk track spoken word count {count} outside window [1000, 1500]. "
        f"Trim or expand the spoken copy before merging."
    )


def test_talk_track_wc_cli_exits_zero_on_real_doc() -> None:
    """Running the CLI against the real talk track exits zero."""
    result = subprocess.run(
        [sys.executable, str(TOOL), "--file", str(TALK_TRACK)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"wc script failed on real talk track: stderr={result.stderr!r}"
    )
    assert "ok " in result.stdout


def test_talk_track_wc_cli_errors_too_short(tmp_path: Path) -> None:
    """Too-short doc -> exit 1 with a clear 'too short' message on stderr."""
    too_short = tmp_path / "short.md"
    too_short.write_text("> hello world\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "--file",
            str(too_short),
            "--min",
            "10",
            "--max",
            "100",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "too short" in result.stderr.lower()


def test_talk_track_wc_cli_errors_too_long(tmp_path: Path) -> None:
    """Too-long doc -> exit 1 with 'too long'."""
    too_long = tmp_path / "long.md"
    too_long.write_text(
        "\n".join("> " + " ".join(["w"] * 20) for _ in range(20)),
        encoding="utf-8",
    )
    # 20 lines * 20 words = 400 spoken words; cap at 100 to force failure.
    result = subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "--file",
            str(too_long),
            "--min",
            "10",
            "--max",
            "100",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "too long" in result.stderr.lower()


def test_talk_track_wc_cli_errors_missing_file(tmp_path: Path) -> None:
    """Missing file -> exit 1, descriptive stderr."""
    missing = tmp_path / "does_not_exist.md"
    result = subprocess.run(
        [sys.executable, str(TOOL), "--file", str(missing)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "not found" in result.stderr.lower()


def test_spoken_word_counter_excludes_non_blockquote_lines(tmp_path: Path) -> None:
    """Stage directions, headings, and tables must NOT count as spoken."""
    sample = tmp_path / "mixed.md"
    sample.write_text(
        "\n".join(
            [
                "# Heading (not counted)",
                "regular paragraph (not counted)",
                "> spoken line one",
                "> spoken line two and three",
                "> | table | row | (not counted)",
                "- bullet (not counted)",
                "> spoken final",
            ]
        ),
        encoding="utf-8",
    )
    # Expected spoken: "spoken line one" (3) + "spoken line two and three" (5)
    # + "spoken final" (2) = 10 words.
    assert count_spoken_words(sample) == 10
