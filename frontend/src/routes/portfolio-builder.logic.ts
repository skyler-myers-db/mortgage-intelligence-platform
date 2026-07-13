import type { CampaignSummary, KpiTrend, PortfolioPreview } from '../types';
import { isPublicLenderRef, LENDER_RELATIONSHIP_OPTIONS } from '../lib/lenderFilters';

export type FilterGroup = {
  label: string;
  key: string;
  options: string[];
};

export type FootprintState = {
  state_code: string;
  state_name: string;
};

// Non-GEO filter groups are tenant-invariant. The GEO group is built at
// render time from the FootprintProvider (see `buildGeoOptions` below) so
// the "All N states" label and per-state options reflect the tenant's
// real footprint rather than a source-code state literal.
// Keys MUST match PortfolioCriteria in backend/schemas/portfolio.py. The
// earlier mismatch (`geo` vs `geography`, `occ` vs `occupancy`, etc.) made
// every filter a no-op because Pydantic silently ignored unknown fields.
export const NON_GEO_FILTER_GROUPS: FilterGroup[] = [
  { label: 'OCCUPANCY',    key: 'occupancy',            options: ['Owner-occupied', 'Non-owner-occupied', 'All'] },
  { label: 'LIEN STATUS',  key: 'lien_status',          options: ['Open 1st lien', 'Open HELOC', 'Free & clear', 'Any'] },
  { label: 'RELATIONSHIP', key: 'lender_relationship',  options: [...LENDER_RELATIONSHIP_OPTIONS] },
  { label: 'OWNER LINK',   key: 'owner_link',           options: ['All', 'Single-property owner', 'Multi-property (2-4)', 'Portfolio investor (5+)'] },
  { label: 'PURCHASE INTENT', key: 'purchase_intent',   options: ['All', 'Listed for sale', 'HELOC intent', 'Both'] },
  { label: 'PRODUCT',      key: 'product',              options: ['All products', 'Refi', 'HELOC', 'Cash-out', 'Purchase', 'Retention'] },
  { label: 'EQUITY',       key: 'min_equity_pct_label', options: ['≥ 15%', '≥ 25%', '≥ 40%', 'Any'] },
  { label: 'CONTACTABILITY', key: 'marketing_eligibility', options: ['Eligible only', 'Any', 'Suppressed only'] },
  { label: 'CONSENT', key: 'consent_status', options: ['Any', 'Opt-in', 'Opt-out', 'Unknown'] },
  { label: 'RECENCY', key: 'recency', options: ['Any', 'Untouched 30d', 'Untouched 60d', 'Untouched 90d'] },
];

// Default filter values keyed by PortfolioCriteria field names. The
// backend rejects unknown fields by omission, so short aliases like
// `geo` / `occ` would silently turn the controls into no-ops.
export const BASE_DEFAULT_FILTERS: Record<string, string> = {
  occupancy: 'Owner-occupied',
  lien_status: 'Open 1st lien',
  lender_relationship: 'All',
  target_lender_ref: 'All',
  owner_link: 'All',
  purchase_intent: 'All',
  product: 'All products',
  min_equity_pct_label: '≥ 15%',
  marketing_eligibility: 'Eligible only',
  consent_status: 'Any',
  recency: 'Any',
};

const DEFAULT_CAMPAIGN_LENDER_NAME = 'configured lender';

/**
 * URL search-param keys we round-trip. One per filter + the reload
 * token so the "Run build" commit is reproducible from a deep link.
 * These match PortfolioCriteria so a copied URL replays the same
 * server-side predicates.
 */
export const URL_FILTER_KEYS = [
  'occupancy',
  'lien_status',
  'lender_relationship',
  'target_lender_ref',
  'owner_link',
  'purchase_intent',
  'product',
  'min_equity_pct_label',
  'marketing_eligibility',
  'consent_status',
  'recency',
] as const;

