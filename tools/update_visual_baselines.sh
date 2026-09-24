#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Regenerate (default) or compare (--check) the pixel baselines of
# frontend/tests/e2e/fixture/visual.fixture.spec.ts inside the pinned
# Playwright container (audit visual-10, quality-02, responsive-v2).
#
# Baselines are amd64-Linux renders: the e2e-visual CI job compares them in
# mcr.microsoft.com/playwright:v<@playwright/test pin>-noble on an amd64
# runner, and the spec refuses any other host. This script runs the same
# image with --platform linux/amd64 (on Apple Silicon start colima with
# `--arch x86_64` or `--vm-type vz --vz-rosetta`; emulation is 3-5x slower).
#
# The repo is NEVER bind-mounted: a container `npm ci` would replace the
# host's macOS node_modules with Linux binaries and leave root-owned files.
# The working tree (tracked plus untracked, non-ignored files, uncommitted
# edits included) is streamed in with tar; only an empty temporary output
# directory is mounted. Each container has its own network namespace, so
# parallel runs never share a port.
#
#   bash tools/update_visual_baselines.sh           # regenerate every baseline
#   bash tools/update_visual_baselines.sh --check   # compare only; exit 1 on a diff
#
# Update mode empties the snapshots directory first, so orphans disappear and
# the directory matches the spec's captures exactly. It regenerates with
# --update-snapshots=all on that empty directory: `missing` would write the
# same files but, in Playwright 1.59, fails every test that wrote one, so the
# run's other assertions (surface overflow, audited reads, hover) would be
# unreadable. Review the diff (git status / an image diff tool) before
# committing; a PNG merge conflict is resolved only by regenerating on the
# merged tree. See docs/testing.md ("Visual regression").
# ---------------------------------------------------------------------------
set -euo pipefail

usage() {
  echo "usage: bash tools/update_visual_baselines.sh [--check]" >&2
}

mode=update
case "${1:-}" in
  '') ;;
  --check) mode=check ;;
  -h|--help) usage; exit 0 ;;
  *) usage; exit 2 ;;
esac
if [ "$#" -gt 1 ]; then
  usage
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

snapshots_rel='frontend/tests/e2e/fixture/visual.fixture.spec.ts-snapshots'

# The image follows the EXACT @playwright/test pin, as the spec's guard does.
pin="$(node -e "process.stdout.write(require('./frontend/package.json').devDependencies['@playwright/test'])")"
case "$pin" in
  [0-9]*.[0-9]*.[0-9]*) ;;
  *)
    echo "frontend/package.json pins @playwright/test as '$pin'; an exact x.y.z pin is required" >&2
    exit 1
    ;;
esac
image="mcr.microsoft.com/playwright:v${pin}-noble"

command -v docker >/dev/null 2>&1 || { echo "docker is not installed" >&2; exit 1; }
docker info >/dev/null 2>&1 || {
  echo "the docker daemon is not reachable (on this Mac: colima start --arch x86_64)" >&2
  exit 1
}

out_dir="$(mktemp -d "${TMPDIR:-/tmp}/mip-vrt.XXXXXX")"
trap 'rm -rf "$out_dir"' EXIT

# Runs inside the container, in /work. Single-quoted on purpose: every
# variable below is expanded by the container's bash, from the -e flags.
# shellcheck disable=SC2016
inner='
set -euo pipefail
mkdir -p /work
tar -x --warning=no-unknown-keyword -C /work
cd /work
npm --prefix frontend ci --no-audit --no-fund
npm --prefix frontend run build
update_flag=""
if [ "$VRT_MODE" = update ]; then
  rm -rf "$SNAPSHOTS_REL"
  update_flag="--update-snapshots=all"
fi
status=0
(
  cd frontend
  E2E_FIXTURE=1 MIP_VRT=1 E2E_FIXTURE_PORT=4273 npx playwright test visual.fixture --workers=2 $update_flag
) || status=$?
if [ "$VRT_MODE" = update ] && [ -d "$SNAPSHOTS_REL" ]; then
  cp -R "$SNAPSHOTS_REL" /out/snapshots
fi
if [ "$status" -ne 0 ]; then
  [ -d frontend/playwright-report/vrt ] && cp -R frontend/playwright-report/vrt /out/report
  [ -d frontend/test-results/vrt ] && cp -R frontend/test-results/vrt /out/test-results
fi
chown -R "$HOST_UID:$HOST_GID" /out 2>/dev/null || true
exit "$status"
'

echo "== $mode: $image (linux/amd64), tree streamed from $repo_root"
status=0
# Tracked plus untracked, non-ignored files that exist (a deleted tracked
# file stays in `ls-files -c` until it is staged). COPYFILE_DISABLE keeps
# macOS tar from adding AppleDouble files.
git ls-files -co --exclude-standard -z \
  | while IFS= read -r -d '' path; do
      if [ -f "$path" ] || [ -L "$path" ]; then printf '%s\0' "$path"; fi
    done \
  | COPYFILE_DISABLE=1 tar --null -T - -c -f - \
  | docker run --rm -i --init --ipc=host --platform linux/amd64 \
      -e "VRT_MODE=$mode" \
      -e "SNAPSHOTS_REL=$snapshots_rel" \
      -e "MIP_VRT_IMAGE=$image" \
      -e "HOST_UID=$(id -u)" \
      -e "HOST_GID=$(id -g)" \
      -v "$out_dir:/out" \
      "$image" bash -c "$inner" || status=$?

if [ "$mode" = update ] && [ -d "$out_dir/snapshots" ]; then
  rm -rf "$snapshots_rel"
  cp -R "$out_dir/snapshots" "$snapshots_rel"
  count="$(find "$snapshots_rel" -name '*.png' | wc -l | tr -d ' ')"
  echo "== wrote $count baselines to $snapshots_rel"
  git status --short -- "$snapshots_rel"
fi

if [ "$status" -ne 0 ]; then
  report_dir="frontend/playwright-report/vrt"
  if [ -d "$out_dir/report" ]; then
    rm -rf "$report_dir"
    mkdir -p "$(dirname "$report_dir")"
    cp -R "$out_dir/report" "$report_dir"
    echo "== diff report: npx --prefix frontend playwright show-report $report_dir" >&2
  fi
  if [ -d "$out_dir/test-results" ]; then
    rm -rf frontend/test-results/vrt
    mkdir -p frontend/test-results
    cp -R "$out_dir/test-results" frontend/test-results/vrt
  fi
  echo "== visual run FAILED (exit $status)" >&2
fi
exit "$status"
