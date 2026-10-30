"""Identity-scoped, exact accepted exposure with a privilege ceiling (plan 4.12, M2-PR4).

A client install shares its workspace and metastore with other teams, and
some of their objects reach this install's identities: ``users`` holds
``CAN_MANAGE`` on every space under ``/Workspace/Shared`` by default, and a
foreign catalog may grant ``account users`` ``SELECT``. The global audits
refuse every such grant. Under the no-revoke ruling the owner cannot always
remove one, so the client profile may accept it, exactly, in
``MIP_ACCEPTED_EXPOSURE_JSON`` (``tools.clients.json_fields.ExposureEntry``).

**Exact tuple.** An entry accepts one observed exposure only when every field
is equal: kind; securable type, id and full name (or title); grantee; the
exact privilege set; source (``direct`` or ``inherited:<path>``); binding
(isolation mode and bound workspace ids). A broader, narrower, renamed,
re-parented or re-bound exposure is a different tuple and refuses. A UC
catalog or registered model has no immutable id in the API, so its id is its
full name. A workspace object (a Genie space) lives in exactly one workspace,
so its binding is ``ISOLATED`` to that one workspace id.

**Ceiling.** The read class is Genie ``CAN_VIEW``/``CAN_RUN`` and Unity
Catalog ``BROWSE``/``USE_CATALOG``/``USE_SCHEMA``/``SELECT``; everything else,
ownership included, is elevated. An entry's ``privilege_class`` must equal the
class its privileges compute to, so an elevated grant is never accepted
implicitly. Elevated is refused for ``runtime`` and ``proxy``: the object's
owner must narrow the grant, or the agentic tier stays off. It is accepted for
``app`` alone, explicitly, for at most 30 days, and only while the static
control holds (``genie_single_space``: every backend Genie API call goes
through the one client bound to the pinned governed space). An entry naming
the verifier, or the governed Genie space, is refused.

**Matching.** ``ExposurePolicy.matcher(identity)`` returns the one matcher of
``app``, ``runtime`` or ``proxy``; the verifier has none, so its
``--forbid-all-genie`` audit stays absolute. Each identity's matcher is handed
to its pre-stop probe and to its post-stop audits, which build the observed
tuples with the same functions, so both refuse the same exposures. An entry
past ``expires_at`` still parses and never matches.

**Plan digest.** The owner attestation is deferred (single_maintainer_audited
review mode), so the entries are pinned by an exact digest ack instead: the
sha256 of the canonical plan below. A run that judges a non-empty policy needs
``MIP_ACK_ACCEPTED_EXPOSURE_PLAN`` set to that digest for that run only, and
every consumer recomputes the plan from the profile immediately before it
judges an exposure, so a changed entry refuses until it is acknowledged again.
An empty policy needs no ack and accepts nothing: the audits behave as before.

Refusal lines name entry indexes, fields and rules, never a value: a refusal
reaches public CI logs, and every identifying leaf of an entry is masked.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError

from tools.clients.json_fields import ExposureEntry
from tools.clients.profile import iter_leaves
from tools.clients.shapes import DIRECT_SOURCE, INHERITED_SOURCE_PREFIX, is_placeholder

EXPOSURE_ENV = "MIP_ACCEPTED_EXPOSURE_JSON"
ACK_ENV = "MIP_ACK_ACCEPTED_EXPOSURE_PLAN"
GOVERNED_SPACE_NAME_ENV = "MIP_GENIE_SPACE_NAME"
# The verifier's application id under the names the deploy tooling reads.
VERIFIER_ENVS = ("DATABRICKS_VERIFIER_CLIENT_ID", "MIP_AI_GATEWAY_VERIFIER_CLIENT_ID")
IDENTITIES = ("app", "runtime", "proxy")
PLAN_VERSION = 1

GENIE_SPACE = "GENIE_SPACE"
GENIE_READ = frozenset({"CAN_VIEW", "CAN_RUN"})
UC_READ = frozenset({"BROWSE", "USE_CATALOG", "USE_SCHEMA", "SELECT"})
READ, ELEVATED = "read", "elevated"
ELEVATED_IDENTITIES = frozenset({"app"})
ELEVATED_MAX = timedelta(days=30)
UC_GRANT, UC_OWNERSHIP, WORKSPACE_PERMISSION = "uc_grant", "uc_ownership", "workspace_permission"
OWNERSHIP = "OWNERSHIP"
# Securable types a workspace permission names; the rest are Unity Catalog's.
WORKSPACE_TYPES = frozenset(
    {"APP", "DASHBOARD", "EXPERIMENT", GENIE_SPACE, "JOB", "PIPELINE", "SERVING_ENDPOINT", "WAREHOUSE"}
)
OPEN, ISOLATED = "OPEN", "ISOLATED"

# Verdict codes: schema vocabulary only, so a refusal line can carry them.
ACCEPTED = "accepted"
UNLISTED = "unlisted"
CHANGED = "changed"
OTHER_IDENTITY = "other_identity"
EXPIRED = "expired"


class ExposureRefusal(RuntimeError):
    """The accepted-exposure policy cannot be used; each line names a field and a rule."""

    def __init__(self, lines: Sequence[str]) -> None:
        self.lines = tuple(lines)
        super().__init__("accepted exposure refused: " + "; ".join(self.lines))


@dataclass(frozen=True)
class Exposure:
    """One observed (or accepted) exposure: the exact tuple the matcher compares."""

    kind: str
    securable_type: str
    securable_id: str
    securable_name: str
    grantee: str
    privileges: frozenset[str]
    source: str
    isolation_mode: str
    workspace_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "privileges", frozenset(self.privileges))
        object.__setattr__(self, "workspace_ids", tuple(sorted(self.workspace_ids)))

    @property
    def near_key(self) -> tuple[str, str, str, str]:
        """What stays put when a grant widens, narrows, moves or is renamed."""

        return (self.kind, self.securable_type, self.securable_id, self.grantee)

    def canonical(self) -> dict[str, Any]:
        """The tuple as an ``ExposureEntry`` JSON object (without the review fields)."""

        return {
            "kind": self.kind,
            "securable": {"type": self.securable_type, "id": self.securable_id, "name": self.securable_name},
            "grantee": self.grantee,
            "privileges": sorted(self.privileges),
            "source": self.source,
            "binding": {"isolation_mode": self.isolation_mode, "workspace_ids": list(self.workspace_ids)},
        }

    def describe(self) -> str:
        """A refusal fragment: kind, securable type and id, grantee, privileges, source.

        Never the securable's name or title: another team's object title must
        not reach a public log (prestop_genie_grants).
        """

        privileges = ", ".join(sorted(self.privileges)) or "no privilege"
        return (
            f"{self.kind} on {self.securable_type} {self.securable_id!r}: {self.grantee!r} "
            f"holds {privileges} ({self.source})"
        )


def privilege_class(exposure: Exposure) -> str:
    """``read`` for a Genie view/run or a UC browse/use/select grant; otherwise ``elevated``."""

    if exposure.kind == WORKSPACE_PERMISSION and exposure.securable_type == GENIE_SPACE:
        read = GENIE_READ
    elif exposure.kind == UC_GRANT:
        read = UC_READ
    else:
        return ELEVATED
    return READ if exposure.privileges and exposure.privileges <= read else ELEVATED


@dataclass(frozen=True)
class AcceptedEntry:
    """One reviewed entry: the exact tuple, who it covers and until when."""

    index: int
    exposure: Exposure
    identities: frozenset[str]
    privilege_class: str
    reason: str
    expires_text: str
    expires_at: datetime

    def canonical(self) -> dict[str, Any]:
        return {
            **self.exposure.canonical(),
            "identities": sorted(self.identities),
            "privilege_class": self.privilege_class,
            "reason": self.reason,
            "expires_at": self.expires_text,
        }


@dataclass(frozen=True)
class Verdict:
    """Whether one observed exposure is accepted for one identity, and why not."""

    code: str
    entry: int | None = None

    @property
    def accepted(self) -> bool:
        return self.code == ACCEPTED


@dataclass(frozen=True)
class ExposureMatcher:
    """One identity's view of the policy; it accepts exact, unexpired, covering entries only."""

    identity: str
    entries: tuple[AcceptedEntry, ...]
    now: datetime

    @property
    def active(self) -> bool:
        """Whether any entry covers this identity, so a consumer may skip extra reads."""

        return any(self.identity in entry.identities for entry in self.entries)

    def verdict(self, observed: Exposure) -> Verdict:
        # The policy refuses a repeated tuple, so at most one entry is exact.
        exact = [entry for entry in self.entries if entry.exposure == observed]
        if exact:
            entry = exact[0]
            if self.identity not in entry.identities:
                return Verdict(OTHER_IDENTITY, entry.index)
            if entry.expires_at <= self.now:
                return Verdict(EXPIRED, entry.index)
            return Verdict(ACCEPTED, entry.index)
        near = [entry for entry in self.entries if entry.exposure.near_key == observed.near_key]
        return Verdict(CHANGED, near[0].index) if near else Verdict(UNLISTED)

    def accepts(self, observed: Exposure) -> bool:
        return self.verdict(observed).accepted

    def accepts_all(self, observed: Iterable[Exposure]) -> bool:
        """Every exposure accepted; an empty collection is never accepted."""

        items = list(observed)
        return bool(items) and all(self.accepts(item) for item in items)

    def assert_not_governed(self, *, space_ids: Iterable[str], space_names: Iterable[str]) -> None:
        """Refuse an entry that names the governed Genie space: it is never relaxed."""

        lines = _governed_lines(self.entries, space_ids=space_ids, space_names=space_names)
        if lines:
            raise ExposureRefusal(lines)


