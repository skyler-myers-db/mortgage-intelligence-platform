"""``python -m tools.clients check | resolve | mask | digest``.

Inputs, one way or the other:

* CI: ``--vars-json SRC`` and ``--secrets-json SRC``, where SRC is ``env:NAME``
  (a step env var holding ``toJSON(vars)`` or ``toJSON(secrets)``) or a file.
* Local: the shell, then ``--env-file`` (default ``.env.local``).
  ``--env-file-only`` ignores the shell, to check a template.

Commands:

* ``check``: validate; print the client key, class, tier and digest.
* ``digest``: print the profile digest.
* ``mask``: register every identifying leaf with the GitHub runner.
* ``resolve``: register the masks, then write one view (deploy controls,
  bundle variables or App payload env) to ``--out`` with mode 0600. Values
  never go to stdout, and no view carries a secret.

Exit 0 on success, 2 on a refusal. A refusal prints field names and rules,
never values.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from tools.clients import profile as views
from tools.clients.environment import from_github, from_local, read_context, resolve
from tools.clients.errors import ProfileRefusal
from tools.clients.redact import emit_mask, in_github_actions
from tools.clients.schema import ClientProfile

REPO = Path(__file__).resolve().parents[2]
VIEWS: dict[str, Callable[[ClientProfile], dict[str, str]]] = {
    "controls": views.deploy_controls,
    "bundle-vars": views.bundle_vars,
    "app-env": views.app_payload_env,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.clients", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("check", "resolve", "mask", "digest"))
    parser.add_argument("--vars-json", help="env:NAME or a file holding toJSON(vars)")
    parser.add_argument("--secrets-json", help="env:NAME or a file holding toJSON(secrets)")
    parser.add_argument("--env-file", help="local env file (default: .env.local)")
    parser.add_argument("--env-file-only", action="store_true", help="ignore the shell environment")
    parser.add_argument(
        "--allow-placeholders",
        action="store_true",
        help="accept <placeholder> values (check and digest only; for the template)",
    )
    parser.add_argument("--emit", choices=sorted(VIEWS), help="resolve: the view to write")
    parser.add_argument("--out", help="resolve: the file to write (mode 0600)")
    parser.add_argument("--format", choices=("json", "env"), default="json", help="resolve: file format")
    return parser


def _load(args: argparse.Namespace) -> ClientProfile:
    if args.allow_placeholders and args.command in {"resolve", "mask"}:
        raise ProfileRefusal([f"--allow-placeholders: a template cannot be used for {args.command}"])
    if args.vars_json or args.secrets_json:
        if args.env_file or args.env_file_only:
            raise ProfileRefusal(["choose the CI contexts or a local env file, not both"])
        vars_context = read_context(args.vars_json) if args.vars_json else {}
        secrets_context = read_context(args.secrets_json) if args.secrets_json else {}
        raw = from_github(vars_context, secrets_context)
    else:
        env_file = Path(args.env_file) if args.env_file else REPO / ".env.local"
        raw = from_local({} if args.env_file_only else os.environ, env_file)
    return resolve(raw, allow_placeholders=args.allow_placeholders)


def _render(values: dict[str, str], fmt: str) -> str:
    if fmt == "json":
        return json.dumps(values, indent=2, sort_keys=True) + "\n"
    unsafe = sorted(name for name, value in values.items() if any(c in value for c in "'\n\r\0"))
    if unsafe:
        raise ProfileRefusal([f"{name}: cannot be written in env format; use --format json" for name in unsafe])
    return "".join(f"{name}='{value}'\n" for name, value in sorted(values.items()))


def _write_private(path: Path, text: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, text.encode("utf-8"))
    finally:
        os.close(descriptor)


def _run(args: argparse.Namespace) -> int:
    profile = _load(args)
    if args.command == "check":
        template = " (template)" if args.allow_placeholders else ""
        print(
            f"client profile ok{template}: client={profile.client} class={profile.install_class} "
            f"tier={profile.agentic_tier} digest={views.digest(profile)}"
        )
    elif args.command == "digest":
        print(views.digest(profile))
    elif args.command == "mask":
        values = views.masks(profile)
        if in_github_actions():
            for value in values:
                emit_mask(value, stream=sys.stdout)
            print(f"registered {len(values)} identifying values as masks", file=sys.stderr)
        else:
            print(f"not in GitHub Actions: would register {len(values)} masks", file=sys.stderr)
    else:
        if not args.emit or not args.out:
            raise ProfileRefusal(["resolve: --emit and --out are both required"])
        for value in views.masks(profile):
            emit_mask(value)
        selected = VIEWS[args.emit](profile)
        _write_private(Path(args.out), _render(selected, args.format))
        print(f"wrote {len(selected)} {args.emit} values to {args.out}", file=sys.stderr)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _run(args)
    except ProfileRefusal as refusal:
        print("client profile refused:", file=sys.stderr)
        for line in refusal.lines:
            print(f"  - {line}", file=sys.stderr)
        return 2
    except Exception as exc:  # the class only: a message could carry a value
        print(f"client profile tool failed: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
