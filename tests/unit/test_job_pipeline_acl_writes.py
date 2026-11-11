"""No tool replaces a job's or pipeline's ACL: every such write is a PATCH (spike S4).

Spike S4 measured that ``databricks jobs set-permissions`` (the permissions
API's PUT) replaces the object's whole ACL: setting IS_OWNER through it
dropped another principal's CAN_MANAGE_RUN entry. On a bundle job that entry
is the App's binding, so an additive grant sent as a set would silently cut
the App off. The only job and pipeline ACL write in the tree is
tools/databricks/jobs_identity_acl.converge, which adds one entry with
``permissions.update`` (PATCH). This scan refuses every replacing form, in
Python (the typed ``jobs``/``pipelines`` ``set_permissions``, a generic
``permissions.set``, a raw call naming a job or pipeline permissions path) and
in shell and workflow files (``jobs set-permissions``, ``pipelines
set-permissions``, ``permissions set jobs|pipelines``). Each rule has a
positive control.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY_ROOTS = ("tools", "scripts", "jobs", "pipelines", "backend")
TEXT_GLOBS = ("scripts/**/*.sh", "scripts/*.sh", ".github/workflows/*.yml", "Makefile")
_RAW_PATH = re.compile(r"/permissions/(?:jobs|pipelines)\b")
_CLI_SET = re.compile(r"\b(?:jobs|pipelines)\s+set-permissions\b|\bpermissions\s+set\s+(?:jobs|pipelines)\b")


def _receiver(node: ast.expr) -> str:
    return node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else ""


def _strings(node: ast.AST) -> list[str]:
    return [item.value for item in ast.walk(node) if isinstance(item, ast.Constant) and isinstance(item.value, str)]


def python_violations(source: str) -> list[str]:
    """``line: form`` for every call that replaces a job or pipeline ACL, or might."""

    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        name, owner = node.func.attr, _receiver(node.func.value)
        if name == "set_permissions" and owner in {"jobs", "pipelines"}:
            found.append(f"{node.lineno}: {owner}.set_permissions")
        elif name == "set" and owner == "permissions":
            found.append(f"{node.lineno}: permissions.set")
        elif (name == "put" or "PUT" in _strings(node)) and any(_RAW_PATH.search(t) for t in _strings(node)):
            found.append(f"{node.lineno}: a raw PUT on a job or pipeline permissions path")
    return found


def _python_files() -> list[Path]:
    return sorted(path for root in PY_ROOTS for path in (REPO / root).rglob("*.py"))


def test_no_python_source_replaces_a_job_or_pipeline_acl() -> None:
    offenders = {
        str(path.relative_to(REPO)): hits
        for path in _python_files()
        if (hits := python_violations(path.read_text(encoding="utf-8")))
    }
    assert offenders == {}


def test_no_shell_or_workflow_sets_a_job_or_pipeline_acl() -> None:
    files = sorted({path for glob in TEXT_GLOBS for path in REPO.glob(glob) if path.is_file()})
    assert any(path.suffix == ".sh" for path in files), "positive control: the scan sees the deploy scripts"
    offenders = [
        f"{path.relative_to(REPO)}:{number}"
        for path in files
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if _CLI_SET.search(line)
    ]
    assert offenders == []


def test_each_rule_catches_its_form() -> None:
    sample = (
        "client.jobs.set_permissions(job_id='1', access_control_list=[])\n"
        "ws.pipelines.set_permissions(pipeline_id='p', access_control_list=[])\n"
        "ws.permissions.set('jobs', '1', access_control_list=[])\n"
        "ws.api_client.do('PUT', f'/api/2.0/permissions/pipelines/{pid}', body={})\n"
        "ws.jobs.update_permissions(job_id='1', access_control_list=[])\n"
        "ws.permissions.update('jobs', '1', access_control_list=[])\n"
        "ws.api_client.do('GET', '/api/2.0/permissions/jobs/1')\n"
        "ws.api_client.do('PATCH', '/api/2.0/permissions/jobs/1', body={})\n"
        "ws.warehouses.set_permissions(warehouse_id='w', access_control_list=[])\n"
    )
    assert [hit.split(":", 1)[0] for hit in python_violations(sample)] == ["1", "2", "3", "4"]
    assert _CLI_SET.search("databricks jobs set-permissions 1 --json @acl.json")
    assert _CLI_SET.search("databricks permissions set pipelines p --json @acl.json")
    assert not _CLI_SET.search("databricks jobs update-permissions 1 --json @acl.json")


def test_the_one_job_and_pipeline_acl_write_is_a_patch() -> None:
    """Positive control: the scan reads the tree, and the jobs identity's grants use ``permissions.update``."""

    writers = {}
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "update" and _receiver(node.func.value) == "permissions"
        ]
        if calls:
            writers[str(path.relative_to(REPO))] = len(calls)
    assert writers == {"tools/databricks/jobs_identity_acl.py": 1}
    source = (REPO / "tools/databricks/jobs_identity_acl.py").read_text(encoding="utf-8")
    assert '"jobs": "jobs"' in source and '"pipelines": "pipelines"' in source, "it writes job and pipeline ACLs"
