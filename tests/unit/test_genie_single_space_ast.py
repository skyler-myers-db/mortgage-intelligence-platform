"""Every backend Genie API call goes through the one client bound to the pinned space.

This is the compensating static control an elevated App exposure depends on
(plan 4.12, M2-PR4): ``accepted_exposure`` refuses such an entry while
``genie_single_space.violations()`` reports anything. The plan named
``backend/tests/test_genie_single_space_ast.py``; this repository keeps its
tests under ``tests/unit``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tools.databricks.estate.genie_single_space import (
    CLIENT_MODULE,
    REGISTRY_MODULE,
    REPO,
    violations,
)


def test_the_backend_holds_the_single_space_shape() -> None:
    assert violations() == []


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A minimal backend: the real client and registry, and one ordinary module."""

    for module in (CLIENT_MODULE, REGISTRY_MODULE):
        target = tmp_path / module
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / module, target)
    (tmp_path / "backend" / "services" / "leads.py").write_text(
        '"""Lead queue: no Genie here."""\n\nGENIE_LABEL = "Ask Genie"\n', encoding="utf-8"
    )
    return tmp_path


def _edit(root: Path, module: str, old: str, new: str) -> None:
    path = root / module
    text = path.read_text(encoding="utf-8")
    assert text.count(old) >= 1, f"{old!r} is not in {module}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_the_copied_tree_is_clean(tree: Path) -> None:
    assert violations(tree) == []


def test_a_genie_path_outside_the_client_breaches(tree: Path) -> None:
    (tree / "backend" / "services" / "bypass.py").write_text(
        'def url(host: str, space: str) -> str:\n    return f"{host}/api/2.0/genie/spaces/{space}"\n',
        encoding="utf-8",
    )

    assert violations(tree) == ["backend/services/bypass.py:2: a Genie API path outside " + CLIENT_MODULE]


def test_a_client_url_for_another_space_breaches(tree: Path) -> None:
    _edit(
        tree,
        CLIENT_MODULE,
        'url = f"{self._host}/api/2.0/genie/spaces/{self._space_id}/start-conversation"',
        'url = f"{self._host}/api/2.0/genie/spaces/{space_id}/start-conversation"',
    )

    found = violations(tree)
    assert len(found) == 1 and "does not address self._space_id" in found[0]


def test_a_reassigned_space_id_breaches(tree: Path) -> None:
    _edit(
        tree,
        CLIENT_MODULE,
        "    def _start_conversation(self, question: str) -> tuple[str, str]:\n",
        "    def _start_conversation(self, question: str) -> tuple[str, str]:\n"
        "        self._space_id = question\n",
    )

    assert any("reassigned outside __init__" in item for item in violations(tree))


def test_a_client_built_outside_the_registry_breaches(tree: Path) -> None:
    (tree / "backend" / "services" / "second.py").write_text(
        "from backend.services.genie_client import GenieClient\n\n"
        'CLIENT = GenieClient(host="h", token="t", space_id="other")\n',
        encoding="utf-8",
    )

    assert violations(tree) == ["backend/services/second.py:3: GenieClient is built outside the registry"]


def test_a_registry_space_id_not_from_the_resolver_breaches(tree: Path) -> None:
    _edit(tree, REGISTRY_MODULE, "            space_id=space_id,\n", '            space_id="other-space",\n')

    found = violations(tree)
    assert len(found) == 1 and "must take space_id= from _resolve_space_id()" in found[0]


def test_the_sdk_genie_api_breaches(tree: Path) -> None:
    (tree / "backend" / "services" / "sdk_genie.py").write_text(
        "def ask(workspace, space_id: str, text: str) -> object:\n"
        "    return workspace.genie.start_conversation_and_wait(space_id, text)\n",
        encoding="utf-8",
    )

    assert violations(tree) == [
        "backend/services/sdk_genie.py:2: reaches the SDK Genie API (.genie) instead of the pinned client"
    ]


def test_a_missing_client_cannot_hold(tree: Path) -> None:
    (tree / CLIENT_MODULE).unlink()

    assert violations(tree) == ["the Genie client or its registry is missing, so the control cannot hold"]
