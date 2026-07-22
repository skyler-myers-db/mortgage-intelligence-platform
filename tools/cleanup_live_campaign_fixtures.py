"""Archive only run-marked campaign fixtures through the deployed public API."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from urllib.parse import urlparse

Request = Callable[..., tuple[int, object]]
RUN_MARKER_RE = re.compile(r"gha[a-j]+r[a-j]+")
CAMPAIGN_FIXTURE_LABELS = frozenset(
    {
        "Conflicting live campaign payload",
        "Live campaign audit contract",
        "Live Lakebase approval contract",
        "Live Lakebase concurrency contract",
    }
)
_FIXTURE_NAME_RE = re.compile(
    rf"(?:{'|'.join(re.escape(label) for label in sorted(CAMPAIGN_FIXTURE_LABELS))}) "
    rf"(?P<marker>{RUN_MARKER_RE.pattern})"
)
_LEASE_RECOVERY_ATTEMPTS = 75
_LEASE_RECOVERY_INTERVAL_SECONDS = 5.0
_ABSENCE_OBSERVATIONS = 3
_INVENTORY_LIMIT = 200


def run_scoped_campaign_name(label: str, *, marker: str | None = None) -> str:
    """Bind a durable live fixture to one discoverable GitHub run attempt."""

    run_marker = (
        marker
        if marker is not None
        else os.environ.get("MIP_LIVE_CAMPAIGN_RUN_MARKER", "").strip()
    )
    if label not in CAMPAIGN_FIXTURE_LABELS or not RUN_MARKER_RE.fullmatch(run_marker):
        raise RuntimeError("live campaign fixture run marker or label is invalid")
    name = f"{label} {run_marker}"
    if len(name) > 80:
        raise RuntimeError("live campaign fixture name exceeds the public contract")
    return name


def _fixture_marker(name: object) -> str | None:
    match = _FIXTURE_NAME_RE.fullmatch(str(name or ""))
    return match.group("marker") if match else None


def _archive_campaign(
    request: Request,
    *,
    campaign_id: str,
    admin_token: str,
    attempts: int = _LEASE_RECOVERY_ATTEMPTS,
    retry_interval_seconds: float = _LEASE_RECOVERY_INTERVAL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    last_result: object = None
    for attempt in range(attempts):
        try:
            status, campaign = request(
                "GET",
                f"/api/campaigns/{campaign_id}",
                token=admin_token,
            )
        except Exception as exc:  # noqa: BLE001 - bounded transport reconciliation
            last_result = f"{type(exc).__name__}: {exc}"
            if attempt + 1 < attempts:
                sleep(retry_interval_seconds)
            continue
        last_result = (status, campaign)
        if status != 200 or not isinstance(campaign, dict):
            if attempt + 1 < attempts:
                sleep(retry_interval_seconds)
            continue
        current_status = str(campaign.get("status") or "").strip()
        if current_status == "archived":
            return
        if not current_status:
            if attempt + 1 < attempts:
                sleep(retry_interval_seconds)
            continue
        try:
            patch_status, patched = request(
                "PATCH",
                f"/api/campaigns/{campaign_id}",
                {
                    "status": "archived",
                    "expected_status": current_status,
                    "rationale": "Archive isolated live release fixture.",
                },
                token=admin_token,
            )
        except Exception as exc:  # noqa: BLE001 - next GET resolves commit ambiguity
            last_result = f"{type(exc).__name__}: {exc}"
            if attempt + 1 < attempts:
                sleep(retry_interval_seconds)
            continue
        last_result = (patch_status, patched)
        if patch_status == 200 and isinstance(patched, dict):
            # Only the next GET may complete the durable proof.
            continue
        if patch_status in {409, 429} or patch_status >= 500:
            if attempt + 1 < attempts:
                sleep(retry_interval_seconds)
            continue
        break
    raise RuntimeError(f"live campaign {campaign_id} could not be archived: {last_result!r}")


def cleanup_live_campaign_fixtures(
    request: Request,
    *,
    owner_token: str,
    admin_token: str,
    run_marker: str | None,
    sleep: Callable[[float], None] = time.sleep,
    absence_interval_seconds: float = 1.0,
    inventory_cycles: int = 50,
) -> tuple[str, ...]:
    """Archive an exact run or every abandoned marked run, then prove absence."""

    if not owner_token or not admin_token:
        raise RuntimeError("live campaign cleanup requires owner and admin identities")
    if run_marker is not None and not RUN_MARKER_RE.fullmatch(run_marker):
        raise RuntimeError("live campaign cleanup run marker is invalid")
    if absence_interval_seconds < 0 or inventory_cycles < _ABSENCE_OBSERVATIONS:
        raise RuntimeError("live campaign cleanup observation budget is invalid")

    archived_ids: set[str] = set()
    absence_observations = 0
    for cycle in range(inventory_cycles):
        status, body = request(
            "GET",
            f"/api/campaigns?limit={_INVENTORY_LIMIT}",
            token=owner_token,
        )
        if status != 200 or not isinstance(body, dict):
            raise RuntimeError(f"live campaign inventory failed: {(status, body)!r}")
        campaigns = body.get("campaigns")
        if not isinstance(campaigns, list):
            raise RuntimeError("live campaign inventory response is malformed")
        matches: list[str] = []
        for campaign in campaigns:
            if not isinstance(campaign, dict):
                raise RuntimeError("live campaign inventory row is malformed")
            marker = _fixture_marker(campaign.get("name"))
            if marker is None or (run_marker is not None and marker != run_marker):
                continue
            campaign_id = campaign.get("campaign_id")
            if not isinstance(campaign_id, str) or not campaign_id:
                raise RuntimeError("marked live campaign has no exact campaign ID")
            matches.append(campaign_id)

        if matches:
            absence_observations = 0
            for campaign_id in matches:
                _archive_campaign(
                    request,
                    campaign_id=campaign_id,
                    admin_token=admin_token,
                    sleep=sleep,
                )
                archived_ids.add(campaign_id)
            continue
        if len(campaigns) >= _INVENTORY_LIMIT:
            raise RuntimeError(
                "live campaign inventory is truncated; marked-fixture absence is unproven"
            )
        absence_observations += 1
        if absence_observations == _ABSENCE_OBSERVATIONS:
            return tuple(sorted(archived_ids))
        if cycle + 1 < inventory_cycles:
            sleep(absence_interval_seconds)
    raise RuntimeError("live campaign marked-fixture absence did not converge")


def _http_request(base_url: str) -> Request:
    def request(
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        *,
        token: str,
    ) -> tuple[int, object]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            f"{base_url}{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as response:  # noqa: S310
                raw = response.read().decode("utf-8")
                return int(response.status), json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            try:
                body: object = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                body = raw
            return int(exc.code), body

    return request


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("MIP_APP_URL", ""))
    parser.add_argument("--owner-token-env", default="MIP_BEARER_TOKEN")
    parser.add_argument("--admin-token-env", default="MIP_ADMIN_BEARER_TOKEN")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--run-marker")
    scope.add_argument("--all-run-markers", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    base_url = str(args.base_url or "").rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path not in {"", "/"}:
        raise SystemExit("live campaign cleanup requires an origin-only HTTPS App URL")
    owner_token = os.environ.get(args.owner_token_env, "").strip()
    admin_token = os.environ.get(args.admin_token_env, "").strip()
    archived = cleanup_live_campaign_fixtures(
        _http_request(base_url),
        owner_token=owner_token,
        admin_token=admin_token,
        run_marker=None if args.all_run_markers else args.run_marker,
    )
    print(
        "[mip-live-campaign-cleanup] marked fixture absence passed "
        f"archived_count={len(archived)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