export type CampaignSetupState = {
  subjectA: string;
  subjectB: string;
  bodyA: string;
  bodyB: string;
  holdoutPct: string;
  startLocal: string;
  endLocal: string;
  budget: string;
  emailCost: string;
  smsCost: string;
  mailCost: string;
  marketHouseholdTogether: boolean;
  generationMode: 'supervisor' | 'reviewed_fallback' | 'operator';
  generatorLabel: string;
};

export type CampaignNumericField = 'holdoutPct' | 'budget' | 'emailCost' | 'smsCost' | 'mailCost';

export const CAMPAIGN_NUMERIC_BOUNDS: Record<CampaignNumericField, {
  min: number;
  max: number;
  fallback: number | null;
  step: number;
}> = {
  holdoutPct: { min: 0, max: 50, fallback: 10, step: 0.1 },
  budget: { min: 0, max: 10_000_000, fallback: null, step: 1 },
  emailCost: { min: 0, max: 1_000, fallback: null, step: 0.01 },
  smsCost: { min: 0, max: 1_000, fallback: null, step: 0.01 },
  mailCost: { min: 0, max: 1_000, fallback: null, step: 0.01 },
};

export function normalizeCampaignNumericValue(
  field: CampaignNumericField,
  raw: string,
): string {
  const bounds = CAMPAIGN_NUMERIC_BOUNDS[field];
  if (raw.trim() === '' && bounds.fallback === null) return '';
  const parsed = Number(raw);
  const value = Number.isFinite(parsed) ? parsed : bounds.fallback;
  if (value === null) return '';
  const bounded = Math.min(bounds.max, Math.max(bounds.min, value));
  return String(Number(bounded.toFixed(2)));
}

export function buildDefaultCampaignSetup(
  _lenderName: string = DEFAULT_CAMPAIGN_LENDER_NAME,
): CampaignSetupState {
  return {
    subjectA: '',
    subjectB: '',
    bodyA: '',
    bodyB: '',
    holdoutPct: '10',
    startLocal: '09:00',
    endLocal: '16:00',
    budget: '',
    emailCost: '',
    smsCost: '',
    mailCost: '',
    marketHouseholdTogether: false,
    generationMode: 'operator',
    generatorLabel: 'Operator edited',
  };
}

export const DEFAULT_CAMPAIGN_SETUP: CampaignSetupState = buildDefaultCampaignSetup();

/**
 * Build the GEO-dropdown options for the current tenant footprint.
 *
 * Emits (in order):
 *   - If live footprint metadata is not ready, "All" only. This prevents the
 *     generic fallback dictionary from appearing as tenant-selectable states.
 *   1. "All N states" — the whole-footprint option, where N is the live
 *      count (so a 4-state tenant sees "All 4 states", not "All 6").
 *   2. Each state by its backend-provided state_name (so TX is "Texas",
 *      CA is "California", etc.).
 */
export function buildGeoOptions(states: ReadonlyArray<FootprintState>): string[] {
  if (states.length === 0) return ['All'];
  const opts: string[] = [];
  opts.push(`All ${states.length} states`);
  for (const s of states) opts.push(s.state_name);
  return opts;
}

export function defaultGeographyForOptions(geoOptions: readonly string[]): string {
  return geoOptions.find((opt) => /^All \d+ states$/.test(opt)) ?? geoOptions[0] ?? 'All';
}

export function parseFiltersFromUrl(
  sp: URLSearchParams,
  defaults: Record<string, string>,
  allowedLenderRefs: readonly string[] = [],
): Record<string, string> {
  const out: Record<string, string> = { ...defaults };
  for (const k of URL_FILTER_KEYS) {
    const v = sp.get(k);
    if (v !== null && v.length > 0) {
      if (k === 'target_lender_ref' && !isPublicLenderRef(v, allowedLenderRefs)) {
        continue;
      }
      out[k] = v;
    }
  }
  return out;
}

