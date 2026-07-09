export type SegmentCode = 'itm' | 'listed' | 'permit' | 'investor' | 'equity' | 'retention';
export type OfferType = 'refi' | 'heloc' | 'cash_out' | 'purchase' | 'retention' | 'recapture';
export type ApprovalStatus = 'pending' | 'approved' | 'rejected' | 'hold';
export type OutreachStatus = 'none' | 'queued' | 'actioned' | 'sent' | 'bounced' | 'replied';

export interface EvidenceEvent {
  evidence_id: string;
  source_product: string;
  source_table: string;
  signal_type: string;
  signal_value: string;
  display_text: string;
  confidence: number;
  timestamp: string;
}

export interface ProofEvidenceEvent {
  evidence_id: string;
  source_product: string;
  signal_type: string;
  signal_value: string;
  display_text: string;
  confidence: number;
  timestamp: string;
}

export interface SegmentSummary {
  code: SegmentCode;
  name: string;
  count: number;
  delta: string;
  avg_score: number;
  description: string;
  color: string;
}

export interface LeadSummary {
  borrower_id: string;
  display_name: string;
  city: string;
  state: string;
  zip: string;
  /** Display-safe Cotality property ref. Raw CLIP is masked at the API
   *  boundary by default; values are `clip_ref_*` or synthetic demo refs
   *  and match Borrower360.clip_id exactly. */
  clip: string;
  segment_codes: SegmentCode[];
  equity_estimate: number;
  rate_spread_bps: number;
  opportunity_score: number;
  confidence: number;
  /** Canonical lowercase code emitted by fn_next_best_offer. */
  recommended_offer_code?: string;
  recommended_offer: string;
  why_now: string;
  evidence_ids: string[];
  approval_status: ApprovalStatus;
  outreach_status?: OutreachStatus;
  approved_at?: string | null;
  outreach_at?: string | null;
  /** Secondary-filter fields (2026-04-23). Carried from gold.borrower_360
   *  through gold.lead_population so /segment-intelligence can run real
   *  client-side predicates against occupancy, owner-link, lien state,
   *  and purchase intent. All optional with safe defaults so older cached
   *  payloads still parse. `listed_for_sale` is live from Cotality MLS.
   *  `has_permit` remains a filed-building-permit flag and must not be
   *  inferred from propensity models; HELOC intent is carried separately. */
  is_owner_occupied?: boolean;
  is_investor?: boolean;
  is_current_customer?: boolean;
  is_former_customer?: boolean;
  is_competitor_lien?: boolean;
  related_property_count?: number;
  current_lien_balance?: number;
  second_pos_amount?: number;
  has_permit?: boolean;
  listed_for_sale?: boolean;
  listing_status_category?: string | null;
  listing_status_description?: string | null;
  listing_date?: string | null;
  listing_status_date?: string | null;
  listing_price?: number | null;
  listing_days_on_market?: number | null;
  listing_service?: string | null;
  heloc_propensity_score?: number | null;
  heloc_propensity_run_date?: string | null;
  has_heloc_propensity_trigger?: boolean;
  refi_propensity_score?: number | null;
  refi_propensity_run_date?: string | null;
  has_refi_propensity_trigger?: boolean;
  /** Public-demo-safe lender alias for the current open lien holder. */
  current_lender_ref?: string | null;
  marketing_eligible?: boolean;
  consent_status?: 'opt_in' | 'opt_out' | 'unknown';
  suppression_reason?: string | null;
  /** Owner-resolution caveats (S1.1). `owner_count` is occupied owner slots on
   *  the property (max 4). `has_unresolved_owner` rows are never
   *  marketing_eligible — gold stamps suppression_reason='unresolved_owner'.
   *  Display-only; suppression is enforced in the gold/backend layer. All
   *  optional with safe defaults so older cached payloads still parse. */
  owner_count?: number;
  has_unresolved_owner?: boolean;
  primary_owner_entity_type?: 'individual' | 'trust' | 'llc' | 'unresolved' | null;
  last_touch_at?: string | null;
  eligible_recontact_at?: string | null;
  assigned_to_email?: string | null;
  assigned_to_label?: string | null;
  assigned_at?: string | null;
  assignment_expires_at?: string | null;
  latest_disposition_outcome?: string | null;
  latest_disposition_at?: string | null;
  latest_callback_at?: string | null;
  aging_days?: number | null;
}

