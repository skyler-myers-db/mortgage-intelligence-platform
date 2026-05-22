# Use the project venv's python by default so `make` works on machines where
# only `python3` is on PATH. Override by running `make PYTHON=python3 …`.
PYTHON ?= $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)

.PHONY: setup dev-api dev-ui test test-e2e lint build validate bundle-validate bundle-plan bundle-deploy zip \
        provision-genie bundle-validate-env bundle-deploy-dev deploy-dev check-workspace-host \
        configure-workspace render-sql

setup:
	python3 -m venv .venv
	. .venv/bin/activate && pip install -r requirements.txt
	npm --prefix frontend install

dev-api:
	uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

dev-ui:
	npm --prefix frontend run dev

test:
	pytest -q
	npm --prefix frontend run test

# End-to-end Playwright — pins the Module 0 DAIS golden path.
# Assumes uvicorn + vite are already running (or Playwright's `webServer`
# block in playwright.config.ts will boot them). Run from the repo root.
test-e2e:
	npm --prefix frontend run e2e

lint:
	ruff check backend tests tools
	npm --prefix frontend run lint

build:
	npm --prefix frontend run build

validate:
	$(PYTHON) tools/verify_scaffold.py
	pytest -q
	npm --prefix frontend run build

# `render-sql` materializes sql/_rendered/** from sql/** for the target UC
# catalog. The bundle's SQL tasks read from sql/_rendered/** so a customer
# who sets MIP_DEFAULT_CATALOG=<their_catalog> gets CTAS statements that
# write to the right place without any manual sed step. Idempotent.
render-sql:
	$(PYTHON) tools/render_sql.py --catalog "$${MIP_DEFAULT_CATALOG:-mip}"

bundle-validate: render-sql
	$(PYTHON) tools/databricks/bundle_env.py validate -t dev

bundle-plan: render-sql
	$(PYTHON) tools/databricks/bundle_env.py plan -t dev

bundle-deploy: render-sql
	$(PYTHON) tools/databricks/bundle_env.py deploy -t dev

zip:
	./scripts/package_source.sh

# ---------------------------------------------------------------------------
# Genie Space + env-wired bundle targets
#
# `provision-genie`       programmatically creates/updates the Genie Space
#                         described by genie/mortgage_lead_intelligence_space.yml
#                         using the Databricks SDK. See the tool's docstring
#                         for auth resolution rules.
# `bundle-validate-env`   sources .env.local (if present), exports
#                         DATABRICKS_WAREHOUSE_ID and GENIE_SPACE_ID into
#                         bundle variables, then validates the dev target.
# `bundle-deploy-dev`     same env sourcing, then deploys to the dev target
#                         after an interactive confirmation.
# ---------------------------------------------------------------------------

provision-genie:
	DATABRICKS_CONFIG_PROFILE=DEFAULT $(PYTHON) tools/databricks/provision_genie_space.py --profile DEFAULT

# The env-wired targets delegate to tools/databricks/bundle_env.py which uses
# python-dotenv to parse .env.local safely (tolerates unquoted spaces and
# angle-bracket placeholder values). It maps DATABRICKS_WAREHOUSE_ID and
# GENIE_SPACE_ID to BUNDLE_VAR_sql_warehouse_id / BUNDLE_VAR_genie_space_id
# before invoking the Databricks CLI.
bundle-validate-env: render-sql
	@$(PYTHON) tools/databricks/bundle_env.py validate -t dev

bundle-deploy-dev: render-sql
	@read -p "About to DEPLOY to your workspace. Continue? [y/N] " ans; \
	  test "$$ans" = "y" || { echo "aborted."; exit 1; }; \
	  $(PYTHON) tools/databricks/bundle_env.py deploy -t dev

# ---------------------------------------------------------------------------
# `deploy-dev` — single-command zero-click dev deploy.
#
# Delegates to scripts/deploy.sh, which chains bundle validate + deploy +
# silver refresh + Lakebase migrate + gold refresh + lifecycle sync +
# Genie provisioning + live smoke, all idempotent. This is the one
# command an operator runs to stand a workspace up from scratch or to
# roll forward after a code change. Every step fails loudly and the
# script can be safely re-run.
#
# For finer-grained control, scripts/deploy.sh accepts:
#     --dry-run       # print the plan, make no changes
#     --skip-silver   # skip the FRED + share refresh (fast path)
#     --skip-smoke    # skip the post-deploy curl smoke test
#     --no-confirm    # skip the interactive y/N prompt
#
# `provision-genie` above is kept as a separate target for operators who
# want to rebind only the Genie space (e.g. after editing
# genie/mortgage_lead_intelligence_space.yml) without re-deploying the
# bundle. Running `deploy-dev` is a strict superset.
# ---------------------------------------------------------------------------
deploy-dev:
	./scripts/deploy.sh

# Rebind the one workspace.host YAML anchor in databricks.yml for a customer
# fork. Usage:
#   make configure-workspace HOST=https://<customer-workspace>
configure-workspace:
	@test -n "$${HOST:-}" || { echo "usage: make configure-workspace HOST=https://<customer-workspace>"; exit 2; }
	./scripts/configure-workspace.sh "$${HOST}"

# ---------------------------------------------------------------------------
# Forkability safeguard (audit R5-24, 2026-04-23).
#
# All four `workspace.host:` values in databricks.yml dereference the
# root-level `&default_host` YAML anchor — a customer forking the repo
# edits EXACTLY ONE line to rebind the host. This target greps for the
# Entrada hostname outside the anchor's declaration line (the `&default_host`
# line is the one legitimate occurrence). Fails the build if any other
# line in databricks.yml still contains the literal — which would mean an
# SE has edited the file and re-introduced a duplicate string, or future
# code has added a new host reference that bypassed the anchor.
#
# Usage:
#   make check-workspace-host         # fails if stray hostname found
#
# Wire into CI before any customer-fork deploy.
# ---------------------------------------------------------------------------
check-workspace-host:
	@STRAY=$$(grep -n "dbc-3aa503a9-4fa8.cloud.databricks.com" databricks.yml | grep -v "&default_host" || true); \
	  if [ -n "$$STRAY" ]; then \
	    echo "[check-workspace-host] FAIL: stray Entrada hostname found outside the anchor:"; \
	    echo "$$STRAY"; \
	    echo "[check-workspace-host] fix: replace with '*default_host' and keep the single &default_host declaration."; \
	    exit 1; \
	  fi; \
	  echo "[check-workspace-host] OK: workspace.host is anchored."
