"""Views a validated profile hands the deploy: bundle variables, deploy
controls, App payload env, log masks and the profile digest.

None of these views carries a secret field. Secrets keep reaching their
consumers through the step env, as today.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel

from tools.clients.policy import install_policy, policy_controls
from tools.clients.rules import SUMMIT_LENDER_NAME, lender_record_json
from tools.clients.schema import (
    ALIASES,
    FIELD_CLASS,
    IDENTITY_ALIASES,
    JSON_LEAF_CLASS,
    ClientProfile,
    FieldClass,
    LeafClass,
)
from tools.clients.shapes import (
    CLOUDS,
    INHERITED_SOURCE_PREFIX,
    VOCABULARY_TERMS,
    https_host,
    is_iso_date,
    is_placeholder,
    is_platform_name,
    is_vocabulary,
)

_JSON_FIELDS = frozenset(JSON_LEAF_CLASS)
# Fields whose value names a URL origin: the hostname is what logs print bare.
_ORIGIN_FIELDS = frozenset({"DATABRICKS_HOST", "DATABRICKS_ACCOUNT_HOST"})
# The approver and admin automation identities the deploy grants App roles to
# (deploy-dev.yml and deploy_step_automation_identities.sh today).
_APPROVER_ROLES = ("normal", "operator2")
_ADMIN_ROLES = ("admin", "release_probe")
# The Cotality source fields, each named as the databricks.yml variable it sets
# (the feature pipeline's configuration reads those variables).
_COTALITY_SOURCE_VARIABLES = (
    "cotality_source_catalog",
    "cotality_source_schema",
    "cotality_source_table_prefix",
)
# A mask this short blanks fragments of ordinary words (``qa`` in ``aqua``).
MIN_MASK_LENGTH = 3
# The words refusal lines and exposure decisions are made of: the schema
# vocabulary and every profile field and alias name.
_READABLE_TERMS = tuple(
    sorted(
        {
            *VOCABULARY_TERMS,
            *(
                name.casefold()
                for name in (*FIELD_CLASS, *IDENTITY_ALIASES, *(a for names in ALIASES.values() for a in names))
            ),
        }
    )
)


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _plain(value.model_dump(exclude_none=True))
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    return value


def _env_text(name: str, value: Any) -> str:
    """One field as the single-line text an environment variable carries."""

    if name in _JSON_FIELDS:
        return json.dumps(_plain(value), sort_keys=True, separators=(",", ":"))
    if isinstance(value, tuple):
        return ",".join(value)
    return str(value)


def canonical_env(profile: ClientProfile, *, include_secrets: bool = False) -> dict[str, str]:
    """Every set field under its environment name; secrets only on request."""

    env: dict[str, str] = {}
    for attribute in ClientProfile.model_fields:
        name = ClientProfile.env_name(attribute)
        value = getattr(profile, attribute)
        if value is None or value == ():
            continue
        if FIELD_CLASS[name] is FieldClass.SECRET:
            if include_secrets:
                env[name] = value.get_secret_value()
            continue
        env[name] = _env_text(name, value)
    return env


def account_host(profile: ClientProfile) -> str:
    """The account console origin: the configured one, else the cloud's."""

    return profile.account_host or f"https://{CLOUDS[profile.cloud][1]}"


def _lender_env(profile: ClientProfile) -> dict[str, str]:
    """The lender env: Summit for demo; the pinned record and its pin for a customer.

    Every consumer re-verifies the record against the pin
    (backend/schemas/lender_identity_record.py), so this cannot widen the
    reviewed identities: a customer still needs the reviewed digest.
    """

    identity = profile.lender_identity
    if identity is None:
        return {"MIP_LENDER_NAME": SUMMIT_LENDER_NAME}
    env = {
        "MIP_LENDER_NAME": identity.legal_name,
        "MIP_LENDER_NMLS_ID": identity.nmls_id,
        "MIP_TENANT_ID": identity.tenant_id,
        "MIP_LENDER_IDENTITY_JSON": lender_record_json(identity),
    }
    if profile.lender_identity_sha256 is not None:
        env["MIP_LENDER_IDENTITY_SHA256"] = profile.lender_identity_sha256
    return env


def client_target_vars(profile: ClientProfile) -> dict[str, str]:
    """The ``client`` target's own variables: its root, its run_as and its overrides.

    The deployer's and the jobs identity's application ids are lower-cased:
    they name the deployer's home folder and each ``service_principal_name``,
    which the platform spells in lower case. The jobs identity (M2-PR5) is the
    run_as of the App-triggerable and refresh jobs and the pipeline. An unset
    dashboard folder keeps its bundle default. An unset readable-secondaries
    flag keeps the retired targets' posture: on for a customer install (the
    prod target attached readable secondaries), the bundle default (off) for a
    demo one.
    """

    # Bound to a name first: tests/unit/test_bootstrap_account_safety.py reads
    # ``<x>.jobs.<method>(...)`` in tools/clients as a Jobs API call.
    jobs_application_id = profile.identity_ids.jobs
    variables = {
        "client_key": profile.client,
        "deployer_principal": profile.identity_ids.deployer.lower(),
        "jobs_principal": jobs_application_id.lower(),
    }
    if profile.dashboard_parent_path is not None:
        variables["dashboard_parent_path"] = profile.dashboard_parent_path
    if profile.lakebase_readable_secondaries is not None:
        variables["lakebase_readable_secondaries"] = profile.lakebase_readable_secondaries
    elif profile.install_class == "customer":
        variables["lakebase_readable_secondaries"] = "true"
    return variables


