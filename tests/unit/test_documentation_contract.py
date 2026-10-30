"""Documentation contract tests for operator-facing Module 0 docs."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from urllib.parse import unquote

from tests.fixtures.deploy_script import deploy_entrypoint_text

ROOT = Path(__file__).resolve().parents[2]

CURRENT_OPERATOR_DOCS = [
    ROOT / "README.md",
    ROOT / "SECURITY.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "CLAUDE.md",
    ROOT / "docs" / "se-onboarding.md",
    ROOT / "docs" / "runbook.md",
    ROOT / "docs" / "runbook-multi-catalog.md",
    ROOT / "docs" / "disaster-recovery.md",
    ROOT / "docs" / "deployment.md",
    ROOT / "docs" / "load-baseline.md",
    ROOT / "docs" / "validation" / "load-baseline.md",
    ROOT / "docs" / "module0-talk-track.md",
    ROOT / "docs" / "observability.md",
    ROOT / "docs" / "credential-kill-drill.md",
    ROOT / "docs" / "dashboards.md",
    ROOT / "docs" / "data-contract.md",
    ROOT / "docs" / "implementation-plan.md",
    ROOT / "docs" / "testing.md",
    ROOT / "docs" / "module0-rehearsal-checklist.md",
]

CURRENT_DEPLOYMENT_GUIDANCE_SURFACES = [
    ROOT / "databricks.yml",
    ROOT / "backend" / "services" / "lakebase_bootstrap.py",
    ROOT / "jobs" / "fred_rates_ingest.py",
    ROOT / "sql" / "ddl" / "004_ref_tables.sql",
    ROOT / "sql" / "ddl" / "005_semantics_views.sql",
    ROOT / "sql" / "transformations" / "gold_borrower_dossier.sql",
    ROOT / "tools" / "render_sql.py",
    ROOT / "tools" / "databricks" / "otlp_deploy_payload.py",
]

UNVERSIONED_API_RE = re.compile(
    r"(?<!v1)/api/"
    r"(?:health|admin/health|admin/|data-estate|config/options|leads|borrowers|"
    r"segments|portfolio|outreach|genie|audit|sales|geo|telemetry)"
)
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_security_policy_is_substantive_and_actionable() -> None:
    text = _read(ROOT / "SECURITY.md")

    assert len(text.splitlines()) >= 50
    for required in (
        "security@entrada.ai",
        "Acknowledgment within 1 business day",
        "Initial triage response within 5 business days",
        "/api/v1/*",
        "HMAC",
        "append-only",
        "MIP_EXPOSE_OPENAPI",
        "docs/security-and-compliance.md",
        "docs/disaster-recovery.md",
    ):
        assert required in text


def test_contributing_policy_documents_regression_gates() -> None:
    text = _read(ROOT / "CONTRIBUTING.md")

    assert len(text.splitlines()) >= 120
    for required in (
        "conventional commit",
        "CHANGELOG.md",
        "test_architecture_boundaries.py",
        "test_supply_chain_licenses.py",
        "test_openapi_contract.py",
        "test_load_test_contract.py",
        "test_documentation_contract.py",
        "ROUTE_TEST_MANIFEST",
        "openapi_baseline.json",
        "tools/load_test/baseline.json",
    ):
        assert required in text


def test_readme_surfaces_current_operator_entrypoints() -> None:
    text = _read(ROOT / "README.md")

    for required in (
        "/api/v1/health",
        "X-API-Version: v1",
        "MIP_LENDER_NAME",
        "MIP_LENDER_NMLS_ID",
        "MIP_TENANT_ID",
        "source-controlled registry",
        "backend/schemas/lender_identity.py",
        "docs/se-onboarding.md",
        "docs/disaster-recovery.md",
        "docs/load-baseline.md",
        "docs/security-and-compliance.md",
        "CONTRIBUTING.md",
        "CHANGELOG.md",
    ):
        assert required in text


EMERGENCY_RECIPE_HEADING = "### The emergency recipe, command by command (legacy dev root)"


def _without_the_emergency_recipe_block(path: Path, text: str) -> str:
    """docs/deployment.md minus the one fenced block of the emergency recipe.

    That block is the owner-sanctioned way to ship the live install until it
    is adopted (M1-PR2); tests/unit/test_docs_recipe_commands.py runs each of
    its commands. Exactly one ``databricks apps deploy`` line may sit in it,
    and nothing else is exempt.
    """

    if path != ROOT / "docs" / "deployment.md":
        return text
    start = text.index("```bash\n", text.index(EMERGENCY_RECIPE_HEADING))
    end = text.index("\n```\n", start) + len("\n```\n")
    block = text[start:end]
    assert len(re.findall(r"databricks\s+(?:apps\s+deploy|bundle\s+deploy)\b", block)) == 1, block
    assert re.search(r"(?m)^databricks apps deploy <app-name> --json @<payload> -p <profile>$", block)
    return text[:start] + text[end:]


def test_current_operator_docs_never_advertise_bare_mutable_deployment() -> None:
    violations: list[str] = []
    command = re.compile(r"databricks\s+(?:apps\s+deploy|bundle\s+deploy)\b")
    for path in (*CURRENT_OPERATOR_DOCS, *CURRENT_DEPLOYMENT_GUIDANCE_SURFACES):
        text = _without_the_emergency_recipe_block(path, _read(path))
        if command.search(text):
            violations.append(str(path.relative_to(ROOT)))
        assert "promote it from the Databricks Apps deployment UI" not in text
    assert violations == []
    assert not re.search(
        r"post-bootstrap\s+resource-recovery\s*#?\s*bundle\s+deploy",
        _read(ROOT / "databricks.yml"),
        re.IGNORECASE,
    )

    for relative in ("README.md", "docs/runbook.md", "docs/se-onboarding.md"):
        text = _read(ROOT / relative)
        assert "./scripts/deploy.sh -t client" in text
        assert "./scripts/deploy.sh -t dev" not in text, "the dev target left databricks.yml (M1-PR2)"


# A raw `databricks bundle <verb> ... -t <target>` in prose or a code block.
_RAW_BUNDLE_COMMAND = re.compile(
    r"databricks bundle (?:run|summary|validate|plan|deploy|destroy)\b[^`\n]*?-t\s+(\w+)"
)


def test_operator_docs_run_raw_bundle_commands_only_on_the_client_target_with_its_root_exported() -> None:
    """The dev and prod targets left databricks.yml in M1-PR2, and the raw CLI reads no .env.local.

    So a raw bundle command names ``-t client``, and every doc that holds one
    says to export the client target's root variables first. Validation that
    reads the profile goes through ``bundle_env.py`` (``make bundle-validate``).
    """

    surfaces = (*CURRENT_OPERATOR_DOCS, ROOT / "AGENTS.md", ROOT / "docs" / "agentic-workflow.md")
    for path in surfaces:
        text = _read(path)
        targets = _RAW_BUNDLE_COMMAND.findall(text)
        assert set(targets) <= {"client"}, (path.relative_to(ROOT), targets)
        if targets:
            assert "BUNDLE_VAR_client_key" in text and "BUNDLE_VAR_deployer_principal" in text, path
    claude = _read(ROOT / "CLAUDE.md")
    assert "- `databricks bundle validate -t client` passes" not in claude
    assert "- `tools/databricks/bundle_env.py validate -t client` (`make bundle-validate`) passes" in claude
    deployment = " ".join(_read(ROOT / "docs" / "deployment.md").split())
    assert "`make bundle-plan` remain read-only diagnostics" not in deployment, "bundle-plan exits 2"
    assert "`make bundle-plan` and the mutable `make bundle-deploy`" in deployment


def test_deployment_doc_pins_the_prebuilt_app_source_contract() -> None:
    deployment = _read(ROOT / "docs" / "deployment.md")
    app_yaml = _read(ROOT / "app.yaml")
    bundle = _read(ROOT / "databricks.yml")

    for required in (
        "## App source contract (prebuilt frontend, no root npm manifest)",
        "converge_static_app_source.py",
        "list files timed out after 1m0s",
        "campaignPrefill.test.ts",
        "opportunityScore.test.ts",
        "campaign_prefill_segment_parity.json",
        "score_band_golden.json",
        "EBADENGINE",
    ):
        assert required in deployment
    # app.yaml's pre-2026-08-05 claim ("deliberately has no root npm manifest
    # or runtime build command") was falsified in the field: a stale root
    # manifest in the promoted tree DOES trigger the platform npm rebuild,
    # which overwrites the prebuilt dist. Both config surfaces must document
    # the trigger and point at the contract instead of denying the hazard.
    assert "deliberately has no root npm manifest" not in app_yaml
    for surface in (app_yaml, bundle):
        assert "converge_static_app_source" in surface
        assert 'docs/deployment.md "App source contract"' in surface


def test_deployment_doc_pins_the_governed_per_release_app_source() -> None:
    from tools.databricks import app_release_prune, app_release_source

    deployment = _read(ROOT / "docs" / "deployment.md")
    section = deployment[
        deployment.index("## Governed per-release App source") : deployment.index(
            "## Emergency recovery: a slim per-release App source folder"
        )
    ]
    for required in (
        "`<bundle root>/app-src/<release id>`",
        "`<commit[:12]>-<digest[:12]>`",
        "app_runtime_closure\n   check --ref <sha>",
        "--require-brotli-check",
        "tools.databricks.app_release_source publish",
        "create-only",
        "Rollback does not depend on these folders.",
        "Transitional exposure.",
        "verify-deployment",
        "Not yet observed live",
    ):
        assert required in section, required
    # The documented retention and budget are the code's.
    assert app_release_prune.KEEP_PREVIOUS == 2 and "the two newest other release folders" in section
    assert f"At most {app_release_prune.MAX_DELETIONS} folders go per run." in section
    assert app_release_prune.TIME_BUDGET_SECONDS % 60 == 0
    assert f"once {app_release_prune.TIME_BUDGET_SECONDS // 60} minutes have passed" in section
    # A per-delete re-check stops the run after earlier deletes, so no whole-run claim.
    assert "stops the run there and deletes nothing more" in section
    assert "uncertainty skips the whole run" not in " ".join(section.split())
    assert f"at most\n   {app_release_source.MAX_FILES:,} files" in section
    # The emergency recipe no longer claims the governed path promotes the files root.
    recovery = deployment[deployment.index("## Emergency recovery") : deployment.index("## App Python dependencies")]
    assert "The governed `./scripts/deploy.sh -t dev` path is unchanged." not in recovery
    assert "Nothing in the repository deletes them." not in recovery
    assert "app_release_contract release-id" in recovery
    contract = deployment[deployment.index("## App source contract") : deployment.index("## Governed per-release App source")]
    assert "Enforcement is three-layer" in contract and "release mode" in contract
    bundle = _read(ROOT / "databricks.yml")
    assert "source_code_path: .\n" in bundle and "app-src/<release id>" in bundle


def test_deployment_doc_documents_the_optional_lender_co_branding() -> None:
    """Audit responsive-10 / 12.4 #9: the three .env.local controls, the
    never-commit rule and the fail-safe; the derived hash is not an operator
    control, so it is not pinned as one."""
    deployment = _read(ROOT / "docs" / "deployment.md")
    section = deployment[deployment.index("## Lender co-branding (optional)") : deployment.index("## Resources")]
    for required in (
        "MIP_LENDER_MARK_FILE",
        "MIP_DEFAULT_THEME",
        "MIP_DEFAULT_ACCENT",
        "Never commit the image",
        "backend/schemas/lender_branding.py",
        "a plain `npm run build` without the preflight exports ships the\nproduct defaults and no mark",
    ):
        assert required in section, required
    assert "never set by an operator" in section


def test_otlp_operator_docs_use_the_same_governed_target() -> None:
    for relative in (
        "docs/observability.md",
        "docs/deployment.md",
        "docs/modernization-todo.md",
    ):
        text = _read(ROOT / relative)
        assert "prod_otlp" not in text
    for relative in ("docs/observability.md", "docs/deployment.md"):
        text = _read(ROOT / relative)
        assert "MIP_OTEL_HEADERS_SECRET_SCOPE" in text
        assert "MIP_OTEL_HEADERS_SECRET_KEY" in text
        assert "scripts/deploy.sh -t client" in text and "scripts/deploy.sh -t prod" not in text
        assert "tools/databricks/otlp_deploy_payload.py" not in text
    observability = _read(ROOT / "docs" / "observability.md")
    assert "databricks secrets list-scopes" in observability
    assert "databricks secrets create-scope customer-observability" in observability


def test_mutable_operator_surfaces_route_to_signed_deploy_only() -> None:
    makefile = _read(ROOT / "Makefile")
    bundle_env = _read(ROOT / "tools" / "databricks" / "bundle_env.py")
    scaffold = _read(ROOT / "tools" / "verify_scaffold.py")
    m2m = _read(ROOT / "tools" / "databricks" / "provision_m2m_oauth.py")
    genie = _read(ROOT / "tools" / "databricks" / "provision_genie_space.py")

    for target in ("bundle-deploy", "bundle-deploy-dev"):
        block = makefile.split(f"{target}: render-sql", 1)[1].split("\n\n", 1)[0]
        assert "retired" in block
        assert "exit 2" in block
        assert "bundle_env.py deploy" not in block
    assert "unrestricted bundle deploy is forbidden" in bundle_env
    assert "precomputed bundle deploy plans are forbidden" in bundle_env
    assert 'match.group("kind") == "apps"' in bundle_env
    assert "databricks apps deploy" not in scaffold
    assert "databricks bundle deploy" not in m2m
    assert "make bundle-deploy-dev" not in genie
    assert "./scripts/deploy.sh -t client" in scaffold
    assert "./scripts/deploy.sh -t client" in m2m
    assert "./scripts/deploy.sh -t client" in genie


def test_production_workflow_is_honestly_non_deploying() -> None:
    docs = _read(ROOT / ".github" / "workflows" / "README.md")
    workflow = _read(ROOT / ".github" / "workflows" / "deploy-prod.yml")

    assert "Non-deploying scaffold gate only" in docs
    assert "does not deploy or authenticate" in docs
    assert "name: prod-readiness-gate" in workflow
    assert "Non-deploying production scaffold gate" in workflow
    assert "scripts/deploy.sh" not in workflow
    assert "DATABRICKS_" not in workflow


def test_external_lakebase_migration_requires_explicit_reviewed_identity() -> None:
    text = _read(ROOT / "docs" / "security" / "GRANTS.md")
    command = text.split("Run the same idempotent migration", 1)[1].split("```", 2)[1]

    for required in (
        'export MIP_LENDER_NAME="<exact-reviewed-legal-lender-name>"',
        'export MIP_LENDER_NMLS_ID="<exact-reviewed-nmls-id>"',
        'export MIP_TENANT_ID="<reviewed-tenant-slug>"',
        '--lender-name "$MIP_LENDER_NAME"',
        '--lender-nmls-id "$MIP_LENDER_NMLS_ID"',
        '--tenant-id "$MIP_TENANT_ID"',
    ):
        assert required in command


def test_current_operator_docs_use_canonical_api_v1_paths() -> None:
    violations: list[str] = []
    for path in CURRENT_OPERATOR_DOCS:
        text = _read(path)
        for index, line in enumerate(text.splitlines(), start=1):
            if UNVERSIONED_API_RE.search(line):
                violations.append(f"{path.relative_to(ROOT)}:{index}: {line.strip()}")

    assert violations == []


def test_load_baseline_has_one_canonical_operator_doc() -> None:
    validation = _read(ROOT / "docs" / "validation" / "load-baseline.md")
    canonical = _read(ROOT / "docs" / "load-baseline.md")

    assert "The canonical load-baseline operator document is" in validation
    assert "| `GET /api/v1/health`" in canonical
    assert "| `GET /api/v1/health`" not in validation


def test_live_smoke_script_uses_canonical_api_v1_by_default() -> None:
    source = _read(ROOT / "scripts" / "smoke_live.sh")
    deploy_source = deploy_entrypoint_text()

    assert 'API_PREFIX="${MIP_API_PREFIX:-/api/v1}"' in source
    assert "$API_PREFIX/health" in source
    assert 'EXPECT_GIT_SHA="${MIP_EXPECT_GIT_SHA:-}"' in source
    assert 'DEPLOYED_GIT_SHA=$(echo "$HEALTH" | jq -r' in source
    assert 'export MIP_EXPECT_GIT_SHA="$APP_GIT_SHA"' in deploy_source
    for deprecated in (
        '"/api/health"',
        '"/api/leads',
        '"/api/borrowers',
        '"/api/outreach',
        '"/api/genie',
        '"/api/admin',
    ):
        assert deprecated not in source


def test_backend_modules_have_module_docstrings() -> None:
    missing: list[str] = []
    for path in sorted((ROOT / "backend").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        module = ast.parse(_read(path), filename=str(path))
        if ast.get_docstring(module) is None:
            missing.append(str(path.relative_to(ROOT)))

    assert missing == []


def test_current_operator_docs_have_no_broken_local_file_links() -> None:
    broken: list[str] = []
    for path in CURRENT_OPERATOR_DOCS:
        for match in MARKDOWN_LINK_RE.finditer(_read(path)):
            target = match.group(1).strip()
            if (
                not target
                or target.startswith(("#", "http://", "https://", "mailto:"))
                or target.startswith("computer://")
            ):
                continue
            local_target = unquote(target.split("#", 1)[0])
            if not local_target:
                continue
            if not (path.parent / local_target).resolve().exists():
                broken.append(f"{path.relative_to(ROOT)} -> {target}")

    assert broken == []
