"""An in-memory Databricks workspace for the App release tools' tests.

``FakeWorkspaceClient().workspace`` models the Workspace API surface the
release tools call (mkdirs, upload, download, get_status, list, delete) over
a typed tree of DIRECTORY, FILE and NOTEBOOK objects:

* ``upload`` with ``ImportFormat.AUTO`` makes a NOTEBOOK (and drops the
  extension) from a notebook file type or a ``Databricks notebook source``
  first line, as the platform does; the SDK default (SOURCE) makes a notebook
  of every ``.py``, ``.sql``, ``.scala`` and ``.r`` file. Without
  ``overwrite`` an existing object raises ResourceAlreadyExists.
* ``list(recursive=True)`` yields every non-directory object below the path
  and never a directory, like the SDK's WorkspaceExt.list.
* ``delete`` refuses a non-empty directory unless ``recursive``.

``FakeWorkspaceClient().apps`` serves one App and its deployments for
``get``, ``list_deployments`` and ``get_deployment``.

Faults: ``fail_before[(op, path)]`` raises before the operation applies;
``fail_after[(op, path)]`` applies it and then raises (an ambiguous result).
An exception there fails every such call; a list fails one call per item, in
order, and lets later calls through (a throttle that clears). ``hooks[op]``
runs before every such call with the path. Every call is recorded in
``calls`` as ``(op, path)``. A real client never raises a 429 from a GET
(``download``, ``list``): ``sdk_gave_up`` builds what it raises instead.
"""

from __future__ import annotations

import io
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import PurePosixPath
from types import SimpleNamespace
from typing import Any, BinaryIO

from databricks.sdk.clock import Clock
from databricks.sdk.errors import (
    BadRequest,
    InvalidParameterValue,
    NotFound,
    ResourceAlreadyExists,
    ResourceDoesNotExist,
)
from databricks.sdk.retries import retried
from databricks.sdk.service.workspace import ImportFormat, ObjectType

from tools.databricks.app_release_contract import notebook_shape

SOURCE_NOTEBOOK_SUFFIXES = (".py", ".sql", ".scala", ".r")
SDK_RETRY_TIMEOUT_SECONDS = 300  # Config.retry_timeout_seconds when unset (SDK 0.103.0)


class InstantClock(Clock):
    """The SDK's clock interface; ``sleep`` only moves ``time`` forward."""

    def __init__(self) -> None:
        self.now = 0.0
        self.lock = threading.Lock()

    def time(self) -> float:
        with self.lock:
            return self.now

    def sleep(self, seconds: float) -> None:
        with self.lock:
            self.now += seconds


def sdk_gave_up(error: Exception, *, max_attempts: int | None = None) -> Exception:
    """What SDK 0.103.0's ``ApiClient.do`` raises once its own retry of ``error`` gives up.

    ``do`` wraps every request with no body (each GET) in ``retries.retried``
    for ``retry_timeout_seconds`` and retries any error that carries
    ``retry_after_secs``, which the SDK sets on every 429 and 503. Past the
    deadline it raises ``TimeoutError`` from the last error; ``RuntimeError``
    when ``max_attempts`` ran out first. The SDK's own decorator builds it here,
    on an instant clock, so the shape is the SDK's and no test waits.
    """

    def failing() -> None:
        raise error

    policy = retried(
        timeout=timedelta(seconds=SDK_RETRY_TIMEOUT_SECONDS), is_retryable=lambda _: "transient",
        clock=InstantClock(), max_attempts=max_attempts,
    )
    try:
        policy(failing)()
    except Exception as gave_up:  # noqa: BLE001 - the shape under test
        return gave_up
    raise AssertionError("the SDK's retry returned without the call ever succeeding")


@dataclass
class Node:
    kind: ObjectType
    data: bytes = b""


def _parent(path: str) -> str:
    return str(PurePosixPath(path).parent)


