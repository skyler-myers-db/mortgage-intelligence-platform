/**
 * Response and query-option contracts for the api client.
 *
 * These are the shapes the FastAPI backend returns (or the option bags the
 * client accepts) that are specific to the client layer; the domain models
 * they compose live in `../types`. Split out of `api.ts` verbatim so the
 * client file stays reviewable — `api.ts` re-exports every name here, which
 * is still the only import path consumers use.
 */
import type {
  CallDisposition,
  LeadSummary,
  LeadAssignment,
  SegmentCode,
  GenieActionSuggestion,
} from '../types';

export interface HealthPayload {
  status: string;
  mode: string;
  warehouse_id?: string | null;
  app_env?: string;
  dependencies?: Record<string, string>;
  circuit_breakers?: Record<string, string>;
  actor_cache_key?: string | null;
  /**
   * Databricks workspace origin (e.g. "https://dbc-x.cloud.databricks.com").
   * Present on the authenticated health body only; absent on the anonymous
   * liveness body and on older backends. Consumed by `useWorkspaceHost()` to
   * build Catalog Explorer deep links for Genie source assets — every caller
   * degrades to plain text when it is missing.
   */
  workspace_host?: string | null;
  /**
   * Deploy-promotion marker state: "enabled" after a governed
   * scripts/deploy.sh promotion, "disabled_baseline_deploy" when the
   * running process came from a bare deploy (treatment writes 503 and
   * status reports degraded). Absent on the anonymous liveness body.
   */
  campaign_treatment_runtime?: string;
  forced_degraded?: {
    active: boolean;
    dependency: string;
    source: string;
    expires_in_s: number;
  } | null;
}

export interface ApproveResult {
  approved: boolean;
  approval_id?: string | null;
  audit_event_id?: string | null;
  /** Loan officer this outreach was assigned to (echoed from the request). */
  assigned_to_email?: string | null;
  /** Follow-up reminder timestamp (ISO), computed from follow_up_in_days. */
  follow_up_at?: string | null;
  draft_generation_id?: string | null;
  draft_edited?: boolean | null;
}

export interface RejectResult {
  rejected: boolean;
  approval_id?: string | null;
  audit_event_id?: string | null;
}

export interface OutreachDraftResult {
  generation_id: string;
  response_hash: string;
  source_refreshed_at: string;
  borrower_id: string;
  campaign_id?: string | null;
  variant_name?: string | null;
  offer_code: string;
  channel: 'email' | 'sms' | 'direct_mail';
  subject?: string | null;
  body: string;
  status: 'draft';
  disclosure_version: string;
  disclosure_state: string;
  marketing_eligible: boolean;
  generation_mode: 'supervisor' | 'governed_fallback';
  generator_label: string;
  strategy_summary: string;
  evidence_summary: string[];
  evidence_assets: string[];
}

export interface GenieResult {
  answer: string;
  source?: string;
  trusted_assets?: string[];
  conversation_id?: string;
  message_id?: string | null;
  elapsed_ms?: number | null;
  question_hash?: string | null;
  sql_query?: string | null;
  row_count?: number | null;
  proof?: Record<string, unknown> | null;
  visualization?: Record<string, unknown> | null;
  actions?: GenieActionSuggestion[];
  metric_value?: string | null;
  table_rows?: Record<string, unknown>[] | null;
  follow_up_questions?: string[];
  // Frozen Genie payload extensions (2026-07). All optional/empty-safe.
  native_visualization?: {
    attachment_id: string;
    query_attachment_id?: string | null;
    title?: string | null;
  } | null;
  reasoning_trace?: Array<{ kind: string; content: string }>;
  genie_status?: string | null;
}

export interface GenieFeedbackResult {
  accepted: boolean;
  audit_event_id?: string | null;
}

/** Async Genie lifecycle (2026-07): `/api/genie/message/submit` body. */
export interface GenieSubmitResult {
  completed: boolean;
  conversation_id?: string | null;
  message_id?: string | null;
  progress_token?: string | null;
  question_hash?: string | null;
  /** Full governed answer when the turn resolved deterministically. */
  response?: GenieResult | null;
}

/** Live progress for one in-flight Genie turn (server-owned vocabulary). */
export interface GenieLiveProgress {
  status: string;
  stage: string;
  stage_label: string;
  terminal: boolean;
  failed: boolean;
  reasoning_trace: Array<{ kind: string; content: string }>;
  sql_preview?: string | null;
  error_hint?: string | null;
}

export interface AuditEventRow {
  event_id: string;
  actor: string;
  action: string;
  entity_type: string;
  entity_id: string;
  payload_json: Record<string, unknown>;
  evidence_ids: string[];
  created_at: string;
  event_type?: string | null;
  subject_clip?: string | null;
  subject_segment?: string | null;
  request_id?: string | null;
  correlation_id?: string | null;
}

