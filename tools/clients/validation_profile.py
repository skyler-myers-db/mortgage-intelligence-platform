"""What nightly.yml reads of a client's profile, per kind of job (M1-PR8).

Two kinds of nightly job reach a workspace, each in its own GitHub
Environment, and neither can become the deployer:

* ``validator`` (``id-token: write``) runs in ``<client>-validate``, never in
  ``<client>``: the deployer's one federation policy trusts exactly
  ``environment:<client>``, and the validator's trusts exactly
  ``environment:<client>-validate`` (tools/clients/bootstrap_roster.py,
  ``VALIDATE_ENVIRONMENT_SUFFIX``). It reads the public and identifying names
  only and holds no secret: GitHub OIDC alone authenticates it.
* ``browser`` (no ``id-token``, so no ID token for any identity) runs in
  ``<client>``, where the credential sink writes the reserved operator
  identities' M2M secrets (tools/databricks/m2m_oauth_github.py refuses to
  write one to a ``-validate`` environment). It reads the same names plus the
  three operator secrets, ``DATABRICKS_CLIENT_SECRET`` (``normal``),
  ``DATABRICKS_OPERATOR2_CLIENT_SECRET`` (``operator2``) and
  ``DATABRICKS_ADMIN_CLIENT_SECRET`` (``admin``, the App's admin role: a
  workspace USER, never a workspace admin), and exchanges them for
  short-lived App bearers (tools/oauth_m2m_mint.py).

The names, under the profile's own classes (tools/clients/schema.py):

* variables (public): ``MIP_CLIENT``, ``MIP_APP_NAME``, ``MIP_DEFAULT_CATALOG``;
* secrets (identifying): ``DATABRICKS_HOST``, ``MIP_IDENTITY_IDS_JSON``;
* secrets (secret), the browser kind only: the three operator secrets.

No other profile name is ever bound, so no deploy-only secret (the account
principal's, the agent proxy's, a signing key) reaches a nightly step.

Each job binds the public and identifying names through the same per-name
collect step deploy-dev.yml uses (``python -m tools.clients.workflow_contexts
collect``, fed by ``collect_env("validator")`` here instead of the deploy's
full block). The operator secrets are bound only in the steps that mint
(``collect_env("operators")``, read from that step's own env by
tools/clients/validation_session.py) and never written to disk, so no npm or
Playwright step can read them.

``resolve`` applies tools/clients/environment.from_github's class rules (an
identifying or secret name in ``vars`` and a public one in ``secrets`` are
refused), binds ``MIP_CLIENT`` to the environment's client, and checks each
value's shape. Every refusal names a field and a rule, never a value.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from tools.clients import workflow_contexts as contexts
from tools.clients.environment import from_github
from tools.clients.errors import ProfileRefusal
from tools.clients.github_protections import VALIDATE_SUFFIX
from tools.clients.json_fields import IdentityIds
from tools.clients.profile import _maskable
from tools.clients.schema import FieldClass, name_class
from tools.clients.shapes import (
    DNS_NAME,
    UC_IDENTIFIER,
    https_host,
    is_placeholder,
    require,
    strict_json_loads,
)

PUBLIC = ("MIP_CLIENT", "MIP_APP_NAME", "MIP_DEFAULT_CATALOG")
IDENTIFYING = ("DATABRICKS_HOST", "MIP_IDENTITY_IDS_JSON")
# The reserved operator identities the browser job mints App bearers for, by role.
OPERATOR_SECRETS: dict[str, str] = {
    "normal": "DATABRICKS_CLIENT_SECRET",
    "operator2": "DATABRICKS_OPERATOR2_CLIENT_SECRET",
    "admin": "DATABRICKS_ADMIN_CLIENT_SECRET",
}
KINDS: dict[str, tuple[str, ...]] = {
    "validator": (*PUBLIC, *IDENTIFYING),
    "browser": (*PUBLIC, *IDENTIFYING, *OPERATOR_SECRETS.values()),
}
# What each kind of nightly step binds: the collect step (to files) and the
# minting steps (in their own env only).
COLLECTS: dict[str, tuple[str, ...]] = {
    "validator": KINDS["validator"],
    "operators": tuple(OPERATOR_SECRETS.values()),
}
_CLIENT_KEY = re.compile(r"[a-z][a-z0-9-]{1,30}")
# The environment each kind runs in, from the client key.
ENVIRONMENT_SUFFIX = {"validator": VALIDATE_SUFFIX, "browser": ""}
OWNER_ACTION = (
    "create it with python -m tools.clients.github_environment --apply and fill it with the "
    "validation profile (docs/deployment.md, 'Live validation per client')"
)


def collect_env(kind: str) -> dict[str, str]:
    """One nightly step's env: ``COLLECTS[kind]``'s names only, each bound one by one.

    The same shape as workflow_contexts.collect_env: a public name from
    ``vars`` by value, every name from ``secrets`` by value (the runner masks
    each, and resolve refuses a public one found there), and each identifying
    or secret name from ``vars`` by presence only.
    """

    names = COLLECTS[kind]
    public = [name for name in names if name_class(name) is FieldClass.PUBLIC]
    private = [name for name in names if name_class(name) is not FieldClass.PUBLIC]
    env = {f"{contexts.VAR_PREFIX}{name}": f"${{{{ vars.{name} }}}}" for name in public}
    env.update({f"{contexts.SECRET_PREFIX}{name}": f"${{{{ secrets.{name} }}}}" for name in names})
    env[contexts.MISFILED_ENV] = " ".join(f"${{{{ vars.{name} && '{name}' || '' }}}}" for name in private)
    return env


def client_of(environment: str, kind: str = "validator") -> str:
    """The client key of ``kind``'s environment (``<client>-validate`` or ``<client>``), or a value-free refusal."""

    suffix = ENVIRONMENT_SUFFIX[kind]
    name = environment.strip().casefold()
    client = name.removesuffix(suffix) if suffix else name
    # A client key ending in the suffix would make another client's validate
    # environment its own deploy environment.
    if (
        not name.endswith(suffix)
        or _CLIENT_KEY.fullmatch(client) is None
        or client in contexts.NON_CLIENT_ENVIRONMENTS
        or client.endswith(VALIDATE_SUFFIX)
    ):
        raise ProfileRefusal(
            [
                f"--environment: must be <client>{suffix} for the {kind} kind, the client key 2-31 "
                "lowercase letters, digits or '-', from a letter, and a client's"
            ]
        )
    return client


