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
one, the group-grant filter reads the visible spaces' titles (an entry names
its space by id and title, and a title is never printed) and the workspace id
(the space's binding); with a Genie entry the governed check below reads the
titles once more.

Every consumer judges expiry at the moment it loads, and the post-stop audits
load long after this probe. So an entry expiring within ``RUN_MARGIN`` refuses
here: it would pass now and refuse as expired with the App already stopped.

The governed space is judged twice. The group-grant filter knows it only by
the configured title, which a renamed space no longer carries. So once the
suite resolves the space the provisioner will use, by its pin
(``prestop_probe_suite.governed_genie_space``), ``governed_refusals`` refuses
an entry naming that id or its live title, as the post-stop audits do with the
id they audit, whether or not a group grant reaches an audited identity.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from tools.databricks.agent_runtime_access import _genie_spaces
from tools.databricks.estate.accepted_exposure import (
    GENIE_SPACE,
    ExposureMatcher,
    ExposurePolicy,
    ExposureRefusal,
    load_policy,
)
from tools.databricks.estate.genie_exposure import genie_exposures
from tools.databricks.prestop_genie_grants import GenieGroupGrant
from tools.databricks.prestop_refusals import ACCEPTED_EXPOSURE, PrestopRefusal

# The reviewed bound on one deploy run, from this probe to its last post-stop audit.
RUN_MARGIN = timedelta(hours=24)
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

    def governed_refusals(self, workspace: Any, *, space_id: str) -> list[PrestopRefusal]:
        """Refuse an entry naming the pinned governed space, by its id or its live title.

        ``audit_global_genie_access`` refuses the same entry after the stop
        (``assert_not_governed`` on the id it audits and that space's title).
        With no Genie entry, or no governed space yet (step 0a creates it),
        nothing is read.
        """

        genie = any(entry.exposure.securable_type == GENIE_SPACE for entry in self.policy.entries)
        if not genie or not space_id:
            return []
        title = _genie_spaces(workspace).get(space_id, "")
        try:
            self.policy.assert_not_governed(space_ids=[space_id], space_names=[title])
        except ExposureRefusal as exc:
            return [_refusal(exc)]
        return []


def expiring_lines(policy: ExposurePolicy, *, margin: timedelta = RUN_MARGIN) -> list[str]:
    """Entries still valid now that expire before the run's post-stop audits may judge them."""

    hours = int(margin.total_seconds() // 3600)
    return [
        f"entry {entry.index}: expires within the {hours} h deploy-run margin, so the post-stop "
        "audits would judge it expired with the App stopped; renew its expires_at and acknowledge "
        "the new plan"
        for entry in policy.entries
        if policy.now < entry.expires_at <= policy.now + margin
    ]


def load_prestop_exposure(
    *, verifier_application_id: str, governed_title: str, now: datetime | None = None
) -> tuple[PrestopExposure | None, list[PrestopRefusal]]:
    """Load the policy as the post-stop audits will; a refusal keeps every grant refused."""

    try:
        policy = load_policy(verifier_application_ids=[verifier_application_id], now=now)
    except ExposureRefusal as exc:
        return None, [_refusal(exc)]
    expiring = expiring_lines(policy)
    if expiring:
        return None, [_refusal(ExposureRefusal(expiring))]
    return PrestopExposure(policy=policy, governed_title=governed_title), []


__all__ = ["RUN_MARGIN", "PrestopExposure", "expiring_lines", "load_prestop_exposure"]
