"""Campaign-treatment runtime authority state (deploy-promotion marker).

The source-controlled ``app.yaml`` baseline ships
``MIP_CAMPAIGN_TREATMENT_RUNTIME_ENABLED=0``. Only the final snapshot
payload emitted by ``scripts/deploy.sh`` (via
``tools/databricks/app_deploy_payload.py --enable-campaign-treatment-runtime``)
flips the marker to ``1`` after UC constraint/property proof completes,
so a deployment that bypassed the governed pipeline can never claim
treatment-write authority.

Enforcement lives here, at the write path, NOT as a boot-time process
kill. The pre-2026-07-30 design raised inside the FastAPI lifespan,
which turned every bare deploy (Databricks UI "Deploy" button, raw
``databricks apps deploy``) into a full-app crash loop: no health
surface, no read routes, no platform-visible reason — the 2026-07-16
and 2026-07-30 outages. The audited replacement contract:

* treatment writes stay fail-closed (503 with an operator-facing
  reason) until the promoted snapshot's marker is present, and
* every other surface keeps serving, with ``/api/health`` reporting
  ``status="degraded"`` so the UI banner and operators see the state.

UC grants are the authoritative backstop either way: deploys quiesce
runtime ``MODIFY`` on the treatment table until promotion, so even a
process that skipped this check could not write. The marker check is
the defense-in-depth layer that also produces a clean, attributable
error instead of a warehouse permission failure.
"""

from __future__ import annotations

import os

from backend.config.settings import looks_like_databricks_app_deploy

CAMPAIGN_TREATMENT_RUNTIME_MARKER_ENV = "MIP_CAMPAIGN_TREATMENT_RUNTIME_ENABLED"

#: Health-body vocabulary. ``disabled_baseline_deploy`` means the process
#: is running from a deployment that never carried the promotion marker —
#: i.e. it did not go through ``scripts/deploy.sh``'s verified snapshot.
CAMPAIGN_TREATMENT_RUNTIME_ENABLED = "enabled"
CAMPAIGN_TREATMENT_RUNTIME_DISABLED = "disabled_baseline_deploy"


class CampaignTreatmentRuntimeDisabledError(RuntimeError):
    """A campaign-treatment write was attempted before promotion proof."""


def campaign_treatment_runtime_enabled() -> bool:
    """Return True when treatment writes carry deployment-promotion proof.

    Outside Databricks Apps (local uvicorn, pytest) the marker contract
    does not apply — parity with the historical boot gate, which only
    enforced under an Apps deploy.
    """

    if not looks_like_databricks_app_deploy():
        return True
    return os.environ.get(CAMPAIGN_TREATMENT_RUNTIME_MARKER_ENV, "").strip() == "1"


def campaign_treatment_runtime_state() -> str:
    """Health-body state string for the treatment runtime marker."""

    if campaign_treatment_runtime_enabled():
        return CAMPAIGN_TREATMENT_RUNTIME_ENABLED
    return CAMPAIGN_TREATMENT_RUNTIME_DISABLED


def require_campaign_treatment_runtime() -> None:
    """Fail closed when the promotion marker is absent under Databricks Apps.

    Evaluated per write request (never cached) so neither
    ``MIP_BYPASS_STARTUP_CHECKS`` nor any boot-time state can disable
    the production write gate.
    """

    if campaign_treatment_runtime_enabled():
        return
    raise CampaignTreatmentRuntimeDisabledError(
        "Campaign treatment runtime is disabled until governed access proof "
        "completes. This deployment did not carry the promoted snapshot "
        "marker (bare UI/CLI deploys ship the disabled app.yaml baseline); "
        "roll forward with scripts/deploy.sh to restore treatment writes."
    )
