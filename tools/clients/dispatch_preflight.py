"""The deploy job's dispatch preflight, before scripts/deploy.sh (M1-PR7, plan §4.5).

deploy-dev.yml runs this after the resolve-client-profile action and the
OIDC proof, with the job's ``toJSON(vars)`` and ``toJSON(secrets)`` in step
env and the dispatch inputs under ``MIP_DISPATCH_*``. It resolves the client
profile again (registering its masks first) and decides, for ``--phase``:

1. **workflow**: a ``workflow_dispatch`` of this repository's
   ``.github/workflows/deploy-dev.yml`` on ``refs/heads/main``;
2. **review mode**: ``single_maintainer_audited`` (the only mode built) needs
   the break-glass acknowledgement; approval provenance is "not applicable"
   in that mode (tools/clients/dispatch_protections.approval_provenance);
3. **trust-root chain**: deferred with the owner attestation layer (M1-PR5);
   nothing here pretends to verify a signature or a chain;
4. **profile ack** (the input ``owner_attestation``): every mutating phase
   needs the exact ``profile`` ack over the client and its profile digest
   (tools/clients/dispatch_acks.py); ``plan`` prints it;
5. **operation acks**: the closed ``purpose=<sha256>`` pairs, each exported
   for this job under the name its own tool recomputes it from;
6. **environment protections** (spike S8 owed): verified, drift, or
   ``environment_protections_unreadable``. A plan records drift or
   unreadable; a mutating phase refuses drift, and refuses unreadable unless
   the dispatch carries the exact ``protections_unverified`` ack;
7. **tree scan**: no resolved identifying value in any tracked file
   (tools/clients/tree_scan.py); a plan records the hits, a mutating phase
   refuses them. Only the closed set of group names the repository itself
   defines, each in its own field (``<prefix>-admin`` as the admin group,
   tree_scan.CONVENTION_NAMES), is reported and never refused;
8. **expected active deployment**: ``expected_active_deployment_sha256``, when
   given, reaches ``--phase release`` as ``MIP_EXPECTED_ACTIVE_DEPLOYMENT_SHA256``.

Everything it prints, and everything it appends to the step summary, is a
code, a count, a digest, a field name, a tracked path or the public client
key: never a value. Exit 0, or 2 on a refusal (every refusal is collected).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from tools.clients import dispatch_acks as acks
from tools.clients import dispatch_protections as protections
from tools.clients import profile as views
from tools.clients import tree_scan
from tools.clients.environment import from_github, read_context, resolve
from tools.clients.errors import ProfileRefusal
from tools.clients.redact import emit_mask

WORKFLOW = ".github/workflows/deploy-dev.yml"
MAIN = "refs/heads/main"
TRUST_ROOT = "deferred (M1-PR5)"
EXPECTED_DEPLOYMENT_ENV = "MIP_EXPECTED_ACTIVE_DEPLOYMENT_SHA256"
PROTECTIONS_ENV = "MIP_DISPATCH_ENVIRONMENT_PROTECTIONS"
_EXPECTED_DEPLOYMENT = re.compile(r"[0-9a-f]{64}|none")


@dataclass
class Verdict:
    phase: str
    facts: list[str] = field(default_factory=list)
    refusals: list[str] = field(default_factory=list)
    exports: dict[str, str] = field(default_factory=dict)

    @property
    def mutating(self) -> bool:
        return self.phase in acks.MUTATING_PHASES

    def refuse(self, code: str, reason: str) -> None:
        self.refusals.append(f"{code}: {reason}")

    def record_or_refuse(self, code: str, reason: str) -> None:
        """A plan records what a mutating phase refuses (plan §4.5: the plan collects)."""

        if self.mutating:
            self.refuse(code, reason)
        else:
            self.facts.append(f"recorded for the plan: {code}: {reason}")


def _workflow(verdict: Verdict, environ: Mapping[str, str]) -> None:
    repository = environ.get("GITHUB_REPOSITORY", "")
    if environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        verdict.refuse("workflow_event", "only a manual workflow_dispatch reaches a client environment")
    if environ.get("GITHUB_REF") != MAIN or environ.get("GITHUB_WORKFLOW_REF") != f"{repository}/{WORKFLOW}@{MAIN}":
        verdict.refuse("workflow_ref", f"only {WORKFLOW} on main deploys")


def _protections(verdict: Verdict, environ: Mapping[str, str], client: str, environment: str, mode: str) -> None:
    try:
        session = protections.from_environ(environ)
    except protections.Unreadable as exc:
        result = protections.Result(protections.UNREADABLE, cause=str(exc))
    else:
        result = protections.read(session, environment, mode, environ.get("GITHUB_ACTOR_ID", ""))
    verdict.exports[PROTECTIONS_ENV] = result.code
    if result.code == protections.VERIFIED:
        verdict.facts.append(f"environment protections: {result.code} ({mode})")
        return
    if result.code == protections.DRIFT:
        verdict.record_or_refuse(result.code, "; ".join(result.lines))
        return
    expected = acks.protections_ack(client, environment, environ.get("GITHUB_SHA", ""))
    given = environ.get("MIP_DISPATCH_PROTECTIONS_ACK", "")
    verdict.facts.append(f"environment protections: {result.code} ({result.cause}; spike S8 owed)")
    if verdict.mutating and acks.same_ack(given, expected):
        verdict.facts.append("protections_unverified ack: exact for this client, environment and commit")
    else:
        verdict.record_or_refuse(
            result.code,
            f"GITHUB_TOKEN could not read the environment's protection rules; to run anyway, dispatch "
            f"with protections_unverified_ack={expected} (purpose protections_unverified, this commit only)",
        )


def decide(
    *,
    phase: str,
    environment: str,
    vars_source: str,
    secrets_source: str,
    environ: Mapping[str, str],
    repo: Path,
) -> Verdict:
    verdict = Verdict(phase)
    if phase not in acks.PHASES:
        verdict.refuse("phase", f"must be one of {', '.join(sorted(acks.PHASES))}")
        return verdict
    try:
        profile = resolve(from_github(read_context(vars_source), read_context(secrets_source), environment=environment))
    except ProfileRefusal as refusal:
        verdict.refusals.extend(f"profile: {line}" for line in refusal.lines)
        return verdict
    for value in (*views.masks(profile), *views.derived_masks(profile)):
        emit_mask(value)
    digest = views.digest(profile)
    verdict.facts.append(f"client {profile.client}: {profile.install_class}, tier {profile.agentic_tier}, profile digest {digest}")
    _workflow(verdict, environ)
    if profile.review_mode == "single_maintainer_audited" and environ.get("MIP_DISPATCH_BREAK_GLASS") != "true":
        verdict.refuse("break_glass", "single_maintainer_audited needs acknowledge_single_maintainer_break_glass=true")
    verdict.facts.append(f"approval provenance: {protections.approval_provenance(profile.review_mode)}")
    verdict.facts.append(f"trust-root chain: {TRUST_ROOT}")
    expected_profile_ack = acks.profile_ack(profile.client, digest)
    if not verdict.mutating:
        verdict.facts.append(f"profile ack (purpose profile) a mutating phase needs: owner_attestation={expected_profile_ack}")
    elif acks.same_ack(environ.get("MIP_DISPATCH_PROFILE_ACK", ""), expected_profile_ack):
        verdict.facts.append("profile ack: exact for this profile digest")
    else:
        verdict.refuse("profile_ack", f"dispatch with owner_attestation={expected_profile_ack} after reviewing this profile")
    try:
        verdict.exports.update(acks.operation_exports(environ.get("MIP_DISPATCH_OPERATION_ACKS", ""), phase))
    except ProfileRefusal as refusal:
        verdict.refusals.extend(refusal.lines)
    operation = sorted(name for name in verdict.exports if name != PROTECTIONS_ENV)
    verdict.facts.append(f"operation acks: {', '.join(operation) or 'none'}")
    expected_deployment = environ.get("MIP_DISPATCH_EXPECTED_ACTIVE_DEPLOYMENT_SHA256", "").strip()
    if expected_deployment:
        if _EXPECTED_DEPLOYMENT.fullmatch(expected_deployment) is None or phase != "release":
            verdict.refuse("expected_active_deployment_sha256", "64 lowercase hex or none, read by --phase release only")
        else:
            verdict.exports[EXPECTED_DEPLOYMENT_ENV] = expected_deployment
    _protections(verdict, environ, profile.client, environment, profile.review_mode)
    _tree_scan(verdict, tree_scan.scan(repo, tree_scan.needles(profile)))
    return verdict


def _tree_scan(verdict: Verdict, hits: Sequence[tree_scan.Hit]) -> None:
    """Counts distinct files; the clean-tree fact only when nothing at all was found."""

    own = [hit for hit in hits if not hit.conventional]
    conventional = [hit for hit in hits if hit.conventional]
    if not hits:
        verdict.facts.append("tree scan: no tracked file holds a resolved identifying value")
    if own:
        listed = ", ".join(f"{hit.field} in {hit.path}" for hit in own[:20])
        files = len({hit.path for hit in own})
        verdict.record_or_refuse("tree_scan", f"{files} tracked files hold a resolved identifying value ({listed})")
    if conventional:
        listed = ", ".join(sorted({hit.field for hit in conventional}))
        files = len({hit.path for hit in conventional})
        verdict.facts.append(
            f"tree scan: {files} tracked files name the repository's own convention name of {listed} (reported, not refused)"
        )


def _report(verdict: Verdict) -> list[str]:
    lines = [f"### deploy-dev dispatch preflight (--phase {verdict.phase})", ""]
    lines += [f"- {fact}" for fact in verdict.facts]
    lines += [f"- REFUSED {refusal}" for refusal in verdict.refusals]
    lines.append(f"- verdict: {'refused' if verdict.refusals else 'passed'}")
    return lines


def main(argv: Sequence[str] | None = None, *, environ: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.clients.dispatch_preflight", description=__doc__.splitlines()[0])
    parser.add_argument("--phase", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--vars-json", required=True)
    parser.add_argument("--secrets-json", required=True)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--github-env", type=Path)
    parser.add_argument("--step-summary", type=Path)
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ
    try:
        verdict = decide(
            phase=args.phase,
            environment=args.environment,
            vars_source=args.vars_json,
            secrets_source=args.secrets_json,
            environ=environ,
            repo=args.repo,
        )
    except Exception as exc:  # the class only: a message could carry a value
        print(f"dispatch preflight failed: {type(exc).__name__}", file=sys.stderr)
        return 2
    lines = _report(verdict)
    print("\n".join(lines))
    if args.step_summary is not None:
        with args.step_summary.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n\n")
    if verdict.refusals:
        return 2
    if args.github_env is not None:
        with args.github_env.open("a", encoding="utf-8") as handle:
            handle.writelines(f"{name}={value}\n" for name, value in sorted(verdict.exports.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
