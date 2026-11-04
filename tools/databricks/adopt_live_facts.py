"""adopt-live's read-only preconditions over the live App, one reviewed refusal each (M2-PR7, §4.8).

``gather`` reads, in order, and refuses at the first precondition that fails:

1. ``deployment_unstable``: the active deployment is the newest SUCCEEDED one,
   nothing is pending and the App compute is ACTIVE.
2. ``env_names_not_proxyless``: the deployment's env-name set (names only;
   Databricks redacts the values) is legacy-proxyless and carries no serving,
   Gateway, Supervisor or proxy name: adopt-live signs a core-tier record.
3. ``bindings_invalid``: the App's live resource bindings are core kinds only
   (a serving-endpoint binding refuses); that they equal the profile's
   reviewed bindings is checked after the binds (``assert_profile_bindings``).
4. ``acl_unreadable``: the App ACL is snapshotted.
5. ``treatment_unverified``: the campaign-treatment boundary is exactly
   converged, read-only, in one reviewed mode (runtime, else quiesce).
6. ``health_unreadable`` / ``health_sha_not_on_main``: the App's authenticated
   health names a commit that resolves in this checkout and is an ancestor of
   the run's main commit.
7. ``tree_drift``: the full-tree digest of the deployment's source folder,
   every file with ``app.yaml`` and ``frontend/dist``, equals that of the
   deployment's own ``/src/`` snapshot (what the App runs).
8. ``app_yaml_differs``: ``app.yaml`` is byte-equal to the repo's at the
   health sha.
9. ``closure_differs``: every other file outside ``frontend/dist`` is the App
   runtime closure at the health sha, byte for byte.
10. ``frontend/dist`` is compared with the build artifact: ``verified`` or
    ``unverified`` (no refusal; an unverified record needs the
    ``restore_unverified_frontend`` ack at any restore).

The record preconditions (no record, the same deployment's adopted record, a
tombstone, or the supersede target) are adopt_live.py's. Every call here is a
GET or a list, the messages name no id, path or value, and nothing stops,
starts, deploys or updates an App.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools.databricks import adopted_source_copy as source
from tools.databricks.app_deployment_state import (
    active_deployment_id,
    deployment_state,
    latest_succeeded,
)
from tools.databricks.app_rollback_record_contract import _text
from tools.databricks.app_rollback_record_v7 import (
    V7RecordError,
    app_acl_digest,
    canonical_app_acl,
    is_agentic_env_name,
    refuse_agentic_resources,
)
from tools.databricks.app_rollback_resource_contract import (
    app_resource_contract_digest,
    validated_app_resource_contract,
)
from tools.databricks.app_start_deployment import env_names, env_names_digest, tree_digest

TREATMENT_ORDER = ("runtime", "quiesce")
REPO = Path(__file__).resolve().parents[2]


class AdoptLiveRefused(RuntimeError):
    """A reviewed adopt-live refusal; nothing was changed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Readers:
    """The reads adopt-live injects, so a test drives every precondition offline."""

    health_sha: Callable[[], str]
    treatment: Callable[[str, str], list[str] | None]
    repo: Path
    main_ref: str
    reference_dist: Path | None = None
    tree: Callable[[Any, str], str] = tree_digest
    read_folder: Callable[[Any, str], dict[str, bytes]] = source.read_folder


@dataclass(frozen=True)
class Facts:
    """What adopt-live proved about the live deployment; ``files`` stay out of every plan."""

    deployment_id: str
    source_code_path: str
    app_client_id: str
    app_scim_id: str
    env_names: tuple[str, ...]
    live_resources: list[dict[str, object]]
    app_acl: list[dict[str, object]]
    treatment_mode: str
    git_sha: str
    tree_sha256: str
    frontend_provenance: str
    frontend_detail: str
    files: Mapping[str, bytes] = field(repr=False, compare=False)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _compute_state(app: object) -> str:
    return (
        _text(getattr(getattr(app, "compute_status", None), "state", None)).split(".")[-1].upper()
    )


def stable_deployment(workspace: Any, *, app_name: str) -> object:
    """Precondition 1: active = latest SUCCEEDED, nothing pending, compute ACTIVE."""

    try:
        app = workspace.apps.get(app_name)
        if _compute_state(app) != "ACTIVE":
            raise RuntimeError("the App compute is not ACTIVE")
        active_id = active_deployment_id(workspace, app_name=app_name)
        latest = latest_succeeded(workspace, app_name=app_name)
    except RuntimeError as exc:
        raise AdoptLiveRefused(
            "deployment_unstable", f"the live deployment is not stable: {exc}"
        ) from exc
    if (
        _text(getattr(latest, "deployment_id", None)) != active_id
        or deployment_state(latest) != "SUCCEEDED"
    ):
        raise AdoptLiveRefused(
            "deployment_unstable", "the active deployment is not the newest succeeded deployment"
        )
    return workspace.apps.get_deployment(app_name, active_id)