def client_target(profile: ClientProfile) -> dict[str, str]:
    """What scripts/deploy.sh keeps from the profile before any other step.

    The client key, the install class, the profile's workspace host, the
    install policy's closed values (tools/clients/policy.py: App env, demo
    feeds, presenter mode, weekly refresh) and the ``client`` target's
    variables as ``BUNDLE_VAR_*`` exports, so a direct ``databricks bundle run
    -t client`` addresses the same root as tools/databricks/bundle_env.py.
    """

    return {
        "MIP_CLIENT": profile.client,
        "MIP_INSTALL_CLASS": profile.install_class,
        "DATABRICKS_HOST": profile.databricks_host,
        **policy_controls(install_policy(profile)),
        **{f"BUNDLE_VAR_{name}": value for name, value in client_target_vars(profile).items()},
    }


def bundle_vars(profile: ClientProfile) -> dict[str, str]:
    """Values for the bundle variables databricks.yml declares today.

    ``scheduled_refresh_failure_recipients`` is a complex variable the CLI
    refuses from the environment, so it is not here.
    """

    lender = _lender_env(profile)
    variables = {
        **client_target_vars(profile),
        "uc_catalog": profile.default_catalog,
        "app_name": profile.app_name,
        "lakebase_instance_name": profile.lakebase_instance,
        "lakebase_catalog_name": profile.lakebase_sync_catalog,
        "lakebase_database_name": profile.lakebase_database,
        "runtime_secret_scope": profile.runtime_secret_scope,
        "scheduled_refresh_pause_status": install_policy(profile).pause_status,
        "lender_name": lender["MIP_LENDER_NAME"],
    }
    if "MIP_LENDER_NMLS_ID" in lender:
        variables["lender_nmls_id"] = lender["MIP_LENDER_NMLS_ID"]
        variables["tenant_id"] = lender["MIP_TENANT_ID"]
        variables["lender_identity_json"] = lender["MIP_LENDER_IDENTITY_JSON"]
        variables["lender_identity_sha256"] = lender.get("MIP_LENDER_IDENTITY_SHA256", "")
    # The Cotality source names, when set; an unset one keeps the variable's
    # canonical default (backend/config/source_names.py).
    for variable in _COTALITY_SOURCE_VARIABLES:
        value = getattr(profile, variable)
        if value is not None:
            variables[variable] = value
    if profile.identity_ids.verifier is not None:
        variables["ai_gateway_verifier_client_id"] = profile.identity_ids.verifier
    return variables


def deploy_controls(profile: ClientProfile) -> dict[str, str]:
    """Every public and identifying value under the names scripts/deploy.sh reads.

    That is the canonical env, plus the alias names the deploy tooling reads
    for the same values today.
    """

    controls = canonical_env(profile)
    controls["APP_ENV"] = profile.app_env
    controls["LAKEBASE_INSTANCE_NAME"] = profile.lakebase_instance
    controls["MIP_LAKEBASE_DATABASE_NAME"] = profile.lakebase_database
    controls["DATABRICKS_ACCOUNT_HOST"] = account_host(profile)
    for name, role in IDENTITY_ALIASES.items():
        value = getattr(profile.identity_ids, role)
        if value is not None:
            controls[name] = value
    return dict(sorted(controls.items()))


def _identities(configured: tuple[str, ...], *derived: str | None) -> str:
    """Configured identities first, then the derived ones, without repeats."""

    values: list[str] = []
    for value in (*configured, *derived):
        if value and value not in values:
            values.append(value)
    return ",".join(values)