@dataclass
class FakeTree:
    nodes: dict[str, Node] = field(default_factory=lambda: {"/": Node(ObjectType.DIRECTORY)})
    calls: list[tuple[str, str]] = field(default_factory=list)
    fail_before: dict[tuple[str, str], Exception | list[Exception]] = field(default_factory=dict)
    fail_after: dict[tuple[str, str], Exception | list[Exception]] = field(default_factory=dict)
    hooks: dict[str, Callable[[str], None]] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock)

    # -- test helpers -------------------------------------------------------
    def add_dir(self, path: str) -> None:
        with self.lock:
            for parent in reversed(PurePosixPath(path).parents):
                self.nodes.setdefault(str(parent), Node(ObjectType.DIRECTORY))
            self.nodes[path] = Node(ObjectType.DIRECTORY)

    def add_file(self, path: str, data: bytes, kind: ObjectType = ObjectType.FILE) -> None:
        with self.lock:
            self.add_dir(_parent(path))
            self.nodes[path] = Node(kind, data)

    def below(self, path: str) -> list[str]:
        prefix = path.rstrip("/") + "/"
        return sorted(name for name in self.nodes if name.startswith(prefix))

    def exists(self, path: str) -> bool:
        return path in self.nodes

    def ops(self, op: str) -> list[str]:
        return [path for name, path in self.calls if name == op]

    # -- fault plumbing -----------------------------------------------------
    def _fault(self, table: dict[tuple[str, str], Exception | list[Exception]], op: str,
               path: str) -> Exception | None:
        with self.lock:
            fault = table.get((op, path))
            if isinstance(fault, list):
                return fault.pop(0) if fault else None
            return fault

    def _enter(self, op: str, path: str) -> None:
        with self.lock:
            self.calls.append((op, path))
        hook = self.hooks.get(op)
        if hook is not None:
            hook(path)
        error = self._fault(self.fail_before, op, path)
        if error is not None:
            raise error

    def _leave(self, op: str, path: str) -> None:
        error = self._fault(self.fail_after, op, path)
        if error is not None:
            raise error

    def _status(self, path: str) -> SimpleNamespace:
        node = self.nodes[path]
        size = len(node.data) if node.kind is ObjectType.FILE else None
        return SimpleNamespace(path=path, object_type=node.kind, size=size)

    # -- the Workspace API surface ------------------------------------------
    def mkdirs(self, path: str) -> None:
        self._enter("mkdirs", path)
        with self.lock:
            for name in [*reversed([str(p) for p in PurePosixPath(path).parents]), path]:
                node = self.nodes.get(name)
                if node is not None and node.kind is not ObjectType.DIRECTORY:
                    raise ResourceAlreadyExists(f"{name} exists and is not a directory")
                self.nodes.setdefault(name, Node(ObjectType.DIRECTORY))
        self._leave("mkdirs", path)

    def upload(
        self,
        path: str,
        content: bytes | BinaryIO,
        *,
        format: ImportFormat | None = None,  # noqa: A002 - the SDK's keyword
        language: object = None,
        overwrite: bool | None = False,
    ) -> None:
        self._enter("upload", path)
        data = content if isinstance(content, bytes) else content.read()
        target, kind = path, ObjectType.FILE
        auto_notebook = format is ImportFormat.AUTO and notebook_shape(path, data) is not None
        source_notebook = format in (None, ImportFormat.SOURCE) and path.casefold().endswith(
            SOURCE_NOTEBOOK_SUFFIXES
        )
        if auto_notebook or source_notebook:
            target, kind = str(PurePosixPath(path).with_suffix("")), ObjectType.NOTEBOOK
        with self.lock:
            parent = self.nodes.get(_parent(target))
            if parent is None or parent.kind is not ObjectType.DIRECTORY:
                raise ResourceDoesNotExist(f"parent of {target} does not exist")
            if target in self.nodes and not overwrite:
                raise ResourceAlreadyExists(f"{target} already exists")
            self.nodes[target] = Node(kind, data)
        self._leave("upload", path)

    def download(self, path: str, *, format: object = None) -> io.BytesIO:  # noqa: A002
        self._enter("download", path)
        with self.lock:
            node = self.nodes.get(path)
            if node is None:
                raise ResourceDoesNotExist(f"{path} does not exist")
            if node.kind is ObjectType.DIRECTORY:
                raise InvalidParameterValue(f"{path} is a directory")
            data = node.data
        self._leave("download", path)
        return io.BytesIO(data)

    def get_status(self, path: str) -> SimpleNamespace:
        self._enter("get_status", path)
        with self.lock:
            if path not in self.nodes:
                raise ResourceDoesNotExist(f"{path} does not exist")
            status = self._status(path)
        self._leave("get_status", path)
        return status

    def list(self, path: str, *, recursive: bool = False, **_: Any) -> Iterator[SimpleNamespace]:
        self._enter("list", path)
        with self.lock:
            node = self.nodes.get(path)
            if node is None or node.kind is not ObjectType.DIRECTORY:
                raise ResourceDoesNotExist(f"{path} does not exist")
            names = self.below(path)
            if not recursive:
                names = [name for name in names if _parent(name) == path]
            else:
                names = [name for name in names if self.nodes[name].kind is not ObjectType.DIRECTORY]
            listing = [self._status(name) for name in names]
        self._leave("list", path)
        return iter(listing)

    def delete(self, path: str, *, recursive: bool | None = None) -> None:
        self._enter("delete", path)
        with self.lock:
            if path not in self.nodes:
                raise ResourceDoesNotExist(f"{path} does not exist")
            children = self.below(path)
            if children and not recursive:
                raise BadRequest(f"{path} is a non-empty directory")
            for name in [*children, path]:
                del self.nodes[name]
        self._leave("delete", path)


