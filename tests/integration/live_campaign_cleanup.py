"""Governed teardown for durable campaigns created by live release drills."""

from __future__ import annotations

import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from threading import Lock
from typing import Any

Request = Callable[..., tuple[int, object]]
_LEASE_RECOVERY_ATTEMPTS = 75
_LEASE_RECOVERY_INTERVAL_SECONDS = 5.0


@dataclass
class _CampaignCreateAttempt:
    payload: dict[str, object]
    idempotency_key: str
    token: str
    campaign_id: str | None = None
    resolved_without_campaign: bool = False


class CampaignFixtureTracker:
    """Reconcile every attempted create, including commit-then-timeout."""

    def __init__(self, *, default_token: str) -> None:
        if not default_token:
            raise RuntimeError("live campaign tracker requires an operator token")
        self._default_token = default_token
        self._attempts: dict[str, _CampaignCreateAttempt] = {}
        self._conflict_probes: list[_CampaignCreateAttempt] = []
        self._lock = Lock()

    def request(
        self,
        request: Request,
        *args: object,
        **kwargs: object,
    ) -> tuple[int, object]:
        """Capture durable replay authority before issuing a create request."""

        method = str(args[0]) if args else str(kwargs.get("method") or "")
        path = str(args[1]) if len(args) > 1 else str(kwargs.get("path") or "")
        attempt: _CampaignCreateAttempt | None = None
        if method == "POST" and path == "/api/portfolio/create":
            payload: Any = args[2] if len(args) > 2 else kwargs.get("payload")
            idempotency_key = str(kwargs.get("idempotency_key") or "").strip()
            token = str(kwargs.get("token") or self._default_token).strip()
            if not isinstance(payload, dict) or not idempotency_key or not token:
                raise RuntimeError(
                    "live campaign create requires payload, idempotency key, and actor token"
                )
            with self._lock:
                attempt = self._attempts.get(idempotency_key)
                if attempt is None:
                    attempt = _CampaignCreateAttempt(
                        payload=deepcopy(payload),
                        idempotency_key=idempotency_key,
                        token=token,
                    )
                    self._attempts[idempotency_key] = attempt
                elif attempt.payload != payload or attempt.token != token:
                    raise RuntimeError(
                        "live campaign idempotency key was reused for a different intent"
                    )

        result = request(*args, **kwargs)
        if attempt is not None:
            status, body = result
            campaign_id = body.get("campaign_id") if isinstance(body, dict) else None
            if status == 200 and isinstance(campaign_id, str) and campaign_id:
                with self._lock:
                    attempt.campaign_id = campaign_id
        return result

    def conflict_probe(
        self,
        request: Request,
        *args: object,
        **kwargs: object,
    ) -> tuple[int, object]:
        """Send one intentional idempotency conflict without replacing cleanup authority."""

        method = str(args[0]) if args else str(kwargs.get("method") or "")
        path = str(args[1]) if len(args) > 1 else str(kwargs.get("path") or "")
        payload: Any = args[2] if len(args) > 2 else kwargs.get("payload")
        idempotency_key = str(kwargs.get("idempotency_key") or "").strip()
        token = str(kwargs.get("token") or self._default_token).strip()
        with self._lock:
            attempt = self._attempts.get(idempotency_key)
            valid_conflict = (
                method == "POST"
                and path == "/api/portfolio/create"
                and isinstance(payload, dict)
                and attempt is not None
                and attempt.campaign_id is not None
                and attempt.token == token
                and attempt.payload != payload
            )
        if not valid_conflict:
            raise RuntimeError(
                "live campaign conflict probe requires a captured, different intent"
            )
        probe = _CampaignCreateAttempt(
            payload=deepcopy(payload),
            idempotency_key=idempotency_key,
            token=token,
        )
        with self._lock:
            self._conflict_probes.append(probe)
        result = request(*args, **kwargs)
        status, body = result
        campaign_id = body.get("campaign_id") if isinstance(body, dict) else None
        with self._lock:
            if _is_payload_conflict(status, body):
                probe.resolved_without_campaign = True
            elif status == 200 and isinstance(campaign_id, str) and campaign_id:
                probe.campaign_id = campaign_id
        return result

    def cleanup(
        self,
        request: Request,
        *,
        admin_token: str,
        attempts: int = _LEASE_RECOVERY_ATTEMPTS,
        retry_interval_seconds: float = _LEASE_RECOVERY_INTERVAL_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Replay ambiguous creates, then archive every resolved campaign."""

        with self._lock:
            create_attempts = list(self._attempts.values())
            conflict_probes = list(self._conflict_probes)
        failures: list[str] = []
        archived_ids: set[str] = set()
        cleanup_attempts = [
            ("conflict", attempt) for attempt in reversed(conflict_probes)
        ] + [("create", attempt) for attempt in reversed(create_attempts)]
        for kind, attempt in cleanup_attempts:
            try:
                if attempt.resolved_without_campaign:
                    continue
                campaign_id = attempt.campaign_id
                if campaign_id is None and kind == "conflict":
                    campaign_id = reconcile_campaign_conflict_probe(
                        request,
                        payload=attempt.payload,
                        idempotency_key=attempt.idempotency_key,
                        token=attempt.token,
                        attempts=attempts,
                        retry_interval_seconds=retry_interval_seconds,
                        sleep=sleep,
                    )
                    if campaign_id is None:
                        continue
                if campaign_id is None:
                    campaign_id = reconcile_campaign_fixture(
                        request,
                        payload=attempt.payload,
                        idempotency_key=attempt.idempotency_key,
                        token=attempt.token,
                        attempts=attempts,
                        retry_interval_seconds=retry_interval_seconds,
                        sleep=sleep,
                    )
                if campaign_id in archived_ids:
                    continue
                archive_campaign_fixture(
                    request,
                    campaign_id=campaign_id,
                    admin_token=admin_token,
                    attempts=attempts,
                    retry_interval_seconds=retry_interval_seconds,
                    sleep=sleep,
                )
                archived_ids.add(campaign_id)
            except Exception as exc:  # noqa: BLE001 - report every leaked fixture risk
                failures.append(
                    f"{kind}:{attempt.idempotency_key}: {type(exc).__name__}: {exc}"
                )
        if failures:
            raise AssertionError(f"governed campaign cleanup failed: {failures!r}")


def reconcile_campaign_fixture(
    request: Request,
    *,
    payload: dict[str, object],
    idempotency_key: str,
    token: str,
    attempts: int = _LEASE_RECOVERY_ATTEMPTS,
    retry_interval_seconds: float = _LEASE_RECOVERY_INTERVAL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Recover the server-owned ID by replaying the exact create intent."""

    if (
        not payload
        or not idempotency_key
        or not token
        or attempts < 1
        or retry_interval_seconds < 0
    ):
        raise RuntimeError("live campaign reconciliation authority is incomplete")
    last_result: object = None
    for _attempt in range(attempts):
        try:
            status, body = request(
                "POST",
                "/api/portfolio/create",
                payload,
                token=token,
                idempotency_key=idempotency_key,
            )
        except Exception as exc:  # noqa: BLE001 - retry ambiguous transport outcomes
            last_result = f"{type(exc).__name__}: {exc}"
            if _attempt + 1 < attempts:
                sleep(retry_interval_seconds)
            continue
        last_result = (status, body)
        campaign_id = body.get("campaign_id") if isinstance(body, dict) else None
        if status == 200 and isinstance(campaign_id, str) and campaign_id:
            return campaign_id
        if _attempt + 1 < attempts:
            sleep(retry_interval_seconds)
    raise AssertionError(
        "governed campaign create reconciliation failed for "
        f"{idempotency_key}: {last_result!r}"
    )


def reconcile_campaign_conflict_probe(
    request: Request,
    *,
    payload: dict[str, object],
    idempotency_key: str,
    token: str,
    attempts: int = _LEASE_RECOVERY_ATTEMPTS,
    retry_interval_seconds: float = _LEASE_RECOVERY_INTERVAL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> str | None:
    """Resolve an ambiguous negative probe to either no write or an exact ID."""

    if (
        not payload
        or not idempotency_key
        or not token
        or attempts < 1
        or retry_interval_seconds < 0
    ):
        raise RuntimeError("live campaign conflict reconciliation authority is incomplete")
    last_result: object = None
    for _attempt in range(attempts):
        try:
            status, body = request(
                "POST",
                "/api/portfolio/create",
                payload,
                token=token,
                idempotency_key=idempotency_key,
            )
        except Exception as exc:  # noqa: BLE001 - retry ambiguous transport outcomes
            last_result = f"{type(exc).__name__}: {exc}"
            if _attempt + 1 < attempts:
                sleep(retry_interval_seconds)
            continue
        last_result = (status, body)
        if _is_payload_conflict(status, body):
            return None
        campaign_id = body.get("campaign_id") if isinstance(body, dict) else None
        if status == 200 and isinstance(campaign_id, str) and campaign_id:
            return campaign_id
        if _attempt + 1 < attempts:
            sleep(retry_interval_seconds)
    raise AssertionError(
        "governed campaign conflict reconciliation failed for "
        f"{idempotency_key}: {last_result!r}"
    )


def archive_campaign_fixture(
    request: Request,
    *,
    campaign_id: str,
    admin_token: str,
    attempts: int = _LEASE_RECOVERY_ATTEMPTS,
    retry_interval_seconds: float = _LEASE_RECOVERY_INTERVAL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Archive one live fixture through the public CAS API, never direct SQL."""

    if (
        not campaign_id
        or not admin_token
        or attempts < 1
        or retry_interval_seconds < 0
    ):
        raise RuntimeError("live campaign cleanup identity is incomplete")
    last_result: tuple[int, object] | None = None
    for _attempt in range(attempts):
        try:
            status, campaign = request(
                "GET",
                f"/api/campaigns/{campaign_id}",
                token=admin_token,
            )
        except Exception:  # noqa: BLE001 - retry ambiguous transport outcomes
            if _attempt + 1 < attempts:
                sleep(retry_interval_seconds)
            continue
        if status != 200 or not isinstance(campaign, dict):
            last_result = (status, campaign)
            if _attempt + 1 < attempts:
                sleep(retry_interval_seconds)
            continue
        current_status = str(campaign.get("status") or "").strip()
        if current_status == "archived":
            return
        if not current_status:
            last_result = (status, campaign)
            if _attempt + 1 < attempts:
                sleep(retry_interval_seconds)
            continue
        try:
            status, archived = request(
                "PATCH",
                f"/api/campaigns/{campaign_id}",
                {
                    "status": "archived",
                    "expected_status": current_status,
                    "rationale": "Archive isolated live release fixture.",
                },
                token=admin_token,
            )
        except Exception:  # noqa: BLE001 - GET on the next pass resolves commit state
            if _attempt + 1 < attempts:
                sleep(retry_interval_seconds)
            continue
        last_result = (status, archived)
        if status == 200:
            # A separate GET in the same bounded attempt, never the mutation
            # response shape, is the durable proof. This preserves a final
            # successful PATCH when no outer retry remains.
            try:
                final_status, final_campaign = request(
                    "GET",
                    f"/api/campaigns/{campaign_id}",
                    token=admin_token,
                )
            except Exception:  # noqa: BLE001 - retry ambiguous observation
                if _attempt + 1 < attempts:
                    sleep(retry_interval_seconds)
                continue
            last_result = (final_status, final_campaign)
            if (
                final_status == 200
                and isinstance(final_campaign, dict)
                and str(final_campaign.get("status") or "").strip() == "archived"
            ):
                return
            if _attempt + 1 < attempts:
                sleep(retry_interval_seconds)
            continue
        if status == 409 or status == 429 or status >= 500:
            if _attempt + 1 < attempts:
                sleep(retry_interval_seconds)
            continue
        break
    raise AssertionError(
        f"governed campaign cleanup failed for {campaign_id}: {last_result!r}"
    )


def _is_payload_conflict(status: int, body: object) -> bool:
    return status == 409 and "different campaign payload" in str(body).casefold()