def app_payload_env(profile: ClientProfile) -> dict[str, str]:
    """The App runtime env the profile owns (the rest stays with the payload builder)."""

    ids = profile.identity_ids
    policy = policy_controls(install_policy(profile))
    env = {
        "APP_ENV": policy["MIP_APP_ENV"],
        "MIP_DEFAULT_CATALOG": profile.default_catalog,
        "MIP_APP_NAME": profile.app_name,
        "MIP_LAKEBASE_INSTANCE": profile.lakebase_instance,
        "LAKEBASE_INSTANCE_NAME": profile.lakebase_instance,
        "LAKEBASE_DATABASE": profile.lakebase_database,
        "MIP_LAKEBASE_DATABASE_NAME": profile.lakebase_database,
        "MIP_LAKEBASE_SYNC_CATALOG": profile.lakebase_sync_catalog,
        "MIP_LAKEBASE_SYNC_SCHEMA": profile.lakebase_sync_schema,
        "MIP_RUNTIME_SECRET_SCOPE": profile.runtime_secret_scope,
        "MIP_APP_ROLLBACK_SECRET_SCOPE": profile.app_rollback_secret_scope,
        "MIP_GENIE_SPACE_NAME": profile.genie_space_name,
        "MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS": policy["MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS"],
        "MIP_PRESENTER_MODE": policy["MIP_PRESENTER_MODE"],
        "MIP_APPROVER_IDENTITIES": _identities(
            profile.approver_identities, *(getattr(ids, role) for role in _APPROVER_ROLES)
        ),
        "MIP_ADMIN_IDENTITIES": _identities(
            profile.admin_identities, *(getattr(ids, role) for role in _ADMIN_ROLES)
        ),
        **_lender_env(profile),
    }
    optional = {
        "MIP_AUDITOR_IDENTITIES": ",".join(profile.auditor_identities),
        "MIP_ADMIN_EMAILS": ",".join(profile.admin_emails),
        "MIP_APPROVER_EMAILS": ",".join(profile.approver_emails),
        "MIP_AUDITOR_EMAILS": ",".join(profile.auditor_emails),
        "MIP_ADMIN_GROUP_NAME": profile.admin_group_name or "",
        "MIP_APPROVER_GROUP_NAME": profile.approver_group_name or "",
    }
    env.update((name, value) for name, value in optional.items() if value)
    if profile.agentic_tier == "on":
        env["MIP_AGENT_SUPERVISOR_NAME"] = profile.agent_supervisor_name
        env["MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE"] = profile.ai_gateway_experiment_base
        env["MIP_AI_GATEWAY_TABLE_PREFIX"] = profile.ai_gateway_table_prefix
    else:
        # The core tier ships the agentic surfaces off (plan §4.15).
        env["MIP_AGENT_ORCHESTRATOR"] = "0"
        env["MIP_AI_GATEWAY"] = "0"
    return dict(sorted(env.items()))


def iter_leaves(value: Any, path: str = "") -> Iterator[tuple[str, str]]:
    """Each string leaf of a JSON value with its class-map path (``[]`` for items)."""

    if isinstance(value, dict):
        for key, item in value.items():
            yield from iter_leaves(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for item in value:
            yield from iter_leaves(item, f"{path}[]")
    elif isinstance(value, str):
        yield path, value
    elif isinstance(value, int | float) and not isinstance(value, bool):
        yield path, str(value)


def _generic(value: str) -> bool:
    """A value that names no client, so masking it only blanks unrelated text.

    That is a value shorter than ``MIN_MASK_LENGTH``, a platform-default name,
    or a fragment of a readable term (``admin`` inside ``admins``).
    """

    folded = value.casefold()
    return (
        len(value) < MIN_MASK_LENGTH
        or is_platform_name(value)
        or any(folded in term for term in _READABLE_TERMS)
    )


def _maskable(value: str) -> bool:
    """Vocabulary, ISO dates, placeholders and generic values are never masks."""

    return (
        bool(value)
        and not is_vocabulary(value)
        and not is_iso_date(value)
        and not is_placeholder(value)
        and not _generic(value)
    )


def _field_masks(name: str, value: Any) -> Iterator[str]:
    if name in _JSON_FIELDS:
        classes = JSON_LEAF_CLASS[name]
        for path, leaf in iter_leaves(_plain(value)):
            if classes.get(path, LeafClass.IDENTIFYING) is not LeafClass.IDENTIFYING:
                continue
            # An ``inherited:<path>`` source masks its path, not the vocabulary prefix.
            yield leaf.removeprefix(INHERITED_SOURCE_PREFIX) if path.endswith("source") else leaf
    elif isinstance(value, tuple):
        yield from value
    elif name in _ORIGIN_FIELDS and not is_placeholder(value):
        yield https_host(value)
    else:
        yield str(value)


def masks(profile: ClientProfile) -> tuple[str, ...]:
    """Every identifying leaf, longest first, for ``::add-mask::``.

    A leaf missing from ``JSON_LEAF_CLASS`` is treated as identifying, so a
    new JSON key fails closed into the mask list. A generic leaf (see
    ``_generic``) is left out. This list decides only what is added to the
    runner's own masks: GitHub still masks each secret's whole value, so a
    scalar secret that is itself generic stays blanked wherever it prints.
    """

    found: set[str] = set()
    for attribute in ClientProfile.model_fields:
        name = ClientProfile.env_name(attribute)
        value = getattr(profile, attribute)
        if FIELD_CLASS[name] is not FieldClass.IDENTIFYING or value is None:
            continue
        found.update(item for item in _field_masks(name, value) if _maskable(item))
    return tuple(sorted(found, key=lambda item: (-len(item), item)))


def digest(profile: ClientProfile) -> str:
    """sha256 over the canonical public and identifying fields, never secrets."""

    canonical = json.dumps(canonical_env(profile), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