def deployment(
    deployment_id: str,
    source_code_path: str | None,
    *,
    create_time: str | None,
    snapshot: str | None = None,
    state: str = "SUCCEEDED",
) -> SimpleNamespace:
    return SimpleNamespace(
        deployment_id=deployment_id,
        source_code_path=source_code_path,
        create_time=create_time,
        update_time=create_time,
        deployment_artifacts=SimpleNamespace(source_code_path=snapshot),
        status=SimpleNamespace(state=state),
    )


@dataclass
class FakeApps:
    app: SimpleNamespace = field(
        default_factory=lambda: SimpleNamespace(
            id="app-id", name="mip-app", active_deployment=None, pending_deployment=None
        )
    )
    deployments: list[SimpleNamespace] = field(default_factory=list)
    calls: list[tuple[str, str]] = field(default_factory=list)
    fail: dict[str, Exception] = field(default_factory=dict)
    hooks: dict[str, Callable[[int], None]] = field(default_factory=dict)

    def _enter(self, op: str, name: str) -> None:
        self.calls.append((op, name))
        hook = self.hooks.get(op)
        if hook is not None:
            hook(sum(1 for called, _ in self.calls if called == op))
        if op in self.fail:
            raise self.fail[op]

    def get(self, name: str) -> SimpleNamespace:
        self._enter("get", name)
        if name != self.app.name:
            raise NotFound(f"App {name} does not exist")
        return SimpleNamespace(**vars(self.app))

    def list_deployments(self, app_name: str, **_: Any) -> Iterator[SimpleNamespace]:
        self._enter("list_deployments", app_name)
        return iter(list(self.deployments))

    def get_deployment(self, app_name: str, deployment_id: str) -> SimpleNamespace:
        self._enter("get_deployment", app_name)
        for item in self.deployments:
            if item.deployment_id == deployment_id:
                return item
        raise NotFound(f"deployment {deployment_id} does not exist")


@dataclass
class FakeWorkspaceClient:
    workspace: FakeTree = field(default_factory=FakeTree)
    apps: FakeApps = field(default_factory=FakeApps)
