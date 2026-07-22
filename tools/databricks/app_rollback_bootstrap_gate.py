#!/usr/bin/env python3
"""Refuse an unsigned-App rebase once any last-good record exists."""

from __future__ import annotations

import argparse
from typing import Any

from databricks.sdk import WorkspaceClient
from tools.databricks.app_rollback_record_contract import _record_key, _secret_value


def assert_rollback_record_absent(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
) -> None:
    """Prove the one-time rebase has not already established durable trust."""

    if _secret_value(workspace, scope=scope, key=_record_key(app_name)) is not None:
        raise RuntimeError(
            "a server-owned last-good App rollback record already exists; "
            "refusing the one-time unsigned rebase"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--scope", default="mip-app-rollback")
    args = parser.parse_args(argv)
    assert_rollback_record_absent(
        WorkspaceClient(),
        app_name=args.app_name,
        scope=args.scope,
    )
    print("Unsigned-App rebase gate: no last-good rollback record exists")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
