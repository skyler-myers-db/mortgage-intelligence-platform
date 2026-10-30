"""A fresh, throwaway GitHub CLI session for the client tools (plan §4.10).

Every ``gh`` call the GitHub-environment tool and its credential sink make
runs like this:

* **A fresh login, never an ambient one.** ``GH_CONFIG_DIR`` names a new
  private temporary directory (mode 0700), and the operator logs in to it
  with ``gh auth login --web``. ``--insecure-storage`` keeps the token in
  that directory instead of the OS keyring, so it never touches the
  operator's own gh credentials and dies with the directory. ``HOME`` points
  inside it too. A directory that already holds anything is refused.
* **No inherited GitHub configuration.** Every ``GH_*`` and ``GITHUB_*``
  name in the operator's environment is removed from the child (``GH_TOKEN``,
  ``GITHUB_TOKEN``, the enterprise tokens, ``GH_HOST``, ``GH_REPO``,
  ``GH_DEBUG``, which would log request bodies), and so are the ``XDG_*``
  directories gh could fall back to.
* **One repository, never ``--repo``.** ``GH_REPO`` names the target, and
  ``gh api`` paths name it explicitly. An argv holding ``--repo``/``-R`` is
  refused, and so are the flags that would put a value in argv (``--body``,
  ``--field``, ``--with-token``): values go to gh on stdin only.
* **Value-free failures.** A failed call names the gh subcommand and its
  exit code, never its output, which could echo a value.

Nothing here reads a secret back: GitHub cannot return one.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools.clients.errors import BootstrapRefusal

GH = "gh"
HOSTNAME = "github.com"
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_SCRUBBED_PREFIXES = ("GH_", "GITHUB_")
_SCRUBBED_NAMES = frozenset({"XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"})
# Flags that would pick another repository or carry a value in argv.
_REFUSED_FLAGS = frozenset(
    {"--repo", "-R", "--body", "-b", "--field", "-f", "--raw-field", "-F", "--with-token", "--env-file"}
)
# Every argv token is a subcommand, a flag, a name or an API path.
_TOKEN = re.compile(r"[A-Za-z0-9_./:=?&,-]{1,200}")
_CALL_TIMEOUT_SECONDS = 60
_LOGIN_TIMEOUT_SECONDS = 900

Runner = Callable[..., Any]


class GhFailure(RuntimeError):
    """A gh call failed; the message names the subcommand and exit code only."""


def child_environment(environ: Mapping[str, str], *, config_dir: Path, home: Path, repository: str) -> dict[str, str]:
    """The operator's environment minus every GitHub name, plus the session's own."""

    kept = {
        name: value
        for name, value in environ.items()
        if not name.startswith(_SCRUBBED_PREFIXES) and name not in _SCRUBBED_NAMES
    }
    kept.update(
        {
            "GH_CONFIG_DIR": str(config_dir),
            "HOME": str(home),
            "GH_REPO": repository,
            "GH_PROMPT_DISABLED": "1",
            "GH_NO_UPDATE_NOTIFIER": "1",
            "GH_NO_EXTENSION_UPDATE_NOTIFIER": "1",
            "GH_SPINNER_DISABLED": "1",
            "NO_COLOR": "1",
        }
    )
    return kept


def _label(args: Sequence[str]) -> str:
    if args and args[0] == "api" and "--method" in args:
        return f"api {args[args.index('--method') + 1]}"
    return " ".join(args[:2])


def check_argv(args: Sequence[str]) -> None:
    """Refuse a flag that names another repository or carries a value, and any odd token."""

    for arg in args:
        flag = arg.split("=", 1)[0]
        if flag in _REFUSED_FLAGS:
            raise BootstrapRefusal([f"github: {flag} is refused; the repository is GH_REPO and values go on stdin"])
        if _TOKEN.fullmatch(arg) is None:
            raise BootstrapRefusal(["github: an argument is not a plain name, flag or API path"])


@dataclass
class GhSession:
    """One throwaway gh login, bound to one repository."""

    root: Path
    repository: str
    environ: Mapping[str, str]
    runner: Runner = subprocess.run
    login_user: dict[str, Any] = field(default_factory=dict)

    @property
    def config_dir(self) -> Path:
        return self.root / "gh"

    @property
    def home(self) -> Path:
        return self.root / "home"

    def environment(self) -> dict[str, str]:
        return child_environment(
            self.environ, config_dir=self.config_dir, home=self.home, repository=self.repository
        )

    @property
    def logged_in(self) -> bool:
        return bool(self.login_user)

    def login(self) -> dict[str, Any]:
        """Log the operator in to the session directory once; return ``{login, id}``."""

        if self.logged_in:
            return self.login_user
        if any(self.config_dir.iterdir()):
            raise BootstrapRefusal(["github: the session's gh config directory is not empty; refusing to reuse it"])
        env = self.environment()
        env.pop("GH_PROMPT_DISABLED")  # the login itself is the one interactive step
        argv = [GH, "auth", "login", "--hostname", HOSTNAME, "--git-protocol", "https", "--web"]
        argv += ["--insecure-storage", "--skip-ssh-key"]
        result = self.runner(argv, env=env, check=False, timeout=_LOGIN_TIMEOUT_SECONDS)
        if getattr(result, "returncode", 1) != 0:
            raise BootstrapRefusal(["github: the gh login did not complete; nothing was changed"])
        self._run(["auth", "status", "--hostname", HOSTNAME])
        user = self._json(["api", "--method", "GET", "user"], None)
        if not isinstance(user, dict) or not isinstance(user.get("id"), int) or not isinstance(user.get("login"), str):
            raise BootstrapRefusal(["github: the signed-in user could not be read"])
        self.login_user = {"login": user["login"], "id": user["id"]}
        return self.login_user

    def call(self, args: Sequence[str], *, stdin: bytes | None = None) -> str:
        """Run one gh call after the fresh login; the value, if any, goes on stdin."""

        if not self.logged_in:
            raise BootstrapRefusal(["github: no fresh gh login in this session"])
        return self._run(args, stdin)

    def call_json(self, args: Sequence[str]) -> Any:
        """``call`` and parse its JSON output (``--json`` listings)."""

        if not self.logged_in:
            raise BootstrapRefusal(["github: no fresh gh login in this session"])
        return self._json(args, None)

    def _run(self, args: Sequence[str], stdin: bytes | None = None) -> str:
        check_argv(args)
        result = self.runner(
            [GH, *args],
            input=b"" if stdin is None else stdin,
            capture_output=True,
            check=False,
            timeout=_CALL_TIMEOUT_SECONDS,
            env=self.environment(),
        )
        if result.returncode != 0:
            raise GhFailure(f"gh {_label(args)} failed (exit {result.returncode})")
        return bytes(result.stdout or b"").decode("utf-8")

    def _json(self, args: Sequence[str], stdin: bytes | None) -> Any:
        text = self._run(args, stdin)
        try:
            return json.loads(text) if text.strip() else None
        except ValueError:
            raise GhFailure(f"gh {_label(args)} returned malformed JSON") from None

    def api(self, method: str, path: str, *, body: Mapping[str, Any] | None = None) -> Any:
        """One REST call; a body goes on stdin (``--input -``), never as fields."""

        if not self.logged_in:
            raise BootstrapRefusal(["github: no fresh gh login in this session"])
        args = ["api", "--method", method, path]
        payload = None
        if body is not None:
            args += ["--input", "-"]
            payload = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return self._json(args, payload)

    def repo_path(self, *parts: str) -> str:
        return "/".join(("repos", self.repository, *parts))


def _new_root() -> Path:
    return Path(tempfile.mkdtemp(prefix="mip-gh-session-"))



@contextmanager
def fresh_gh_session(
    repository: str,
    *,
    environ: Mapping[str, str] | None = None,
    make_root: Callable[[], Path] = _new_root,
    runner: Runner = subprocess.run,
) -> Iterator[GhSession]:
    """Yield a session in a new private, empty directory; delete it afterwards."""

    if REPOSITORY.fullmatch(repository) is None:
        raise BootstrapRefusal(["--github-repository: must be <owner>/<repository>"])
    root = make_root()
    os.chmod(root, 0o700)
    if any(root.iterdir()):
        raise BootstrapRefusal(["github: the temporary session directory is not empty; refusing to reuse it"])
    try:
        for directory in (root / "gh", root / "home"):
            directory.mkdir(mode=0o700)
            os.chmod(directory, 0o700)
        yield GhSession(
            root=root, repository=repository, environ=dict(os.environ if environ is None else environ), runner=runner
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)
