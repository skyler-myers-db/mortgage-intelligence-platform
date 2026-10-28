"""Path values, call-name tables and pure path helpers for the App read scan.

Shared by tests/fixtures/app_runtime_flow.py, app_runtime_consumers.py and
app_runtime_reads.py (which documents the scan).
"""

from __future__ import annotations

import ast
import posixpath
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

Verdict = Literal["follow", "sink", "subtree", "unhandled"]
Scope = tuple[str, ...]
StrValue = tuple[str, bool]  # (text or constant head, has a non-constant tail)

PATH_CTORS = frozenset(
    f"pathlib.{name}" for name in ("Path", "PurePath", "PosixPath", "PurePosixPath")
)
STR_CALLS = frozenset({"str", "os.fspath"})
OPEN_CALLS = frozenset({"open", "io.open", "builtins.open", "codecs.open"})
DIR_CALLS = frozenset({"os.walk", "os.listdir", "os.scandir", "glob.glob", "glob.iglob",
                        "shutil.copytree"})
ENV_GETS = frozenset({"os.environ.get", "os.getenv", "os.environ.setdefault"})
CWD_CALLS = frozenset({"pathlib.Path.cwd", "os.getcwd"})
OS_PATH_SAME = frozenset({"os.path.abspath", "os.path.realpath", "os.path.normpath",
                           "os.path.expanduser"})
OS_PATH_DIRNAME = "os.path.dirname"
OS_PATH_JOIN = "os.path.join"
SAME_METHODS = frozenset({"resolve", "absolute", "expanduser", "as_posix", "__fspath__"})
FOLLOW_METHODS = SAME_METHODS | {"with_name", "with_suffix", "with_stem", "joinpath",
                                   "glob", "rglob", "iterdir"}
SINK_ATTRS = frozenset({
    "read_text", "read_bytes", "open", "exists", "is_file", "is_dir", "is_symlink", "stat",
    "lstat", "samefile", "readlink", "owner", "group", "write_text", "write_bytes", "touch",
    "mkdir", "unlink", "rmdir", "chmod", "name", "stem", "suffix", "suffixes", "parts",
    "anchor", "drive", "root", "is_absolute", "is_relative_to", "relative_to", "match",
    "as_uri",
})
SUBTREE_ATTRS = frozenset({"walk"})
GLOB_CHARS = frozenset("*?[")
# Roots the scan does not follow. Each names a file or folder of the running
# App without going through a bare ``__file__`` or the cwd: a module's own
# path (``backend.__file__``, ``sys.modules[__name__].__file__``,
# ``globals()["__file__"]``, ``__spec__.origin``, ``inspect.getfile``, a
# code object's ``co_filename``, a frame from ``sys._getframe`` or
# ``inspect.currentframe``, a ``find_spec`` result's ``origin``), the script
# path (``sys.argv``), an import root (``sys.path``) or a package's resource
# root (``importlib.resources.files``). A reference to one is reported as
# unhandled, as a bare ``__file__`` outside the path constructors is.
ROOT_DUNDERS = frozenset({"__file__", "__spec__", "__loader__", "__path__", "__cached__"})
ROOT_ATTRS = frozenset({"co_filename"})  # any code object's source file
# Attributes of a ModuleSpec that name files, flagged on a find_spec result.
SPEC_ATTRS = frozenset({"origin", "submodule_search_locations", "cached", "loader"})
FRAME_REFS = frozenset({
    "sys._getframe", "sys._current_frames", "inspect.currentframe", "inspect.stack",
    "inspect.trace", "inspect.getouterframes", "inspect.getinnerframes",
    "inspect.getframeinfo", "traceback.extract_stack", "traceback.walk_stack",
})
ROOT_REFS = frozenset({
    "sys.argv", "sys.path", "os.curdir", *FRAME_REFS,
    "inspect.getfile", "inspect.getsourcefile", "inspect.getabsfile",
    *(f"importlib.resources.{name}" for name in (
        "files", "as_file", "path", "contents", "is_resource", "read_text", "read_binary",
        "open_text", "open_binary",
    )),
    "importlib_resources.files",
    "pkgutil.get_data",
    *(f"pkg_resources.{name}" for name in (
        "resource_filename", "resource_string", "resource_stream", "resource_listdir",
    )),
})



@dataclass(frozen=True)
class Ref:
    """A repo-relative POSIX path (``.`` is the root); ``subtree`` adds ``/**``."""

    path: str
    subtree: bool = False

    def key(self) -> str:
        return f"{self.path}/**" if self.subtree else self.path


MAX_DEPTH = 12  # a deeper path, or one more than two levels above the root, is folded


def norm(path: str) -> str:
    return posixpath.normpath(path or ".")


def bounded(ref: Ref) -> Ref:
    """Keep the path lattice finite, so ``p = p.parent`` in a loop reaches a fixed point."""

    parts = ref.path.split("/")
    if parts[:3] == ["..", "..", ".."]:
        return Ref("../..", True)
    if len(parts) > MAX_DEPTH:
        return Ref("/".join(parts[:MAX_DEPTH]), True)
    return ref


def join(base: str, *segments: str) -> str:
    return norm(posixpath.join(base, *segments))


def up(path: str, levels: int) -> str:
    """``levels`` directories up; above the repo root stays visible as ``..``."""

    for _ in range(levels):
        path = f"{path}/.." if posixpath.basename(path) in ("", ".", "..") else posixpath.dirname(path)
        path = norm(path)
    return path


def join_ref(ref: Ref, text: str, dynamic: bool) -> Ref | None:
    if text.startswith("/"):
        return None
    if ref.subtree:
        ups = text.split("/").count("..")
        return Ref(up(ref.path, ups) if ups else ref.path, True)
    if dynamic:
        return Ref(join(ref.path, posixpath.dirname(text)), True)
    return Ref(join(ref.path, text))


def subtree(refs: Iterable[Ref]) -> set[Ref]:
    return {Ref(ref.path, True) for ref in refs}


def const_int(node: ast.AST) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    return None


def attr_chain(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = attr_chain(node.value)
        return f"{head}.{node.attr}" if head else None
    return None


def module_package(relfile: str, dotted: str) -> str:
    """The package a module's relative imports resolve against."""

    return dotted if relfile.endswith("__init__.py") else dotted.rpartition(".")[0]


def absolute_module(package: str, level: int, module: str | None) -> str | None:
    """``from {"." * level}{module} import ...`` in ``package``, as an absolute name."""

    if level == 0:
        return module
    parts = package.split(".") if package else []
    if level - 1 > len(parts):
        return None
    base = parts[: len(parts) - (level - 1)]
    return ".".join([*base, module] if module else base) or None


def glob_prefix(pattern: str) -> str:
    parts: list[str] = []
    for part in pattern.split("/"):
        if GLOB_CHARS & set(part):
            break
        parts.append(part)
    return "/".join(parts)


