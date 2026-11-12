"""The deployer reads the metastore through its summary, never ``metastores.get`` (spike S0(b)).

``tools/databricks/uc_metastore_read.py`` is the one reader the pre-stop owner
probe, the owner-equivalent probe, the treatment converger and the client
install checks share. The ratchets below pin what docs/deployment.md
("Metastore owner") claims:

* no module under ``tools/`` calls ``metastores.get`` (directly, through a
  name bound from ``.metastores``, or through ``getattr``) outside three
  modules that are owner-run or expect the call to be denied;
* no script, workflow or job does it either (inline Python and YAML
  included), by method, CLI command or REST path;
* every Unity Catalog grants read under ``tools/`` and in the Python of
  ``scripts/``, ``.github/workflows/`` and ``jobs/`` names its principal,
  except the owner-equivalent probe's direct-grant listing, which reads only
  the governed catalog and its schemas, never the metastore (spike S0(b′):
  an unfiltered metastore read answers a non-admin with only its own view).
  That exemption is one call site (module, method, enclosing function),
  whose callers pass ``"catalog"`` or ``"schema"`` and nothing else;
* no script, workflow or job reads the metastore's grants without naming a
  principal either, by CLI command (``grants get`` / ``get-effective``), REST
  path (``/unity-catalog/permissions/metastore/`` and its ``effective-``
  twin) or SDK call. ``--principal`` or ``principal=`` must be in the read's
  own command, never in a later pipe stage;
* ``_effective_privileges`` sums every returned assignment: a principal
  filter returns the grants that reach it through its groups, whose
  assignment names the group (spike S0(b′)), so filtering by principal would
  undercount.

Every ratchet has a positive control, so none can pass by matching nothing.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from databricks.sdk.errors import PermissionDenied

from tools.databricks import uc_metastore_read as reader
from tools.databricks.converge_campaign_treatment_access import _effective_privileges

ROOT = Path(__file__).resolve().parents[2]
# Owner-run, or expecting the admin-only read to be denied to its identity.
GET_ALLOWED = {
    # The owner's estate inventory: it requires the caller to own the metastore.
    "tools/databricks/estate/collect.py",
    # Both expect metastores.get to be DENIED to a low-privilege identity.
    "tools/databricks/verify_agent_proxy_identity_boundary.py",
    "tools/databricks/verify_verifier_identity_boundary.py",
}
# The owner-equivalent probe lists every direct grantee of the governed catalog
# and of each of its schemas (never the metastore): unfiltered by design. The
# exemption is that one call site (module, method, enclosing function), never
# the module: a second unfiltered read beside it is one too many.
OWNER_EQUIVALENT = "tools/databricks/prestop_owner_equivalent.py"
UNFILTERED_GRANTS_ALLOWED = [(OWNER_EQUIVALENT, "get", "_uc_grants")]
DEPLOYER_READERS = (
    "tools/databricks/prestop_owner_policy.py",
    "tools/databricks/prestop_owner_equivalent.py",
    "tools/databricks/converge_campaign_treatment_access.py",
    "tools/databricks/campaign_treatment_access_check.py",
    "tools/clients/install_checks.py",
)
TEXT_DIRS = ("scripts", ".github/workflows", "jobs")
TEXT_PATTERN = re.compile(r"metastores\s*\.\s*get\s*\(|metastores\s+get\b|/unity-catalog/metastores/")
# Spike S0(b′): a metastore grants read, as a script, workflow or job spells it.
_CLI_FLAGS = r"(?:\s+--?[\w-]+(?:=\S+|\s+(?!--?[\w-])\S+)??)*?"
METASTORE_GRANTS_PATTERN = re.compile(
    "|".join(
        (
            # CLI: `grants get` / `grants get-effective`, flags or not before the securable.
            r"\bgrants\s+get(?:-effective)?" + _CLI_FLAGS + r"""\s+["']?metastore["']?(?![\w./-])""",
            # REST: the direct and the effective permissions paths.
            r"/unity-catalog/(?:effective-)?permissions/metastore/",
            # SDK, inline or in a job: the securable as a string or an enum member.
            r"\bgrants\s*\.\s*get(?:_effective)?\s*\(.*?\bmetastore\b",
        )
    ),
    re.IGNORECASE,
)
# `--principal <p>` or `principal=<p>`; an empty value, `None` and a comparison name no one.
NAMES_PRINCIPAL = re.compile(r"""--principal(?:=|\s+)["']?[^\s"'-]|\bprincipal=(?!=|None\b)["']?[^\s"'&)]""")
# A pipe stage or a list item is another command: its `principal=` filters after the read.
_COMMAND_END = re.compile(r"\|\||&&|[|;]")


