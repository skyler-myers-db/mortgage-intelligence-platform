"""Checks that each staged ``.br`` decodes to exactly its source.

The App's ``/assets`` route serves ``<asset>.br`` in place of ``<asset>`` to a
client that accepts brotli (backend/services/static_assets.py), so
``app_runtime_closure stage`` ships a ``.br`` only when it has decoded it and
compared the bytes. ``brotli_verifier`` picks the first checker available:

* the optional ``brotli`` Python module (not a repo dependency);
* else ``node`` on PATH, the runtime that wrote the files
  (tools/precompress_assets.mjs): one short-lived process per batch decodes
  each file with ``zlib.brotliDecompressSync`` (``maxOutputLength`` one byte
  over the source, so a large expansion stops early instead of filling
  memory), requires the stream to use every input byte (node stops at the end
  of the first stream and ignores what follows), compares the output with the
  source inside node, and prints one JSON boolean per file. The paths travel
  as argv after ``--``, never inside the script, and each process is
  time-bound;
* else none, and stage leaves every ``.br`` out (the App then serves the
  checked ``.gz``).

A file that does not decode to its source is a refusal, never an omission.
A checker that cannot finish (node exits non-zero, times out or prints
anything but the verdict list) raises ``BrotliCheckError``, which the review
turns into a refusal as well.
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

Decoder = Callable[[bytes], bytes]
Pair = tuple[Path, Path]  # (the .br file, its source)

NODE_TIMEOUT_S = 120
NODE_BATCH = 64  # files per node process: keeps argv short on every platform
# Paths arrive in process.argv; nothing is interpolated into this script.
NODE_CHECK = r"""
'use strict';
const fs = require('node:fs');
const zlib = require('node:zlib');
const args = process.argv.slice(1);
const verdicts = [];
for (let i = 0; i + 1 < args.length; i += 2) {
  let same = false;
  try {
    const source = fs.readFileSync(args[i + 1]);
    const input = fs.readFileSync(args[i]);
    // info: the engine counts the input bytes it consumed. Decoding stops at
    // the end of the first brotli stream and ignores the rest, so a file
    // with bytes after its stream (a second stream, appended text) would
    // otherwise decode to its source and ship.
    const result = zlib.brotliDecompressSync(input, {
      info: true,
      maxOutputLength: source.length + 1,
    });
    same = result.engine.bytesWritten === input.length && result.buffer.equals(source);
  } catch (error) {
    same = false;
  }
  verdicts.push(same);
}
process.stdout.write(JSON.stringify(verdicts));
"""


class BrotliCheckError(RuntimeError):
    """The checker itself failed; no verdict is trusted."""


@dataclass(frozen=True)
class BrotliVerifier:
    """``check(pairs)`` returns, per (``.br``, source) pair, whether they match."""

    method: str
    check: Callable[[Sequence[Pair]], list[bool]]


def brotli_decoder() -> Decoder | None:
    """``brotli.decompress`` when the optional module is installed."""

    try:
        module = importlib.import_module("brotli")
    except ImportError:
        return None
    return cast(Decoder, module.decompress)


def decoder_verifier(decode: Decoder, method: str = "the brotli module") -> BrotliVerifier:
    """A verifier that decodes in this process with ``decode``."""

    def check(pairs: Sequence[Pair]) -> list[bool]:
        verdicts: list[bool] = []
        for compressed, source in pairs:
            try:
                verdicts.append(decode(compressed.read_bytes()) == source.read_bytes())
            except Exception:  # noqa: BLE001 -- any decoder error is a mismatch
                verdicts.append(False)
        return verdicts

    return BrotliVerifier(method, check)


def _node_batch(node: str, batch: Sequence[Pair]) -> list[bool]:
    argv = [str(path) for pair in batch for path in pair]
    env = {key: value for key, value in os.environ.items() if not key.startswith("NODE_")}
    try:
        result = subprocess.run(
            [node, "-e", NODE_CHECK, "--", *argv],
            capture_output=True, timeout=NODE_TIMEOUT_S, check=False, env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise BrotliCheckError(f"node did not finish within {NODE_TIMEOUT_S} s") from exc
    except OSError as exc:
        raise BrotliCheckError(f"node could not run: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()[-1:]
        raise BrotliCheckError(f"node exited {result.returncode}: {' '.join(detail)}")
    try:
        verdicts = json.loads(result.stdout)
    except ValueError as exc:
        raise BrotliCheckError("node printed no verdict list") from exc
    if not (isinstance(verdicts, list) and len(verdicts) == len(batch)
            and all(isinstance(v, bool) for v in verdicts)):
        raise BrotliCheckError("node printed no verdict list")
    return verdicts


def node_verifier(node: str) -> BrotliVerifier:
    """A verifier that decodes with ``node``'s zlib, ``NODE_BATCH`` files a process."""

    def check(pairs: Sequence[Pair]) -> list[bool]:
        verdicts: list[bool] = []
        for start in range(0, len(pairs), NODE_BATCH):
            verdicts += _node_batch(node, pairs[start : start + NODE_BATCH])
        return verdicts

    return BrotliVerifier("node", check)


def brotli_verifier() -> BrotliVerifier | None:
    """The brotli module, else ``node`` on PATH, else None (every .br left out)."""

    decode = brotli_decoder()
    if decode is not None:
        return decoder_verifier(decode)
    node = shutil.which("node")
    return node_verifier(node) if node else None
