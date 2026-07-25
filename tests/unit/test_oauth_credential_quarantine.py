from __future__ import annotations

import base64
import io
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.services.ai_gateway_proof_attestation import (
    derive_gateway_proof_verify_key,
)
from tools.databricks import (
    app_deployment_lease,
    oauth_credential_boundary,
    oauth_credential_quarantine,
    oauth_credential_record_inventory,
    oauth_credential_recovery,
)
from tools.databricks import oauth_credential_records as records
from tools.databricks.oauth_credential_boundary import (
    app_credential_mutation_boundary,
    held_deployment_credential_assertion,
)
from tools.databricks.oauth_credential_creation import (
    create_exact_oauth_credential,
)
from tools.databricks.oauth_credential_quarantine import (
    CredentialMutationContext,
    CredentialMutationFence,
    CredentialMutationQuarantineError,
    CredentialMutationTerminalFenceError,
    assert_no_credential_quarantine,
    raise_credential_quarantine,
)
from tools.databricks.oauth_credential_recovery import (
    OrphanCredentialLeaseCoordinates,
    orphan_credential_mutation_lease_coordinates,
    recover_oauth_credential_mutation,
    recover_orphan_credential_mutation_lease,
)

_SIGNING_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")
_VERIFY_KEY = derive_gateway_proof_verify_key(_SIGNING_KEY)
_GLOBAL_LEASE_ID = "11111111-1111-4111-8111-111111111111"
_GLOBAL_GENERATION_ID = "22222222-2222-4222-8222-222222222222"
_RESOLVER_LEASE_ID = "55555555-5555-4555-8555-555555555555"
_RESOLVER_GENERATION_ID = "66666666-6666-4666-8666-666666666666"
_SOURCE_GIT_SHA = "a" * 40
_CONTEXT = CredentialMutationContext(
    authority_scope="workspace",
    authority_identity="application-id",
    provider_api="workspace.service_principal_secrets_proxy",
    operation_mode="persistent_delivery",
    sink_descriptor="github:entrada.test/repo:atomic=true:CLIENT_ID,CLIENT_SECRET",
    credential_lifetime_seconds=0,
    sink_repository="entrada.test/repo",
    sink_secret_names=frozenset({"CLIENT_ID", "CLIENT_SECRET"}),
    sink_atomic_credential_bundle=True,
)
_CUTOVER_CONTEXT = replace(
    _CONTEXT,
    retirement_mode="signed_app_cutover",
)


@pytest.fixture(autouse=True)
def _credential_record_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", _SIGNING_KEY)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", _VERIFY_KEY)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY", "")
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS", "")


class _WorkspaceFiles:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def list(self, _path: str) -> object:
        return (SimpleNamespace(path=path) for path in sorted(self.objects))

    def upload(
        self,
        path: str,
        content: io.BytesIO,
        *,
        format: object,
        overwrite: bool,
    ) -> None:
        assert format is not None
        assert not overwrite
        assert path not in self.objects
        self.objects[path] = content.read()

    def download(self, path: str) -> io.BytesIO:
        return io.BytesIO(self.objects[path])


def _workspace() -> SimpleNamespace:
    return SimpleNamespace(workspace=_WorkspaceFiles())


def _outer_fence(
    workspace: object,
    *,
    app_name: str = "mip-app-one",
    assertion: object | None = None,
) -> CredentialMutationFence:
    callback = assertion if callable(assertion) else (lambda: None)
    return CredentialMutationFence(
        workspace=workspace,
        app_name=app_name,
        lease_id="33333333-3333-4333-8333-333333333333",
        source_git_sha=_SOURCE_GIT_SHA,
        writer_application_id="runtime-writer",
        assertion=callback,
    )


def _global_lease_record() -> dict[str, object]:
    return {
        "version": 4,
        "app_name": (
            oauth_credential_quarantine.CREDENTIAL_MUTATION_LEASE_NAME
        ),
        "lease_id": _GLOBAL_LEASE_ID,
        "generation_id": _GLOBAL_GENERATION_ID,
        "generation_seq": 0,
        "recovery_root_lease_id": _GLOBAL_LEASE_ID,
        "source_git_sha": _SOURCE_GIT_SHA,
        "writer_application_id": "runtime-writer",
        "attestation_signature": "signed-lease",
    }


def _resolver_lease_record() -> dict[str, object]:
    return {
        "version": 4,
        "app_name": (
            oauth_credential_quarantine.CREDENTIAL_MUTATION_LEASE_NAME
        ),
        "lease_id": _RESOLVER_LEASE_ID,
        "generation_id": _RESOLVER_GENERATION_ID,
        "generation_seq": 1,
        "recovery_root_lease_id": _GLOBAL_LEASE_ID,
        "source_git_sha": _SOURCE_GIT_SHA,
        "writer_application_id": "runtime-writer",
        "attestation_signature": "signed-resolver-lease",
    }


def _patch_canonical_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def canonical(
        _workspace: object,
        resolution: dict[str, object],
        **_kwargs: object,
    ) -> dict[str, object]:
        if resolution["resolver_lease_id"] == _GLOBAL_LEASE_ID:
            return _global_lease_record()
        if resolution["resolver_lease_id"] == _RESOLVER_LEASE_ID:
            return _resolver_lease_record()
        raise RuntimeError("resolver generation is not canonical")

    monkeypatch.setattr(
        oauth_credential_record_inventory,
        "canonical_resolver_lease_record",
        canonical,
    )
    monkeypatch.setattr(
        oauth_credential_record_inventory,
        "canonical_resolver_lease_records",
        lambda _workspace, *, app_name: {
            _GLOBAL_GENERATION_ID: _global_lease_record(),
            _RESOLVER_GENERATION_ID: _resolver_lease_record(),
        }
        if app_name
        == oauth_credential_quarantine.CREDENTIAL_MUTATION_LEASE_NAME
        else pytest.fail("unexpected resolver lease name"),
    )
    monkeypatch.setattr(
        oauth_credential_recovery,
        "canonical_resolver_lease_record",
        canonical,
    )


def _resign_record(
    workspace: SimpleNamespace,
    path: str,
    *,
    replacements: dict[str, object],
) -> None:
    unsigned, _encoded = records.read_json(workspace, path)
    unsigned.update(replacements)
    workspace.workspace.objects[path] = records.canonical_json(
        records._sign(unsigned)  # noqa: SLF001 - adversarial signed-record test
    )


def _delivered_record_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[SimpleNamespace, str]:
    workspace = _workspace()
    events: list[str] = []
    _patch_global_lease(monkeypatch, events=events)
    session = _outer_fence(workspace).begin_session(
        label="M2M OAuth",
        principal_id="principal-id",
        context=_CONTEXT,
    )
    intent = session.persist_intent(before_ids=frozenset({"existing"}))
    intent.observe(
        credential_id="created",
        observed_ids=frozenset({"existing", "created"}),
    )
    intent.arm_sink(
        repository="entrada.test/repo",
        secret_names=frozenset({"CLIENT_ID", "CLIENT_SECRET"}),
        atomic_credential_bundle=True,
    )
    intent.acknowledge_delivery(
        acknowledged_ids=frozenset({"existing", "created"})
    )
    intent.resolve(
        outcome="delivered",
        final_ids=frozenset({"created"}),
        retained_credential_id="created",
        sink_disposition="acknowledged",
    )
    return workspace, intent.path