def _workspace(*, current: object = "metastore-1", summary: object = None, me: object = None) -> object:
    def get(_metastore_id: str) -> object:
        raise AssertionError("metastores.get is metastore-admin only")

    return SimpleNamespace(
        metastores=SimpleNamespace(
            current=lambda: SimpleNamespace(metastore_id=current),
            summary=lambda: summary
            if summary is not None
            else SimpleNamespace(metastore_id="metastore-1", owner="owner@example.invalid"),
            get=get,
        ),
        current_user=SimpleNamespace(me=lambda: me),
    )


# ------------------------------------------------------------------ the reader


def test_the_reader_returns_the_summary_and_its_owner_without_metastores_get() -> None:
    metastore = reader.current_metastore(_workspace())

    assert metastore.metastore_id == "metastore-1"
    assert reader.readable_owner(metastore) == "owner@example.invalid"
    assert reader.owner_state(metastore) == reader.OWNER_CANONICAL


@pytest.mark.parametrize("summary_id", ["metastore-2", " metastore-1", "metastore-1 ", None, 7, ""])
def test_a_summary_naming_another_or_a_noncanonical_metastore_refuses(summary_id: object) -> None:
    summary = SimpleNamespace(metastore_id=summary_id, owner="owner@example.invalid")

    with pytest.raises(reader.MetastoreReadRefused, match="names another metastore than the workspace's"):
        reader.current_metastore(_workspace(summary=summary))


@pytest.mark.parametrize("current", ["", None, "   "])
def test_a_workspace_without_a_current_metastore_refuses(current: object) -> None:
    with pytest.raises(reader.MetastoreReadRefused, match="no authoritative metastore identifier"):
        reader.current_metastore(_workspace(current=current))


def test_a_summary_read_error_propagates() -> None:
    workspace = _workspace()

    def denied() -> object:
        raise PermissionDenied("denied")

    workspace.metastores.summary = denied
    with pytest.raises(PermissionDenied):
        reader.current_metastore(workspace)


@pytest.mark.parametrize(
    ("fields", "state"),
    [
        ({}, reader.OWNER_ABSENT),
        ({"owner": None}, reader.OWNER_ABSENT),
        ({"owner": ""}, reader.OWNER_ABSENT),
        ({"owner": " padded"}, reader.OWNER_NONCANONICAL),
        ({"owner": "padded "}, reader.OWNER_NONCANONICAL),
        ({"owner": "   "}, reader.OWNER_NONCANONICAL),
        ({"owner": 7}, reader.OWNER_NONCANONICAL),
        ({"owner": "owner@example.invalid"}, reader.OWNER_CANONICAL),
    ],
)
def test_an_absent_owner_and_a_noncanonical_one_are_told_apart(fields: dict[str, object], state: str) -> None:
    item = SimpleNamespace(metastore_id="metastore-1", **fields)

    assert reader.owner_state(item) == state
    assert reader.readable_owner(item) == (fields["owner"] if state == reader.OWNER_CANONICAL else "")