export function buildUrlFromFilters(
  filters: Record<string, string>,
  defaults: Record<string, string>,
  stateCodes: readonly string[],
  allowedLenderRefs: readonly string[] = [],
): URLSearchParams {
  const sp = new URLSearchParams();
  if (stateCodes.length > 0) {
    sp.set('states', stateCodes.join(','));
  }
  for (const k of URL_FILTER_KEYS) {
    const v = filters[k];
    // Skip defaults so the URL stays compact and shareable — a user
    // who hasn't touched a filter won't have 6 redundant params in
    // their address bar.
    if (v !== undefined && v !== defaults[k]) {
      if (k === 'target_lender_ref' && !isPublicLenderRef(v, allowedLenderRefs)) {
        continue;
      }
      sp.set(k, v);
    }
  }
  return sp;
}

export function buildPreviewCriteria(
  filters: Record<string, string>,
  stateCodes: readonly string[],
): Record<string, unknown> {
  const criteria: Record<string, unknown> = { ...filters };
  if (stateCodes.length > 0) criteria.states = [...stateCodes];
  return criteria;
}

export function buildLeadQueueUrlFromFilters(
  filters: Record<string, string>,
  stateCodes: readonly string[],
  allowedLenderRefs: readonly string[] = [],
): string {
  const sp = new URLSearchParams();
  if (stateCodes.length > 0) {
    sp.set('states', stateCodes.join(','));
  }
  for (const k of URL_FILTER_KEYS) {
    const v = filters[k];
    if (v !== undefined && v.length > 0) {
      if (k === 'target_lender_ref' && !isPublicLenderRef(v, allowedLenderRefs)) {
        continue;
      }
      sp.set(k, v);
    }
  }
  const query = sp.toString();
  return query ? `/lead-queue?${query}` : '/lead-queue';
}

export function buildSegmentIntelligenceUrlFromFilters(
  filters: Record<string, string>,
  allowedLenderRefs: readonly string[] = [],
): string {
  const sp = new URLSearchParams();
  const relationship = filters.lender_relationship;
  if (relationship && relationship !== BASE_DEFAULT_FILTERS.lender_relationship) {
    sp.set('lender_relationship', relationship);
  }
  const target = filters.target_lender_ref;
  if (
    target
    && target !== BASE_DEFAULT_FILTERS.target_lender_ref
    && isPublicLenderRef(target, allowedLenderRefs)
  ) {
    sp.set('target_lender_ref', target);
  }
  for (const key of ['owner_link', 'purchase_intent'] as const) {
    const value = filters[key];
    if (value && value !== BASE_DEFAULT_FILTERS[key]) {
      sp.set(key, value);
    }
  }
  const query = sp.toString();
  return query ? `/segment-intelligence?${query}` : '/segment-intelligence';
}

export function campaignCriteriaSummary(campaign: CampaignSummary): string {
  const criteria = campaign.criteria ?? {};
  const parts: string[] = [];
  const states = Array.isArray(criteria.states) ? criteria.states.map(String).filter(Boolean) : [];
  if (states.length > 0) parts.push(states.join(', '));
  for (const key of ['lender_relationship', 'target_lender_ref', 'owner_link', 'purchase_intent', 'product', 'marketing_eligibility', 'consent_status', 'recency']) {
    const value = criteria[key];
    if (typeof value === 'string' && value && value !== 'All' && value !== 'Any') {
      parts.push(value);
    }
  }
  const policy = campaign.suppression_policy?.default;
  if (typeof policy === 'string' && policy) parts.push(policy.replace(/_/g, ' '));
  const holdoutPct = campaign.holdout?.size_pct;
  if (typeof holdoutPct === 'number') parts.push(`${holdoutPct}% holdout`);
  // Drop case-insensitive duplicates so the suppression policy "eligible only"
  // does not double-print the "Eligible only" marketing-eligibility criterion.
  const seen = new Set<string>();
  const deduped = parts.filter((part) => {
    const key = part.toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  return deduped.length > 0 ? deduped.join(' · ') : 'Eligible-only draft campaign';
}

export interface SavedCampaignRow {
  /** Representative campaign (most recent) — wins the row's key/links. */
  campaign: CampaignSummary;
  /** Number of same-name drafts collapsed into this row; 1 = not collapsed. */
  draftCount: number;
  latestAt: string | null;
}

/**
 * Collapse repeated same-name DRAFT campaigns into one representative row so a
 * long list of identical "Genie strategy draft" rows reads as one item with a
 * count. Non-draft or uniquely-named campaigns pass through 1:1. The most-recent
 * campaign (by updated_at/created_at) represents the group.
 */
export function groupSavedCampaigns(campaigns: CampaignSummary[]): SavedCampaignRow[] {
  const timeOf = (campaign: CampaignSummary): number => {
    const iso = campaign.updated_at ?? campaign.created_at;
    const parsed = iso ? Date.parse(iso) : NaN;
    return Number.isNaN(parsed) ? 0 : parsed;
  };
  const groups = new Map<string, CampaignSummary[]>();
  const order: string[] = [];
  for (const campaign of campaigns) {
    // Only DRAFT rows sharing a name collapse; everything else stays unique.
    const key = campaign.status === 'draft' ? `draft:${campaign.name}` : `one:${campaign.campaign_id}`;
    const bucket = groups.get(key);
    if (bucket) {
      bucket.push(campaign);
    } else {
      groups.set(key, [campaign]);
      order.push(key);
    }
  }
  return order.map((key) => {
    const members = groups.get(key)!;
    const representative = members.reduce(
      (latest, campaign) => (timeOf(campaign) >= timeOf(latest) ? campaign : latest),
      members[0],
    );
    return {
      campaign: representative,
      draftCount: members.length,
      latestAt: representative.updated_at ?? representative.created_at ?? null,
    };
  });
}

function boundedNumber(raw: string, fallback: number, min: number, max: number): number {
  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, parsed));
}

