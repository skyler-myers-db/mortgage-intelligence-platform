"""Which install may run and mail ``mip_scheduled_refresh`` (data-freshness design, 2026-10-26).

The weekly ``mip_scheduled_refresh`` job (databricks.yml) chains FRED -> gold ->
lifecycle -> KPI snapshot. Only the dev target's live install (the default
bundle root leaf, so an empty ``install_suffix``) may run it on a schedule or
mail its failures:

* ``scheduled_refresh_pause_status`` is a plain variable. scripts/deploy.sh
  exports ``BUNDLE_VAR_scheduled_refresh_pause_status`` (UNPAUSED only for the
  live install); :func:`check_pause_status` refuses UNPAUSED anywhere else, so
  a stray export can never unpause the CI staging install or prod through
  tools/databricks/bundle_env.py either.
* ``scheduled_refresh_failure_recipients`` is a complex variable (a list), and
  the CLI refuses a ``BUNDLE_VAR_`` export for a complex variable (CLI v1.10.0:
  "setting via environment variables ... is not supported for complex
  variable"). Its only non-target channel is the bundle's
  ``.databricks/bundle/<target>/variable-overrides.json``, which
  :func:`write_failure_recipients` owns for this one key: the deploying user
  (``${workspace.current_user.userName}``, resolved by the CLI) for the live
  install, an empty list for everything else. The CI staging install deploys
  as a service principal whose ``userName`` is an application id, which the
  Jobs API is not proven to accept as an email recipient, so it gets none.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path

JOB_KEY = "mip_scheduled_refresh"
PAUSE_VARIABLE = "scheduled_refresh_pause_status"
RECIPIENTS_VARIABLE = "scheduled_refresh_failure_recipients"
PAUSE_ENV = f"BUNDLE_VAR_{PAUSE_VARIABLE}"
LIVE_INSTALL_RECIPIENTS = ("${workspace.current_user.userName}",)
PAUSE_STATES = frozenset({"PAUSED", "UNPAUSED"})
_TARGET_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


def is_live_install(*, target: str, install_suffix: str) -> bool:
    """The dev target's default root leaf: the only install the schedule serves."""

    return target == "dev" and install_suffix == ""


def check_pause_status(env: Mapping[str, str], *, target: str, install_suffix: str) -> None:
    """Refuse a malformed pause status, or UNPAUSED outside the live install.

    An absent export keeps the bundle default (PAUSED). An empty or unknown
    value would reach the Jobs API as an invalid pause status, so it is
    refused here instead.
    """

    if PAUSE_ENV not in env:
        return
    value = env[PAUSE_ENV]
    if value not in PAUSE_STATES:
        raise ValueError(f"{PAUSE_ENV} must be PAUSED or UNPAUSED")
    if value == "UNPAUSED" and not is_live_install(target=target, install_suffix=install_suffix):
        raise ValueError(
            f"{PAUSE_ENV}=UNPAUSED is allowed only for the dev target's live install; "
            "the CI staging install and prod keep mip_scheduled_refresh PAUSED"
        )


def failure_recipients(*, target: str, install_suffix: str) -> list[str]:
    """The live install mails its deployer; every other install mails nobody."""

    if is_live_install(target=target, install_suffix=install_suffix):
        return list(LIVE_INSTALL_RECIPIENTS)
    return []


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


def apply(env: Mapping[str, str], *, target: str, install_suffix: str, bundle_root: Path) -> str:
    """Check the pause export and write the recipients; return a one-line status."""

    check_pause_status(env, target=target, install_suffix=install_suffix)
    recipients = failure_recipients(target=target, install_suffix=install_suffix)
    write_failure_recipients(bundle_root, target=target, recipients=recipients)
    pause = env.get(PAUSE_ENV) or "PAUSED (bundle default)"
    mail = "deployer" if recipients else "none"
    return f"scheduled_refresh pause={pause} failure_email={mail}"
