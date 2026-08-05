"""New-credential propagation settle window for the identity-membership probe.

A freshly minted service-principal secret is eventually consistent at the
account token endpoint, so the first authentication can be rejected with
`invalid_client` for a few seconds. Deploy runs on 2026-08-01/03 failed at
step 4 roughly half the time for exactly this reason. The settle window must
absorb that narrow case WITHOUT becoming an authorization fallback.
"""

from __future__ import annotations

import pytest

from tools.databricks.converge_campaign_treatment_access import (
    read_identity_with_credential_settle,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_returns_identity_on_first_success() -> None:
    clock = _Clock()

    result = read_identity_with_credential_settle(
        lambda: {"id": "sp-1"},
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    assert result == {"id": "sp-1"}
    assert clock.slept == []


@pytest.mark.parametrize(
    "code",
    ["invalid_client", "invalid_grant", "unauthenticated", "unauthorized_client"],
)
def test_absorbs_propagation_rejection_then_succeeds(code: str) -> None:
    clock = _Clock()
    attempts: list[int] = []

    def read_identity() -> dict[str, str]:
        attempts.append(len(attempts))
        if len(attempts) < 3:
            raise ValueError(f"{code}: Client authentication failed")
        return {"id": "sp-1"}

    result = read_identity_with_credential_settle(
        read_identity,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    assert result == {"id": "sp-1"}
    assert len(attempts) == 3
    assert clock.slept == [5.0, 5.0]


def test_non_auth_errors_propagate_immediately() -> None:
    clock = _Clock()

    def read_identity() -> dict[str, str]:
        raise RuntimeError("Target identity membership proof returned a malformed identity")

    with pytest.raises(RuntimeError, match="malformed identity"):
        read_identity_with_credential_settle(
            read_identity,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )

    assert clock.slept == []


def test_persistent_rejection_still_fails_at_the_deadline() -> None:
    """The window must never become an authorization fallback."""
    clock = _Clock()
    attempts: list[int] = []

    def read_identity() -> dict[str, str]:
        attempts.append(len(attempts))
        raise ValueError("invalid_client: Client authentication failed")

    with pytest.raises(ValueError, match="invalid_client"):
        read_identity_with_credential_settle(
            read_identity,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )

    # Bounded: 90s deadline at a 5s interval, then the rejection propagates.
    assert clock.now >= 90.0
    assert len(attempts) == 19


class _FakeConfig:
    def __init__(self, client_id: str, auth_type: str, host: str) -> None:
        self.client_id = client_id
        self.auth_type = auth_type
        self.host = host


class _FakeWorkspace:
    def __init__(self, client_id: str, auth_type: str = "oauth-m2m") -> None:
        self.config = _FakeConfig(client_id, auth_type, "https://ws.example.com")


def test_isolated_auth_env_strips_and_restores() -> None:
    """Ambient deployer credentials must not be visible to the target probe."""
    import os

    from tools.databricks.converge_campaign_treatment_access import (
        _AMBIENT_AUTH_ENV_VARS,
        isolated_target_auth_env,
    )

    os.environ["DATABRICKS_TOKEN"] = "ambient-deployer-token"
    os.environ["DATABRICKS_AUTH_TYPE"] = "pat"
    try:
        with isolated_target_auth_env() as stripped:
            assert "DATABRICKS_TOKEN" in stripped
            assert not [n for n in _AMBIENT_AUTH_ENV_VARS if n in os.environ]
        assert os.environ["DATABRICKS_TOKEN"] == "ambient-deployer-token"
        assert os.environ["DATABRICKS_AUTH_TYPE"] == "pat"
    finally:
        os.environ.pop("DATABRICKS_TOKEN", None)
        os.environ.pop("DATABRICKS_AUTH_TYPE", None)


def test_isolated_auth_env_restores_on_exception() -> None:
    import os

    from tools.databricks.converge_campaign_treatment_access import isolated_target_auth_env

    os.environ["DATABRICKS_TOKEN"] = "ambient"
    try:
        with pytest.raises(RuntimeError), isolated_target_auth_env():
            raise RuntimeError("probe blew up")
        assert os.environ["DATABRICKS_TOKEN"] == "ambient"
    finally:
        os.environ.pop("DATABRICKS_TOKEN", None)


def test_probe_failure_description_is_secret_free_and_actionable() -> None:
    from tools.databricks.converge_campaign_treatment_access import _describe_probe_failure

    text = _describe_probe_failure(
        ValueError("invalid_client: Client authentication failed"),
        workspace=_FakeWorkspace(client_id="wrong-client-id"),
        application_id="intended-app-id",
        host="https://ws.example.com",
        stripped_env=("DATABRICKS_TOKEN",),
    )

    assert "intended_client_id=intended-app-id" in text
    assert "resolved_client_id=wrong-client-id" in text
    assert "client_id_matches=false" in text
    assert "resolved_auth_type=oauth-m2m" in text
    assert "ambient_auth_env_removed=DATABRICKS_TOKEN" in text
    # No secret material may appear in a CI log line.
    assert "secret" not in text.lower()


def test_transient_instability_markers_are_recognized() -> None:
    from tools.databricks.converge_campaign_treatment_access import (
        _is_transient_credential_instability,
    )

    assert _is_transient_credential_instability(
        RuntimeError("temporary target identity credential create response is incomplete or ambiguous")
    )
    assert _is_transient_credential_instability(
        RuntimeError("temporary target identity credential inventory did not become stable")
    )
    # A real authorization or identity failure must NOT be retried.
    assert not _is_transient_credential_instability(
        RuntimeError("Temporary credential authenticated as a different target identity")
    )
    assert not _is_transient_credential_instability(
        ValueError("invalid_client: Client authentication failed")
    )
