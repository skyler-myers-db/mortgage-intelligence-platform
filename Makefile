.PHONY: setup dev-api dev-ui test lint build validate bundle-validate bundle-deploy zip \
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

bundle-validate-env:
	@set -a; [ -f .env.local ] && . ./.env.local; set +a; \
	  BUNDLE_VAR_sql_warehouse_id="$${DATABRICKS_WAREHOUSE_ID:-00000000PLACEHOLDER}" \
	  BUNDLE_VAR_genie_space_id="$${GENIE_SPACE_ID:-00000000PLACEHOLDER}" \
	  databricks bundle validate -t dev

bundle-deploy-dev:
	@set -a; [ -f .env.local ] && . ./.env.local; set +a; \
	  read -p "About to DEPLOY to your workspace. Continue? [y/N] " ans; \
	  test "$$ans" = "y" || { echo "aborted."; exit 1; }; \
	  BUNDLE_VAR_sql_warehouse_id="$${DATABRICKS_WAREHOUSE_ID:-00000000PLACEHOLDER}" \
	  BUNDLE_VAR_genie_space_id="$${GENIE_SPACE_ID:-00000000PLACEHOLDER}" \
	  databricks bundle deploy -t dev
