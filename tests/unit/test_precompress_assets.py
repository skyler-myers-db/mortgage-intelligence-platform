"""The build's precompress step writes the same siblings, concurrently.

``tools/precompress_assets.mjs`` runs inside every ``npm --prefix frontend run
build`` (CI and ``scripts/deploy.sh``). Since 2026-10-27 it compresses its
files concurrently on libuv's thread pool instead of one after another:
brotli 11 over the 51 committed per-state ZCTA TopoJSON assets had made the
step about a minute of single-threaded CPU.

This test runs the real tool on a scratch tree (it resolves
``frontend/dist/assets`` from its own location) and pins:

* every eligible asset gets a ``.br`` and a ``.gz`` whose bytes equal Node's
  synchronous ``brotliCompressSync`` (quality 11, size hint) and
  ``gzipSync`` (level 9) output, so concurrency changed no byte and dropped
  no file;
* a file under 1 KiB, a non-text suffix and an incompressible file get no
  sibling (a "compressed" copy larger than identity is never written);
* the summary line counts exactly the eligible files.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "precompress_assets.mjs"

# Node's synchronous reference: sha256 of each file's brotli 11 and gzip 9 bytes.
_SYNC_REFERENCE = """
const { readFileSync } = require('node:fs');
const { createHash } = require('node:crypto');
const zlib = require('node:zlib');
const sha = (buf) => createHash('sha256').update(buf).digest('hex');
const out = {};
for (const file of JSON.parse(readFileSync(0, 'utf8'))) {
  const raw = readFileSync(file);
  const br = zlib.brotliCompressSync(raw, { params: {
    [zlib.constants.BROTLI_PARAM_QUALITY]: 11,
    [zlib.constants.BROTLI_PARAM_SIZE_HINT]: raw.length,
  } });
  out[file] = { br: sha(br), gz: sha(zlib.gzipSync(raw, { level: 9 })) };
}
process.stdout.write(JSON.stringify(out));
"""


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("MIP_REQUIRE_FIXTURE_CONTRACT") == "1":
            pytest.fail("node is required to run the precompress tool (MIP_REQUIRE_FIXTURE_CONTRACT=1)")
        pytest.skip("node is not installed")
    return node


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_concurrent_step_writes_the_sync_bytes_for_every_eligible_asset(tmp_path: Path) -> None:
    node = _node()
    (tmp_path / "tools").mkdir()
    shutil.copy2(TOOL, tmp_path / "tools" / TOOL.name)
    assets = tmp_path / "frontend" / "dist" / "assets"
    assets.mkdir(parents=True)

    eligible: list[Path] = []
    # More files than any thread pool or in-flight limit, in every compressible suffix.
    for index in range(24):
        suffix = ("js", "css", "svg", "json", "txt")[index % 5]
        body = json.dumps({"id": index, "rows": [[index, row, f"zip-{row:05d}"] for row in range(200 + 37 * index)]})
        path = assets / f"chunk{index:02d}-{index:08x}.{suffix}"
        path.write_text(body, encoding="utf-8")
        eligible.append(path)
    small = assets / "tiny-0000.js"
    small.write_text("x" * 1023, encoding="utf-8")
    font = assets / "geist-0000.woff2"
    font.write_bytes(b"w" * 4096)
    noise = assets / "noise-0000.js"
    noise.write_bytes(random.Random(7).randbytes(4096))

    result = subprocess.run(
        [node, str(tmp_path / "tools" / TOOL.name)], capture_output=True, text=True, check=True, timeout=120
    )

    assert result.stdout.startswith(f"precompress: {len(eligible) + 1} assets"), result.stdout
    reference = json.loads(
        subprocess.run(
            [node, "-e", _SYNC_REFERENCE],
            input=json.dumps([str(path) for path in eligible]),
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        ).stdout
    )
    written = {
        str(path): {"br": _sha(Path(f"{path}.br")), "gz": _sha(Path(f"{path}.gz"))} for path in eligible
    }
    assert written == reference
    for skipped in (small, font, noise):
        assert not Path(f"{skipped}.br").exists(), skipped.name
        assert not Path(f"{skipped}.gz").exists(), skipped.name
