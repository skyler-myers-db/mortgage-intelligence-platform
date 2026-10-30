"""Which install may run and mail ``mip_scheduled_refresh`` (data-freshness design, 2026-10-26).

The weekly ``mip_scheduled_refresh`` job (databricks.yml) chains FRED -> gold ->
lifecycle -> KPI snapshot. The client profile's own refresh fields decide it
(tools/clients/policy.py, M1-PR3), for any install class:

* ``scheduled_refresh_pause_status`` is a plain variable. scripts/deploy.sh
  exports ``BUNDLE_VAR_scheduled_refresh_pause_status``, UNPAUSED only when the
  profile's ``MIP_SCHEDULED_REFRESH`` is ``on``; :func:`check_pause_status`
  refuses UNPAUSED anywhere else (an install whose refresh is off, and the
  profile-free ``ci`` target), so a stray export can never unpause it through
  tools/databricks/bundle_env.py either.
* ``scheduled_refresh_failure_recipients`` is a complex variable (a list), and
  the CLI refuses a ``BUNDLE_VAR_`` export for a complex variable (CLI v1.10.0:
  "setting via environment variables ... is not supported for complex
  variable"). Its only non-target channel is the bundle's
  ``.databricks/bundle/<target>/variable-overrides.json``, which
  :func:`write_failure_recipients` owns for this one key: the profile's
  ``MIP_SCHEDULED_REFRESH_RECIPIENTS`` (email addresses; the profile refuses an
  application id) when the refresh is on, an empty list otherwise.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path

from tools.clients.policy import InstallPolicy

JOB_KEY = "mip_scheduled_refresh"
PAUSE_VARIABLE = "scheduled_refresh_pause_status"
RECIPIENTS_VARIABLE = "scheduled_refresh_failure_recipients"
PAUSE_ENV = f"BUNDLE_VAR_{PAUSE_VARIABLE}"
PAUSE_STATES = frozenset({"PAUSED", "UNPAUSED"})
_TARGET_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


def refresh_on(policy: InstallPolicy | None) -> bool:
    """The profile turns the weekly refresh on; the profile-free ci target never does."""

    return policy is not None and policy.scheduled_refresh


def check_pause_status(env: Mapping[str, str], *, policy: InstallPolicy | None) -> None:
    """Refuse a malformed pause status, or UNPAUSED when the profile's refresh is off.

    An absent export keeps the bundle default (PAUSED). An empty or unknown
    value would reach the Jobs API as an invalid pause status, so it is
    refused here instead.
    """

    if PAUSE_ENV not in env:
        return
    value = env[PAUSE_ENV]
    if value not in PAUSE_STATES:
        raise ValueError(f"{PAUSE_ENV} must be PAUSED or UNPAUSED")
    if value == "UNPAUSED" and not refresh_on(policy):
        raise ValueError(
            f"{PAUSE_ENV}=UNPAUSED is allowed only when the client profile's "
            "MIP_SCHEDULED_REFRESH is on; every other install keeps mip_scheduled_refresh PAUSED"
        )


def failure_recipients(*, policy: InstallPolicy | None) -> list[str]:
    """The profile's recipients when the refresh is on; nobody otherwise."""

    return list(policy.refresh_recipients) if refresh_on(policy) and policy is not None else []


def overrides_path(bundle_root: Path, target: str) -> Path:
    if _TARGET_NAME.fullmatch(target) is None:
        raise ValueError("bundle target name is invalid")
    return bundle_root / ".databricks" / "bundle" / target / "variable-overrides.json"


def write_failure_recipients(bundle_root: Path, *, target: str, recipients: list[str]) -> Path:
    """Set this one key in the target's variable-overrides.json, keeping any other key.

    A file that is not a JSON object is refused (never overwritten), so an
    operator's own overrides are never lost to a parse error.
    """

    path = overrides_path(bundle_root, target)
    overrides: dict[str, object] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} is not valid JSON; fix or remove it") from exc
        if not isinstance(loaded, dict):
            raise ValueError(f"{path} must hold a JSON object; fix or remove it")
        overrides = loaded
    overrides[RECIPIENTS_VARIABLE] = list(recipients)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f"{path.name}.tmp")
    staged.write_text(json.dumps(overrides, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    staged.replace(path)
    return path


def apply(
    env: Mapping[str, str], *, target: str, policy: InstallPolicy | None, bundle_root: Path
) -> str:
    """Check the pause export and write the recipients; return a one-line status.

    ``target`` names the bundle target whose overrides file is written;
    ``policy`` (None for the ci target) decides the posture. The status line
    counts recipients and never names one.
    """

    check_pause_status(env, policy=policy)
    recipients = failure_recipients(policy=policy)
    write_failure_recipients(bundle_root, target=target, recipients=recipients)
    pause = env.get(PAUSE_ENV) or "PAUSED (bundle default)"
    return f"scheduled_refresh pause={pause} failure_email_recipients={len(recipients)}"
