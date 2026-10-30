"""Where the account bootstrap hands what it creates: the credential sink.

The bootstrap produces two things the deploy needs and the repository must
never hold (plan §4.1):

* the identity pins, ``MIP_IDENTITY_IDS_JSON`` (identifying), after every
  create or adopt;
* a freshly minted M2M secret (secret), during ``--rotate-adopted``.

Both go to a ``CredentialSink`` and nowhere else: never stdout, stderr, a
log, the plan or an exception message. A secret travels as a
``pydantic.SecretStr``, whose ``repr`` and ``str`` are redacted, and only
``put_secret`` may reveal it.

The GitHub-environment sink (``<client>`` environment secrets through a
temporary ``GH_CONFIG_DIR``) is M2-PR2. Until it lands the default sink is
``UnconfiguredSink``, which refuses before the first mutation, so a real
``--apply`` that would create, adopt or mint stops with nothing changed.

``CredentialProbe`` proves a new secret authenticates before any older one
is revoked. The default exchanges it for an account-level OAuth token with
the client-credentials grant and keeps nothing but the yes/no answer.
"""

from __future__ import annotations

import base64
import json
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Protocol

from pydantic import SecretStr

from tools.clients.errors import BootstrapRefusal

_PROBE_TIMEOUT_SECONDS = 30


class CredentialSink(Protocol):
    """The one place a pin map or a minted secret may go."""

    def ready(self) -> None:
        """Raise ``BootstrapRefusal`` when the sink cannot accept values."""

    def put_identity_ids(self, pins: Mapping[str, str]) -> None:
        """Store the whole pin map (``MIP_IDENTITY_IDS_JSON``), replacing the old one."""

    def put_secret(self, field: str, application_id: str, secret: SecretStr) -> None:
        """Store ``secret`` as the profile secret ``field`` for that identity."""

    def confirm_secret(self, field: str) -> bool:
        """``True`` once the sink holds a value for ``field`` stored by this run."""


class UnconfiguredSink:
    """The default until M2-PR2: refuses, so nothing is created that cannot be pinned."""

    def ready(self) -> None:
        raise BootstrapRefusal(
            [
                "credential sink: none is configured; the GitHub-environment sink lands "
                "in M2-PR2, and until then a run that would create, adopt or mint refuses "
                "before its first mutation"
            ]
        )

    def put_identity_ids(self, pins: Mapping[str, str]) -> None:
        self.ready()

    def put_secret(self, field: str, application_id: str, secret: SecretStr) -> None:
        self.ready()

    def confirm_secret(self, field: str) -> bool:
        self.ready()
        return False


CredentialProbe = Callable[[str, SecretStr], bool]


def account_token_probe(
    account_host: str,
    account_id: str,
    *,
    opener: Callable[..., object] = urllib.request.urlopen,
) -> CredentialProbe:
    """A probe that asks the account OIDC endpoint for a client-credentials token.

    It returns only whether a token came back. Every failure, including one
    whose message would echo the request, becomes ``False``.
    """

    endpoint = f"{account_host.rstrip('/')}/oidc/accounts/{urllib.parse.quote(account_id)}/v1/token"

    def probe(application_id: str, secret: SecretStr) -> bool:
        basic = base64.b64encode(f"{application_id}:{secret.get_secret_value()}".encode()).decode()
        request = urllib.request.Request(
            endpoint,
            data=urllib.parse.urlencode({"grant_type": "client_credentials", "scope": "all-apis"}).encode(),
            headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with opener(request, timeout=_PROBE_TIMEOUT_SECONDS) as response:  # type: ignore[attr-defined]
                status = getattr(response, "status", 0)
                body = json.loads(response.read() or b"{}")
        except Exception:  # noqa: BLE001 - the answer is yes or no, never the reason
            return False
        return status == 200 and isinstance(body, dict) and bool(body.get("access_token"))

    return probe
