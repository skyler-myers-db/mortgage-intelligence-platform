/**
 * Wire contract, Admin domain (audit 2026-09-21 quality-04, D-api-types-a3 P1):
 * admin, audit, lineage and health (plus the config reads).
 *
 * Compile-time assertions only, checked by tsc through `include: ["src"]`;
 * nothing imports this file. (i) pairs every schema-named hand type with its
 * schema; (ii) pairs every bound call site whose type argument is not
 * schema-named with its operation. tests/unit/test_frontend_wire_contract.py
 * proves the coverage and the binding. A failing pair carries a dated
 * wire-drift entry tagged to the lane that fixes it at the cause.
 */
import type { ApiBody, ApiOk, ApiResponse } from './api.gen';
import type { DeepNoPhantomKeys, Expect, NoPhantomKeys, WireFits } from './wireContract.check';
import type { FootprintPayload } from '../components/FootprintProvider';
import type { HealthPayload as BannerHealthPayload } from '../components/mortgage/DegradedBanner';
import type { adminApi } from '../lib/apiClients/admin';
import type { AuditRollupResponse } from '../lib/apiClients/audit';
import type { RefusalReportFamilyCount, RefusalReportItem, RefusalReportListResponse, RefusalReportQuestionResponse } from '../lib/apiClients/refusalReports';
import type { ActorAuditEventPage, ActorAuditEventSummary, AuditEventPage, AuditEventRow, DecisionReceipt, HealthPayload as LibHealthPayload } from '../lib/apiTypes';
import type { AssetColumn, AssetLineageNode, AssetMetadataResponse, AssetProperty, AssetTag, DataEstateAsset, DataEstateLane, DataEstateResponse } from '../types';
import type { ConfigOptions } from './geo';
import type { LineageManifestResponse } from './lineage';