def _patch_global_lease(
    monkeypatch: pytest.MonkeyPatch,
    *,
    events: list[str],
    release_error: BaseException | None = None,
) -> None:
    _patch_canonical_resolver(monkeypatch)

    def acquire(_workspace: object, **kwargs: object) -> str:
        events.append(f"acquire:{kwargs['app_name']}")
        return _GLOBAL_LEASE_ID

    def held(_workspace: object, **kwargs: object) -> object:
        assert kwargs["app_name"] == (
            oauth_credential_quarantine.CREDENTIAL_MUTATION_LEASE_NAME
        )

        def assertion() -> None:
            events.append("global-held")

        return assertion

    def assert_held(_workspace: object, **kwargs: object) -> dict[str, object]:
        assert kwargs["lease_id"] == _GLOBAL_LEASE_ID
        return _global_lease_record()

    def release(_workspace: object, **kwargs: object) -> None:
        events.append(f"release:{kwargs['app_name']}")
        if release_error is not None:
            raise release_error

    monkeypatch.setattr(app_deployment_lease, "acquire", acquire)
    monkeypatch.setattr(app_deployment_lease, "held_assertion", held)
    monkeypatch.setattr(app_deployment_lease, "assert_held", assert_held)
    monkeypatch.setattr(app_deployment_lease, "release", release)


def _patch_recovery_lease(
    monkeypatch: pytest.MonkeyPatch,
    *,
    events: list[str],
    delete_release_error: BaseException | None = None,
) -> None:
    _patch_canonical_resolver(monkeypatch)

    def acquire(_workspace: object, **kwargs: object) -> str:
        assert kwargs == {
            "app_name": (
                oauth_credential_quarantine.CREDENTIAL_MUTATION_LEASE_NAME
            ),
            "source_git_sha": _SOURCE_GIT_SHA,
            "writer_application_id": "runtime-writer",
            "expired_recovery_lease_id": _GLOBAL_LEASE_ID,
        }
        events.append("resolver-acquire")
        return _RESOLVER_LEASE_ID

    def assert_held(_workspace: object, **kwargs: object) -> dict[str, object]:
        assert kwargs["lease_id"] == _RESOLVER_LEASE_ID
        return _resolver_lease_record()

    def held(_workspace: object, **kwargs: object) -> object:
        assert kwargs["lease_id"] == _RESOLVER_LEASE_ID

        def assertion() -> None:
            events.append("resolver-held")

        return assertion

    def release(_workspace: object, **kwargs: object) -> None:
        assert kwargs["lease_id"] == _RESOLVER_LEASE_ID
        events.append("resolver-release")
        if delete_release_error is not None:
            raise delete_release_error

    monkeypatch.setattr(app_deployment_lease, "acquire", acquire)
    monkeypatch.setattr(app_deployment_lease, "assert_held", assert_held)
    monkeypatch.setattr(app_deployment_lease, "held_assertion", held)
    monkeypatch.setattr(app_deployment_lease, "release", release)


