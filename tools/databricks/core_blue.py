"""The core tier's signed blue: load, verify, health, re-sign and its restore payload (M2-PR7b).

The core release (``MIP_AGENTIC_TIER=off``) stands on a v7 core record: an
adopted one (adopt-live, M2-PR7) or one its own signed capture wrote
(tools/databricks/core_blue_capture.py). Everything here reads or writes the
record only; the App itself is restored by tools/databricks/core_blue_restore.py.

* ``load_core_blue``: the verified current record, judged by the tier gate
  (tools/databricks/blue_tier_gate.py) first, so a core profile never stands
  on an agentic blue and an agentic profile on a core blue only with the
  exact ``tier_transition`` ack. No record or a retired tombstone is None.
* ``verify_core_blue``: the App's service-principal identity and the core
  resource proof (``app_rollback_record_v7.assert_core_resource_proof``):
  exactly the record's bindings, any serving-endpoint, Gateway or proxy
  binding refused by name. It never consults the Gateway resource proof.
* ``core_health``: the App's authenticated health names the record's commit
  and no Gateway binding, pinned to the expected deployment. A deployment
  whose env-name set holds the lease name (a governed capture) is pinned with
  it; an adopted deployment (the emergency recipe's, which sets no lease) is
  pinned by its id alone.
* ``resign``: the next version of a record (a restore's re-pin) under the
  lease, read back exactly.
* ``blue_restore_payload``: blue's tier-b restore payload for an adopted
  record that has none yet. Databricks redacts env values from
  ``get_deployment``, so the payload is rebuilt with the payload builder from
  the profile, the way the emergency recipe built the adopted deployment:
  blue's source is the immutable copy, ``MIP_GIT_SHA`` is blue's commit, the
  campaign-treatment runtime is off and, as for every core payload, no agentic
  name rides it (the two switches only when blue's own set holds them, as
  ``"0"``). A profile-optional name (``OPTIONAL_PAYLOAD_ENV_NAMES``: the
  auditor identities, the role emails and group names) that blue's set lacks
  is left out too: blue ran without it, so leaving it out reproduces blue. It
  is returned only when its env-name set equals blue's exactly; otherwise
  there is none, and the pre-activation proof then needs the exact
  ``no_rollback_for`` ack.

Messages name codes, digests and env names, never an id, a path or a value.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any, cast

from tools.clients.profile import OPTIONAL_PAYLOAD_ENV_NAMES
from tools.databricks.app_deployment_state import active_deployment_id
from tools.databricks.app_rollback_record_contract import (
    V7_RECORD_VERSION,
    _text,
    _validated_payload,
    load_signed_record,
    save_versioned_record,
)
from tools.databricks.app_rollback_record_v7 import (
    CORE_OFF_SWITCHES,
    V7RecordError,
    assert_core_off_switches,
    assert_core_resource_proof,
    refuse_agentic_resources,
    unsigned,
    validated_v7_record,
)
from tools.databricks.app_rollback_resource_contract import validated_app_resource_contract
from tools.databricks.blue_tier_gate import assert_blue_tier, current_blue

LEASE_ENV = "MIP_APP_DEPLOYMENT_LEASE_ID"
ABSENT = "no server-owned last-good App rollback contract exists"
# health(record, deployment_id): raise unless the App serves that record there.
Health = Callable[[Mapping[str, Any], str], None]


class CoreBlueRefused(RuntimeError):
    """A core-blue proof refuses; ``code`` is reviewed and the message names no id."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def load_core_blue(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
    lakebase_instance: str,
    agentic_tier: str,
    tier_transition_ack: str | None = None,
) -> dict[str, Any] | None:
    """The verified current core blue, or None; the tier gate judges it first."""

    record = current_blue(
        load_signed_record(
            workspace, app_name=app_name, scope=scope, expected_lakebase_instance=lakebase_instance
        )
    )
    if record is None:
        return None
    assert_blue_tier(
        record,
        app_name=app_name,
        agentic_tier=agentic_tier,
        tier_transition_ack=tier_transition_ack,
    )
    if record.get("version") != V7_RECORD_VERSION or record.get("tier") != "core":
        raise CoreBlueRefused(
            "not_core",
            "the signed blue is the agentic tier's, which the agentic release path proves",
        )
    return dict(record)


def require_core_blue(record: dict[str, Any] | None) -> dict[str, Any]:
    if record is None:
        raise CoreBlueRefused(
            "rollback_record_absent", f"{ABSENT} (the slot is empty or holds a retired tombstone)"
        )
    return record