export interface SalesTeamMember {
  email: string;
  display_label: string;
  role: 'loan_officer' | 'sales_manager' | 'admin';
  region?: string | null;
  manager_email?: string | null;
  capacity_per_day: number;
  active: boolean;
}

export interface LeadAssignment {
  assignment_id: string;
  borrower_id: string;
  assigned_to_email: string;
  assigned_to_label?: string | null;
  assigned_by: string;
  assigned_at: string;
  expires_at?: string | null;
  released_at?: string | null;
  strategy: 'manual' | 'round_robin' | 'score_balanced';
}

export interface CallDisposition {
  disposition_id: string;
  borrower_id: string;
  lo_email: string;
  outcome:
    | 'called_no_answer'
    | 'called_left_voicemail'
    | 'connected'
    | 'callback_scheduled'
    | 'application_started'
    | 'not_interested'
    | 'not_now'
    | 'dead';
  attempt_number: number;
  occurred_at: string;
  callback_at?: string | null;
  notes?: string | null;
  audit_event_id?: string | null;
}

export interface BorrowerLifecycle {
  borrower_id: string;
  approval_status: ApprovalStatus;
  outreach_status: OutreachStatus;
  approval_id?: string | null;
  approved_at?: string | null;
  outreach_at?: string | null;
  synced_at?: string | null;
  assignment?: LeadAssignment | null;
  latest_disposition?: CallDisposition | null;
}

export interface SalesAgingLead {
  borrower_id: string;
  approval_status: 'approved';
  approved_at: string;
  age_days: number;
  outreach_status: OutreachStatus;
  outreach_at?: string | null;
  assigned_to_email?: string | null;
  latest_disposition_outcome?: string | null;
  latest_disposition_at?: string | null;
}

export interface SalesStandupResponse {
  date: string;
  calls_logged: number;
  contacts_reached: number;
  callbacks_scheduled: number;
  applications_started: number;
  dead_leads: number;
  by_lo: Array<{
    lo_email: string;
    outcomes: Record<string, number>;
    calls_logged: number;
  }>;
}

export interface SalesConversionResponse {
  from_date: string;
  to_date: string;
  group_by: 'lo' | 'cohort';
  rows: Array<{
    group_key: string;
    calls_attempted: number;
    contacts_reached: number;
    callbacks_scheduled: number;
    applications_started: number;
    application_start_rate: number;
  }>;
}

export interface SalesOutcomeSummaryResponse {
  from_date: string;
  to_date: string;
  total_outcomes: number;
  applications_submitted: number;
  closed_funded: number;
  lost_to_competitor: number;
  withdrawn: number;
  not_qualified: number;
  by_source_system: Array<{
    source_system: string;
    outcomes: Record<string, number>;
    total: number;
  }>;
  source_statuses: Array<{
    source_system: string;
    display_name: string;
    status: string;
    configured: boolean;
    outcome_count: number;
  }>;
  by_lo: Array<{
    lo_email: string;
    outcomes: Record<string, number>;
    total: number;
  }>;
  top_competitors: Array<{
    competitor_lender_label: string;
    lost_to_competitor: number;
  }>;
}

export interface SavedLead {
  borrower_id: string;
  city?: string | null;
  state?: string | null;
  zip?: string | null;
  recommended_offer?: string | null;
  opportunity_score?: number | null;
  confidence?: number | null;
  saved_at: string;
  updated_at: string;
}

export type SavedLeadInput = Omit<SavedLead, 'saved_at' | 'updated_at'>;