@dataclass(frozen=True)
class ExposurePolicy:
    """The reviewed entries, their plan digest and the moment they are judged at."""

    entries: tuple[AcceptedEntry, ...]
    now: datetime
    digest: str = field(default="")

    def matcher(self, identity: str) -> ExposureMatcher:
        if identity not in IDENTITIES:
            raise ValueError(
                f"no accepted-exposure matcher for {identity!r}: only app, runtime and proxy "
                "consult one; the verifier's audits stay absolute"
            )
        return ExposureMatcher(identity=identity, entries=self.entries, now=self.now)


def parse_expiry(value: str) -> datetime:
    """An ISO date (midnight UTC, the earliest reading) or an offset-qualified time."""

    text = value.strip()
    if len(text) == 10:
        return datetime.fromisoformat(text).replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("a time needs Z or an offset")
    return parsed.astimezone(UTC)


def _exposure(entry: ExposureEntry) -> Exposure:
    return Exposure(
        kind=entry.kind,
        securable_type=entry.securable.type,
        securable_id=entry.securable.id,
        securable_name=entry.securable.name,
        grantee=entry.grantee,
        privileges=frozenset(entry.privileges),
        source=entry.source,
        isolation_mode=entry.binding.isolation_mode,
        workspace_ids=entry.binding.workspace_ids,
    )


