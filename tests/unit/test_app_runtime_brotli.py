"""Each staged .br is checked against its source: the brotli module, else node.

tools/databricks/app_runtime_brotli.py. The App serves ``<asset>.br`` in place
of ``<asset>`` to a client accepting brotli, so a .br ships only after it has
been decoded and compared byte for byte. The node checker gets its paths as
argv, bounds the decoded size, runs time-bound, and any failure of its own is
an error, never a verdict.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tools.databricks import app_runtime_brotli as brotli_rules
from tools.databricks.app_runtime_brotli import (
    BrotliCheckError,
    brotli_verifier,
    decoder_verifier,
    node_verifier,
)

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH")
APP_JS = b"app();\n" * 200
COMPRESS = (
    "process.stdout.write(require('node:zlib').brotliCompressSync("
    "require('node:fs').readFileSync(0)))"
)


def node_brotli(data: bytes) -> bytes:
    assert NODE is not None
    return subprocess.run([NODE, "-e", COMPRESS], input=data, capture_output=True,
                          check=True).stdout


def _corrupt(data: bytes) -> bytes:
    middle = len(data) // 2
    return data[:middle] + bytes([data[middle] ^ 0xFF]) + data[middle + 1 :]


def _pair(folder: Path, name: str, br: bytes, source: bytes = APP_JS) -> tuple[Path, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(source)
    (folder / f"{name}.br").write_bytes(br)
    return folder / f"{name}.br", folder / name


@needs_node
def test_node_passes_only_a_br_that_decodes_to_exactly_its_source(tmp_path: Path) -> None:
    good = node_brotli(APP_JS)
    pairs = [
        _pair(tmp_path, "good.js", good),
        _pair(tmp_path, "other.js", node_brotli(b"alert('swapped');\n")),
        _pair(tmp_path, "corrupt.js", _corrupt(good)),
        # Decodes to 64x its source: cut off one byte past the source length.
        _pair(tmp_path, "expanding.js", node_brotli(APP_JS * 64)),
        _pair(tmp_path, "prefix.js", node_brotli(APP_JS[:-1])),
        _pair(tmp_path, "not-brotli.js", b"not a brotli stream"),
    ]

    assert node_verifier(NODE or "").check(pairs) == [True, False, False, False, False, False]


@needs_node
def test_node_gets_the_paths_as_argv_never_as_code(tmp_path: Path) -> None:
    # A folder name that would break out of a quoted JS string or a shell.
    folder = tmp_path / "it's \"dist\" `x` $(touch pwned) ${y}; ')"
    pair = _pair(folder, "app.js", node_brotli(APP_JS))

    assert node_verifier(NODE or "").check([pair]) == [True]
    assert not (tmp_path / "pwned").exists()


@needs_node
def test_node_checks_in_batches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(brotli_rules, "NODE_BATCH", 2)
    good = node_brotli(APP_JS)
    pairs = [_pair(tmp_path, f"{i}.js", good if i != 3 else _corrupt(good)) for i in range(5)]

    assert node_verifier(NODE or "").check(pairs) == [True, True, True, False, True]


@pytest.mark.parametrize(
    ("script", "message"),
    [
        ("echo boom >&2\nexit 3", "node exited 3: boom"),
        ("printf 'not json'", "node printed no verdict list"),
        ("printf '[true, true]'", "node printed no verdict list"),
        ("printf '[1]'", "node printed no verdict list"),
        ("exec sleep 5", "node did not finish within 0.5 s"),
    ],
)
def test_a_node_that_fails_is_an_error_not_a_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str, message: str
) -> None:
    monkeypatch.setattr(brotli_rules, "NODE_TIMEOUT_S", 0.5)
    fake = tmp_path / "node"
    fake.write_text(f"#!/bin/sh\n{script}\n", encoding="utf-8")
    fake.chmod(0o755)

    with pytest.raises(BrotliCheckError, match=message.replace("[", r"\[")):
        node_verifier(str(fake)).check([_pair(tmp_path / "d", "app.js", b"x")])


def test_the_checker_is_the_module_then_node_then_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(brotli_rules, "brotli_decoder", lambda: bytes)
    module = brotli_verifier()
    assert module is not None and module.method == "the brotli module"

    monkeypatch.setattr(brotli_rules, "brotli_decoder", lambda: None)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "node").write_text("#!/bin/sh\n", encoding="utf-8")
    (bin_dir / "node").chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    node = brotli_verifier()
    assert node is not None and node.method == "node"

    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert brotli_verifier() is None


def test_an_in_process_decoder_error_is_a_mismatch(tmp_path: Path) -> None:
    def decode(data: bytes) -> bytes:
        if data == b"raise":
            raise ValueError("bad stream")
        return data

    verifier = decoder_verifier(decode)
    pairs = [_pair(tmp_path, "a.js", APP_JS), _pair(tmp_path, "b.js", b"raise")]

    assert verifier.check(pairs) == [True, False]
