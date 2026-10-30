"""A client's GitHub Environment protections, per review mode (plan §4.2, §4.10).

Both environments, ``<client>`` (the deploy) and ``<client>-validate`` (the
validator's federation subject), carry the same protections:

* the review mode's required reviewers and ``prevent_self_review``;
* ``can_admins_bypass=false``;
* a deployment branch policy of exactly ``{main}``: custom branch policies
  on, protected branches off, and one policy, the branch ``main``.

**Review modes.** ``MIP_REVIEW_MODE`` (tools/clients/schema.py) names one.

* ``single_maintainer_audited``, the only mode built: the one maintainer,
  the GitHub user signed in to the tool's fresh session, is the one required
  reviewer, and ``prevent_self_review`` is false because that maintainer
  approves their own runs. The approval is an audit trail, not an
  independent review; the plan says so plainly (docs/clients/github-environment.md).
* ``independent`` is modelled and refused: it needs a second, human-only
  GitHub identity, ``prevent_self_review=true`` and the deferred owner
  attestation layer.

``environments`` and ``branch_policies`` read the live state through a
session (tools/clients/github_session.py); ``drift`` compares it with the
expectation and names fields only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from tools.clients.errors import BootstrapRefusal

VALIDATE_SUFFIX = "-validate"
MAIN_POLICY = ("main", "branch")
_PAGE = 100


@dataclass(frozen=True)
class ReviewMode:
    name: str
    built: bool
    prevent_self_review: bool
    refusal: str = ""


REVIEW_MODES: dict[str, ReviewMode] = {
    "single_maintainer_audited": ReviewMode("single_maintainer_audited", built=True, prevent_self_review=False),
    "independent": ReviewMode(
        "independent",
        built=False,
        prevent_self_review=True,
        refusal=(
            "independent needs a second, human-only GitHub identity and the deferred owner "
            "attestation layer; it is not built, use single_maintainer_audited"
        ),
    ),
}


def review_mode(name: str | None) -> ReviewMode:
    """The built review mode ``name`` names, or a value-free refusal."""

    mode = REVIEW_MODES.get(name or "")
    if mode is None:
        raise BootstrapRefusal([f"MIP_REVIEW_MODE: must be one of {', '.join(sorted(REVIEW_MODES))}"])
    if not mode.built:
        raise BootstrapRefusal([f"MIP_REVIEW_MODE: {mode.refusal}"])
    return mode


def environment_names(client: str) -> tuple[str, str]:
    return client, f"{client}{VALIDATE_SUFFIX}"


@dataclass(frozen=True)
class Protections:
    """What one environment's protection rules say, reduced to the reviewed fields."""

    reviewers: tuple[tuple[str, int], ...]
    prevent_self_review: bool
    can_admins_bypass: bool
    protected_branches: bool
    custom_branch_policies: bool

    def body(self) -> dict[str, Any]:
        """The ``PUT /environments/{name}`` body that sets exactly these."""

        return {
            "reviewers": [{"type": kind, "id": ident} for kind, ident in self.reviewers],
            "prevent_self_review": self.prevent_self_review,
            "can_admins_bypass": self.can_admins_bypass,
            "deployment_branch_policy": {
                "protected_branches": self.protected_branches,
                "custom_branch_policies": self.custom_branch_policies,
            },
        }


def expected(mode: ReviewMode, maintainer_id: int) -> Protections:
    """The protections ``mode`` requires, with the signed-in maintainer as reviewer."""

    if not mode.built:
        raise BootstrapRefusal([f"MIP_REVIEW_MODE: {mode.refusal}"])
    return Protections(
        reviewers=(("User", maintainer_id),),
        prevent_self_review=mode.prevent_self_review,
        can_admins_bypass=False,
        protected_branches=False,
        custom_branch_policies=True,
    )


def _malformed(what: str) -> BootstrapRefusal:
    return BootstrapRefusal([f"github: the {what} listing is malformed"])


