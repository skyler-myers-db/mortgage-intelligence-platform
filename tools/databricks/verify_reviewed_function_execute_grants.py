#!/usr/bin/env python3
"""Prove exact effective EXECUTE grants on the reviewed Growth Agent functions."""

from __future__ import annotations

import argparse
import re
from typing import Any

from tools.databricks.agent_runtime_uc_baseline import ALLOWED_FUNCTIONS
from tools.databricks.workspace_auth import deployment_workspace_client

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _text(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _effective_privileges(
    workspace: Any,
    *,
    full_name: str,
    principal: str,
) -> dict[str, set[tuple[str, str]]]:
    """Read every effective page, retaining the inheritance source."""

    privileges: dict[str, set[tuple[str, str]]] = {}
    page_token: str | None = None
    seen_tokens: set[str] = set()
    while True:
        response = workspace.grants.get_effective(
            "function",
            full_name,
            principal=principal,
            max_results=1000,
            page_token=page_token,
        )
        for assignment in getattr(response, "privilege_assignments", None) or []:
            assignment_principal = _text(getattr(assignment, "principal", None))
            if assignment_principal != principal:
                raise RuntimeError(
                    f"effective grant for {full_name} was attributed to unexpected principal "
                    f"{assignment_principal or '<missing>'!r}"
                )
            for privilege in getattr(assignment, "privileges", None) or []:
                name = _text(getattr(privilege, "privilege", None)).upper()
                if not name:
                    raise RuntimeError(f"effective grant for {full_name} omitted its privilege")
                privileges.setdefault(name, set()).add(
                    (
                        _text(getattr(privilege, "inherited_from_type", None)).upper(),
                        _text(getattr(privilege, "inherited_from_name", None)),
                    )
                )
        next_token = _text(getattr(response, "next_page_token", None))
        if not next_token:
            return privileges
        if next_token in seen_tokens:
            raise RuntimeError("effective grant pagination repeated a page token")
        seen_tokens.add(next_token)
        page_token = next_token


def verify_reviewed_function_execute_grants(
    workspace: Any,
    *,
    catalog: str,
    principals: tuple[str, str],
) -> None:
    """Require one direct EXECUTE privilege for both production consumers."""

    if not _IDENTIFIER_RE.fullmatch(catalog):
        raise ValueError(f"invalid catalog identifier: {catalog!r}")
    if any(not principal.strip() for principal in principals):
        raise ValueError("both application IDs are required")
    if principals[0] == principals[1]:
        raise ValueError("App and agent-runtime application IDs must be distinct")

    expected = {"EXECUTE": {("", "")}}
    for principal in principals:
        for function_name in sorted(ALLOWED_FUNCTIONS):
            full_name = f"{catalog}.gold.{function_name}"
            actual = _effective_privileges(
                workspace,
                full_name=full_name,
                principal=principal,
            )
            if actual != expected:
                raise RuntimeError(
                    f"exact effective EXECUTE postflight failed for {full_name} and "
                    f"{principal}: expected={expected}, actual={actual}"
                )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--app-application-id", required=True)
    parser.add_argument("--agent-runtime-application-id", required=True)
    args = parser.parse_args(argv)
    verify_reviewed_function_execute_grants(
        deployment_workspace_client(),
        catalog=args.catalog,
        principals=(args.app_application_id, args.agent_runtime_application_id),
    )
    print("[function-grants] verified six exact effective direct EXECUTE grants")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