export interface SavedDraft {
  borrower_id: string;
  offer_code?: string | null;
  channel: 'email' | 'sms' | 'direct_mail';
  body: string;
  saved_at: string;
  updated_at: string;
}

export type SavedDraftInput = Omit<SavedDraft, 'saved_at' | 'updated_at'>;

export interface WorkspaceState {
  saved_leads: SavedLead[];
  saved_drafts: SavedDraft[];
}

export interface WorkspaceMutationResult {
  ok: boolean;
  borrower_id: string;
  audit_event_id?: string | null;
}

export type ActivationDestinationType = 'salesforce' | 'crm_cdp' | 'los_pos' | 'servicing' | 'webhook';
export type ActivationDestinationStatus = 'not_configured' | 'dry_run' | 'connected' | 'disabled';
export type ActivationOutboxStatus = 'dry_run' | 'staged' | 'delivered' | 'failed' | 'cancelled';

export interface ActivationDestination {
  destination_key: string;
  destination_type: ActivationDestinationType;
  display_name: string;
  status: ActivationDestinationStatus;
  allowed_actions: string[];
  updated_at?: string | null;
}

export interface ActivationOutboxItem {
  activation_id: string;
  destination_key: string;
  destination_type: ActivationDestinationType;
  destination_display_name: string;
  destination_status: ActivationDestinationStatus;
  entity_type: 'borrower' | 'campaign' | 'cohort';
  entity_id: string;
  borrower_id: string;
  campaign_id?: string | null;
  approval_id: string;
  offer_code?: string | null;
  channel?: 'email' | 'sms' | 'direct_mail' | null;
  status: ActivationOutboxStatus;
  request_id: string;
  created_by: string;
  created_at: string;
  updated_at: string;
  /** Salesforce delivery outcome (Feature B): { delivered, salesforce_id?,
   *  reason?/error? }. Present once a delivery is attempted; absent = staged only. */
  delivery_metadata?: Record<string, unknown> | null;
}

export interface ActivationSummary {
  destinations: ActivationDestination[];
  recent_outbox: ActivationOutboxItem[];
}

export interface ActivationStageResponse {
  staged: boolean;
  activation: ActivationOutboxItem;
  audit_event_id?: string | null;
}

/** Business-friendly label for a UC source (2026-04-22). `name` is the
 *  raw UC object name (drives drawer lineage); `display_label` is the
 *  human-readable chip text (e.g. "In-the-money rule"). */
export interface SourceLabel {
  name: string;
  display_label: string;
}

export interface WhyPanel {
  rate_spread_bps: number;
  market_rate: number;
  equity_pct: number;
  in_the_money: boolean;
  in_the_money_reason: string;
  min_spread_bps: number;
  min_equity_pct: number;
  sources: string[];
  /** Index-aligned with `sources`. Added 2026-04-22. */
  source_labels?: SourceLabel[];
}

export interface Borrower360 extends LeadSummary {
  clip_id: string;
  owner_link_id: string;
  subject_property: string;
  avm_value: number;
  current_lien_balance: number;
  current_rate: number;
  ltv: number;
  related_property_count: number;
  situs_cbsa_code?: string | null;
  first_pos_loan_type?: string | null;
  is_absentee?: boolean;
  is_corporate_owner?: boolean;
  has_first_party_relationship?: boolean;
  first_party_relationship_depth?: number;
  first_party_recent_interactions?: number;
  first_party_recent_application?: boolean;
  first_party_synthetic_demo?: boolean;
  trigger_timeline: EvidenceEvent[];
  evidence_events: EvidenceEvent[];
  why_panel: WhyPanel;
}

export interface ProofFormulaLine {
  label: string;
  expression: string;
  result: string;
  source?: string | null;
}

export type ProofScoreComponentKey =
  | 'economic_incentive'
  | 'intent_trigger'
  | 'fit'
  | 'relationship'
  | 'evidence';

