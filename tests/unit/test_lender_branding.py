"""Reviewed lender marks and the deploy-time CLI (audit responsive-10, 12.4 #9).

Every image here is generated in-test (abstract pixels, never a brand), and
both registries are monkeypatched with the synthetic "Fixture Test Lending"
identity; the source-controlled registries stay untouched.
"""

from __future__ import annotations

import hashlib
import os
import struct
import subprocess
import sys
import zlib
from datetime import date, timedelta
from pathlib import Path

import pytest

from backend.schemas import lender_branding, lender_identity
from backend.schemas.lender_branding import ReviewedLenderMark, validate_lender_mark
from tools.branding import lender_mark

ROOT = Path(__file__).resolve().parents[2]
LENDER = "Fixture Test Lending"
NMLS = "987654"
OTHER_NMLS = "987655"
SIZE_MESSAGE = "32-512 px tall and 1-2 times as wide as tall"


def _png_chunk(kind: bytes, payload: bytes, *, crc: int | None = None) -> bytes:
    checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF if crc is None else crc
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def make_png(width: int = 64, height: int = 32, *, extra: tuple[bytes, ...] = (), tail: bytes = b"") -> bytes:
    """An abstract two-tone RGBA image with valid chunk CRCs."""
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    rows = b"".join(b"\x00" + (b"\x1f\x6f\xb4\xff" * (width // 2) + b"\xe8\xee\xf3\xff" * (width - width // 2)) for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + b"".join(extra)
        + _png_chunk(b"IDAT", zlib.compress(rows))
        + _png_chunk(b"IEND", b"")
        + tail
    )


def _riff_chunk(kind: bytes, payload: bytes) -> bytes:
    return kind + struct.pack("<I", len(payload)) + payload + (b"\x00" if len(payload) % 2 else b"")


def _riff(chunks: bytes) -> bytes:
    return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WEBP" + chunks


def _vp8l(width: int, height: int) -> bytes:
    bits = (width - 1) | ((height - 1) << 14) | (1 << 28)
    return _riff_chunk(b"VP8L", b"\x2f" + struct.pack("<I", bits) + b"\x00" * 7)


def make_webp_vp8l(width: int = 48, height: int = 40) -> bytes:
    return _riff(_vp8l(width, height))


def make_webp_vp8x(width: int = 64, height: int = 48, *, flags: int = 0x10, extra: bytes = b"") -> bytes:
    header = bytes([flags, 0, 0, 0]) + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    return _riff(_riff_chunk(b"VP8X", header) + extra + _vp8l(width, height))


def entry_for(data: bytes, media_type: str, width: int, height: int, *, nmls: str = NMLS) -> ReviewedLenderMark:
    return ReviewedLenderMark(
        nmls_id=nmls,
        sha256=hashlib.sha256(data).hexdigest(),
        media_type=media_type,  # type: ignore[arg-type]
        width=width,
        height=height,
        authorization_ref="BRAND-AUTH/2026-0042",
        reviewed_on=date(2026, 9, 30),
    )


@pytest.fixture
def fixture_lender(monkeypatch: pytest.MonkeyPatch):
    """Register the synthetic identity (two NMLS ids) and return a mark installer."""
    monkeypatch.setitem(lender_identity._REVIEWED_PUBLIC_LENDER_IDENTITIES, LENDER, frozenset({NMLS, OTHER_NMLS}))

    def install(entry: ReviewedLenderMark) -> ReviewedLenderMark:
        monkeypatch.setitem(lender_branding._REVIEWED_LENDER_MARKS, LENDER, entry)
        return entry

    return install


def refusal(data: bytes, *, nmls: str = NMLS, lender: str = LENDER) -> str:
    with pytest.raises(ValueError) as caught:
        validate_lender_mark(lender, nmls, data)
    return str(caught.value)


# --- registry -----------------------------------------------------------------


def test_summit_has_no_mark_and_the_committed_registry_is_clean() -> None:
    assert lender_branding.reviewed_lender_mark("Summit Mortgage") is None
    assert lender_branding._REVIEWED_LENDER_MARKS == {}
    assert lender_branding._registry_problems() == []


def test_registry_problems_report_unbound_or_malformed_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    good = entry_for(make_png(), "image/png", 64, 32)
    monkeypatch.setitem(lender_identity._REVIEWED_PUBLIC_LENDER_IDENTITIES, LENDER, frozenset({NMLS}))
    monkeypatch.setitem(lender_branding._REVIEWED_LENDER_MARKS, LENDER, good)
    assert lender_branding._registry_problems() == []

    cases = {
        "Unbound Mortgage": (good, "not a reviewed (lender name, NMLS id) identity"),
        "Scheme Lending": (ReviewedLenderMark(**{**good.__dict__, "authorization_ref": "https://brand.example/ok"}), "authorization_ref"),
        "Mail Lending": (ReviewedLenderMark(**{**good.__dict__, "authorization_ref": "brand@lender"}), "authorization_ref"),
        "Future Lending": (ReviewedLenderMark(**{**good.__dict__, "reviewed_on": date.today() + timedelta(days=1)}), "reviewed_on"),
    }
    for name, (entry, _) in cases.items():
        if name != "Unbound Mortgage":
            monkeypatch.setitem(lender_identity._REVIEWED_PUBLIC_LENDER_IDENTITIES, name, frozenset({NMLS}))
        monkeypatch.setitem(lender_branding._REVIEWED_LENDER_MARKS, name, entry)
    problems = lender_branding._registry_problems()
    for name, (_, needle) in cases.items():
        assert [p for p in problems if p.startswith(f"{name}:") and needle in p], name


# --- validate_lender_mark -------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "media_type", "width", "height"),
    [
        (make_png(64, 32), "image/png", 64, 32),
        (make_webp_vp8l(48, 40), "image/webp", 48, 40),
        (make_webp_vp8x(64, 48), "image/webp", 64, 48),
    ],
    ids=["png", "webp-vp8l", "webp-vp8x"],
)
def test_accepts_a_reviewed_png_or_webp(fixture_lender, data: bytes, media_type: str, width: int, height: int) -> None:
    entry = fixture_lender(entry_for(data, media_type, width, height))
    assert validate_lender_mark(LENDER, NMLS, data) is entry


def test_refuses_formats_other_than_png_and_webp(fixture_lender) -> None:
    fixture_lender(entry_for(make_png(), "image/png", 64, 32))
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="32"><rect width="64" height="32"/></svg>'
    jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x00" * 64
    gif = b"GIF89a" + b"\x00" * 64
    for data in (svg, jpeg, gif):
        assert refusal(data) == "lender mark must be a PNG or WebP image"


def test_refuses_more_than_64_kib(fixture_lender) -> None:
    fixture_lender(entry_for(make_png(), "image/png", 64, 32))
    assert refusal(make_png() + b"\x00" * 65536) == "lender mark is larger than 64 KiB"


@pytest.mark.parametrize(("width", "height"), [(32, 64), (100, 40), (62, 31), (513, 513)], ids=["ratio-0.5", "ratio-2.5", "height-31", "height-513"])
def test_refuses_sizes_outside_the_bounds(fixture_lender, width: int, height: int) -> None:
    data = make_png(width, height)
    fixture_lender(entry_for(data, "image/png", width, height))
    assert SIZE_MESSAGE in refusal(data)


def test_refuses_a_hash_mismatch_a_wrong_nmls_and_an_unknown_lender(fixture_lender) -> None:
    data = make_png()
    fixture_lender(entry_for(data, "image/png", 64, 32))
    # Same type and size, other bytes (an allowed pHYs chunk): only the hash differs.
    other = make_png(extra=(_png_chunk(b"pHYs", struct.pack(">IIB", 2835, 2835, 1)),))
    assert "does not match its reviewed sha256" in refusal(other)
    # The image is the reviewed one, but the entry is bound to another NMLS id.
    assert "no reviewed lender mark" in refusal(data, nmls=OTHER_NMLS)
    with pytest.raises(ValueError):
        validate_lender_mark("Unknown Test Lending", NMLS, data)
    # Same hash, recorded with a different media type or size: still refused.
    fixture_lender(entry_for(data, "image/png", 64, 33))
    assert "does not match its reviewed sha256" in refusal(data)


@pytest.mark.parametrize(
    "extra",
    [
        _png_chunk(b"tEXt", b"Author\x00Fixture"),
        _png_chunk(b"eXIf", b"MM\x00*\x00\x00\x00\x08"),
        _png_chunk(b"iCCP", b"icc\x00\x00" + zlib.compress(b"profile")),
        _png_chunk(b"acTL", struct.pack(">II", 2, 0)),
        _png_chunk(b"zzZz", b"unknown"),
    ],
    ids=["tEXt", "eXIf", "iCCP", "acTL", "unknown"],
)
def test_refuses_png_metadata_profiles_and_animation(fixture_lender, extra: bytes) -> None:
    data = make_png(extra=(extra,))
    fixture_lender(entry_for(data, "image/png", 64, 32))
    assert refusal(data) == "lender mark PNG carries metadata, a profile or animation"


def test_refuses_a_forged_or_padded_png(fixture_lender) -> None:
    clean = make_png()
    fixture_lender(entry_for(clean, "image/png", 64, 32))
    bad_crc = clean[:-4] + b"\x00\x00\x00\x00"
    assert refusal(bad_crc) == "lender mark PNG has a chunk with a bad checksum"
    assert refusal(clean + b"trailing") == "lender mark PNG has bytes after IEND"
    assert refusal(clean[:-12]) == "lender mark PNG must run IHDR ... IDAT ... IEND"


@pytest.mark.parametrize(
    "data",
    [
        make_webp_vp8x(extra=_riff_chunk(b"EXIF", b"MM\x00*")),
        make_webp_vp8x(extra=_riff_chunk(b"XMP ", b"<x:xmpmeta/>")),
        make_webp_vp8x(flags=0x12),
        make_webp_vp8x(flags=0x18),
        make_webp_vp8x(extra=_riff_chunk(b"ANIM", b"\x00" * 6)),
        make_webp_vp8x(extra=_riff_chunk(b"ANMF", b"\x00" * 16)),
        make_webp_vp8x(extra=_riff_chunk(b"ICCP", b"profile")),
    ],
    ids=["EXIF", "XMP", "animation-flag", "exif-flag", "ANIM", "ANMF", "ICCP"],
)
def test_refuses_webp_metadata_profiles_and_animation(fixture_lender, data: bytes) -> None:
    fixture_lender(entry_for(data, "image/webp", 64, 48))
    assert refusal(data) == "lender mark WebP carries metadata, a profile or animation"


def test_no_message_carries_the_path_or_file_bytes(fixture_lender, tmp_path: Path) -> None:
    fixture_lender(entry_for(make_png(), "image/png", 64, 32))
    secret = b"Author\x00Do-not-echo-0xC0FFEE"
    samples = [make_png(extra=(_png_chunk(b"tEXt", secret),)), b"<svg>" + secret, make_png() + secret]
    for data in samples:
        message = refusal(data)
        assert "Do-not-echo" not in message and "C0FFEE" not in message
        assert str(tmp_path) not in message


# --- CLI ------------------------------------------------------------------------


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = lender_mark.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "checkout"
    (root / "frontend").mkdir(parents=True)
    monkeypatch.setattr(lender_mark, "REPO_ROOT", root)
    return root


def test_validate_without_a_file(capsys: pytest.CaptureFixture[str], fixture_lender) -> None:
    assert _run(capsys, "validate", "--lender", "Summit Mortgage", "--nmls", "123456", "--print-sha256") == (
        0,
        "",
        "[lender-mark] lender mark: none\n",
    )
    fixture_lender(entry_for(make_png(), "image/png", 64, 32))
    code, out, err = _run(capsys, "validate", "--lender", LENDER, "--nmls", NMLS, "--print-sha256")
    assert (code, out) == (2, "")
    assert f"reviewed mark configured for {LENDER} but MIP_LENDER_MARK_FILE is unset" in err


def test_validate_prints_only_the_hash_and_never_the_path(capsys: pytest.CaptureFixture[str], fixture_lender, tmp_path: Path) -> None:
    data = make_png()
    entry = fixture_lender(entry_for(data, "image/png", 64, 32))
    mark_file = tmp_path / "private-brand-dir" / "mark.png"
    mark_file.parent.mkdir()
    mark_file.write_bytes(data)
    code, out, err = _run(capsys, "validate", "--lender", LENDER, "--nmls", NMLS, "--file", str(mark_file), "--print-sha256")
    assert (code, out) == (0, f"{entry.sha256}\n")
    assert err == f"[lender-mark] lender mark: {entry.sha256[:12]} image/png 64x32\n"
    assert "private-brand-dir" not in out + err

    # A file for a lender with no reviewed mark is refused, path-free.
    code, out, err = _run(capsys, "validate", "--lender", "Summit Mortgage", "--nmls", "123456", "--file", str(mark_file))
    assert (code, out) == (2, "")
    assert "no reviewed lender mark is recorded for Summit Mortgage" in err
    assert "private-brand-dir" not in err


def test_validate_refuses_symlinks_and_non_regular_files(capsys: pytest.CaptureFixture[str], fixture_lender, tmp_path: Path) -> None:
    data = make_png()
    fixture_lender(entry_for(data, "image/png", 64, 32))
    real = tmp_path / "secret-real.png"
    real.write_bytes(data)
    link = tmp_path / "secret-link.png"
    link.symlink_to(real)
    directory = tmp_path / "secret-dir.png"
    directory.mkdir()
    missing = tmp_path / "secret-missing.png"
    for path, reason in ((link, "symbolic link"), (directory, "not a regular file"), (missing, "could not be opened")):
        code, out, err = _run(capsys, "validate", "--lender", LENDER, "--nmls", NMLS, "--file", str(path))
        assert (code, out) == (2, ""), path.name
        assert reason in err
        assert "secret-" not in err


def test_stage_purges_and_writes_only_the_validated_mark(capsys: pytest.CaptureFixture[str], fixture_lender, repo: Path, tmp_path: Path) -> None:
    stage = repo / "frontend" / ".branding-stage"
    stage.mkdir()
    (stage / "lender-mark.webp").write_bytes(b"stale")

    # Nothing configured: the stale stage is purged and no directory is left.
    assert _run(capsys, "stage", "--lender", "Summit Mortgage", "--nmls", "123456", "--out", "frontend/.branding-stage")[0] == 0
    assert not stage.exists()

    data = make_png()
    entry = fixture_lender(entry_for(data, "image/png", 64, 32))
    mark_file = tmp_path / "mark.png"
    mark_file.write_bytes(data)
    stage.mkdir()
    (stage / "stale.txt").write_text("stale")
    common = ("stage", "--lender", LENDER, "--nmls", NMLS, "--file", str(mark_file), "--out", "frontend/.branding-stage")
    # A hash other than the one preflight validated: refused, nothing staged.
    code, _, err = _run(capsys, *common, "--expect-sha256", "0" * 64)
    assert code == 2 and "changed since preflight" in err
    assert not stage.exists()
    code, _, _ = _run(capsys, *common, "--expect-sha256", entry.sha256)
    assert code == 0
    assert sorted(p.name for p in stage.iterdir()) == ["lender-mark.png"]
    assert (stage / "lender-mark.png").read_bytes() == data

    # A reviewed mark with no file is refused.
    code, _, err = _run(capsys, "stage", "--lender", LENDER, "--nmls", NMLS, "--out", "frontend/.branding-stage")
    assert code == 2 and "MIP_LENDER_MARK_FILE is unset" in err


def test_stage_reads_the_path_from_an_env_variable_so_deploy_never_echoes_it(
    capsys: pytest.CaptureFixture[str], fixture_lender, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = make_png()
    entry = fixture_lender(entry_for(data, "image/png", 64, 32))
    mark_file = tmp_path / "env-held-secret.png"
    mark_file.write_bytes(data)
    monkeypatch.setenv("MIP_LENDER_MARK_FILE", str(mark_file))
    args = ("stage", "--lender", LENDER, "--nmls", NMLS, "--file-env", "MIP_LENDER_MARK_FILE", "--out", "frontend/.branding-stage")
    code, out, err = _run(capsys, *args, "--expect-sha256", entry.sha256)
    assert code == 0
    assert (repo / "frontend" / ".branding-stage" / "lender-mark.png").read_bytes() == data
    assert "env-held-secret" not in out + err
    code, _, err = _run(capsys, *args[:5], "--file-env", "HOME", "--out", "frontend/.branding-stage")
    assert code == 2 and "--file-env must name an MIP_* environment variable" in err


def test_stage_refuses_any_out_but_the_stage_dir(capsys: pytest.CaptureFixture[str], repo: Path, tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("keep")
    for out in (str(victim), "frontend/.branding-stage/../../victim", "../victim", "frontend", "frontend/.branding-stage/sub"):
        code, _, err = _run(capsys, "stage", "--lender", "Summit Mortgage", "--nmls", "123456", "--out", out)
        assert code == 2, out
        assert "--out must be frontend/.branding-stage" in err
    assert (victim / "keep.txt").read_text() == "keep"


def test_cli_subprocess_smoke_from_the_repo_root() -> None:
    env = {key: value for key, value in os.environ.items() if not key.startswith("MIP_")}
    proc = subprocess.run(
        [sys.executable, "-m", "tools.branding.lender_mark", "validate", "--lender", "Summit Mortgage", "--nmls", "123456", "--print-sha256"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert (proc.returncode, proc.stdout) == (0, "")
    assert proc.stderr == "[lender-mark] lender mark: none\n"