def test_quarantine_record_is_durable_exact_and_blocks_future_baselines(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = _workspace()
    marker = tmp_path / "credential-quarantine.marker"
    monkeypatch.setenv("MIP_OAUTH_CREDENTIAL_QUARANTINE_FILE", str(marker))
    fence = CredentialMutationFence(
        workspace=workspace,
        app_name="mip-app",
        lease_id="lease-id",
        source_git_sha="a" * 40,
        writer_application_id="runtime-writer",
        assertion=lambda: None,
    )

    with pytest.raises(CredentialMutationQuarantineError, match="cleanup unproven"):
        raise_credential_quarantine(
            message="cleanup unproven",
            label="M2M OAuth",
            principal_id="principal-id",
            before_ids=frozenset({"existing"}),
            candidate_ids=frozenset({"new"}),
            fence=fence,
        )

    quarantine_path = (
        "/.mip-deployment-leases/"
        "mip-app.lease-id.oauth-credential-quarantine.json"
    )
    payload, _encoded = records.read_json(workspace, quarantine_path)
    assert payload == {
        "app_name": "mip-app",
        "before_credential_ids": ["existing"],
        "candidate_credential_ids": ["new"],
        "label": "M2M OAuth",
        "lease_id": "lease-id",
        "intent_path": "",
        "principal_id": "principal-id",
        "source_git_sha": "a" * 40,
        "version": 2,
    }
    assert "retain the deployment lease" in marker.read_text(encoding="utf-8")
    with pytest.raises(
        CredentialMutationQuarantineError,
        match=quarantine_path,
    ):
        assert_no_credential_quarantine(workspace, app_name="mip-app")


def test_acquired_app_boundary_fences_and_releases_exact_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    checks: list[str] = []
    releases: list[tuple[object, str, str]] = []
    monkeypatch.delenv("MIP_APP_DEPLOYMENT_LEASE_ID", raising=False)
    monkeypatch.setattr(
        oauth_credential_boundary,
        "assert_no_credential_quarantine",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        app_deployment_lease,
        "acquire",
        lambda observed, **kwargs: (
            checks.append(f"acquire:{kwargs['writer_application_id']}") or "lease-id"
        ),
    )

    def held(
        observed: object,
        *,
        app_name: str,
        lease_id: str,
        source_git_sha: str,
    ) -> object:
        assert observed is workspace
        assert (app_name, lease_id, source_git_sha) == (
            "mip-app",
            "lease-id",
            "a" * 40,
        )

        def assertion() -> None:
            checks.append("held")

        return assertion

    monkeypatch.setattr(app_deployment_lease, "held_assertion", held)
    monkeypatch.setattr(
        app_deployment_lease,
        "release",
        lambda observed, *, app_name, lease_id: releases.append(
            (observed, app_name, lease_id)
        ),
    )

    with app_credential_mutation_boundary(
        workspace,
        app_name="mip-app",
        writer_application_id="runtime-writer",
        source_git_sha="a" * 40,
    ) as assertion:
        assertion()

    assert checks == ["acquire:runtime-writer", "held", "held", "held"]
    assert releases == [(workspace, "mip-app", "lease-id")]


def test_borrowed_deployment_boundary_is_not_released(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    checks: list[str] = []
    monkeypatch.setenv("MIP_APP_DEPLOYMENT_LEASE_ID", "borrowed-lease")
    monkeypatch.setattr(
        oauth_credential_boundary,
        "assert_no_credential_quarantine",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        app_deployment_lease,
        "held_assertion",
        lambda *_args, **_kwargs: lambda: checks.append("held"),
    )
    monkeypatch.setattr(
        app_deployment_lease,
        "assert_held",
        lambda *_args, **_kwargs: {
            "writer_application_id": "runtime-writer"
        },
    )
    monkeypatch.setattr(
        app_deployment_lease,
        "acquire",
        lambda *_args, **_kwargs: pytest.fail("borrowed lease was reacquired"),
    )
    monkeypatch.setattr(
        app_deployment_lease,
        "release",
        lambda *_args, **_kwargs: pytest.fail("borrowed lease was released"),
    )

    with app_credential_mutation_boundary(
        workspace,
        app_name="mip-app",
        writer_application_id="runtime-writer",
        source_git_sha="b" * 40,
    ) as assertion:
        assertion()

    assert checks == ["held", "held", "held"]


def test_acquired_boundary_retains_lease_after_quarantine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    releases: list[str] = []
    monkeypatch.delenv("MIP_APP_DEPLOYMENT_LEASE_ID", raising=False)
    monkeypatch.setattr(
        oauth_credential_boundary,
        "assert_no_credential_quarantine",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        app_deployment_lease,
        "acquire",
        lambda *_args, **_kwargs: "lease-id",
    )
    monkeypatch.setattr(
        app_deployment_lease,
        "held_assertion",
        lambda *_args, **_kwargs: lambda: None,
    )
    monkeypatch.setattr(
        app_deployment_lease,
        "assert_held",
        lambda *_args, **_kwargs: {
            "writer_application_id": "runtime-writer"
        },
    )
    monkeypatch.setattr(
        app_deployment_lease,
        "release",
        lambda *_args, **_kwargs: releases.append("released"),
    )

    with (
        pytest.raises(CredentialMutationQuarantineError),
        app_credential_mutation_boundary(
            workspace,
            app_name="mip-app",
            writer_application_id="runtime-writer",
            source_git_sha="c" * 40,
        ),
    ):
        raise CredentialMutationQuarantineError(
            "ambiguous",
            label="test",
            principal_id="principal-id",
            before_ids=frozenset(),
        )

    assert releases == []


def test_acquired_boundary_retains_lease_after_final_fence_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    assertion_calls = 0
    releases: list[str] = []

    def assertion() -> None:
        nonlocal assertion_calls
        assertion_calls += 1
        if assertion_calls == 2:
            raise RuntimeError("outer lease lost")

    monkeypatch.delenv("MIP_APP_DEPLOYMENT_LEASE_ID", raising=False)
    monkeypatch.setattr(
        oauth_credential_boundary,
        "assert_no_credential_quarantine",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        app_deployment_lease,
        "acquire",
        lambda *_args, **_kwargs: "lease-id",
    )
    monkeypatch.setattr(
        app_deployment_lease,
        "held_assertion",
        lambda *_args, **_kwargs: assertion,
    )
    monkeypatch.setattr(
        app_deployment_lease,
        "release",
        lambda *_args, **_kwargs: releases.append("released"),
    )

    with (
        pytest.raises(
            CredentialMutationTerminalFenceError,
            match="terminal",
        ),
        app_credential_mutation_boundary(
            workspace,
            app_name="mip-app",
            writer_application_id="runtime-writer",
            source_git_sha="c" * 40,
        ),
    ):
        pass

    assert releases == []


def test_borrowed_boundary_propagates_quarantine_without_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    monkeypatch.setenv("MIP_APP_DEPLOYMENT_LEASE_ID", "borrowed-lease")
    monkeypatch.setattr(
        oauth_credential_boundary,
        "assert_no_credential_quarantine",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        app_deployment_lease,
        "held_assertion",
        lambda *_args, **_kwargs: lambda: None,
    )
    monkeypatch.setattr(
        app_deployment_lease,
        "assert_held",
        lambda *_args, **_kwargs: {
            "writer_application_id": "runtime-writer"
        },
    )
    monkeypatch.setattr(
        app_deployment_lease,
        "acquire",
        lambda *_args, **_kwargs: pytest.fail("borrowed lease was reacquired"),
    )
    monkeypatch.setattr(
        app_deployment_lease,
        "release",
        lambda *_args, **_kwargs: pytest.fail("borrowed lease was released"),
    )

    with (
        pytest.raises(CredentialMutationQuarantineError),
        app_credential_mutation_boundary(
            workspace,
            app_name="mip-app",
            writer_application_id="runtime-writer",
            source_git_sha="d" * 40,
        ),
    ):
        raise CredentialMutationQuarantineError(
            "ambiguous",
            label="test",
            principal_id="principal-id",
            before_ids=frozenset(),
        )


@pytest.mark.parametrize(
    "failure_phase",
    ("assert-held", "build-held-assertion", "session-check"),
)
def test_post_acquire_pre_intent_setup_failure_releases_global_lease(
    monkeypatch: pytest.MonkeyPatch,
    failure_phase: str,
) -> None:
    workspace = _workspace()
    events: list[str] = []
    _patch_global_lease(monkeypatch, events=events)

    if failure_phase == "assert-held":
        monkeypatch.setattr(
            app_deployment_lease,
            "assert_held",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("lease read failed")
            ),
        )
    elif failure_phase == "build-held-assertion":
        monkeypatch.setattr(
            app_deployment_lease,
            "held_assertion",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("lease assertion build failed")
            ),
        )
    else:
        monkeypatch.setattr(
            app_deployment_lease,
            "held_assertion",
            lambda *_args, **_kwargs: lambda: (_ for _ in ()).throw(
                RuntimeError("lease assertion failed")
            ),
        )

    with pytest.raises(RuntimeError, match="failed"):
        _outer_fence(workspace).begin_session(
            label="M2M OAuth",
            principal_id="principal-id",
            context=_CONTEXT,
        )

    assert events[0] == "acquire:mip-oauth-credential-mutations"
    assert events[-1] == "release:mip-oauth-credential-mutations"
    assert records.record_paths(workspace) == ()


def test_pre_intent_setup_release_failure_is_terminal_and_marks_parent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = _workspace()
    events: list[str] = []
    marker = tmp_path / "credential-quarantine.marker"
    monkeypatch.setenv("MIP_OAUTH_CREDENTIAL_QUARANTINE_FILE", str(marker))
    _patch_global_lease(
        monkeypatch,
        events=events,
        release_error=PermissionError("release denied"),
    )
    monkeypatch.setattr(
        app_deployment_lease,
        "assert_held",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("lease read failed")
        ),
    )

    with pytest.raises(
        CredentialMutationTerminalFenceError,
        match="pre-intent lease release is unproven",
    ):
        _outer_fence(workspace).begin_session(
            label="M2M OAuth",
            principal_id="principal-id",
            context=_CONTEXT,
        )

    assert events[-1] == "release:mip-oauth-credential-mutations"
    assert "retain the deployment lease" in marker.read_text(encoding="utf-8")
    assert records.record_paths(workspace) == ()


def test_intent_persistence_failure_retains_recoverable_lease_without_quarantine(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = _workspace()
    events: list[str] = []
    marker = tmp_path / "credential-quarantine.marker"
    monkeypatch.setenv("MIP_OAUTH_CREDENTIAL_QUARANTINE_FILE", str(marker))
    _patch_global_lease(monkeypatch, events=events)
    session = _outer_fence(workspace).begin_session(
        label="M2M OAuth",
        principal_id="principal-id",
        context=_CONTEXT,
    )
    monkeypatch.setattr(
        records,
        "write_immutable_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            TimeoutError("intent write response lost")
        ),
    )

    with pytest.raises(
        CredentialMutationTerminalFenceError,
        match="intent persistence is unproven",
    ):
        session.persist_intent(before_ids=frozenset({"existing"}))

    assert session.intent_path == records.intent_path(
        oauth_credential_quarantine.CREDENTIAL_MUTATION_LEASE_NAME,
        _GLOBAL_LEASE_ID,
        _GLOBAL_LEASE_ID,
    )
    assert not any(event.startswith("release:") for event in events)
    assert records.record_paths(workspace) == ()
    assert "retain the deployment lease" in marker.read_text(encoding="utf-8")