@pytest.mark.parametrize(
    ("me", "admin"),
    [
        (SimpleNamespace(user_name="owner@example.invalid", groups=[]), True),
        (SimpleNamespace(user_name="OWNER@example.invalid"), True),
        (SimpleNamespace(user_name="sp", application_id="owner@example.invalid"), True),
        (SimpleNamespace(user_name="deployer", groups=[SimpleNamespace(display="Owner@Example.invalid")]), True),
        (SimpleNamespace(user_name="deployer", groups=[{"display": "owner@example.invalid"}]), True),
        (SimpleNamespace(user_name="deployer", groups=[SimpleNamespace(display="admins")]), False),
        # A projection without groups proves the caller's own names only.
        (SimpleNamespace(user_name="deployer"), False),
        (SimpleNamespace(user_name="deployer", groups="not-a-list"), False),
    ],
)
def test_the_deployer_is_a_metastore_admin_only_when_proven(me: object, admin: bool) -> None:
    workspace = _workspace(me=me)

    assert reader.deployer_is_metastore_admin(workspace, reader.current_metastore(workspace)) is admin


@pytest.mark.parametrize("owner", [None, "", " owner@example.invalid"])
def test_an_unreadable_owner_never_makes_the_deployer_an_admin(owner: object) -> None:
    me = SimpleNamespace(user_name="owner@example.invalid", groups=[SimpleNamespace(display=" owner@example.invalid")])
    metastore = SimpleNamespace(metastore_id="metastore-1", owner=owner)

    assert reader.deployer_is_metastore_admin(_workspace(me=me), metastore) is False


def test_the_owner_equivalent_caller_names_still_refuse_a_projection_without_groups() -> None:
    from tools.databricks.prestop_owner_equivalent import _caller_names

    with pytest.raises(RuntimeError, match="omitted its groups"):
        _caller_names(_workspace(me=SimpleNamespace(user_name="deployer")))
    with pytest.raises(RuntimeError, match="malformed collection"):
        _caller_names(_workspace(me=SimpleNamespace(user_name="deployer", groups="x")))
    names = _caller_names(_workspace(me=SimpleNamespace(user_name="Deployer", groups=[{"display": "G"}])))
    assert names == frozenset({"deployer", "g"})


def test_effective_privileges_sum_every_assignment_group_conveyed_ones_included() -> None:
    # Spike S0(b′): a principal-filtered read returns 'account users'' grant to the
    # App under the group's name; a principal-equality filter would drop it.
    pages = {
        None: SimpleNamespace(
            privilege_assignments=[
                SimpleNamespace(principal="account users", privileges=[SimpleNamespace(privilege="USE_MARKETPLACE_ASSETS")]),
            ],
            next_page_token="page-2",
        ),
        "page-2": SimpleNamespace(
            privilege_assignments=[SimpleNamespace(principal="app-client", privileges=["CREATE_CATALOG"])],
            next_page_token="",
        ),
    }
    calls: list[dict[str, object]] = []

    def get_effective(securable_type: str, full_name: str, **kwargs: object) -> object:
        calls.append({"securable": securable_type, **kwargs})
        return pages[kwargs["page_token"]]  # type: ignore[index]

    workspace = SimpleNamespace(grants=SimpleNamespace(get_effective=get_effective))

    held = _effective_privileges(
        workspace, securable_type="metastore", full_name="metastore-1", principal="app-client"
    )
    assert held == {"USE_MARKETPLACE_ASSETS", "CREATE_CATALOG"}
    assert all(call["principal"] == "app-client" for call in calls) and len(calls) == 2


# ------------------------------------------------------------------ ratchets


def _get_calls(tree: ast.AST) -> list[int]:
    """Lines calling ``get`` on ``<x>.metastores``, on a name bound from it, or via getattr."""

    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AnnAssign):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if _is_metastores(value):
                bound.update(target.id for target in targets if isinstance(target, ast.Name))
        if isinstance(node, ast.NamedExpr) and _is_metastores(node.value):
            bound.add(node.target.id)
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "get"
            and (_is_metastores(func.value) or (isinstance(func.value, ast.Name) and func.value.id in bound))
        ):
            lines.append(node.lineno)
        if isinstance(func, ast.Name) and func.id == "getattr" and len(node.args) >= 2:
            name = node.args[1]
            if isinstance(name, ast.Constant) and name.value == "metastores":
                lines.append(node.lineno)
            if isinstance(name, ast.Constant) and name.value == "get" and _is_metastores(node.args[0]):
                lines.append(node.lineno)
    return sorted(lines)


