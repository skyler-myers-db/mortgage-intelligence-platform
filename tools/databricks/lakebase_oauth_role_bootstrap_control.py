"""The fresh OAuth-M2M positive control a one-use Lakebase role creator needs.

The one-use creator proves its own destroyed credential unusable against a
control: a second, fresh OAuth-M2M client that still authenticates and reads
the instance (tools/databricks/lakebase_oauth_role_bootstrap_orchestration.py).
scripts/deploy.sh hands the control only to the convergence child, as
``MIP_LAKEBASE_BOOTSTRAP_CONTROL_CLIENT_ID`` and ``..._SECRET``: the tier's
control identity (tools/databricks/m2m_identity_contract.
lakebase_bootstrap_control). Both values must be present, and the control
must not be the role being created.
"""

from __future__ import annotations

from collections.abc import Mapping

CONTROL_CLIENT_ID_ENV = "MIP_LAKEBASE_BOOTSTRAP_CONTROL_CLIENT_ID"
CONTROL_CLIENT_SECRET_ENV = "MIP_LAKEBASE_BOOTSTRAP_CONTROL_CLIENT_SECRET"


def bootstrap_control_credentials(
    environ: Mapping[str, str], *, target_application_id: str
) -> tuple[str, str]:
    """``(client id, client secret)`` of the control, or refuse before any creator exists."""

    control_application_id = environ.get(CONTROL_CLIENT_ID_ENV, "").strip()
    control_client_secret = environ.get(CONTROL_CLIENT_SECRET_ENV, "").strip()
    if not control_application_id or not control_client_secret:
        raise RuntimeError(
            "fresh OAuth-M2M Lakebase bootstrap control credentials are required"
        )
    if control_application_id == target_application_id:
        raise RuntimeError(
            "Lakebase bootstrap control identity must be distinct from the target"
        )
    return control_application_id, control_client_secret
