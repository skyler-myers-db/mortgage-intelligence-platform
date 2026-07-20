from __future__ import annotations

import pytest

from tools.databricks.app_resource_bindings import (
    _assert_exact_transition,
    build_resource_binding_payload,
)


def _summary() -> dict[str, object]:
    return {
        "resources": {
            "apps": {
                "mip_app": {
                    "name": "mip-app-staging",
                    "description": "Mortgage Intelligence Platform",
                    "source_code_path": "/Workspace/uploaded/source",
                    "resources": [
                        {
                            "name": "sql_warehouse",
                            "sql_warehouse": {
                                "id": "${resources.sql_warehouses.mip_serverless_sql.id}",
                                "permission": "CAN_USE",
                            },
                        },
                        {
                            "name": "database",
                            "database": {
                                "instance_name": "${resources.database_instances.mip_app_state.name}",
                                "database_name": "mip_app_state",
                                "permission": "CAN_CONNECT_AND_CREATE",
                            },
                        },
                        {
                            "name": "migration_job",
                            "job": {
                                "id": "${resources.jobs.mip_lakebase_migrate.id}",
                                "permission": "CAN_MANAGE_RUN",
                            },
                        },
                    ],
                }
            },
            "sql_warehouses": {"mip_serverless_sql": {"id": "warehouse-id"}},
            "database_instances": {"mip_app_state": {"name": "mip-state-staging"}},
            "jobs": {"mip_lakebase_migrate": {"id": 12345}},
        }
    }


def test_build_payload_resolves_resources_without_source_activation_fields() -> None:
    payload = build_resource_binding_payload(_summary(), app_name="mip-app-staging")

    assert payload == {
        "name": "mip-app-staging",
        "description": "Mortgage Intelligence Platform",
        "resources": [
            {
                "name": "sql_warehouse",
                "sql_warehouse": {"id": "warehouse-id", "permission": "CAN_USE"},
            },
            {
                "name": "database",
                "database": {
                    "instance_name": "mip-state-staging",
                    "database_name": "mip_app_state",
                    "permission": "CAN_CONNECT_AND_CREATE",
                },
            },
            {
                "name": "migration_job",
                "job": {"id": 12345, "permission": "CAN_MANAGE_RUN"},
            },
        ],
    }
    assert not ({"source_code_path", "env_vars", "mode"} & payload.keys())


def test_build_payload_adds_otlp_secret_to_canonical_resources() -> None:
    payload = build_resource_binding_payload(
        _summary(),
        app_name="mip-app-staging",
        otel_header_secret_scope="customer-observability",
        otel_header_secret_key="otlp-headers",
    )

    assert payload["resources"][-1] == {
        "name": "otel_headers",
        "description": "Customer-owned OTLP collector authorization headers.",
        "secret": {
            "scope": "customer-observability",
            "key": "otlp-headers",
            "permission": "READ",
        },
    }


@pytest.mark.parametrize(
    ("scope", "key"),
    (("customer", ""), ("", "headers"), ("bad scope", "headers")),
)
def test_build_payload_rejects_partial_or_invalid_otlp_secret(scope: str, key: str) -> None:
    with pytest.raises(ValueError, match="OTLP"):
        build_resource_binding_payload(
            _summary(),
            app_name="mip-app-staging",
            otel_header_secret_scope=scope,
            otel_header_secret_key=key,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value["resources"]["apps"]["mip_app"].update(name="other"), "name"),
        (
            lambda value: value["resources"]["jobs"]["mip_lakebase_migrate"].pop("id"),
            "concrete",
        ),
        (
            lambda value: value["resources"]["apps"]["mip_app"]["resources"].append(
                value["resources"]["apps"]["mip_app"]["resources"][0]
            ),
            "unique",
        ),
    ],
)
def test_build_payload_fails_closed_on_drift(mutation, message: str) -> None:  # type: ignore[no-untyped-def]
    summary = _summary()
    mutation(summary)
    with pytest.raises(ValueError, match=message):
        build_resource_binding_payload(summary, app_name="mip-app-staging")


def test_verify_first_install_requires_stopped_source_free_app() -> None:
    expected = build_resource_binding_payload(_summary(), app_name="mip-app-staging")
    after = {
        **expected,
        "compute_status": {"state": "STOPPED"},
        "active_deployment": None,
        "pending_deployment": None,
    }

    _assert_exact_transition(
        expected=expected,
        after=after,
        before=None,
        require_stopped_without_deployment=True,
    )

    after["active_deployment"] = {"deployment_id": "unreviewed"}
    with pytest.raises(ValueError, match="source deployment"):
        _assert_exact_transition(
            expected=expected,
            after=after,
            before=None,
            require_stopped_without_deployment=True,
        )


def test_verify_update_preserves_existing_deployment_and_compute() -> None:
    expected = build_resource_binding_payload(_summary(), app_name="mip-app-staging")
    before = {
        "active_deployment": {"deployment_id": "signed-blue"},
        "pending_deployment": None,
        "compute_status": {"state": "RUNNING"},
    }
    after = {
        "name": "mip-app-staging",
        "description": expected["description"],
        "resources": expected["resources"],
        **before,
    }
    _assert_exact_transition(
        expected=expected,
        after=after,
        before=before,
        require_stopped_without_deployment=False,
    )

    after["active_deployment"] = {"deployment_id": "candidate"}
    with pytest.raises(ValueError, match="active_deployment"):
        _assert_exact_transition(
            expected=expected,
            after=after,
            before=before,
            require_stopped_without_deployment=False,
        )
