"""Stdlib-only Salesforce REST client for the activation-delivery adapter.

Feature B: the activation outbox (``mip_app.activation_outbox``) stages
approved borrower outreach for customer destinations. This module is the
HTTP seam that actually delivers a ``salesforce`` destination to a real
Salesforce org by creating an sObject (default ``Task``) via the REST API.

Honesty posture (commercial product):

- The client is REAL HTTP. It is constructed only when every required
  credential is present (``settings.salesforce_configured``). When unset,
  ``get_salesforce_client()`` returns ``None`` and the delivery adapter
  takes a clean no-op path that records
  ``{delivered: false, reason: "salesforce_not_configured"}``. We NEVER
  mark a row delivered without a real ``201 Created`` from Salesforce.

Design notes:

- ``urllib`` only, mirroring ``backend.services.genie_client`` so
  ``app.yaml`` doesn't pull a new wheel into the Databricks Apps
  serverless runtime. JSON is parsed with the stdlib.
- Auth is the OAuth 2.0 **username-password** flow: POST
  ``{instance_url}/services/oauth2/token`` with ``grant_type=password``.
  This is the minimal flow that exercises a real token exchange without a
  signing key. PRODUCTION SHOULD USE THE JWT BEARER FLOW instead -- it
  keeps the user password and security token off the wire and supports
  per-user impersonation. The token is cached in a short-TTL cache and
  force-refreshed on a 401.
- HTTP calls are wrapped in ``Resilient`` (breaker + retry) keyed
  ``"salesforce"`` so ``/api/health`` and the resilience telemetry see the
  dependency alongside warehouse/lakebase/genie.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from threading import Lock
from typing import Any

from backend.services.observability import emit
from backend.services.resilience import Resilient, TTLCache, get_breaker

log = logging.getLogger(__name__)

# OAuth tokens from the username-password flow do not carry a documented
# TTL in the response; Salesforce session lifetimes are org-configurable
# (default ~2h). We cache conservatively and refresh on 401, so a short
# TTL just bounds how long a revoked session lingers in-process.
_TOKEN_TTL_S: float = 1800.0
_TOKEN_CACHE_KEY = "salesforce_access_token"


class SalesforceClientError(RuntimeError):
    """Raised when a Salesforce REST call returns a non-success response.

    ``status_code`` and ``sf_error_code`` are surfaced verbatim so the
    breaker telemetry and the delivery adapter's ``delivery_metadata`` can
    record operator-facing context. ``is_auth_error`` flags the 401 case
    the create path retries once after a token refresh.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        sf_error_code: str | None = None,
        is_auth_error: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.sf_error_code = sf_error_code
        self.is_auth_error = is_auth_error


