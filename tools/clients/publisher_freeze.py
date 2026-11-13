"""deploy-dev.yml's ``freeze`` and ``thaw`` phases: the freeze marker, signed in CI (wave 7c).

  python -m tools.clients.publisher_freeze --phase freeze|thaw --env-file .env.local

The emergency recipe's publisher (``app_release_source publish``) obeys a
signed freeze marker (tools/databricks/app_deployment_freeze.py). A client's
proof-signing key is generated in memory into its GitHub Environment's
write-only secret (tools/clients/github_environment.py), so no shell can sign
one. This tool signs it in the deploy job instead, behind the environment's
approval, the break-glass acknowledgement, the OIDC proof and the dispatch
preflight, which holds both phases to the mutating phases' rules (the exact
profile ack, the protections rule, the tree scan). It never runs
scripts/deploy.sh, reads no warehouse and no bundle state, and writes at most
one create-only file per run (a freeze over no lease root also creates that
root, bare).

* **CI only** (``ci_only``): outside GitHub Actions it refuses before it reads
  anything. An operator who holds a signing key keeps the lease CLI.
* **The key.** Read from ``--env-file`` (the ``.env.local`` the
  resolve-client-profile action wrote), uninterpolated, into a local. It is
  never put in ``os.environ``, never in an argv and never printed: it reaches
  ``freeze`` and ``thaw`` only in the ``environ=`` mapping they sign with. The
  public verify key is derived from it (``freeze_unsigned`` when that fails,
  with nothing written), and only public keys go to ``os.environ``, where the
  lease's key registry reads them.
* **freeze**: one marker for the profile's App, until now plus the marker's
  maximum (30 days), through ``app_deployment_freeze.freeze`` with the lease
  CLI's own read-only root judgement (``prepare_root`` never sets a
  permission). The marker is read back and must verify and read frozen. It
  prints the marker id, its ``until`` and the thaw's exact ack.
* **thaw**: the plan of each of this App's verified frozen markers is
  ``{app_name, marker_id, freeze_sha256}``, digested for the purpose
  ``freeze_thaw`` (tools/databricks/operation_ack.py). ``MIP_ACK_FREEZE_THAW``
  (the dispatch input ``operation_acks``, admitted in the thaw phase only) must
  be exactly one marker's digest: that marker alone is thawed, and it must then
  read thawed. No ack, a malformed one or another plan's refuses
  ``thaw_unacked`` and prints each marker's digest, with nothing written.

What obeys the marker is unchanged: ``app_release_source publish`` refuses
while one is frozen, the governed run's own publish is exempt through its held
lease, and the raw CLI (``apps start``, ``apps deploy``) reads no marker.

Refusal codes: ``ci_only``, ``freeze_app_invalid``, ``freeze_unsigned``,
``already_frozen``, ``freeze_root_unprotected``, ``freeze_unverified``,
``nothing_frozen``, ``thaw_unacked``, ``thaw_unverified``, and the marker
module's own (``freeze_exists``). Output is marker ids, epochs, counts and
digests: never a key, the freezing identity, an env value or a path. Exit 0,
or 2 on a refusal.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from backend.services.ai_gateway_proof_attestation import derive_gateway_proof_verify_key
from tools.databricks import app_deployment_freeze as freezer
from tools.databricks import app_deployment_lease as lease
from tools.databricks import app_deployment_lease_support as lease_support
from tools.databricks.operation_ack import AckRefused, ack_digest, require_ack
from tools.databricks.workspace_auth import deployment_workspace_client

PURPOSE = "freeze_thaw"
ACK_ENV = "MIP_ACK_FREEZE_THAW"
APP_NAME = "MIP_APP_NAME"
SIGNING_KEY = "MIP_AI_GATEWAY_PROOF_SIGNING_KEY"
VERIFY_KEY = "MIP_AI_GATEWAY_PROOF_VERIFY_KEY"
# The registry's other public keys: the env file's or none, never an ambient one.
OLDER_VERIFY_KEYS = (
    "MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY",
    "MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS",
)
PREFIX = "[mip-publisher-freeze]"
LIST_HINT = (
    "dispatch phase=thaw with no operation_acks to list each marker of this App that reads "
    "frozen (it writes nothing)"
)


def thaw_plan(app_name: str, marker_id: str, document: Mapping[str, Any]) -> dict[str, str]:
    """The canonical plan a ``freeze_thaw`` ack covers: one App, one marker, that marker's bytes."""

    return {
        "app_name": app_name,
        "marker_id": marker_id,
        "freeze_sha256": freezer.freeze_document_sha256(dict(document)),
    }


def _profile(env_file: Path) -> tuple[str, dict[str, str]]:
    """The App name and the signing mapping, from the job's env file; public keys to ``os.environ``."""

    values = dotenv_values(env_file, interpolate=False)
    app_name = str(values.get(APP_NAME) or "").strip()
    if not app_name:
        raise freezer.FreezeRefused(
            "freeze_app_invalid", f"the env file names no {APP_NAME}; nothing was written"
        )
    signing = str(values.get(SIGNING_KEY) or "").strip()
    try:
        verify = derive_gateway_proof_verify_key(signing)
    except Exception:  # noqa: BLE001 - any failure is the same refusal, and none may print
        raise freezer.FreezeRefused(
            "freeze_unsigned",
            "the env file holds no valid proof-signing key; nothing was written",
        ) from None
    os.environ[VERIFY_KEY] = verify
    for name in OLDER_VERIFY_KEYS:
        older = str(values.get(name) or "").strip()
        if older:
            os.environ[name] = older
        else:
            os.environ.pop(name, None)
    try:
        lease_support.key_registry()
    except RuntimeError:
        raise freezer.FreezeRefused(
            "freeze_unsigned",
            "the env file's verify key registry is invalid; nothing was written",
        ) from None
    return app_name, {SIGNING_KEY: signing, VERIFY_KEY: verify}


