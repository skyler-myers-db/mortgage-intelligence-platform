"""bundle_env's adopt-plan authority: its wrapper-only flags and the check of one state edit (M2-PR6).

``bundle_env deployment bind|unbind`` admits the governed App binding, and a
non-App key only under an adopt plan (tools/databricks/install_state_adopt.py).
The caller hands bundle_env the plan file, its sha256 and the evidence of the
signed lease it holds, as four wrapper-only flags that never reach the CLI.
The plan file and its digest prove nothing alone, since anyone can digest a
document they wrote, so the edit is admitted only when
``install_state_adopt.verify_state_edit`` also finds the digest to be the
operator's ``MIP_ADOPT_PLAN_ACK`` in this process's own environment (shell
only, never .env.local), the plan naming this profile's install, and the
profile App's signed deployment lease held. Taking that lease needs the
deploy's signing authority, which the bundle CLI never inherits.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

# install_state_adopt.AUTHORITY_FLAGS: the plan file, its digest and the held lease.
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
        return adopt.verify_state_edit(
            authority,
            action,
            key,
            live_id,
            acknowledged=str(source.get(adopt.ACK_ENV) or ""),
            inputs=adopt.inputs_from_profile(profile),
            lease=lambda: assert_lease(profile.app_name, authority)(),
        )

    return verify
