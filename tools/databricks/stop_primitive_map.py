"""Where an App can be stopped, started, deployed or updated, and who may ask (attack O16).

A shell stop outside the window is easy to spot; one inside a Python tool is
not. This module is the reviewed map of both, and the scanners that keep it
true (``tests/unit/test_stop_primitive_ast_contract.py`` and
``tests/unit/test_quiesce_window_lexical_contract.py``):

* ``python_primitive_sites`` walks every module under ``tools/`` and ``jobs/``
  with ``ast`` and returns each ``<expr>.apps.<method>`` whose method is an
  SDK App stop, start, deploy or update (``SDK_METHODS``, their ``*_and_wait``
  forms included), called or only referenced, and any
  ``getattr(<client>, <name>)`` whose name is one of them or not a constant.
  The client is ``<expr>.apps``, ``getattr(<expr>, "apps")``, a name
  ``apps`` (a parameter), or any name bound from an expression holding one:
  plain, tuple and starred unpacking, ``for``, ``with``, a walrus, a default
  argument, and chains of those (a fixed point; it over-approximates, never
  under). A raw REST path to an App's ``start`` or ``stop`` in any string is
  a site too (``rest:<verb>``). ``PRIMITIVE_SITES`` must equal that set
  exactly. ``jobs/window_deadman.py`` (M0-PR9) holds one: the
  dead-man's ``apps start``, reached by no shell file (it runs only as the
  one-time run the window submits).
* ``python_job_run_sites`` does the same for ``<expr>.jobs.submit``,
  ``run_now``, ``repair_run`` and ``cancel_run`` (``JOB_RUN_METHODS``) and the
  REST ``jobs/runs/submit``, ``jobs/run-now``, ``repair`` and ``cancel``
  paths: the dead-man's submit and cancel sit only in
  ``tools/databricks/quiesce_window_deadman.py``, reached only through the
  window's ``open`` and ``cancel-deadman`` modes, which only the window library
  calls; the data phase's run-now (M1-PR6) sits only in
  ``tools/databricks/job_phase_runner.py``'s ``start_run``, reached only
  through its ``run`` mode, which only the data-phase library calls
  (``JOB_RUN_SITES``). ``python_pipeline_run_sites`` does the same for a
  pipeline update (``start_update``, ``stop``; the REST
  ``pipelines/<id>/updates|stop`` paths), a silver refresh by another door:
  there is none (``PIPELINE_RUN_SITES`` is empty).
* ``reaching_entry_points`` follows a coarse static call graph (calls and
  function references through imports, nested functions folded into their
  top-level function) from each site back to the CLI modules whose ``main``
  reaches it. ``ENTRY_POINTS`` must equal that set exactly, and names each
  entry's mutating modes and the shell files allowed to invoke them.
* ``shell_violations`` reads shell text with continuation lines joined and
  reports every invocation of a mutating mode (``-m <module>`` or the
  ``tools/databricks/<module>.py`` path; the mode is read from that one
  command's own tokens, up to ``;``, ``&&``, ``|`` or a comment), every
  import of a stop module (a heredoc or ``-c`` can call it directly), every
  ``databricks apps stop|start`` or REST ``apps/<name>/start|stop`` path,
  every inline SDK primitive and every inline ``.apps`` client that is not
  directly a read call (a tuple alias in a heredoc) outside its allowlisted
  files; and the same for one-time job runs (``databricks jobs submit``, the
  REST ``jobs/runs/submit|cancel`` paths, an inline ``.jobs`` client) outside
  the window library; and every ``databricks bundle run <job>`` outside the
  reviewed shell job runs (``SHELL_JOB_RUN_SITES``, M1-PR6): ``--phase data``
  runs its jobs only through the runner, and a run with no ``--phase`` keeps
  today's ``bundle run`` sites, each named; and every pipeline update or stop
  (``databricks pipelines start-update|stop|run|dry-run``, the REST paths, an
  inline ``.pipelines.start_update``) in any shell file.

The window library and the EXIT-trap library are the only shell callers of a
stop; every top-level script is scanned too, so the owner-run WR5 entry
(``scripts/lakebase_handover_window.sh``, M2-PR8) only calls the window
library's functions. Everything is matched lexically, so a comment line never
counts.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SDK_METHODS = frozenset(
    {
        "stop",
        "stop_and_wait",
        "start",
        "start_and_wait",
        "deploy",
        "deploy_and_wait",
        "update",
        "create_update",
        "create_update_and_wait",
    }
)
# Job runs: the dead-man's one-time submit and cancel (M0-PR9) and the data
# phase's run-now (M1-PR6); a repair reruns a run's tasks, so it is one too.
JOB_RUN_METHODS = frozenset(
    {
        "submit",
        "submit_and_wait",
        "run_now",
        "run_now_and_wait",
        "repair_run",
        "repair_run_and_wait",
        "cancel_run",
        "cancel_run_and_wait",
        "cancel_all_runs",
    }
)
WINDOW_LIBRARY = "scripts/lib/deploy_quiesce_window_lifecycle.sh"
EXIT_TRAP_LIBRARY = "scripts/lib/deploy_app_release_lifecycle.sh"
STOP_LIBRARIES = frozenset({WINDOW_LIBRARY, EXIT_TRAP_LIBRARY})
# The data phase (M1-PR6): the only shell caller of the job phase runner.
DATA_PHASE_LIBRARY = "scripts/lib/deploy_governed_phases_lifecycle.sh"
# The release's binding step: the only shell caller of the online description fix-up.
BUNDLE_APPLY_STEP = "scripts/lib/deploy_step_bundle_apply.sh"
JOB_RUN_LIBRARIES = frozenset({WINDOW_LIBRARY, DATA_PHASE_LIBRARY})
# Shell file -> the bundle jobs it may ``databricks bundle run``: the release's
# deploy-time jobs and the gold refresh's reviewed reconcile wrapper, and the
# no-phase data refresh block, which ``--phase release`` sources but skips
# (M1-PR6). ``--phase data`` runs its jobs only through the runner.
SHELL_JOB_RUN_SITES: Mapping[str, frozenset[str]] = {
    "scripts/lib/deploy_step_lakebase_and_uc_grants.sh": frozenset(
        {"mip_lakebase_migrate", "mip_init_catalog_schemas", "mip_refresh_scores"}
    ),
    "scripts/lib/deploy_step_app_promotion_and_refresh.sh": frozenset(
        {"mip_fred_rates_ingest", "mip_refresh_silver", "mip_kpi_snapshot"}
    ),
}

# (module path, top-level function, SDK method): every reviewed SDK call site.
PRIMITIVE_SITES = frozenset(
    {
        ("tools/databricks/stop_app_fail_closed.py", "stop_app_fail_closed", "stop"),
        ("tools/databricks/app_start_deployment.py", "start_app", "start"),
        ("tools/databricks/app_deployment_repin.py", "redeploy_signed_blue", "deploy_and_wait"),
        # The core tier's tier-b restore (M2-PR7b): blue's restore payload, deploy only.
        ("tools/databricks/core_blue_restore.py", "redeploy_core_blue", "deploy_and_wait"),
        # A create-update masked to resources (spike S2: a PATCH clears the description).
        (
            "tools/databricks/app_rollback_resource_contract.py",
            "restore_signed_app_resource_contract",
            "create_update",
        ),
        # The dead-man's restart (M0-PR9): start and get only, never a stop.
        ("jobs/window_deadman.py", "start_blue", "start"),
        # The release's online description fix-up (spike S2): a create-update
        # masked to description, which restarts nothing and re-grants nothing.
        ("tools/databricks/app_description_fixup.py", "apply_bundle_description", "create_update"),
    }
)
# The scanned roots: the deploy tooling and the job sources (M0-PR9).
SCAN_ROOTS = ("tools", "jobs")
# (module path, top-level function, SDK method): every job submit, run-now or
# cancel: the dead-man's arm and its cancel (M0-PR9) and the data phase's
# ledgered run-now (M1-PR6), nothing else.
JOB_RUN_SITES: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("tools/databricks/quiesce_window_deadman.py", "submit_deadman", "submit"),
        ("tools/databricks/quiesce_window_deadman.py", "cancel_deadman", "cancel_run"),
        ("tools/databricks/job_phase_runner.py", "start_run", "run_now"),
    }
)
# A pipeline update or stop is a silver refresh by another door (M1-PR6): no
# tool or job source holds one; silver refreshes only as mip_refresh_silver.
PIPELINE_RUN_METHODS = frozenset({"start_update", "stop", "stop_and_wait"})
PIPELINE_RUN_SITES: frozenset[tuple[str, str, str]] = frozenset()


@dataclass(frozen=True)
class EntryPoint:
    """A CLI module that reaches a primitive: its mutating modes and their shell callers.

    ``modes`` maps a mode to whether it mutates. A positional mode is the first
    argument after the module; ``--flag`` modes are flags; ``(default)`` is an
    invocation without any listed flag.
    """

    modes: Mapping[str, bool]
    callers: frozenset[str] = STOP_LIBRARIES
    positional: bool = True


ENTRY_POINTS: Mapping[str, EntryPoint] = {
    "tools.databricks.stop_app_fail_closed": EntryPoint(
        modes={"(default)": True, "--assert-identity-only": False}, positional=False
    ),
    "tools.databricks.converge_lakebase_oauth_role": EntryPoint(
        modes={"--stop-app-for-mutation": True, "(default)": False},
        callers=frozenset({WINDOW_LIBRARY}),
        positional=False,
    ),
    "tools.databricks.app_deployment_rollback": EntryPoint(
        modes={"ensure": True, "restore": True, "repin": True, "capture": False, "inspect": False}
    ),
    "tools.databricks.app_deployment_rollback_cli": EntryPoint(
        modes={"ensure": True, "restore": True, "repin": True, "capture": False, "inspect": False}
    ),
    # The core tier's step-0a proof and tier-b restore (M2-PR7b); its capture and
    # restore-payload modes deploy, start and stop nothing. Its repin (the
    # window's v7 core re-pin, spike S7) is reviewed as mutating, as v6's is.
    "tools.databricks.app_core_rollback": EntryPoint(
        modes={
            "ensure": True,
            "restore": True,
            "repin": True,
            "capture": False,
            "restore-payload": False,
        }
    ),
    # The dead-man job (M0-PR9): only the window's submit runs it, no shell file.
    "jobs.window_deadman": EntryPoint(
        modes={"(default)": True}, callers=frozenset(), positional=False
    ),
    "tools.databricks.quiesce_window": EntryPoint(
        modes={
            "open": True,
            "start": True,
            "await": True,
            "applicable": False,
            "plan": False,
            "close": False,
            "status": False,
            "assert-serving": False,
            # WR5 (M2-PR8): read-only plan and start proof; the window library stops and starts.
            "plan-wr5": False,
            "await-wr5": False,
            # M0-PR9: the dead-man's cancel, after the journal closes.
            "cancel-deadman": True,
        },
        callers=frozenset({WINDOW_LIBRARY}),
    ),
    # The online description fix-up (spike S2): it updates the description
    # alone, never a binding, a deployment or the compute.
    "tools.databricks.app_description_fixup": EntryPoint(
        modes={"(default)": True},
        callers=frozenset({BUNDLE_APPLY_STEP}),
        positional=False,
    ),
    # The data phase's runner (M1-PR6): plan and quiet are read-only; begin,
    # run and close write the signed ledger, and run starts the job. No App call.
    "tools.databricks.job_phase_runner": EntryPoint(
        modes={"plan": False, "quiet": False, "begin": True, "run": True, "close": True},
        callers=frozenset({DATA_PHASE_LIBRARY}),
    ),
}
# The modules that hold a primitive site: importing one is a stop path.
SITE_MODULES = frozenset(
    ".".join(Path(path).with_suffix("").parts) for path, _function, _method in PRIMITIVE_SITES
)
# The modules that hold a job-run site: importing one is the window's alone.
JOB_RUN_MODULES = frozenset(
    ".".join(Path(path).with_suffix("").parts) for path, _function, _method in JOB_RUN_SITES
)
# A raw REST call that starts or stops an App, or runs or cancels a one-time
# job run: in a string of a tool or a shell file, as much a primitive as the SDK.
_REST_APP = re.compile(r"/api/2\.\d+/apps/[^\s\"'/]+/(start|stop)\b")
_REST_JOB_RUN = re.compile(r"/api/2\.\d+/jobs/(?:runs/)?(submit|run-now|repair|cancel-all|cancel)\b")
# The reads an inline client may make; any other use of it is fail-closed.
_APP_READS = ("get", "list", "list_deployments", "get_deployment", "get_permissions", "get_permission_levels")
_JOB_READS = ("get", "list", "get_run", "list_runs", "get_run_output")
# Shell commands that stop or start an App directly, and inline SDK calls.
_CLI_PRIMITIVE = re.compile(r"\bdatabricks\s+apps\s+(stop|start)\b|" + _REST_APP.pattern)
_INLINE_SDK = re.compile(
    r"\.apps\.(" + "|".join(sorted(SDK_METHODS)) + r")\s*\(|\b(deploy|start|stop)_and_wait\s*\("
    + r"|\.apps\b(?!\.(?:" + "|".join(_APP_READS) + r")\s*\()"
    + r"|\bgetattr\(\s*[^,()]+,\s*[\"']apps[\"']"
)
# A one-time job run or its cancel (M0-PR9): the window library's alone.
_JOB_RUN = re.compile(
    r"\bdatabricks\s+jobs\s+(submit|run-now|repair-run|cancel-run|cancel-all-runs)\b|\.jobs\.("
    + "|".join(sorted(JOB_RUN_METHODS))
    + r")\s*\("
    + r"|\.jobs\b(?!\.(?:" + "|".join(_JOB_READS) + r")\s*\()"
    + r"|\bgetattr\(\s*[^,()]+,\s*[\"']jobs[\"']|"
    + _REST_JOB_RUN.pattern
)


# A bundle job run from shell (M1-PR6): its key is the command's first operand.
_BUNDLE_RUN = re.compile(r"\bdatabricks\s+bundle\s+run\b(?P<rest>[^;&|#]*)")
# A pipeline update or stop (M1-PR6): reviewed in no shell file. ``run`` starts
# an update, and ``dry-run`` a validate-only one whose ``--restart`` stops a
# running update.
_REST_PIPELINE_RUN = re.compile(r"/api/2\.\d+/pipelines/[^\s\"'/]+/(updates|stop)\b")
_PIPELINE_RUN = re.compile(
    r"\bdatabricks\s+pipelines\s+(start-update|stop|run|dry-run)\b|\.pipelines\.("
    + "|".join(sorted(PIPELINE_RUN_METHODS))
    + r")\s*\(|"
    + _REST_PIPELINE_RUN.pattern
)


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(REPO).with_suffix("").parts)


def _python_files(root: Path | None) -> list[Path]:
    roots = [root] if root is not None else [REPO / name for name in SCAN_ROOTS]
    return sorted(
        path for base in roots for path in base.rglob("*.py") if "__pycache__" not in path.parts
    )


def _is_service_client(node: ast.AST | None, service: str, aliases: frozenset[str] = frozenset()) -> bool:
    """``<expr>.<service>``, ``getattr(<expr>, "<service>")``, a name ``<service>`` or an alias."""

    if isinstance(node, ast.Attribute):
        return node.attr == service and isinstance(node.ctx, ast.Load)
    if isinstance(node, ast.Name):
        return node.id == service or node.id in aliases
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == service
    )


def _bindings(node: ast.AST) -> list[tuple[ast.AST, ast.AST]]:
    """(target, value) for every way ``node`` binds a name to an expression."""

    if isinstance(node, ast.Assign):
        return [(target, node.value) for target in node.targets]
    if isinstance(node, ast.AnnAssign | ast.NamedExpr) and node.value is not None:
        return [(node.target, node.value)]
    if isinstance(node, ast.For | ast.AsyncFor | ast.comprehension):
        return [(node.target, node.iter)]
    if isinstance(node, ast.With | ast.AsyncWith):
        return [(item.optional_vars, item.context_expr) for item in node.items if item.optional_vars]
    if isinstance(node, ast.arguments):
        positional = [*node.posonlyargs, *node.args]
        paired = zip(positional[len(positional) - len(node.defaults) :], node.defaults, strict=True)
        keyword = zip(node.kwonlyargs, node.kw_defaults, strict=True)
        return [
            (ast.Name(id=arg.arg, ctx=ast.Store()), default)
            for arg, default in (*paired, *keyword)
            if default is not None
        ]
    return []


def _bind(
    target: ast.AST, value: ast.AST, service: str, exact: set[str], tainted: set[str]
) -> None:
    """Pair an unpacking element by element; a client is an exact alias, else it taints.

    ``exact`` names are bound to the client itself (``apps = w.apps``, ``a, j =
    w.apps, w.jobs``); ``tainted`` names to anything holding one (``app =
    w.apps.get(x)``, a starred unpacking). Both are scanned for primitive
    methods; only exact ones for a dynamic ``getattr``.
    """

    sequence = ast.Tuple | ast.List
    if (
        isinstance(target, sequence)
        and isinstance(value, sequence)
        and len(target.elts) == len(value.elts)
        and not any(isinstance(item, ast.Starred) for item in (*target.elts, *value.elts))
    ):
        for item, part in zip(target.elts, value.elts, strict=True):
            _bind(item, part, service, exact, tainted)
        return
    if isinstance(target, ast.Name) and _is_service_client(value, service, frozenset(exact)):
        exact.add(target.id)
        return
    known = frozenset(exact | tainted)
    if any(_is_service_client(node, service, known) for node in ast.walk(value)):
        tainted.update(node.id for node in ast.walk(target) if isinstance(node, ast.Name))


def _service_aliases(tree: ast.AST, service: str) -> tuple[set[str], set[str]]:
    """(exact, tainted) names bound from a service client anywhere in ``tree``, to a fixed point."""

    pairs = [pair for node in ast.walk(tree) for pair in _bindings(node)]
    exact: set[str] = set()
    tainted: set[str] = set()
    while True:
        before = len(exact) + len(tainted)
        for target, value in pairs:
            _bind(target, value, service, exact, tainted)
        if len(exact) + len(tainted) == before:
            return exact, tainted


def _apps_aliases(tree: ast.AST) -> set[str]:
    exact, tainted = _service_aliases(tree, "apps")
    return exact | tainted


def python_primitive_sites(root: Path | None = None) -> set[tuple[str, str, str]]:
    """Every SDK primitive on the Apps client below ``root``, aliases and REST paths included."""

    return _service_sites(root, "apps", SDK_METHODS, _REST_APP)


def python_job_run_sites(root: Path | None = None) -> set[tuple[str, str, str]]:
    """Every one-time job submit or cancel on the Jobs client below ``root`` (M0-PR9)."""

    return _service_sites(root, "jobs", JOB_RUN_METHODS, _REST_JOB_RUN)


def python_pipeline_run_sites(root: Path | None = None) -> set[tuple[str, str, str]]:
    """Every pipeline update or stop on the Pipelines client below ``root`` (M1-PR6)."""

    return _service_sites(root, "pipelines", PIPELINE_RUN_METHODS, _REST_PIPELINE_RUN)


def _site_method(
    node: ast.AST,
    service: str,
    methods: frozenset[str],
    aliases: tuple[frozenset[str], frozenset[str]],
) -> str:
    """The primitive ``node`` names on a client, called or referenced, or ""."""

    exact, tainted = aliases
    # Any receiver holding a client: a name, a walrus, a conditional, a subscript.
    if (
        isinstance(node, ast.Attribute)
        and node.attr in methods
        and any(_is_service_client(part, service, exact | tainted) for part in ast.walk(node.value))
    ):
        return node.attr
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and _is_service_client(node.args[0], service, exact)
    ):
        name = node.args[1]
        if not isinstance(name, ast.Constant):
            return "<dynamic>"
        return str(name.value) if name.value in methods else ""
    return ""


def _rest_text(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ast.unparse(node) if isinstance(node, ast.JoinedStr) else ""


def _service_sites(
    root: Path | None, service: str, methods: frozenset[str], rest: re.Pattern[str]
) -> set[tuple[str, str, str]]:
    sites: set[tuple[str, str, str]] = set()
    for path in _python_files(root):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        exact, tainted = _service_aliases(tree, service)
        aliases = (frozenset(exact), frozenset(tainted))
        for top in tree.body:
            owner = top.name if isinstance(top, ast.FunctionDef | ast.ClassDef) else "<module>"
            for node in ast.walk(top):
                method = _site_method(node, service, methods, aliases)
                if method:
                    sites.add((str(path.relative_to(REPO)), owner, method))
                for match in rest.finditer(_rest_text(node)):
                    sites.add((str(path.relative_to(REPO)), owner, f"rest:{match.group(1)}"))
    return sites


def _imports(tree: ast.Module) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    """Name aliases (``from m import f as g``) and module aliases, anywhere in the module."""

    names: dict[str, tuple[str, str]] = {}
    modules: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("tools"):
            for alias in node.names:
                target = f"{node.module}.{alias.name}"
                names[alias.asname or alias.name] = (node.module, alias.name)
                modules[alias.asname or alias.name] = target
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("tools"):
                    modules[alias.asname or alias.name] = alias.name
    return names, modules


def call_graph(root: Path | None = None) -> dict[tuple[str, str], set[tuple[str, str]]]:
    """(module, function) -> the (module, function) pairs it calls or references."""

    graph: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for path in _python_files(root):
        module = _module_name(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names, modules = _imports(tree)
        local = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
        for top in tree.body:
            if not isinstance(top, ast.FunctionDef):
                continue
            edges: set[tuple[str, str]] = set()
            for node in ast.walk(top):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    if node.id in names:
                        edges.add(names[node.id])
                    elif node.id in local:
                        edges.add((module, node.id))
                elif (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id in modules
                ):
                    edges.add((modules[node.value.id], node.attr))
            graph[(module, top.name)] = edges
    return graph


def reaching_entry_points(
    root: Path | None = None, sites: set[tuple[str, str, str]] | None = None
) -> dict[tuple[str, str, str], set[str]]:
    """For each primitive site (or each of ``sites``), the CLI modules whose ``main`` reaches it."""

    graph = call_graph(root)
    reverse: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for caller, callees in graph.items():
        for callee in callees:
            reverse.setdefault(callee, set()).add(caller)
    reached: dict[tuple[str, str, str], set[str]] = {}
    for site in python_primitive_sites(root) if sites is None else sites:
        start = (_module_name(REPO / site[0]), site[1])
        seen, frontier = {start}, [start]
        while frontier:
            for caller in reverse.get(frontier.pop(), ()):
                if caller not in seen:
                    seen.add(caller)
                    frontier.append(caller)
        reached[site] = {module for module, function in seen if function == "main"}
    return reached


def logical_lines(text: str) -> list[tuple[int, str]]:
    """Shell lines with ``\\`` continuations joined; comment lines dropped."""

    out: list[tuple[int, str]] = []
    buffer, start = "", 0
    for number, line in enumerate(text.splitlines(), start=1):
        if not buffer:
            start = number
            if line.lstrip().startswith("#"):
                continue
        if line.rstrip().endswith("\\"):
            buffer += line.rstrip()[:-1] + " "
            continue
        out.append((start, buffer + line))
        buffer = ""
    if buffer:
        out.append((start, buffer))
    return out


def _command_tail(line: str, start: int) -> str:
    """The rest of one command from ``start``: up to ``;``, ``&``, ``|`` or a comment.

    Quotes are respected, so a separator inside a quoted argument does not
    end the command. Only that command's own tokens can name its mode.
    """

    quote, previous = "", " "
    for index in range(start, len(line)):
        char = line[index]
        if quote:
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char in ";&|\n" or (char == "#" and previous.isspace()):
            return line[start:index]
        previous = char
    return line[start:]


def _stop_modules() -> list[tuple[str, str, str, frozenset[str]]]:
    """(module, package, name, allowed shell files) for each module whose import stops."""

    rules = []
    for module in sorted({*ENTRY_POINTS, *SITE_MODULES, *JOB_RUN_MODULES}):
        package, _, name = module.rpartition(".")
        if module in ENTRY_POINTS:
            callers = ENTRY_POINTS[module].callers
        else:
            callers = frozenset({WINDOW_LIBRARY}) if module in JOB_RUN_MODULES else STOP_LIBRARIES
        rules.append((module, package, name, callers))
    return rules


def _mode(entry: EntryPoint, tail: str) -> str:
    tokens = tail.split()
    if entry.positional:
        for token in tokens:
            if not token.startswith("-"):
                return token.strip("'\"")
        return "(default)"
    flags = [mode for mode in entry.modes if mode.startswith("--") and mode in tokens]
    return flags[0] if flags else "(default)"


def shell_violations(path: str, text: str) -> list[str]:
    """Every stop primitive in ``text`` that ``path`` is not allowed to hold."""

    found: list[str] = []
    for number, line in logical_lines(text):
        for module, entry in ENTRY_POINTS.items():
            invocation = rf"(?:-m\s+{re.escape(module)}|{re.escape(module.replace('.', '/'))}\.py)"
            for match in re.finditer(rf"{invocation}(?![\w.])", line):
                mode = _mode(entry, _command_tail(line, match.end()))
                if entry.modes.get(mode, True) and path not in entry.callers:
                    found.append(f"{path}:{number}: {module} {mode}")
        for module, package, name, callers in _stop_modules():
            imported = rf"\b(?:from|import)\s+{re.escape(module)}(?![\w.])"
            from_package = rf"\bfrom\s+{re.escape(package)}\s+import\b[^#]*?\b{name}\b"
            if path not in callers and (re.search(imported, line) or re.search(from_package, line)):
                found.append(f"{path}:{number}: import {module}")
        if path not in STOP_LIBRARIES:
            for pattern in (_CLI_PRIMITIVE, _INLINE_SDK):
                for match in pattern.finditer(line):
                    found.append(f"{path}:{number}: {match.group(0).strip()}")
        if path != WINDOW_LIBRARY:
            for match in _JOB_RUN.finditer(line):
                found.append(f"{path}:{number}: {match.group(0).strip()}")
        for match in _BUNDLE_RUN.finditer(line):
            operands = [token for token in match.group("rest").split() if not token.startswith("-")]
            key = operands[0] if operands else "<none>"
            if key not in SHELL_JOB_RUN_SITES.get(path, frozenset()):
                found.append(f"{path}:{number}: databricks bundle run {key}")
        for match in _PIPELINE_RUN.finditer(line):
            found.append(f"{path}:{number}: {match.group(0).strip()}")
    # A parenthesized ``from <package> import (...)`` spans lines in a heredoc.
    for match in re.finditer(r"\bfrom\s+(tools(?:\.\w+)*)\s+import\s*\(([^)]*)\)", text):
        names = set(re.findall(r"\w+", match.group(2)))
        for module, package, name, callers in _stop_modules():
            if package == match.group(1) and name in names and path not in callers:
                number = text.count("\n", 0, match.start()) + 1
                found.append(f"{path}:{number}: import {module}")
    return found


def shell_files(root: Path | None = None) -> list[Path]:
    """deploy.sh, every other top-level script (the owner-run WR5 entry among them) and the libraries."""

    base = root or REPO
    return sorted({base / "scripts" / "deploy.sh", *(base / "scripts").glob("*.sh"), *(base / "scripts").glob("lib/*.sh")})


def repo_shell_violations(
    files: Iterable[Path] | None = None, root: Path | None = None
) -> list[str]:
    base = root or REPO
    violations: list[str] = []
    for path in files or shell_files(base):
        violations.extend(
            shell_violations(str(path.relative_to(base)), path.read_text(encoding="utf-8"))
        )
    return violations
