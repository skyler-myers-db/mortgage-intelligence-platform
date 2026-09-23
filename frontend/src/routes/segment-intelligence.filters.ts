/**
 * Segment Intelligence URL state (audit 2026-09-21 flow-09).
 *
 * Every filter the Segments route offers lives in typed search params, so a
 * refresh, Back/Forward or a shared link restores the exact view. The route
 * derives its filter state from the URL on every render (no mirrored
 * useState), and every change is written back as one history entry.
 *
 * Contract, following `lead-queue.filters.ts`:
 *   - parse functions never trust the URL: unknown values fall back to the
 *     default, raw lender strings are rejected (`isPublicLenderRef`), and
 *     segment codes must be registered;
 *   - defaults are omitted from the URL, so the bare route stays `?`-free;
 *   - writers PATCH the current params: they touch only the key being
 *     changed and preserve everything else, including params other features
 *     own (the map drill's geography keys) and values that could not be
 *     validated yet (a target lender before the config options loaded);
 *   - keys and values reuse the Lead Queue's portfolio-criteria vocabulary
 *     (`occupancy`, `lien_status`, `min_equity_pct_label`, ...), so the same
 *     query string means the same filter on both routes.
 *
 * The map's drill-down state is NOT owned here.
 */
import type { SegmentFilterMode } from '../lib/apiTypes';
import { isPublicLenderRef, LENDER_RELATIONSHIP_OPTIONS } from '../lib/lenderFilters';
import type { LeadSummary, SegmentCode } from '../types';

// Equity thresholds expressed as a minimum equity-to-AVM ratio. We don't
// have AVM on LeadSummary (it's on Borrower360), so the predicate uses
// equity_estimate as a lower bound proxy. Good enough for the UI filter.
export const EQUITY_FLOOR_USD: Readonly<Record<string, number>> = {
  Any: 0,
  'Equity ≥ 15%': 50_000,
  'Equity ≥ 25%': 150_000,
  'Equity ≥ 40%': 300_000,
};
export const CASHOUT_OPTIONS = Object.keys(EQUITY_FLOOR_USD);

// OCCUPANCY is a real predicate against `is_owner_occupied`.
export const OCCUPANCY_OPTIONS = ['All', 'Owner-occupied', 'Non-owner-occupied'] as const;
// LIEN (secondary filter) operates on the open-lien state of the subject
// property (`current_lien_balance` + `second_pos_amount`). Distinct from the
// portfolio-builder's population-level lien-status filter.
export const LIEN_OPTIONS = ['Any', 'Open 1st lien only', 'Open 2nd lien / HELOC', 'Free & clear'] as const;
// OWNER LINK buckets use `related_property_count` from the Owner Link bridge.
export const OWNER_LINK_OPTIONS = ['All', 'Single-property owner', 'Multi-property (2-4)', 'Portfolio investor (5+)'] as const;
// PURCHASE / EQUITY-CREDIT INTENT: MLS listing is a live purchase trigger.
// HELOC intent is a live Cotality propensity model signal; true filed
// building-permit activity remains a separate pending source.
export const PURCHASE_OPTIONS = ['All', 'Listed for sale', 'HELOC intent', 'Both'] as const;
export const CONTACTABILITY_OPTIONS = ['Eligible only', 'Any', 'Suppressed only'] as const;
export const CONSENT_OPTIONS = ['Any', 'Opt-in', 'Opt-out', 'Unknown'] as const;
export const RECENCY_OPTIONS = ['Any', 'Untouched 30d', 'Untouched 60d', 'Untouched 90d'] as const;

export const SEGMENT_MODE_OPTIONS: ReadonlyArray<{
  mode: SegmentFilterMode;
  label: string;
  description: string;
}> = [
  { mode: 'any', label: 'Any selected', description: 'OR · de-duped' },
  { mode: 'all', label: 'All selected', description: 'AND · intersection' },
];

export const VALID_SEGMENT_CODES: readonly SegmentCode[] = [
  'itm',
  'listed',
  'permit',
  'investor',
  'equity',
  'retention',
  // S1.3 overlay segments — keep in registry order with the gold meta table.
  'second_lien_itm',
  'heloc_draw_to_payback',
  'home_equity_history',
  'refi_propensity',
  'itm_on_related_property',
  'payoff_loss_leads',
  'permit_activity',
];
const VALID_SEGMENT_CODE_SET = new Set<string>(VALID_SEGMENT_CODES);

