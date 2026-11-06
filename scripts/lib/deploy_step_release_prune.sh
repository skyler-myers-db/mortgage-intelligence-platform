# shellcheck shell=bash
# Deploy step file. Step 11b: prune App release folders after a green capture (best effort),
# in either tier (moved verbatim from the agentic step file, M2-PR7b).
# Sourced by scripts/deploy.sh in reviewed order; it runs in the entrypoint's
# shell with its strict mode, traps, and private variables, never on its own.
[[ "${BASH_SOURCE[0]}" != "$0" ]] || {
  echo "[deploy] ${BASH_SOURCE[0]} is a step file; run ./scripts/deploy.sh instead." >&2
  exit 2
}

# >>> app release source: prune >>>
# Best effort, and only after a green capture: the rollback branch above
# never sets green_verified. tools/databricks/app_release_prune.py deletes,
# as the deploy identity, only release-id DIRECTORY children of
# <bundle root>/app-src that no retained deployment references. It keeps the
# current release, the signed rollback record's folder, the folder the App's
# default source names and the two newest other referenced ones, re-checks
# the lease and the App before each delete, and stops on any uncertainty,
# deleting nothing more (one in its first reads deletes nothing at all).
# Rollback never depends on these folders: a signed record restores the
# deployment's own /src/ snapshot. The call sits in an `if` condition, so
# neither errexit nor the ERR trap can fire on a prune failure; the deploy
# only warns.
if [[ "$DRY_RUN" -eq 1 || "$APP_UPGRADE_STATE" == "green_verified" ]]; then
  step "prune App release folders no retained deployment references (best effort)"
  if ! run "$PYTHON" -m tools.databricks.app_release_prune \
    --app-name "$APP_NAME" \
    --expected-app-id "${APP_OBJECT_ID:-dry-run-app-id}" \
    --current-release "$APP_RELEASE_ID" \
    --rollback-deployment-id "${MIP_APP_ROLLBACK_DEPLOYMENT_ID:-}" \
    --deployment-lease-id "${MIP_APP_DEPLOYMENT_LEASE_ID:-}" \
    --source-git-sha "$SOURCE_GIT_SHA" \
    --files-root "$APP_BUNDLE_FILES_PATH" \
    --expected-principal "$APP_RELEASE_PRINCIPAL" \
    --expected-target "$TARGET" \
    --expected-root-leaf "$APP_RELEASE_ROOT_LEAF"; then
    echo "${YLW}[deploy] App release folder prune did not finish; the release is live and nothing it keeps was touched. A later green run retries.${RST}" >&2
  fi
fi
# <<< app release source: prune <<<