def test_unused_pre_intent_lease_release_failure_is_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    events: list[str] = []
    _patch_global_lease(
        monkeypatch,
        events=events,
        release_error=TimeoutError("release response lost"),
    )
    session = _outer_fence(workspace).begin_session(
        label="M2M OAuth",
        principal_id="principal-id",
        context=_CONTEXT,
    )

    with pytest.raises(
        CredentialMutationTerminalFenceError,
        match="unused credential lease release is unproven",
    ):
        session.abort_before_intent()

    assert not session.released
    assert events[-1] == "release:mip-oauth-credential-mutations"
    assert records.record_paths(workspace) == ()


def test_global_session_journals_signed_phases_and_releases_after_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    events: list[str] = []
    _patch_global_lease(monkeypatch, events=events)

    session = _outer_fence(workspace).begin_session(
        label="M2M OAuth",
        principal_id="principal-id",
        context=_CONTEXT,
    )
    events.append("baseline")
    intent = session.persist_intent(before_ids=frozenset({"existing"}))
    intent.observe(
        credential_id="created",
        observed_ids=frozenset({"existing", "created"}),
    )
    intent.arm_sink(
        repository="entrada.test/repo",
        secret_names=frozenset({"CLIENT_ID", "CLIENT_SECRET"}),
        atomic_credential_bundle=True,
    )
    intent.acknowledge_delivery(
        acknowledged_ids=frozenset({"existing", "created"})
    )
    intent.resolve(
        outcome="delivered",
        final_ids=frozenset({"created"}),
        retained_credential_id="created",
        sink_disposition="acknowledged",
    )

    paths = records.record_paths(workspace)
    assert len(paths) == 5
    assert records.unresolved_record_paths(workspace) == ()
    assert events[0] == (
        "acquire:"
        f"{oauth_credential_quarantine.CREDENTIAL_MUTATION_LEASE_NAME}"
    )
    assert events.index("baseline") > events.index("acquire:mip-oauth-credential-mutations")
    assert events[-1] == "release:mip-oauth-credential-mutations"
    for path in paths:
        raw = workspace.workspace.objects[path]
        assert b"one-shot-secret" not in raw
        signed = json.loads(raw)
        assert signed["attestation_algorithm"] == records.ATTESTATION_ALGORITHM
        assert signed["attestation_verify_key"] == _VERIFY_KEY
        records.read_json(workspace, path)


@pytest.mark.parametrize(
    ("field_name", "substitute"),
    (
        ("sink_secret_names", "['CLIENT_ID', 'CLIENT_SECRET']"),
        ("sink_atomic_credential_bundle", 1),
        ("credential_lifetime_seconds", "0"),
        ("lease_generation_seq", "0"),
        ("lease_generation_seq", False),
        ("principal_id", 7),
        ("retained_credential_id", 0),
    ),
)
def test_signed_resolution_rejects_every_scalar_type_substitution(
    monkeypatch: pytest.MonkeyPatch,
    field_name: str,
    substitute: object,
) -> None:
    workspace, intent_path = _delivered_record_fixture(monkeypatch)
    resolution_path = records.resolution_path(intent_path)
    _resign_record(
        workspace,
        resolution_path,
        replacements={field_name: substitute},
    )

    with pytest.raises(RuntimeError):
        records.unresolved_record_paths(workspace)


def test_signed_resolution_rejects_fabricated_successor_lease_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, intent_path = _delivered_record_fixture(monkeypatch)
    resolution_path = records.resolution_path(intent_path)
    _resign_record(
        workspace,
        resolution_path,
        replacements={
            "resolver_lease_id": (
                "77777777-7777-4777-8777-777777777777"
            ),
            "resolver_lease_generation_id": (
                "88888888-8888-4888-8888-888888888888"
            ),
            "resolver_lease_generation_seq": 999,
            "resolver_lease_record_sha256": "b" * 64,
            "resolver_source_git_sha": "b" * 40,
        },
    )

    with pytest.raises(RuntimeError, match="not canonical"):
        records.unresolved_record_paths(workspace)


@pytest.mark.parametrize(
    ("record_kind", "field_name", "substitute"),
    (
        ("intent", "principal_id", 7),
        ("intent", "label", 7),
        ("observed", "lease_generation_seq", "0"),
        ("sink", "atomic_credential_bundle", 1),
    ),
)
def test_signed_phase_records_reject_exact_schema_substitution(
    monkeypatch: pytest.MonkeyPatch,
    record_kind: str,
    field_name: str,
    substitute: object,
) -> None:
    workspace, intent_path = _delivered_record_fixture(monkeypatch)
    phase_path = {
        "intent": intent_path,
        "observed": records.observed_path(intent_path),
        "sink": records.sink_attempt_path(intent_path),
    }[record_kind]
    _resign_record(
        workspace,
        phase_path,
        replacements={field_name: substitute},
    )

    with pytest.raises(RuntimeError):
        records.unresolved_record_paths(workspace)


def test_signed_intent_cannot_move_to_an_alternate_lease_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    events: list[str] = []
    _patch_global_lease(monkeypatch, events=events)
    session = _outer_fence(workspace).begin_session(
        label="M2M OAuth",
        principal_id="principal-id",
        context=_CONTEXT,
    )
    intent = session.persist_intent(before_ids=frozenset({"existing"}))
    unsigned, _encoded = records.read_json(workspace, intent.path)
    del workspace.workspace.objects[intent.path]
    alternate_path = records.intent_path(
        "alternate-app-journal",
        _GLOBAL_LEASE_ID,
        _GLOBAL_LEASE_ID,
    )
    unsigned["app_name"] = "alternate-app-journal"
    workspace.workspace.objects[alternate_path] = records.canonical_json(
        records._sign(unsigned)  # noqa: SLF001 - adversarial namespace test
    )

    with pytest.raises(RuntimeError, match="intent is malformed"):
        records.unresolved_record_paths(workspace)


@pytest.mark.parametrize(
    ("repository", "secret_names", "atomic_bundle"),
    (
        (
            "attacker.test/repo",
            frozenset({"CLIENT_ID", "CLIENT_SECRET"}),
            True,
        ),
        ("entrada.test/repo", frozenset({"CLIENT_SECRET"}), True),
        (
            "entrada.test/repo",
            frozenset({"CLIENT_ID", "CLIENT_SECRET"}),
            False,
        ),
    ),
)
def test_sink_attempt_cannot_substitute_signed_intent_coordinates(
    monkeypatch: pytest.MonkeyPatch,
    repository: str,
    secret_names: frozenset[str],
    atomic_bundle: bool,
) -> None:
    workspace = _workspace()
    events: list[str] = []
    _patch_global_lease(monkeypatch, events=events)
    session = _outer_fence(workspace).begin_session(
        label="M2M OAuth",
        principal_id="principal-id",
        context=_CONTEXT,
    )
    intent = session.persist_intent(before_ids=frozenset({"existing"}))
    intent.observe(
        credential_id="created",
        observed_ids=frozenset({"existing", "created"}),
    )

    with pytest.raises(RuntimeError, match="do not match the signed intent"):
        intent.arm_sink(
            repository=repository,
            secret_names=secret_names,
            atomic_credential_bundle=atomic_bundle,
        )

    assert intent.sink_path == ""
    assert records.sink_attempt_path(intent.path) not in records.record_paths(
        workspace
    )