function nullableMoney(raw: string, max: number): number | null {
  const parsed = Number(raw);
  if (!Number.isFinite(parsed) || parsed <= 0) return null;
  return Number(Math.min(max, parsed).toFixed(2));
}

function configuredChannelCosts(setup: CampaignSetupState): Record<string, number> {
  const costs = {
    email: nullableMoney(setup.emailCost, CAMPAIGN_NUMERIC_BOUNDS.emailCost.max),
    sms: nullableMoney(setup.smsCost, CAMPAIGN_NUMERIC_BOUNDS.smsCost.max),
    direct_mail: nullableMoney(setup.mailCost, CAMPAIGN_NUMERIC_BOUNDS.mailCost.max),
  };
  return Object.fromEntries(
    Object.entries(costs).filter((entry): entry is [string, number] => entry[1] !== null),
  );
}

export function buildCampaignConfig(setup: CampaignSetupState): {
  suppression_policy: Record<string, unknown>;
  message_variants: Record<string, unknown>[];
  channel_cascade: Record<string, unknown>[];
  send_window: Record<string, unknown>;
  holdout: Record<string, unknown>;
  roi_assumptions: Record<string, unknown>;
  household_dedup: Record<string, unknown>;
} {
  const holdoutPct = boundedNumber(setup.holdoutPct, 10, 0, 50);
  return {
    suppression_policy: { default: 'eligible_only', frequency_cap_days: 30 },
    message_variants: [
      {
        variant_name: 'A',
        channel: 'email',
        subject: setup.subjectA.trim(),
        body: setup.bodyA.trim(),
        weight_pct: Math.max(0, Math.round((100 - holdoutPct) / 2)),
        generation_mode: setup.generationMode,
        generator_label: setup.generatorLabel,
      },
      {
        variant_name: 'B',
        channel: 'email',
        subject: setup.subjectB.trim(),
        body: setup.bodyB.trim(),
        weight_pct: Math.max(0, Math.floor((100 - holdoutPct) / 2)),
        generation_mode: setup.generationMode,
        generator_label: setup.generatorLabel,
      },
    ],
    channel_cascade: [
      { channel: 'email', step: 1 },
      { channel: 'sms', step: 2, after_days: 3 },
      { channel: 'direct_mail', step: 3, after_days: 10 },
    ],
    send_window: {
      days: ['Tuesday', 'Wednesday', 'Thursday'],
      timezone: 'borrower_local',
      start_local: setup.startLocal,
      end_local: setup.endLocal,
    },
    holdout: { method: 'hash_modulo', size_pct: holdoutPct },
    roi_assumptions: {
      budget_usd: nullableMoney(setup.budget, CAMPAIGN_NUMERIC_BOUNDS.budget.max),
      cost_per_contact_usd: configuredChannelCosts(setup),
      source: 'operator_configured',
    },
    household_dedup: {
      enabled: setup.marketHouseholdTogether,
      dedupe_unit: setup.marketHouseholdTogether ? 'household' : 'borrower',
      primary_contact_strategy: 'highest_opportunity_eligible',
    },
  };
}

