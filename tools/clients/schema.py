"""The client profile schema (v1): fields, value classes and validation rules.

A profile field is named by its environment variable and has exactly one
class (plan §4.1):

* ``public``: a GitHub Environment variable in CI; may print in logs.
* ``identifying``: a GitHub Environment secret in CI; never printed in clear.
  The runner masks the whole secret, and ``profile.masks`` adds each
  identifying leaf of a JSON or list field, so a leaf printed on its own is
  masked too.
* ``secret``: a GitHub Environment secret; never printed, never digested.

Locally every class lives in ``.env.local``. ``JSON_LEAF_CLASS`` classes each
leaf of the JSON fields; schema vocabulary, ISO dates and generic values
(platform-default names, fragments of schema terms) are never masked, so
refusal lines and exposure decisions stay readable.
"""

from __future__ import annotations

import json
import re
from enum import StrEnum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationInfo,
    field_validator,
    model_validator,
)

from tools.clients import rules
from tools.clients.json_fields import ExposureEntry, IdentityIds, LenderIdentity
from tools.clients.shapes import (
    DNS_NAME,
    EMAIL,
    GROUP_NAME,
    PRINCIPAL,
    SECRET_SCOPE,
    SHARE_OBJECT_NAME,
    STORAGE_ROOT,
    TABLE_PREFIX,
    TITLE,
    UC_IDENTIFIER,
    UUID,
    WORKSPACE_ID,
    WORKSPACE_PATH,
    https_host,
    is_placeholder,
    is_vocabulary,
    require,
    split_list,
)


class FieldClass(StrEnum):
    PUBLIC = "public"
    IDENTIFYING = "identifying"
    SECRET = "secret"


class LeafClass(StrEnum):
    PUBLIC = "public"
    IDENTIFYING = "identifying"
    VOCABULARY = "vocabulary"
    DATE = "date"


_P, _I, _S = FieldClass.PUBLIC, FieldClass.IDENTIFYING, FieldClass.SECRET
FIELD_CLASS: dict[str, FieldClass] = {
    "MIP_CLIENT": _P,
    "MIP_PROFILE_VERSION": _P,
    "MIP_CLOUD": _P,
    "MIP_IDENTITY_PREFIX": _P,
    "MIP_INSTALL_CLASS": _P,
    "MIP_APP_ENV": _P,
    "MIP_AGENTIC_TIER": _P,
    "MIP_SCHEDULED_REFRESH": _P,
    "MIP_REVIEW_MODE": _P,
    "MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS": _P,
    "MIP_PRESENTER_MODE": _P,
    "MIP_LAKEBASE_READABLE_SECONDARIES": _P,
    "MIP_DEFAULT_CATALOG": _P,
    "MIP_LAKEBASE_SYNC_CATALOG": _P,
    "MIP_LAKEBASE_SYNC_SCHEMA": _P,
    "MIP_APP_NAME": _P,
    "MIP_LAKEBASE_INSTANCE": _P,
    "LAKEBASE_DATABASE": _P,
    "MIP_RUNTIME_SECRET_SCOPE": _P,
    "MIP_APP_ROLLBACK_SECRET_SCOPE": _P,
    "MIP_AGENT_PROXY_SECRET_SCOPE": _P,
    "MIP_PII_SALT_SCOPE": _P,
    "MIP_GENIE_SPACE_NAME": _P,
    "MIP_AGENT_SUPERVISOR_NAME": _P,
    "MIP_AI_GATEWAY_TABLE_PREFIX": _P,
    "MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE": _P,
    "MIP_COTALITY_SOURCE_CATALOG": _P,
    "MIP_COTALITY_SOURCE_SCHEMA": _P,
    "MIP_COTALITY_SOURCE_TABLE_PREFIX": _P,
    "MIP_LENDER_IDENTITY_SHA256": _P,
    "DATABRICKS_HOST": _I,
    "MIP_EXPECT_WORKSPACE_ID": _I,
    "MIP_EXPECT_METASTORE_ID": _I,
    "DATABRICKS_ACCOUNT_ID": _I,
    "DATABRICKS_ACCOUNT_HOST": _I,
    "MIP_IDENTITY_IDS_JSON": _I,
    "MIP_UC_METASTORE_OWNER": _I,
    "MIP_UC_APPROVED_OWNER_PRINCIPALS": _I,
    "MIP_GENIE_MANAGER_PRINCIPALS": _I,
    "MIP_SCHEDULED_REFRESH_RECIPIENTS": _I,
    "MIP_ADMIN_EMAILS": _I,
    "MIP_APPROVER_EMAILS": _I,
    "MIP_AUDITOR_EMAILS": _I,
    "MIP_ADMIN_IDENTITIES": _I,
    "MIP_APPROVER_IDENTITIES": _I,
    "MIP_AUDITOR_IDENTITIES": _I,
    "MIP_ADMIN_GROUP_NAME": _I,
    "MIP_APPROVER_GROUP_NAME": _I,
    "MIP_COTALITY_SHARE_PROVIDER": _I,
    "MIP_COTALITY_SHARE_NAME": _I,
    "MIP_UC_CATALOG_STORAGE_ROOT": _I,
    "MIP_DASHBOARD_PARENT_PATH": _I,
    "MIP_LENDER_IDENTITY_JSON": _I,
    "MIP_ACCEPTED_EXPOSURE_JSON": _I,
    "MIP_SEED_OPERATOR_EMAIL": _I,
    "DATABRICKS_CLIENT_SECRET": _S,
    "DATABRICKS_OPERATOR2_CLIENT_SECRET": _S,
    "DATABRICKS_ADMIN_CLIENT_SECRET": _S,
    "DATABRICKS_RELEASE_PROBE_CLIENT_SECRET": _S,
    "DATABRICKS_ACCOUNT_CLIENT_SECRET": _S,
    "MIP_AI_GATEWAY_PROOF_SIGNING_KEY": _S,
    "DATABRICKS_VERIFIER_CLIENT_SECRET": _S,
    "DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET": _S,
    "DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE": _S,
    "MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY": _S,
}