def test_signed_app_cutover_requires_persistent_atomic_sink() -> None:
    with pytest.raises(ValueError, match="context is invalid"):
        replace(
            _CONTEXT,
            sink_descriptor=(
                "github:entrada.test/repo:"
                "atomic=false:CLIENT_ID,CLIENT_SECRET"
            ),
            sink_atomic_credential_bundle=False,
            retirement_mode="signed_app_cutover",
        )


def test_unresolved_global_intent_blocks_an_alternate_app_before_acquire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    events: list[str] = []
    _patch_global_lease(monkeypatch, events=events)
    session = _outer_fence(workspace).begin_session(
        label="M2M OAuth",
        principal_id="principal-id",
        context=_CONTEXT,
    )
    session.persist_intent(before_ids=frozenset({"existing"}))

    with pytest.raises(
        CredentialMutationQuarantineError,
        match="globally unresolved|is unresolved",
    ):
        _outer_fence(workspace, app_name="mip-app-two").begin_session(
            label="M2M OAuth",
            principal_id="other-principal",
            context=_CONTEXT,
        )

    assert events.count("acquire:mip-oauth-credential-mutations") == 1
    assert not any(event.startswith("release:") for event in events)


def test_interruption_after_provider_create_leaves_observed_global_recovery_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    events: list[str] = []
    _patch_global_lease(monkeypatch, events=events)
    live_ids = ["existing"]

    def list_credentials() -> object:
        events.append("provider-list")
        return (SimpleNamespace(id=value) for value in live_ids)

    def create_credential() -> object:
        events.append("provider-create")
        live_ids.append("created")
        return SimpleNamespace(
            id="created",
            secret="one-shot-super-secret",
        )

    credential = create_exact_oauth_credential(
        principal_id="principal-id",
        list_credentials=list_credentials,
        create_credential=create_credential,
        delete_credential=lambda credential_id: live_ids.remove(credential_id),
        assert_single_writer=_outer_fence(workspace),
        mutation_context=_CONTEXT,
        label="M2M OAuth",
        sleep=lambda _seconds: None,
    )

    paths = records.record_paths(workspace)
    assert credential.credential_id == "created"
    assert any(path.endswith(records.INTENT_SUFFIX) for path in paths)
    assert any(path.endswith(records.OBSERVED_SUFFIX) for path in paths)
    assert not any(path.endswith(records.RESOLUTION_SUFFIX) for path in paths)
    assert not any(event.startswith("release:") for event in events)
    assert events.index("acquire:mip-oauth-credential-mutations") < events.index(
        "provider-list"
    )
    assert all(
        b"one-shot-super-secret" not in workspace.workspace.objects[path]
        for path in paths
    )
    with pytest.raises(CredentialMutationQuarantineError, match="unresolved"):
        _outer_fence(workspace, app_name="mip-app-two").begin_session(
            label="M2M OAuth",
            principal_id="other-principal",
            context=_CONTEXT,
        )


def test_fresh_process_recovery_revokes_observed_secret_and_invalidates_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    events: list[str] = []
    _patch_global_lease(monkeypatch, events=events)
    session = _outer_fence(workspace).begin_session(
        label="M2M OAuth",
        principal_id="principal-id",
        context=_CONTEXT,
    )
    intent = session.persist_intent(before_ids=frozenset({"existing"}))
    intent.observe(
        credential_id="created",
        observed_ids=frozenset({"existing", "created"}),
    )
    intent.arm_sink(
        repository="entrada.test/repo",
        secret_names=frozenset({"CLIENT_ID", "CLIENT_SECRET"}),
        atomic_credential_bundle=True,
    )
    live_ids = ["existing", "created"]
    invalidated: list[tuple[str, frozenset[str]]] = []
    events.clear()
    _patch_recovery_lease(monkeypatch, events=events)

    result = recover_oauth_credential_mutation(
        workspace,
        intent_path=intent.path,
        outer_fence=_outer_fence(workspace),
        principal_id="principal-id",
        authority_identity="application-id",
        provider_api="workspace.service_principal_secrets_proxy",
        list_credentials=lambda: (
            SimpleNamespace(id=value) for value in live_ids
        ),
        delete_credential=lambda credential_id: live_ids.remove(credential_id),
        invalidate_sink=lambda repository, names: invalidated.append(
            (repository, names)
        ),
        sleep=lambda _seconds: None,
    )

    resolution, _encoded = records.read_json(
        workspace,
        records.resolution_path(intent.path),
    )
    assert live_ids == ["existing"]
    assert result.revoked_credential_id == "created"
    assert result.outcome == "restored"
    assert result.sink_disposition == "invalidated"
    assert invalidated == [
        (
            "entrada.test/repo",
            frozenset({"CLIENT_ID", "CLIENT_SECRET"}),
        )
    ]
    assert resolution["resolver_lease_id"] == _RESOLVER_LEASE_ID
    assert resolution["lease_id"] == _GLOBAL_LEASE_ID
    assert resolution["sink_disposition"] == "invalidated"
    assert records.unresolved_record_paths(workspace) == ()
    assert events[-1] == "resolver-release"


def test_recovery_discovers_sole_delta_when_process_died_before_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    events: list[str] = []
    _patch_global_lease(monkeypatch, events=events)
    session = _outer_fence(workspace).begin_session(
        label="M2M OAuth",
        principal_id="principal-id",
        context=_CONTEXT,
    )
    intent = session.persist_intent(before_ids=frozenset({"existing"}))
    live_ids = ["existing", "created-before-crash"]
    events.clear()
    _patch_recovery_lease(monkeypatch, events=events)

    result = recover_oauth_credential_mutation(
        workspace,
        intent_path=intent.path,
        outer_fence=_outer_fence(workspace),
        principal_id="principal-id",
        authority_identity="application-id",
        provider_api="workspace.service_principal_secrets_proxy",
        list_credentials=lambda: (
            SimpleNamespace(id=value) for value in live_ids
        ),
        delete_credential=lambda credential_id: live_ids.remove(credential_id),
        sleep=lambda _seconds: None,
    )

    assert result.revoked_credential_id == "created-before-crash"
    assert live_ids == ["existing"]
    assert records.observed_path(intent.path) in records.record_paths(workspace)
    assert records.unresolved_record_paths(workspace) == ()


def test_recovery_without_observation_or_delta_retains_global_quarantine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    events: list[str] = []
    _patch_global_lease(monkeypatch, events=events)
    session = _outer_fence(workspace).begin_session(
        label="M2M OAuth",
        principal_id="principal-id",
        context=_CONTEXT,
    )
    intent = session.persist_intent(before_ids=frozenset({"existing"}))
    events.clear()
    _patch_recovery_lease(monkeypatch, events=events)

    live_ids = ["existing"]
    with pytest.raises(
        CredentialMutationQuarantineError,
        match="delayed create cannot still commit",
    ):
        recover_oauth_credential_mutation(
            workspace,
            intent_path=intent.path,
            outer_fence=_outer_fence(workspace),
            principal_id="principal-id",
            authority_identity="application-id",
            provider_api="workspace.service_principal_secrets_proxy",
            list_credentials=lambda: (
                SimpleNamespace(id=value) for value in live_ids
            ),
            delete_credential=lambda _credential_id: pytest.fail(
                "no credential should be deleted"
            ),
            sleep=lambda _seconds: None,
        )

    live_ids.append("late-provider-commit")
    unresolved = records.unresolved_record_paths(workspace)
    assert intent.path in unresolved
    assert any(path.endswith(records.QUARANTINE_SUFFIX) for path in unresolved)
    assert not any(
        path.endswith(records.RESOLUTION_SUFFIX)
        for path in records.record_paths(workspace)
    )
    assert "resolver-release" not in events