def _is_metastores(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "metastores"


def _tool_modules() -> list[Path]:
    return sorted((ROOT / "tools").rglob("*.py"))


def test_the_get_matcher_sees_every_shape_it_claims() -> None:
    # Positive control on synthetic source: each shape is found, a summary is not.
    source = (
        "workspace.metastores.get(metastore_id)\n"
        "client = ws\nm = client.metastores\nm.get('x')\n"
        "getattr(ws, 'metastores').current()\n"
        "getattr(ws.metastores, 'get')('x')\n"
        "lambda: workspace.metastores.get(inventory.metastore_id)\n"
        "workspace.metastores.summary()\nworkspace.metastores.current()\n"
    )
    assert _get_calls(ast.parse(source)) == [1, 4, 5, 6, 7]


def test_no_tool_reads_the_metastore_with_the_admin_only_get() -> None:
    hits = {
        str(path.relative_to(ROOT)): lines
        for path in _tool_modules()
        if (lines := _get_calls(ast.parse(path.read_text(encoding="utf-8"), str(path))))
    }

    assert set(hits) == GET_ALLOWED, hits
    # Positive control: the matcher finds the read in each allowlisted module.
    assert all(hits[name] for name in GET_ALLOWED)


def test_the_deployer_readers_all_go_through_the_shared_summary_reader() -> None:
    for name in DEPLOYER_READERS:
        assert "current_metastore" in (ROOT / name).read_text(encoding="utf-8"), name


def _text_hits(paths: list[Path]) -> dict[str, list[int]]:
    hits: dict[str, list[int]] = {}
    for path in paths:
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if TEXT_PATTERN.search(line):
                hits.setdefault(str(path.relative_to(ROOT)), []).append(number)
    return hits


def _text_files() -> list[Path]:
    return sorted(path for name in TEXT_DIRS for path in (ROOT / name).rglob("*") if path.is_file())


def test_no_script_workflow_or_job_reads_the_metastore_with_the_admin_only_get(tmp_path: Path) -> None:
    files = _text_files()
    names = {str(path.relative_to(ROOT)) for path in files}
    # The scan walks the real tree: the deploy entrypoint, its libraries, the
    # dispatch workflow and the job sources.
    assert {"scripts/deploy.sh", ".github/workflows/deploy-dev.yml"} <= names
    assert any(name.startswith("scripts/lib/") for name in names)
    assert any(name.startswith("jobs/") for name in names)

    assert _text_hits(files) == {}

    # Positive control: each spelling is found in shell, YAML and inline Python.
    planted = tmp_path / "planted"
    planted.mkdir()
    samples = {
        "inline.yml": "run: python -c 'print(client.metastores.get(mid).owner)'\n",
        "cli.sh": "databricks metastores get \"$MID\" -p x\n",
        "rest.sh": "curl \"$HOST/api/2.1/unity-catalog/metastores/$MID\"\n",
        "spaced.py": "w.metastores . get (x)\n",
        "clean.sh": "databricks metastores summary\ndatabricks metastores current\n",
    }
    for name, text in samples.items():
        (planted / name).write_text(text, encoding="utf-8")
    found = {Path(name).name for name in _text_hits_under(planted)}
    assert found == {"inline.yml", "cli.sh", "rest.sh", "spaced.py"}


def _text_hits_under(root: Path) -> list[str]:
    return [
        str(path)
        for path in sorted(root.iterdir())
        if any(TEXT_PATTERN.search(line) for line in path.read_text(encoding="utf-8").splitlines())
    ]


def _enclosing_functions(tree: ast.AST) -> dict[int, str]:
    """``id(node)`` -> the innermost named function that holds it (absent at module level)."""

    owner: dict[int, str] = {}
    # ast.walk is breadth-first, so an inner function overwrites its outer one.
    for function in ast.walk(tree):
        if isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            for node in ast.walk(function):
                owner[id(node)] = function.name
    return owner


def _names_a_principal(call: ast.Call) -> bool:
    principal = next((kw.value for kw in call.keywords if kw.arg == "principal"), None)
    return principal is not None and not (isinstance(principal, ast.Constant) and principal.value is None)


def _is_grants_read(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"get", "get_effective"}
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "grants"
    )