def _acl(workspace: Any, app_name: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    try:
        listing = (
            getattr(workspace.apps.get_permissions(app_name), "access_control_list", None) or []
        )
        for entry in listing:
            kinds = [
                (kind, _text(getattr(entry, attr, None)))
                for kind, attr in (
                    ("user", "user_name"),
                    ("group", "group_name"),
                    ("service_principal", "service_principal_name"),
                )
            ]
            named = [(kind, name) for kind, name in kinds if name]
            if len(named) != 1:
                raise RuntimeError("an App ACL entry names no single principal")
            principal = f"{named[0][0]}:{named[0][1]}"
            for permission in getattr(entry, "all_permissions", None) or []:
                rows.append(
                    {
                        "principal": principal,
                        "permission": _text(getattr(permission, "permission_level", None)).split(
                            "."
                        )[-1],
                        "inherited": bool(getattr(permission, "inherited", False)),
                    }
                )
        return canonical_app_acl(rows)
    except (RuntimeError, V7RecordError) as exc:
        raise AdoptLiveRefused(
            "acl_unreadable", f"the App ACL could not be snapshotted: {exc}"
        ) from exc


def _treatment_mode(readers: Readers, principal: str) -> str:
    for mode in TREATMENT_ORDER:
        try:
            drift = readers.treatment(principal, mode)
        except Exception as exc:  # noqa: BLE001 - the class only: a message can carry a name
            raise AdoptLiveRefused(
                "treatment_unverified",
                f"the treatment boundary check failed ({type(exc).__name__})",
            ) from exc
        if drift is None:
            raise AdoptLiveRefused("treatment_unverified", "the governed treatment table is absent")
        if not drift:
            return mode
    raise AdoptLiveRefused(
        "treatment_unverified", "the treatment boundary is converged in neither reviewed mode"
    )


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )


def resolved_health_sha(readers: Readers) -> str:
    """Precondition 6: the health sha resolves to one commit that is an ancestor of main."""

    try:
        reported = readers.health_sha().strip().lower()
    except Exception as exc:  # noqa: BLE001 - the class only
        raise AdoptLiveRefused(
            "health_unreadable", f"the App health could not be read ({type(exc).__name__})"
        ) from exc
    if not 7 <= len(reported) <= 40 or any(c not in "0123456789abcdef" for c in reported):
        raise AdoptLiveRefused("health_unreadable", "the App health names no git sha")
    resolved = _git(readers.repo, "rev-parse", "--verify", "--quiet", f"{reported}^{{commit}}")
    sha = resolved.stdout.strip()
    if resolved.returncode != 0 or len(sha) != 40 or not sha.startswith(reported):
        raise AdoptLiveRefused(
            "health_sha_not_on_main",
            "the health sha resolves to no single commit in this checkout (fetch full history)",
        )
    if _git(readers.repo, "merge-base", "--is-ancestor", sha, readers.main_ref).returncode != 0:
        raise AdoptLiveRefused(
            "health_sha_not_on_main", "the health sha is not an ancestor of main"
        )
    return sha


def gather(workspace: Any, *, app_name: str, readers: Readers) -> Facts:
    """Every precondition above, in order; returns the proven facts or raises AdoptLiveRefused."""

    deployment = stable_deployment(workspace, app_name=app_name)
    deployment_id = _text(getattr(deployment, "deployment_id", None))
    names = tuple(sorted(env_names(deployment)))
    agentic = [name for name in names if is_agentic_env_name(name)]
    if agentic:
        raise AdoptLiveRefused(
            "env_names_not_proxyless",
            f"the deployment carries {len(agentic)} serving, Gateway, Supervisor or proxy env "
            "names; adopt-live signs a legacy-proxyless core-tier deployment only",
        )
    app = workspace.apps.get(app_name)
    client_id = _text(getattr(app, "service_principal_client_id", None))
    scim_id = _text(getattr(app, "service_principal_id", None))
    if not client_id or not scim_id:
        raise AdoptLiveRefused(
            "deployment_unstable", "the App has no complete service-principal identity"
        )
    raw = [
        item.as_dict() if hasattr(item, "as_dict") else item
        for item in (getattr(app, "resources", None) or [])
    ]
    try:
        refuse_agentic_resources(raw)
        live = validated_app_resource_contract(raw)
    except (RuntimeError, V7RecordError) as exc:
        raise AdoptLiveRefused(
            "bindings_invalid", f"the App's live bindings are not core-only: {exc}"
        ) from exc
    acl = _acl(workspace, app_name)
    treatment = _treatment_mode(readers, client_id)
    git_sha = resolved_health_sha(readers)
    folder = _text(getattr(deployment, "source_code_path", None))
    snapshot = _text(
        getattr(getattr(deployment, "deployment_artifacts", None), "source_code_path", None)
    )
    if not folder.startswith("/Workspace/Users/") or "/src/" not in snapshot:
        raise AdoptLiveRefused(
            "tree_drift", "the deployment names no source folder and /src/ snapshot"
        )
    files = readers.read_folder(workspace, folder)
    tree = source.folder_digest(files)
    if readers.tree(workspace, snapshot) != tree:
        raise AdoptLiveRefused(
            "tree_drift",
            "the source folder's full-tree digest is not the deployment snapshot's: the folder "
            "changed after the deployment, so the bytes that run cannot be copied from it",
        )
    if not source.app_yaml_equal(files, repo=readers.repo, commit=git_sha):
        raise AdoptLiveRefused(
            "app_yaml_differs", "app.yaml is not byte-equal to the repo's at the health sha"
        )
    try:
        differences = source.closure_differences(files, repo=readers.repo, commit=git_sha)
    except Exception as exc:  # noqa: BLE001 - an unreadable closure proves nothing
        raise AdoptLiveRefused(
            "closure_differs", f"the closure at the health sha is unreadable: {exc}"
        ) from exc
    if differences:
        raise AdoptLiveRefused(
            "closure_differs", "the backend closure differs: " + "; ".join(differences)
        )
    provenance, detail = source.frontend_provenance(files, readers.reference_dist)
    return Facts(
        deployment_id=deployment_id,
        source_code_path=folder,
        app_client_id=client_id,
        app_scim_id=scim_id,
        env_names=names,
        live_resources=live,
        app_acl=acl,
        treatment_mode=treatment,
        git_sha=git_sha,
        tree_sha256=tree,
        frontend_provenance=provenance,
        frontend_detail=detail,
        files=files,
    )