# Names the deploy tooling already reads for a scalar field. Each must agree
# with the field; an alias alone supplies the field (bundle_env.py's rule).
ALIASES: dict[str, tuple[str, ...]] = {
    "MIP_APP_ENV": ("APP_ENV",),
    "MIP_LAKEBASE_INSTANCE": ("LAKEBASE_INSTANCE_NAME",),
    "LAKEBASE_DATABASE": ("MIP_LAKEBASE_DATABASE_NAME",),
}
# Names the deploy tooling already reads for one MIP_IDENTITY_IDS_JSON role.
# They never supply a role; when set, each must equal it.
IDENTITY_ALIASES: dict[str, str] = {
    "MIP_CI_DEPLOYER_CLIENT_ID": "deployer",
    "DATABRICKS_CLIENT_ID": "normal",
    "DATABRICKS_OPERATOR2_CLIENT_ID": "operator2",
    "DATABRICKS_ADMIN_CLIENT_ID": "admin",
    "DATABRICKS_RELEASE_PROBE_CLIENT_ID": "release_probe",
    "DATABRICKS_VERIFIER_CLIENT_ID": "verifier",
    "MIP_AI_GATEWAY_VERIFIER_CLIENT_ID": "verifier",
    "DATABRICKS_AGENT_RUNTIME_CLIENT_ID": "runtime",
    "MIP_AGENT_RUNTIME_CLIENT_ID": "runtime",
    "DATABRICKS_AGENT_PROXY_CLIENT_ID": "proxy",
    "MIP_AGENT_PROXY_CLIENT_ID": "proxy",
    "DATABRICKS_ACCOUNT_CLIENT_ID": "account_principal",
}