def _grants_calls(tree: ast.AST) -> list[tuple[str, int, bool, str]]:
    """Every ``<x>.grants.get`` / ``get_effective`` call: (method, line, names its principal, function)."""

    owner = _enclosing_functions(tree)
    return sorted(
        (node.func.attr, node.lineno, _names_a_principal(node), owner.get(id(node), "<module>"))
        for node in ast.walk(tree)
        if _is_grants_read(node)
    )


def _is_metastore_securable(node: ast.AST | None) -> bool:
    """The string in any case, an enum member (``SecurableType.METASTORE``) or its subscript."""

    if isinstance(node, ast.Subscript):
        node = node.slice
    if isinstance(node, ast.Constant):
        return str(node.value).lower() == "metastore"
    return isinstance(node, ast.Attribute) and node.attr == "METASTORE"


def _metastore_reads(tree: ast.AST) -> list[tuple[int, bool]]:
    """Every call whose securable is the metastore: (line, names its principal)."""

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        securable = next((kw.value for kw in node.keywords if kw.arg == "securable_type"), None)
        if securable is None and node.args:
            securable = node.args[0]
        if _is_metastore_securable(securable):
            found.append((node.lineno, _names_a_principal(node)))
    return sorted(found)


def _python_modules() -> list[Path]:
    """What the S0(b′) ratchets parse: ``tools/`` and the Python under the text directories."""

    return sorted({*_tool_modules(), *(path for path in _text_files() if path.suffix == ".py")})


def _parsed(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), str(path))


def test_the_grants_matchers_see_every_shape_they_claim() -> None:
    # Positive control on synthetic source: an enum member, a keyword securable,
    # `principal=None` and a multi-line call are each seen for what they are.
    source = (
        "def listing(workspace):\n"
        "    return workspace.grants.get(SecurableType.METASTORE, metastore_id)\n"
        "def filtered(workspace):\n"
        "    return workspace.grants.get_effective('metastore', metastore_id, principal=app)\n"
        "workspace.grants.get('METASTORE', metastore_id, principal=None)\n"
        "workspace.grants.get_effective(\n    securable_type=SecurableType['METASTORE'],\n    full_name=metastore_id,\n)\n"
        "workspace.grants.get('catalog', name)\n"
        "class Probe:\n"
        "    def read(self):\n"
        "        def inner():\n"
        "            return self.workspace.grants.get(kind, name)\n"
        "        return inner()\n"
    )
    tree = ast.parse(source)
    assert _grants_calls(tree) == [
        ("get", 2, False, "listing"),
        ("get", 5, False, "<module>"),
        ("get", 10, False, "<module>"),
        ("get", 14, False, "inner"),
        ("get_effective", 4, True, "filtered"),
        ("get_effective", 6, False, "<module>"),
    ]
    assert _metastore_reads(tree) == [(2, False), (4, True), (5, False), (6, False)]


def test_every_grants_read_names_its_principal_but_the_owner_equivalent_listing() -> None:
    # A list, not a set: a second unfiltered read in the exempt module, or in the
    # exempt function, is not the exempt call site.
    unfiltered: list[tuple[str, str, str]] = []
    total = 0
    for path in _python_modules():
        for method, _line, names, function in _grants_calls(_parsed(path)):
            total += 1
            if not names:
                unfiltered.append((str(path.relative_to(ROOT)), method, function))

    assert unfiltered == UNFILTERED_GRANTS_ALLOWED
    assert total >= 6, "positive control: the scan finds the converger's and the probes' reads"
    # The one unfiltered listing reads only the governed catalog and its schemas.
    source = (ROOT / OWNER_EQUIVALENT).read_text(encoding="utf-8")
    callers = re.findall(r"_uc_grants\(workspace, ([^,]+),", source)
    assert callers and set(callers) == {'"catalog"', '"schema"'}, callers