def plan_facts(facts: Facts) -> dict[str, object]:
    """The facts' share of a canonical plan: digests and reviewed words, never an id or a path."""

    return {
        "deployment_sha256": sha256_text(facts.deployment_id),
        "source_folder_sha256": sha256_text(facts.source_code_path),
        "app_identity_sha256": sha256_text(f"{facts.app_client_id}\0{facts.app_scim_id}"),
        "git_sha": facts.git_sha,
        "env_names_sha256": env_names_digest(facts.env_names),
        "env_name_count": len(facts.env_names),
        "app_resources_sha256": app_resource_contract_digest(facts.live_resources),
        "app_acl_sha256": app_acl_digest(facts.app_acl),
        "treatment_mode": facts.treatment_mode,
        "source_tree_sha256": facts.tree_sha256,
        "file_count": len(facts.files),
        "frontend_provenance": facts.frontend_provenance,
    }


def assert_profile_bindings(facts: Facts, reviewed: list[dict[str, object]]) -> None:
    """Precondition 7, after the binds: the live bindings equal the profile's reviewed ones."""

    try:
        expected = validated_app_resource_contract(reviewed)
    except RuntimeError as exc:
        raise AdoptLiveRefused(
            "bindings_differ", f"the profile's reviewed bindings are invalid: {exc}"
        ) from exc
    if expected != facts.live_resources:
        raise AdoptLiveRefused(
            "bindings_differ", "the App's live bindings are not the profile's reviewed bindings"
        )


# --- the live readers the command line injects ----------------------------------------


def health_reader(workspace: Any, app_name: str, token_env: str) -> Callable[[], str]:
    """The App's authenticated health ``git_sha``, with the bearer ``token_env`` holds."""

    def read() -> str:
        from tools.databricks.app_health_contract import authenticated_app_health

        token = os.environ.get(token_env, "").strip()
        if not token:
            raise RuntimeError("no bearer token")
        url = _text(getattr(workspace.apps.get(app_name), "url", None))
        body = authenticated_app_health(
            workspace, app_name=app_name, base_url=url, bearer_token=token
        )
        return str(body.get("git_sha") or "")

    return read


def treatment_reader(workspace: Any, catalog: str) -> Callable[[str, str], list[str] | None]:
    """The read-only campaign-treatment check (``converge_campaign_treatment_access --check``)."""

    def check(principal: str, mode: str) -> list[str] | None:
        from tools.databricks.campaign_treatment_access_check import check_campaign_treatment_access

        return check_campaign_treatment_access(
            catalog=catalog, principal=principal, mode=mode, workspace=workspace
        )  # type: ignore[arg-type]

    return check


def reviewed_bindings() -> list[dict[str, object]]:
    """The profile's reviewed App bindings: the client target's bundle summary, after the binds."""

    from tools.databricks.app_rollback_resource_contract import reviewed_app_resource_contract

    private = ("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", "MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY")
    env = {name: value for name, value in os.environ.items() if name not in private}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.databricks.bundle_env",
            "summary",
            "-t",
            "client",
            "-o",
            "json",
        ],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AdoptLiveRefused(
            "bindings_differ", f"the bundle summary failed (exit {result.returncode})"
        )
    return reviewed_app_resource_contract(json.loads(result.stdout))
