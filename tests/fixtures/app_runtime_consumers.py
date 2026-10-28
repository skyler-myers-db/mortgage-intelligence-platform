"""Consumers: what reading a path value means where the value ends up.

``consumer`` returns ``follow`` (another node carries the value on),
``sink`` (exactly this path is read), ``subtree`` (anything under it may be
read) or ``unhandled``. tests/fixtures/app_runtime_reads.py documents the scan.
"""

from __future__ import annotations

import ast

from tests.fixtures.app_runtime_flow import ModuleFlow
from tests.fixtures.app_runtime_paths import (
    DIR_CALLS,
    ENV_GETS,
    FOLLOW_METHODS,
    OPEN_CALLS,
    OS_PATH_DIRNAME,
    OS_PATH_JOIN,
    OS_PATH_SAME,
    PATH_CTORS,
    SINK_ATTRS,
    STR_CALLS,
    SUBTREE_ATTRS,
    Scope,
    Verdict,
    const_int,
)


def consumer(module: ModuleFlow, node: ast.AST, scope: Scope) -> Verdict:  # noqa: C901, PLR0911, PLR0912
    parent = module.parents.get(id(node))
    if isinstance(parent, ast.Attribute):
        return _attribute_consumer(module, parent)
    if isinstance(parent, ast.Subscript):
        return "follow" if parent.value is node else "sink"
    if isinstance(parent, ast.BinOp):
        return "follow" if isinstance(parent.op, ast.Div) and parent.left is node else "subtree"
    if isinstance(parent, ast.Compare | ast.UnaryOp):
        return "sink"
    if isinstance(parent, ast.BoolOp | ast.NamedExpr | ast.Tuple | ast.List | ast.Set):
        return "follow"
    if isinstance(parent, ast.IfExp):
        return "sink" if parent.test is node else "follow"
    if isinstance(parent, ast.keyword):
        call = module.parents.get(id(parent))
        if isinstance(call, ast.Call):
            return _call_consumer(module, call, node, scope)
        return "unhandled"
    if isinstance(parent, ast.Call):
        return "unhandled" if parent.func is node else _call_consumer(module, parent, node, scope)
    if isinstance(parent, ast.Assign | ast.AnnAssign):
        targets: list[ast.expr] = (
            list(parent.targets) if isinstance(parent, ast.Assign) else [parent.target]
        )
        return _binding_consumer(module, targets)
    if isinstance(parent, ast.Return):
        called = scope and (module.name, scope[-1]) in module.world.called
        return "follow" if called else "subtree"
    if isinstance(parent, ast.For | ast.AsyncFor | ast.comprehension):
        if parent.iter is node:
            return _binding_consumer(module, [parent.target])
        return "sink"
    if isinstance(parent, ast.arguments):
        owner = module.parents.get(id(parent))
        return "subtree" if isinstance(owner, ast.Lambda) else "follow"
    if isinstance(parent, ast.Expr | ast.If | ast.While | ast.Assert | ast.Raise):
        return "sink"
    if isinstance(parent, ast.AugAssign):
        # p /= q rebinds p (bind_round); _classify reports any other operator.
        return "follow" if isinstance(parent.op, ast.Div) else "unhandled"
    if parent is None or isinstance(parent, ast.stmt):
        return "sink"
    return "subtree"  # f-string, dict, lambda, yield, await, starred, with-item, ...

def _attribute_consumer(module: ModuleFlow, parent: ast.Attribute) -> Verdict:
    grand = module.parents.get(id(parent))
    called = isinstance(grand, ast.Call) and grand.func is parent
    if parent.attr == "parent":
        return "follow"
    if parent.attr == "parents":
        constant = isinstance(grand, ast.Subscript) and const_int(grand.slice) is not None
        return "follow" if constant else "unhandled"
    if parent.attr in FOLLOW_METHODS and called:
        return "follow"
    if parent.attr in SINK_ATTRS:
        return "sink"
    return "subtree" if parent.attr in SUBTREE_ATTRS else "unhandled"

def _call_consumer(module: ModuleFlow, call: ast.Call, arg: ast.AST, scope: Scope) -> Verdict:  # noqa: PLR0911
    name = module.canon(call.func)
    if name in PATH_CTORS or name in STR_CALLS or name in OS_PATH_SAME:
        return "follow"
    if name in (OS_PATH_JOIN, OS_PATH_DIRNAME):
        return "follow"
    if name in ENV_GETS:
        return "follow" if module.env_default(call) is arg else "sink"
    if name in OPEN_CALLS:
        return "sink"
    if name in DIR_CALLS:
        return "subtree"
    if isinstance(call.func, ast.Attribute) and module.resolve(call.func.value, scope):
        # joinpath(path) yields that path; relative_to, samefile, ... read nothing.
        return "follow" if call.func.attr == "joinpath" else "sink"
    return "follow" if module.param_targets(call, arg) else "subtree"

def _binding_consumer(module: ModuleFlow, targets: list[ast.expr]) -> Verdict:
    names: list[ast.expr] = []
    for target in targets:
        names.extend(target.elts if isinstance(target, ast.Tuple | ast.List) else [target])
    world = module.world
    used = True
    for t in names:
        if isinstance(t, ast.Name):
            used &= (t.id in module.loaded or (module.name, t.id) in world.imported
                     or t.id in world.attr_loads)
        elif isinstance(t, ast.Attribute):
            used &= t.attr in world.attr_loads
        else:
            return "subtree"  # stored into a subscript or a starred target
    return "follow" if used else "sink"