def _is_the_listing(node: ast.AST) -> bool:
    return (isinstance(node, ast.Name) and node.id == "_uc_grants") or (
        isinstance(node, ast.Attribute) and node.attr == "_uc_grants"
    )


def _listing_uses(trees: list[ast.AST]) -> tuple[list[object], int]:
    """Each ``_uc_grants`` call's securable (a string literal's value, else None), and every mention that is no call."""

    securables: list[object] = []
    mentions = 0
    for tree in trees:
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_the_listing(node.func):
                called.add(id(node.func))
                securable = next((kw.value for kw in node.keywords if kw.arg == "securable_type"), None)
                if securable is None and len(node.args) >= 2:
                    securable = node.args[1]
                literal = isinstance(securable, ast.Constant) and isinstance(securable.value, str)
                securables.append(securable.value if literal else None)
        for node in ast.walk(tree):
            mentions += _is_the_listing(node) and id(node) not in called
            if isinstance(node, ast.ImportFrom | ast.Import):
                mentions += sum(alias.name.rsplit(".", 1)[-1] == "_uc_grants" for alias in node.names)
    return sorted(securables, key=str), mentions


def test_the_exempt_listing_is_one_call_site_that_never_reads_the_metastore() -> None:
    """The unfiltered read's securable is the listing's own parameter, and only two literals reach it."""

    functions = [
        node
        for node in ast.walk(_parsed(ROOT / OWNER_EQUIVALENT))
        if isinstance(node, ast.FunctionDef) and node.name == "_uc_grants"
    ]
    assert len(functions) == 1
    listing = functions[0]
    reads = [node for node in ast.walk(listing) if _is_grants_read(node)]
    assert len(reads) == 1 and reads[0].func.attr == "get"
    securable = reads[0].args[0]
    assert isinstance(securable, ast.Name) and securable.id == "securable_type"
    assert [argument.arg for argument in listing.args.args] == ["workspace", "securable_type", "full_name"]
    # ...which the function never rebinds.
    assert not [
        node
        for node in ast.walk(listing)
        if isinstance(node, ast.Name) and node.id == "securable_type" and not isinstance(node.ctx, ast.Load)
    ]
    # Every caller, in any module and any spelling of the call, passes one of the
    # two literals; the listing is never aliased, imported or passed along.
    assert _listing_uses([_parsed(path) for path in _python_modules()]) == (["catalog", "schema"], 0)
    # Positive control: a keyword call the regex above cannot see, a securable
    # that is no literal, an alias and an import are each read for what they are.
    planted = (
        "from tools.databricks.prestop_owner_equivalent import _uc_grants\n"
        "_uc_grants(workspace=w, securable_type='metastore', full_name=m)\n"
        "probe._uc_grants(w, SecurableType.METASTORE, m)\n"
        "listing = _uc_grants\n"
    )
    assert _listing_uses([ast.parse(planted)]) == ([None, "metastore"], 2)


def test_every_metastore_grant_read_names_its_principal() -> None:
    sites = 0
    for path in _python_modules():
        for line, names in _metastore_reads(_parsed(path)):
            sites += 1
            assert names, f"{path.relative_to(ROOT)}:{line}"
    # Positive control: the attestation's live read, its owner command and the
    # agentic verifiers' baseline reads are all found.
    assert sites >= 4


def _commands(text: str) -> list[tuple[int, str]]:
    """(first line, simple command): backslash continuations joined, pipe stages and lists split."""

    found: list[tuple[int, str]] = []
    pending, start = "", 0
    for number, line in enumerate([*text.splitlines(), ""], 1):
        if not pending:
            start = number
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        found.extend((start, command) for command in _COMMAND_END.split(pending + line))
        pending = ""
    return found


