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
};

export function buildDefaultCampaignSetup(
  lenderName: string = DEFAULT_CAMPAIGN_LENDER_NAME,
): CampaignSetupState {
  const label = lenderName.trim() || DEFAULT_CAMPAIGN_LENDER_NAME;
  return {
    subjectA: `${label} review for your current loan options`,
    subjectB: 'A refinance review may improve your mortgage fit',
    bodyA: `Review current mortgage fit with ${label} using the governed relationship-aware template.`,
    bodyB: 'Highlight rate, equity, and human review using the governed relationship-aware template.',
    holdoutPct: '10',
    startLocal: '09:00',
    endLocal: '16:00',
    budget: '',
    emailCost: '1.20',
    smsCost: '0.08',
    mailCost: '0.86',
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
  const query = sp.toString();
  return query ? `/segment-intelligence?${query}` : '/segment-intelligence';
}

export function campaignCriteriaSummary(campaign: CampaignSummary): string {
  const criteria = campaign.criteria ?? {};
  const parts: string[] = [];
  const states = Array.isArray(criteria.states) ? criteria.states.map(String).filter(Boolean) : [];
  if (states.length > 0) parts.push(states.join(', '));
  for (const key of ['lender_relationship', 'target_lender_ref', 'product', 'marketing_eligibility', 'consent_status', 'recency']) {
    const value = criteria[key];
    if (typeof value === 'string' && value && value !== 'All' && value !== 'Any') {
      parts.push(value);
    }
  }
  const policy = campaign.suppression_policy?.default;
  if (typeof policy === 'string' && policy) parts.push(policy.replace(/_/g, ' '));
  const holdoutPct = campaign.holdout?.size_pct;
  if (typeof holdoutPct === 'number') parts.push(`${holdoutPct}% holdout`);
  return parts.length > 0 ? parts.join(' · ') : 'Eligible-only draft campaign';
}

function boundedNumber(raw: string, fallback: number, min: number, max: number): number {
  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, parsed));
}

function nullableMoney(raw: string): number | null {
  const parsed = Number(raw);
  if (!Number.isFinite(parsed) || parsed <= 0) return null;
  return Number(parsed.toFixed(2));
}

export function buildCampaignConfig(setup: CampaignSetupState): {
  suppression_policy: Record<string, unknown>;
  message_variants: Record<string, unknown>[];
  channel_cascade: Record<string, unknown>[];
  send_window: Record<string, unknown>;
  holdout: Record<string, unknown>;
  roi_assumptions: Record<string, unknown>;
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
      },
      {
        variant_name: 'B',
        channel: 'email',
        subject: setup.subjectB.trim(),
        body: setup.bodyB.trim(),
        weight_pct: Math.max(0, Math.floor((100 - holdoutPct) / 2)),
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
      budget_usd: nullableMoney(setup.budget),
      cost_per_contact_usd: {
        email: nullableMoney(setup.emailCost),
        sms: nullableMoney(setup.smsCost),
        direct_mail: nullableMoney(setup.mailCost),
      },
      source: 'operator_configured',
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

export function dayZeroSafe(
  preview: PortfolioPreview | null,
  value: number | null | undefined,
): number | null {
  if (isDayZero(preview)) {
    return null;
  }
  return value ?? null;
}
