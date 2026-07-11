"""Regenerate the committed OpenAPI wire-contract baseline.

Usage:
    python tools/regen_openapi_baseline.py

Writes ``tests/fixtures/openapi_baseline.json`` from the live FastAPI app's
``app.openapi()`` document, formatted deterministically (sorted keys, two-space
indent, trailing newline) so reruns produce byte-identical output.

Determinism note: every ``/api/*`` router mounts unconditionally in
``backend.main``, but the SPA catch-all ``/{full_path}`` mounts only when
``frontend/dist/index.html`` exists (see the guard above ``_spa_fallback``).
The original baseline was generated with a built frontend, so the catch-all is
part of the committed snapshot. To keep regen byte-identical whether or not the
developer has run ``npm --prefix frontend run build``, this tool drops a
sentinel ``index.html`` before importing the app and removes it afterwards if
it created it. A real frontend build is never touched.
"""
from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests" / "fixtures" / "openapi_baseline.json"
_DIST_INDEX = ROOT / "frontend" / "dist" / "index.html"
_SENTINEL = "<!-- regen_openapi_baseline sentinel: safe to delete -->\n"


def _generate_spec() -> dict[str, object]:
    created_sentinel = False
    if not _DIST_INDEX.is_file():
        _DIST_INDEX.parent.mkdir(parents=True, exist_ok=True)
        _DIST_INDEX.write_text(_SENTINEL, encoding="utf-8")
        created_sentinel = True
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from backend.main import app

        return app.openapi()
    finally:
        if created_sentinel:
            _DIST_INDEX.unlink(missing_ok=True)
            with contextlib.suppress(OSError):
                _DIST_INDEX.parent.rmdir()


def main() -> int:
    if "backend.main" in sys.modules:
        print("backend.main already imported; run this tool as a fresh process.")
        return 1
    spec = _generate_spec()
    rendered = json.dumps(spec, indent=2, sort_keys=True) + "\n"
    previous = BASELINE.read_text(encoding="utf-8") if BASELINE.is_file() else ""
    BASELINE.write_text(rendered, encoding="utf-8")
    path_count = len(spec.get("paths", {}))  # type: ignore[union-attr]
    status = "unchanged" if rendered == previous else "updated"
    print(f"{status}: {BASELINE.relative_to(ROOT)} ({path_count} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
