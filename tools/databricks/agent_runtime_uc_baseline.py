"""Reviewed Databricks platform and MIP runtime UC privilege baselines."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any
from weakref import ReferenceType, ref


@dataclass(frozen=True, order=True)
class CatalogBindingEvidence:
    """Exact control-plane evidence that a workspace cannot access a catalog."""

    catalog: str
    owner: str
    catalog_type: str
    isolation_mode: str
    bindings: tuple[tuple[str, str], ...]


_CONTROL_PLANE_PROOF_ISSUER = object()
_CONTROL_PLANE_PROOF_LOCK = Lock()
_CONTROL_PLANE_PROOF_REGISTRY: dict[
    int,
    tuple[
        ReferenceType[ControlPlaneForeignCatalogProof],
        tuple[
            str,
            str,
            str,
            str,
            frozenset[str],
            tuple[CatalogBindingEvidence, ...],
        ],
    ],
] = {}


@dataclass(frozen=True, init=False)
class ControlPlaneForeignCatalogProof:
    """Opaque in-process evidence from the authoritative foreign-catalog inventory."""

    application_id: str
    catalog: str
    metastore_id: str
    workspace_id: str
    grant_audited_catalogs: frozenset[str]
    binding_denied_catalogs: tuple[CatalogBindingEvidence, ...]
    _issuer: object

    @property
    def audited_catalogs(self) -> frozenset[str]:
        return self.grant_audited_catalogs | frozenset(
            item.catalog for item in self.binding_denied_catalogs
        )


@dataclass(frozen=True)
class ConsumedControlPlaneForeignCatalogProof:
    """Immutable one-use snapshot returned only by successful proof consumption."""

    application_id: str
    catalog: str
    metastore_id: str
    workspace_id: str
    grant_audited_catalogs: frozenset[str]
    binding_denied_catalogs: tuple[CatalogBindingEvidence, ...]

    @property
    def audited_catalogs(self) -> frozenset[str]:
        return self.grant_audited_catalogs | frozenset(
            item.catalog for item in self.binding_denied_catalogs
        )


def _control_plane_proof_snapshot(
    proof: ControlPlaneForeignCatalogProof,
) -> tuple[
    str,
    str,
    str,
    str,
    frozenset[str],
    tuple[CatalogBindingEvidence, ...],
]:
    return (
        proof.application_id,
        proof.catalog,
        proof.metastore_id,
        proof.workspace_id,
        proof.grant_audited_catalogs,
        proof.binding_denied_catalogs,
    )


def _issue_control_plane_foreign_catalog_proof(
    *,
    application_id: str,
    catalog: str,
    metastore_id: str,
    workspace_id: str,
    grant_audited_catalogs: frozenset[str],
    binding_denied_catalogs: tuple[CatalogBindingEvidence, ...],
) -> ControlPlaneForeignCatalogProof:
    proof = object.__new__(ControlPlaneForeignCatalogProof)
    for name, value in (
        ("application_id", application_id),
        ("catalog", catalog),
        ("metastore_id", metastore_id),
        ("workspace_id", workspace_id),
        ("grant_audited_catalogs", grant_audited_catalogs),
        ("binding_denied_catalogs", binding_denied_catalogs),
        ("_issuer", _CONTROL_PLANE_PROOF_ISSUER),
    ):
        object.__setattr__(proof, name, value)
    proof_id = id(proof)

    def retire(reference: ReferenceType[ControlPlaneForeignCatalogProof]) -> None:
        with _CONTROL_PLANE_PROOF_LOCK:
            registered = _CONTROL_PLANE_PROOF_REGISTRY.get(proof_id)
            if registered is not None and registered[0] is reference:
                _CONTROL_PLANE_PROOF_REGISTRY.pop(proof_id, None)

    reference = ref(proof, retire)
    with _CONTROL_PLANE_PROOF_LOCK:
        _CONTROL_PLANE_PROOF_REGISTRY[proof_id] = (
            reference,
            _control_plane_proof_snapshot(proof),
        )
    return proof


def consume_issued_control_plane_foreign_catalog_proof(
    proof: ControlPlaneForeignCatalogProof,
) -> ConsumedControlPlaneForeignCatalogProof:
    if not isinstance(proof, ControlPlaneForeignCatalogProof):
        raise RuntimeError("foreign-catalog control-plane proof was not issued by the auditor")
    with _CONTROL_PLANE_PROOF_LOCK:
        registered = _CONTROL_PLANE_PROOF_REGISTRY.get(id(proof))
        if (
            registered is None
            or registered[0]() is not proof
            or registered[1] != _control_plane_proof_snapshot(proof)
            or getattr(proof, "_issuer", None) is not _CONTROL_PLANE_PROOF_ISSUER
        ):
            raise RuntimeError("foreign-catalog control-plane proof was not issued by the auditor")
        _CONTROL_PLANE_PROOF_REGISTRY.pop(id(proof), None)
        snapshot = registered[1]
    return ConsumedControlPlaneForeignCatalogProof(*snapshot)


def authoritative_workspace_id(workspace: Any) -> str:
    """Resolve the host-backed workspace ID and reject conflicting client config."""

    workspace_id = str(workspace.get_workspace_id() or "").strip()
    if not workspace_id:
        raise RuntimeError("UC boundary found no authoritative workspace identity")
    configured = str(
        getattr(getattr(workspace, "config", None), "workspace_id", None) or ""
    ).strip()
    if configured and configured != workspace_id:
        raise RuntimeError(
            "configured workspace ID does not match the authenticated workspace host"
        )
    return workspace_id


ALLOWED_FUNCTIONS = frozenset(
    {
        "fn_build_cohort",
        "fn_segment_counts",
        "fn_lead_queue_url",
    }
)
ALLOWED_METASTORE_BASELINE = frozenset({"USE_MARKETPLACE_ASSETS"})
_MAX_INVENTORY_WORKERS = 8
_SYSTEM_SCHEMA_PRIVILEGES = {
    "ai": {"EXECUTE", "READ_VOLUME", "SELECT", "USE_SCHEMA"},
    "data_quality_monitoring": {"USE_SCHEMA"},
    "information_schema": {"USE_SCHEMA"},
}
_SYSTEM_AI_FUNCTIONS = frozenset({"ai_classify", "ai_extract", "ai_parse_document", "python_exec"})
_SYSTEM_INFORMATION_SCHEMA_TABLES = frozenset(
    {
        "abac_policy_definitions",
        "catalog_privileges",
        "catalog_provider_share_usage",
        "catalog_tags",
        "catalogs",
        "check_constraints",
        "column_masks",
        "column_tags",
        "columns",
        "connections",
        "constraint_column_usage",
        "constraint_table_usage",
        "external_location_privileges",
        "external_locations",
        "information_schema_catalog_name",
        "key_column_usage",
        "metastore_privileges",
        "metastores",
        "parameters",
        "providers",
        "recipient_allowed_ip_ranges",
        "recipient_tokens",
        "recipients",
        "referential_constraints",
        "routine_columns",
        "routine_privileges",
        "routines",
        "row_filters",
        "schema_privileges",
        "schema_share_usage",
        "schema_tags",
        "schemata",
        "share_recipient_privileges",
        "shares",
        "storage_credential_privileges",
        "storage_credentials",
        "table_constraints",
        "table_privileges",
        "table_share_usage",
        "table_tags",
        "tables",
        "views",
        "volume_privileges",
        "volume_tags",
        "volumes",
    }
)
_CATALOG_INFORMATION_SCHEMA_TABLES = frozenset(
    {
        "abac_policy_definitions",
        "catalog_privileges",
        "catalog_tags",
        "catalogs",
        "check_constraints",
        "column_masks",
        "column_tags",
        "columns",
        "constraint_column_usage",
        "constraint_table_usage",
        "information_schema_catalog_name",
        "key_column_usage",
        "parameters",
        "referential_constraints",
        "routine_columns",
        "routine_privileges",
        "routines",
        "row_filters",
        "schema_privileges",
        "schema_tags",
        "schemata",
        "table_constraints",
        "table_privileges",
        "table_tags",
        "tables",
        "views",
        "volume_privileges",
        "volume_tags",
        "volumes",
    }
)
PrivilegeSource = tuple[str, str, str]
_ACCOUNT_USERS_DIRECT: set[PrivilegeSource] = {("account users", "", "")}
_SAMPLES_INHERITED: set[PrivilegeSource] = {("account users", "CATALOG", "samples")}
_SYSTEM_AI_INHERITED: set[PrivilegeSource] = {("account users", "SCHEMA", "system.ai")}
_SYSTEM_AI_MODELS = frozenset(
    {
        "system.ai.bge_base_en_v1_5",
        "system.ai.bge_large_en_v1_5",
        "system.ai.bge_m3",
        "system.ai.bge_small_en_v1_5",
        "system.ai.chronos-t5-large",
        "system.ai.databricks-claude-3-7-sonnet",
        "system.ai.databricks-claude-fable-5",
        "system.ai.databricks-claude-haiku-4-5",
        "system.ai.databricks-claude-opus-4-1",
        "system.ai.databricks-claude-opus-4-5",
        "system.ai.databricks-claude-opus-4-6",
        "system.ai.databricks-claude-opus-4-7",
        "system.ai.databricks-claude-opus-4-8",
        "system.ai.databricks-claude-sonnet-4",
        "system.ai.databricks-claude-sonnet-4-5",
        "system.ai.databricks-claude-sonnet-4-6",
        "system.ai.databricks-claude-sonnet-5",
        "system.ai.databricks-gemini-2-5-flash",
        "system.ai.databricks-gemini-2-5-pro",
        "system.ai.databricks-gemini-3-1-flash-image",
        "system.ai.databricks-gemini-3-1-flash-lite",
        "system.ai.databricks-gemini-3-1-pro",
        "system.ai.databricks-gemini-3-5-flash",
        "system.ai.databricks-gemini-3-5-flash-lite",
        "system.ai.databricks-gemini-3-6-flash",
        "system.ai.databricks-gemini-3-flash",
        "system.ai.databricks-gemini-3-pro",
        "system.ai.databricks-gemini-3-pro-image",
        "system.ai.databricks-glm-5-2",
        "system.ai.databricks-gpt-5",
        "system.ai.databricks-gpt-5-1",
        "system.ai.databricks-gpt-5-1-codex-max",
        "system.ai.databricks-gpt-5-1-codex-mini",
        "system.ai.databricks-gpt-5-2",
        "system.ai.databricks-gpt-5-2-codex",
        "system.ai.databricks-gpt-5-3-codex",
        "system.ai.databricks-gpt-5-4",
        "system.ai.databricks-gpt-5-4-mini",
        "system.ai.databricks-gpt-5-4-nano",
        "system.ai.databricks-gpt-5-5",
        "system.ai.databricks-gpt-5-5-pro",
        "system.ai.databricks-gpt-5-6-luna",
        "system.ai.databricks-gpt-5-6-sol",
        "system.ai.databricks-gpt-5-6-terra",
        "system.ai.databricks-gpt-5-mini",
        "system.ai.databricks-gpt-5-nano",
        "system.ai.databricks-inkling",
        "system.ai.databricks-kimi-k2-6",
        "system.ai.databricks-kimi-k2-7-code",
        "system.ai.databricks-qwen35-122b-a10b",
        "system.ai.dbrx_base",
        "system.ai.dbrx_instruct",
        "system.ai.gemma-3-12b-it",
        "system.ai.gpt-oss-120b",
        "system.ai.gpt-oss-20b",
        "system.ai.gte_base_en_v1_5",
        "system.ai.gte_large_en_v1_5",
        "system.ai.llama-4-maverick",
        "system.ai.llama_v3_2_1b",
        "system.ai.llama_v3_2_1b_instruct",
        "system.ai.llama_v3_2_3b",
        "system.ai.llama_v3_2_3b_instruct",
        "system.ai.llama_v3_3_70b_instruct",
        "system.ai.llama_v3_flash_preview",
        "system.ai.meta_llama_3_70b",
        "system.ai.meta_llama_3_70b_instruct",
        "system.ai.meta_llama_3_8b",
        "system.ai.meta_llama_3_8b_instruct",
        "system.ai.meta_llama_v3_1_405b",
        "system.ai.meta_llama_v3_1_405b_instruct_fp8",
        "system.ai.meta_llama_v3_1_70b",
        "system.ai.meta_llama_v3_1_70b_instruct",
        "system.ai.meta_llama_v3_1_8b",
        "system.ai.meta_llama_v3_1_8b_instruct",
        "system.ai.mistral_7b_instruct_v0_1",
        "system.ai.mistral_7b_instruct_v0_2",
        "system.ai.mistral_7b_v0_1",
        "system.ai.mixtral_8x7b_instruct_v0_1",
        "system.ai.mixtral_8x7b_v0_1",
        "system.ai.qwen3-embedding-0-6b",
        "system.ai.qwen3-next-80b-a3b-instruct",
        "system.ai.whisper_large_v3",
    }
)
_SYSTEM_AI_MODELS_WITH_DIRECT_EXECUTE = frozenset(
    {
        "system.ai.bge_base_en_v1_5",
        "system.ai.bge_large_en_v1_5",
        "system.ai.bge_m3",
        "system.ai.bge_small_en_v1_5",
        "system.ai.dbrx_base",
        "system.ai.dbrx_instruct",
        "system.ai.mistral_7b_instruct_v0_1",
        "system.ai.mistral_7b_instruct_v0_2",
        "system.ai.mistral_7b_v0_1",
        "system.ai.mixtral_8x7b_instruct_v0_1",
        "system.ai.mixtral_8x7b_v0_1",
        "system.ai.whisper_large_v3",
    }
)
_SAMPLES_CATALOG_PRIVILEGES = {
    "EXECUTE",
    "READ_VOLUME",
    "SELECT",
    "USE_CATALOG",
    "USE_SCHEMA",
}
_SAMPLES_SCHEMA_PRIVILEGES = {"EXECUTE", "READ_VOLUME", "SELECT", "USE_SCHEMA"}
