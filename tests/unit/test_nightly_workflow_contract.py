"""Contracts for the live-UC validation workflow.

Live validation is a release-readiness gate over Databricks assets, not a daily
meter burn. It must be manual-only, and when it runs it must refresh the
governed snapshot before asserting raw-share/gold parity; otherwise weekly
upstream FRED changes can make gold look wrong until a human manually reruns
the scoring job.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
NIGHTLY = REPO / ".github" / "workflows" / "nightly.yml"
REAL_DATA_SPEC = REPO / "frontend" / "tests" / "e2e" / "real_data.spec.ts"
LIVE_HARDENING_SPEC = REPO / "frontend" / "tests" / "e2e" / "live_hardening_regressions.spec.ts"
CONSOLE_LAYOUT_SPEC = REPO / "frontend" / "tests" / "e2e" / "console-layout.spec.ts"


def test_live_validation_is_manual_only() -> None:
    text = NIGHTLY.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert "\n  schedule:" not in text
    assert "cron:" not in text


def test_live_validation_refreshes_live_snapshot_before_live_parity() -> None:
    text = NIGHTLY.read_text(encoding="utf-8")

    validate_pos = text.index("databricks bundle validate -t dev --profile DEFAULT")
    fred_pos = text.index("databricks bundle run mip_fred_rates_ingest -t dev --profile DEFAULT")
    silver_pos = text.index("databricks bundle run mip_refresh_silver -t dev --profile DEFAULT")
    gold_pos = text.index("databricks bundle run mip_refresh_scores -t dev --profile DEFAULT")
    parity_pos = text.index("pytest -q tests/integration/test_sql_python_parity.py")
    segment_pos = text.index("pytest tests/integration/test_segment_count_parity.py -q --tb=short")
    intersection_pos = text.index(
        "pytest tests/integration/test_segment_intersection_parity.py -q --tb=short"
    )
    source_pos = text.index("pytest tests/integration/test_source_readiness_live.py -q --tb=short")

    assert (
        validate_pos
        < fred_pos
        < silver_pos
        < gold_pos
        < parity_pos
        < segment_pos
        < intersection_pos
        < source_pos
    )


def test_live_validation_proves_source_bound_app_before_expensive_mutations() -> None:
    text = NIGHTLY.read_text(encoding="utf-8")

    resolve_pos = text.index("- name: Resolve source-bound Gateway runtime contract")
    app_proof_pos = text.index(
        "- name: Fail fast unless the exact app commit and Gateway binding are deployed"
    )
    refresh_pos = text.index("- name: Refresh live FRED market rates before validation")
    grant_pos = text.index("- name: Reconcile delayed AI Gateway inference-table grants")
    exact_proof_pos = text.index("- name: Refresh and verify AI Gateway exact proof ledger")

    assert resolve_pos < app_proof_pos < refresh_pos < grant_pos < exact_proof_pos
    app_proof_block = text[app_proof_pos:refresh_pos]
    assert "tools/verify_deployed_app_contract.py" in app_proof_block
    assert '--git-sha "$GITHUB_SHA"' in app_proof_block
    assert "MIP_EXPECTED_AGENT_GATEWAY_BINDING_SHA256" in app_proof_block


def test_live_validation_ignores_historical_gateway_resource_secrets() -> None:
    text = NIGHTLY.read_text(encoding="utf-8")

    assert "secrets.MIP_AI_GATEWAY_ENDPOINT" not in text
    assert "secrets.MIP_AI_GATEWAY_INFERENCE_TABLE" not in text
    assert text.count("tools/databricks/export_gateway_runtime_contract.py") == 2


def test_live_validation_gateway_proof_uses_only_verifier_derived_auth() -> None:
    text = NIGHTLY.read_text(encoding="utf-8")
    start = text.index("- name: Refresh and verify AI Gateway exact proof ledger")
    end = text.index("\n      - name:", start + 1)
    block = text[start:end]

    assert block.count("--require-verifier-derived-auth") == 2
    assert block.count('--warehouse-id "$DATABRICKS_WAREHOUSE_ID"') == 2
    assert "DATABRICKS_AUTH_TYPE: oauth-m2m" in block
    assert "DATABRICKS_CLIENT_ID: ${{ secrets.DATABRICKS_VERIFIER_CLIENT_ID }}" in block
    assert "DATABRICKS_CLIENT_SECRET: ${{ secrets.DATABRICKS_VERIFIER_CLIENT_SECRET }}" in block


def test_live_browser_rechecks_exact_contract_before_live_mutations() -> None:
    text = NIGHTLY.read_text(encoding="utf-8")
    job_pos = text.index("\n  playwright-e2e-live:")
    next_job_pos = text.index("\n  kill-drill-simulated:", job_pos + 1)
    job = text[job_pos:next_job_pos]

    resolve_pos = job.index("- name: Resolve source-bound Gateway runtime contract")
    mint_pos = job.index("- name: Mint per-run Playwright Bearer tokens")
    recheck_pos = job.index(
        "- name: Recheck deployed commit and Gateway binding with the browser identity"
    )
    mutations_pos = job.index("- name: Low-volume deployed workflow contracts")

    assert resolve_pos < mint_pos < recheck_pos < mutations_pos
    recheck_block = job[recheck_pos:mutations_pos]
    assert "tools/verify_deployed_app_contract.py" in recheck_block
    assert "--token-env MIP_NON_ADMIN_BEARER_TOKEN" in recheck_block
    assert '--git-sha "$GITHUB_SHA"' in recheck_block


def test_live_validation_refresh_steps_use_real_dev_bundle_profile() -> None:
    text = NIGHTLY.read_text(encoding="utf-8")

    for step_name in (
        "Refresh live FRED market rates before validation",
        "Refresh live Cotality silver features before validation",
        "Refresh live gold scoring snapshot before validation",
    ):
        step_pos = text.index(f"- name: {step_name}")
        next_step_pos = text.find("\n      - name:", step_pos + 1)
        block = text[step_pos:] if next_step_pos == -1 else text[step_pos:next_step_pos]

        assert "DATABRICKS_AUTH_TYPE: pat" in block
        assert "BUNDLE_VAR_sql_warehouse_id: ${{ secrets.DATABRICKS_WAREHOUSE_ID }}" in block
        assert "BUNDLE_VAR_genie_space_id: ${{ secrets.GENIE_SPACE_ID }}" in block
        assert "-t dev --profile DEFAULT" in block


def test_live_validation_renders_dev_demo_feeds_for_bundle_validation() -> None:
    text = NIGHTLY.read_text(encoding="utf-8")

    prepare_pos = text.index("- name: Prepare bundle sync inputs")
    export_pos = text.index("- name: Export live Databricks test env")
    block = text[prepare_pos:export_pos]

    assert "MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS=1" in block
    assert 'python tools/render_sql.py --catalog "${MIP_DEFAULT_CATALOG:-mip}"' in block


def test_live_validation_mints_admin_token_for_every_admin_proof() -> None:
    text = NIGHTLY.read_text(encoding="utf-8")

    assert "skip_admin_degraded_proof:" not in text
    assert "secrets.MIP_ADMIN_BEARER_TOKEN" not in text
    assert "DATABRICKS_ADMIN_CLIENT_ID: ${{ secrets.DATABRICKS_ADMIN_CLIENT_ID }}" in text
    assert "DATABRICKS_ADMIN_CLIENT_SECRET: ${{ secrets.DATABRICKS_ADMIN_CLIENT_SECRET }}" in text
    assert "DATABRICKS_OPERATOR2_CLIENT_ID: ${{ secrets.DATABRICKS_OPERATOR2_CLIENT_ID }}" in text
    assert (
        "DATABRICKS_OPERATOR2_CLIENT_SECRET: "
        "${{ secrets.DATABRICKS_OPERATOR2_CLIENT_SECRET }}"
    ) in text
    assert "--github-env MIP_OPERATOR2_BEARER_TOKEN" in text
    assert "--github-env MIP_ADMIN_BEARER_TOKEN" in text
    assert "DATABRICKS_VERIFIER_CLIENT_ID: ${{ secrets.DATABRICKS_VERIFIER_CLIENT_ID }}" in text
    assert (
        "Operator A, operator B, admin, and verifier M2M client IDs must be distinct"
        in text
    )
    assert "campaign-audit" in text
    assert "Growth Agent audit" in text
    assert "exit 1" in text


def test_segment_intersection_parity_inherits_fail_fast_live_credentials() -> None:
    text = NIGHTLY.read_text(encoding="utf-8")

    export_pos = text.index("- name: Export live Databricks test env")
    next_step_pos = text.index("\n      - name:", export_pos + 1)
    export_block = text[export_pos:next_step_pos]
    intersection_pos = text.index(
        "pytest tests/integration/test_segment_intersection_parity.py -q --tb=short"
    )

    for secret in (
        "DATABRICKS_HOST",
        "DATABRICKS_TOKEN",
        "DATABRICKS_WAREHOUSE_ID",
        "GENIE_SPACE_ID",
    ):
        assert f"{secret}: ${{{{ secrets.{secret} }}}}" in export_block
        assert secret in export_block
    assert "Missing required secrets" in export_block
    assert "exit 1" in export_block
    assert export_pos < intersection_pos


def test_live_playwright_executes_live_hardening_and_excludes_mocked_console() -> None:
    text = NIGHTLY.read_text(encoding="utf-8")
    console_text = CONSOLE_LAYOUT_SPEC.read_text(encoding="utf-8")

    job_pos = text.index("\n  playwright-e2e-live:")
    next_job_pos = text.index("\n  kill-drill-simulated:", job_pos + 1)
    job_block = text[job_pos:next_job_pos]
    run_pos = job_block.index("- name: Run real-UC Playwright specs")
    next_step_pos = job_block.index("\n      - name:", run_pos + 1)
    run_block = job_block[run_pos:next_step_pos]
    run_commands = "\n".join(
        line for line in run_block.splitlines() if not line.lstrip().startswith("#")
    )

    assert "E2E_LIVE: '1'" in job_block
    assert "tests/e2e/live_hardening_regressions.spec.ts" in run_block
    assert "tests/e2e/layout-stability.spec.ts" in run_block
    assert "tests/e2e/console-layout.spec.ts" not in run_block
    assert "--list" not in run_commands
    assert "E2E_LAYOUT_MOCK" not in run_block
    assert "page.route('**/api/**'" in console_text


def test_live_playwright_credentials_fail_before_browser_proofs() -> None:
    text = NIGHTLY.read_text(encoding="utf-8")

    resolve_pos = text.index("- name: Resolve deployed app URL")
    bearer_pos = text.index("- name: Mint per-run Playwright Bearer tokens")
    run_pos = text.index("- name: Run real-UC Playwright specs")
    resolve_block = text[resolve_pos:bearer_pos]
    bearer_block = text[bearer_pos:run_pos]

    assert "Missing DATABRICKS_HOST or DATABRICKS_TOKEN" in resolve_block
    assert "exit 1" in resolve_block
    assert "DATABRICKS_ADMIN_CLIENT_ID DATABRICKS_ADMIN_CLIENT_SECRET" in bearer_block
    assert "DATABRICKS_OPERATOR2_CLIENT_ID DATABRICKS_OPERATOR2_CLIENT_SECRET" in bearer_block
    assert "DATABRICKS_VERIFIER_CLIENT_ID" in bearer_block
    for left, right in (
        ("DATABRICKS_CLIENT_ID", "DATABRICKS_OPERATOR2_CLIENT_ID"),
        ("DATABRICKS_CLIENT_ID", "DATABRICKS_ADMIN_CLIENT_ID"),
        ("DATABRICKS_CLIENT_ID", "DATABRICKS_VERIFIER_CLIENT_ID"),
        ("DATABRICKS_OPERATOR2_CLIENT_ID", "DATABRICKS_ADMIN_CLIENT_ID"),
        ("DATABRICKS_OPERATOR2_CLIENT_ID", "DATABRICKS_VERIFIER_CLIENT_ID"),
        ("DATABRICKS_ADMIN_CLIENT_ID", "DATABRICKS_VERIFIER_CLIENT_ID"),
    ):
        assert f'[ "${left}" = "${right}" ]' in bearer_block
    assert "Missing required M2M OAuth secret(s)" in bearer_block
    assert "--github-env MIP_BEARER_TOKEN" in bearer_block
    assert "--github-env MIP_OPERATOR2_BEARER_TOKEN" in bearer_block
    assert "--github-env MIP_ADMIN_BEARER_TOKEN" in bearer_block
    assert "secrets.MIP_ADMIN_BEARER_TOKEN" not in bearer_block
    assert "exit 1" in bearer_block
    assert "continue-on-error" not in resolve_block
    assert "continue-on-error" not in bearer_block
    assert resolve_pos < bearer_pos < run_pos


def test_live_gate_runs_two_operator_recovery_contract() -> None:
    text = NIGHTLY.read_text(encoding="utf-8")

    assert "tests/integration/test_lakebase_concurrency_live.py" in text
    assert "tests/integration/test_two_operator_recovery_live.py" in text
    assert "tests/integration/test_lifecycle_delta_replay_live.py" in text
    assert "tests/integration/test_campaign_treatment_at_cap_live.py" in text
    assert '"operator_b": os.environ["MIP_OPERATOR2_BEARER_TOKEN"]' in text


def test_lifecycle_delta_replay_is_in_explicit_low_volume_mutation_gate() -> None:
    text = NIGHTLY.read_text(encoding="utf-8")
    step_pos = text.index("- name: Low-volume deployed workflow contracts")
    next_step_pos = text.index("\n      - name:", step_pos + 1)
    block = text[step_pos:next_step_pos]

    assert "MIP_LIVE_MUTATION_OK: '1'" in block
    assert "DATABRICKS_HOST: ${{ secrets.DATABRICKS_HOST }}" in block
    assert "DATABRICKS_TOKEN: ${{ secrets.DATABRICKS_TOKEN }}" in block
    assert "DATABRICKS_WAREHOUSE_ID: ${{ secrets.DATABRICKS_WAREHOUSE_ID }}" in block
    assert "tests/integration/test_lifecycle_delta_replay_live.py" in block
    assert "tests/integration/test_campaign_treatment_at_cap_live.py" in block
    assert "MIP_LIVE_SCRATCH_SUFFIX: gha_${{ github.run_id }}" in block
    assert "tools.databricks.cleanup_campaign_treatment_scratch" in block
    assert "--stale-older-than-hours 2" in block

    cleanup_pos = text.index("- name: Always clean deterministic treatment scratch table")
    cleanup_next = text.index("\n      - name:", cleanup_pos + 1)
    cleanup_block = text[cleanup_pos:cleanup_next]
    assert "if: always()" in cleanup_block
    assert "tools.databricks.cleanup_campaign_treatment_scratch" in cleanup_block
    assert "MIP_LIVE_SCRATCH_SUFFIX: gha_${{ github.run_id }}" in cleanup_block


def test_nightly_agent_eval_passes_distinct_normal_and_admin_bearers() -> None:
    text = NIGHTLY.read_text(encoding="utf-8")

    remint_pos = text.index("- name: Remint per-run Bearers immediately before Agent Evaluation")
    eval_pos = text.index("- name: Growth Agent evaluation with admin identity proof")
    next_step_pos = text.index("\n      - name:", eval_pos + 1)
    remint_block = text[remint_pos:eval_pos]
    eval_block = text[eval_pos:next_step_pos]

    assert "--github-env MIP_BEARER_TOKEN" in remint_block
    assert "--github-env MIP_ADMIN_BEARER_TOKEN" in remint_block
    assert "DATABRICKS_ADMIN_CLIENT_ID" in remint_block
    assert "Per-run admin M2M bearer was not minted" in eval_block
    assert "exit 1" in eval_block
    assert "python tools/databricks/run_agent_eval.py" in eval_block
    assert "MIP_ADMIN_BEARER_TOKEN" in eval_block
    assert '--token "$MIP_BEARER_TOKEN"' not in eval_block
    assert '--admin-token "$MIP_ADMIN_BEARER_TOKEN"' not in eval_block
    assert '--admin-token "$MIP_BEARER_TOKEN"' not in eval_block
    assert remint_pos < eval_pos


def test_ai_gateway_proof_uses_dedicated_verifier_m2m_identity() -> None:
    text = NIGHTLY.read_text(encoding="utf-8")
    proof_pos = text.index("- name: Refresh and verify AI Gateway exact proof ledger")
    next_step_pos = text.index("\n      - name:", proof_pos + 1)
    proof_block = text[proof_pos:next_step_pos]

    assert "DATABRICKS_AUTH_TYPE: oauth-m2m" in proof_block
    assert "unset DATABRICKS_TOKEN" in proof_block
    assert "secrets.DATABRICKS_VERIFIER_CLIENT_ID" in proof_block
    assert "secrets.DATABRICKS_VERIFIER_CLIENT_SECRET" in proof_block
    assert "secrets.DATABRICKS_ADMIN_CLIENT_ID" not in proof_block
    assert "dedicated AI Gateway verifier" in proof_block


def test_verifier_boundary_uses_its_own_identity_and_precedes_exact_proof() -> None:
    text = NIGHTLY.read_text(encoding="utf-8")
    boundary_pos = text.index("- name: Prove the verifier's effective authorization boundary")
    proof_pos = text.index("- name: Refresh and verify AI Gateway exact proof ledger")
    block = text[boundary_pos:proof_pos]

    assert boundary_pos < proof_pos
    assert "tools/databricks/verify_verifier_identity_boundary.py" in block
    assert "DATABRICKS_AUTH_TYPE: oauth-m2m" in block
    assert "secrets.DATABRICKS_VERIFIER_CLIENT_ID" in block
    assert "secrets.DATABRICKS_VERIFIER_CLIENT_SECRET" in block
    assert "secrets.DATABRICKS_ACCOUNT_ID" in block
    assert "unset DATABRICKS_TOKEN" in block
    assert "--protected-service-principal-id" in block
    assert "--forbidden-relation" not in block
    assert "--obsolete-endpoint" not in block
    assert "Missing required verifier-boundary input" in block


def test_native_genie_browser_gate_requires_live_governed_turn_and_visible_trust() -> None:
    text = REAL_DATA_SPEC.read_text(encoding="utf-8")

    native_pos = text.index(
        "ask-genie: native Conversation API turn renders governed proof and feedback"
    )
    next_test_pos = text.index("\n  test(", native_pos + 1)
    native_block = text[native_pos:next_test_pos]
    helper_pos = text.index("async function expectLiveGenieUi")
    helper_end = text.index("\n}\n\ntype MapDrillTarget", helper_pos) + 2
    helper_block = text[helper_pos:helper_end]
    turn_helper_block = text[text.index("function expectLiveGenieTurn") : helper_pos]

    assert "NATIVE_GENIE_CONVERSATION_QUESTION" in native_block
    assert "expect(payload.source" in native_block
    assert ").toBe('genie')" in native_block
    assert "expectLiveGenieTurn(payload" in native_block
    assert "conversation_id" in turn_helper_block
    assert "genie_status" in turn_helper_block
    assert "toBe('COMPLETED')" in turn_helper_block
    assert "genie-feedback-up" in helper_block
    assert "genie-feedback-down" in helper_block
    assert "Answer source: Databricks Genie Conversation API" in helper_block
    assert "Public Preview reasoning returned by the API must render" in helper_block
    assert "completed native turn must expose public API reasoning summaries" in turn_helper_block
    assert "completed native turn must expose follow-up suggestions" in turn_helper_block
    assert "proof must preserve the same public reasoning summaries" in turn_helper_block
    assert "API follow-up suggestions must render as actions" in helper_block
    assert "test.skip(" not in native_block


def test_deterministic_genie_has_no_native_reasoning_or_follow_ups() -> None:
    text = REAL_DATA_SPEC.read_text(encoding="utf-8")

    canonical_pos = text.index("genie FAB returns a non-empty answer and source chip opens lineage")
    next_test_pos = text.index("\n  test(", canonical_pos + 1)
    canonical_block = text[canonical_pos:next_test_pos]

    assert ").toBe('trusted_sql')" in canonical_block
    assert (
        "deterministic trusted_sql must not expose native Genie reasoning summaries"
        in canonical_block
    )
    assert (
        "deterministic trusted_sql proof must not fabricate native reasoning summaries"
        in canonical_block
    )
    assert (
        "deterministic trusted_sql must not claim Genie-authored follow-up suggestions"
        in canonical_block
    )
    assert "trusted_sql UI must not render a native reasoning disclosure" in canonical_block
    assert "test.skip(" not in canonical_block


def test_live_browser_specs_pin_campaign_role_segment_and_catalog_proofs() -> None:
    real_data = REAL_DATA_SPEC.read_text(encoding="utf-8")
    hardening = LIVE_HARDENING_SPEC.read_text(encoding="utf-8")

    assert "/api/v1/admin/capabilities?live=1" in real_data
    assert (
        "a claimable Supervisor capability must produce the AI campaign recommendation" in real_data
    )
    assert "recommendation.performance_status" in real_data
    assert "campaign response must carry governed evidence" in real_data
    assert "clicking a lineage asset must open its exact Catalog Explorer destination" in real_data

    role_pos = hardening.index(
        "admin and non-admin identities enforce navigation and activity boundaries"
    )
    next_test_pos = hardening.index("\ntest(", role_pos + 1)
    role_block = hardening[role_pos:next_test_pos]
    assert "/api/session" in role_block
    assert "/api/audit/my-events?limit=8" in role_block
    assert "/api/audit/events?limit=1" in role_block
    assert "non-admin global activity lookup must fail closed" in role_block
    assert "admin global activity lookup" in role_block
    assert "toHaveURL(/\\/admin-config$/)" in role_block
    assert "test.skip(" not in role_block

    segment_pos = hardening.index(
        "segment any/all API counts are de-duplicated and intersection-safe"
    )
    next_test_pos = hardening.index("\ntest(", segment_pos + 1)
    segment_block = hardening[segment_pos:next_test_pos]
    assert "itm + equity - allMode" in segment_block
    assert "new Set(borrowerIds).size" in segment_block
    assert "live proof requires a nonempty ITM/equity intersection" in segment_block
