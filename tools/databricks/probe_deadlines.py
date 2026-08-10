"""Hard deadlines for CLI probes with no legitimate long network read.

Extracted from ``audit_agent_runtime_foreign_uc_access`` when the size gate
flagged it (2026-08-09). The incident behind every layer here: six deploy
runs wedged ~60 minutes each in ``PySSL_select`` reading an accounts-API
response the server held open and never answered (stack-sampled every time;
``lsof`` showed CLOSE_WAIT zombies beside the wedged ESTABLISHED socket).
``Config.http_timeout_seconds``, a requests-Session default, and
``socket.setdefaulttimeout`` were each defeated in turn by an explicit
``settimeout(None)`` deeper in some stack, and one wedge outlived even the
socket clamp — so the wall-clock watchdog is the only layer trusted
absolutely.
"""

from __future__ import annotations

import concurrent.futures
import faulthandler
import json
import os
import socket
import subprocess
import sys
import threading

_DEADLINE_SECONDS = 120.0
# Env-tunable: recovery walks a 30+-record ledger where most streaming
# reads stall once before clearing on retry (2026-08-10 dumps sat in the
# bounded join while the walk progressed) — that legitimately needs more
# wall-clock than the default.
_WATCHDOG_SECONDS = float(os.environ.get("MIP_PROBE_WATCHDOG_S", "") or 900.0)
_STARTUP_SETTLE_ENV = "MIP_PROBE_STARTUP_SETTLE_S"


def install_probe_deadlines(*, label: str) -> None:
    """Bound every socket and the whole process for one CLI probe run.

    1. ``setdefaulttimeout`` covers sockets that never call ``settimeout``.
    2. ``settimeout(None)`` is clamped to a real deadline so no library at
       any layer can create an unbounded socket in this process.
    3. A daemon timer hard-exits the process after ``_WATCHDOG_SECONDS`` —
       immune to any connection state; the deploy sees a failed step within
       minutes instead of losing an hour per wedge.
    """

    socket.setdefaulttimeout(_DEADLINE_SECONDS)
    original_settimeout = socket.socket.settimeout

    def _bounded_settimeout(self: socket.socket, value: float | None) -> None:
        original_settimeout(self, _DEADLINE_SECONDS if value is None else value)

    socket.socket.settimeout = _bounded_settimeout  # type: ignore[method-assign]

    def _watchdog_abort() -> None:
        print(
            f"[{label}] watchdog: probe exceeded {int(_WATCHDOG_SECONDS)}s "
            "wall-clock; aborting so the deploy fails visibly instead of hanging",
            file=sys.stderr,
            flush=True,
        )
        os._exit(3)

    watchdog = threading.Timer(_WATCHDOG_SECONDS, _watchdog_abort)
    watchdog.daemon = True
    watchdog.start()

    # Periodic thread dumps: every component of the wedging flow passes in
    # isolation (2026-08-10 discriminators), so when the wedge recurs the
    # dump names the exact blocked line instead of another theory.
    faulthandler.dump_traceback_later(180, repeat=True, file=sys.stderr)


_LEASE_ROOT = "/.mip-deployment-leases"
_MIRROR_DIR_ENV = "MIP_PROBE_LEDGER_MIRROR_DIR"
# Rewritten in place (overwrite=True) or delete-then-recreated during
# repairs — never served from the mirror.
_MUTABLE_BASENAME_SUFFIXES = (".head", ".protocol-v5")
_mirror_lock = threading.Lock()
_mirror_snapshot: set[str] | None = None


def _mirror_immutable(path: str) -> bool:
    """True for ledger records the writers create exactly once.

    Every ledger write except the head hint and protocol marker uses
    ``overwrite=False``; chain roots (bare ``<app>.json``) are additionally
    delete-then-recreated by re-root repairs, so only basenames with content
    after ``.json`` (generations, ``.next`` pointers, mutation records,
    delete-with-backup copies) are safe to serve from disk forever.
    """

    basename = path.rsplit("/", 1)[-1]
    if basename.endswith(_MUTABLE_BASENAME_SUFFIXES):
        return False
    if ".oauth-credential-" in basename and basename.endswith(".json"):
        return True
    return ".json." in basename


