"""The GitHub-environment credential sink (M2-PR2).

``bootstrap_account --sink github-environment`` selects it. It implements
``CredentialSink`` (tools/clients/credential_sink.py) over a fresh gh session
(tools/clients/github_session.py), writing to the ``<client>`` environment's
secrets and nowhere else:

* ``ready`` logs in fresh (once) and refuses unless ``<client>`` exists with
  exactly the protections its own ``MIP_REVIEW_MODE`` variable names, for the
  signed-in maintainer, and a branch policy of exactly ``{main}``
  (tools/clients/github_protections.py). So no credential lands where an
  unreviewed branch, an admin bypass or a missing reviewer could read it.
  ``python -m tools.clients.github_environment --apply`` sets them up.
* ``put_identity_ids`` validates the pin map (``IdentityPins``) and writes
  it whole, as canonical JSON, to the ``MIP_IDENTITY_IDS_JSON`` secret.
* ``put_secret`` stores the field's exact value; the executor already built
  the agent proxy's canonical bundle. Only a field the bootstrap roster mints
  into is accepted: any other name, a runtime secret's above all, is refused.
* ``confirm_secret`` is true once the listing shows the field with an
  ``updatedAt`` other than the one seen just before this run wrote it.

Values go on stdin only; nothing is read back, because GitHub cannot return
a secret. A gh failure surfaces as its class and exit code, never its text.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from typing import Any

from pydantic import SecretStr, ValidationError

from tools.clients.bootstrap_plan import canonical_json
from tools.clients.bootstrap_roster import ROSTER
from tools.clients.errors import BootstrapRefusal, refusal_lines
from tools.clients.github_environment_plan import PINS_FIELD, named_listing
from tools.clients.github_protections import environments, protection_refusals, review_mode
from tools.clients.github_session import GhSession, fresh_gh_session
from tools.clients.json_fields import IdentityPins
from tools.clients.shapes import is_placeholder

# The profile secrets a bootstrap rotation mints into, and nothing else.
MINTED_FIELDS = frozenset(spec.secret_field for spec in ROSTER if spec.secret_field is not None)
CONFIRM_READS = 3
CONFIRM_INTERVAL_SECONDS = 2.0


class GitHubEnvironmentSink:
    """``CredentialSink`` over one client's GitHub Environment secrets."""

    def __init__(self, session: GhSession, client: str, *, sleep: Callable[[float], None] = time.sleep) -> None:
        self.session = session
        self.client = client
        self.sleep = sleep
        self._ready = False
        # Each field this run wrote, with the updatedAt it had just before.
        self._before: dict[str, str | None] = {}

    def ready(self) -> None:
        if self._ready:
            return
        maintainer = self.session.login()
        listing = environments(self.session)
        if self.client not in listing:
            raise BootstrapRefusal(
                [
                    f"credential sink: the {self.client} GitHub environment does not exist; run "
                    "python -m tools.clients.github_environment --apply first"
                ]
            )
        variables = named_listing(
            self.session.call_json(["variable", "list", "--env", self.client, "--json", "name,value"]),
            "value",
            "variable",
        )
        mode = review_mode(variables.get("MIP_REVIEW_MODE"))
        lines = protection_refusals(self.session, self.client, mode, int(maintainer["id"]), listing=listing)
        if lines:
            raise BootstrapRefusal(
                [f"credential sink: {line}" for line in lines]
                + ["credential sink: re-run python -m tools.clients.github_environment --apply, then retry"]
            )
        self._ready = True

    def _updates(self) -> dict[str, str]:
        return named_listing(
            self.session.call_json(["secret", "list", "--env", self.client, "--json", "name,updatedAt"]),
            "updatedAt",
            "secret",
        )

    def _write(self, name: str, value: bytes) -> None:
        self.ready()
        self._before[name] = self._updates().get(name)
        self.session.call(["secret", "set", name, "--env", self.client], stdin=value)

    def put_identity_ids(self, pins: Mapping[str, str]) -> None:
        try:
            validated = IdentityPins.model_validate(dict(pins))
        except ValidationError as exc:
            raise BootstrapRefusal([f"{PINS_FIELD}.{line}" for line in refusal_lines(exc)]) from None
        by_role = validated.by_role()
        if not by_role or any(is_placeholder(value) for value in by_role.values()):
            raise BootstrapRefusal([f"{PINS_FIELD}: the pin map is empty or holds a <placeholder>"])
        self._write(PINS_FIELD, canonical_json(by_role))

    def put_secret(self, field: str, application_id: str, secret: SecretStr) -> None:
        if field not in MINTED_FIELDS:
            raise BootstrapRefusal([f"{field}: is not a secret the bootstrap mints; the sink refuses it"])
        value = secret.get_secret_value()
        if not value:
            raise BootstrapRefusal([f"{field}: the value to store is empty"])
        self._write(field, value.encode("utf-8"))

    def confirm_secret(self, field: str) -> bool:
        if field not in self._before:
            return False
        before = self._before[field]
        for attempt in range(CONFIRM_READS):
            updated = self._updates().get(field)
            if updated is not None and updated != before:
                return True
            if attempt + 1 < CONFIRM_READS:
                self.sleep(CONFIRM_INTERVAL_SECONDS)
        return False


SinkFactory = Callable[[str, str], AbstractContextManager[Any]]


@contextmanager
def github_environment_sink(
    client: str,
    repository: str,
    *,
    open_session: Callable[[str], AbstractContextManager[GhSession]] = fresh_gh_session,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[GitHubEnvironmentSink]:
    """The sink for ``client``'s environment of ``repository``, in a fresh gh session."""

    with open_session(repository) as session:
        yield GitHubEnvironmentSink(session, client, sleep=sleep)
