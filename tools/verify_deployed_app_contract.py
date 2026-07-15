#!/usr/bin/env python3
"""Fail unless authenticated deployed health matches the exact release contract."""

from __future__ import annotations

import argparse
import json
import os
import urllib.request


def verify(
    *,
    base_url: str,
    bearer_token: str,
    git_sha: str,
    gateway_binding_sha256: str,
) -> None:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/health",
        headers={"Authorization": f"Bearer {bearer_token}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        body = json.load(response)
    if body.get("git_sha") != git_sha:
        raise RuntimeError(
            f"deployed app git_sha is {body.get('git_sha')!r}, expected {git_sha!r}"
        )
    actual_binding = body.get("agent_gateway_binding_sha256")
    if actual_binding != gateway_binding_sha256:
        raise RuntimeError(
            "deployed App Gateway binding does not match the source-bound live resource contract"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token-env", required=True)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--gateway-binding-sha256", required=True)
    args = parser.parse_args(argv)
    token = os.environ.get(args.token_env, "").strip()
    if not token:
        parser.error(f"{args.token_env} is empty")
    verify(
        base_url=args.base_url,
        bearer_token=token,
        git_sha=args.git_sha,
        gateway_binding_sha256=args.gateway_binding_sha256,
    )
    print("[deploy-contract] authenticated app SHA and Gateway binding match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
