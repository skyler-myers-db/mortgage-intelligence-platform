"""Static guardrails for public Module 0 docs claims.

These are intentionally narrow. Historical validation notes under
``docs/validation`` can preserve old language, but operator- and
buyer-facing docs must not claim mock runtime modes, unshipped
production packaging, or degraded Genie behavior that the product no
longer provides.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]

PUBLIC_DOCS = (
    "README.md",
    "docs/deployment.md",
    "docs/runbook.md",
    "docs/testing.md",
    "docs/se-onboarding.md",
    "docs/module0-talk-track.md",
    "docs/data-contract.md",
    "docs/security-and-compliance.md",
    "docs/partner-review-checklist.md",
    "docs/implementation-plan.md",
    "docs/module0-rehearsal-checklist.md",
)

INTERNAL_DOCS = (
    "docs/data-contract-module0.md",
    "docs/data-sources-gap-analysis.md",
    "docs/governance-real-data-review.md",
    "docs/module0-real-data-plan.md",
    "docs/security/GRANTS.md",
    "docs/slice13-accuracy-report.md",
    "docs/validation/borrower-e2e-audit.md",
    "docs/validation/segment-count-parity.md",
)

OTLP_DOCS = (
    "docs/deployment.md",
    "docs/observability.md",
    "docs/modernization-todo.md",
)

GENIE_DOCS = (
    "genie/instructions.md",
    "genie/mortgage_lead_intelligence_space.yml",
    "genie/sample_questions.md",
    "genie/regression_suite.md",
)

GENIE_EVAL_DOCS = GENIE_DOCS + ("tools/genie_eval_questions.yml",)


@pytest.mark.parametrize("relative_path", PUBLIC_DOCS)
def test_public_docs_do_not_resurrect_mip_mock_mode(relative_path: str) -> None:
    text = (REPO / relative_path).read_text(encoding="utf-8")

    assert "MIP_MOCK_MODE" not in text


@pytest.mark.parametrize(
    ("relative_path", "pattern", "reason"),
    [
        (
            "docs/implementation-plan.md",
            r"thresholds\s+editable\s+in\s+mock\s+mode",
            "Admin threshold copy must describe the live governed rules path, not mock mode.",
        ),
        (
            "docs/implementation-plan.md",
            r"Prod target:\s*same bundle,\s*larger warehouse,\s*production Genie space",
            "Do not claim a differentiated production warehouse/Genie target unless the bundle ships it.",
        ),
        (
            "docs/module0-talk-track.md",
            r"local answer catalog.*canonical answers",
            "The degraded Genie path must not claim local answer fallback in the running app.",
        ),
    ],
)
def test_public_docs_do_not_claim_unshipped_or_retired_behavior(
    relative_path: str,
    pattern: str,
    reason: str,
) -> None:
    text = (REPO / relative_path).read_text(encoding="utf-8")

    assert re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL) is None, reason


@pytest.mark.parametrize("relative_path", OTLP_DOCS)
def test_otlp_docs_keep_customer_retention_claims_gated(relative_path: str) -> None:
    text = (REPO / relative_path).read_text(encoding="utf-8")

    forbidden_patterns = (
        r"durable (?:customer )?(?:log )?retention (?:is|has been|was) "
        r"(?:enabled|proven|closed|complete|certified)",
        r"customer durable retention (?:is|has been|was) "
        r"(?:enabled|proven|closed|complete|certified)",
    )
    for pattern in forbidden_patterns:
        assert re.search(pattern, text, flags=re.IGNORECASE) is None

    assert re.search(r"customer-owned\s+collector", text, flags=re.IGNORECASE)
    assert re.search(r"retention/acl\s+(?:proof|evidence)", text, flags=re.IGNORECASE)
    assert re.search(
        r"collector\s+(?:query proof|query that finds)",
        text,
        flags=re.IGNORECASE,
    )


def test_otlp_docs_name_the_customer_retention_evidence_gate() -> None:
    text = (REPO / "docs" / "observability.md").read_text(encoding="utf-8")

    assert "tools/databricks/otlp_customer_retention_gate.py" in text
    assert "--min-retention-days" in text


@pytest.mark.parametrize("relative_path", OTLP_DOCS)
def test_otlp_docs_do_not_show_plaintext_header_values(relative_path: str) -> None:
    text = (REPO / relative_path).read_text(encoding="utf-8")

    forbidden_patterns = (
        r"MIP_OTEL_HEADERS\s*=\s*authorization",
        r"MIP_OTEL_HEADERS\s*=\s*dd-api-key",
        r"Bearer\s+<token>",
    )
    for pattern in forbidden_patterns:
        assert re.search(pattern, text, flags=re.IGNORECASE) is None


@pytest.mark.parametrize("relative_path", PUBLIC_DOCS)
def test_public_docs_do_not_overstate_cotality_geo_scope(relative_path: str) -> None:
    text = (REPO / relative_path).read_text(encoding="utf-8")

    forbidden = (
        "6-state footprint",
        "six-state footprint",
        "all six real states",
        "full six-state coverage",
        "six anchor counties",
        "anchor county",
        "Cotality evaluation share",
    )
    for phrase in forbidden:
        assert phrase.lower() not in text.lower()


@pytest.mark.parametrize("relative_path", GENIE_DOCS)
def test_genie_docs_do_not_overstate_cotality_geo_scope(relative_path: str) -> None:
    text = (REPO / relative_path).read_text(encoding="utf-8")

    forbidden = (
        "6-state footprint",
        "six-state footprint",
        "all six states",
        "all six real states",
        "full six-state coverage",
        "six anchor counties",
        "anchor county",
        "Cotality evaluation share",
    )
    for phrase in forbidden:
        assert phrase.lower() not in text.lower()


@pytest.mark.parametrize("relative_path", GENIE_DOCS)
def test_genie_docs_do_not_overclaim_evidence_append_only_or_gold_redaction(relative_path: str) -> None:
    text = (REPO / relative_path).read_text(encoding="utf-8").lower()

    forbidden = (
        "append-only ledger",
        "append-only event ledger",
        "backend/services/evidence.py",
        "masks them at the gold layer",
        "masked at the gold layer",
        "redacted view",
    )
    for phrase in forbidden:
        assert phrase not in text


@pytest.mark.parametrize("relative_path", GENIE_EVAL_DOCS)
def test_genie_docs_do_not_pin_retired_exact_answer_counts(relative_path: str) -> None:
    text = (REPO / relative_path).read_text(encoding="utf-8")

    forbidden = (
        "12,840",
        "3,471",
        "1,842",
        "$2.18",
        "47 outreach",
        "Cost per contact is trending",
    )
    for phrase in forbidden:
        assert phrase not in text


@pytest.mark.parametrize("relative_path", GENIE_DOCS)
def test_genie_docs_do_not_allow_unsupported_geo_zero_proof(relative_path: str) -> None:
    text = (REPO / relative_path).read_text(encoding="utf-8")

    forbidden = (
        "coverage-proven zero",
        "refusal or zero",
        "return zero/no result",
        "approved_at::date",
        "event_ts",
        "trigger_type",
        "ORDER BY lead_score",
        "GROUP BY zip_code",
        "WHERE segment=",
        "scored_at",
    )
    for phrase in forbidden:
        assert phrase not in text


def test_live_genie_regression_requires_refusal_without_sql_for_adversarial_prompts() -> None:
    text = (REPO / "tests" / "integration" / "test_genie_regression.py").read_text(
        encoding="utf-8"
    )

    assert "expect_zero_ok" not in text
    assert "coverage-proven zero" not in text
    assert "adversarial prompt emitted SQL" in text


def test_genie_zip_prompt_uses_canonical_zip_validity_predicate() -> None:
    text = (REPO / "genie" / "sample_questions.md").read_text(encoding="utf-8")

    assert "FROM mip.gold.borrower_360 WHERE in_the_money = true" in text
    assert "zip IS NOT NULL" in text
    assert "LENGTH(zip) = 5" in text


def test_genie_sample_sql_respects_rollup_grain() -> None:
    text = (REPO / "genie" / "sample_questions.md").read_text(encoding="utf-8")

    assert "segment_code = 'itm' AND state <> '_ALL' AND count > 0" in text
    assert "WHERE state = '_ALL' AND segment_code = '_ALL'" in text
    assert "WHERE state = '_ALL' ORDER BY approval_rate DESC" in text
    assert "SUM(approved_borrowers)" not in text


@pytest.mark.parametrize("relative_path", ("genie/sample_questions.md", "genie/regression_suite.md"))
def test_genie_docs_use_current_borrower_id_shape(relative_path: str) -> None:
    text = (REPO / relative_path).read_text(encoding="utf-8")

    assert "B-#####" not in text
    assert r"^B-\d{5}$" not in text
    assert "B-[0-9A-Z]{13}" in text


@pytest.mark.parametrize("relative_path", ("genie/sample_questions.md", "genie/regression_suite.md"))
def test_genie_docs_use_percent_approval_rate(relative_path: str) -> None:
    text = (REPO / relative_path).read_text(encoding="utf-8")

    assert "approval_rate in `[0, 1]`" not in text
    assert "approval_rate in `[0,1]`" not in text
    assert "approval_rate as a percent in `[0,100]`" in text or "approval_rate as a percent in `[0, 100]`" in text


def test_genie_numeric_eval_ranges_have_canonical_sql() -> None:
    data = yaml.safe_load((REPO / "tools" / "genie_eval_questions.yml").read_text(encoding="utf-8"))
    questions = data.get("questions") if isinstance(data, dict) else None
    assert isinstance(questions, list)

    for item in questions:
        if not isinstance(item, dict) or "expected_numeric_range" not in item:
            continue
        assert item.get("canonical_sql"), f"{item.get('id')} numeric range lacks canonical_sql"
        assert item.get("canonical_column"), f"{item.get('id')} numeric range lacks canonical_column"


def test_nightly_runs_standard_genie_fuzz_and_keeps_deep_fuzz_manual() -> None:
    text = (REPO / ".github" / "workflows" / "nightly.yml").read_text(encoding="utf-8")

    assert "genie-fuzz-standard:" in text
    assert "pytest -q tests/integration/test_genie_fuzz.py -m integration" in text
    assert "MIP_GENIE_FUZZ_EXAMPLES: '15'" in text
    assert "genie-fuzz-deep:" in text
    assert "run_deep_genie_fuzz:" in text
    assert (
        "if: ${{ github.event_name == 'workflow_dispatch' && "
        "inputs.run_deep_genie_fuzz == true }}"
    ) in text
    assert "pytest -m genie_fuzz_deep tests/integration/test_genie_fuzz.py -q" in text


@pytest.mark.parametrize(
    "relative_path",
    (
        "docs/architecture.md",
        "docs/data-contract.md",
        "docs/security-and-compliance.md",
        "docs/partner-review-checklist.md",
        "docs/enterprise-readiness-checklist.md",
    ),
)
def test_public_partner_docs_are_not_placeholders(relative_path: str) -> None:
    text = (REPO / relative_path).read_text(encoding="utf-8").strip()

    assert text != "# Placeholder"
    assert not text.lower().startswith("# placeholder")


def test_public_talk_track_does_not_pin_fixture_borrowers_or_urls() -> None:
    text = (REPO / "docs/module0-talk-track.md").read_text(encoding="utf-8")

    forbidden = (
        "B-48291",
        "https://mip-app-2543889327043640.aws.databricksapps.com",
        "5.16 million",
        "3.1 million",
        "never synthesized",
    )
    for phrase in forbidden:
        assert phrase not in text


def test_public_talk_track_pins_claim_boundaries() -> None:
    text = " ".join(
        (REPO / "docs/module0-talk-track.md").read_text(encoding="utf-8").split()
    )

    required = (
        "Do not claim live Salesforce, CRM/CDP, LOS/POS, or servicing writeback",
        "Do not claim the app auto-sends email or SMS",
        "Do not add standalone segment card counts together",
        "Do not claim arbitrary custom segment definitions",
        "Do not call primary-offer selection a trained MIP ML model",
        "Do not claim HITRUST or any certification",
        "Do not claim every click, filter change, or page view is audited",
        "Do not infer filed permit activity from HELOC intent",
    )
    for phrase in required:
        assert phrase in text


def test_archived_cotality_walkthrough_cannot_be_used_as_live_teleprompter() -> None:
    text = (REPO / "docs/demo/cotality-walkthrough.md").read_text(encoding="utf-8")

    assert "Archived rehearsal snapshot" in text
    assert "do not use as the live demo teleprompter" in text
    forbidden_operator_values = (
        "01f1532b4e1314e7964cb093feade193",
        "https://mip-app-2543889327043640.aws.databricksapps.com",
        "B-102FL7THC6Q3L",
        "B-1AT5CXZZ1NI2N",
        "5.16M",
        "5,156,184",
        "135,520",
        "4,351",
        "4,472,667",
        "67,858",
        "6,235",
        "top 500",
        "10.27%",
        "91% equity",
        "346 related",
        "+391 bps",
        "6 result rows",
    )
    for phrase in forbidden_operator_values:
        assert phrase not in text


@pytest.mark.parametrize("relative_path", INTERNAL_DOCS)
def test_internal_cotality_inventory_docs_are_not_public(relative_path: str) -> None:
    text = (REPO / relative_path).read_text(encoding="utf-8")

    assert "Internal implementation" in text
    assert "Not approved for public release" in text


@pytest.mark.parametrize("relative_path", PUBLIC_DOCS)
def test_public_docs_do_not_disclose_cotality_share_inventory(relative_path: str) -> None:
    text = (REPO / relative_path).read_text(encoding="utf-8").lower()

    forbidden = (
        "cotality_mortgage_data.corelogic",
        "datashare_us_west_2",
        "corelogic_226d2860",
        "entrada_eval_voluntary_lien_status_marketing_v2",
        "entrada_eval_property_domain_v3",
        "entrada_eval_mortgage_domain_v1",
        "entrada_eval_owner_transfer_domain_v1",
        "entrada_eval_mortgage_market_analytics_domain_v1",
    )
    for phrase in forbidden:
        assert phrase.lower() not in text


@pytest.mark.parametrize("relative_path", (*PUBLIC_DOCS, *GENIE_DOCS))
def test_public_and_genie_docs_do_not_link_internal_inventory_docs(relative_path: str) -> None:
    text = (REPO / relative_path).read_text(encoding="utf-8").lower()

    forbidden_links = (
        "data-contract-module0.md",
        "data-sources-gap-analysis.md",
        "governance-real-data-review.md",
        "security/grants.md",
    )
    for link in forbidden_links:
        assert link not in text


def test_validation_notes_are_explicitly_internal_artifacts() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "docs/validation/*.md", "docs/audits/*.md", "docs/audits/**/*.md"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    internal_docs = [REPO / line for line in tracked.stdout.splitlines() if line.endswith(".md")]
    assert internal_docs
    for path in internal_docs:
        text = path.read_text(encoding="utf-8")
        assert "Internal" in text
        lowered = " ".join(re.sub(r"\s*>\s*", " ", text.lower()).split())
        assert (
            "not approved for public release" in lowered
            or "not public release collateral" in lowered
        )


def test_current_release_docs_do_not_cite_stale_deployments() -> None:
    current_docs = (
        "docs/partner-review-checklist.md",
        "docs/module0-alignment-todo.md",
        "docs/audits/persona-walkthroughs/01-head-of-growth.md",
    )
    current_deployment_id = "01f14d00b90b15bba16e412e31a8edbd"
    deployment_id_pattern = re.compile(r"\b01f14[0-9a-f]{27}\b")
    for relative_path in current_docs:
        text = (REPO / relative_path).read_text(encoding="utf-8")
        cited_deployments = set(deployment_id_pattern.findall(text))
        assert cited_deployments <= {current_deployment_id}


def test_genie_yaml_off_topic_redirect_points_to_bucket_c() -> None:
    text = (REPO / "genie" / "mortgage_lead_intelligence_space.yml").read_text(
        encoding="utf-8"
    )

    assert "bucket-C redirect text" in text
    assert "bucket-B redirect text" not in text


def test_head_of_growth_audit_does_not_document_stale_cash_out_equity_route() -> None:
    text = (
        REPO / "docs/audits/persona-walkthroughs/01-head-of-growth.md"
    ).read_text(encoding="utf-8")

    assert "cash-out" in text.lower()
    assert "product=Cash-out" in text
    assert "segment=equity" not in text