export interface ProofScoreComponent {
  key: ProofScoreComponentKey;
  label: string;
  value: number;
  weight: number;
  weighted_points: number;
  explanation: string;
  source_fields: string[];
  fair_lending_note?: string | null;
}

export interface ProofOfferBranch {
  code: string;
  label: string;
  passed: boolean;
  selected: boolean;
  reason: string;
}

export interface ProofReproduceQuery {
  title: string;
  sql: string;
  sql_hash: string;
  note: string;
  databricks_sql_url?: string | null;
}

export interface BorrowerProof {
  borrower_id: string;
  trusted: boolean;
  known_data_gaps: string[];
  generated_from: string;
  source_refresh_at?: string | null;
  opportunity_score: number;
  signal_strength: number;
  signal_strength_note: string;
  evidence_confidence_note: string;
  score_components: ProofScoreComponent[];
  score_formula: ProofFormulaLine;
  signal_strength_formula: ProofFormulaLine;
  rate_spread_formula: ProofFormulaLine;
  equity_formula: ProofFormulaLine;
  ltv_formula: ProofFormulaLine;
  offer_code: string;
  offer_label: string;
  offer_branches: ProofOfferBranch[];
  evidence_rows: ProofEvidenceEvent[];
  source_assets: string[];
  reproduce: ProofReproduceQuery[];
}

export interface KpiTrend {
  series: number[];
  delta_pct: number | null;
  direction: 'up' | 'down' | 'flat';
  comparison_label?: string | null;
  note?: string | null;
}

export interface PortfolioPreview {
  marketable_population: number;
  high_intent_leads: number;
  top_tier_opportunities: number | null;
  offers_recommended: number | null;
  avg_score: number | null;
  data_refreshed_at: string | null; // ISO timestamp
  trends?: Record<string, KpiTrend>;
  trend_status?: 'live' | 'not_applicable' | 'unavailable' | 'empty' | string;
  trend_note?: string | null;
  // R5-20: server-authoritative day-zero flag. Optional so older servers
  // that don't emit it still parse; consumers must fall back to
  // `marketable_population === 0 && data_refreshed_at === null` when
  // absent.
  day_zero?: boolean;
  approved_count: number | null;
  in_outreach_count: number | null;
}

export interface PortfolioCreateResponse {
  portfolio_id: string;
  campaign_id?: string | null;
  name: string;
  marketable_population: number;
  audit_event_id?: string | null;
}

