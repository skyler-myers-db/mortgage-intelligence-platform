from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.databricks import oauth_credential_recovery_cli as cli

_INTENT_PATH = (
    "/.mip-deployment-leases/mip-oauth-credential-mutations."
    "11111111-1111-4111-8111-111111111111."
    "11111111-1111-4111-8111-111111111111."
    "oauth-credential-intent.json"
)
_INTENT = {
    "version": 4,
    "outer_app_name": "mip-app",
    "source_git_sha": "a" * 40,
    "label": "M2M OAuth",
    "principal_id": "principal-id",
    "authority_scope": "workspace",
    "authority_identity": "application-id",
    "provider_api": "workspace.service_principal_secrets_proxy",
    "operation_mode": "persistent_delivery",
    "sink_descriptor": "github:owner/repo:atomic=false:A,B",
    "sink_repository": "owner/repo",
    "sink_secret_names": ["A", "B"],
    "sink_atomic_credential_bundle": False,
    "retirement_mode": "immediate",
    "credential_lifetime_seconds": 0,
    "before_credential_ids": ["existing"],
}


def _patch_intent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli.records,
        "read_json",
        lambda _workspace, path: (
            dict(_INTENT),
            b"signed",
        )
        if path == _INTENT_PATH
        else pytest.fail("unexpected path"),
    )
    monkeypatch.setattr(
        cli.records,
        "validate_intent",
        lambda path, _intent: None
        if path == _INTENT_PATH
        else pytest.fail("unexpected path"),
    )


def test_inspect_prints_only_non_secret_signed_recovery_coordinates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_intent(monkeypatch)

    result = cli.execute(
        ["inspect", "--intent-path", _INTENT_PATH],
        workspace_factory=lambda: object(),
    )

    assert result["principal_id"] == "principal-id"
    assert result["authority_identity"] == "application-id"
    assert "secret" not in result


def test_inspect_orphan_lease_prints_only_reviewed_recovery_coordinates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = object()
    monkeypatch.setattr(
        cli,
        "orphan_credential_mutation_lease_coordinates",
        lambda observed: SimpleNamespace(
            lease_id="lease-id",
            recovery_root_lease_id="recovery-root-id",
            source_git_sha="a" * 40,
            expected_intent_path="/signed/expected-intent.json",
            intent_present=False,
        )
        if observed is workspace
        else pytest.fail("unexpected workspace"),
    )

    result = cli.execute(
        ["inspect-orphan-lease"],
        workspace_factory=lambda: workspace,
    )

    assert result == {
        "lease_id": "lease-id",
        "recovery_root_lease_id": "recovery-root-id",
        "source_git_sha": "a" * 40,
        "expected_intent_path": "/signed/expected-intent.json",
        "intent_present": False,
    }


def test_recover_orphan_lease_requires_exact_confirmations_and_outer_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = object()
    fence = object()
    monkeypatch.setattr(
        cli,
        "held_deployment_credential_assertion",
        lambda observed: fence
        if observed is workspace
        else pytest.fail("unexpected workspace"),
    )

    def recover(observed: object, **kwargs: object) -> object:
        assert observed is workspace
        assert kwargs == {
            "outer_fence": fence,
            "expected_lease_id": "lease-id",
            "expected_recovery_root_lease_id": "recovery-root-id",
        }
        return SimpleNamespace(
            lease_id="lease-id",
            recovery_root_lease_id="recovery-root-id",
            expected_intent_path="/signed/expected-intent.json",
        )

    monkeypatch.setattr(
        cli,
        "recover_orphan_credential_mutation_lease",
        recover,
    )

    result = cli.execute(
        [
            "recover-orphan-lease",
            "--confirm-lease-id",
            "lease-id",
            "--confirm-recovery-root-lease-id",
            "recovery-root-id",
        ],
        workspace_factory=lambda: workspace,
    )

    assert result == {
        "lease_id": "lease-id",
        "recovery_root_lease_id": "recovery-root-id",
        "expected_intent_path": "/signed/expected-intent.json",
        "status": "released_without_intent",
    }


@pytest.mark.parametrize("outcome", ["restored", "delivered"])
def test_workspace_recovery_reports_exact_signed_terminal_outcome(
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    _patch_intent(monkeypatch)
    deleted: list[tuple[str, str]] = []
    credentials = SimpleNamespace(
        list=lambda principal_id: [SimpleNamespace(id=f"{principal_id}-credential")],
        delete=lambda principal_id, credential_id: deleted.append(
            (principal_id, credential_id)
        ),
    )
    workspace = SimpleNamespace(
        service_principals=SimpleNamespace(
            get=lambda principal_id: SimpleNamespace(
                id=principal_id,
                application_id="application-id",
            )
        ),
        service_principal_secrets_proxy=credentials,
    )
    fence = object()
    monkeypatch.setattr(
        cli,
        "held_deployment_credential_recovery_assertion",
        lambda observed, *, intent_path: fence
        if (observed, intent_path) == (workspace, _INTENT_PATH)
        else pytest.fail("unexpected recovery boundary"),
    )

    def recover(observed: object, **kwargs: object) -> object:
        assert observed is workspace
        assert kwargs["outer_fence"] is fence
        assert kwargs["principal_id"] == "principal-id"
        assert [
            item.id for item in kwargs["list_credentials"]()  # type: ignore[operator]
        ] == ["principal-id-credential"]
        if outcome == "restored":
            kwargs["delete_credential"]("created")  # type: ignore[operator]
        return SimpleNamespace(
            intent_path=_INTENT_PATH,
            principal_id="principal-id",
            outcome=outcome,
            revoked_credential_id="created" if outcome == "restored" else "",
            sink_disposition=(
                "invalidated" if outcome == "restored" else "acknowledged"
            ),
        )

    monkeypatch.setattr(cli, "recover_oauth_credential_mutation", recover)

    result = cli.execute(
        [
            "recover",
            "--intent-path",
            _INTENT_PATH,
            "--confirm-principal-id",
            "principal-id",
            "--confirm-authority-identity",
            "application-id",
            "--confirm-provider-api",
            "workspace.service_principal_secrets_proxy",
        ],
        workspace_factory=lambda: workspace,
        account_factory=lambda: pytest.fail("account provider was used"),
    )

    assert result["status"] == outcome
    assert deleted == (
        [("principal-id", "created")] if outcome == "restored" else []
    )


def test_recovery_confirmation_mismatch_precedes_any_provider_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_intent(monkeypatch)

    with pytest.raises(RuntimeError, match="confirmations do not match"):
        cli.execute(
            [
                "recover",
                "--intent-path",
                _INTENT_PATH,
                "--confirm-principal-id",
                "different-principal",
                "--confirm-authority-identity",
                "application-id",
                "--confirm-provider-api",
                "workspace.service_principal_secrets_proxy",
            ],
            workspace_factory=lambda: object(),
            account_factory=lambda: pytest.fail("provider must not be used"),
        )