def test_delivery_ack_recovery_finishes_partial_prior_credential_retirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    events: list[str] = []
    _patch_global_lease(monkeypatch, events=events)
    session = _outer_fence(workspace).begin_session(
        label="M2M OAuth",
        principal_id="principal-id",
        context=_CONTEXT,
    )
    intent = session.persist_intent(
        before_ids=frozenset({"old-a", "old-b"})
    )
    intent.observe(
        credential_id="created",
        observed_ids=frozenset({"old-a", "old-b", "created"}),
    )
    intent.arm_sink(
        repository="entrada.test/repo",
        secret_names=frozenset({"CLIENT_ID", "CLIENT_SECRET"}),
        atomic_credential_bundle=True,
    )
    intent.acknowledge_delivery(
        acknowledged_ids=frozenset({"old-a", "old-b", "created"})
    )
    live_ids = ["old-b", "created"]
    deleted: list[str] = []
    events.clear()
    _patch_recovery_lease(monkeypatch, events=events)

    def delete_credential(credential_id: str) -> None:
        deleted.append(credential_id)
        live_ids.remove(credential_id)

    result = recover_oauth_credential_mutation(
        workspace,
        intent_path=intent.path,
        outer_fence=_outer_fence(workspace),
        principal_id="principal-id",
        authority_identity="application-id",
        provider_api="workspace.service_principal_secrets_proxy",
        list_credentials=lambda: (
            SimpleNamespace(id=value) for value in live_ids
        ),
        delete_credential=delete_credential,
        invalidate_sink=lambda *_args: pytest.fail(
            "acknowledged sink must not be invalidated"
        ),
        sleep=lambda _seconds: None,
    )

    assert result.outcome == "delivered"
    assert result.revoked_credential_id == ""
    assert result.sink_disposition == "acknowledged"
    assert deleted == ["old-b"]
    assert live_ids == ["created"]
    assert records.unresolved_record_paths(workspace) == ()
    assert events[-1] == "resolver-release"


def test_delivery_ack_recovery_preserves_signed_blue_for_app_cutover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    events: list[str] = []
    _patch_global_lease(monkeypatch, events=events)
    session = _outer_fence(workspace).begin_session(
        label="M2M OAuth",
        principal_id="principal-id",
        context=_CUTOVER_CONTEXT,
    )
    intent = session.persist_intent(before_ids=frozenset({"signed-blue"}))
    intent.observe(
        credential_id="green",
        observed_ids=frozenset({"signed-blue", "green"}),
    )
    intent.arm_sink(
        repository="entrada.test/repo",
        secret_names=frozenset({"CLIENT_ID", "CLIENT_SECRET"}),
        atomic_credential_bundle=True,
    )
    intent.acknowledge_delivery(
        acknowledged_ids=frozenset({"signed-blue", "green"})
    )
    live_ids = ["signed-blue", "green"]
    events.clear()
    _patch_recovery_lease(monkeypatch, events=events)

    result = recover_oauth_credential_mutation(
        workspace,
        intent_path=intent.path,
        outer_fence=_outer_fence(workspace),
        principal_id="principal-id",
        authority_identity="application-id",
        provider_api="workspace.service_principal_secrets_proxy",
        list_credentials=lambda: (
            SimpleNamespace(id=value) for value in live_ids
        ),
        delete_credential=lambda _credential_id: pytest.fail(
            "signed blue must remain until App cutover"
        ),
        invalidate_sink=lambda *_args: pytest.fail(
            "acknowledged sink must not be invalidated"
        ),
        sleep=lambda _seconds: None,
    )

    assert result.outcome == "delivered"
    assert result.sink_disposition == "acknowledged"
    assert live_ids == ["signed-blue", "green"]
    resolution, _encoded = records.read_json(
        workspace,
        records.resolution_path(intent.path),
    )
    assert resolution["final_credential_ids"] == ["green", "signed-blue"]
    assert resolution["pending_retirement_credential_ids"] == [
        "signed-blue"
    ]
    assert records.unresolved_record_paths(workspace) == ()


@pytest.mark.parametrize("intent_present", (False, True))
def test_orphan_lease_inspection_exposes_signed_recovery_coordinate(
    monkeypatch: pytest.MonkeyPatch,
    intent_present: bool,
) -> None:
    workspace = _workspace()
    expected_path = records.intent_path(
        oauth_credential_quarantine.CREDENTIAL_MUTATION_LEASE_NAME,
        _GLOBAL_LEASE_ID,
        _GLOBAL_LEASE_ID,
    )
    if intent_present:
        workspace.workspace.objects[expected_path] = b"signed-intent-placeholder"
    monkeypatch.setattr(
        app_deployment_lease,
        "_download",
        lambda *_args, **_kwargs: {
            "state": "active",
            "lease_id": _GLOBAL_LEASE_ID,
            "recovery_root_lease_id": _GLOBAL_LEASE_ID,
            "source_git_sha": _SOURCE_GIT_SHA,
        },
    )

    coordinate = orphan_credential_mutation_lease_coordinates(workspace)

    assert coordinate == OrphanCredentialLeaseCoordinates(
        lease_id=_GLOBAL_LEASE_ID,
        recovery_root_lease_id=_GLOBAL_LEASE_ID,
        source_git_sha=_SOURCE_GIT_SHA,
        expected_intent_path=expected_path,
        intent_present=intent_present,
    )


def test_orphan_lease_recovery_takes_over_expired_root_and_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    coordinate = OrphanCredentialLeaseCoordinates(
        lease_id=_GLOBAL_LEASE_ID,
        recovery_root_lease_id=_GLOBAL_LEASE_ID,
        source_git_sha=_SOURCE_GIT_SHA,
        expected_intent_path=records.intent_path(
            oauth_credential_quarantine.CREDENTIAL_MUTATION_LEASE_NAME,
            _GLOBAL_LEASE_ID,
            _GLOBAL_LEASE_ID,
        ),
        intent_present=False,
    )
    events: list[str] = []
    monkeypatch.setattr(
        oauth_credential_recovery,
        "orphan_credential_mutation_lease_coordinates",
        lambda observed: coordinate
        if observed is workspace
        else pytest.fail("unexpected workspace"),
    )

    def acquire(observed: object, **kwargs: object) -> str:
        assert observed is workspace
        assert kwargs["expired_recovery_lease_id"] == _GLOBAL_LEASE_ID
        events.append("acquire")
        return _RESOLVER_LEASE_ID

    monkeypatch.setattr(app_deployment_lease, "acquire", acquire)
    monkeypatch.setattr(
        app_deployment_lease,
        "assert_held",
        lambda *_args, **_kwargs: events.append("held") or {},
    )
    monkeypatch.setattr(
        app_deployment_lease,
        "release",
        lambda *_args, **_kwargs: events.append("release"),
    )

    result = recover_orphan_credential_mutation_lease(
        workspace,
        outer_fence=_outer_fence(workspace),
        expected_lease_id=_GLOBAL_LEASE_ID,
        expected_recovery_root_lease_id=_GLOBAL_LEASE_ID,
    )

    assert result.lease_id == _GLOBAL_LEASE_ID
    assert result.recovery_root_lease_id == _GLOBAL_LEASE_ID
    assert events == ["acquire", "held", "release"]