@dataclass(frozen=True)
class ValidationProfile:
    """The validated subset; secret values never repr."""

    client: str
    environment: str
    app_name: str
    catalog: str
    host: str
    identity: IdentityIds = field(repr=False)
    operator_secrets: Mapping[str, str] = field(default_factory=dict, repr=False)

    @property
    def validator(self) -> str:
        return self.identity.validator

    def operator(self, role: str) -> tuple[str, str]:
        """(application id, M2M secret) of one reserved operator role."""

        return getattr(self.identity, role), self.operator_secrets[role]


_SHAPES = {
    "MIP_APP_NAME": (DNS_NAME, "a lowercase DNS-style name"),
    "MIP_DEFAULT_CATALOG": (UC_IDENTIFIER, "a lowercase unquoted identifier"),
}


def _shape(name: str, value: str, refusals: list[str]) -> str:
    pattern, expected = _SHAPES[name]
    try:
        return require(value, pattern, expected=expected)
    except ValueError:
        refusals.append(f"{name}: must be {expected}")
        return ""


def _identity(value: str, refusals: list[str]) -> IdentityIds | None:
    try:
        identity = IdentityIds.model_validate(strict_json_loads(value))
    except (ValueError, TypeError):
        # The pydantic error would carry its input; name the field and the rule only.
        refusals.append("MIP_IDENTITY_IDS_JSON: must be the client's identity map, the validator role included")
        return None
    placeholders = [role for role, leaf in identity.by_role().items() if is_placeholder(leaf)]
    if placeholders:
        refusals.append(f"MIP_IDENTITY_IDS_JSON: {', '.join(placeholders)} is a template placeholder")
        return None
    return identity


def resolve(
    vars_context: Mapping[str, object],
    secrets_context: Mapping[str, object],
    *,
    environment: str,
    kind: str,
) -> ValidationProfile:
    """Validate ``kind``'s names from the two collected contexts, or raise ``ProfileRefusal``."""

    if kind not in KINDS:
        raise ProfileRefusal([f"--kind: must be one of {', '.join(KINDS)}"])
    client = client_of(environment, kind)
    named = f"{client}{ENVIRONMENT_SUFFIX[kind]}"
    if not str(vars_context.get("MIP_CLIENT") or "").strip():
        raise ProfileRefusal([f"{named}: holds no validation profile (no MIP_CLIENT variable); {OWNER_ACTION}"])
    wanted = set(KINDS[kind])
    raw = from_github(
        {name: value for name, value in vars_context.items() if name in wanted},
        {name: value for name, value in secrets_context.items() if name in wanted},
        environment=client,
    ).values
    refusals = [
        f"{name}: required in the {named} environment as a "
        f"{'variable' if name_class(name) is FieldClass.PUBLIC else 'secret'}"
        for name in KINDS[kind]
        if not raw.get(name, "").strip()
    ]
    refusals += [f"{name}: is a template placeholder" for name in KINDS[kind] if is_placeholder(raw.get(name, ""))]
    if refusals:
        raise ProfileRefusal(refusals)
    app_name = _shape("MIP_APP_NAME", raw["MIP_APP_NAME"], refusals)
    catalog = _shape("MIP_DEFAULT_CATALOG", raw["MIP_DEFAULT_CATALOG"], refusals)
    try:
        host = f"https://{https_host(raw['DATABRICKS_HOST'])}"
    except ValueError:
        refusals.append("DATABRICKS_HOST: must be a bare https:// origin with no path, port or credentials")
        host = ""
    identity = _identity(raw["MIP_IDENTITY_IDS_JSON"], refusals)
    if refusals or identity is None:
        raise ProfileRefusal(refusals)
    secrets = {role: raw[name] for role, name in OPERATOR_SECRETS.items() if name in wanted}
    return ValidationProfile(client, named, app_name, catalog, host, identity, secrets)


def masks(profile: ValidationProfile) -> tuple[str, ...]:
    """Every identifying value and the spellings a run derives from one, longest first."""

    found: set[str] = set()
    for value in (profile.host, profile.host.removeprefix("https://"), *profile.identity.by_role().values()):
        for spelling in (value, value.lower(), value.casefold()):
            if _maskable(spelling):
                found.add(spelling)
    return tuple(sorted(found, key=lambda item: (-len(item), item)))