def _shape_lines(index: int, exposure: Exposure) -> list[str]:
    """Tuples no audit can ever observe: refused, so a typo cannot hide as a dead entry."""

    at = f"entry {index}"
    lines: list[str] = []
    workspace = exposure.kind == WORKSPACE_PERMISSION
    if workspace != (exposure.securable_type in WORKSPACE_TYPES):
        lines.append(f"{at}: securable.type does not fit kind {exposure.kind}")
    if exposure.kind == UC_OWNERSHIP and exposure.privileges != {OWNERSHIP}:
        lines.append(f"{at}: uc_ownership holds exactly OWNERSHIP")
    if exposure.kind != UC_OWNERSHIP and OWNERSHIP in exposure.privileges:
        lines.append(f"{at}: only uc_ownership holds OWNERSHIP")
    if len(set(exposure.workspace_ids)) != len(exposure.workspace_ids):
        lines.append(f"{at}: binding.workspace_ids repeats an id")
    if exposure.isolation_mode == OPEN and exposure.workspace_ids:
        lines.append(f"{at}: an OPEN binding lists no workspace ids")
    if exposure.isolation_mode == ISOLATED and not exposure.workspace_ids:
        lines.append(f"{at}: an ISOLATED binding lists its bound workspace ids")
    if workspace and (exposure.isolation_mode != ISOLATED or len(exposure.workspace_ids) != 1):
        lines.append(f"{at}: a workspace object is ISOLATED to exactly one workspace id")
    if exposure.source != DIRECT_SOURCE and not exposure.source.startswith(INHERITED_SOURCE_PREFIX):
        lines.append(f"{at}: source must be direct or inherited:<path>")
    return lines


