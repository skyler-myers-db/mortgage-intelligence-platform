"""Mint a short-lived workspace Bearer token via Databricks M2M OAuth.

Used by the nightly Playwright workflow to traverse the Databricks Apps
OAuth proxy in front of the deployed Module 0 app. The SP (service
principal) backing `DATABRICKS_CLIENT_ID` must have `CAN USE` on the app
resource (see docs/security/m2m-oauth-setup.md for the one-time admin
procedure).

Contract
--------
Env vars required (Databricks-canonical names; do NOT rename):
    DATABRICKS_HOST            e.g. https://dbc-xxxxx.cloud.databricks.com
    DATABRICKS_CLIENT_ID       OAuth client_id for the SP
    DATABRICKS_CLIENT_SECRET   OAuth client_secret for the SP

Output:
    --github-env NAME -> append NAME=<Bearer> to $GITHUB_ENV
    --output-file PATH -> write the Bearer to a mode-0600 file
    stderr  -> secret-free diagnostics ([mip-m2m-mint] ...)
    exit 0  -> success
    exit 2  -> missing / empty env var (usage error)
    exit 3  -> authenticate() returned no Bearer header (config error)
    exit 4  -> SDK raised an exception (transient auth failure)

TTL
---
M2M tokens from Databricks are short-lived (~1h). Workflows mint at job start
and remint immediately before long evaluation/smoke phases.

Usage
-----
    export DATABRICKS_HOST=https://dbc-xxxxx.cloud.databricks.com
    export DATABRICKS_CLIENT_ID=<your-m2m-client-id>
    export DATABRICKS_CLIENT_SECRET=<your-m2m-client-secret>
    python tools/oauth_m2m_mint.py --output-file /tmp/bearer.txt
    curl -H "Authorization: Bearer $(cat /tmp/bearer.txt)" \\
        https://mip-app-2543889327043640.aws.databricksapps.com/api/health
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def _remove_tools_shadow_path() -> None:
    """Avoid `tools/databricks/` shadowing the real Databricks SDK.

    GitHub Actions executes this helper as `python tools/oauth_m2m_mint.py`,
    which puts `<repo>/tools` at `sys.path[0]`. That directory contains this
    repo's provisioning helpers under `tools/databricks/`; if left in front of
    site-packages, `from databricks.sdk...` resolves against the helper package
    instead of the installed `databricks-sdk` wheel.
    """
    tools_dir = str(Path(__file__).resolve().parent)
    while tools_dir in sys.path:
        sys.path.remove(tools_dir)


_remove_tools_shadow_path()


def _diag(msg: str) -> None:
    """Write a diagnostic to stderr. stdout stays clean for the token."""
    print(f"[mip-m2m-mint] {msg}", file=sys.stderr)


def _require_env(*, host_env: str, client_id_env: str, client_secret_env: str) -> dict[str, str]:
    """Collect + validate required env vars. Exit 2 on any missing/empty."""
    collected: dict[str, str] = {}
    missing: list[str] = []
    for name in (host_env, client_id_env, client_secret_env):
        if not _ENV_NAME_RE.fullmatch(name):
            _diag(f"ERROR invalid environment variable name: {name!r}")
            sys.exit(2)
        val = os.environ.get(name, "").strip()
        if not val:
            missing.append(name)
        else:
            collected[name] = val
    if missing:
        _diag(
            "ERROR missing required env var(s): "
            + ", ".join(missing)
            + ". See docs/security/m2m-oauth-setup.md for how to provision "
            "the service-principal OAuth client."
        )
        sys.exit(2)
    return collected


def mint_token(
    *,
    host_env: str = "DATABRICKS_HOST",
    client_id_env: str = "DATABRICKS_CLIENT_ID",
    client_secret_env: str = "DATABRICKS_CLIENT_SECRET",
) -> str:
    """Mint a Bearer via the SDK's oauth-m2m auth strategy.

    Returns the raw token string. Caller is responsible for any
    formatting (e.g. appending to $GITHUB_ENV).
    """
    env = _require_env(
        host_env=host_env,
        client_id_env=client_id_env,
        client_secret_env=client_secret_env,
    )

    # Imported lazily so the missing-env-var path stays fast and does not
    # require databricks-sdk to be installed just to see the usage error.
    try:
        from databricks.sdk.core import Config  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover -- requirements.txt pins it
        _diag(f"ERROR databricks-sdk not importable: {exc}")
        sys.exit(4)

    _diag(
        f"minting M2M token for host={env[host_env]} "
        f"client_id_env={client_id_env}"
    )

    try:
        cfg = Config(
            host=env[host_env],
            client_id=env[client_id_env],
            client_secret=env[client_secret_env],
            auth_type="oauth-m2m",
        )
        headers = cfg.authenticate()
    except Exception as exc:  # noqa: BLE001 -- surface root cause to operator
        message = str(exc).replace(env[client_secret_env], "[REDACTED]")
        _diag(f"ERROR authenticate() raised {type(exc).__name__}: {message[:400]}")
        sys.exit(4)

    if not isinstance(headers, dict):
        _diag(f"ERROR authenticate() returned non-dict: {type(headers).__name__}")
        sys.exit(3)

    auth = headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        _diag(
            "ERROR authenticate() returned no Bearer header; "
            f"keys={list(headers.keys())}"
        )
        sys.exit(3)

    token = auth.removeprefix("Bearer ").strip()
    if not token:
        _diag("ERROR Bearer header was present but empty")
        sys.exit(3)

    _diag(
        f"ok token_len={len(token)} auth_type={getattr(cfg, 'auth_type', 'unknown')}"
    )
    return token


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mint a short-lived M2M bearer without logging it.")
    parser.add_argument("--host-env", default="DATABRICKS_HOST")
    parser.add_argument("--client-id-env", default="DATABRICKS_CLIENT_ID")
    parser.add_argument("--client-secret-env", default="DATABRICKS_CLIENT_SECRET")
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument(
        "--github-env",
        action="append",
        metavar="NAME",
        help="Append the bearer to $GITHUB_ENV under NAME; may be repeated.",
    )
    output.add_argument("--output-file", type=Path, help="Write the bearer to a mode-0600 file.")
    return parser


def _write_output(token: str, *, github_env_names: list[str] | None, output_file: Path | None) -> None:
    if github_env_names:
        github_env_path = os.environ.get("GITHUB_ENV", "").strip()
        if not github_env_path:
            raise SystemExit("--github-env requires GITHUB_ENV")
        for name in github_env_names:
            if not _ENV_NAME_RE.fullmatch(name):
                raise SystemExit(f"Invalid --github-env name: {name!r}")
        with open(github_env_path, "a", encoding="utf-8") as handle:
            for name in github_env_names:
                handle.write(f"{name}={token}\n")
        return

    assert output_file is not None
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(output_file, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, (token + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    token = mint_token(
        host_env=args.host_env,
        client_id_env=args.client_id_env,
        client_secret_env=args.client_secret_env,
    )
    _write_output(token, github_env_names=args.github_env, output_file=args.output_file)


if __name__ == "__main__":
    main()