/** Secondary chip filters, held as the option labels the FilterSelects show. */
export interface ChipFilters {
  /** USPS state code of the LOCATION filter, or 'All'. */
  location: string;
  demographics: string;
  lenderRelationship: string;
  targetLenderRef: string;
  lien: string;
  ownerLink: string;
  purchase: string;
  cashout: string;
  contactability: string;
  consent: string;
  recency: string;
}
export type ChipFilterKey = keyof ChipFilters;

export const INITIAL_FILTERS: Readonly<ChipFilters> = {
  location: 'All',
  demographics: 'All',
  lenderRelationship: 'All',
  targetLenderRef: 'All',
  lien: 'Any',
  ownerLink: 'All',
  purchase: 'All',
  cashout: 'Any',
  contactability: 'Eligible only',
  consent: 'Any',
  recency: 'Any',
};

export const INITIAL_ACTIVE_SEGMENTS: SegmentCode[] = [];

/** Search-param key that stores each chip filter. */
export const CHIP_FILTER_PARAM: Readonly<Record<ChipFilterKey, string>> = {
  location: 'state',
  demographics: 'occupancy',
  lenderRelationship: 'lender_relationship',
  targetLenderRef: 'target_lender_ref',
  lien: 'lien_status',
  ownerLink: 'owner_link',
  purchase: 'purchase_intent',
  cashout: 'min_equity_pct_label',
  contactability: 'marketing_eligibility',
  consent: 'consent_status',
  recency: 'recency',
};

/**
 * Option label <-> URL value, where the URL speaks the portfolio-criteria
 * value the Lead Queue and the API already accept. Keys not listed here use
 * the option label itself as the URL value.
 */
const LIEN_URL_VALUE: Readonly<Record<string, string>> = {
  'Open 1st lien only': 'Open 1st lien',
  'Open 2nd lien / HELOC': 'Open HELOC',
  'Free & clear': 'Free & clear',
};
const CASHOUT_URL_VALUE: Readonly<Record<string, string>> = {
  'Equity ≥ 15%': '≥ 15%',
  'Equity ≥ 25%': '≥ 25%',
  'Equity ≥ 40%': '≥ 40%',
};
/** Extra spellings a deep link may carry (lead-queue parity). */
const URL_VALUE_ALIASES: Partial<Record<ChipFilterKey, Readonly<Record<string, string>>>> = {
  lien: { 'Open first lien': 'Open 1st lien only', 'Free and clear': 'Free & clear' },
  cashout: { '>= 15%': 'Equity ≥ 15%', '>= 25%': 'Equity ≥ 25%', '>= 40%': 'Equity ≥ 40%' },
  purchase: { 'Recent permit activity': 'HELOC intent' },
};
const URL_VALUE_BY_OPTION: Partial<Record<ChipFilterKey, Readonly<Record<string, string>>>> = {
  lien: LIEN_URL_VALUE,
  cashout: CASHOUT_URL_VALUE,
};
const OPTION_SETS: Partial<Record<ChipFilterKey, readonly string[]>> = {
  demographics: OCCUPANCY_OPTIONS,
  lenderRelationship: LENDER_RELATIONSHIP_OPTIONS,
  lien: LIEN_OPTIONS,
  ownerLink: OWNER_LINK_OPTIONS,
  purchase: PURCHASE_OPTIONS,
  cashout: CASHOUT_OPTIONS,
  contactability: CONTACTABILITY_OPTIONS,
  consent: CONSENT_OPTIONS,
  recency: RECENCY_OPTIONS,
};
const STATE_CODE_RE = /^[A-Z]{2}$/;
const CHIP_FILTER_KEYS = Object.keys(INITIAL_FILTERS) as ChipFilterKey[];
const SEGMENT_PARAM_KEYS = ['segment', 'segments', 'segment_codes', 'segment_mode'] as const;

export interface ChipFilterParseOptions {
  /** Public target-lender refs from /config/options (may still be loading). */
  targetLenderOptions?: readonly string[];
  /** Footprint state codes once loaded; an unknown code then reads as 'All'. */
  locationCodes?: readonly string[];
}

function optionFromUrlValue(key: ChipFilterKey, raw: string): string | undefined {
  const byOption = URL_VALUE_BY_OPTION[key];
  const aliased = URL_VALUE_ALIASES[key]?.[raw];
  if (aliased) return aliased;
  if (byOption) {
    // Keys with a criteria spelling accept only that spelling (and the
    // aliases above), never the option label: one URL per view.
    return Object.entries(byOption).find(([, value]) => value === raw)?.[0];
  }
  const options = OPTION_SETS[key];
  return options?.includes(raw) ? raw : undefined;
}

