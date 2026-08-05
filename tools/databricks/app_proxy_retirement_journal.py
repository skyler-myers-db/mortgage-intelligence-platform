"""Pure state transition for the signed proxy-credential retirement journal."""

from __future__ import annotations

from typing import Any

from tools.databricks.app_rollback_record_contract import RECORD_VERSION


def capture_proxy_retirement_ids(
    previous_record: dict[str, Any] | None,
    *,
    candidate_gateway_resources: dict[str, str],
) -> tuple[str, ...]:
    if previous_record is None or previous_record["version"] != RECORD_VERSION:
        return ()
    previous_resources = previous_record["gateway_resources"]
    if (
        previous_resources["proxy_caller_application_id"]
        != candidate_gateway_resources["proxy_caller_application_id"]
    ):
        raise RuntimeError(
            "App rollback proxy identity changed while credential retirement is pending"
        )
    candidate_id = candidate_gateway_resources["proxy_caller_credential_id"]
    candidates = {
        previous_resources["proxy_caller_credential_id"],
        *previous_record["pending_proxy_credential_retirement_ids"],
    }
    return tuple(sorted(candidates - {candidate_id}))
