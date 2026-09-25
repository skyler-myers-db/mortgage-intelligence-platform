"""Which HTML shell the SPA fallback serves (2026-09-21 UI audit, ``bundle-02``).

The frontend build writes two shells into ``frontend/dist``:

- ``index.html``: the boot module, the entry script and the vendor
  modulepreloads. Every client route can start from it.
- ``index.home.html``: the same HTML plus a ``modulepreload`` for each JS file
  of the Home route's closure (``frontend/src/lib/bootModulePlugin.ts``), so a
  cold ``/`` fetches Home's chunks beside the entry instead of after it.

Rules (pure functions over a dist directory, so they are testable without a
build):

- exactly ``/`` (an empty path, whatever the query string) serves
  ``index.home.html`` when it exists, else ``index.html``;
- every deep link serves ``index.html``: a deep link that preloaded Home's
  chunks would pay for a route it is not showing;
- a direct request for either shell by name is a shell request too, so
  ``/index.home.html`` gets ``index.html`` rather than itself verbatim;
- every shell is served with ``SHELL_CACHE_CONTROL`` so a browser never keeps
  a shell that names retired hashed assets after a deploy.

A build without ``index.home.html`` (an older build) degrades to today's
single shell.
"""

from __future__ import annotations

from pathlib import Path

INDEX_SHELL = "index.html"
HOME_SHELL = "index.home.html"
SHELL_NAMES = frozenset({INDEX_SHELL, HOME_SHELL})
SHELL_CACHE_CONTROL = "no-cache, no-store, must-revalidate"


def is_shell_file(candidate: Path) -> bool:
    """True for a dist file that must only ever be served as a shell."""
    return candidate.name in SHELL_NAMES


def spa_shell_path(dist_dir: Path, full_path: str) -> Path:
    """The shell to serve for ``full_path`` (the SPA route without its leading slash)."""
    if full_path == "":
        home = dist_dir / HOME_SHELL
        if home.is_file():
            return home
    return dist_dir / INDEX_SHELL
