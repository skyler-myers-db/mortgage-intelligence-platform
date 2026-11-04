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

    assert any("assigned other than once in GenieClient.__init__" in item for item in violations(tree))


def test_a_client_built_outside_the_registry_breaches(tree: Path) -> None:
    (tree / "backend" / "services" / "second.py").write_text(
        "from backend.services.genie_client import GenieClient\n\n"
        'CLIENT = GenieClient(host="h", token="t", space_id="other")\n',
        encoding="utf-8",
    )

    assert violations(tree) == [
        "backend/services/second.py:1: imports GenieClient outside the registry, or under an alias",
        "backend/services/second.py:3: GenieClient is built outside the registry",
    ]


def test_another_client_class_with_its_own_space_breaches(tree: Path) -> None:
    """A second class may not carry a space id into a Genie URL (review of #306, tree 1)."""

    path = tree / CLIENT_MODULE
    path.write_text(
        path.read_text(encoding="utf-8")
        + "\n\nclass _OtherSpaceClient:\n"
        "    def __init__(self, host: str, space_id: str) -> None:\n"
        "        self._host = host\n"
        "        self._space_id = space_id\n\n"
        "    def ask(self, http, text: str):\n"
        '        return http.post(f"{self._host}/api/2.0/genie/spaces/{self._space_id}/start-conversation")\n',
        encoding="utf-8",
    )
    (tree / "backend" / "services" / "zz_any_space.py").write_text(
        "from backend.services.genie_client import _OtherSpaceClient\n\n\n"
        "def ask_any(http, host: str, space: str, text: str):\n"
        "    return _OtherSpaceClient(host, space).ask(http, text)\n",
        encoding="utf-8",
    )

    found = violations(tree)
    assert any("a Genie API path outside a plain GenieClient method" in item for item in found)
    assert sum("_space_id is named outside a GenieClient method" in item for item in found) == 2


def test_an_aliased_client_outside_the_registry_breaches(tree: Path) -> None:
    """An alias hides the call's name, so the import itself refuses (review of #306, tree 2)."""

    (tree / "backend" / "services" / "zz_alias.py").write_text(
        "from backend.services.genie_client import GenieClient as _Pinned\n\n\n"
        "def any_space_client(host: str, space: str, token_provider):\n"
        "    return _Pinned(host=host, space_id=space, token=token_provider)\n",
        encoding="utf-8",
    )

    assert violations(tree) == [
        "backend/services/zz_alias.py:1: imports GenieClient outside the registry, or under an alias"
    ]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "import backend.services.genie_client as gc\n\n"
            'CLIENT = gc.GenieClient(host="h", token="t", space_id="other")\n',
            "reaches GenieClient as an attribute",
        ),
        (
            "from backend.services.genie_client import *  # noqa: F403\n",
            "star-imports a module that binds GenieClient",
        ),
        (
            "from backend.services.genie_client_registry import *  # noqa: F403\n",
            "star-imports a module that binds GenieClient",
        ),
        (
            "import backend.services.genie_client.GenieClient\n",
            "binds the name GenieClient by an import",
        ),
        (
            "def move(client, space: str) -> None:\n    client._space_id = space\n",
            "_space_id is named outside a GenieClient method",
        ),
        (
            'def move(client, space: str) -> None:\n    setattr(client, "_space_id", space)\n',
            "_space_id is named outside a GenieClient method",
        ),
        (
            "class GenieClient:\n    pass\n",
            "defines another GenieClient",
        ),
    ],
    ids=["module-attribute", "star-client", "star-registry", "dotted-import", "attribute-store", "setattr", "shadow"],
)
def test_a_reference_that_reaches_another_space_breaches(tree: Path, source: str, expected: str) -> None:
    (tree / "backend" / "services" / "zz_reach.py").write_text(source, encoding="utf-8")

    found = violations(tree)
    assert found and all(item.startswith("backend/services/zz_reach.py") for item in found)
    assert any(expected in item for item in found), found


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        (
            "from backend.services.genie_client import (  # noqa: E402\n    GenieClient,\n",
            "from backend.services.genie_client import (  # noqa: E402\n    GenieClient as _Bare,\n",
            "imports GenieClient outside the registry, or under an alias",
        ),
        (
            "        space_id = _resolve_space_id()\n",
            "        space_id = _resolve_space_id()\n        space_id = space_id + '-other'\n",
            "must take space_id= from _resolve_space_id()",
        ),
        (
            "        bare = GenieClient(\n",
            "        factory = GenieClient\n        bare = GenieClient(\n",
            "GenieClient is referenced outside a governed call or an annotation",
        ),
        (
            "def _resolve_space_id() -> str:\n",
            "_resolve_space_id = str\n\n\ndef _resolve_space_id() -> str:\n",
            "_resolve_space_id must be one module-level function the registry never rebinds",
        ),
    ],
    ids=["registry-alias", "rebound-space-id", "bound-constructor", "rebound-resolver"],
)
def test_a_registry_edit_that_unpins_the_space_breaches(tree: Path, old: str, new: str, expected: str) -> None:
    _edit(tree, REGISTRY_MODULE, old, new)

    found = violations(tree)
    assert any(expected in item for item in found), found


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        (
            "        self._space_id = space_id\n",
            '        self._space_id = "other-space"\n',
            "must assign self._space_id = space_id exactly once",
        ),
        (
            "        self._space_id = space_id\n",
            '        space_id = "other-space"\n        self._space_id = space_id\n',
            "must assign self._space_id = space_id exactly once",
        ),
        (
            "    def _start_conversation(self, question: str) -> tuple[str, str]:\n",
            "    def _start_conversation(self, question: str) -> tuple[str, str]:\n"
            "        self = GenieClient.__new__(GenieClient)\n",
            "self is rebound in the client module",
        ),
        (
            "class GenieClient:\n",
            "class _Pinned(GenieClient):\n    pass\n\n\nclass GenieClient:\n",
            "GenieClient is referenced outside a governed call or an annotation",
        ),
    ],
    ids=["literal-space", "rebound-parameter", "rebound-self", "subclass"],
)
def test_a_client_edit_that_unpins_the_space_breaches(tree: Path, old: str, new: str, expected: str) -> None:
    _edit(tree, CLIENT_MODULE, old, new)

    found = violations(tree)
    assert any(expected in item for item in found), found


def test_a_genie_url_in_a_static_method_breaches(tree: Path) -> None:
    path = tree / CLIENT_MODULE
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "    @property\n    def space_id(self) -> str:\n",
            "    @staticmethod\n"
            "    def _url(self) -> str:\n"
            '        return f"/api/2.0/genie/spaces/{self._space_id}"\n\n'
            "    @property\n    def space_id(self) -> str:\n",
            1,
        ),
        encoding="utf-8",
    )

    found = violations(tree)
    assert any("a Genie API path outside a plain GenieClient method" in item for item in found), found


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
