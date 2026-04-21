"""Talk-track word-count gate -- Slice 9.

The conference-session window is 45s open + 6-8 min main + 30s close,
so the spoken portion of ``docs/module0-talk-track.md`` has to land in
roughly [1000, 1500] words -- the ~165 wpm natural-delivery band for a
presenter. Outside that window means either the talk underflows
(audience checks out) or overruns (audience starts arriving at the next
pitch).

Convention: inside the talk track, every line that starts with ``>`` is
spoken copy. Everything else is stage direction / route markers / table
rows / appendices and is NOT counted. This matches the ``awk '/^> /'``
one-liner Slice 8 used to estimate runtime.

Usage:

    python tools/talk_track_wc.py                  # default path
    python tools/talk_track_wc.py --file <path>    # custom path
    python tools/talk_track_wc.py --min 900 --max 1600  # band override

Exit codes:
    0 -- count is inside ``[min, max]``. Prints ``ok <count>``.
    1 -- count is outside the window OR the file is missing. Prints a
         descriptive error to stderr that names the count, the band,
         and the rough wall-clock delta at 165 wpm.

The script is stdlib-only so it runs on every CI image and every
developer laptop without a pip install.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Tuned to the session window: 6-8 min spoken, plus 45s open + 30s close.
# At ~165 wpm, 1000 words = 6.1 min, 1500 words = 9.1 min. The lower
# bound protects against a talk that underflows (missing beat), the
# upper bound against one that overruns (audience fade). Slice 9 keeps
# the band loose enough to allow natural drafting.
DEFAULT_MIN = 1000
DEFAULT_MAX = 1500

# Average presenter delivery cadence. Rounded to the session-pacing target.
_WPM = 165

# Convention: lines beginning with "> " (a blockquote marker followed by
# a space, no tab) are the spoken track. Lines with "> " followed by a
# pipe character (table rows) are explicitly excluded because they're
# stage / narrative reference, not copy the presenter reads aloud.
_SPOKEN_PREFIX = "> "


def _default_path() -> Path:
    """Resolve the repo-root path to the talk track.

    ``tools/`` sits next to ``docs/`` at the repo root, so the talk
    track is always ``../docs/module0-talk-track.md`` relative to
    this file.
    """
    return Path(__file__).resolve().parent.parent / "docs" / "module0-talk-track.md"


def count_spoken_words(path: Path) -> int:
    """Count spoken words in the talk track at ``path``.

    A "spoken word" is any whitespace-delimited token on a line that
    starts with ``> `` and is NOT a markdown-table row (``> |`` prefix).
    The blockquote marker itself and bold/italic markup are stripped by
    the simple whitespace split; we don't strip asterisks because "back*"
    style emphasis is rare in the track and wouldn't skew the count.
    """
    text = path.read_text(encoding="utf-8")
    total = 0
    for line in text.splitlines():
        stripped = line.lstrip()
        if not stripped.startswith(_SPOKEN_PREFIX):
            continue
        # Strip the blockquote marker then check for table-row continuation.
        content = stripped[len(_SPOKEN_PREFIX):]
        if content.startswith("|"):
            # Markdown table row inside a blockquote -- not spoken copy.
            continue
        total += len(content.split())
    return total


def _minutes(words: int) -> float:
    return words / _WPM


def _format_error(count: int, min_words: int, max_words: int) -> str:
    if count < min_words:
        shortfall = min_words - count
        return (
            f"talk track is too short: {count} spoken words "
            f"({_minutes(count):.1f} min at {_WPM} wpm), "
            f"needs at least {min_words} ({_minutes(min_words):.1f} min). "
            f"Short by {shortfall} words (~{_minutes(shortfall):.1f} min)."
        )
    overflow = count - max_words
    return (
        f"talk track is too long: {count} spoken words "
        f"({_minutes(count):.1f} min at {_WPM} wpm), "
        f"cap is {max_words} ({_minutes(max_words):.1f} min). "
        f"Over by {overflow} words (~{_minutes(overflow):.1f} min)."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--file",
        type=Path,
        default=_default_path(),
        help="Path to talk track markdown file.",
    )
    parser.add_argument(
        "--min",
        type=int,
        default=DEFAULT_MIN,
        dest="min_words",
        help="Minimum spoken word count (default 1000).",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=DEFAULT_MAX,
        dest="max_words",
        help="Maximum spoken word count (default 1500).",
    )
    args = parser.parse_args(argv)

    if not args.file.exists():
        print(f"talk track file not found: {args.file}", file=sys.stderr)
        return 1

    count = count_spoken_words(args.file)
    if count < args.min_words or count > args.max_words:
        print(
            _format_error(count, args.min_words, args.max_words),
            file=sys.stderr,
        )
        return 1

    print(f"ok {count} spoken words ({_minutes(count):.1f} min at {_WPM} wpm)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
