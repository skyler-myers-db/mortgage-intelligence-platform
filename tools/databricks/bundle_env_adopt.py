"""bundle_env's adopt-plan authority: its wrapper-only flags and the check of one state edit (M2-PR6).

``bundle_env deployment bind|unbind`` admits the governed App binding, and a
non-App key only under an adopt plan (tools/databricks/install_state_adopt.py).
The caller hands bundle_env the plan file, its sha256 and the evidence of the
signed lease it holds, as four wrapper-only flags that never reach the CLI.
The plan file and its digest prove nothing alone, since anyone can digest a
document they wrote, so the edit is admitted only when ``verify_state_edit``
also finds the digest to be the operator's ``MIP_ADOPT_PLAN_ACK`` in this
process's own environment (shell only, never .env.local), the plan naming
this profile's install, and the profile App's signed deployment lease held.
Taking that lease needs the deploy's signing authority, which the bundle CLI
never inherits.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

# The operator's exact digest ack (shell only, never .env.local).
ACK_ENV = "MIP_ADOPT_PLAN_ACK"
# The wrapper-only flags: the plan file, its digest and the held lease's evidence.
FLAGS = ("--adopt-plan", "--adopt-plan-sha256", "--adopt-lease-id", "--adopt-source-git-sha")


def split_authority(args: list[str]) -> tuple[list[str], Any | None, str | None]:
    """``(args without the wrapper flags, install_state_adopt.Authority or None, error)``.

    The wrapper-only flags go together, once each, and never reach the CLI.
    """

    kept: list[str] = []
    found: dict[str, str] = {}
    index = 0
    while index < len(args):
        argument = args[index]
        name, equals, value = argument.partition("=")
        if name in FLAGS:
            if not equals:
                if index + 1 >= len(args):
                    return args, None, f"{name} requires a value"
                value = args[index + 1]
                index += 1
            if name in found or not value.strip():
                return args, None, f"{name} must be given exactly once, with a value"
            found[name] = value
        else:
            kept.append(argument)
        index += 1
    if not found:
        return kept, None, None
    if len(found) != len(FLAGS):
        return args, None, f"the adopt authority flags go together: {', '.join(FLAGS)}"
    from tools.databricks.install_state_adopt import Authority

    plan, sha256, lease_id, source_git_sha = (found[name] for name in FLAGS)
    return kept, Authority(Path(plan), sha256, lease_id, source_git_sha), None


def authorize_state_edit(document_text: str, sha256: str, action: str, key: str, live_id: str | None) -> str | None:
    """``None`` when the acknowledged plan names exactly this bind or unbind; else why not.

    bundle_env calls this before it lets the CLI edit state for a non-App key:
    a bind must be one of an adopt plan's binds; an unbind one of an unadopt
    plan's unbinds, or the inverse of an adopt plan's bind.
    """

    from tools.databricks.adopt_legacy_map import PLAN_SCHEMA
    from tools.databricks.bundle_plan_shape import sha256_hex

    try:
        document = json.loads(document_text)
    except ValueError:
        return "the adopt plan is not JSON"
    if not isinstance(document, dict) or document.get("schema") != PLAN_SCHEMA:
        return "the adopt plan is not the reviewed schema"
    if sha256_hex(document) != sha256:
        return "the adopt plan is not the acknowledged one"
    phase = document.get("phase")
    edits = document.get("bind" if phase == "adopt" else "unbind") or []
    pairs = {(str(edit.get("key")), str(edit.get("id"))) for edit in edits if isinstance(edit, dict)}
    if action == "bind" and phase == "adopt" and (key, str(live_id)) in pairs:
        return None
    if action == "unbind" and key in {pair[0] for pair in pairs}:
        return None
    return f"the acknowledged {phase} plan does not {action} this key"


def verify_state_edit(
    authority: Any,
    action: str,
    key: str,
    live_id: str | None,
    *,
    acknowledged: str,
    inputs: Any,
    lease: Callable[[], None],
) -> str | None:
    """``None`` only for an edit the operator acknowledged, for this install, under the held lease.

    bundle_env's gate for a non-App state edit. A plan file and its digest
    prove nothing alone: anyone can digest a document they wrote. So the
    digest must also be the operator's ``MIP_ADOPT_PLAN_ACK`` (``acknowledged``,
    from bundle_env's own environment), the document must name this
    profile's client, deployer root and legacy root, and the signed deployment
    lease must be held: taking one needs the deploy's signing authority,
    which the bundle CLI never inherits. The lease is asserted last, after
    every local check.
    """

    if not acknowledged.strip() or acknowledged.strip() != authority.sha256:
        return f"{ACK_ENV} is not the adopt plan's sha256"
    try:
        text = Path(authority.plan_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "the adopt plan could not be read"
    refusal = authorize_state_edit(text, authority.sha256, action, key, live_id)
    if refusal is not None:
        return refusal
    document = json.loads(text)
    for name, expected in (
        ("client", inputs.client_key),
        ("deployer_root", inputs.deployer_root),
        ("legacy_root", inputs.legacy_root),
    ):
        if document.get(name) != expected:
            return f"the adopt plan's {name} is not this install's"
    try:
        lease()
    except Exception as exc:  # noqa: BLE001 - the class only: a lease error can name a holder
        return f"the signed deployment lease is not held ({type(exc).__name__})"
    return None


def held_deployment_lease(app_name: str, authority: Any) -> Callable[[], None]:
    """The signed lease's held assertion, with the deployer's own client."""

    from tools.databricks import app_deployment_lease
    from tools.databricks.workspace_auth import deployment_workspace_client

    return app_deployment_lease.held_assertion(
        deployment_workspace_client(),
        app_name=app_name,
        lease_id=authority.lease_id,
        source_git_sha=authority.source_git_sha,
    )


def edit_verifier(
    authority: Any,
    profile: Any,
    *,
    environ: Mapping[str, str] | None = None,
    held_lease: Callable[[str, Any], Callable[[], None]] | None = None,
) -> Callable[[str, str, str | None], str | None]:
    """The check of one adopt-plan state edit against ``profile``: ``None`` admits it, else why not."""

    from tools.databricks import install_state_adopt as adopt

    source = os.environ if environ is None else environ
    assert_lease = held_lease or held_deployment_lease

    def verify(action: str, key: str, live_id: str | None) -> str | None:
        return verify_state_edit(
            authority,
            action,
            key,
            live_id,
            acknowledged=str(source.get(ACK_ENV) or ""),
            inputs=adopt.inputs_from_profile(profile),
            lease=lambda: assert_lease(profile.app_name, authority)(),
        )

    return verify
