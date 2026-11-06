"""The core release's signed capture: green becomes a v7 core record, provenance captured (M2-PR7b).

After the one activation and the live smoke, the core release
(``MIP_AGENTIC_TIER=off``) signs the deployment it activated as the new last
good App, the core tier's counterpart of v6's ``capture_current``. It needs
no verifier, agent runtime or Supervisor proxy, reads no Gateway resource
proof and writes no Gateway contract: the record's resource proof is exactly
the core App bindings. In order, each with its own refusal:

1. the profile's tier is core (``tier_agentic``): a capture's tier is the
   profile's, and the agentic tier keeps v6's capture;
2. the payload is the governed one: SNAPSHOT, complete, this release's own
   folder (``app-src/<release id>``), the profile's Lakebase instance, the
   agentic switches exactly ``"0"`` or absent, and this run's lease;
3. authenticated health, pinned to the active deployment, names this commit,
   no Gateway binding and this lease; the lease is held;
4. the active deployment is the newest SUCCEEDED one, deployed from that
   folder, with exactly the payload's env-name set;
5. the folder and the deployment's ``/src/`` snapshot both hash to the
   full-tree digest of the staged release this run built and published, so
   ``frontend/dist`` is this commit's own build (``verified``, with the dist
   digest);
6. the live bindings are core kinds only (a serving-endpoint binding refuses
   by name), equal the profile's reviewed bindings, and bind the governed
   Genie space; the App ACL is snapshotted;
7. then, exactly as v6 does: treatment quiesced, the record written under the
   lease and read back, treatment converged to runtime, and identity, the core
   resource proof and health proven again. Any failure after the runtime grant
   quiesces treatment again before it propagates.

The record's restore source is the release folder itself (create-only and
byte-verified; prune keeps every folder a record names) and its restore
payload is the payload that was deployed, so a later tier-b restore of it
needs nothing rebuilt.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from tools.databricks import adopted_source_copy as source
from tools.databricks import app_release_contract as release
from tools.databricks.app_deployment_state import active_deployment_id, latest_succeeded
from tools.databricks.app_rollback_record_contract import (
    _text,
    _validated_payload,
    load_signed_record,
    save_versioned_record,
)
from tools.databricks.app_rollback_record_v7 import (
    V7RecordError,
    assert_core_off_switches,
    build_v7_record,
    tier_of_profile,
    unsigned,
)
from tools.databricks.app_rollback_resource_contract import validated_app_resource_contract
from tools.databricks.app_start_deployment import env_names, tree_digest
from tools.databricks.core_blue import (
    LEASE_ENV,
    CoreBlueRefused,
    live_core_bindings,
    verify_core_blue,
)

# health() -> (git sha, Gateway binding, lease id, the deployment it pinned)
GreenHealth = Callable[[], tuple[str, str | None, str, str]]


def stage_files(stage_dir: Path) -> dict[str, bytes]:
    """The staged release this run built, by relative path (what was published)."""

    return {
        path.relative_to(stage_dir).as_posix(): path.read_bytes()
        for path in sorted(stage_dir.rglob("*"))
        if path.is_file()
    }


def _env_vars(payload: Mapping[str, object]) -> list[dict[str, Any]]:
    return list(cast(list[dict[str, Any]], payload["env_vars"]))


def _payload(
    payload: Mapping[str, object], *, release_id: str, lease_id: str, lakebase_instance: str
) -> dict[str, object]:
    try:
        candidate = _validated_payload(
            dict(payload),
            require_immutable_source=False,
            expected_lakebase_instance=lakebase_instance,
        )
        if release.release_id_in(candidate["source_code_path"]) != release_id:
            raise CoreBlueRefused("source_not_release", "the payload names another release folder")
        assert_core_off_switches(candidate["env_vars"])  # type: ignore[arg-type]
        UUID(lease_id)
    except (RuntimeError, ValueError, release.ReleaseContractError) as exc:
        if isinstance(exc, CoreBlueRefused):
            raise
        raise CoreBlueRefused("payload_invalid", f"the green payload refuses: {exc}") from exc
    literal = {
        str(item["name"]): str(item.get("value") or "") for item in _env_vars(candidate)
    }
    if literal.get(LEASE_ENV) != lease_id:
        raise CoreBlueRefused("payload_invalid", "the green payload does not carry this run's lease")
    return candidate


def _acl(workspace: Any, app_name: str) -> list[dict[str, object]]:
    from tools.databricks.adopt_live_facts import AdoptLiveRefused
    from tools.databricks.adopt_live_facts import _acl as snapshot

    try:
        return snapshot(workspace, app_name)
    except AdoptLiveRefused as exc:
        raise CoreBlueRefused(exc.code, str(exc)) from exc


def _green(
    workspace: Any, *, app_name: str, folder: str, names: set[str], pinned: str
) -> object:
    latest = latest_succeeded(workspace, app_name=app_name)
    latest_id = _text(getattr(latest, "deployment_id", None))
    if (
        _text(getattr(latest, "source_code_path", None)) != folder
        or active_deployment_id(workspace, app_name=app_name) != latest_id
        or pinned != latest_id
    ):
        raise CoreBlueRefused(
            "green_not_active",
            "the active deployment is not the newest succeeded one from this release folder",
        )
    if set(env_names(latest)) != names:
        raise CoreBlueRefused(
            "env_names_differ", "the active deployment's env-name set is not the payload's"
        )
    return latest


def capture_core_green(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
    lakebase_instance: str,
    agentic_tier: str,
    payload: Mapping[str, object],
    release_id: str,
    staged: Mapping[str, bytes],
    expected_git_sha: str,
    lease_id: str,
    genie_space_id: str,
    reviewed_resources: list[dict[str, object]],
    health: GreenHealth,
    lease: Callable[[], None],
    treatment: Callable[[str], None],
    tree: Callable[[Any, str], str] = tree_digest,
) -> dict[str, Any]:
    """Sign the active core green as the last-good App; returns the persisted record."""

    if tier_of_profile(agentic_tier) != "core":
        raise CoreBlueRefused(
            "tier_agentic", "the core capture signs a core-tier profile only; v6 capture is agentic's"
        )
    candidate = _payload(
        payload, release_id=release_id, lease_id=lease_id, lakebase_instance=lakebase_instance
    )
    folder = str(candidate["source_code_path"])
    names = {str(item["name"]) for item in _env_vars(candidate)}
    sha, binding, health_lease, pinned = health()
    if (sha, binding, health_lease) != (expected_git_sha, None, lease_id):
        raise CoreBlueRefused(
            "health_mismatch",
            "green's health does not name this commit and lease with no Gateway binding",
        )
    lease()
    latest = _green(workspace, app_name=app_name, folder=folder, names=names, pinned=pinned)
    deployment_id = _text(getattr(latest, "deployment_id", None))
    local = source.folder_digest(staged)
    snapshot = _text(
        getattr(getattr(latest, "deployment_artifacts", None), "source_code_path", None)
    )
    dist = {path: data for path, data in staged.items() if path.startswith(source.DIST_PREFIX)}
    if not dist or tree(workspace, folder) != local or "/src/" not in snapshot or tree(
        workspace, snapshot
    ) != local:
        raise CoreBlueRefused(
            "tree_drift",
            "the release folder or the deployment's snapshot is not the release this run staged",
        )
    try:
        live = live_core_bindings(workspace, app_name=app_name)
        if validated_app_resource_contract(reviewed_resources) != live:
            raise V7RecordError("the live bindings are not the profile's reviewed bindings")
    except (V7RecordError, RuntimeError) as exc:
        raise CoreBlueRefused("bindings_invalid", str(exc)) from exc
    app = workspace.apps.get(app_name)
    try:
        record = build_v7_record(
            expected_lakebase_instance=lakebase_instance,
            tier="core",
            provenance="captured",
            app_name=app_name,
            deployment_id=deployment_id,
            git_sha=sha,
            app_service_principal_client_id=_text(getattr(app, "service_principal_client_id", None)),
            app_service_principal_scim_id=_text(getattr(app, "service_principal_id", None)),
            app_resources=live,
            app_acl=_acl(workspace, app_name),
            treatment_mode="runtime",
            env_names=sorted(names),
            source_code_path=folder,
            source_tree_sha256=local,
            restore_source_path=folder,
            frontend_provenance="verified",
            frontend_reference_sha256=source.dist_digest(dist),
            restore_payload=candidate,
            predecessor_deployment_id=None,
            ack_purpose=None,
            ack_sha256=None,
        )
    except V7RecordError as exc:
        raise CoreBlueRefused("record_invalid", str(exc)) from exc
    if record["genie_space_id"] != genie_space_id:
        raise CoreBlueRefused("bindings_invalid", "the App binds another Genie space")
    return _persist(
        workspace,
        record,
        scope=scope,
        lakebase_instance=lakebase_instance,
        health=health,
        lease=lease,
        treatment=treatment,
        expected=(sha, binding, health_lease, pinned),
    )


def _persist(
    workspace: Any,
    record: dict[str, Any],
    *,
    scope: str,
    lakebase_instance: str,
    health: GreenHealth,
    lease: Callable[[], None],
    treatment: Callable[[str], None],
    expected: tuple[str, str | None, str, str],
) -> dict[str, Any]:
    app_name, deployment_id = str(record["app_name"]), str(record["deployment_id"])

    def still_green() -> None:
        if active_deployment_id(workspace, app_name=app_name) != deployment_id:
            raise CoreBlueRefused(
                "deployment_changed", "the active deployment changed during the core capture"
            )

    activated = False
    try:
        # Durable and readable while green still cannot write treatment state.
        treatment("quiesce")
        still_green()
        lease()
        save_versioned_record(workspace, scope=scope, record=record)
        persisted = load_signed_record(
            workspace, app_name=app_name, scope=scope, expected_lakebase_instance=lakebase_instance
        )
        if persisted is None or unsigned(persisted) != unsigned(record):
            raise CoreBlueRefused("record_unverified", "the signed record did not read back exactly")
        still_green()
        treatment("runtime")
        activated = True
        still_green()
        verify_core_blue(workspace, persisted, app_name=app_name)
        if health() != expected:
            raise CoreBlueRefused("health_mismatch", "green's health drifted after the capture")
        still_green()
        lease()
        return persisted
    except BaseException:
        if activated:
            treatment("quiesce")
        raise