function parseChipFilter(key: ChipFilterKey, raw: string | null, options: ChipFilterParseOptions): string {
  const value = raw?.trim();
  const fallback = INITIAL_FILTERS[key];
  if (!value) return fallback;
  if (key === 'location') {
    const code = value.toUpperCase();
    if (!STATE_CODE_RE.test(code)) return fallback;
    const known = options.locationCodes ?? [];
    return known.length === 0 || known.includes(code) ? code : fallback;
  }
  if (key === 'targetLenderRef') {
    return isPublicLenderRef(value, options.targetLenderOptions ?? []) ? value : fallback;
  }
  return optionFromUrlValue(key, value) ?? fallback;
}

export function chipFiltersFromSearch(
  searchParams: URLSearchParams,
  options: ChipFilterParseOptions = {},
): ChipFilters {
  const out = { ...INITIAL_FILTERS };
  for (const key of CHIP_FILTER_KEYS) {
    out[key] = parseChipFilter(key, searchParams.get(CHIP_FILTER_PARAM[key]), options);
  }
  return out;
}

/** The four lender-overlay filters other routes deep-link with. */
export function lenderFiltersFromSearch(
  searchParams: URLSearchParams,
  targetLenderOptions: readonly string[] = [],
): Pick<ChipFilters, 'lenderRelationship' | 'targetLenderRef' | 'ownerLink' | 'purchase'> {
  const all = chipFiltersFromSearch(searchParams, { targetLenderOptions });
  return {
    lenderRelationship: all.lenderRelationship,
    targetLenderRef: all.targetLenderRef,
    ownerLink: all.ownerLink,
    purchase: all.purchase,
  };
}

/**
 * Patch one chip filter into the URL: the default value removes the key,
 * anything else writes its URL value. Every other param is preserved.
 */
export function searchParamsWithChipFilter(
  searchParams: URLSearchParams,
  key: ChipFilterKey,
  value: string,
): URLSearchParams {
  const next = new URLSearchParams(searchParams);
  const param = CHIP_FILTER_PARAM[key];
  if (value === INITIAL_FILTERS[key]) {
    next.delete(param);
  } else {
    next.set(param, URL_VALUE_BY_OPTION[key]?.[value] ?? value);
  }
  return next;
}

export function chipFiltersAreDefault(filters: ChipFilters): boolean {
  return CHIP_FILTER_KEYS.every((key) => filters[key] === INITIAL_FILTERS[key]);
}

export function activeSegmentsFromSearch(searchParams: URLSearchParams): SegmentCode[] {
  const raw = [
    searchParams.get('segment'),
    searchParams.get('segments'),
    searchParams.get('segment_codes'),
  ]
    .filter(Boolean)
    .join(',');
  const selected: SegmentCode[] = [];
  for (const value of raw.split(',')) {
    const code = value.trim().toLowerCase();
    if (!VALID_SEGMENT_CODE_SET.has(code) || selected.includes(code as SegmentCode)) continue;
    selected.push(code as SegmentCode);
  }
  return selected;
}

export function segmentModeFromSearch(searchParams: URLSearchParams): SegmentFilterMode {
  return searchParams.get('segment_mode')?.toLowerCase() === 'all' ? 'all' : 'any';
}

/**
 * Write the selected cards and the match mode. A multi-card selection always
 * carries its mode, the convention every route shares (the Lead Queue and
 * its deep links write `segment_codes` with `segment_mode`). Otherwise `any`
 * is the default and is omitted, while `all` is kept even with fewer than
 * two cards, so a user who picks the mode before the cards keeps the choice.
 */
export function segmentSearchParamsForState(
  searchParams: URLSearchParams,
  segments: readonly SegmentCode[],
  mode: SegmentFilterMode,
): URLSearchParams {
  const next = new URLSearchParams(searchParams);
  next.delete('segment');
  next.delete('segments');
  next.delete('segment_codes');
  if (segments.length === 1) {
    next.set('segment', segments[0]);
  } else if (segments.length > 1) {
    next.set('segment_codes', segments.join(','));
  }
  if (segments.length > 1 || mode === 'all') next.set('segment_mode', mode);
  else next.delete('segment_mode');
  return next;
}

/**
 * The params this module owns, in a canonical order. A stable memo key: a
 * change to a param some other feature owns (the map drill) leaves it equal,
 * so derived filter objects keep their identity and nothing refetches.
 */
export function segmentFilterSearchKey(searchParams: URLSearchParams): string {
  const owned = new URLSearchParams();
  for (const key of [...SEGMENT_PARAM_KEYS, ...CHIP_FILTER_KEYS.map((k) => CHIP_FILTER_PARAM[k])]) {
    const value = searchParams.get(key);
    if (value !== null) owned.set(key, value);
  }
  return owned.toString();
}

