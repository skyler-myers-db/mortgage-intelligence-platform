"""Each pre-stop probe calls the very assertion its post-stop or mutating step calls (M0-PR5).

A probe that re-implemented its step's check could pass what the step later
refuses, with the App already stopped. These identity tests pin that every
probe's ``ASSERT`` is the same function object the step uses, not a copy, and
that the step still calls it.
"""

from __future__ import annotations

import inspect

from backend.services.genie_client import GenieClient
from tools.databricks import (
    app_deployment_rollback,
    app_deployment_rollback_cli,
    app_rollback_bootstrap_gate,
    audit_global_m2m_access,
    bundle_plan_app_acl,
    converge_app_release_access,
    converge_campaign_treatment_access,
    prestop_genie_budget,
    prestop_owner_equivalent,
    prestop_owner_policy,
    prestop_probe_suite,
    prestop_rollback_record,
    prestop_serving_endpoints,
    provision_genie_space,
    serving_endpoint_acl,
    uc_owner_policy,
)


def test_the_rollback_probe_runs_step_0a_record_verifier_and_bootstrap_gate() -> None:
    assert (
        prestop_rollback_record.ASSERT_SIGNED
        is app_deployment_rollback.verified_signed_last_good_contract
    )
    assert (
        prestop_rollback_record.ASSERT_ABSENT
        is app_rollback_bootstrap_gate.assert_rollback_record_absent
    )
    assert prestop_rollback_record.ACTIVE_DEPLOYMENT is app_deployment_rollback._active_deployment_id
    # The post-stop ensure path and the bootstrap gate still call them.
    assert "verified_signed_last_good_contract(" in inspect.getsource(app_deployment_rollback_cli)
    assert "assert_rollback_record_absent(" in inspect.getsource(app_rollback_bootstrap_gate.main)


def test_the_owner_policy_probe_runs_the_converger_policy_on_its_objects() -> None:
    assert prestop_owner_policy.ASSERT is uc_owner_policy.ApprovedOwnerPolicy.assert_objects
    assert prestop_owner_policy.OBJECTS is converge_campaign_treatment_access._object_presence
    source = inspect.getsource(converge_campaign_treatment_access)
    assert "owner_policy.assert_objects(objects)" in source
    assert "owner_policy.assert_objects((metastore,))" in source


def test_the_serving_probe_runs_the_cutover_app_audit() -> None:
    assert (
        prestop_serving_endpoints.ASSERT
        is serving_endpoint_acl.audit_global_serving_endpoint_access
        is audit_global_m2m_access.audit_global_serving_endpoint_access
    )
    assert (
        prestop_serving_endpoints.ASSERT_NONE
        is serving_endpoint_acl.audit_global_no_serving_endpoint_access
        is audit_global_m2m_access.audit_global_no_serving_endpoint_access
    )
    assert prestop_serving_endpoints.MODE is serving_endpoint_acl.inspect_exact_query_access_mode


def test_the_genie_budget_probe_asks_through_the_app_live_client() -> None:
    # The App's live path and the provisioner's live smoke both call this.
    assert prestop_genie_budget.ASSERT is GenieClient.ask
    assert ").ask(DEFAULT_SMOKE_QUESTION)" in inspect.getsource(provision_genie_space)
    assert prestop_genie_budget.QUESTION == provision_genie_space.DEFAULT_SMOKE_QUESTION


def test_without_a_record_the_governed_space_is_the_one_the_provisioner_adopts() -> None:
    assert prestop_probe_suite.GOVERNED_SPACE_BY_TITLE is provision_genie_space._find_space


def test_the_owner_equivalent_probe_reads_approvals_with_the_owner_policy_parser() -> None:
    # No post-stop step checks owner-equivalent grantees yet (attack G12), so the
    # probe owns its assertion; the approved-owner list is parsed exactly as the
    # owner policy parses it.
    assert (
        prestop_owner_equivalent.ASSERT
        is prestop_owner_equivalent.assert_owner_equivalent_grantees
    )
    assert (
        prestop_owner_equivalent.parse_approved_owner_principals
        is uc_owner_policy.parse_approved_owner_principals
    )


def test_the_app_acl_preview_runs_the_converger_reader_and_plan() -> None:
    assert bundle_plan_app_acl.INSPECT is converge_app_release_access._inspect_acl
    assert bundle_plan_app_acl.PLAN is converge_app_release_access.planned_release_acl
    assert "planned_release_acl(current" in inspect.getsource(
        converge_app_release_access.converge_app_release_access
    )
