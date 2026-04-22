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
    stdout  -> the Bearer token, nothing else (trailing newline OK)
    stderr  -> diagnostics ([mip-m2m-mint] ...)
    exit 0  -> success
    exit 2  -> missing / empty env var (usage error)
    exit 3  -> authenticate() returned no Bearer header (config error)
    exit 4  -> SDK raised an exception (transient auth failure)

TTL
---
M2M tokens from Databricks are short-lived (~1h). Playwright specs run
for 5–10 min end-to-end, so a single mint at job start is sufficient; we
don't need mid-run refresh. If/when a test suite grows past ~45 min, wrap
the mint in a step that re-runs before each long phase rather than
caching across steps.

Usage
-----
    export DATABRICKS_HOST=https://dbc-xxxxx.cloud.databricks.com
    export DATABRICKS_CLIENT_ID=<your-m2m-client-id>
    export DATABRICKS_CLIENT_SECRET=<your-m2m-client-secret>
    python tools/oauth_m2m_mint.py > /tmp/bearer.txt
    curl -H "Authorization: Bearer $(cat /tmp/bearer.txt)" \\
        https://mip-app-2543889327043640.aws.databricksapps.com/api/health
"""

from __future__ import annotations

import os
import sys

_REQUIRED_ENV = (
    "DATABRICKS_HOST",
    "DATABRICKS_CLIENT_ID",
    "DATABRICKS_CLIENT_SECRET",
)


def _diag(msg: str) -> None:
    """Write a diagnostic to stderr. stdout stays clean for the token."""
    print(f"[mip-m2m-mint] {msg}", file=sys.stderr)


def _require_env() -> dict[str, str]:
    """Collect + validate required env vars. Exit 2 on any missing/empty."""
    collected: dict[str, str] = {}
    missing: list[str] = []
    for name in _REQUIRED_ENV:
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


def mint_token() -> str:
    """Mint a Bearer via the SDK's oauth-m2m auth strategy.

    Returns the raw token string. Caller is responsible for any
    formatting (e.g. appending to $GITHUB_ENV).
    """
    env = _require_env()

    # Imported lazily so the missing-env-var path stays fast and does not
    # require databricks-sdk to be installed just to see the usage error.
    try:
        from databricks.sdk.core import Config  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover -- requirements.txt pins it
        _diag(f"ERROR databricks-sdk not importable: {exc}")
        sys.exit(4)

    _diag(
        f"minting M2M token for host={env['DATABRICKS_HOST']} "
        f"client_id={env['DATABRICKS_CLIENT_ID'][:6]}..."
    )

    try:
        cfg = Config(
            host=env["DATABRICKS_HOST"],
            client_id=env["DATABRICKS_CLIENT_ID"],
            client_secret=env["DATABRICKS_CLIENT_SECRET"],
            auth_type="oauth-m2m",
        )
        headers = cfg.authenticate()
    except Exception as exc:  # noqa: BLE001 -- surface root cause to operator
        _diag(f"ERROR authenticate() raised {type(exc).__name__}: {str(exc)[:400]}")
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
        f"ok token_len={len(token)} auth_type={getattr(cfg, 'auth_type', 'unknown')} "
        "(token TTL ~1h; single mint is enough for a <45min Playwright run)"
    )
    return token


def main() -> None:
    token = mint_token()
    # Exactly one write to stdout. Downstream: `TOKEN=$(python tools/oauth_m2m_mint.py)`.
    sys.stdout.write(token + "\n")


if __name__ == "__main__":
    main()
