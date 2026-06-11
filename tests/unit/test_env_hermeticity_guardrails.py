"""Guardrails against import-time process-environment mutation.

Incident (2026-06-11): ``tools/databricks/provision_genie_space.py`` called
``load_dotenv(.env.local)`` at module scope. Three unit-test files import
that module, so on any operator machine with a populated ``.env.local`` the
operator's real config (MIP_ADMIN_EMAILS=...) leaked into ``os.environ`` for
the remainder of the pytest process and flipped the fail-closed settings
contract test. CI stayed green (no .env.local there), masking the defect.

Rule: ``load_dotenv`` may only run inside a function (a CLI ``main()``
path), never as a module-scope side effect of import. ``dotenv_values`` is
exempt — it is a pure read that returns a dict without touching the
process environment.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

SCAN_ROOTS = (
    REPO / "backend",
    REPO / "tools",
    REPO / "pipelines",
)


def _module_scope_statements(tree: ast.Module):
    """Yield every statement that executes at import time.

    Descends through module-level ``if``/``try``/``with`` blocks (those run
    on import — the original offender hid inside a module-level ``try``)
    but NOT into function or class bodies.
    """
    stack: list[ast.stmt] = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.stmt):
                stack.append(child)


def _calls_load_dotenv(node: ast.stmt) -> bool:
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else ""
        )
        if name == "load_dotenv":
            return True
    return False


def test_no_module_scope_load_dotenv() -> None:
    offenders: list[str] = []
    for root in SCAN_ROOTS:
        for path in sorted(root.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if "load_dotenv" not in text:
                continue
            tree = ast.parse(text, filename=str(path))
            for stmt in _module_scope_statements(tree):
                # Imports of the symbol are fine; calling it is not.
                if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                    continue
                if _calls_load_dotenv(stmt):
                    offenders.append(
                        f"{path.relative_to(REPO)}:{stmt.lineno} calls "
                        "load_dotenv at module scope (import-time env mutation)"
                    )
    assert not offenders, "\n".join(offenders)