def _ceiling_lines(
    entry: AcceptedEntry, *, now: datetime, static_control: Callable[[], Sequence[str]]
) -> list[str]:
    at = f"entry {entry.index}"
    computed = privilege_class(entry.exposure)
    if entry.privilege_class != computed:
        return [
            f"{at}: privilege_class is {entry.privilege_class} but its privileges are "
            f"{computed}; an elevated grant is accepted only explicitly"
        ]
    if computed != ELEVATED:
        return []
    lines: list[str] = []
    refused = sorted(entry.identities - ELEVATED_IDENTITIES)
    if refused:
        lines.append(
            f"{at}: elevated exposure is refused for {', '.join(refused)}; the object's owner "
            "must narrow the grant to the read class, or the agentic tier stays off"
        )
    if entry.expires_at - now > ELEVATED_MAX:
        lines.append(f"{at}: elevated exposure expires at most 30 days ahead")
    violations = list(static_control())
    if violations:
        lines.append(
            f"{at}: elevated App exposure needs the single-space Genie control, which fails: "
            + "; ".join(violations)
        )
    return lines


def _governed_lines(
    entries: Iterable[AcceptedEntry], *, space_ids: Iterable[str], space_names: Iterable[str]
) -> list[str]:
    ids = {value for value in space_ids if value}
    names = {value.casefold() for value in space_names if value}
    return [
        f"entry {entry.index}: names the governed Genie space, which no entry may relax"
        for entry in entries
        if entry.exposure.securable_type == GENIE_SPACE
        and (entry.exposure.securable_id in ids or entry.exposure.securable_name.casefold() in names)
    ]


def _parse(raw: str) -> tuple[list[ExposureEntry], list[str]]:
    text = raw.strip()
    if not text:
        return [], []
    try:
        payload = json.loads(text)
    except ValueError:
        return [], [f"{EXPOSURE_ENV}: must be single-line JSON"]
    if not isinstance(payload, list):
        return [], [f"{EXPOSURE_ENV}: must be a JSON list"]
    entries: list[ExposureEntry] = []
    lines: list[str] = []
    for index, item in enumerate(payload):
        try:
            entry = ExposureEntry.model_validate(item)
        except ValidationError as exc:
            # Location and rule only: pydantic's own text can quote the value.
            fields = sorted({".".join(str(part) for part in error["loc"]) or "entry" for error in exc.errors()})
            lines.append(f"entry {index}: invalid {', '.join(fields)}")
            continue
        if any(is_placeholder(leaf) for _path, leaf in iter_leaves(entry.model_dump(mode="json"))):
            lines.append(f"entry {index}: holds a <placeholder>; fill it in after the owner's review")
            continue
        entries.append(entry)
    return entries, lines


def build_policy(
    entries: Sequence[ExposureEntry],
    *,
    now: datetime,
    static_control: Callable[[], Sequence[str]],
    verifier_application_ids: Iterable[str] = (),
    governed_space_ids: Iterable[str] = (),
    governed_space_names: Iterable[str] = (),
) -> ExposurePolicy:
    """Apply the closed schema and the ceiling; refuse with every violation at once."""

    lines: list[str] = []
    accepted: list[AcceptedEntry] = []
    seen: dict[Exposure, int] = {}
    verifiers = {value.strip().casefold() for value in verifier_application_ids if value.strip()}
    for index, entry in enumerate(entries):
        exposure = _exposure(entry)
        lines.extend(_shape_lines(index, exposure))
        if exposure in seen:
            lines.append(f"entry {index}: repeats entry {seen[exposure]}'s exact tuple")
        seen.setdefault(exposure, index)
        if exposure.grantee.casefold() in verifiers:
            lines.append(f"entry {index}: names the verifier, whose audits never consult a matcher")
        try:
            expires_at = parse_expiry(entry.expires_at)
        except ValueError:
            lines.append(f"entry {index}: expires_at is not a date or an offset-qualified time")
            continue
        item = AcceptedEntry(
            index=index,
            exposure=exposure,
            identities=frozenset(entry.identities),
            privilege_class=entry.privilege_class,
            reason=entry.reason,
            expires_text=entry.expires_at,
            expires_at=expires_at,
        )
        lines.extend(_ceiling_lines(item, now=now, static_control=static_control))
        accepted.append(item)
    lines.extend(
        _governed_lines(accepted, space_ids=governed_space_ids, space_names=governed_space_names)
    )
    if lines:
        raise ExposureRefusal(lines)
    items = tuple(accepted)
    return ExposurePolicy(entries=items, now=now, digest=plan_digest(items))


