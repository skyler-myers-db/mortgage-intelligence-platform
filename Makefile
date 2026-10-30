# Use the project venv's python by default so `make` works on machines where
# only `python3` is on PATH. Override by running `make PYTHON=python3 …`.
PYTHON ?= $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)

.PHONY: setup dev-api dev-ui test test-e2e lint lint-shell build validate bundle-validate bundle-plan bundle-deploy zip \
        provision-genie bundle-validate-env bundle-deploy-dev deploy-dev render-sql

setup:
	python3 -m venv .venv
	. .venv/bin/activate && pip install -r requirements-dev.txt
	npm --prefix frontend install

dev-api:
	uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

dev-ui:
	npm --prefix frontend run dev

test:
	$(PYTHON) -m pytest -q
	npm --prefix frontend run test

# End-to-end Playwright — pins the Module 0 DAIS golden path.
# Assumes uvicorn + vite are already running (or Playwright's `webServer`
# block in playwright.config.ts will boot them). Run from the repo root.
test-e2e:
	npm --prefix frontend run e2e

lint:
	$(PYTHON) -m ruff check backend tests tools
	npm --prefix frontend run lint
	$(MAKE) lint-shell

# Shell lint. `git ls-files` is the file list on purpose: it tracks new
# scripts automatically and never walks dist/ or node_modules.
# Fails loudly when shellcheck is absent rather than skipping — a lint gate
# that silently passes when its linter is missing is worse than no gate.
lint-shell:
	@command -v shellcheck >/dev/null 2>&1 || { \
	  echo "shellcheck not found. Install it: brew install shellcheck (macOS) / apt-get install shellcheck (Debian)"; \
	  exit 1; \
	}
	shellcheck --severity=warning $$(git ls-files '*.sh')

# 2026-06-11 audit P2-14: ratcheted mypy gate — exemption list in
# pyproject.toml [tool.mypy] overrides may only shrink.
typecheck:
	$(PYTHON) -m mypy backend

build:
	npm --prefix frontend run build

validate:
	$(PYTHON) tools/verify_scaffold.py
	$(PYTHON) -m pytest -q
	npm --prefix frontend run build

# `render-sql` materializes sql/_rendered/** from sql/** for the target UC
# catalog. The bundle's SQL tasks read from sql/_rendered/** so a customer
# who sets MIP_DEFAULT_CATALOG=<their_catalog> gets CTAS statements that
# write to the right place without any manual sed step. Idempotent.
render-sql:
	$(PYTHON) tools/render_sql.py --catalog "$${MIP_DEFAULT_CATALOG:-mip}"

# Local validation validates the `client` target (M1-PR2) against the
# workspace the operator's own CLI auth names, with the root, run_as and names
# of the filled client profile in .env.local (tools/databricks/bundle_env.py).
# The CLI authenticates (SCIM Me) and, when the root has no `files` folder yet,
# creates that empty folder, so it is not a purely offline check. The `ci`
# target cannot stand in: its host is an RFC 2606 `.invalid` placeholder, so a
# real validate of it never reaches a workspace. A bundle plan of the `client`
# target runs only inside the governed deploy, never from a workstation default.
bundle-validate: render-sql
	$(PYTHON) tools/databricks/bundle_env.py validate -t client

bundle-plan: render-sql
	@echo "bundle-plan is retired: the client target is planned only inside the governed deploy; use make bundle-validate or ./scripts/deploy.sh -t client --dry-run" >&2
	@exit 2

bundle-deploy: render-sql
	@echo "bundle-deploy is retired: use make deploy-dev for the signed command-of-record flow" >&2
	@exit 2

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
#                         bundle variables, then validates the client
#                         target, as bundle-validate does.
# `bundle-deploy-dev`     retired unsafe alias; use `deploy-dev`.
# ---------------------------------------------------------------------------

provision-genie:
	DATABRICKS_CONFIG_PROFILE=DEFAULT $(PYTHON) tools/databricks/provision_genie_space.py --profile DEFAULT

# The env-wired targets delegate to tools/databricks/bundle_env.py which uses
# python-dotenv to parse .env.local safely (tolerates unquoted spaces and
# angle-bracket placeholder values). It maps DATABRICKS_WAREHOUSE_ID and
# GENIE_SPACE_ID to BUNDLE_VAR_sql_warehouse_id / BUNDLE_VAR_genie_space_id
# before invoking the Databricks CLI.
bundle-validate-env: render-sql
	@$(PYTHON) tools/databricks/bundle_env.py validate -t client

bundle-deploy-dev: render-sql
	@echo "bundle-deploy-dev is retired: use make deploy-dev for signed App promotion" >&2
	@exit 2

# ---------------------------------------------------------------------------
# `deploy-dev` — print the governed deploy's dispatch command (M1-PR2).
#
# A workstation no longer deploys. The command of record is
# `./scripts/deploy.sh -t client`, run in CI per client environment (the
# deploy-dev workflow, parked until M1-PR7 selects one). Locally, use
# `./scripts/deploy.sh -t client --dry-run` to print the plan and
# `make bundle-validate` to validate the bundle. The live install that
# predates the client target ships only through the emergency recipe in
# docs/deployment.md until it is adopted (M2-PR6). This target makes no change
# and exits 2, so nothing mistakes it for a deploy.
# ---------------------------------------------------------------------------
deploy-dev:
	@echo "deploy-dev does not deploy from a workstation. Dispatch the governed deploy in CI:" >&2
	@echo "  gh workflow run deploy-dev.yml --ref main -f acknowledge_single_maintainer_break_glass=true" >&2
	@echo "(parked until M1-PR7 selects a client environment; dry run locally with ./scripts/deploy.sh -t client --dry-run)" >&2
	@exit 2
