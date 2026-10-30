"""Pre-stop: the accepted-exposure matchers in the Genie group-grant clause (M2-PR4).

The pre-stop Genie clause (``deploy_prestop_probes.base_verdict``) lists every
Genie group grant that reaches the App, agent-runtime or verifier service
principal. A grant the client profile accepts for an identity
(``estate.accepted_exposure``) no longer refuses for that identity, exactly as
the post-stop audit (``agent_runtime_access``) judges the same grant with the
same matcher: both build the tuple with ``estate.genie_exposure`` from the
same ACL entry. The verifier has no matcher, so a grant reaching it still
refuses whatever the profile says, and the governed space is never relaxed.

With no entry for the App or the agent-runtime nothing extra is read; with
one, the probe reads the visible spaces' titles once (an entry names its
space by id and title, and a title is never printed) and the workspace id
(the space's binding).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from tools.databricks.agent_runtime_access import _genie_spaces
from tools.databricks.estate.accepted_exposure import (
    ExposureMatcher,
    ExposurePolicy,
    ExposureRefusal,
    load_policy,
)
from tools.databricks.estate.genie_exposure import genie_exposures
from tools.databricks.prestop_genie_grants import GenieGroupGrant
from tools.databricks.prestop_refusals import ACCEPTED_EXPOSURE, PrestopRefusal

_REMEDY = (
    "Fix MIP_ACCEPTED_EXPOSURE_JSON in the client profile (each line names the entry "
    "and the rule), or acknowledge its reviewed plan for this run, then re-run."
)


def _refusal(exc: ExposureRefusal) -> PrestopRefusal:
    return PrestopRefusal(
        ACCEPTED_EXPOSURE,
        "the accepted-exposure policy the post-stop audits load refuses: "
        + "; ".join(exc.lines)
        + ". "
        + _REMEDY,
    )


@dataclass(frozen=True)
class PrestopExposure:
    """The policy the post-stop audits will load, and the governed space's title."""

    policy: ExposurePolicy
    governed_title: str

    def filter_group_grants(
        self,
        workspace: Any,
        grants: list[GenieGroupGrant],
        *,
        labels: Mapping[str, str],
    ) -> tuple[list[GenieGroupGrant], list[str], list[PrestopRefusal]]:
        """Drop each principal whose matcher accepts every exposure of a grant."""

        matchers: dict[str, ExposureMatcher] = {
            label: self.policy.matcher(identity) for label, identity in labels.items()
        }
        active = {label: matcher for label, matcher in matchers.items() if matcher.active}
        if not active or not grants:
            return grants, [], []
        from tools.databricks.agent_runtime_uc_baseline import authoritative_workspace_id

        titles = _genie_spaces(workspace)
        workspace_id = authoritative_workspace_id(workspace)
        title = self.governed_title.strip()
        governed = {space for space, name in titles.items() if title and name == title}
        try:
            for matcher in active.values():
                matcher.assert_not_governed(space_ids=governed, space_names=[title])
        except ExposureRefusal as exc:
            return grants, [], [_refusal(exc)]
        kept: list[GenieGroupGrant] = []
        notes: list[str] = []
        for grant in grants:
            exposures = genie_exposures(
                grant.granted,
                space_id=grant.space_id,
                title=titles.get(grant.space_id, ""),
                grantee=grant.group,
                workspace_id=workspace_id,
            )
            cleared = {
                label
                for label in grant.principals
                if label in active and active[label].accepts_all(exposures)
            }
            if cleared:
                notes.append(
                    f"accepted exposure: Genie space {grant.space_id}: group {grant.group!r} "
                    f"for {', '.join(sorted(labels[label] for label in cleared))}"
                )
            remaining = tuple(label for label in grant.principals if label not in cleared)
            if remaining:
                kept.append(replace(grant, principals=remaining))
        return kept, notes, []


def load_prestop_exposure(
    *, verifier_application_id: str, governed_title: str
) -> tuple[PrestopExposure | None, list[PrestopRefusal]]:
    """Load the policy as the post-stop audits will; a refusal keeps every grant refused."""

    try:
        policy = load_policy(verifier_application_ids=[verifier_application_id])
    except ExposureRefusal as exc:
        return None, [_refusal(exc)]
    return PrestopExposure(policy=policy, governed_title=governed_title), []


__all__ = ["PrestopExposure", "load_prestop_exposure"]
