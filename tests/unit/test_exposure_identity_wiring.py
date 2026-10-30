"""Each audited identity reaches its own accepted-exposure matcher, and only its own (M2-PR4).

``audit_global_m2m_access --exposure-identity`` picks the matcher. The deploy
passes ``app`` for the App's service principal and ``runtime`` for the
agent-runtime's; the verifier's ``--forbid-all-genie`` audit takes none, and
the CLI refuses the combination. ``app`` is the one identity that may hold an
elevated entry, so the CLI proves the audited principal is the App's own.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from tests.fixtures.deploy_script import deploy_entrypoint_text
from tools.databricks import audit_global_m2m_access as audit

_MODULE = "-m tools.databricks.audit_global_m2m_access"


def _invocations() -> list[str]:
    """Each call's arguments: a backslash-continued command, or an argument array up to ``)``."""

    lines = deploy_entrypoint_text().splitlines()
    bodies: list[str] = []
    for index, line in enumerate(lines):
        if _MODULE not in line:
            continue
        continued = line.rstrip().endswith("\\")
        body: list[str] = []
        for following in lines[index + 1 :]:
            if not continued and following.strip() == ")":
                break
            body.append(following)
            if continued and not following.rstrip().endswith("\\"):
                break
        bodies.append("\n".join(body))
    return bodies


def test_every_global_audit_names_the_identity_it_audits() -> None:
    expected = {
        '"$APP_SP_CLIENT_ID"': "app",
        '"${APP_SP_CLIENT_ID:?App service principal is required}"': "app",
        '"$DATABRICKS_AGENT_RUNTIME_CLIENT_ID"': "runtime",
        '"$DATABRICKS_VERIFIER_CLIENT_ID"': None,
    }
    seen: dict[str | None, int] = {}
    invocations = _invocations()
    assert len(invocations) >= 7, invocations
    for body in invocations:
        principal = re.search(r'--application-id ("[^"]*"|\S+)', body)
        assert principal is not None, body
        identity = re.search(r"--exposure-identity (\w+)", body)
        assert principal.group(1) in expected, body
        want = expected[principal.group(1)]
        assert (identity.group(1) if identity else None) == want, body
        if want is None:
            assert "--forbid-all-genie" in body, body
        seen[want] = seen.get(want, 0) + 1
    assert seen.get("app", 0) >= 3 and seen.get("runtime", 0) >= 2 and seen.get(None, 0) >= 2, seen


def test_forbid_all_genie_rejects_a_matcher() -> None:
    with pytest.raises(SystemExit) as exited:
        audit.main(
            [
                "--app-name", "mip-app",
                "--application-id", "verifier-EXAMPLE",
                "--account-id", "account-EXAMPLE",
                "--expected-inventory-principal", "deployer@example.invalid",
                "--forbid-customer-serving",
                "--forbid-all-genie",
                "--exposure-identity", "app",
            ]
        )  # fmt: skip

    assert exited.value.code == 2


def _workspace(app_client_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        apps=SimpleNamespace(get=lambda _name: SimpleNamespace(service_principal_client_id=app_client_id))
    )


@pytest.mark.parametrize(
    ("identity", "application_id", "refused"),
    [
        ("app", "app-client-EXAMPLE", False),
        ("app", "APP-CLIENT-EXAMPLE", False),
        ("app", "runtime-client-EXAMPLE", True),
        ("runtime", "runtime-client-EXAMPLE", False),
        ("runtime", "app-client-EXAMPLE", True),
        ("proxy", "app-client-EXAMPLE", True),
    ],
)
def test_the_app_identity_is_proven_against_the_app_itself(
    identity: str, application_id: str, refused: bool
) -> None:
    workspace = _workspace("app-client-EXAMPLE")

    if refused:
        with pytest.raises(RuntimeError, match="does not match the audited principal"):
            audit.assert_exposure_identity(
                workspace, identity=identity, app_name="mip-app", application_id=application_id
            )
    else:
        audit.assert_exposure_identity(
            workspace, identity=identity, app_name="mip-app", application_id=application_id
        )


def test_an_app_without_a_service_principal_cannot_be_the_app_identity() -> None:
    with pytest.raises(RuntimeError, match="not the App's service principal"):
        audit.assert_exposure_identity(
            _workspace(""), identity="app", app_name="mip-app", application_id="app-client-EXAMPLE"
        )