def _mirror_scope(cli_profile: str) -> str:
    """Directory component isolating one workspace's ledger from another's.

    Ledger paths are identical across workspaces while their contents are
    not, so a flat mirror would serve one workspace's signed records for
    another's identical path. Key the cache by the profile actually used to
    read it (2026-08-10: the same tree exists on pr105-staging and paychex).
    """

    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in cli_profile)
    return safe or "default"


def _mirror_file(mirror_dir: str, path: str, *, scope: str) -> str:
    return os.path.join(mirror_dir, scope, path.rsplit("/", 1)[-1])


def _mirror_store(path: str, data: bytes, *, scope: str) -> None:
    mirror_dir = os.environ.get(_MIRROR_DIR_ENV, "").strip()
    if not mirror_dir or _mirror_snapshot is None or not _mirror_immutable(path):
        return
    try:
        with open(_mirror_file(mirror_dir, path, scope=scope), "wb") as handle:
            handle.write(data)
        with _mirror_lock:
            _mirror_snapshot.add(path)
    except OSError:
        return


def _ensure_mirror(
    mirror_dir: str, cli_profile: str, cli_env: dict[str, str], *, scope: str
) -> set[str]:
    """List the live ledger once and prefetch immutable records in parallel.

    The 2026-08-10 hour-long recovery was 5k sequential ~0.7s reads over the
    grown ledger (4.8k lease-chain records), repeated per stability round —
    not a network stall at all. One parallel prefetch makes every later walk
    a local read. Only paths present in the live listing are ever served, so
    a record deleted by an operator repair can never be resurrected from a
    stale mirror file; failures just fall back to live reads.
    """

    global _mirror_snapshot
    with _mirror_lock:
        if _mirror_snapshot is not None:
            return _mirror_snapshot
    listing = subprocess.run(
        ["databricks", "workspace", "list", _LEASE_ROOT,
         "--output", "json", "--profile", cli_profile],
        capture_output=True,
        timeout=60,
        check=False,
        env=cli_env,
    )
    if listing.returncode != 0:
        with _mirror_lock:
            _mirror_snapshot = set()
        return _mirror_snapshot
    live_paths = [
        path
        for item in json.loads(listing.stdout or b"[]")
        if (path := str(item.get("path", ""))).startswith(f"{_LEASE_ROOT}/")
        and _mirror_immutable(path)
    ]
    os.makedirs(os.path.join(mirror_dir, scope), exist_ok=True)
    reused = {
        p
        for p in live_paths
        if os.path.exists(_mirror_file(mirror_dir, p, scope=scope))
    }

    def _fetch(path: str) -> str | None:
        try:
            completed = subprocess.run(
                ["databricks", "workspace", "export", path, "--profile", cli_profile],
                capture_output=True,
                timeout=30,
                check=False,
                env=cli_env,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0:
            return None
        try:
            with open(_mirror_file(mirror_dir, path, scope=scope), "wb") as handle:
                handle.write(completed.stdout)
        except OSError:
            return None
        return path

    missing = [p for p in live_paths if p not in reused]
    fetched: set[str] = set()
    if missing:
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            fetched = {p for p in pool.map(_fetch, missing) if p}
    snapshot = reused | fetched
    print(
        f"[probe-deadlines] ledger mirror ready: {len(reused)} reused, "
        f"{len(fetched)} fetched, {len(missing) - len(fetched)} left to live reads",
        file=sys.stderr,
        flush=True,
    )
    with _mirror_lock:
        _mirror_snapshot = snapshot
    return snapshot


def bounded_workspace_read(
    workspace: object,
    path: str,
    *,
    attempts: int = 5,
    deadline_seconds: float = 20.0,
) -> bytes:
    """Download one workspace file with per-attempt deadlines and retries.

    Transport note (2026-08-10): the Python SDK's streaming download stalled
    on ~every ledger record for hours (faulthandler captures inside
    ``urllib3.response.stream``) while the Go CLI's ``workspace export``
    answered the same paths in 1-2 seconds every time. The CLI is therefore
    the primary transport, authenticated by the same environment the SDK
    client uses; the bounded SDK thread-read remains as fallback for
    environments without the CLI. ``NotFound``/``ResourceDoesNotExist``
    keep their shape so callers keep their missing-record contracts.
    """

    from databricks.sdk.errors import NotFound
    from databricks.sdk.errors.platform import ResourceDoesNotExist

    last_error: BaseException | None = None
    # Opt-in via env: the deploy wrappers set this so live runs use the CLI;
    # unit tests with mocked workspace clients never leak network calls.
    cli_attempts = 2 if os.environ.get("MIP_PROBE_CLI_TRANSPORT", "").strip() else 0
    # Identity note (2026-08-10): requests authenticated as the per-run
    # bounded temp identities stall server-side (dumps: the CLI subprocess
    # itself sat in communicate() for an hour under the wrapper env), while
    # the same reads under the operator profile answer in seconds. The
    # ledger walk is read-only and every record is signature-verified
    # locally, and the lease root grants the holder CAN_MANAGE by design —
    # so the read transport pins the operator profile with a clean env
    # rather than inheriting the wrapper's bounded credentials.
    cli_profile = os.environ.get("MIP_PROBE_CLI_PROFILE", "").strip() or "DEFAULT"
    cli_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", ""),
    }
    mirror_dir = os.environ.get(_MIRROR_DIR_ENV, "").strip()
    mirror_scope = _mirror_scope(cli_profile)
    if mirror_dir and _mirror_immutable(path):
        snapshot = _ensure_mirror(mirror_dir, cli_profile, cli_env, scope=mirror_scope)
        if path in snapshot:
            try:
                with open(
                    _mirror_file(mirror_dir, path, scope=mirror_scope), "rb"
                ) as handle:
                    return handle.read()
            except OSError:
                pass
    for _attempt in range(cli_attempts):
        try:
            completed = subprocess.run(
                ["databricks", "workspace", "export", path, "--profile", cli_profile],
                capture_output=True,
                timeout=30,
                check=False,
                env=cli_env,
            )
        except FileNotFoundError as exc:
            last_error = exc
            break
        except subprocess.TimeoutExpired as exc:
            last_error = exc
            continue
        if completed.returncode == 0:
            _mirror_store(path, completed.stdout, scope=mirror_scope)
            return completed.stdout
        stderr = completed.stderr.decode(errors="replace")
        if (
            "RESOURCE_DOES_NOT_EXIST" in stderr
            or "does not exist" in stderr
            or "doesn't exist" in stderr
        ):
            raise ResourceDoesNotExist(f"workspace path missing: {path}")
        last_error = RuntimeError(f"cli export failed ({completed.returncode}): {stderr[:200]}")

    for _attempt in range(attempts):
        outcome: list[object] = []

        def _download(target: list[object] = outcome) -> None:
            try:
                target.append(workspace.workspace.download(path).read())  # type: ignore[attr-defined]
            except BaseException as exc:  # noqa: BLE001 - dispatched below
                target.append(exc)

        worker = threading.Thread(target=_download, daemon=True)
        worker.start()
        worker.join(deadline_seconds)
        if worker.is_alive():
            last_error = RuntimeError(f"workspace read stalled: {path}")
            continue
        result = outcome[0] if outcome else RuntimeError("workspace read returned nothing")
        if isinstance(result, NotFound | ResourceDoesNotExist):
            raise result
        if isinstance(result, BaseException):
            last_error = result
            continue
        _mirror_store(path, result, scope=mirror_scope)  # type: ignore[arg-type]
        return result  # type: ignore[return-value]
    raise RuntimeError(
        f"workspace read failed after {attempts} bounded attempts: {path}"
    ) from last_error
