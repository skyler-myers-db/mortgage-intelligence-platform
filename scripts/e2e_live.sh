#!/usr/bin/env bash
set -euo pipefail

APP_URL="${MIP_APP_URL:-https://mip-app-2543889327043640.aws.databricksapps.com}"
API_URL="${MIP_API_URL:-$APP_URL}"
DATABRICKS_PROFILE="${DATABRICKS_PROFILE:-DEFAULT}"
groups=(
  "API payloads|dashboard renders|segment multi-select|segment map drill|segment secondary|home map|ranked borrower"
  "genie FAB|brand favicon|approve outreach|forcing a 503|portfolio-builder|lead-queue: rows render|lead-queue: row-preview"
  "lead-queue: inline approval|borrower-360|offer-orchestrator|ask-genie: standalone"
  "ask-genie: shows governed|ask-genie: dynamic chart|ask-genie: open cohort|admin-config"
)

for i in "${!groups[@]}"; do
  group_no=$((i + 1))
  group_count=${#groups[@]}
  token="$(databricks auth token --profile "$DATABRICKS_PROFILE" -o json | jq -r .access_token)"
  if [[ -z "$token" || "$token" == "null" ]]; then
    echo "Failed to obtain Databricks bearer token for profile $DATABRICKS_PROFILE" >&2
    exit 1
  fi

  echo "Running live Playwright group $group_no/$group_count against $APP_URL"
  E2E_LIVE=1 \
  MIP_APP_URL="$APP_URL" \
  MIP_API_URL="$API_URL" \
  MIP_BEARER_TOKEN="$token" \
  npm --prefix frontend run e2e:ci -- --grep "${groups[$i]}" real_data.spec.ts
done
