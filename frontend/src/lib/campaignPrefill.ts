import type { SegmentCode } from '../types';
import { normalizeSegmentCode } from './segmentMetadata';

/**
 * Typed campaign-draft prefill contract for the geo → campaigns handoff (S9).
 *
 * TypeScript mirror of `backend/schemas/campaign_prefill.py` — keep the two in
 * lockstep (same parameter names, same validation). The geography drill-down's
 * "Start campaign" affordance seeds the EXISTING campaigns surface (the
 * Portfolio Builder, which owns campaign creation today) with a typed draft
 * context. S10 (the dedicated campaign builder) is not built yet — this module
 * IS the wire contract it will consume, so the encoding is versioned by
 * parameter names and validated on both sides.
 *
 *  - `states=XX` reuses the Portfolio Builder's existing deep-link parameter,
 *    so the state predicate applies to the build immediately (see
 *    `parseStateCodesFromUrl` in routes/portfolio-builder.logic.ts).
 *  - `prefill_*` parameters carry the drill context (county / ZIP / segment
 *    carry-over) that today's surface can only display as draft context. The
 *    UI copy must stay honest about that boundary: county/ZIP narrowing becomes
 *    a build predicate when S10 lands, not before.
 */

export const PREFILL_SOURCE = 'geo-drilldown' as const;

// Wire parameter names — the versioned contract S10 will read. These MUST
// match backend/schemas/campaign_prefill.py exactly.
export const PARAM_SOURCE = 'prefill_source';
export const PARAM_LEVEL = 'prefill_level';
export const PARAM_STATES = 'states'; // existing Portfolio Builder deep-link param
export const PARAM_COUNTY_FIPS = 'prefill_county_fips';
export const PARAM_COUNTY_NAME = 'prefill_county_name';
export const PARAM_ZIP = 'prefill_zip';
export const PARAM_SEGMENTS = 'prefill_segments';
export const PARAM_SEGMENT_MODE = 'prefill_segment_mode';
export const PARAM_LEAD_COUNT = 'prefill_lead_count';
export const PARAM_UNATTENDED_COUNT = 'prefill_unattended_count';

export type CampaignPrefillLevel = 'state' | 'county' | 'zip';
export type CampaignSegmentMode = 'any' | 'all';

const STATE_RE = /^[A-Z]{2}$/;
const FIPS_RE = /^\d{5}$/;
const ZIP_RE = /^\d{5}$/;

/** One drilled geography unit, carried into a campaign draft. */
export interface CampaignGeoPrefill {
  source: typeof PREFILL_SOURCE;
  level: CampaignPrefillLevel;
  /** 2-char USPS state code of the drilled unit (always uppercase). */
  state: string;
  /** 5-char county FIPS; set at county and zip levels. */
  countyFips: string | null;
  /** Display name for the county chip. */
  countyName: string | null;
  /** 5-digit ZIP; set at zip level. */
  zip: string | null;
  /** Segment filter active on the map when the draft was started. */
  segmentCodes: SegmentCode[];
  segmentMode: CampaignSegmentMode;
  /** Unit lead count snapshot at draft time. */
  leadCount: number | null;
  /** Unit unattended count snapshot at draft time. */
  unattendedCount: number | null;
}

export interface CampaignGeoPrefillInput {
  level: CampaignPrefillLevel;
  state: string;
  countyFips?: string | null;
  countyName?: string | null;
  zip?: string | null;
  segmentCodes?: readonly string[];
  segmentMode?: CampaignSegmentMode;
  leadCount?: number | null;
  unattendedCount?: number | null;
}

class PrefillError extends Error {}

function normState(value: string | null | undefined): string {
  const code = String(value ?? '').trim().toUpperCase();
  if (!STATE_RE.test(code)) {
    throw new PrefillError('state must be a two-letter USPS code');
  }
  return code;
}

function normFips(value: string | null | undefined): string | null {
  if (value === null || value === undefined) return null;
  const fips = String(value).trim();
  if (fips.length === 0) return null;
  if (!FIPS_RE.test(fips)) {
    throw new PrefillError('county_fips must be a 5-digit county FIPS code');
  }
  return fips;
}

function normZip(value: string | null | undefined): string | null {
  if (value === null || value === undefined) return null;
  const zip5 = String(value).trim();
  if (zip5.length === 0) return null;
  if (!ZIP_RE.test(zip5)) {
    throw new PrefillError('zip must be a 5-digit ZIP code');
  }
  return zip5;
}

/**
 * Normalise + dedupe segment codes. Unknown codes throw (mirrors the backend's
 * `_ALLOWED_SEGMENTS` gate) so a malformed marked link surfaces a warning
 * rather than silently dropping context.
 */
function normSegments(values: readonly string[] | undefined): SegmentCode[] {
  const out: SegmentCode[] = [];
  for (const raw of values ?? []) {
    const trimmed = String(raw ?? '').trim();
    if (!trimmed) continue;
    const code = normalizeSegmentCode(trimmed);
    if (!code) {
      throw new PrefillError(`unknown segment code: ${trimmed.toLowerCase()}`);
    }
    if (!out.includes(code)) out.push(code);
  }
  return out;
}

