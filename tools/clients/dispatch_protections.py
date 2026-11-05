"""Read the deploy environment's protection rules with the job's GITHUB_TOKEN (M1-PR7, spike S8).

Every phase reads the live protections of the GitHub Environment it runs in
and compares them with what its review mode requires
(tools/clients/github_protections.py: the one maintainer as required reviewer,
``prevent_self_review`` per mode, ``can_admins_bypass=false`` and a
deployment branch policy of exactly ``{main}``). The maintainer is the
dispatching actor (``GITHUB_ACTOR_ID``): in ``single_maintainer_audited``
mode the one maintainer approves their own run.

Spike S8 is owed: it is not yet known whether GITHUB_TOKEN can read an
environment's protection rules. This module is built for the worst case. A
401, 403 or 404, a network error or a malformed listing is
``environment_protections_unreadable``, never "unprotected" and never
"verified"; the caller then records it in a plan and refuses a mutating phase
unless the dispatch carries the exact ``protections_unverified`` ack
(tools/clients/dispatch_acks.py).

Approval provenance (``GET /actions/runs/{id}/approvals``) applies to the
``independent`` review mode only, which the profile schema refuses until the
owner attestation layer exists, so it is reported "not applicable".
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from tools.clients import github_protections as protections
from tools.clients.errors import BootstrapRefusal

VERIFIED = "environment_protections_verified"
UNREADABLE = "environment_protections_unreadable"
DRIFT = "environment_protections_drift"
_TIMEOUT_SECONDS = 30


class Unreadable(Exception):
    """GitHub did not answer the read; the class name of the cause is the only detail."""


@dataclass(frozen=True)
class Result:
    code: str
    lines: tuple[str, ...] = ()
    cause: str = ""


Opener = Callable[[urllib.request.Request, int], Any]


def _open(request: urllib.request.Request, timeout: int) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - an https API origin


class ActionsTokenSession:
    """The session interface github_protections reads through, over GITHUB_TOKEN."""

    def __init__(self, *, token: str, repository: str, api_url: str = "https://api.github.com", opener: Opener = _open) -> None:
        if not token or not protections_repository(repository):
            raise Unreadable("ValueError")
        if not api_url.startswith("https://"):
            raise Unreadable("ValueError")
        self._token = token
        self.repository = repository
        self._api = api_url.rstrip("/")
        self._opener = opener

    def repo_path(self, *parts: str) -> str:
        return "/".join(("repos", self.repository, *parts))

    def api(self, method: str, path: str) -> Any:
        if method != "GET":
            raise Unreadable("PermissionError")
        request = urllib.request.Request(
            f"{self._api}/{path}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "mip-deploy-preflight",
            },
            method="GET",
        )
        try:
            with self._opener(request, _TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise Unreadable(f"HTTP {exc.code}") from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise Unreadable(type(exc).__name__) from None


def protections_repository(repository: str) -> bool:
    owner, _, name = repository.partition("/")
    return bool(owner and name and "/" not in name)


def read(session: Any, environment: str, mode_name: str, maintainer_id: str) -> Result:
    """Verified, drift (field names only) or unreadable; never raises."""

    try:
        mode = protections.review_mode(mode_name)
    except BootstrapRefusal as refusal:
        return Result(DRIFT, tuple(refusal.lines))
    if not maintainer_id.isdigit():
        return Result(UNREADABLE, cause="GITHUB_ACTOR_ID unset")
    try:
        listing = protections.environments(session)
        named = {name.casefold(): (name, item) for name, item in listing.items()}
        found = named.get(environment.casefold())
        if found is None:
            return Result(DRIFT, (f"github: the {environment} environment is not in the listing",))
        name, item = found
        lines = protections.protection_refusals(session, name, mode, int(maintainer_id), listing={name: item})
    except Unreadable as exc:
        return Result(UNREADABLE, cause=str(exc))
    except BootstrapRefusal:
        return Result(UNREADABLE, cause="malformed listing")
    return Result(DRIFT, tuple(lines)) if lines else Result(VERIFIED)


def approval_provenance(mode_name: str) -> str:
    if mode_name == "single_maintainer_audited":
        return "not applicable (single_maintainer_audited)"
    return "refused: independent review needs approval provenance, deferred with the attestation layer (M1-PR5)"


def from_environ(environ: Mapping[str, str], *, opener: Opener = _open) -> ActionsTokenSession:
    return ActionsTokenSession(
        token=environ.get("GITHUB_TOKEN", ""),
        repository=environ.get("GITHUB_REPOSITORY", ""),
        api_url=environ.get("GITHUB_API_URL", "https://api.github.com"),
        opener=opener,
    )