def _unnamed_metastore_grant_reads(text: str) -> list[int]:
    return sorted(
        {
            number
            for number, command in _commands(text)
            if METASTORE_GRANTS_PATTERN.search(command) and not NAMES_PRINCIPAL.search(command)
        }
    )


def test_no_script_workflow_or_job_reads_the_metastore_grants_without_a_principal(tmp_path: Path) -> None:
    """Spike S0(b′): an unfiltered metastore read answers a non-admin with only its own view."""

    files = _text_files()
    names = {str(path.relative_to(ROOT)) for path in files}
    assert {"scripts/deploy.sh", ".github/workflows/deploy-dev.yml"} <= names
    assert any(name.startswith("scripts/lib/") for name in names)
    assert any(name.startswith("jobs/") and name.endswith(".py") for name in names)

    hits = {
        str(path.relative_to(ROOT)): lines
        for path in files
        if (lines := _unnamed_metastore_grant_reads(path.read_text(encoding="utf-8", errors="replace")))
    }
    assert hits == {}

    # Positive control: each spelling is found in shell, YAML and Python, and
    # only a principal in the read's own command clears it.
    unnamed = {
        "cli.sh": 'ms="$(databricks grants get-effective metastore "$MID" -o json)"\n',
        "cli_direct.sh": 'databricks grants get metastore "$MID"\n',
        "cli_flags_first.sh": 'databricks grants get-effective -o json --debug "METASTORE" "$MID"\n',
        "cli_continued.sh": 'databricks grants get-effective \\\n  metastore "$MID" \\\n  -o json\n',
        "cli_then_filtered.sh": "databricks grants get-effective metastore \"$MID\" | jq 'select(.principal==\"x\")'\n",
        "cli_later_stage.sh": 'databricks grants get metastore "$MID" | python -c "f(principal=app)"\n',
        "cli_empty.sh": 'databricks grants get-effective metastore "$MID" --principal ""\n',
        "rest.sh": 'databricks api get "/api/2.1/unity-catalog/effective-permissions/metastore/$MID"\n',
        "rest_direct.yml": 'run: curl "$HOST/api/2.1/unity-catalog/permissions/metastore/$MID?max_results=0"\n',
        "inline.yml": "run: python -c 'print(w.grants.get_effective(\"metastore\", mid))'\n",
        "enum.py": "listing = workspace.grants.get(SecurableType.METASTORE, metastore_id)\n",
        "none.py": 'workspace.grants.get_effective("metastore", mid, principal=None)\n',
    }
    named = {
        "named_cli.sh": 'databricks grants get-effective metastore "$MID" --principal "$APP" | jq .\n',
        "named_cli_first.sh": 'databricks grants get-effective --principal="$APP" metastore "$MID"\n',
        "named_continued.sh": 'databricks grants get-effective metastore "$MID" \\\n  --principal "$APP"\n',
        "named_rest.sh": 'databricks api get "/api/2.1/unity-catalog/effective-permissions/metastore/$MID?principal=$APP"\n',
        "named_inline.yml": "run: python -c 'w.grants.get_effective(\"metastore\", mid, principal=app)'\n",
        "catalog.sh": 'databricks grants get catalog "$CATALOG"\ndatabricks grants get-effective schema "$S"\n',
        "summary.sh": 'databricks grants get-effective catalog "$METASTORE_CATALOG" # metastore_summary\n',
        "rest_catalog.sh": 'curl "$HOST/api/2.1/unity-catalog/effective-permissions/catalog/$CATALOG"\n',
    }
    planted = tmp_path / "planted"
    planted.mkdir()
    for name, text in {**unnamed, **named}.items():
        (planted / name).write_text(text, encoding="utf-8")
    found = {
        path.name: _unnamed_metastore_grant_reads(path.read_text(encoding="utf-8"))
        for path in sorted(planted.iterdir())
    }
    assert {name for name, lines in found.items() if lines} == set(unnamed)
    assert all(lines == [1] for name, lines in found.items() if name in unnamed), found
