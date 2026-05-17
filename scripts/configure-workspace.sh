#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# scripts/configure-workspace.sh -- rebind the bundle workspace host anchor
#
# Usage:
#   ./scripts/configure-workspace.sh https://<workspace-host>
#   ./scripts/configure-workspace.sh adb-1234567890123456.7.azuredatabricks.net
#   ./scripts/configure-workspace.sh --dry-run https://<workspace-host>
#
# Test-only / advanced:
#   ./scripts/configure-workspace.sh --file /tmp/databricks.yml https://...
#
# The Databricks bundle schema requires workspace.host to be a literal value
# at bundle-load time, so databricks.yml intentionally centralizes it behind
# the root-level YAML anchor `&default_host`. This helper changes exactly that
# one line and then runs the existing forkability safeguard on the real file.
# ---------------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_FILE="${REPO_ROOT}/databricks.yml"
DRY_RUN=0

usage() {
  sed -n '2,28p' "$0" >&2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --file)
      if [[ $# -lt 2 || -z "$2" ]]; then
        echo "[configure-workspace] missing value for --file" >&2
        exit 2
      fi
      TARGET_FILE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --*)
      echo "[configure-workspace] unknown option: $1" >&2
      usage
      exit 2
      ;;
    *)
      break
      ;;
  esac
done

if [[ $# -ne 1 || -z "${1:-}" ]]; then
  echo "[configure-workspace] expected exactly one workspace host" >&2
  usage
  exit 2
fi

HOST_INPUT="$1"
export TARGET_FILE DRY_RUN HOST_INPUT

python3 <<'PY'
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

path = Path(os.environ["TARGET_FILE"])
dry_run = os.environ["DRY_RUN"] == "1"
raw_host = os.environ["HOST_INPUT"].strip()

if not raw_host:
    raise SystemExit("[configure-workspace] workspace host cannot be empty")
if re.search(r"\s", raw_host):
    raise SystemExit("[configure-workspace] workspace host must not contain whitespace")
if "@" in raw_host:
    raise SystemExit("[configure-workspace] workspace host must not contain credentials")
if "://" not in raw_host:
    raw_host = "https://" + raw_host

parsed = urlparse(raw_host)
if parsed.scheme != "https" or not parsed.netloc:
    raise SystemExit("[configure-workspace] workspace host must be an https URL")
if parsed.username or parsed.password:
    raise SystemExit("[configure-workspace] workspace host must not contain credentials")
if parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.params:
    raise SystemExit("[configure-workspace] workspace host must be only the workspace origin")

normalized = f"https://{parsed.netloc.rstrip('/')}"
if not re.fullmatch(r"https://[A-Za-z0-9.-]+", normalized):
    raise SystemExit("[configure-workspace] workspace host contains unsupported characters")

if not path.exists():
    raise SystemExit(f"[configure-workspace] file not found: {path}")

text = path.read_text(encoding="utf-8")
lines = text.splitlines(keepends=True)
matches = [idx for idx, line in enumerate(lines) if "host: &default_host " in line]
if len(matches) != 1:
    raise SystemExit(
        f"[configure-workspace] expected exactly one '&default_host' host line, found {len(matches)}"
    )

idx = matches[0]
old_line = lines[idx].rstrip("\n")
line_ending = "\n" if lines[idx].endswith("\n") else ""
new_line = f"  host: &default_host {normalized}"
lines[idx] = new_line + line_ending

print(f"[configure-workspace] file: {path}")
print(f"[configure-workspace] old:  {old_line.strip()}")
print(f"[configure-workspace] new:  {new_line.strip()}")

if dry_run:
    print("[configure-workspace] dry-run only; no file written.")
else:
    path.write_text("".join(lines), encoding="utf-8")
    print("[configure-workspace] updated workspace.host anchor.")
PY

if [[ "$DRY_RUN" -eq 0 && "$TARGET_FILE" == "${REPO_ROOT}/databricks.yml" ]]; then
  make check-workspace-host
fi