class SalesforceClient:
    """Stdlib-only Salesforce REST client.

    ``create_record(sobject, fields)`` drives the
    ``authenticate -> POST sobjects/{sobject}`` sequence, refreshing the
    cached OAuth token once on a 401. The access token is cached in a
    process-local :class:`TTLCache`.
    """

    def __init__(
        self,
        *,
        instance_url: str,
        client_id: str,
        client_secret: str,
        username: str,
        password: str,
        security_token: str = "",
        api_version: str = "v60.0",
        timeout_s: float = 10.0,
        token_cache: TTLCache | None = None,
    ) -> None:
        if not instance_url.startswith("http"):
            instance_url = "https://" + instance_url
        self._instance_url = instance_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._username = username
        self._password = password
        self._security_token = security_token or ""
        self._api_version = api_version
        self._timeout_s = timeout_s
        self._token_cache = token_cache if token_cache is not None else TTLCache()
        self._token_lock = Lock()

    @property
    def instance_url(self) -> str:
        return self._instance_url

    # ------------------------------------------------------------------
    # OAuth
    # ------------------------------------------------------------------

    def _access_token(self, *, force_refresh: bool = False) -> str:
        if not force_refresh:
            cached = self._token_cache.get(_TOKEN_CACHE_KEY)
            if cached:
                return str(cached)
        with self._token_lock:
            # Double-check under the lock so a burst of callers mints once.
            if not force_refresh:
                cached = self._token_cache.get(_TOKEN_CACHE_KEY)
                if cached:
                    return str(cached)
            token = self._request_token()
            self._token_cache.set(_TOKEN_CACHE_KEY, token, _TOKEN_TTL_S)
            return token

    def _request_token(self) -> str:
        url = f"{self._instance_url}/services/oauth2/token"
        form = {
            "grant_type": "password",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "username": self._username,
            # Salesforce requires password + security token concatenated.
            "password": f"{self._password}{self._security_token}",
        }
        body = urllib.parse.urlencode(form).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp = self._send(req)
        token = resp.get("access_token")
        if not token:
            raise SalesforceClientError(
                "Salesforce OAuth response missing access_token",
                is_auth_error=True,
            )
        # Salesforce returns the per-session instance_url; honour it so a
        # My Domain / sandbox login host that differs from the data host
        # still targets the right endpoint.
        returned_instance = resp.get("instance_url")
        if returned_instance:
            self._instance_url = str(returned_instance).rstrip("/")
        return str(token)

    # ------------------------------------------------------------------
    # Records
    # ------------------------------------------------------------------

    def create_record(self, sobject: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Create an sObject and return ``{"id": ..., "success": True}``.

        Refreshes the OAuth token once and retries the create on a 401, so
        a session that expired between cache writes self-heals without a
        bubbled auth error.
        """
        try:
            return self._create_once(sobject, fields, force_token_refresh=False)
        except SalesforceClientError as exc:
            if not exc.is_auth_error:
                raise
            emit(
                log,
                "salesforce_token_refresh",
                level=logging.WARNING,
                dependency="salesforce",
                reason="401_on_create",
            )
            return self._create_once(sobject, fields, force_token_refresh=True)

    def _create_once(
        self,
        sobject: str,
        fields: dict[str, Any],
        *,
        force_token_refresh: bool,
    ) -> dict[str, Any]:
        token = self._access_token(force_refresh=force_token_refresh)
        url = (
            f"{self._instance_url}/services/data/{self._api_version}"
            f"/sobjects/{urllib.parse.quote(sobject)}"
        )
        payload = json.dumps(fields).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        resp = self._send(req)
        record_id = resp.get("id")
        if not record_id or resp.get("success") is False:
            # The POST returned 2xx but no usable record id -- a malformed/
            # unexpected success body, not a real create. Report it as an
            # upstream-response fault (502), not a 201, so breaker telemetry
            # isn't misled into counting it as a success.
            raise SalesforceClientError(
                f"Salesforce create returned no id / success=false: {resp}",
                status_code=502,
            )
        return {"id": str(record_id), "success": True}

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _send(self, req: urllib.request.Request) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            sf_code = _parse_sf_error_code(detail)
            raise SalesforceClientError(
                f"HTTP {exc.code} from Salesforce API: {detail[:500]}",
                status_code=exc.code,
                sf_error_code=sf_code,
                is_auth_error=exc.code == 401,
            ) from exc
        except urllib.error.URLError as exc:
            raise SalesforceClientError(
                f"Network error reaching Salesforce API: {exc.reason!r}",
            ) from exc
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SalesforceClientError(
                f"Malformed JSON from Salesforce API (first 200 chars): {raw[:200]!r}",
            ) from exc
        # Salesforce wraps some success bodies in a single-element list.
        if isinstance(parsed, list) and parsed:
            first = parsed[0]
            if isinstance(first, dict):
                return first
        if isinstance(parsed, dict):
            return parsed
        return {}


def _parse_sf_error_code(detail: str) -> str | None:
    """Best-effort extraction of the Salesforce errorCode from a body.

    Salesforce errors come back as ``[{"message": ..., "errorCode": ...}]``
    for the REST data API and ``{"error": ..., "error_description": ...}``
    for OAuth. We never raise from here -- a missing code is just ``None``.
    """
    try:
        parsed = json.loads(detail)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
        code = parsed[0].get("errorCode")
        return str(code) if code else None
    if isinstance(parsed, dict):
        code = parsed.get("errorCode") or parsed.get("error")
        return str(code) if code else None
    return None


class ResilientSalesforceClient:
    """Compose ``Resilient`` (breaker) around ``create_record``.

    The breaker key is ``"salesforce"`` so the dependency shows up in the
    resilience registry. The write is NOT auto-retried (attempts=1): a
    ``create_record`` is a non-idempotent POST, and Salesforce is not given an
    idempotency key here (``request_id`` is only free text in the Task
    Description, NOT an External-Id upsert), so retrying a create that may have
    committed server-side would risk a duplicate Task. Re-delivery is instead
    bounded by the breaker and by the UNIQUE ``request_id`` on
    ``activation_outbox`` (which dedupes re-staging). Production should migrate
    to an External-Id upsert to make the write itself idempotent.
    """

    def __init__(self, client: SalesforceClient, resilient: Resilient[Any]) -> None:
        self._client = client
        self._resilient = resilient

    @property
    def instance_url(self) -> str:
        return self._client.instance_url

    @property
    def resilient(self) -> Resilient[Any]:
        return self._resilient

    def create_record(self, sobject: str, fields: dict[str, Any]) -> dict[str, Any]:
        return self._resilient.call(lambda: self._client.create_record(sobject, fields))


_CLIENT: ResilientSalesforceClient | None = None
_CLIENT_LOCK = Lock()


def get_salesforce_client() -> ResilientSalesforceClient | None:
    """Return the process-wide Salesforce client, or ``None`` when unconfigured.

    Returning ``None`` is the honest degraded path: the delivery adapter
    records ``salesforce_not_configured`` and leaves the outbox row staged.
    Constructed lazily so test processes that never configure Salesforce
    never build a client and never need real credentials.
    """
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    from backend.config.settings import settings

    if not settings.salesforce_configured:
        return None

    with _CLIENT_LOCK:
        if _CLIENT is not None:
            return _CLIENT
        # mypy: salesforce_configured guarantees these are non-None.
        bare = SalesforceClient(
            instance_url=str(settings.salesforce_instance_url),
            client_id=str(settings.salesforce_client_id),
            client_secret=settings.salesforce_client_secret.get_secret_value(),  # type: ignore[union-attr]
            username=str(settings.salesforce_username),
            password=settings.salesforce_password.get_secret_value(),  # type: ignore[union-attr]
            security_token=(
                settings.salesforce_security_token.get_secret_value()
                if settings.salesforce_security_token is not None
                else ""
            ),
            api_version=settings.salesforce_api_version,
            timeout_s=settings.salesforce_timeout_s,
        )
        breaker = get_breaker("salesforce", failure_threshold=3, cooldown_s=30.0)
        # attempts=1: a create_record is a NON-IDEMPOTENT write. A create that
        # commits server-side but then fails the client read (timeout/5xx) would,
        # on retry, double-write a Task -- Salesforce has no idempotency key here
        # and request_id is only free text in the Description. So we do NOT retry
        # the write; the breaker still trips on repeated failures, and re-staging
        # is deduped by the UNIQUE request_id on activation_outbox. (The single
        # controlled 401 token-refresh lives inside create_record and is safe: a
        # 401 means the write never happened.)
        resilient = Resilient[Any](
            breaker=breaker,
            dependency_name="salesforce",
            attempts=1,
            retry_on=(SalesforceClientError, OSError),
        )
        _CLIENT = ResilientSalesforceClient(bare, resilient)
        return _CLIENT


def _reset_salesforce_client_for_tests() -> None:
    """Test helper -- drops the singleton so a fresh stub installs cleanly."""
    global _CLIENT
    with _CLIENT_LOCK:
        _CLIENT = None


__all__ = [
    "ResilientSalesforceClient",
    "SalesforceClient",
    "SalesforceClientError",
    "get_salesforce_client",
]