_IV, _VO, _DA = LeafClass.IDENTIFYING, LeafClass.VOCABULARY, LeafClass.DATE
JSON_LEAF_CLASS: dict[str, dict[str, LeafClass]] = {
    "MIP_IDENTITY_IDS_JSON": {role: _IV for role in IdentityIds.model_fields},
    "MIP_LENDER_IDENTITY_JSON": {
        "version": LeafClass.PUBLIC,
        "legal_name": _IV,
        "nmls_id": _IV,
        "tenant_id": _IV,
        "servicer_aliases[]": _IV,
        "reviewed_by": _IV,
        "reviewed_at": _DA,
        "mark.sha256": _IV,
        "mark.media_type": LeafClass.PUBLIC,
        "mark.width": LeafClass.PUBLIC,
        "mark.height": LeafClass.PUBLIC,
        "mark.authorization_ref": _IV,
        "mark.reviewed_on": _DA,
    },
    "MIP_ACCEPTED_EXPOSURE_JSON": {
        "[].kind": _VO,
        "[].securable.type": _VO,
        "[].securable.id": _IV,
        "[].securable.name": _IV,
        # A grantee is identifying unless it is vocabulary (users, account
        # users, admins), which masks() never registers.
        "[].grantee": _IV,
        "[].privileges[]": _VO,
        # ``direct`` is vocabulary; an ``inherited:<path>`` path is identifying.
        "[].source": _IV,
        "[].binding.isolation_mode": _VO,
        "[].binding.workspace_ids[]": _IV,
        "[].identities[]": _VO,
        "[].privilege_class": _VO,
        "[].reason": _IV,
        "[].expires_at": _DA,
    },
}

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


def name_class(name: str) -> FieldClass | None:
    """The class of a field name or of one of its aliases; ``None`` if unknown."""

    if name in FIELD_CLASS:
        return FIELD_CLASS[name]
    if name in IDENTITY_ALIASES:
        return FIELD_CLASS["MIP_IDENTITY_IDS_JSON"]
    for canonical, aliases in ALIASES.items():
        if name in aliases:
            return FIELD_CLASS[canonical]
    return None


_SCALAR_SHAPES: dict[str, tuple[re.Pattern[str], str]] = {
    "client": (re.compile(r"[a-z][a-z0-9-]{1,30}"), "2-31 lowercase letters, digits or '-', from a letter"),
    "profile_version": (re.compile(r"1"), "1"),
    "cloud": (re.compile(r"aws|azure|gcp"), "aws, azure or gcp"),
    "identity_prefix": (re.compile(r"[a-z][a-z0-9-]{1,20}"), "2-21 lowercase letters, digits or '-'"),
    "install_class": (re.compile(r"demo|customer"), "demo or customer"),
    "agentic_tier": (re.compile(r"off|on"), "off or on"),
    "scheduled_refresh": (re.compile(r"off|on"), "off or on"),
    "lakebase_readable_secondaries": (re.compile(r"true|false"), "true or false"),
    "default_catalog": (UC_IDENTIFIER, "a lowercase unquoted identifier"),
    "lakebase_sync_catalog": (UC_IDENTIFIER, "a lowercase unquoted identifier"),
    "lakebase_sync_schema": (UC_IDENTIFIER, "a lowercase unquoted identifier"),
    "lakebase_database": (UC_IDENTIFIER, "a lowercase unquoted identifier"),
    "app_name": (DNS_NAME, "a lowercase DNS-style name"),
    "lakebase_instance": (DNS_NAME, "a lowercase DNS-style name"),
    "runtime_secret_scope": (SECRET_SCOPE, "a secret scope name"),
    "app_rollback_secret_scope": (SECRET_SCOPE, "a secret scope name"),
    "agent_proxy_secret_scope": (SECRET_SCOPE, "a secret scope name"),
    "pii_salt_scope": (SECRET_SCOPE, "a secret scope name"),
    "genie_space_name": (TITLE, "a title of at most 100 characters"),
    "agent_supervisor_name": (TITLE, "a title of at most 100 characters"),
    "ai_gateway_table_prefix": (UC_IDENTIFIER, "a lowercase unquoted identifier"),
    "ai_gateway_experiment_base": (DNS_NAME, "a lowercase DNS-style name"),
    "cotality_source_catalog": (UC_IDENTIFIER, "a lowercase unquoted identifier"),
    "cotality_source_schema": (UC_IDENTIFIER, "a lowercase unquoted identifier"),
    "cotality_source_table_prefix": (
        TABLE_PREFIX,
        "1 to 64 lowercase letters, digits or '_', or '-' for no prefix",
    ),
    "lender_identity_sha256": (re.compile(r"[0-9a-f]{64}"), "64 lowercase hex characters"),
    "expect_workspace_id": (WORKSPACE_ID, "a decimal workspace id"),
    "expect_metastore_id": (UUID, "a metastore id (UUID)"),
    "account_id": (UUID, "an account id (UUID)"),
    "admin_group_name": (GROUP_NAME, "a group name"),
    "approver_group_name": (GROUP_NAME, "a group name"),
    "cotality_share_provider": (SHARE_OBJECT_NAME, "a provider name"),
    "cotality_share_name": (SHARE_OBJECT_NAME, "a share name"),
    "catalog_storage_root": (STORAGE_ROOT, "an s3://, abfss:// or gs:// storage root"),
    "dashboard_parent_path": (WORKSPACE_PATH, "a /Workspace/... folder path"),
    "seed_operator_email": (EMAIL, "an email address"),
}
_EMAIL_LISTS = rules.EMAIL_LISTS
_IDENTITY_LISTS = rules.IDENTITY_LISTS
_PRINCIPAL_LISTS = ("approved_owner_principals", "genie_manager_principals")
_LISTS = (*_EMAIL_LISTS, *_IDENTITY_LISTS, *_PRINCIPAL_LISTS, "scheduled_refresh_recipients")