def observed(environment: Mapping[str, Any]) -> Protections:
    """The reviewed fields of one environment object from the environments listing."""

    reviewers: list[tuple[str, int]] = []
    prevent = False
    for rule in environment.get("protection_rules") or ():
        if not isinstance(rule, Mapping):
            raise _malformed("environment")
        if rule.get("type") != "required_reviewers":
            continue
        prevent = rule.get("prevent_self_review") is True
        for entry in rule.get("reviewers") or ():
            reviewer = entry.get("reviewer") if isinstance(entry, Mapping) else None
            if not isinstance(reviewer, Mapping) or not isinstance(reviewer.get("id"), int):
                raise _malformed("environment reviewer")
            reviewers.append((str(entry.get("type")), int(reviewer["id"])))
    policy = environment.get("deployment_branch_policy")
    policy = policy if isinstance(policy, Mapping) else {}
    return Protections(
        reviewers=tuple(sorted(reviewers)),
        prevent_self_review=prevent,
        # GitHub's default is true; only an explicit false counts.
        can_admins_bypass=environment.get("can_admins_bypass") is not False,
        protected_branches=policy.get("protected_branches") is True,
        custom_branch_policies=policy.get("custom_branch_policies") is True,
    )


def drift(want: Protections, have: Protections) -> list[str]:
    """Each reviewed field that differs, by name only."""

    fields = ("reviewers", "prevent_self_review", "can_admins_bypass", "protected_branches", "custom_branch_policies")
    return [name for name in fields if getattr(want, name) != getattr(have, name)]


@dataclass(frozen=True)
class BranchPolicy:
    policy_id: int
    name: str
    kind: str

    @property
    def is_main(self) -> bool:
        return (self.name, self.kind) == MAIN_POLICY


def environments(session: Any) -> dict[str, Mapping[str, Any]]:
    """Every environment of the session's repository, by name (one page of 100 at most)."""

    listing = session.api("GET", session.repo_path(f"environments?per_page={_PAGE}"))
    if listing is None:
        return {}
    found = listing.get("environments") if isinstance(listing, Mapping) else None
    total = listing.get("total_count") if isinstance(listing, Mapping) else None
    if not isinstance(found, list) or not isinstance(total, int):
        raise _malformed("environments")
    if total > len(found):
        raise BootstrapRefusal([f"github: the repository has more than {_PAGE} environments; refusing to guess"])
    named: dict[str, Mapping[str, Any]] = {}
    for item in found:
        if not isinstance(item, Mapping) or not isinstance(item.get("name"), str):
            raise _malformed("environments")
        named[item["name"]] = item
    return named


def branch_policies(session: Any, environment: str) -> tuple[BranchPolicy, ...]:
    """The environment's custom deployment branch policies."""

    path = session.repo_path("environments", environment, f"deployment-branch-policies?per_page={_PAGE}")
    listing = session.api("GET", path)
    found = listing.get("branch_policies") if isinstance(listing, Mapping) else None
    total = listing.get("total_count") if isinstance(listing, Mapping) else None
    if not isinstance(found, list) or not isinstance(total, int):
        raise _malformed("branch policy")
    if total > len(found):
        raise BootstrapRefusal([f"github: {environment} has more than {_PAGE} branch policies; refusing to guess"])
    policies = []
    for item in found:
        if not isinstance(item, Mapping) or not isinstance(item.get("id"), int) or not isinstance(item.get("name"), str):
            raise _malformed("branch policy")
        policies.append(BranchPolicy(int(item["id"]), str(item["name"]), str(item.get("type") or "branch")))
    return tuple(sorted(policies, key=lambda policy: policy.policy_id))


def protection_refusals(
    session: Any, environment: str, mode: ReviewMode, maintainer_id: int, *, listing: Mapping[str, Any] | None = None
) -> list[str]:
    """Why ``environment`` is not protected exactly as ``mode`` requires (empty when it is)."""

    found = (environments(session) if listing is None else listing).get(environment)
    if found is None:
        return [f"github: the {environment} environment does not exist"]
    lines = [f"github: {environment} {name} differs from {mode.name}" for name in drift(expected(mode, maintainer_id), observed(found))]
    if not lines:
        policies = branch_policies(session, environment)
        if [policy for policy in policies if policy.is_main] != list(policies) or len(policies) != 1:
            lines.append(f"github: {environment} deployment branch policy is not exactly {{main}}")
    return lines
