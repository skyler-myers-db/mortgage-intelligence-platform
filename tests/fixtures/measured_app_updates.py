"""How an App update behaves, as spikes S2 and S2b measured it; every fake Apps API's update models it.

The spikes ran on throwaway Apps (2026-11-06 and 2026-11-10, Databricks CLI
v1.10.0, the databricks-sdk pinned in ``uv.lock``; the cutover's spike results
S2-4 and S2b answers 1, 3b and 4). What they measured:

* ``apps update`` (PATCH) replaces every field it carries and CLEARS an
  omitted ``description`` to ``""``: PATCH replaces the field. It ignores
  ``default_source_code_path`` (S7-2). It never restarts the App, creates a
  deployment or changes the compute.
* ``apps create-update`` changes only the fields its ``update_mask`` names
  (comma-separated), and is a long-running operation: a waiter whose
  ``response`` is IN_PROGRESS and whose ``result()`` polls to SUCCEEDED. A
  mask outside the allowed set (``default_source_code_path`` among them) is
  refused. ``update_mask="description"`` touches nothing else: no restart, no
  deployment, no role change.
* EVERY update that carries ``resources``, an identical array included, a
  PATCH or a create-update masked to ``resources`` alike, grants CREATE ON
  DATABASE to the App's Lakebase role again (``=c`` -> ``=Cc``; TEMPORARY is
  not re-granted), with no restart. ``regrants`` counts those sends.
* A secret binding to a key the scope does not hold refuses the whole update
  atomically (S2b 3b): nothing changes and nothing is re-granted.

Not measured: what a resources send does with a binding's OWN
``description`` when the sent binding omits it (every spike payload carried
one). The fakes take the worst case: the sent array replaces the live one
whole, so an omitted per-binding description is gone. No caller may rely on
the API keeping it by name; the restore re-sends each live one
(``app_rollback_resource_contract``), and the binding diff treats it as
display text (``app_resource_bindings.BINDING_DISPLAY_FIELDS``).

``tests/unit/test_fake_app_updates_measured.py`` drives every fake through
these and fails when one answers the way a caller hoped instead.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

# The create-update masks the spike's error text allowed (S7-2).
CREATE_UPDATE_MASKS = frozenset(
    {
        "description",
        "budget_policy_id",
        "usage_policy_id",
        "resources",
        "user_api_scopes",
        "compute_size",
        "compute_min_instances",
        "compute_max_instances",
        "compute_autoscaling",
        "git_repository",
        "git_source",
        "telemetry_export_destinations",
        "forward_user_access_token",
        "compatibility_flags",
        "compute_generation",
    }
)


class InvalidUpdateMask(ValueError):
    """``apps create-update`` with a mask outside the allowed set."""


class MissingSecretKey(ValueError):
    """``Invalid secret resource ...: Secret with scope ... and key ... does not exist.`` (S2b 3b)."""


@dataclass
class UpdateLedger:
    """What the updates did: the PATCHes, the create-updates' masks and the role re-grants."""

    patches: int = 0
    masks: list[str] = field(default_factory=list)
    regrants: int = 0


class MeasuredWait:
    """The create-update waiter: IN_PROGRESS at once, SUCCEEDED from ``result()``."""

    def __init__(self, app_name: str, applied: dict[str, Any]) -> None:
        self.response = SimpleNamespace(
            status=SimpleNamespace(state="IN_PROGRESS", message="App is updating."), **applied
        )
        self._done = SimpleNamespace(
            status=SimpleNamespace(state="SUCCEEDED", message="App update completed successfully."),
            **applied,
        )
        self.app_name = app_name
        self.results = 0

    def result(self, timeout: Any = None) -> SimpleNamespace:
        self.results += 1
        return self._done


def _as_dict(app: Any) -> dict[str, Any]:
    return dict(app.as_dict()) if hasattr(app, "as_dict") else dict(app)


def _check_secret_keys(resources: list[dict[str, Any]], secret_keys: frozenset[tuple[str, str]] | None) -> None:
    if secret_keys is None:
        return
    for item in resources:
        secret = item.get("secret")
        if isinstance(secret, dict) and (secret.get("scope"), secret.get("key")) not in secret_keys:
            raise MissingSecretKey(f"Invalid secret resource {item.get('name')}: the key does not exist.")


def apply_patch(
    target: Any,
    app: Any,
    ledger: UpdateLedger,
    *,
    secret_keys: frozenset[tuple[str, str]] | None = None,
) -> None:
    """``apps update``: carried fields replace; an omitted description becomes ``""``.

    No caller PATCHes without ``resources``, and the spikes never measured
    that shape, so an omitted ``resources`` is left as it was.
    """

    body = _as_dict(app)
    resources = copy.deepcopy(body.get("resources") or [])
    _check_secret_keys(resources, secret_keys)
    ledger.patches += 1
    target.description = str(body.get("description") or "")
    if "resources" in body:
        target.resources = resources
        ledger.regrants += 1


def apply_create_update(
    target: Any,
    app_name: str,
    update_mask: str,
    app: Any,
    ledger: UpdateLedger,
    *,
    secret_keys: frozenset[tuple[str, str]] | None = None,
) -> MeasuredWait:
    """``apps create-update``: only the masked fields; a resources mask re-grants CREATE."""

    fields = [item.strip() for item in str(update_mask).split(",")]
    if not fields or any(item not in CREATE_UPDATE_MASKS for item in fields):
        raise InvalidUpdateMask("Invalid update mask")
    body = _as_dict(app) if app is not None else {}
    applied: dict[str, Any] = {}
    if "resources" in fields:
        # Whole replacement: an omitted per-binding description is gone (the
        # unmeasured worst case, module docstring).
        resources = copy.deepcopy(body.get("resources") or [])
        _check_secret_keys(resources, secret_keys)
        target.resources = resources
        applied["resources"] = copy.deepcopy(resources)
        ledger.regrants += 1
    if "description" in fields:
        target.description = str(body.get("description") or "")
        applied["description"] = target.description
    ledger.masks.append(",".join(fields))
    return MeasuredWait(app_name, applied)