def canonical_plan(entries: Iterable[AcceptedEntry]) -> str:
    """The plan the ack covers: every entry, canonically ordered, no time-dependent field."""

    items = sorted(
        (entry.canonical() for entry in entries),
        key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
    )
    return json.dumps({"version": PLAN_VERSION, "entries": items}, sort_keys=True, separators=(",", ":"))


def plan_digest(entries: Iterable[AcceptedEntry]) -> str:
    return hashlib.sha256(canonical_plan(entries).encode("utf-8")).hexdigest()


def _default_static_control() -> Sequence[str]:
    from tools.databricks.estate.genie_single_space import violations

    return violations()


def load_policy(
    environ: Mapping[str, str] | None = None,
    *,
    now: datetime | None = None,
    ack: str | None = None,
    static_control: Callable[[], Sequence[str]] | None = None,
    verifier_application_ids: Iterable[str] = (),
    governed_space_ids: Iterable[str] = (),
) -> ExposurePolicy:
    """Recompute the policy from ``MIP_ACCEPTED_EXPOSURE_JSON`` and check its ack.

    ``ack`` defaults to ``MIP_ACK_ACCEPTED_EXPOSURE_PLAN``. The governed space
    name (``MIP_GENIE_SPACE_NAME``) and the verifier ids the deploy exports
    join whatever the caller names.
    """

    env = os.environ if environ is None else environ
    moment = now or datetime.now(UTC)
    parsed, lines = _parse(env.get(EXPOSURE_ENV, ""))
    if lines:
        raise ExposureRefusal(lines)
    verifiers = [*verifier_application_ids, *(env.get(name, "") for name in VERIFIER_ENVS)]
    policy = build_policy(
        parsed,
        now=moment,
        static_control=static_control or _default_static_control,
        verifier_application_ids=verifiers,
        governed_space_ids=governed_space_ids,
        governed_space_names=[env.get(GOVERNED_SPACE_NAME_ENV, "")],
    )
    if policy.entries:
        acknowledged = (env.get(ACK_ENV, "") if ack is None else ack).strip()
        if acknowledged != policy.digest:
            raise ExposureRefusal(
                [
                    f"the accepted-exposure plan is not acknowledged: review it "
                    f"(python -m tools.databricks.estate.cli --plan), then set {ACK_ENV}="
                    f"{policy.digest} for this run only"
                ]
            )
    return policy


def identity_matcher(
    identity: str,
    environ: Mapping[str, str] | None = None,
    *,
    now: datetime | None = None,
    verifier_application_ids: Iterable[str] = (),
    governed_space_ids: Iterable[str] = (),
) -> ExposureMatcher:
    """Load the policy afresh and return one identity's matcher."""

    return load_policy(
        environ,
        now=now,
        verifier_application_ids=verifier_application_ids,
        governed_space_ids=governed_space_ids,
    ).matcher(identity)


__all__ = [
    "ACK_ENV",
    "EXPOSURE_ENV",
    "AcceptedEntry",
    "Exposure",
    "ExposureMatcher",
    "ExposurePolicy",
    "ExposureRefusal",
    "Verdict",
    "build_policy",
    "canonical_plan",
    "identity_matcher",
    "load_policy",
    "plan_digest",
    "privilege_class",
]
