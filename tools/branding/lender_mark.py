"""Validate and stage the optional lender mark at deploy time (audit responsive-10).

``scripts/deploy.sh`` runs two subcommands, both before the frontend build:

``validate`` (preflight, before any workspace mutation) checks
``MIP_LENDER_MARK_FILE`` against the reviewed registry in
``backend/schemas/lender_branding.py`` and, with ``--print-sha256``, writes
only the file's sha256 to stdout (nothing when no mark is configured).

``stage`` (Step 1, right before ``npm run build``) purges
``frontend/.branding-stage``, re-validates the file, checks it is still the
file preflight validated (``--expect-sha256``) and copies it there as
``lender-mark.png`` or ``lender-mark.webp`` for the Vite build to emit.

Exit codes are 0 (accepted, or nothing configured) and 2 (refused). Human
lines go to stderr and show only the hash prefix, media type and size; the
file path is never printed, and an OS error becomes a fixed sentence.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.schemas.lender_branding import (  # noqa: E402
    MAX_MARK_BYTES,
    ReviewedLenderMark,
    reviewed_lender_mark,
    validate_lender_mark,
)
from backend.schemas.lender_identity import validate_public_lender_identity  # noqa: E402

EXIT_OK = 0
EXIT_REFUSED = 2
STAGE_RELATIVE = Path("frontend") / ".branding-stage"
_EXTENSIONS = {"image/png": "png", "image/webp": "webp"}


class Refused(Exception):
    """A refusal whose message is a fixed sentence (no path, no file bytes)."""


def _say(message: str) -> None:
    print(f"[lender-mark] {message}", file=sys.stderr)


def _resolve_in_repo(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


def _read_mark(path_text: str) -> bytes:
    """At most MAX_MARK_BYTES + 1 bytes of a regular, non-symlink file."""

    path = _resolve_in_repo(path_text)
    if path.is_symlink():
        raise Refused("the lender mark file is a symbolic link")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise Refused("the lender mark file could not be opened") from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Refused("the lender mark file is not a regular file")
        chunks: list[bytes] = []
        remaining = MAX_MARK_BYTES + 1
        while remaining > 0:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
    except OSError:
        raise Refused("the lender mark file could not be read") from None
    finally:
        os.close(fd)


def _identity(lender: str, nmls: str) -> tuple[str, str]:
    try:
        return validate_public_lender_identity(lender, nmls)
    except ValueError:
        raise Refused("the lender name and NMLS id are not a reviewed lender identity") from None


def _checked_mark(lender: str, nmls: str, file_text: str) -> tuple[ReviewedLenderMark, bytes] | None:
    """The validated mark and its bytes; None when neither a file nor a reviewed mark exists."""

    name, nmls_id = _identity(lender, nmls)
    reviewed = reviewed_lender_mark(name)
    if not file_text:
        if reviewed is None:
            return None
        raise Refused(f"reviewed mark configured for {name} but MIP_LENDER_MARK_FILE is unset")
    if reviewed is None:
        raise Refused(f"no reviewed lender mark is recorded for {name}; unset MIP_LENDER_MARK_FILE")
    data = _read_mark(file_text)
    try:
        mark = validate_lender_mark(name, nmls_id, data)
    except ValueError as error:
        # lender_branding's messages are fixed sentences without file bytes.
        raise Refused(str(error)) from None
    _say(f"lender mark: {mark.sha256[:12]} {mark.media_type} {mark.width}x{mark.height}")
    return mark, data


def _validate(args: argparse.Namespace) -> int:
    checked = _checked_mark(args.lender, args.nmls, args.file)
    if checked is None:
        _say("lender mark: none")
        return EXIT_OK
    if args.print_sha256:
        print(checked[0].sha256)
    return EXIT_OK


def _stage_dir(out_text: str) -> Path:
    expected = REPO_ROOT / STAGE_RELATIVE
    candidate = Path(os.path.normpath(_resolve_in_repo(out_text)))
    if candidate != Path(os.path.normpath(expected)) or expected.is_symlink():
        raise Refused("--out must be frontend/.branding-stage inside this checkout")
    return expected


def _purge(out: Path) -> None:
    if out.is_dir():
        shutil.rmtree(out)
    elif out.exists():
        out.unlink()


def _stage(args: argparse.Namespace) -> int:
    out = _stage_dir(args.out)
    _purge(out)
    checked = _checked_mark(args.lender, args.nmls, args.file)
    if checked is None:
        _say("lender mark: none staged")
        return EXIT_OK
    mark, data = checked
    if mark.sha256 != args.expect_sha256:
        raise Refused("the lender mark changed since preflight validated it")
    out.mkdir(parents=True)
    # The exact bytes that were validated, never a second read of the file.
    (out / f"lender-mark.{_EXTENSIONS[mark.media_type]}").write_bytes(data)
    _say(f"lender mark staged: {mark.sha256[:12]}")
    return EXIT_OK


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.branding.lender_mark", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "stage"):
        command = commands.add_parser(name)
        command.add_argument("--lender", required=True)
        command.add_argument("--nmls", required=True)
        command.add_argument("--file", default="")
        if name == "validate":
            command.add_argument("--print-sha256", action="store_true")
        else:
            command.add_argument("--out", required=True)
            command.add_argument("--expect-sha256", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _validate(args) if args.command == "validate" else _stage(args)
    except Refused as refusal:
        _say(str(refusal))
        return EXIT_REFUSED
    except Exception:
        # A fixed sentence, never a traceback that could carry the path.
        _say("the lender mark check failed unexpectedly")
        return EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
