"""The exact digest acks a deploy-dev.yml dispatch carries (M1-PR7).

The owner hardware-key attestation layer (plan §4.2, M1-PR5) is deferred, so
each attestation the plan names is an exact digest ack instead: a tool prints
the sha256 of a canonical read-only body that names its purpose, and a
mutating run must be handed that digest for one run (a workflow input here,
never a ``.env.local`` line). The body is recomputed immediately before it is
relied on, and any difference refuses. An ack for one purpose never satisfies
another: each body names its purpose and each digest is domain-separated, the
scheme the data-ledger and first-install acks use
(tools/databricks/job_phase_runner.py, first_install_bootstrap.py).

Two purposes are new here:

* ``profile`` (the dispatch input ``owner_attestation``): the client and the
  profile digest (tools/clients/profile.digest, over the public and
  identifying fields). Every mutating phase needs it; ``--phase plan``
  prints it.
* ``protections_unverified``: the client, the GitHub Environment and the
  run's head sha. Spike S8 is owed: GITHUB_TOKEN may be unable to read the
  environment's protection rules. When it cannot, every mutating phase
  refuses unless the dispatch carries this ack.

The operation acks keep their own purposes and their own tools: the dispatch
input ``operation_acks`` carries ``purpose=<sha256>`` pairs from the closed
map ``OPERATION_ACKS``, each admitted only in the phases whose run reads it,
and the deploy job exports each under the name its tool recomputes it from.
The input is the only carrier: ``deploy-dev.yml``'s input set is pinned at
ten (tests/unit/test_deploy_dev_workflow_contract.py), and no ack is ever a
profile field.

Each entry has a kind. An ``ack`` is a purpose digest its tool recomputes
from a canonical body and compares exactly. The one ``selector``,
``supersedes_deployment`` (``MIP_ADOPT_LIVE_SUPERSEDE``), is no ack: it is the
sha256 of the adopted record's deployment id, which picks adopt-live's
supersede mode and which the ``supersede`` ack's plan commits to. It is a
digest, never a switch: ``yes``, ``true`` or ``1`` fail the entry shape.

The carrier checks each entry alone and pairs none. A pair the adopt phases
need is refused one layer later, still before the lease and with nothing
changed: the bind's ``adopt_plan`` without adopt-live's own ack
(scripts/lib/deploy_adopt_phase_lifecycle.sh, ``run_adopt_live_plan``), and a
selector that names no adopted record (adopt-live's ``supersede_mismatch``).
An exported ack its run does not read is never a mutation's authority.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass

from tools.clients.errors import ProfileRefusal
from tools.databricks.bundle_plan_shape import canonical_json

PROFILE_PURPOSE = "profile"
PROTECTIONS_PURPOSE = "protections_unverified"
MUTATING_PHASES = frozenset({"release", "data", "adopt", "unadopt"})
PHASES = frozenset({"plan", *MUTATING_PHASES})
_DIGEST = re.compile(r"[0-9a-f]{64}")
_SHA = re.compile(r"[0-9a-f]{40}")
_PAIR = re.compile(r"([a-z_]+)=([0-9a-f]{64})")


KINDS = frozenset({"ack", "selector"})


@dataclass(frozen=True)
class OperationAck:
    """One reviewed purpose: the env name its tool reads, the phases that read it, and its kind."""

    env: str
    phases: frozenset[str]
    kind: str = "ack"

    def __post_init__(self) -> None:
        if self.kind not in KINDS or not self.phases <= PHASES:
            raise ValueError("an operation ack has an unreviewed kind or phase")


# Every phase listed reads the env in its own deploy.sh path (the readers are
# pinned in tests/unit/test_dispatch_preflight.py). genie_pin is never read by
# data, which never pins: an ack there could only hide a lost pin.
OPERATION_ACKS: dict[str, OperationAck] = {
    "adopt_plan": OperationAck("MIP_ADOPT_PLAN_ACK", frozenset({"adopt", "unadopt"})),
    "adopt_live": OperationAck("MIP_ACK_ADOPT_LIVE", frozenset({"adopt"})),
    "supersede": OperationAck("MIP_ACK_SUPERSEDE", frozenset({"adopt"})),
    "supersedes_deployment": OperationAck("MIP_ADOPT_LIVE_SUPERSEDE", frozenset({"adopt"}), kind="selector"),
    "unadopt": OperationAck("MIP_ACK_UNADOPT", frozenset({"unadopt"})),
    # A plan reads it at its pre-lease window plan (read-only), so the plan
    # that predicted a window can complete; a release still needs it exact.
    "quiesce_window": OperationAck("MIP_ACK_QUIESCE_WINDOW", frozenset({"plan", "release"})),
    "database_instance_update": OperationAck("MIP_ACK_DATABASE_INSTANCE_UPDATE", frozenset({"plan", "release"})),
    "genie_pin": OperationAck("MIP_GENIE_PIN_ACK", frozenset({"plan", "release", "adopt", "unadopt"})),
    "accepted_exposure_plan": OperationAck(
        "MIP_ACK_ACCEPTED_EXPOSURE_PLAN", frozenset({"plan", "release", "adopt", "unadopt"})
    ),
    "data_ledger_supersede": OperationAck("MIP_ACK_DATA_LEDGER_SUPERSEDE", frozenset({"data"})),
    "data_ledger_rerun": OperationAck("MIP_ACK_DATA_LEDGER_RERUN", frozenset({"data"})),
    # The metastore owner's attestation of the App's metastore privileges (spike
    # S0(b′), tools/databricks/metastore_app_grants.py): read by the pre-stop
    # probes (plan, adopt, unadopt, release), adopt-live's treatment fact
    # (adopt) and the release's treatment converger, never by the data phase.
    "metastore_app_grants": OperationAck(
        "MIP_ACK_METASTORE_APP_GRANTS", frozenset({"plan", "adopt", "unadopt", "release"})
    ),
}


def _digest(domain: bytes, body: Mapping[str, str]) -> str:
    return hashlib.sha256(domain + b"\0" + canonical_json(dict(body)).encode("ascii")).hexdigest()


def profile_body(client: str, profile_digest: str) -> dict[str, str]:
    return {"purpose": PROFILE_PURPOSE, "client": client, "profile_digest": profile_digest}


def profile_ack(client: str, profile_digest: str) -> str:
    """The exact ``owner_attestation`` a mutating phase of ``client`` needs."""

    if _DIGEST.fullmatch(profile_digest) is None:
        raise ProfileRefusal(["profile digest: not a sha256 hex digest"])
    return _digest(b"mip-dispatch-profile", profile_body(client, profile_digest))


def protections_body(client: str, environment: str, head_sha: str) -> dict[str, str]:
    return {"purpose": PROTECTIONS_PURPOSE, "client": client, "environment": environment, "head_sha": head_sha}


def protections_ack(client: str, environment: str, head_sha: str) -> str:
    """The exact ``protections_unverified`` ack: this client, this environment, this run's commit."""

    if _SHA.fullmatch(head_sha) is None:
        raise ProfileRefusal(["GITHUB_SHA: the run's head is not a 40-hex commit"])
    return _digest(b"mip-dispatch-protections-unverified", protections_body(client, environment.casefold(), head_sha))


