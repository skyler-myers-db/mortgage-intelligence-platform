"""State-machine tests for lease-fenced Gateway model archival convergence."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from tools.databricks import gateway_model_archival as archival
from tools.databricks.gateway_model_archival import GatewayModelArchiveScope

_MODEL = "mip.audit.mortgage_growth_supervisor_proxy_aaaaaaaaaaaa"
_TABLE = "mip.audit.mip_agent_gateway_growth_agent_aaaaaaaaaaaa_payload"
_ARCHIVE_OWNER = "governance@example.com"
_GOVERNANCE_GROUP = "mortgage-governance"
_ARCHIVE_EXPERIMENT = "/Users/governance@example.com/.mip-gateway-archive/mip-app/archive"


def _scope() -> GatewayModelArchiveScope:
    return GatewayModelArchiveScope(
        app_name="mip-app",
        lease_id="11111111-1111-4111-8111-111111111111",
        source_git_sha="a" * 40,
        runtime_application_id="runtime-application-id",
        app_application_id="app-application-id",
        proxy_application_id="proxy-application-id",
        verifier_application_id="verifier-application-id",
        archive_owner=_ARCHIVE_OWNER,
        governance_group=_GOVERNANCE_GROUP,
        catalog="mip",
        model_family="mip.audit.mortgage_growth_supervisor_proxy",
        experiment_base="mip-agent-runtime-gateway-proxy",
        inference_schema="audit",
        inference_table_prefix="mip_agent_gateway_growth_agent",
        rollback_scope="production",
        expected_lakebase_instance="mip-lakebase",
        warehouse_id="warehouse-id",
    )


def _scope_workspace() -> Any:
    return SimpleNamespace(
        config=SimpleNamespace(host="https://workspace.cloud.databricks.com/"),
        get_workspace_id=lambda: 123456789,
        metastores=SimpleNamespace(
            current=lambda: SimpleNamespace(metastore_id="metastore-id")
        ),
    )


@pytest.mark.parametrize("failure_state", ["unchanged", "third"])
def test_owner_lost_response_rejects_non_target_state(
    failure_state: str,
) -> None:
    state = SimpleNamespace(owner="runtime-application-id")

    def update() -> None:
        if failure_state == "third":
            state.owner = "unreviewed-principal"
        raise OSError("owner update response lost")

    with pytest.raises(RuntimeError, match="owner update is ambiguous"):
        archival._converge_owner(
            lambda: state,
            update,
            archive_owner=_ARCHIVE_OWNER,
            label=f"table {_TABLE}",
            assert_held=lambda: None,
        )


def test_owner_lost_response_accepts_only_exact_post_state() -> None:
    state = SimpleNamespace(owner="runtime-application-id")
    held_checks = 0

    def assert_held() -> None:
        nonlocal held_checks
        held_checks += 1

    def update() -> None:
        state.owner = _ARCHIVE_OWNER
        raise OSError("owner update committed before response loss")

    archival._converge_owner(
        lambda: state,
        update,
        archive_owner=_ARCHIVE_OWNER,
        label=f"table {_TABLE}",
        assert_held=assert_held,
    )

    assert state.owner == _ARCHIVE_OWNER
    assert held_checks == 1


def test_owner_update_rechecks_lease_immediately_before_mutation() -> None:
    update_called = False

    def update() -> None:
        nonlocal update_called
        update_called = True

    with pytest.raises(RuntimeError, match="lease lost"):
        archival._converge_owner(
            lambda: SimpleNamespace(owner="runtime-application-id"),
            update,
            archive_owner=_ARCHIVE_OWNER,
            label=_MODEL,
            assert_held=lambda: (_ for _ in ()).throw(RuntimeError("lease lost")),
        )

    assert update_called is False


def _initial_acl() -> tuple[dict[str, Any], ...]:
    return (
        {
            "service_principal_name": "runtime-application-id",
            "all_permissions": [
                {
                    "permission_level": "CAN_MANAGE",
                    "inherited": False,
                }
            ],
        },
    )


def _target_acl() -> tuple[dict[str, Any], ...]:
    return (
        {
            "group_name": _GOVERNANCE_GROUP,
            "all_permissions": [
                {
                    "permission_level": "CAN_MANAGE",
                    "inherited": False,
                }
            ],
        },
    )


class _ExperimentState:
    def __init__(
        self,
        *,
        rename_mode: str = "success",
        acl_mode: str = "success",
    ) -> None:
        self.name = "/Shared/original"
        self.acl = _initial_acl()
        self.rename_mode = rename_mode
        self.acl_mode = acl_mode
        self.events: list[str] = []

    def rename_experiment(self, _experiment_id: str, archive_name: str) -> None:
        self.events.append("rename")
        if self.rename_mode in {"success", "commit_then_raise"}:
            self.name = archive_name
        elif self.rename_mode == "third":
            self.name = "/Shared/unreviewed-third-state"
        if self.rename_mode != "success":
            raise OSError("rename response lost")

    def mkdirs(self, _path: str) -> None:
        self.events.append("mkdirs")

    def set_permissions(
        self,
        _experiment_id: str,
        *,
        access_control_list: list[Any],
    ) -> None:
        assert len(access_control_list) == 1
        self.events.append("set-acl")
        if self.acl_mode in {"success", "commit_then_raise"}:
            self.acl = _target_acl()
        elif self.acl_mode == "third":
            self.acl = (
                {
                    "user_name": "unreviewed@example.com",
                    "all_permissions": [
                        {
                            "permission_level": "CAN_MANAGE",
                            "inherited": False,
                        }
                    ],
                },
            )
        if self.acl_mode != "success":
            raise OSError("ACL response lost")


def _experiment_stage() -> dict[str, Any]:
    return {
        "experiment_id": "experiment-id",
        "experiment_archive_name": _ARCHIVE_EXPERIMENT,
        "governance_group": _GOVERNANCE_GROUP,
        "archive_owner": _ARCHIVE_OWNER,
        "experiment_acl": list(_initial_acl()),
    }


def _install_experiment_reads(
    monkeypatch: pytest.MonkeyPatch,
    state: _ExperimentState,
) -> None:
    monkeypatch.setattr(
        archival,
        "_assert_experiment_identity",
        lambda *_args, **_kwargs: {"name": state.name},
    )
    monkeypatch.setattr(
        archival,
        "_experiment_state",
        lambda *_args, **_kwargs: {"name": state.name},
    )
    monkeypatch.setattr(
        archival,
        "exact_experiment_acl",
        lambda *_args, **_kwargs: state.acl,
    )


@pytest.mark.parametrize(
    ("rename_mode", "acl_mode"),
    [
        ("commit_then_raise", "success"),
        ("success", "commit_then_raise"),
    ],
)
def test_experiment_lost_response_accepts_exact_post_state(
    monkeypatch: pytest.MonkeyPatch,
    rename_mode: str,
    acl_mode: str,
) -> None:
    state = _ExperimentState(rename_mode=rename_mode, acl_mode=acl_mode)
    workspace = SimpleNamespace(
        workspace=SimpleNamespace(mkdirs=state.mkdirs),
        experiments=SimpleNamespace(set_permissions=state.set_permissions),
    )
    _install_experiment_reads(monkeypatch, state)
    held_checks = 0

    def assert_held() -> None:
        nonlocal held_checks
        held_checks += 1

    exact_state, exact_acl = archival._converge_experiment(
        workspace,
        state,
        stage=_experiment_stage(),
        assert_held=assert_held,
    )

    assert exact_state == {"name": _ARCHIVE_EXPERIMENT}
    assert exact_acl == _target_acl()
    assert state.events == ["mkdirs", "rename", "set-acl"]
    assert held_checks == 3


@pytest.mark.parametrize(
    ("rename_mode", "acl_mode", "message"),
    [
        ("unchanged", "success", "rename is ambiguous"),
        ("third", "success", "rename is ambiguous"),
        ("success", "unchanged", "ACL update is ambiguous"),
        ("success", "third", "ACL update is ambiguous"),
    ],
)
def test_experiment_lost_response_rejects_non_target_state(
    monkeypatch: pytest.MonkeyPatch,
    rename_mode: str,
    acl_mode: str,
    message: str,
) -> None:
    state = _ExperimentState(rename_mode=rename_mode, acl_mode=acl_mode)
    workspace = SimpleNamespace(
        workspace=SimpleNamespace(mkdirs=state.mkdirs),
        experiments=SimpleNamespace(set_permissions=state.set_permissions),
    )
    _install_experiment_reads(monkeypatch, state)

    with pytest.raises(RuntimeError, match=message):
        archival._converge_experiment(
            workspace,
            state,
            stage=_experiment_stage(),
            assert_held=lambda: None,
        )


def test_experiment_parent_creation_rechecks_lease_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _ExperimentState()
    workspace = SimpleNamespace(
        workspace=SimpleNamespace(mkdirs=state.mkdirs),
        experiments=SimpleNamespace(set_permissions=state.set_permissions),
    )
    _install_experiment_reads(monkeypatch, state)

    with pytest.raises(RuntimeError, match="lease lost"):
        archival._converge_experiment(
            workspace,
            state,
            stage=_experiment_stage(),
            assert_held=lambda: (_ for _ in ()).throw(RuntimeError("lease lost")),
        )

    assert state.events == []


def test_fresh_lease_adopts_only_same_stable_pointer_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _scope_workspace()
    current = _scope()
    previous = replace(
        current,
        lease_id="22222222-2222-4222-8222-222222222222",
        source_git_sha="b" * 40,
    )
    pointer = {
        **archival._scope_record(workspace, previous),
        "phase": "staged",
        "model_name": _MODEL,
        "immutable_payload": {"digest": "c" * 64},
        "created_at": "2026-07-28T20:00:00+00:00",
        "attestation_alg": "test",
        "attestation_verify_key": "test",
        "attestation_signature": "test",
    }
    monkeypatch.setattr(archival, "sign_retirement_record", lambda record: dict(record))

    adopted = archival._adopt_stage(
        workspace,
        scope=current,
        pointer=pointer,
    )

    assert adopted["lease_id"] == current.lease_id
    assert adopted["source_git_sha"] == current.source_git_sha
    assert adopted["archive_owner"] == previous.archive_owner
    assert adopted["immutable_payload"] == pointer["immutable_payload"]
    assert "attestation_signature" not in adopted


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("archive_owner", "other-governance@example.com"),
        ("runtime_application_id", "other-runtime"),
        ("catalog", "other"),
        ("workspace_id", "other-workspace"),
        ("metastore_id", "other-metastore"),
    ],
)
def test_fresh_lease_rejects_pointer_scope_drift(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
) -> None:
    workspace = _scope_workspace()
    current = _scope()
    pointer = {
        **archival._scope_record(workspace, current),
        "phase": "staged",
        "model_name": _MODEL,
    }
    pointer[field] = value
    monkeypatch.setattr(archival, "sign_retirement_record", lambda record: dict(record))

    with pytest.raises(RuntimeError, match="escaped caller scope"):
        archival._adopt_stage(
            workspace,
            scope=current,
            pointer=pointer,
        )


def test_recovery_rejects_divergent_operation_records() -> None:
    with pytest.raises(RuntimeError, match="divergent completion records"):
        archival._unique_record(
            [{"phase": "completed", "digest": "a"}, {"phase": "completed", "digest": "b"}],
            label="completion",
        )


class _ArchiveHarness:
    def __init__(
        self,
        *,
        fail_check: int | None = None,
        table_owner: str = "runtime-application-id",
        model_owner: str = "runtime-application-id",
        experiment_name: str = "/Shared/original",
        acl: tuple[dict[str, Any], ...] | None = None,
    ) -> None:
        self.fail_check = fail_check
        self.checks = 0
        self.events: list[str] = []
        self.records: dict[str, dict[str, Any]] = {}
        self.table_owner = table_owner
        self.model_owner = model_owner
        self.experiment_name = experiment_name
        self.acl = acl or _initial_acl()
        self.stage = {
            "model_name": _MODEL,
            "inference_tables": [{"full_name": _TABLE}],
            "serving_inventory": [],
            "protected_allocation_contracts": [],
            **_experiment_stage(),
        }
        self.completion = {
            "phase": "completed",
            "model_name": _MODEL,
            "archive_owner": _ARCHIVE_OWNER,
        }

    def assert_held(self) -> None:
        self.checks += 1
        self.events.append(f"assert:{self.checks}")
        if self.checks == self.fail_check:
            raise RuntimeError("deployment lease lost")

    def mutation(self, name: str) -> None:
        expected_fence = (
            "assert:"
            if name.startswith("record-")
            else "protection-fence"
        )
        assert self.events[-1].startswith(expected_fence)
        self.events.append(name)

    def table_get(self, _name: str, **_kwargs: Any) -> Any:
        return SimpleNamespace(owner=self.table_owner)

    def table_update(self, _name: str, *, owner: str) -> None:
        self.mutation("table-owner")
        self.table_owner = owner

    def model_get(self, _name: str) -> Any:
        return SimpleNamespace(owner=self.model_owner)

    def model_update(self, _name: str, *, owner: str) -> None:
        self.mutation("model-owner")
        self.model_owner = owner

    def mkdirs(self, _path: str) -> None:
        self.mutation("experiment-mkdir")

    def rename_experiment(self, _experiment_id: str, name: str) -> None:
        self.mutation("experiment-rename")
        self.experiment_name = name

    def set_permissions(
        self,
        _experiment_id: str,
        *,
        access_control_list: list[Any],
    ) -> None:
        assert len(access_control_list) == 1
        self.mutation("experiment-acl")
        self.acl = _target_acl()

    def workspace(self) -> Any:
        return SimpleNamespace(
            tables=SimpleNamespace(
                get=self.table_get,
                update=self.table_update,
            ),
            registered_models=SimpleNamespace(
                get=self.model_get,
                update=self.model_update,
            ),
            workspace=SimpleNamespace(mkdirs=self.mkdirs),
            experiments=SimpleNamespace(set_permissions=self.set_permissions),
        )


def _record_label(path: str) -> str:
    if path.endswith("/in-progress.json"):
        return "pointer"
    if path.endswith("/stage.json"):
        return "stage"
    if path.endswith("/complete.json"):
        return "completion"
    if path.endswith("/archived.json"):
        return "head"
    raise AssertionError(f"unexpected test record path {path}")


def _install_archive_harness(
    monkeypatch: pytest.MonkeyPatch,
    harness: _ArchiveHarness,
    *,
    operation_completions: list[dict[str, Any]] | None = None,
    operation_stages: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    monkeypatch.setattr(
        archival,
        "held_assertion",
        lambda *_args, **_kwargs: harness.assert_held,
    )
    monkeypatch.setattr(
        archival,
        "load_retirement_record",
        lambda _workspace, path: harness.records.get(path),
    )

    def persist(
        _workspace: Any,
        path: str,
        record: dict[str, Any],
        *,
        assert_before_mutation: Any,
    ) -> None:
        label = _record_label(path)
        assert_before_mutation()
        harness.mutation(f"record-mkdir:{label}")
        assert_before_mutation()
        harness.mutation(f"record-upload:{label}")
        harness.records[path] = dict(record)

    monkeypatch.setattr(archival, "persist_retirement_record", persist)

    def records(
        _workspace: Any,
        *,
        leaf: str,
        **_kwargs: Any,
    ) -> list[dict[str, Any]]:
        if leaf == "complete.json":
            return list(operation_completions or [])
        if leaf == "stage.json":
            return list(operation_stages or [])
        raise AssertionError(f"unexpected operation leaf {leaf}")

    monkeypatch.setattr(archival, "_operation_records", records)
    monkeypatch.setattr(
        archival,
        "_fresh_stage",
        lambda *_args, **_kwargs: harness.stage,
    )
    def fence(
        *_args: Any,
        assert_held: Any,
        **_kwargs: Any,
    ) -> None:
        assert_held()
        harness.events.append("protection-fence")

    monkeypatch.setattr(archival, "_fence", fence)
    monkeypatch.setattr(
        archival,
        "_completion_record",
        lambda *_args, **_kwargs: harness.completion,
    )
    monkeypatch.setattr(
        archival,
        "_assert_experiment_identity",
        lambda *_args, **_kwargs: {"name": harness.experiment_name},
    )
    monkeypatch.setattr(
        archival,
        "_experiment_state",
        lambda *_args, **_kwargs: {"name": harness.experiment_name},
    )
    monkeypatch.setattr(
        archival,
        "exact_experiment_acl",
        lambda *_args, **_kwargs: harness.acl,
    )
    postflights: list[dict[str, Any]] = []

    def postflight(
        _workspace: Any,
        _registry: Any,
        _tracking: Any,
        *,
        completion: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        postflights.append(completion)
        return completion

    from tools.databricks import gateway_model_lifecycle_audit

    monkeypatch.setattr(
        gateway_model_lifecycle_audit,
        "assert_completed_gateway_archive",
        postflight,
    )
    return postflights


def _run_archive(harness: _ArchiveHarness) -> dict[str, Any]:
    return archival.archive_gateway_model(
        harness.workspace(),
        object(),
        harness,
        scope=_scope(),
        model_name=_MODEL,
        resolve_delta_version=lambda _name: "7",
    )


def test_full_archival_fences_every_mutation_and_runs_live_postflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _ArchiveHarness()
    postflights = _install_archive_harness(monkeypatch, harness)

    completion = _run_archive(harness)

    mutations = [
        event
        for event in harness.events
        if not event.startswith("assert:") and event != "protection-fence"
    ]
    assert mutations == [
        "record-mkdir:pointer",
        "record-upload:pointer",
        "record-mkdir:stage",
        "record-upload:stage",
        "table-owner",
        "model-owner",
        "experiment-mkdir",
        "experiment-rename",
        "experiment-acl",
        "record-mkdir:completion",
        "record-upload:completion",
        "record-mkdir:head",
        "record-upload:head",
    ]
    for index, event in enumerate(harness.events):
        if event == "protection-fence":
            assert index > 0
            assert harness.events[index - 1].startswith("assert:")
        elif event.startswith(("table-", "model-", "experiment-")):
            assert index > 0
            assert harness.events[index - 1] == "protection-fence"
        elif event.startswith("record-"):
            assert index > 0
            assert harness.events[index - 1].startswith("assert:")
    assert harness.events.count("protection-fence") == 7
    assert harness.checks == 16
    assert completion == harness.completion
    assert postflights == [harness.completion]


@pytest.mark.parametrize(
    ("failed_check", "blocked_mutation"),
    [
        (1, "record-mkdir:pointer"),
        (2, "record-mkdir:pointer"),
        (3, "record-upload:pointer"),
        (4, "record-mkdir:stage"),
        (5, "record-upload:stage"),
        (6, "table-owner"),
        (7, "table-owner"),
        (8, "model-owner"),
        (9, "experiment-mkdir"),
        (10, "experiment-rename"),
        (11, "experiment-acl"),
        (12, "record-mkdir:completion"),
        (13, "record-mkdir:completion"),
        (14, "record-upload:completion"),
        (15, "record-mkdir:head"),
        (16, "record-upload:head"),
    ],
)
def test_lease_loss_immediately_before_each_mutation_blocks_that_mutation(
    monkeypatch: pytest.MonkeyPatch,
    failed_check: int,
    blocked_mutation: str,
) -> None:
    harness = _ArchiveHarness(fail_check=failed_check)
    _install_archive_harness(monkeypatch, harness)

    with pytest.raises(RuntimeError, match="deployment lease lost"):
        _run_archive(harness)

    assert blocked_mutation not in harness.events


@pytest.mark.parametrize(
    ("drift_read", "blocked_mutation"),
    [
        (2, "table-owner"),
        (3, "model-owner"),
        (4, "experiment-mkdir"),
        (5, "experiment-rename"),
        (6, "experiment-acl"),
    ],
)
def test_registration_journal_appearing_at_each_mutation_fence_blocks_change(
    monkeypatch: pytest.MonkeyPatch,
    drift_read: int,
    blocked_mutation: str,
) -> None:
    real_fence = archival._fence
    harness = _ArchiveHarness()
    _install_archive_harness(monkeypatch, harness)

    def observed_real_fence(*args: Any, **kwargs: Any) -> None:
        real_fence(*args, **kwargs)
        harness.events.append("protection-fence")

    monkeypatch.setattr(archival, "_fence", observed_real_fence)
    monkeypatch.setattr(
        archival,
        "_assert_frozen_versions",
        lambda *_args, **_kwargs: "a" * 64,
    )
    monkeypatch.setattr(
        archival,
        "_assert_tables",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        archival,
        "inventory_gateway_serving",
        lambda *_args, **_kwargs: ((), ()),
    )
    protection_reads = 0

    def protection_inventory(*_args: Any, **_kwargs: Any) -> tuple[dict[str, Any], ...]:
        nonlocal protection_reads
        protection_reads += 1
        if protection_reads < drift_read:
            return ()
        return (
            {
                "kind": "registration-recovery",
                "gateway_model_name": _MODEL,
            },
        )

    monkeypatch.setattr(archival, "_protection_inventory", protection_inventory)

    with pytest.raises(
        RuntimeError,
        match="serving/protection fence drifted",
    ):
        _run_archive(harness)

    assert protection_reads == drift_read
    assert blocked_mutation not in harness.events


def test_operation_completion_recovery_persists_head_then_live_postflights(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _ArchiveHarness()
    postflights = _install_archive_harness(
        monkeypatch,
        harness,
        operation_completions=[harness.completion],
    )
    monkeypatch.setattr(
        archival,
        "_fresh_stage",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("fresh inventory must not run during completion recovery")
        ),
    )

    completion = _run_archive(harness)

    assert completion == harness.completion
    assert [
        event for event in harness.events if not event.startswith("assert:")
    ] == ["record-mkdir:head", "record-upload:head"]
    assert postflights == [harness.completion]


def test_existing_archived_head_is_never_returned_without_live_postflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _ArchiveHarness()
    harness.records[
        archival.archived_head_path(_scope().app_name, _MODEL)
    ] = harness.completion
    postflights = _install_archive_harness(monkeypatch, harness)

    completion = _run_archive(harness)

    assert completion == harness.completion
    assert harness.events == ["assert:1"]
    assert postflights == [harness.completion]


def test_fresh_lease_recovers_partial_archival_from_stable_pointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _ArchiveHarness(
        table_owner=_ARCHIVE_OWNER,
        experiment_name=_ARCHIVE_EXPERIMENT,
    )
    pointer_path = archival.in_progress_path(_scope().app_name, _MODEL)
    harness.records[pointer_path] = {"phase": "staged", "prior": True}
    postflights = _install_archive_harness(monkeypatch, harness)
    adopted: list[dict[str, Any]] = []

    def adopt(
        _workspace: Any,
        *,
        pointer: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        adopted.append(pointer)
        return harness.stage

    monkeypatch.setattr(archival, "_adopt_stage", adopt)
    monkeypatch.setattr(
        archival,
        "_fresh_stage",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("fresh inventory must not replace the stable pointer")
        ),
    )

    completion = _run_archive(harness)

    assert completion == harness.completion
    assert adopted == [{"phase": "staged", "prior": True}]
    mutations = [
        event for event in harness.events if not event.startswith("assert:")
    ]
    assert "record-mkdir:pointer" not in mutations
    assert "record-upload:pointer" not in mutations
    assert "table-owner" not in mutations
    assert "experiment-rename" not in mutations
    assert "model-owner" in mutations
    assert "record-upload:head" in mutations
    assert postflights == [harness.completion]