export function parseStateCodesFromUrl(
  sp: URLSearchParams,
  states: ReadonlyArray<FootprintState>,
): string[] {
  const allowed = new Set(states.map((state) => state.state_code));
  const rawStates = sp.get('states');
  if (!rawStates) return [];
  const out: string[] = [];
  for (const raw of rawStates.split(',')) {
    const code = raw.trim().toUpperCase();
    if (!allowed.has(code) || out.includes(code)) continue;
    out.push(code);
  }
  return out.length === states.length ? [] : out;
}

export function stateLabel(code: string, states: ReadonlyArray<FootprintState>): string {
  return states.find((state) => state.state_code === code)?.state_name ?? code;
}

export function formatDelta(trend: KpiTrend | undefined): string | undefined {
  const pct = trend?.delta_pct;
  if (pct === null || pct === undefined) return undefined;
  const sign = pct > 0 ? '+' : '';
  return `${sign}${pct.toFixed(1)}% ${trend?.comparison_label ?? 'vs prior snapshot'}`;
}

/**
 * Day-0 detection: trust the server-authoritative ``day_zero`` flag on
 * PortfolioPreview (R5-20). When true, the KPI grid swaps raw 0 values
 * for `null` so KpiCard renders an em-dash and the banner explains why.
 *
 * R6-06: the two-field fallback inference (marketable_population === 0
 * && data_refreshed_at === null) was dead code -- the backend always
 * emits ``day_zero`` (default False), so the "older server" case cannot
 * exist. The inference also returned a wrong answer for the valid case
 * where a filter happens to match zero borrowers on a populated
 * workspace (e.g. "investors in WY"). Removed; we trust the server.
 */
export function isDayZero(preview: PortfolioPreview | null): boolean {
  return preview?.day_zero === true;
}

/**
 * Campaign ROI projector (re-audit #4 Buyer-Wow #7).
 *
 * A transparent, fully client-side projection — every input is visible and
 * editable, nothing is hidden. It turns the build's lead count into a
 * dollar headline a CFO recognizes, without pretending to a precision the
 * data doesn't have:
 *
 *   fundings = addressable leads × response rate
 *   volume   = fundings × avg origination balance
 *   gross    = volume × revenue rate (gain-on-sale + fees)
 *   outreach = leads × per-lead blended contact cost
 *   net      = gross − outreach
 *
 * `leads` comes from the build (preview.high_intent_leads); the rate and
 * balance assumptions are operator-tunable with conservative mortgage
 * defaults. Pure and deterministic so the demo never surprises and the
 * math is unit-pinnable.
 */
/** Compact USD formatter for the projector headline ($2.3M, $940K, $1.2B, $1.5T). */
export function formatUsdCompact(n: number): string {
  if (!Number.isFinite(n)) return '—';
  const abs = Math.abs(n);
  const sign = n < 0 ? '-' : '';
  if (abs >= 1_000_000_000_000) return `${sign}$${(abs / 1_000_000_000_000).toFixed(1)}T`;
  if (abs >= 1_000_000_000) return `${sign}$${(abs / 1_000_000_000).toFixed(1)}B`;
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${sign}$${Math.round(abs / 1_000)}K`;
  return `${sign}$${Math.round(abs)}`;
}

export function dayZeroSafe(
  preview: PortfolioPreview | null,
  value: number | null | undefined,
): number | null {
  if (isDayZero(preview)) {
    return null;
  }
  return value ?? null;
}
