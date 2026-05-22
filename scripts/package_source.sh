#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
PROJECT="mortgage-intelligence-platform"
DIST="$ROOT/dist"
OUT="$DIST/$PROJECT.zip"
TMP="$(mktemp -d)"
PYTHON_BIN="${PYTHON:-python3}"

cleanup() {
  rm -rf "$TMP"
}
trap cleanup EXIT

mkdir -p "$DIST"
rm -f "$OUT"

STAGE="$TMP/$PROJECT"
mkdir -p "$STAGE"

# Start from git-tracked source only, then remove tracked local/release
# evidence that should never ship in the source package.
git -C "$ROOT" archive --format=tar HEAD | tar -xf - -C "$STAGE"

rm -rf \
  "$STAGE/.git" \
  "$STAGE/.databricks" \
  "$STAGE/.bundle" \
  "$STAGE/.playwright-mcp" \
  "$STAGE/.claude" \
  "$STAGE/.vscode" \
  "$STAGE/.idea" \
  "$STAGE/.pytest_cache" \
  "$STAGE/.ruff_cache" \
  "$STAGE/.mypy_cache" \
  "$STAGE/.hypothesis" \
  "$STAGE/.venv" \
  "$STAGE/node_modules" \
  "$STAGE/frontend/node_modules" \
  "$STAGE/frontend/dist" \
  "$STAGE/frontend/test-results" \
  "$STAGE/playwright-report" \
  "$STAGE/test-results" \
  "$STAGE/coverage" \
  "$STAGE/sql/_rendered" \
  "$STAGE/docs/validation/screenshots"

find "$STAGE" \( \
    -name '__pycache__' -o \
    -name '.DS_Store' -o \
    -name '*.pyc' -o \
    -name '*.pyo' -o \
    -name '*.tsbuildinfo' -o \
    -name 'trace.zip' \
  \) -prune -exec rm -rf {} +

find "$STAGE" -type f \( \
    -name '.env' -o \
    -name '.env.*' -o \
    -name '.mcp.json' \
  \) ! -name '.env.example' -delete

(
  cd "$TMP"
  zip -qr "$OUT" "$PROJECT"
)

"$PYTHON_BIN" "$ROOT/tools/release_hygiene.py" "$OUT"
echo "created $OUT"
