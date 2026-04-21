.PHONY: setup dev-api dev-ui test test-e2e lint build validate bundle-validate bundle-deploy zip \
        provision-genie bundle-validate-env bundle-deploy-dev

setup:
	python -m venv .venv
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
	npx playwright test -c playwright.config.ts

lint:
	ruff check backend tests tools
	npm --prefix frontend run lint

build:
	npm --prefix frontend run build

validate:
	python tools/verify_scaffold.py
	pytest -q
	npm --prefix frontend run build

bundle-validate:
	databricks bundle validate -t dev

bundle-deploy:
	databricks bundle deploy -t dev

zip:
	cd .. && zip -r mortgage-intelligence-platform.zip mortgage-intelligence-platform -x '*/node_modules/*' '*/.venv/*' '*/frontend/dist/*'

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
	DATABRICKS_CONFIG_PROFILE=DEFAULT python tools/databricks/provision_genie_space.py --profile DEFAULT

# The env-wired targets delegate to tools/databricks/bundle_env.py which uses
# python-dotenv to parse .env.local safely (tolerates unquoted spaces and
# angle-bracket placeholder values). It maps DATABRICKS_WAREHOUSE_ID and
# GENIE_SPACE_ID to BUNDLE_VAR_sql_warehouse_id / BUNDLE_VAR_genie_space_id
# before invoking the Databricks CLI.
bundle-validate-env:
	@python tools/databricks/bundle_env.py validate -t dev

bundle-deploy-dev:
	@read -p "About to DEPLOY to your workspace. Continue? [y/N] " ans; \
	  test "$$ans" = "y" || { echo "aborted."; exit 1; }; \
	  python tools/databricks/bundle_env.py deploy -t dev
