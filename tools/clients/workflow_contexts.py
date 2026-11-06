"""The client profile's names as deploy-dev.yml binds them, one by one (M1-PR7 review, G2).

GitHub prints every ``with:`` input and every ``env:`` value of a step in the
step's log header, before the step runs, and only secrets are masked there:
a variable prints in clear, and the repository and its run logs are public.
So no step holds ``toJSON(vars)`` or ``toJSON(secrets)``, which would print
every repository-, organization- and environment-level variable in clear and
carry every repository secret into the step. deploy-dev.yml names each
profile name it reads instead, and this module is the one place those names
come from (tools/clients/schema.py: the fields and their aliases; an identity
alias such as ``DATABRICKS_CLIENT_ID`` never supplies a value, so none is
bound):

* **the deploy job's collect step** binds each public name from ``vars`` by
  value (a public value may print), every profile name from ``secrets`` by
  value (the runner masks each), and, for each identifying or secret name,
  only whether ``vars`` holds it: a misfiled one prints its name, never its
  value. ``collect`` writes the two contexts the resolver reads
  (tools/clients/environment.from_github) as files, mode 0600 and create-only,
  for the resolve-client-profile action and the dispatch preflight, and
  refuses a misfiled name. It prints counts only.
* **check-dispatch's repository-scope step** runs in no environment, so
  ``vars`` and ``secrets`` hold only repository and organization entries
  there. GitHub merges those into a client environment's contexts and nothing
  says which scope an entry came from, so a field the environment omits would
  be filled silently from them: it refuses when any profile name is set at
  either scope, an identity alias included (``SCOPE_NAMES``: the owner deletes
  those too, docs/deployment.md owner step 3), and prints names only.

Both blocks put each expression on its own line of a folded scalar.
``python -m tools.clients.workflow_contexts print`` prints them;
tests/unit/test_workflow_contexts.py holds deploy-dev.yml equal to them and
checks that no other expression in the workflow names ``vars`` or
``secrets``. In step debug logging the runner traces each expression with its
operands, so both jobs refuse a debug run before any step that names one.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from tools.clients.schema import ALIASES, FIELD_CLASS, IDENTITY_ALIASES, FieldClass, name_class

BOUND_NAMES = tuple(sorted({*FIELD_CLASS, *(alias for names in ALIASES.values() for alias in names)}))
# An identity alias supplies no value, so the collect step binds none; at
# repository or organization scope it is still another install's profile
# name in every client environment's contexts, so check-dispatch refuses it.
SCOPE_NAMES = tuple(sorted({*BOUND_NAMES, *IDENTITY_ALIASES}))
PUBLIC_NAMES = tuple(name for name in BOUND_NAMES if name_class(name) is FieldClass.PUBLIC)
PRIVATE_NAMES = tuple(name for name in BOUND_NAMES if name_class(name) is not FieldClass.PUBLIC)
VAR_PREFIX = "MIP_CLIENT_VAR__"
SECRET_PREFIX = "MIP_CLIENT_SECRET__"
MISFILED_ENV = "MIP_CLIENT_VARS_MISFILED"
REPOSITORY_VARS_ENV = "MIP_REPOSITORY_SCOPE_VARS"
REPOSITORY_SECRETS_ENV = "MIP_REPOSITORY_SCOPE_SECRETS"
# GitHub Environments of this repository that are not a client's: never a deploy target.
NON_CLIENT_ENVIRONMENTS = ("copilot", "dev", "github-pages")


def _present(context: str, name: str) -> str:
    return f"${{{{ {context}.{name} && '{name}' || '' }}}}"


def collect_env() -> dict[str, str]:
    """The deploy job's collect step env: values by class, and misfiled names by presence only."""

    env = {f"{VAR_PREFIX}{name}": f"${{{{ vars.{name} }}}}" for name in PUBLIC_NAMES}
    env.update({f"{SECRET_PREFIX}{name}": f"${{{{ secrets.{name} }}}}" for name in BOUND_NAMES})
    env[MISFILED_ENV] = " ".join(_present("vars", name) for name in PRIVATE_NAMES)
    return env


def repository_scope_env() -> dict[str, str]:
    """check-dispatch's env: the names set at repository or organization scope, never a value."""

    return {
        REPOSITORY_VARS_ENV: " ".join(_present("vars", name) for name in SCOPE_NAMES),
        REPOSITORY_SECRETS_ENV: " ".join(_present("secrets", name) for name in SCOPE_NAMES),
    }


def yaml_block(env: Mapping[str, str], indent: int) -> str:
    """``env`` as deploy-dev.yml holds it: each presence expression on its own folded line."""

    pad = " " * indent
    lines = []
    for name, value in env.items():
        if " " in value and value.startswith("${{") and "&&" in value:
            lines.append(f"{pad}{name}: >-")
            lines += [f"{pad}  {part}" for part in _expressions(value)]
        else:
            lines.append(f"{pad}{name}: {value}")
    return "\n".join(lines)


def _expressions(value: str) -> list[str]:
    return [f"${{{{{part}".strip() for part in value.split("${{")[1:]]


def _write_new(path: Path, data: dict[str, str]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, json.dumps(data, sort_keys=True).encode("utf-8"))
    finally:
        os.close(descriptor)


def collect(environ: Mapping[str, str], *, vars_out: Path, secrets_out: Path) -> tuple[int, int]:
    """Write the vars and secrets objects from the collect step's env; refuse a misfiled name."""

    misfiled = environ.get(MISFILED_ENV, "").split()
    unknown = sorted(set(misfiled) - set(PRIVATE_NAMES))
    if unknown:
        raise ValueError(f"{MISFILED_ENV}: {len(unknown)} names that are not identifying or secret profile names")
    if misfiled:
        raise ValueError(
            "; ".join(
                f"{name}: is {name_class(name)}; store it as an environment secret, never a variable"
                for name in sorted(set(misfiled))
            )
        )
    variables = {name: value for name in PUBLIC_NAMES if (value := environ.get(f"{VAR_PREFIX}{name}", "")).strip()}
    secrets = {name: value for name in BOUND_NAMES if (value := environ.get(f"{SECRET_PREFIX}{name}", "")).strip()}
    _write_new(vars_out, variables)
    _write_new(secrets_out, secrets)
    return len(variables), len(secrets)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.clients.workflow_contexts", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("print", help="print the two generated env blocks for deploy-dev.yml")
    command = sub.add_parser("collect", help="write the collect step's vars and secrets objects")
    command.add_argument("--vars-out", required=True, type=Path)
    command.add_argument("--secrets-out", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "print":
        print("# check-dispatch, 'Refuse a profile name set at repository or organization level' env:")
        print(yaml_block(repository_scope_env(), 10))
        print("# deploy, 'Collect the client's profile from its environment' env:")
        print(yaml_block(collect_env(), 10))
        return 0
    try:
        count_vars, count_secrets = collect(os.environ, vars_out=args.vars_out, secrets_out=args.secrets_out)
    except FileExistsError:
        print("client contexts refused: an output file already exists; the deploy job writes each once", file=sys.stderr)
        return 2
    except ValueError as refusal:  # names only: never a value
        print(f"client contexts refused: {refusal}", file=sys.stderr)
        return 2
    print(f"client contexts collected: {count_vars} variables and {count_secrets} secrets (names and values not printed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