def assert_identity(workspace: Any, record: Mapping[str, Any], *, app_name: str) -> None:
    app = workspace.apps.get(app_name)
    identity = (
        _text(getattr(app, "service_principal_client_id", None)),
        _text(getattr(app, "service_principal_id", None)),
    )
    if identity != (
        record["app_service_principal_client_id"],
        record["app_service_principal_scim_id"],
    ):
        raise CoreBlueRefused(
            "identity_drift", "App service-principal identity drifted from the signed core record"
        )


def live_core_bindings(workspace: Any, *, app_name: str) -> list[dict[str, object]]:
    """The App's live bindings, core kinds only: a serving-endpoint binding refuses by name."""

    app = workspace.apps.get(app_name)
    raw = [
        item.as_dict() if hasattr(item, "as_dict") else item
        for item in (getattr(app, "resources", None) or [])
    ]
    try:
        refuse_agentic_resources(raw)
        return validated_app_resource_contract(raw)
    except (V7RecordError, RuntimeError) as exc:
        raise CoreBlueRefused("resource_drift", str(exc)) from exc


def verify_core_blue(
    workspace: Any,
    record: Mapping[str, Any],
    *,
    app_name: str,
    reviewed: list[dict[str, object]] | None = None,
) -> None:
    """Identity and the core resource proof; never the Gateway resource proof."""

    assert_identity(workspace, record, app_name=app_name)
    live = live_core_bindings(workspace, app_name=app_name)
    try:
        assert_core_resource_proof(record, live=live, reviewed=reviewed)
    except (V7RecordError, RuntimeError) as exc:
        raise CoreBlueRefused("resource_drift", str(exc)) from exc


def payload_lease(record: Mapping[str, Any]) -> str | None:
    """The literal lease id blue's restore payload carries, if any."""

    payload = record.get("restore_payload") or {}
    for item in payload.get("env_vars") or []:
        if item.get("name") == LEASE_ENV and "value" in item:
            return str(item["value"]).strip()
    return None


def assert_serving(workspace: Any, *, app_name: str, deployment_id: str) -> None:
    if active_deployment_id(workspace, app_name=app_name) != deployment_id:
        raise CoreBlueRefused(
            "deployment_changed", "the App's active deployment is not the one this proof pinned"
        )


def core_health(
    workspace: Any, *, app_name: str, base_url: str, bearer_token: str
) -> Health:
    """The live health check: the record's commit, no Gateway binding, pinned."""

    from tools.databricks.app_deployment_health import health as pinned_health
    from tools.databricks.app_health_contract import (
        APP_HEALTH_READY_INTERVAL_S,
        APP_HEALTH_READY_TIMEOUT_S,
        active_app_deployment_pin,
        wait_for_authenticated_app_health,
    )

    def check(record: Mapping[str, Any], deployment_id: str) -> None:
        assert_serving(workspace, app_name=app_name, deployment_id=deployment_id)
        if LEASE_ENV in record["env_names"]:
            pin = active_app_deployment_pin(workspace, app_name=app_name)
            if pin.deployment_id != deployment_id:
                raise CoreBlueRefused("health_unpinned", "App health pinned another deployment")
            sha, binding, lease = pinned_health(
                workspace,
                app_name=app_name,
                base_url=base_url,
                bearer_token=bearer_token,
                expected_pin=pin,
            )
            expected_lease = payload_lease(record)
            if expected_lease is not None and lease != expected_lease:
                raise CoreBlueRefused("health_mismatch", "App health names another lease")
        else:
            body = wait_for_authenticated_app_health(
                workspace,
                app_name=app_name,
                base_url=base_url,
                bearer_token=bearer_token,
                timeout_s=APP_HEALTH_READY_TIMEOUT_S,
                interval_s=APP_HEALTH_READY_INTERVAL_S,
                assert_pinned=lambda: assert_serving(
                    workspace, app_name=app_name, deployment_id=deployment_id
                ),
            )
            sha, binding = str(body.get("git_sha") or "").strip(), body.get(
                "agent_gateway_binding_sha256"
            )
        if sha != record["git_sha"] or binding is not None:
            raise CoreBlueRefused(
                "health_mismatch",
                "App health does not name the signed core record's commit with no Gateway binding",
            )
        assert_serving(workspace, app_name=app_name, deployment_id=deployment_id)

    return check


def resign(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
    lakebase_instance: str,
    record: Mapping[str, Any],
    lease: Callable[[], None],
    **changes: Any,
) -> dict[str, Any]:
    """The record's next version with ``changes``, written under the lease and read back."""

    candidate = validated_v7_record(
        {**unsigned(record), **changes},
        app_name=app_name,
        expected_lakebase_instance=lakebase_instance,
    )
    lease()
    save_versioned_record(workspace, scope=scope, record=unsigned(candidate))
    persisted = load_signed_record(
        workspace, app_name=app_name, scope=scope, expected_lakebase_instance=lakebase_instance
    )
    if persisted is None or unsigned(persisted) != unsigned(candidate):
        raise CoreBlueRefused("record_unverified", "the signed record did not read back exactly")
    return persisted


