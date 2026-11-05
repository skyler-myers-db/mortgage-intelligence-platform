"""Write the deploy job's profile files from its GitHub Environment (M1-PR7).

``.github/actions/resolve-client-profile`` runs this right after its mask step
(``python -m tools.clients mask``), with the same vars and secrets objects the
deploy job's collect step wrote as files (tools/clients/workflow_contexts.py):

* ``.env.local``: the ``controls`` view (tools/clients/profile.deploy_controls),
  every public and identifying field under the names scripts/deploy.sh reads,
  plus the secret fields the environment holds, under their own names: the
  file an operator keeps. scripts/deploy.sh un-exports every credential before
  its first child, and each child that resolves the profile (the client
  target, the live checks, bundle_env) reads the required secret fields from
  ``.env.local``, so a step-env secret would never reach them. The runner is
  ephemeral, the file is mode 0600, and the job's last step deletes it.
* ``~/.databrickscfg``: the ``DEFAULT`` profile, ``host``, ``auth_type =
  github-oidc`` and the deployer's application id, so the SDK and the CLI
  exchange the job's GitHub ID token for a short-lived Databricks token. No
  credential is stored.
* ``$GITHUB_OUTPUT``: public values only (the client key, its class, tier and
  review mode, the App name and the profile digest).

It registers every identifying leaf and derived spelling as a runner mask
again before it writes anything (``emit_mask``; the mask step already did),
refuses to overwrite either file, writes both mode 0600 and prints counts and
the digest, never a value. Exit 0, or 2 on a refusal.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from tools.clients import profile as views
from tools.clients.environment import from_github, read_context, resolve
from tools.clients.errors import ProfileRefusal
from tools.clients.redact import emit_mask
from tools.clients.schema import FIELD_CLASS, ClientProfile, FieldClass

DEPLOYER_PROFILE = "DEFAULT"


def databrickscfg_text(profile: ClientProfile) -> str:
    """The deployer's github-oidc profile: host, auth type and client id, nothing else."""

    return (
        f"[{DEPLOYER_PROFILE}]\n"
        f"host  = {profile.databricks_host}\n"
        "auth_type = github-oidc\n"
        f"client_id = {profile.identity_ids.deployer}\n"
    )


def env_local_text(profile: ClientProfile) -> str:
    controls = {**views.deploy_controls(profile), **secret_fields(profile)}
    unsafe = sorted(name for name, value in controls.items() if any(c in value for c in "'\n\r\0"))
    if unsafe:
        raise ProfileRefusal([f"{name}: cannot be written to .env.local" for name in unsafe])
    return "".join(f"{name}='{value}'\n" for name, value in sorted(controls.items()))


def secret_fields(profile: ClientProfile) -> dict[str, str]:
    """The secret fields the profile holds, under their own names (never printed)."""

    return {
        name: value
        for name, value in views.canonical_env(profile, include_secrets=True).items()
        if FIELD_CLASS[name] is FieldClass.SECRET
    }


def public_outputs(profile: ClientProfile) -> dict[str, str]:
    return {
        "client": profile.client,
        "install-class": profile.install_class,
        "agentic-tier": profile.agentic_tier,
        "review-mode": profile.review_mode,
        "app-name": profile.app_name,
        "profile-digest": views.digest(profile),
    }


def _write_new(path: Path, text: str) -> None:
    """Create ``path`` mode 0600; an existing file refuses (never overwritten)."""

    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ProfileRefusal([f"{path.name}: already exists; the deploy job writes it once"]) from None
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, text.encode("utf-8"))
    finally:
        os.close(descriptor)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.clients.ci_profile", description=__doc__.splitlines()[0])
    parser.add_argument("--vars-json", required=True, help="env:NAME or a file holding the vars object")
    parser.add_argument("--secrets-json", required=True, help="env:NAME or a file holding the secrets object")
    parser.add_argument("--environment", required=True, help="the GitHub Environment the job runs in")
    parser.add_argument("--env-local", required=True, type=Path)
    parser.add_argument("--databrickscfg", required=True, type=Path)
    parser.add_argument("--github-output", type=Path, help="the step's $GITHUB_OUTPUT")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        raw = from_github(read_context(args.vars_json), read_context(args.secrets_json), environment=args.environment)
        profile = resolve(raw)
        for value in (*views.masks(profile), *views.derived_masks(profile)):
            emit_mask(value)
        env_local = env_local_text(profile)
        _write_new(args.env_local, env_local)
        args.databrickscfg.parent.mkdir(parents=True, exist_ok=True)
        _write_new(args.databrickscfg, databrickscfg_text(profile))
        outputs = public_outputs(profile)
        if args.github_output is not None:
            with args.github_output.open("a", encoding="utf-8") as handle:
                handle.writelines(f"{name}={value}\n" for name, value in outputs.items())
    except ProfileRefusal as refusal:
        print("client profile refused:", file=sys.stderr)
        for line in refusal.lines:
            print(f"  - {line}", file=sys.stderr)
        return 2
    except Exception as exc:  # the class only: a message could carry a value
        print(f"client profile tool failed: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(
        f"client profile resolved: client={outputs['client']} class={outputs['install-class']} "
        f"tier={outputs['agentic-tier']} review={outputs['review-mode']} digest={outputs['profile-digest']}; "
        f"wrote {env_local.count(chr(10))} profile values to {args.env_local.name} and the github-oidc "
        f"{DEPLOYER_PROFILE} profile"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
