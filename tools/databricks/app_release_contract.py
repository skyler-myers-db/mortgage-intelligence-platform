#!/usr/bin/env python3
"""Workspace path contract for the governed Databricks App source."""

from __future__ import annotations

import re


def files_root(*, principal: str, target: str, root_leaf: str | None = None) -> str:
    """The bundle files root; ``root_leaf`` (default: the target) is its last root segment.

    The live dev install keeps ``.../dev/files``; the isolated CI staging
    install (W5e, tools/databricks/ci_install_isolation.py) passes its own
    leaf, e.g. ``dev-ci``.
    """
    if (
        type(principal) is not str
        or not principal
        or principal != principal.strip()
        or principal in {".", ".."}
        or "/" in principal
        or any(character.isspace() or ord(character) < 32 for character in principal)
    ):
        raise RuntimeError("expected App source principal is not canonical")
    if (
        type(target) is not str
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", target) is None
    ):
        raise RuntimeError("expected App source target is not canonical")
    leaf = target if root_leaf is None else root_leaf
    if type(leaf) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", leaf) is None:
        raise RuntimeError("expected App source root leaf is not canonical")
    return (
        f"/Workspace/Users/{principal}/.bundle/"
        f"mortgage-intelligence-platform/{leaf}/files"
    )