function normMode(value: string | null | undefined): CampaignSegmentMode {
  return String(value ?? 'any').trim().toLowerCase() === 'all' ? 'all' : 'any';
}

function normCount(value: string | null | undefined, key: string): number | null {
  const raw = String(value ?? '').trim();
  if (!raw) return null;
  if (!/^\d+$/.test(raw)) {
    throw new PrefillError(`${key} must be a non-negative integer`);
  }
  return Number.parseInt(raw, 10);
}

/**
 * Build a validated `CampaignGeoPrefill`. Enforces the same level-consistency
 * rules as the backend model validator: county/zip require a county FIPS; zip
 * requires a ZIP; state level must NOT carry county/zip.
 */
export function makeCampaignPrefill(input: CampaignGeoPrefillInput): CampaignGeoPrefill {
  const level = input.level;
  const state = normState(input.state);
  const countyFips = normFips(input.countyFips);
  const zip = normZip(input.zip);
  const countyName = (() => {
    const name = (input.countyName ?? '').trim();
    return name ? name.slice(0, 64) : null;
  })();
  const segmentCodes = normSegments(input.segmentCodes);
  const leadCount =
    input.leadCount === null || input.leadCount === undefined
      ? null
      : Math.max(0, Math.trunc(input.leadCount));
  const unattendedCount =
    input.unattendedCount === null || input.unattendedCount === undefined
      ? null
      : Math.max(0, Math.trunc(input.unattendedCount));

  if ((level === 'county' || level === 'zip') && !countyFips) {
    throw new PrefillError('county_fips is required at county and zip levels');
  }
  if (level === 'zip' && !zip) {
    throw new PrefillError('zip is required at zip level');
  }
  if (level === 'state' && (countyFips || zip)) {
    throw new PrefillError('state-level prefill must not carry county_fips or zip');
  }

  return {
    source: PREFILL_SOURCE,
    level,
    state,
    countyFips,
    countyName,
    zip,
    segmentCodes,
    segmentMode: normMode(input.segmentMode),
    leadCount,
    unattendedCount,
  };
}

/**
 * Encode a prefill into query params for a `/portfolio-builder` deep link
 * (S10-stable). Omits empty/default fields so the URL stays compact.
 */
export function buildCampaignPrefillSearch(prefill: CampaignGeoPrefill): URLSearchParams {
  const params = new URLSearchParams();
  params.set(PARAM_SOURCE, prefill.source);
  params.set(PARAM_LEVEL, prefill.level);
  params.set(PARAM_STATES, prefill.state);
  if (prefill.countyFips) params.set(PARAM_COUNTY_FIPS, prefill.countyFips);
  if (prefill.countyName) params.set(PARAM_COUNTY_NAME, prefill.countyName);
  if (prefill.zip) params.set(PARAM_ZIP, prefill.zip);
  if (prefill.segmentCodes.length > 0) {
    params.set(PARAM_SEGMENTS, prefill.segmentCodes.join(','));
    params.set(PARAM_SEGMENT_MODE, prefill.segmentMode);
  }
  if (prefill.leadCount !== null) params.set(PARAM_LEAD_COUNT, String(prefill.leadCount));
  if (prefill.unattendedCount !== null) {
    params.set(PARAM_UNATTENDED_COUNT, String(prefill.unattendedCount));
  }
  return params;
}

export interface CampaignPrefillParseResult {
  prefill: CampaignGeoPrefill | null;
  error?: string;
}

/**
 * Decode a query-parameter mapping.
 *
 * Returns `{ prefill: null }` when the prefill marker is absent (an ordinary
 * deep link, not a geo handoff). Returns `{ prefill: null, error }` on a
 * marked but malformed payload — the route surfaces the error as a small
 * honest warning line instead of applying half a context.
 */
export function parseCampaignPrefill(sp: URLSearchParams): CampaignPrefillParseResult {
  if ((sp.get(PARAM_SOURCE) ?? '').trim() !== PREFILL_SOURCE) {
    return { prefill: null };
  }
  try {
    const level = (sp.get(PARAM_LEVEL) ?? '').trim();
    if (level !== 'state' && level !== 'county' && level !== 'zip') {
      throw new PrefillError('prefill_level must be state, county, or zip');
    }
    const segmentsRaw = (sp.get(PARAM_SEGMENTS) ?? '').trim();
    const prefill = makeCampaignPrefill({
      level,
      state: (sp.get(PARAM_STATES) ?? '').split(',')[0] ?? '',
      countyFips: sp.get(PARAM_COUNTY_FIPS),
      countyName: sp.get(PARAM_COUNTY_NAME),
      zip: sp.get(PARAM_ZIP),
      segmentCodes: segmentsRaw ? segmentsRaw.split(',') : [],
      segmentMode: normMode(sp.get(PARAM_SEGMENT_MODE)),
      leadCount: normCount(sp.get(PARAM_LEAD_COUNT), PARAM_LEAD_COUNT),
      unattendedCount: normCount(sp.get(PARAM_UNATTENDED_COUNT), PARAM_UNATTENDED_COUNT),
    });
    return { prefill };
  } catch (err) {
    return {
      prefill: null,
      error: err instanceof Error ? err.message : 'invalid campaign prefill link',
    };
  }
}