def test_orphan_lease_recovery_rejects_authoritative_intent_before_takeover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    coordinate = OrphanCredentialLeaseCoordinates(
        lease_id=_GLOBAL_LEASE_ID,
        recovery_root_lease_id=_GLOBAL_LEASE_ID,
        source_git_sha=_SOURCE_GIT_SHA,
        expected_intent_path=records.intent_path(
            oauth_credential_quarantine.CREDENTIAL_MUTATION_LEASE_NAME,
            _GLOBAL_LEASE_ID,
            _GLOBAL_LEASE_ID,
        ),
        intent_present=True,
    )
    monkeypatch.setattr(
        oauth_credential_recovery,
        "orphan_credential_mutation_lease_coordinates",
        lambda _workspace: coordinate,
    )
    monkeypatch.setattr(
        app_deployment_lease,
        "acquire",
        lambda *_args, **_kwargs: pytest.fail(
            "authoritative intent must prevent orphan takeover"
        ),
    )

    with pytest.raises(RuntimeError, match="authoritative intent"):
        recover_orphan_credential_mutation_lease(
            workspace,
            outer_fence=_outer_fence(workspace),
            expected_lease_id=_GLOBAL_LEASE_ID,
            expected_recovery_root_lease_id=_GLOBAL_LEASE_ID,
        )


def test_recovery_callback_identity_mismatch_fails_before_takeover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    events: list[str] = []
    _patch_global_lease(monkeypatch, events=events)
    session = _outer_fence(workspace).begin_session(
        label="M2M OAuth",
        principal_id="principal-id",
        context=_CONTEXT,
    )
    intent = session.persist_intent(before_ids=frozenset({"existing"}))
    events.clear()

    with pytest.raises(RuntimeError, match="callback binding"):
        recover_oauth_credential_mutation(
            workspace,
            intent_path=intent.path,
            outer_fence=_outer_fence(workspace),
            principal_id="different-principal",
            authority_identity="application-id",
            provider_api="workspace.service_principal_secrets_proxy",
            list_credentials=lambda: (),
            delete_credential=lambda _credential_id: None,
            sleep=lambda _seconds: None,
        )

    assert events == []


def test_global_lease_serializes_different_apps_before_any_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    active = False
    acquire_names: list[str] = []

    def acquire(_workspace: object, **kwargs: object) -> str:
        nonlocal active
        acquire_names.append(str(kwargs["app_name"]))
        if active:
            raise RuntimeError("global credential lease is active")
        active = True
        return _GLOBAL_LEASE_ID

    monkeypatch.setattr(app_deployment_lease, "acquire", acquire)
    monkeypatch.setattr(
        app_deployment_lease,
        "held_assertion",
        lambda *_args, **_kwargs: lambda: None,
    )
    monkeypatch.setattr(
        app_deployment_lease,
        "assert_held",
        lambda *_args, **_kwargs: {
            "generation_id": _GLOBAL_GENERATION_ID,
            "generation_seq": 0,
            "recovery_root_lease_id": _GLOBAL_LEASE_ID,
        },
    )

    _outer_fence(workspace, app_name="mip-app-one").begin_session(
        label="M2M OAuth",
        principal_id="principal-one",
        context=_CONTEXT,
    )
    with pytest.raises(RuntimeError, match="global credential lease is active"):
        _outer_fence(workspace, app_name="mip-app-two").begin_session(
            label="M2M OAuth",
            principal_id="principal-two",
            context=_CONTEXT,
        )

    assert acquire_names == [
        oauth_credential_quarantine.CREDENTIAL_MUTATION_LEASE_NAME,
        oauth_credential_quarantine.CREDENTIAL_MUTATION_LEASE_NAME,
    ]


def test_signed_recovery_record_tampering_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = _workspace()
    fence = _outer_fence(workspace)
    monkeypatch.setenv(
        "MIP_OAUTH_CREDENTIAL_QUARANTINE_FILE",
        str(tmp_path / "marker"),
    )
    with pytest.raises(CredentialMutationQuarantineError):
        raise_credential_quarantine(
            message="cleanup unproven",
            label="M2M OAuth",
            principal_id="principal-id",
            before_ids=frozenset({"existing"}),
            fence=fence,
        )
    [path] = records.record_paths(workspace)
    tampered = json.loads(workspace.workspace.objects[path])
    tampered["principal_id"] = "attacker-controlled"
    workspace.workspace.objects[path] = records.canonical_json(tampered)

    with pytest.raises(RuntimeError, match="not authoritative"):
        records.unresolved_record_paths(workspace)


@pytest.mark.parametrize(
    ("outcome", "final_ids", "retained_id", "sink_disposition"),
    [
        ("delivered", frozenset({"existing"}), "created", "acknowledged"),
        ("restored", frozenset({"existing"}), "", "not_attempted"),
    ],
)
def test_semantically_false_terminal_record_is_quarantined(
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
    final_ids: frozenset[str],
    retained_id: str,
    sink_disposition: str,
) -> None:
    workspace = _workspace()
    events: list[str] = []
    _patch_global_lease(monkeypatch, events=events)
    session = _outer_fence(workspace).begin_session(
        label="M2M OAuth",
        principal_id="principal-id",
        context=_CONTEXT,
    )
    intent = session.persist_intent(before_ids=frozenset({"existing"}))
    intent.observe(
        credential_id="created",
        observed_ids=frozenset({"existing", "created"}),
    )
    intent.arm_sink(
        repository="entrada.test/repo",
        secret_names=frozenset({"CLIENT_ID", "CLIENT_SECRET"}),
        atomic_credential_bundle=True,
    )
    intent.acknowledge_delivery(
        acknowledged_ids=frozenset({"existing", "created"})
    )

    with pytest.raises(
        CredentialMutationQuarantineError,
        match="resolution is unproven",
    ):
        intent.resolve(
            outcome=outcome,
            final_ids=final_ids,
            retained_credential_id=retained_id,
            sink_disposition=sink_disposition,
        )

    assert any(
        path.endswith(records.QUARANTINE_SUFFIX)
        for path in records.record_paths(workspace)
    )
    assert not any(event.startswith("release:") for event in events)


def test_terminal_global_lease_release_failure_does_not_misclassify_provider_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    events: list[str] = []
    _patch_global_lease(
        monkeypatch,
        events=events,
        release_error=PermissionError("release denied"),
    )
    session = _outer_fence(workspace).begin_session(
        label="M2M OAuth",
        principal_id="principal-id",
        context=_CONTEXT,
    )
    intent = session.persist_intent(before_ids=frozenset({"existing"}))
    intent.observe(
        credential_id="created",
        observed_ids=frozenset({"existing", "created"}),
    )
    intent.arm_sink(
        repository="entrada.test/repo",
        secret_names=frozenset({"CLIENT_ID", "CLIENT_SECRET"}),
        atomic_credential_bundle=True,
    )
    intent.acknowledge_delivery(
        acknowledged_ids=frozenset({"existing", "created"})
    )

    with pytest.raises(
        CredentialMutationTerminalFenceError,
        match="terminal",
    ):
        intent.resolve(
            outcome="delivered",
            final_ids=frozenset({"created"}),
            retained_credential_id="created",
            sink_disposition="acknowledged",
        )

    assert records.unresolved_record_paths(workspace) == ()
    assert not any(
        path.endswith(records.QUARANTINE_SUFFIX)
        for path in records.record_paths(workspace)
    )


