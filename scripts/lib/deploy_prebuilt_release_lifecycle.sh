# shellcheck shell=bash
# The prebuilt release (M1-PR7): deploy-dev.yml's deploy job holds an ID token
# and runs no Node and no npm, so the build job's frontend/dist and staged App
# runtime closure reach it as one artifact with a sha256 manifest
# (tools/databricks/prebuilt_release.py). With --prebuilt-release <dir>, the
# manifest's own sha256 must be MIP_PREBUILT_RELEASE_SHA256 (the build job's
# output, one run only, never .env.local), its commit this checkout's and its
# wheelhouse digest this tree's requirements-deploy.hashes.txt. Every file is
# re-hashed before any Databricks call, and again in step 1 right before the
# copy takes the place of the build and the stage; any difference refuses.
# Each check also re-derives the staged App source from this checkout's own
# commit (git blobs, byte for byte) and reviews the dist again: the build job
# runs npm, so only the dist's bytes are taken from it.
# A tenant build input vite bakes into the dist refuses (see below), before
# any Databricks call and again in step 1.
# Sourced by scripts/deploy.sh; this file must not change shell options or traps.

PREBUILT_RELEASE_REQUIREMENTS="requirements-deploy.hashes.txt"
# A prebuilt release carries none of the tenant build inputs vite bakes into
# the dist (frontend/vite.config.ts): the lender mark is a local file
# (tools/branding/lender_mark.py), and the build job sets no MIP_DEFAULT_THEME
# or MIP_DEFAULT_ACCENT, so its dist wears these product defaults
# (frontend/src/lib/tenantAppearancePlugin.ts; a test pins them equal). A
# configured mark, or another theme or accent, refuses and builds locally.
PREBUILT_RELEASE_DEFAULT_THEME="dark"
PREBUILT_RELEASE_DEFAULT_ACCENT="bright"

# $1 the lender mark (its file before the preflight, its sha256 after), $2
# the theme and $3 the accent (empty is the default), $4 what was changed.
refuse_prebuilt_tenant_build_inputs() {
  if [[ -n "$1" ]]; then
    echo "${RED}[deploy] a lender mark is configured, and a prebuilt release carries none (the mark is a local file); build this release locally. $4${RST}" >&2
    exit 2
  fi
  if [[ "${2:-$PREBUILT_RELEASE_DEFAULT_THEME}" != "$PREBUILT_RELEASE_DEFAULT_THEME" || \
        "${3:-$PREBUILT_RELEASE_DEFAULT_ACCENT}" != "$PREBUILT_RELEASE_DEFAULT_ACCENT" ]]; then
    echo "${RED}[deploy] a tenant default theme or accent is configured, and a prebuilt release wears the product defaults (vite bakes both into the dist; the build job sets neither); build this release locally. $4${RST}" >&2
    exit 2
  fi
}

prebuilt_release_args() {
  printf '%s\n' --root "${PREBUILT_RELEASE_DIR:-}" --git-sha "$SOURCE_GIT_SHA" \
    --requirements "$PREBUILT_RELEASE_REQUIREMENTS" \
    --expect-manifest-sha256 "${MIP_PREBUILT_RELEASE_SHA256:-}"
}

# Before any Databricks call, in a dry run too (it reads local files only).
verify_prebuilt_release() {
  local -a args=()
  if [[ ! "${MIP_PREBUILT_RELEASE_SHA256:-}" =~ ^[0-9a-f]{64}$ ]]; then
    echo "${RED}[deploy] --prebuilt-release needs MIP_PREBUILT_RELEASE_SHA256, the 64-hex sha256 of its manifest that the build job printed; refusing before any Databricks call.${RST}" >&2
    exit 2
  fi
  # The preflight's own reads of the three inputs: a live run must not reach
  # the lease, the rollback scope or the runtime secrets first.
  refuse_prebuilt_tenant_build_inputs "$(deployment_control_value MIP_LENDER_MARK_FILE)" \
    "$(deployment_control_value MIP_DEFAULT_THEME)" "$(deployment_control_value MIP_DEFAULT_ACCENT)" \
    "Refusing before any Databricks call; nothing was changed."
  mapfile -t args < <(prebuilt_release_args)
  echo "${DIM}\$ $PYTHON -m tools.databricks.prebuilt_release verify --root <prebuilt release> --git-sha ${SOURCE_GIT_SHA} ...${RST}"
  if ! "$PYTHON" -m tools.databricks.prebuilt_release verify "${args[@]}"; then
    echo "${RED}[deploy] the prebuilt release is not the one its manifest describes, or its staged source is not this checkout's commit; refusing before any Databricks call. Nothing was changed.${RST}" >&2
    exit 2
  fi
}

# Step 1, in place of the frontend build and the closure stage: re-hash the
# release again, then copy its dist and staged closure where both would land.
install_prebuilt_release() {
  local -a args=()
  # Defence in depth: the values the preflight resolved and exported.
  refuse_prebuilt_tenant_build_inputs "${MIP_LENDER_MARK_SHA256:-}" "${MIP_DEFAULT_THEME:-}" "${MIP_DEFAULT_ACCENT:-}" \
    "Nothing more was changed."
  mapfile -t args < <(prebuilt_release_args)
  echo "${DIM}\$ $PYTHON -m tools.databricks.prebuilt_release install --root <prebuilt release> --git-sha ${SOURCE_GIT_SHA} ... --dist-out frontend/dist --stage-out ${APP_RELEASE_STAGE_ROOT}${RST}"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  would re-hash the prebuilt release and place its dist and staged closure (no npm, no stage)"
    return 0
  fi
  if ! "$PYTHON" -m tools.databricks.prebuilt_release install "${args[@]}" \
      --dist-out frontend/dist --stage-out "$APP_RELEASE_STAGE_ROOT"; then
    echo "${RED}[deploy] the prebuilt release changed after its first check; refusing. Nothing more was changed.${RST}" >&2
    exit 2
  fi
}