export interface CampaignSummary {
  campaign_id: string;
  name: string;
  owner_email: string;
  status: 'draft' | 'pending_review' | 'approved' | 'live' | 'active' | 'rejected' | 'archived';
  criteria: Record<string, unknown>;
  suppression_policy?: Record<string, unknown>;
  message_variants?: Record<string, unknown>[];
  channel_cascade?: Record<string, unknown>[];
  send_window?: Record<string, unknown>;
  holdout?: Record<string, unknown> | null;
  roi_assumptions?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface CampaignListResponse {
  campaigns: CampaignSummary[];
}

export interface OfferAlternative {
  offer_code: string;
  product_label: string;
  reason_not_chosen: string;
}

export interface OfferRecommendation {
  borrower_id: string;
  offer_code: string;
  offer_type: string;
  product_label: string;
  confidence: number;
  rationale: string;
  evidence_ids: string[];
  sources: string[];
  /** Index-aligned with `sources`. Added 2026-04-22 so chip text renders
   *  business-friendly labels (e.g. "In-the-money rule") instead of raw
   *  UC object names. Optional for back-compat with cached responses. */
  source_labels?: SourceLabel[];
  alternatives: OfferAlternative[];
  thresholds_applied: Record<string, number>;
}

export * from './types/genie';

export type {
  PropertyLoanLookupLoan,
  PropertyLoanLookupRequest,
  PropertyLoanLookupResponse,
} from './types/propertyLookup';

export type {
  GrowthAgentCadence,
  GrowthAgentCustomRunRequest,
  GrowthAgentDueMonitorRunRequest,
  ComposePlanRequest,
  ComposePlanResponse,
  ComposedPlan,
  PlanStep,
  PlanStepTrace,
  GrowthAgentDueMonitorRunResponse,
  GrowthAgentGovernanceChip,
  GrowthAgentHomeResponse,
  GrowthAgentMonitor,
  GrowthAgentMonitorDraftRequest,
  GrowthAgentNotificationChannel,
  GrowthAgentNotificationDraft,
  GrowthAgentPolicyCheck,
  GrowthAgentPromptRunRequest,
  GrowthAgentRunRequest,
  GrowthAgentRunResponse,
  GrowthAgentSegmentCode,
  GrowthAgentSegmentMode,
  GrowthAgentSpecialist,
  GrowthAgentToolStep,
  GrowthAgentWorkflow,
  GrowthAgentWorkflowId,
} from './types/growthAgent';

export type DataEstateStatus =
  | 'live'
  | 'demo_synthetic'
  | 'configured_empty'
  | 'not_configured'
  | 'roadmap'
  | 'permission_denied'
  | 'error';

export interface DataEstateAsset {
  name: string;
  label: string;
  status: DataEstateStatus;
  uc_object?: string | null;
  catalog_explorer_url?: string | null;
  row_count?: number | null;
  last_updated?: string | null;
  note: string;
  synthetic_demo?: boolean;
}

export interface DataEstateLane {
  id: string;
  title: string;
  description: string;
  status: DataEstateStatus;
  assets: DataEstateAsset[];
}

export interface DataEstateResponse {
  generated_at: string;
  lender_name: string;
  public_demo_masking: boolean;
  lanes: DataEstateLane[];
  known_data_gaps: string[];
  proof_assets: string[];
}

export type AssetFreshness = 'fresh' | 'aging' | 'stale' | 'unavailable';
export type AssetObjectType = 'table' | 'view' | 'function';
export type AssetLineageDirection = 'upstream' | 'downstream' | 'observed';

export interface AssetColumn {
  name: string;
  data_type?: string | null;
  comment?: string | null;
  ordinal_position?: number | null;
  redacted?: boolean;
}

export interface AssetTag {
  name: string;
  value?: string | null;
}

export interface AssetProperty {
  name: string;
  value?: string | null;
}

export interface AssetLineageNode {
  direction: AssetLineageDirection;
  asset_path: string;
  label: string;
  object_type?: string | null;
  event_time?: string | null;
  source: string;
}

export interface AssetMetadataResponse {
  asset_path: string;
  title: string;
  description: string;
  object_type: AssetObjectType;
  status: DataEstateStatus | 'unknown';
  freshness: AssetFreshness;
  catalog: string;
  schema_name: string;
  object_name: string;
  uc_object: string;
  generated_at: string;
  last_updated?: string | null;
  delta_last_modified?: string | null;
  row_count?: number | null;
  row_count_source: 'source_readiness' | 'delta_stats' | 'count' | 'unavailable';
  num_files?: number | null;
  size_in_bytes?: number | null;
  size_label?: string | null;
  catalog_explorer_url?: string | null;
  source_note?: string | null;
  checked_at?: string | null;
  tags: AssetTag[];
  properties: AssetProperty[];
  columns: AssetColumn[];
  ddl?: string | null;
  ddl_redacted_lines: number;
  lineage: AssetLineageNode[];
  known_data_gaps: string[];
}

export interface FunnelTotals {
  snapshot_date?: string | null;
  addressable_borrowers: number;
  in_the_money_borrowers: number;
  high_opportunity_borrowers: number;
  offer_recommended_borrowers: number;
  approved_borrowers: number;
  actioned_borrowers: number;
}

export interface FunnelStage {
  stage: string;
  stage_order: number;
  borrower_count: number;
}

export interface ScoreBucket {
  score_bucket: number;
  borrower_count: number;
}

export interface ExecutiveAnalyticsResponse {
  totals: FunnelTotals;
  stages: FunnelStage[];
  score_distribution: ScoreBucket[];
}

export interface StateOpportunityRow {
  state: string;
  borrower_count: number;
  mean_opportunity_score: number;
  in_the_money_borrowers: number;
}

export interface StateAvmValueRow {
  state: string;
  total_avm_value_usd: number;
  total_lien_balance_usd: number;
  total_equity_usd: number;
}

export interface TopZipOpportunityRow {
  state: string;
  zip: string;
  city?: string | null;
  borrower_count: number;
  in_the_money_borrowers: number;
  mean_opportunity_score: number;
  mean_rate_spread_bps: number;
}

export interface GeographyAnalyticsResponse {
  state_opportunities: StateOpportunityRow[];
  state_avm_values: StateAvmValueRow[];
  top_zips: TopZipOpportunityRow[];
}

export interface RateSpreadBucket {
  spread_bucket_bps: number;
  borrower_count: number;
}

export interface EquitySpreadPoint {
  borrower_id: string;
  display_name: string;
  segment: string;
  state: string;
  equity_pct: number;
  rate_spread_bps: number;
  opportunity_score: number;
}

export interface TopBorrowerAnalyticsRow {
  borrower_id: string;
  display_name: string;
  state: string;
  city?: string | null;
  opportunity_score: number;
  rate_spread_bps: number;
  equity_pct: number;
  recommended_offer: string;
  rank_overall: number;
}

export interface EconomicsAnalyticsResponse {
  rate_spread_histogram: RateSpreadBucket[];
  equity_vs_spread: EquitySpreadPoint[];
  top_borrowers: TopBorrowerAnalyticsRow[];
}

export interface SegmentOverviewRow {
  segment_code: SegmentCode;
  name: string;
  borrower_count: number;
  mean_opportunity_score: number;
  delta_vs_prior_label: string;
  description: string;
  approval_rate?: number | null;
  outreach_rate?: number | null;
  mean_rate_spread_bps?: number | null;
  mean_equity_pct?: number | null;
  in_the_money_borrowers: number;
}

export interface SegmentMetricRow {
  segment_code: SegmentCode;
  segment_name: string;
  value: number;
}

export interface SegmentByStateRow {
  state: string;
  segment_code: SegmentCode;
  segment_name: string;
  borrower_count: number;
}

export interface TopSegmentByStateRow extends SegmentByStateRow {
  state_rank: number;
}

export interface AnalyticsScope {
  code: string;
  label: string;
  description: string;
}

export interface SegmentAnalyticsResponse {
  scope: AnalyticsScope;
  overview: SegmentOverviewRow[];
  counts: SegmentMetricRow[];
  average_scores: SegmentMetricRow[];
  by_state: SegmentByStateRow[];
  top_segments_by_state: TopSegmentByStateRow[];
}

export interface EvidenceDailyRow {
  event_date: string;
  signal_type: string;
  event_count: number;
}

export interface EvidenceBySignalRow {
  signal_type: string;
  source_product: string;
  source_table: string;
  source_label?: string | null;
  event_count: number;
  mean_confidence?: number | null;
  confidence_source: string;
  confidence_label?: string | null;
}

export interface SignalEvidenceExample {
  borrower_id: string;
  display_name: string;
  state: string;
  signal_type: string;
  source_product: string;
  signal_value: string;
  display_text: string;
  confidence: number;
  timestamp: string;
}

export interface SignalAnalyticsResponse {
  evidence_daily: EvidenceDailyRow[];
  evidence_by_signal: EvidenceBySignalRow[];
  evidence_examples: SignalEvidenceExample[];
}

export type {
  ConfigOptions,
  CountyRollup,
  CountyRollupResponse,
  StateRollup,
  StateRollupResponse,
  ZipRollup,
  ZipRollupResponse,
} from './types/geo';