def _parse_json(value: object, *, expected: type) -> object:
    if not isinstance(value, str):
        return value
    try:
        parsed = json.loads(value)
    except ValueError:
        raise ValueError("must be single-line JSON") from None
    if not isinstance(parsed, expected):
        raise ValueError(f"must be a JSON {'list' if expected is list else 'object'}")
    return parsed


class ClientProfile(BaseModel):
    """One install's profile. Fields validate by their environment names."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    # Public.
    client: str = Field(alias="MIP_CLIENT")
    profile_version: str = Field(alias="MIP_PROFILE_VERSION")
    cloud: str = Field(alias="MIP_CLOUD")
    identity_prefix: str = Field(default="mip", alias="MIP_IDENTITY_PREFIX")
    install_class: str = Field(alias="MIP_INSTALL_CLASS")
    app_env: str = Field(alias="MIP_APP_ENV")
    agentic_tier: str = Field(alias="MIP_AGENTIC_TIER")
    scheduled_refresh: str = Field(alias="MIP_SCHEDULED_REFRESH")
    review_mode: str = Field(alias="MIP_REVIEW_MODE")
    demo_first_party_feeds: str | None = Field(default=None, alias="MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS")
    presenter_mode: str | None = Field(default=None, alias="MIP_PRESENTER_MODE")
    lakebase_readable_secondaries: str | None = Field(default=None, alias="MIP_LAKEBASE_READABLE_SECONDARIES")
    default_catalog: str = Field(alias="MIP_DEFAULT_CATALOG")
    lakebase_sync_catalog: str = Field(alias="MIP_LAKEBASE_SYNC_CATALOG")
    lakebase_sync_schema: str = Field(alias="MIP_LAKEBASE_SYNC_SCHEMA")
    app_name: str = Field(alias="MIP_APP_NAME")
    lakebase_instance: str = Field(alias="MIP_LAKEBASE_INSTANCE")
    lakebase_database: str = Field(alias="LAKEBASE_DATABASE")
    runtime_secret_scope: str = Field(alias="MIP_RUNTIME_SECRET_SCOPE")
    app_rollback_secret_scope: str = Field(alias="MIP_APP_ROLLBACK_SECRET_SCOPE")
    agent_proxy_secret_scope: str = Field(alias="MIP_AGENT_PROXY_SECRET_SCOPE")
    pii_salt_scope: str = Field(alias="MIP_PII_SALT_SCOPE")
    genie_space_name: str = Field(alias="MIP_GENIE_SPACE_NAME")
    agent_supervisor_name: str = Field(alias="MIP_AGENT_SUPERVISOR_NAME")
    ai_gateway_table_prefix: str = Field(alias="MIP_AI_GATEWAY_TABLE_PREFIX")
    ai_gateway_experiment_base: str = Field(alias="MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE")
    cotality_source_catalog: str | None = Field(default=None, alias="MIP_COTALITY_SOURCE_CATALOG")
    cotality_source_schema: str | None = Field(default=None, alias="MIP_COTALITY_SOURCE_SCHEMA")
    cotality_source_table_prefix: str | None = Field(default=None, alias="MIP_COTALITY_SOURCE_TABLE_PREFIX")
    # The reviewed pin of MIP_LENDER_IDENTITY_JSON (customer class only).
    lender_identity_sha256: str | None = Field(default=None, alias="MIP_LENDER_IDENTITY_SHA256")
    # Identifying.
    databricks_host: str = Field(alias="DATABRICKS_HOST")
    expect_workspace_id: str = Field(alias="MIP_EXPECT_WORKSPACE_ID")
    expect_metastore_id: str = Field(alias="MIP_EXPECT_METASTORE_ID")
    account_id: str = Field(alias="DATABRICKS_ACCOUNT_ID")
    account_host: str | None = Field(default=None, alias="DATABRICKS_ACCOUNT_HOST")
    identity_ids: IdentityIds = Field(alias="MIP_IDENTITY_IDS_JSON")
    metastore_owner: str = Field(alias="MIP_UC_METASTORE_OWNER")
    approved_owner_principals: tuple[str, ...] = Field(alias="MIP_UC_APPROVED_OWNER_PRINCIPALS")
    genie_manager_principals: tuple[str, ...] = Field(default=(), alias="MIP_GENIE_MANAGER_PRINCIPALS")
    scheduled_refresh_recipients: tuple[str, ...] = Field(default=(), alias="MIP_SCHEDULED_REFRESH_RECIPIENTS")
    admin_emails: tuple[str, ...] = Field(default=(), alias="MIP_ADMIN_EMAILS")
    approver_emails: tuple[str, ...] = Field(default=(), alias="MIP_APPROVER_EMAILS")
    auditor_emails: tuple[str, ...] = Field(default=(), alias="MIP_AUDITOR_EMAILS")
    admin_identities: tuple[str, ...] = Field(default=(), alias="MIP_ADMIN_IDENTITIES")
    approver_identities: tuple[str, ...] = Field(default=(), alias="MIP_APPROVER_IDENTITIES")
    auditor_identities: tuple[str, ...] = Field(default=(), alias="MIP_AUDITOR_IDENTITIES")
    admin_group_name: str | None = Field(default=None, alias="MIP_ADMIN_GROUP_NAME")
    approver_group_name: str | None = Field(default=None, alias="MIP_APPROVER_GROUP_NAME")
    cotality_share_provider: str | None = Field(default=None, alias="MIP_COTALITY_SHARE_PROVIDER")
    cotality_share_name: str | None = Field(default=None, alias="MIP_COTALITY_SHARE_NAME")
    catalog_storage_root: str | None = Field(default=None, alias="MIP_UC_CATALOG_STORAGE_ROOT")
    dashboard_parent_path: str | None = Field(default=None, alias="MIP_DASHBOARD_PARENT_PATH")
    lender_identity: LenderIdentity | None = Field(default=None, alias="MIP_LENDER_IDENTITY_JSON")
    accepted_exposure: tuple[ExposureEntry, ...] = Field(default=(), alias="MIP_ACCEPTED_EXPOSURE_JSON")
    seed_operator_email: str | None = Field(default=None, alias="MIP_SEED_OPERATOR_EMAIL")
    # Secret. The agentic-tier credentials are required only when the tier is on.
    normal_client_secret: SecretStr = Field(alias="DATABRICKS_CLIENT_SECRET")
    operator2_client_secret: SecretStr = Field(alias="DATABRICKS_OPERATOR2_CLIENT_SECRET")
    admin_client_secret: SecretStr = Field(alias="DATABRICKS_ADMIN_CLIENT_SECRET")
    release_probe_client_secret: SecretStr = Field(alias="DATABRICKS_RELEASE_PROBE_CLIENT_SECRET")
    account_client_secret: SecretStr = Field(alias="DATABRICKS_ACCOUNT_CLIENT_SECRET")
    ai_gateway_proof_signing_key: SecretStr = Field(alias="MIP_AI_GATEWAY_PROOF_SIGNING_KEY")
    verifier_client_secret: SecretStr | None = Field(default=None, alias="DATABRICKS_VERIFIER_CLIENT_SECRET")
    agent_runtime_client_secret: SecretStr | None = Field(default=None, alias="DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET")
    agent_proxy_credential_bundle: SecretStr | None = Field(default=None, alias="DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE")
    gateway_model_attestation_signing_key: SecretStr | None = Field(default=None, alias="MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY")

    @classmethod
    def env_name(cls, attribute: str) -> str:
        alias = cls.model_fields[attribute].alias
        assert alias is not None
        return alias

    # --- field rules -------------------------------------------------------

    @field_validator(*_SCALAR_SHAPES)
    @classmethod
    def _scalar_shape(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        assert info.field_name is not None
        shape, expected = _SCALAR_SHAPES[info.field_name]
        return require(value, shape, expected=expected)

    @field_validator("app_env")
    @classmethod
    def _app_env(cls, value: str) -> str:
        if is_placeholder(value) or value in {"sandbox", "prod"}:
            return value
        if value in {"local", "test"}:
            raise ValueError("must not be local or test, which enable local-only fallbacks; use sandbox or prod")
        raise ValueError("must be sandbox or prod")

    @field_validator("review_mode")
    @classmethod
    def _review_mode(cls, value: str) -> str:
        if is_placeholder(value) or value == "single_maintainer_audited":
            return value
        if value == "independent":
            raise ValueError(
                "independent needs the owner-attestation layer, which is deferred; "
                "use single_maintainer_audited"
            )
        raise ValueError("must be single_maintainer_audited")

    @field_validator("demo_first_party_feeds", "presenter_mode")
    @classmethod
    def _flag(cls, value: str | None) -> str | None:
        if value is None or is_placeholder(value):
            return value
        folded = value.casefold()
        if folded in _TRUE | _FALSE:
            return "1" if folded in _TRUE else "0"
        raise ValueError("must be one of 0/1/true/false/yes/no/on/off")

    @field_validator("databricks_host", "account_host")
    @classmethod
    def _origin(cls, value: str | None) -> str | None:
        if value is None or is_placeholder(value):
            return value
        return f"https://{https_host(value)}"

    @field_validator("metastore_owner")
    @classmethod
    def _metastore_owner(cls, value: str) -> str:
        require(value, PRINCIPAL, expected="one principal")
        if is_vocabulary(value):
            raise ValueError("must be an exact principal, never users, account users or admins")
        return value

    @field_validator(*_LISTS, mode="before")
    @classmethod
    def _split(cls, value: object) -> object:
        return split_list(value) if isinstance(value, str) else value

    @field_validator(*_EMAIL_LISTS, "scheduled_refresh_recipients")
    @classmethod
    def _emails(cls, values: tuple[str, ...], info: ValidationInfo) -> tuple[str, ...]:
        first_of: dict[str, int] = {}
        for index, value in enumerate(values):
            if info.field_name == "scheduled_refresh_recipients" and UUID.fullmatch(value):
                raise ValueError(
                    f"entry {index} is an application id; recipients must be email addresses"
                )
            require(value, EMAIL, expected="comma-separated email addresses")
            # The postflight compares the live job's recipients case-insensitively
            # (verify_scheduled_refresh_state), so the written set must be too.
            first = first_of.setdefault(value.casefold(), index)
            if info.field_name == "scheduled_refresh_recipients" and first != index:
                raise ValueError(f"entry {index} repeats entry {first} in another case; list each recipient once")
        return values

    @field_validator(*_IDENTITY_LISTS, *_PRINCIPAL_LISTS)
    @classmethod
    def _principals(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            require(value, PRINCIPAL, expected="comma-separated principals")
        return values

    @field_validator("identity_ids", "lender_identity", mode="before")
    @classmethod
    def _json_object(cls, value: object) -> object:
        return _parse_json(value, expected=dict)

    @field_validator("accepted_exposure", mode="before")
    @classmethod
    def _json_list(cls, value: object) -> object:
        return _parse_json(value, expected=list)

    # --- cross-field rules (tools/clients/rules.py) ---------------------------

    @model_validator(mode="after")
    def _rules(self, info: ValidationInfo) -> ClientProfile:
        allow_placeholders = bool((info.context or {}).get("allow_placeholders"))
        lines = rules.cross_field_refusals(self, allow_placeholders=allow_placeholders)
        if lines:
            raise ValueError("\n".join(lines))
        return self
