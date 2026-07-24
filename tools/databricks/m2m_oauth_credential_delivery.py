"""Fail-closed OAuth credential inventory, compensation, and sink delivery."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tools.databricks.agent_proxy_credential_bundle import (
    canonical_agent_proxy_credential_bundle,
)

ErrorFactory = Callable[..., BaseException]
SecretWriter = Callable[[str, str, str], None]


def oauth_credential_ids(
    client: Any,
    *,
    sp_id: str,
    error_factory: ErrorFactory,
) -> set[str]:
    try:
        credentials = list(client.service_principal_secrets_proxy.list(sp_id))
    except Exception as exc:  # noqa: BLE001
        raise error_factory(exc, step="list OAuth credentials") from exc
    values = [str(getattr(item, "id", "") or "").strip() for item in credentials]
    if any(not value for value in values) or len(values) != len(set(values)):
        raise SystemExit("OAuth credential inventory is malformed")
    return set(values)


def revoke_oauth_secret(
    client: Any,
    *,
    sp_id: str,
    credential_id: str,
    error_factory: ErrorFactory,
) -> None:
    try:
        client.service_principal_secrets_proxy.delete(sp_id, credential_id)
        remaining = oauth_credential_ids(
            client,
            sp_id=sp_id,
            error_factory=error_factory,
        )
    except SystemExit:
        raise
    except BaseException as exc:
        if isinstance(exc, Exception):
            raise error_factory(
                exc,
                step="revoke undelivered OAuth secret after credential-sink failure",
            ) from exc
        raise
    if credential_id in remaining:
        raise SystemExit(
            "Could not prove that the undelivered OAuth credential was revoked; "
            "quarantine the service principal before retrying."
        )


def deliver_oauth_credential(
    *,
    writer: SecretWriter,
    revoker: Callable[..., None],
    gh_repo: str,
    client_id_secret_name: str,
    client_id: str,
    client_secret_secret_name: str,
    client_secret: str,
    credential_id: str,
    credential_id_secret_name: str | None,
    app_url_secret_name: str | None,
    app_url: str | None,
    atomic_credential_bundle: bool = False,
) -> None:
    """Publish usable material last and compensate every partial sink failure."""

    try:
        if atomic_credential_bundle:
            writer(
                gh_repo,
                client_secret_secret_name,
                canonical_agent_proxy_credential_bundle(
                    client_id=client_id,
                    credential_id=credential_id,
                    client_secret=client_secret,
                ),
            )
        else:
            writer(gh_repo, client_id_secret_name, client_id)
            if credential_id_secret_name:
                writer(gh_repo, credential_id_secret_name, credential_id)
            if app_url_secret_name:
                if app_url is None:
                    raise AssertionError("resolved App URL is required")
                writer(gh_repo, app_url_secret_name, app_url)
            writer(gh_repo, client_secret_secret_name, client_secret)
    except BaseException:
        revoker(credential_id=credential_id)
        raise