def same_ack(given: str, expected: str) -> bool:
    return _DIGEST.fullmatch(given.strip()) is not None and given.strip() == expected


def operation_exports(text: str, phase: str) -> dict[str, str]:
    """The env exports of ``operation_acks`` for ``phase``; a value-free refusal otherwise.

    Pairs are ``purpose=<64 hex>``, separated by commas or whitespace, each
    purpose at most once and each admitted in ``phase``.
    """

    if phase not in PHASES:
        raise ProfileRefusal([f"phase: must be one of {', '.join(sorted(PHASES))}"])
    exports: dict[str, str] = {}
    refusals: list[str] = []
    for token in (part for part in re.split(r"[\s,]+", text.strip()) if part):
        match = _PAIR.fullmatch(token)
        if match is None:
            refusals.append("operation_acks: each entry is purpose=<64 lowercase hex>")
            continue
        purpose, value = match.groups()
        ack = OPERATION_ACKS.get(purpose)
        if ack is None:
            refusals.append(f"operation_acks: {purpose} is not a reviewed purpose ({', '.join(sorted(OPERATION_ACKS))})")
        elif phase not in ack.phases:
            refusals.append(f"operation_acks: {purpose} is never read by --phase {phase}")
        elif ack.env in exports:
            refusals.append(f"operation_acks: {purpose} is given twice")
        else:
            exports[ack.env] = value
    if refusals:
        raise ProfileRefusal(sorted(set(refusals)))
    return exports