/** Remove every param this module owns; params other features own survive. */
export function searchParamsWithoutSegmentFilters(searchParams: URLSearchParams): URLSearchParams {
  const next = new URLSearchParams(searchParams);
  for (const key of SEGMENT_PARAM_KEYS) next.delete(key);
  for (const key of CHIP_FILTER_KEYS) next.delete(CHIP_FILTER_PARAM[key]);
  return next;
}

export function formatSelectedSegmentLabel(
  labels: string[],
  segmentMode: SegmentFilterMode,
): string {
  if (labels.length <= 1) return labels.join('');
  const conjunction = segmentMode === 'all' ? 'and' : 'or';
  if (labels.length === 2) return `${labels[0]} ${conjunction} ${labels[1]}`;
  return `${labels.slice(0, -1).join(', ')}, ${conjunction} ${labels[labels.length - 1]}`;
}

export function segmentCardQuerySelection(
  segments: SegmentCode[],
  mode: SegmentFilterMode,
): { segmentCodes?: SegmentCode[]; segmentMode: SegmentFilterMode } {
  return segments.length > 0
    ? { segmentCodes: segments, segmentMode: mode }
    : { segmentCodes: undefined, segmentMode: 'any' };
}

/**
 * Server-side portfolio criteria for the secondary filters. No-op values
 * ('All' / 'Any') are omitted; the default contactability 'Eligible only' is
 * NOT a no-op and is always sent, exactly as before the URL move. LOCATION
 * travels as the geography `state`, not as a criterion.
 */
export function secondaryPortfolioCriteria(filters: ChipFilters): Record<string, string> {
  const criteria: Record<string, string> = {};
  for (const key of CHIP_FILTER_KEYS) {
    const value = filters[key];
    if (key === 'location' || value === 'All' || value === 'Any') continue;
    criteria[CHIP_FILTER_PARAM[key]] = URL_VALUE_BY_OPTION[key]?.[value] ?? value;
  }
  return criteria;
}

/**
 * Secondary predicates carried on the ranked lead rows. Primary segment and
 * geography filters are pushed down to /api/leads before LIMIT; these still
 * run locally on the returned rows.
 */
export function applySecondaryLeadFilters(leads: LeadSummary[], filters: ChipFilters): LeadSummary[] {
  let out = leads;
  // OCCUPANCY -> is_owner_occupied predicate.
  if (filters.demographics === 'Owner-occupied') {
    out = out.filter((l) => l.is_owner_occupied === true);
  } else if (filters.demographics === 'Non-owner-occupied') {
    out = out.filter((l) => l.is_owner_occupied === false);
  }
  // LIEN (secondary) -> current_lien_balance + second_pos_amount.
  if (filters.lien === 'Open 1st lien only') {
    out = out.filter(
      (l) =>
        (l.current_lien_balance ?? 0) > 0 &&
        (l.second_pos_amount == null || l.second_pos_amount === 0),
    );
  } else if (filters.lien === 'Open 2nd lien / HELOC') {
    out = out.filter((l) => (l.second_pos_amount ?? 0) > 0);
  } else if (filters.lien === 'Free & clear') {
    out = out.filter((l) => (l.current_lien_balance ?? 0) === 0);
  }
  // OWNER LINK -> related_property_count buckets.
  const related = (l: LeadSummary) => l.related_property_count ?? 1;
  if (filters.ownerLink === 'Single-property owner') {
    out = out.filter((l) => related(l) <= 1);
  } else if (filters.ownerLink === 'Multi-property (2-4)') {
    out = out.filter((l) => related(l) >= 2 && related(l) <= 4);
  } else if (filters.ownerLink === 'Portfolio investor (5+)') {
    out = out.filter((l) => related(l) >= 5);
  }
  // PURCHASE INTENT -> listed_for_sale + Cotality HELOC propensity. True
  // filed building permits remain a separate pending source and must not be
  // inferred from the propensity model.
  const hasHelocIntent = (l: LeadSummary) => l.has_heloc_propensity_trigger === true;
  if (filters.purchase === 'Listed for sale') {
    out = out.filter((l) => l.listed_for_sale === true);
  } else if (filters.purchase === 'HELOC intent') {
    out = out.filter(hasHelocIntent);
  } else if (filters.purchase === 'Both') {
    out = out.filter((l) => l.listed_for_sale === true && hasHelocIntent(l));
  }
  // Cash-out equity floor.
  const floor = EQUITY_FLOOR_USD[filters.cashout] ?? 0;
  if (floor > 0) out = out.filter((l) => l.equity_estimate >= floor);
  return out;
}