def test_fresh_process_finishes_terminal_resolver_lease_without_provider_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    events: list[str] = []
    _patch_global_lease(
        monkeypatch,
        events=events,
        release_error=TimeoutError("release response lost"),
    )
    session = _outer_fence(workspace).begin_session(
        label="M2M OAuth",
        principal_id="principal-id",
        context=_CONTEXT,
    )
    intent = session.persist_intent(before_ids=frozenset({"existing"}))
    intent.observe(
        credential_id="created",
        observed_ids=frozenset({"existing", "created"}),
    )
    intent.arm_sink(
        repository="entrada.test/repo",
        secret_names=frozenset({"CLIENT_ID", "CLIENT_SECRET"}),
        atomic_credential_bundle=True,
    )
    intent.acknowledge_delivery(
        acknowledged_ids=frozenset({"existing", "created"})
    )
    with pytest.raises(CredentialMutationTerminalFenceError):
        intent.resolve(
            outcome="delivered",
            final_ids=frozenset({"created"}),
            retained_credential_id="created",
            sink_disposition="acknowledged",
        )
    events.clear()
    _patch_recovery_lease(monkeypatch, events=events)

    result = recover_oauth_credential_mutation(
        workspace,
        intent_path=intent.path,
        outer_fence=_outer_fence(workspace),
        principal_id="principal-id",
        authority_identity="application-id",
        provider_api="workspace.service_principal_secrets_proxy",
        list_credentials=lambda: pytest.fail("terminal provider list was used"),
        delete_credential=lambda _credential_id: pytest.fail(
            "terminal provider delete was used"
        ),
        sleep=lambda _seconds: None,
    )

    assert result.revoked_credential_id == ""
    assert result.outcome == "delivered"
    assert result.sink_disposition == "acknowledged"
    assert events == [
        "resolver-acquire",
        "resolver-release",
    ]
    assert records.unresolved_record_paths(workspace) == ()


def test_recovery_delete_timeout_stays_quarantined_even_after_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    events: list[str] = []
    _patch_global_lease(monkeypatch, events=events)
    session = _outer_fence(workspace).begin_session(
        label="M2M OAuth",
        principal_id="principal-id",
        context=_CONTEXT,
    )
    intent = session.persist_intent(before_ids=frozenset({"existing"}))
    intent.observe(
        credential_id="created",
        observed_ids=frozenset({"existing", "created"}),
    )
    live_ids = ["existing", "created"]
    events.clear()
    _patch_recovery_lease(monkeypatch, events=events)

    def delete_then_timeout(credential_id: str) -> None:
        live_ids.remove(credential_id)
        raise TimeoutError("delete response lost")

    with pytest.raises(
        CredentialMutationQuarantineError,
        match="delete result is ambiguous",
    ):
        recover_oauth_credential_mutation(
            workspace,
            intent_path=intent.path,
            outer_fence=_outer_fence(workspace),
            principal_id="principal-id",
            authority_identity="application-id",
            provider_api="workspace.service_principal_secrets_proxy",
            list_credentials=lambda: (
                SimpleNamespace(id=value) for value in live_ids
            ),
            delete_credential=delete_then_timeout,
            sleep=lambda _seconds: None,
        )

    assert live_ids == ["existing"]
    assert "resolver-release" not in events
    assert any(
        path.endswith(records.QUARANTINE_SUFFIX)
        for path in records.record_paths(workspace)
    )


def test_recovery_sink_clear_failure_revokes_provider_but_stays_quarantined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    events: list[str] = []
    _patch_global_lease(monkeypatch, events=events)
    session = _outer_fence(workspace).begin_session(
        label="M2M OAuth",
        principal_id="principal-id",
        context=_CONTEXT,
    )
    intent = session.persist_intent(before_ids=frozenset({"existing"}))
    intent.observe(
        credential_id="created",
        observed_ids=frozenset({"existing", "created"}),
    )
    intent.arm_sink(
        repository="entrada.test/repo",
        secret_names=frozenset({"CLIENT_ID", "CLIENT_SECRET"}),
        atomic_credential_bundle=True,
    )
    live_ids = ["existing", "created"]
    events.clear()
    _patch_recovery_lease(monkeypatch, events=events)

    with pytest.raises(
        CredentialMutationQuarantineError,
        match="recovery is unproven",
    ):
        recover_oauth_credential_mutation(
            workspace,
            intent_path=intent.path,
            outer_fence=_outer_fence(workspace),
            principal_id="principal-id",
            authority_identity="application-id",
            provider_api="workspace.service_principal_secrets_proxy",
            list_credentials=lambda: (
                SimpleNamespace(id=value) for value in live_ids
            ),
            delete_credential=lambda credential_id: live_ids.remove(credential_id),
            invalidate_sink=lambda _repository, _names: (_ for _ in ()).throw(
                RuntimeError("GitHub list unavailable")
            ),
            sleep=lambda _seconds: None,
        )

    assert live_ids == ["existing"]
    assert "resolver-release" not in events
    with pytest.raises(CredentialMutationQuarantineError, match="unresolved"):
        assert_no_credential_quarantine(workspace, app_name="another-app")


def test_operation_bound_quarantine_clears_only_after_valid_terminal_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    events: list[str] = []
    _patch_global_lease(monkeypatch, events=events)
    session = _outer_fence(workspace).begin_session(
        label="M2M OAuth",
        principal_id="principal-id",
        context=_CONTEXT,
    )
    intent = session.persist_intent(before_ids=frozenset({"existing"}))
    intent.observe(
        credential_id="created",
        observed_ids=frozenset({"existing", "created"}),
    )
    intent.arm_sink(
        repository="entrada.test/repo",
        secret_names=frozenset({"CLIENT_ID", "CLIENT_SECRET"}),
        atomic_credential_bundle=True,
    )
    with pytest.raises(CredentialMutationQuarantineError):
        raise_credential_quarantine(
            message="delivery acknowledgment was interrupted",
            label="M2M OAuth",
            principal_id="principal-id",
            before_ids=frozenset({"existing"}),
            candidate_ids=frozenset({"created"}),
            fence=intent,
        )
    with pytest.raises(CredentialMutationQuarantineError, match="unresolved"):
        assert_no_credential_quarantine(workspace, app_name="alternate-app")

    intent.acknowledge_delivery(
        acknowledged_ids=frozenset({"existing", "created"})
    )
    intent.resolve(
        outcome="delivered",
        final_ids=frozenset({"created"}),
        retained_credential_id="created",
        sink_disposition="acknowledged",
    )

    assert records.unresolved_record_paths(workspace) == ()
    assert events[-1] == "release:mip-oauth-credential-mutations"


def test_environment_lease_binding_requires_complete_exact_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "MIP_APP_NAME",
        "MIP_APP_DEPLOYMENT_LEASE_ID",
        "MIP_DEPLOYMENT_SOURCE_GIT_SHA",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="exact signed App deployment lease"):
        held_deployment_credential_assertion(object())