def _freeze(workspace: Any, *, app_name: str, signing: dict[str, str], now: int) -> list[str]:
    holder = lease._holder(workspace)
    until = now + freezer.MAX_FREEZE_SECONDS
    marker_id = freezer.freeze(
        workspace,
        app_name=app_name,
        until=until,
        holder=holder,
        # Never the lease's own _ensure_protected_root: it would rewrite the root's ACL.
        ensure_root=lambda: freezer.prepare_root(
            workspace, app_name=app_name, lease=lease, holder=holder
        ),
        environ=signing,
        now=float(now),
    )
    written = dict(freezer.frozen_documents(workspace, app_name=app_name, now=float(now)))
    if marker_id not in written:
        raise freezer.FreezeRefused(
            "freeze_unverified",
            f"marker {marker_id} was written but does not read back as frozen under the "
            f"deploy's key; a publisher that cannot verify it reads it as frozen. To see "
            f"what it reads as, {LIST_HINT}",
        )
    digest = ack_digest(PURPOSE, thaw_plan(app_name, marker_id, written[marker_id]))
    return [
        f"frozen: marker {marker_id} until {until}",
        f'thaw with -f phase=thaw -f operation_acks="{PURPOSE}={digest}"',
    ]


def _acked(plans: Mapping[str, dict[str, str]], given: str) -> str | None:
    """The one marker whose exact ``freeze_thaw`` digest ``given`` is, or None."""

    for marker_id, plan in sorted(plans.items()):
        try:
            require_ack(PURPOSE, plan, given)
        except AckRefused:
            continue
        return marker_id
    return None


def _thaw(
    workspace: Any, *, app_name: str, signing: dict[str, str], given: str, now: float
) -> list[str]:
    plans = {
        marker_id: thaw_plan(app_name, marker_id, document)
        for marker_id, document in freezer.frozen_documents(workspace, app_name=app_name, now=now)
    }
    if not plans:
        unverifiable = [
            marker
            for marker in freezer.judge_markers(workspace, now=now)
            if marker.frozen and marker.app_name == app_name
        ]
        if unverifiable:
            raise freezer.FreezeRefused(
                "thaw_unverified",
                f"{len(unverifiable)} freeze marker(s) of this App read frozen but do not "
                "verify under the deploy's key registry, so no thaw can name one; nothing "
                "was written",
            )
        raise freezer.FreezeRefused(
            "nothing_frozen", "no freeze marker of this App reads frozen; nothing was written"
        )
    marker_id = _acked(plans, given)
    if marker_id is None:
        listed = "; ".join(
            f'marker {name}: operation_acks="{PURPOSE}={ack_digest(PURPOSE, plan)}"'
            for name, plan in sorted(plans.items())
        )
        raise freezer.FreezeRefused(
            "thaw_unacked",
            f"the {PURPOSE} ack names none of this App's frozen markers; nothing was "
            f"written. Review, then dispatch phase=thaw with exactly one of: {listed}",
        )
    freezer.thaw(
        workspace,
        app_name=app_name,
        marker_id=marker_id,
        holder=lease._holder(workspace),
        environ=signing,
    )
    after = [m for m in freezer.judge_markers(workspace, now=now) if m.app_name == app_name]
    if [m.reason for m in after if m.marker_id == marker_id] != ["thawed"]:
        raise freezer.FreezeRefused(
            "thaw_unverified",
            f"the thaw of marker {marker_id} was written but the marker does not read back "
            f"as thawed. To see what it reads as, {LIST_HINT}",
        )
    lines = [f"thawed: marker {marker_id} (nothing was deleted)"]
    still = sum(1 for m in after if m.frozen)
    if still:
        lines.append(f"{still} other freeze marker(s) of this App still read frozen: {LIST_HINT}")
    return lines


def main(
    argv: Sequence[str] | None = None,
    *,
    workspace_factory: Callable[[], Any] = deployment_workspace_client,
    clock: Callable[[], float] = time.time,
) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.clients.publisher_freeze", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--phase", required=True, choices=("freeze", "thaw"))
    parser.add_argument("--env-file", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if os.environ.get("GITHUB_ACTIONS") != "true":
            raise freezer.FreezeRefused(
                "ci_only",
                "only deploy-dev.yml's freeze and thaw phases run this tool: it signs with the "
                "client environment's key, which no shell holds. An operator who holds a "
                "signing key uses python -m tools.databricks.app_deployment_lease freeze|thaw",
            )
        app_name, signing = _profile(args.env_file)
        workspace = workspace_factory()
        now = clock()
        if args.phase == "freeze":
            lines = _freeze(workspace, app_name=app_name, signing=signing, now=int(now))
        else:
            given = os.environ.get(ACK_ENV, "")
            lines = _thaw(workspace, app_name=app_name, signing=signing, given=given, now=now)
    except freezer.FreezeRefused as exc:
        print(f"{PREFIX} REFUSED {exc.code}: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - the class only: a message could carry a value
        print(
            f"{PREFIX} REFUSED failed: {type(exc).__name__}. Whether a marker was written is "
            f"not known: {LIST_HINT}",
            file=sys.stderr,
        )
        return 2
    for line in lines:
        print(f"{PREFIX} {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