export type WireContractAdmin = [
  // (i) schema-named hand types
  // @ts-expect-error wire-drift quality-04 2026-10-01 w5-wire-types: fail-closed field event_type is string | null on the wire but string in the hand row type
  Expect<WireFits<ApiResponse<'AuditRollupResponse'>, AuditRollupResponse>>,
  Expect<NoPhantomKeys<AuditRollupResponse, ApiResponse<'AuditRollupResponse'>>>,
  Expect<WireFits<ApiResponse<'ActorAuditEventPage'>, ActorAuditEventPage>>,
  Expect<NoPhantomKeys<ActorAuditEventPage, ApiResponse<'ActorAuditEventPage'>>>,
  Expect<WireFits<ApiResponse<'ActorAuditEventSummary'>, ActorAuditEventSummary>>,
  Expect<NoPhantomKeys<ActorAuditEventSummary, ApiResponse<'ActorAuditEventSummary'>>>,
  Expect<WireFits<ApiResponse<'AuditEventPage'>, AuditEventPage>>,
  Expect<NoPhantomKeys<AuditEventPage, ApiResponse<'AuditEventPage'>>>,
  Expect<WireFits<ApiResponse<'DecisionReceipt'>, DecisionReceipt>>,
  Expect<NoPhantomKeys<DecisionReceipt, ApiResponse<'DecisionReceipt'>>>,
  Expect<WireFits<ApiResponse<'RefusalReportFamilyCount'>, RefusalReportFamilyCount>>,
  Expect<NoPhantomKeys<RefusalReportFamilyCount, ApiResponse<'RefusalReportFamilyCount'>>>,
  Expect<WireFits<ApiResponse<'RefusalReportItem'>, RefusalReportItem>>,
  Expect<NoPhantomKeys<RefusalReportItem, ApiResponse<'RefusalReportItem'>>>,
  Expect<WireFits<ApiResponse<'RefusalReportListResponse'>, RefusalReportListResponse>>,
  Expect<NoPhantomKeys<RefusalReportListResponse, ApiResponse<'RefusalReportListResponse'>>>,
  Expect<WireFits<ApiResponse<'RefusalReportQuestionResponse'>, RefusalReportQuestionResponse>>,
  Expect<NoPhantomKeys<RefusalReportQuestionResponse, ApiResponse<'RefusalReportQuestionResponse'>>>,
  Expect<WireFits<ApiResponse<'AssetColumn'>, AssetColumn>>,
  Expect<NoPhantomKeys<AssetColumn, ApiResponse<'AssetColumn'>>>,
  Expect<WireFits<ApiResponse<'AssetLineageNode'>, AssetLineageNode>>,
  Expect<NoPhantomKeys<AssetLineageNode, ApiResponse<'AssetLineageNode'>>>,
  Expect<WireFits<ApiResponse<'AssetMetadataResponse'>, AssetMetadataResponse>>,
  Expect<NoPhantomKeys<AssetMetadataResponse, ApiResponse<'AssetMetadataResponse'>>>,
  Expect<WireFits<ApiResponse<'AssetProperty'>, AssetProperty>>,
  Expect<NoPhantomKeys<AssetProperty, ApiResponse<'AssetProperty'>>>,
  Expect<WireFits<ApiResponse<'AssetTag'>, AssetTag>>,
  Expect<NoPhantomKeys<AssetTag, ApiResponse<'AssetTag'>>>,
  Expect<WireFits<ApiResponse<'DataEstateAsset'>, DataEstateAsset>>,
  Expect<NoPhantomKeys<DataEstateAsset, ApiResponse<'DataEstateAsset'>>>,
  Expect<WireFits<ApiResponse<'DataEstateLane'>, DataEstateLane>>,
  Expect<NoPhantomKeys<DataEstateLane, ApiResponse<'DataEstateLane'>>>,
  Expect<WireFits<ApiResponse<'DataEstateResponse'>, DataEstateResponse>>,
  Expect<NoPhantomKeys<DataEstateResponse, ApiResponse<'DataEstateResponse'>>>,
  Expect<WireFits<ApiResponse<'LineageManifestResponse'>, LineageManifestResponse>>,
  Expect<NoPhantomKeys<LineageManifestResponse, ApiResponse<'LineageManifestResponse'>>>,
  // (ii) bound call sites whose type argument is not schema-named
  Expect<WireFits<ApiOk<'GET /api/v1/config/options'>, ConfigOptions>>,
  Expect<NoPhantomKeys<ConfigOptions, ApiOk<'GET /api/v1/config/options'>>>,
  Expect<WireFits<ApiOk<'GET /api/v1/config/footprint'>, FootprintPayload>>,
  Expect<NoPhantomKeys<FootprintPayload, ApiOk<'GET /api/v1/config/footprint'>>>,
  Expect<WireFits<ApiResponse<'AuditEvent'>, AuditEventRow>>,
  Expect<NoPhantomKeys<AuditEventRow, ApiResponse<'AuditEvent'>>>,
  // @ts-expect-error wire-drift quality-04 2026-10-01 w5-wire-types: campaign_treatment_runtime is string | null on the wire but string in lib/apiTypes HealthPayload
  Expect<WireFits<ApiResponse<'HealthResponse'>, LibHealthPayload>>,
  // @ts-expect-error wire-drift quality-04 2026-10-01 w5-wire-types: lib/apiTypes HealthPayload declares warehouse_id and app_env, which HealthResponse does not
  Expect<NoPhantomKeys<LibHealthPayload, ApiResponse<'HealthResponse'>>>,
  // @ts-expect-error wire-drift quality-04 2026-10-01 w5-wire-types: status, dependencies and circuit_breakers values are plain strings on the wire but literal unions in DegradedBanner HealthPayload
  Expect<WireFits<ApiResponse<'HealthResponse'>, BannerHealthPayload>>,
  Expect<NoPhantomKeys<BannerHealthPayload, ApiResponse<'HealthResponse'>>>,
  // the adminRunOperation egress check (the site stays BINDING_EXEMPT)
  // @ts-expect-error wire-drift quality-04 2026-10-01 w5-wire-types: job_key and reason are plain strings in the adminRunOperation payload but closed literal unions in AdminOperationRunRequest
  Expect<WireFits<Parameters<typeof adminApi['adminRunOperation']>[0], ApiBody<'POST /api/v1/admin/operations/run'>>>,
  Expect<DeepNoPhantomKeys<Parameters<typeof adminApi['adminRunOperation']>[0], ApiBody<'POST /api/v1/admin/operations/run'>>>,
];