def binding_values(record: Mapping[str, Any]) -> dict[str, str]:
    """The shell's view of the core blue (blue_restore_proof inspect's names)."""

    return {
        "MIP_APP_ROLLBACK_RECORD_VERSION": str(record["version"]),
        "MIP_APP_ROLLBACK_TIER": str(record["tier"]),
        "MIP_APP_ROLLBACK_PROVENANCE": str(record["provenance"]),
        "MIP_APP_ROLLBACK_DEPLOYMENT_ID": str(record["deployment_id"]),
        "MIP_APP_ROLLBACK_GENIE_SPACE_ID": str(record["genie_space_id"]),
        "MIP_APP_ROLLBACK_FRONTEND_PROVENANCE": str(record["frontend_provenance"]),
        "MIP_APP_ROLLBACK_RESTORE_PAYLOAD_SHA256": str(record["restore_payload_sha256"] or ""),
    }


@contextmanager
def _environ(changes: Mapping[str, str | None]) -> Iterator[None]:
    saved = {name: os.environ.get(name) for name in changes}
    try:
        for name, value in changes.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def blue_restore_payload(
    record: Mapping[str, Any] | None,
    *,
    install_class: str,
    app_env: str,
    catalog: str,
    schema: str,
    lakebase_instance: str,
    lakebase_database: str | None = None,
    build: Callable[..., dict[str, object]] | None = None,
) -> tuple[dict[str, object] | None, str]:
    """(blue's restore payload or None, why); never a payload whose env-name set is not blue's.

    ``lakebase_database`` is the database the deploy binds (the profile's
    ``LAKEBASE_DATABASE``), handed to the builder as the deploy hands it, so
    a client bound to another database than the App's default rebuilds the
    ``LAKEBASE_DATABASE`` its blue carries (M3-PR4). ``None`` leaves the
    builder's default, the App's own database, which adds no name.
    """

    if record is None or record.get("version") != V7_RECORD_VERSION or record.get("tier") != "core":
        return None, "no v7 core blue needs a restore payload built"
    if record.get("restore_payload") is not None:
        return None, "blue's restore payload is already recorded"
    if build is None:
        from tools.databricks.app_deploy_payload import build_payload

        build = build_payload
    names = set(record["env_names"])
    # The builder reads the shell and .env.local: blue's own commit, and no
    # lease name unless blue's deployment carried one.
    lease = os.environ.get(LEASE_ENV) if LEASE_ENV in names else None
    database = {} if lakebase_database is None else {"lakebase_database": lakebase_database}
    try:
        with _environ({"MIP_GIT_SHA": str(record["git_sha"]), LEASE_ENV: lease}):
            payload = build(
                source_code_path=str(record["restore_source_path"]),
                install_class=install_class,
                app_env=app_env,
                catalog=catalog,
                schema=schema,
                lakebase_instance=lakebase_instance,
                campaign_treatment_runtime_enabled=False,
                **database,
                agentic_tier="off",
            )
    except ValueError as exc:  # a lender or tier refusal names no value
        return None, f"the payload builder refused ({type(exc).__name__}: {exc})"
    # The core payload: no agentic name, the two switches "0". A blue the
    # emergency recipe built carries neither switch, which reads as off too,
    # nor a profile-optional name the profile set only after it was built.
    droppable = {*CORE_OFF_SWITCHES, *OPTIONAL_PAYLOAD_ENV_NAMES}
    env_vars = [
        item
        for item in cast(list[dict[str, object]], payload["env_vars"])
        if item["name"] not in droppable or item["name"] in names
    ]
    payload = {**payload, "env_vars": env_vars}
    built = {str(item["name"]) for item in env_vars}
    if built != names:
        extra, missing = sorted(built - names), sorted(names - built)
        return None, (
            "the rebuilt payload's env-name set is not blue's (adds "
            f"{', '.join(extra) or 'none'}; lacks {', '.join(missing) or 'none'})"
        )
    try:
        _validated_payload(
            payload, require_immutable_source=False, expected_lakebase_instance=lakebase_instance
        )
        assert_core_off_switches(env_vars)
    except (RuntimeError, V7RecordError) as exc:
        return None, f"the rebuilt payload refuses: {exc}"
    return payload, "blue's restore payload was rebuilt with blue's exact env-name set"