export interface AuditEventPage {
  items: AuditEventRow[];
  next_cursor: string | null;
}

export interface ActorAuditEventSummary {
  event_type: string;
  entity_type: string;
  subject_id: string | null;
  created_at: string;
}

export interface ActorAuditEventPage {
  items: ActorAuditEventSummary[];
  next_cursor: string | null;
}

/**
 * Segment multi-select semantics forwarded to /api/leads and geo rollups.
 * `any` = de-duplicated OR, `all` = AND intersection. Segment Intelligence
 * exposes both modes; Genie cohorts and drilldowns pass the mode explicitly
 * so the Lead Queue, map, and backend count the same cohort.
 */
export type SegmentFilterMode = 'any' | 'all';
export type LeadFunnelStage =
  | 'addressable'
  | 'in_the_money'
  | 'high_opportunity'
  | 'offer_recommended'
  | 'approved'
  | 'actioned';

export interface LeadQueryOptions {
  segmentCodes?: SegmentCode[];
  /** `all` means borrowers must match every code in `segmentCodes`. */
  segmentMode?: SegmentFilterMode;
  /** Exact native Analytics Lead Funnel drilldown stage. */
  funnelStage?: LeadFunnelStage | null;
  targetLenderRef?: string | null;
  cohortId?: string | null;
  portfolioCriteria?: GeoQueryCriteria;
  approvalStatus?: 'pending' | 'approved' | 'rejected' | 'hold' | 'any';
  outreachStatus?: 'none' | 'queued' | 'actioned' | 'sent' | 'bounced' | 'replied' | 'any';
  assignedTo?: string | null;
  agedDays?: number | null;
  limit?: number;
  growthAgentProof?: GrowthAgentCohortProof | null;
}

export interface GrowthAgentCohortProof {
  runId: string;
  actionableTotal: number;
  cohortFingerprint: string;
  snapshotId: string;
  toolResultHash: string;
  growthHandoff: string;
}

export interface GrowthAgentCohortVerification {
  status: 'verified';
  runId: string;
  total: number;
  cohortFingerprint: string;
  snapshotId: string;
}

export interface AnalyticsQueryOptions {
  states?: string[] | null;
  state?: string | null;
  segmentCodes?: SegmentCode[] | null;
  segmentMode?: SegmentFilterMode;
  lenderRelationship?: string | null;
  targetLenderRef?: string | null;
  signalTypes?: string[] | null;
  signalType?: string | null;
  days?: number | null;
}

export interface AssignmentResponse {
  assignment: LeadAssignment;
  audit_event_id?: string | null;
}

export interface DispositionResponse {
  disposition: CallDisposition;
  audit_event_id?: string | null;
}

export interface LeadsPageResult {
  leads: LeadSummary[];
  totalMatching: number | null;
  /** Ranked subset (score >= 50) of totalMatching on geo-filtered queries. */
  rankedMatching: number | null;
  returnedRows: number | null;
  truncatedAt: number | null;
  growthAgentVerification: GrowthAgentCohortVerification | null;
}

export type GeoQueryCriteria = Record<string, string | number | readonly string[] | null | undefined>;

/**
 * The ZIP-rollup geography key — exactly one, never both. `state` is the
 * live drill; `countyFips` is reserved for a future licensed county
 * dataset and returns [] against the current share.
 */
export type ZipRollupKey =
  | { state: string; countyFips?: never }
  | { countyFips: string; state?: never };

/**
 * S9 assigned-vs-unattended overlay. Mirrors
 * `backend/schemas/geo_overlay.py`: per-geography-unit lead / assigned /
 * unattended counts plus loan-officer coverage. The overlay is the
 * difference between the live lead queue population
 * (`mip.gold.borrower_360`, marketing_eligible) and active Lakebase
 * assignments (`mip_app.lead_assignments`, released_at IS NULL).
 */
export type GeoOverlayLevel = 'state' | 'county' | 'zip';

export interface GeoAssignmentOverlayUnit {
  /** USPS code (state) | 5-char FIPS (county) | 5-digit ZIP (zip). */
  unit_id: string;
  lead_count: number;
  assigned_count: number;
  unattended_count: number;
  covering_officer_count: number;
  covering_officers: string[];
}

export interface GeoAssignmentOverlayResponse {
  level: GeoOverlayLevel;
  state: string | null;
  county_fips: string | null;
  units: GeoAssignmentOverlayUnit[];
  total_leads: number;
  total_assigned: number;
  total_unattended: number;
  /** Honest copy for the legend — cite verbatim. */
  lead_definition: string;
}
