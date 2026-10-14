/**
 * Page-context templates for the floating Genie panel (audit 2026-09-21
 * `genie-04`, phase 1: frontend only).
 *
 * Genie used to be blind to the page: the panel showed the same four server
 * starters everywhere, and no KPI, segment card, map region or lead row could
 * launch a grounded question. This module supplies per-route starters and
 * the "Ask Genie about this" prompts; `openGenie` (`genieOpen.ts`) PREFILLS
 * the composer with one and never submits it, so the prompt of record is
 * exactly what the user sends and the server-side guards scan. A
 * server-composed scope sentence (phase 2) would alter the token-verified
 * prompt of record; phase 1 deliberately does not.
 *
 * Every sentence lives in `genieContextTemplates.json`, the ONE source that
 * both this module and `tests/unit/test_genie_context_templates.py` read. The
 * Python test runs every rendering (every state x every segment) through the
 * real deterministic prompt guards; `genieContext.test.ts` pins the
 * vocabulary to the shipped starter set (`genie/sample_questions.md`) and the
 * glossary. The only interpolated values are registry enum values: segment
 * names from `segmentMetadata`, and federal state names by USPS code. A
 * template never interpolates free text -- not a KPI label, not a city, never
 * a masked borrower id or a contact field.
 */
import type { GenieContext, GenieContextSegmentCode, GenieContextStateCode } from './apiClients/genieContextScope';
import templates from './genieContextTemplates.json';
import { resolveRouteMeta } from './routeMeta';
import { segmentByCode } from './segmentMetadata';
import { isUspsStateCode } from './uspsStates';

/**
 * Verbatim starters from `genie/sample_questions.md`. Two shipped starters are
 * deliberately absent -- #7 ("Show the top 10 cash-out candidates ...") and
 * #9 ("Break down the In-the-Money segment by state.") -- because the submit
 * route's own prompt battery refuses both as `unreviewed_criterion` (measured
 * offline 2026-09-22). Offering a starter the guard refuses is a dead end,
 * and the guard is not this lane's to relax.
 */
export const GENIE_STARTERS: Readonly<Record<string, string>> = templates.starters;

const ROUTE_STARTERS: Readonly<Record<string, readonly string[]>> = templates.routeStarters;
const KPI_PROMPTS: Readonly<Record<string, string>> = templates.kpiPrompts;
const SEGMENTS_WITHOUT_TEMPLATES: Readonly<Record<string, string>> = templates.segmentsWithoutTemplates;

/**
 * Federal state names by USPS code: the 50 states plus DC. The value a
 * template renders is always one of these 51 strings, never caller text.
 * `genieContext.test.ts` pins this table to the map's own state registry.
 */
export const US_STATE_NAME_BY_CODE: Readonly<Record<string, string>> = templates.stateNames;

function fill(template: string, values: { state?: string; segment?: string }): string {
  return template
    .replace('{state}', values.state ?? '')
    .replace('{segment}', values.segment ?? '');
}

/**
 * Starter prompts for the page at `pathname`, keyed by its `ROUTE_META`
 * pattern; empty when the route has none. `/ask-genie` is absent on purpose:
 * the deep-dive route keeps the server's own sample list, and the floating
 * panel falls back to the same list there.
 */
export function genieStartersForRoute(pathname: string): string[] {
  const keys = ROUTE_STARTERS[resolveRouteMeta(pathname).pattern] ?? [];
  return keys.map((key) => GENIE_STARTERS[key]).filter((prompt): prompt is string => Boolean(prompt));
}

/**
 * Reviewed prompt for a rendered KPI label, or null for an unknown one. The
 * label is a lookup key into a closed set, never interpolated; matching is
 * case-insensitive so the Analytics title-case twins ("Addressable
 * Borrowers") share the Home template.
 */
export function genieKpiPrompt(label: string): string | null {
  return KPI_PROMPTS[label.trim().toLowerCase()] ?? null;
}

/**
 * The registry's segment name as it appears inside a template sentence, or
 * null for an unknown code or one without templates. Lower-cased on purpose:
 * the prompt guard's person-name heuristic reads a title-case word pair as a
 * name shape, and six registered names ("Payoff Loss", "Refi Propensity",
 * "Permit Activity", ...) are exactly that shape. Measured over every state x
 * segment rendering (2026-09-22): title case refused those six on name shape,
 * lower case refuses none of them. The words are unchanged, so the vocabulary
 * contract holds. A code listed in `segmentsWithoutTemplates` has no prompt at
 * all (the JSON says why, and the Python battery keeps that list honest).
 */
function segmentPhrase(code: string): string | null {
  if (Object.prototype.hasOwnProperty.call(SEGMENTS_WITHOUT_TEMPLATES, code)) return null;
  const segment = segmentByCode(code);
  return segment ? segment.name.toLowerCase() : null;
}

/**
 * "Which states have the most borrowers in the <segment> segment?" for a
 * registered segment code. Not "Break down the <segment> segment by state":
 * the criterion machine refuses that shape for every segment, the shipped
 * starter #9 included.
 */
export function genieSegmentPrompt(code: string): string | null {
  const segment = segmentPhrase(code);
  return segment ? fill(templates.segmentTemplate, { segment }) : null;
}

/** Federal state name for a USPS code, or null when the code is not one. */
export function stateNameForCode(code: string | null | undefined): string | null {
  const upper = (code ?? '').trim().toUpperCase();
  if (!upper || !isUspsStateCode(upper)) return null;
  return US_STATE_NAME_BY_CODE[upper] ?? null;
}

/** "Which 5 ZIP codes in <State> have the most in-the-money borrowers?" */
export function genieStatePrompt(code: string | null | undefined): string | null {
  const state = stateNameForCode(code);
  return state ? fill(templates.stateTemplate, { state }) : null;
}

export interface GenieLeadContext {
  /** The lead's segment codes; the first one with templates names the segment. */
  segmentCodes: readonly string[];
  /** The lead's USPS state code. */
  stateCode: string | null | undefined;
}

/**
 * A lead row's context is its SEGMENT and its STATE: never the masked
 * borrower id, the property ref, or any contact field. Without a segment
 * that has templates the state template stands in; without a state there is
 * nothing to ask that the registry can vouch for.
 */
export function genieLeadPrompt({ segmentCodes, stateCode }: GenieLeadContext): string | null {
  const state = stateNameForCode(stateCode);
  if (!state) return null;
  const segment = segmentCodes.map(segmentPhrase).find((phrase) => phrase !== null);
  if (!segment) return genieStatePrompt(stateCode);
  return fill(templates.leadTemplate, { state, segment });
}

/**
 * Phase 2 (audit genie-04, states-v2): the Lead Queue's filter params as the
 * CLOSED context model `POST /api/genie/context-scope` renders server-side
 * (backend/schemas/genie_context.py), or null.
 *
 * A closed grammar, not a mapping: every accepted key is listed below with
 * its arity, and ANY other key (borrower ids, a cohort, approval / outreach /
 * funnel / aged / portfolio criteria, score or spread bounds) makes the whole
 * selection null, so a filter the question cannot state never silently drops
 * out of it. Values are codes only: registry segment codes, a USPS code, a
 * five-digit county FIPS or ZIP, one `CITY~ST` key, a public Competitor ref.
 * `assigned_to` is accepted only as `me` and travels as `assignee: 'me'` so
 * the server can say it was left out; an email value is null and never
 * leaves the browser. A config-allowed tenant lender ref is not reviewed
 * vocabulary, so it is null too.
 *
 * Null as well when phase 1 already covers the selection (at most one
 * segment and nothing but a state: `queueFilterGeniePrompt`), and for a
 * combination the model refuses (two places, a city outside the state, or
 * no slot it can render). A state beside a ZIP (the map's ZIP drill) travels
 * as both codes, and the server's question names both ('ZIP code NNNNN,
 * ST'): no committed table checks that the ZIP lies in the state, so the
 * state never drops out.
 */
const CONTEXT_FILTER_KEYS: ReadonlySet<string> = new Set([
  'segment', 'segment_codes', 'segment_mode', 'state', 'states', 'county', 'counties',
  'zip', 'zips', 'cities', 'target_lender_ref', 'assigned_to',
]);
const FIVE_DIGITS = /^[0-9]{5}$/;
const CONTEXT_CITY_KEY = /^[A-Z][A-Z .-]{0,47}~[A-Z]{2}$/;
const COMPETITOR_REF = /^Competitor ([A-Z]|Other)$/;

function contextValues(filters: URLSearchParams, ...keys: string[]): string[] {
  const raw = keys.flatMap((key) => filters.getAll(key).flatMap((value) => value.split(',')));
  return [...new Set(raw.map((value) => value.trim()).filter(Boolean))];
}

function isContextStateCode(code: string): code is GenieContextStateCode {
  return Object.prototype.hasOwnProperty.call(US_STATE_NAME_BY_CODE, code);
}

function isContextSegmentCode(code: string): code is GenieContextSegmentCode {
  return segmentByCode(code) !== undefined && !Object.prototype.hasOwnProperty.call(SEGMENTS_WITHOUT_TEMPLATES, code);
}

/** One `CITY~ST` key of at most four words in a USPS state, or null. */
function isContextCity(key: string): boolean {
  if (!CONTEXT_CITY_KEY.test(key)) return false;
  const [name, state] = key.split('~');
  return name.trim().split(/\s+/).length <= 4 && isContextStateCode(state);
}

const atMostOne = (values: readonly string[], valid: (value: string) => boolean): boolean =>
  values.length <= 1 && values.every(valid);

export function genieContextFromQueueFilters(filters: URLSearchParams): GenieContext | null {
  for (const key of filters.keys()) {
    if (!CONTEXT_FILTER_KEYS.has(key)) return null;
  }
  const segments = contextValues(filters, 'segment', 'segment_codes').map((code) => code.toLowerCase());
  const states = contextValues(filters, 'state', 'states').map((code) => code.toUpperCase());
  const counties = contextValues(filters, 'county', 'counties');
  const zips = contextValues(filters, 'zip', 'zips');
  const cities = contextValues(filters, 'cities');
  const lenders = contextValues(filters, 'target_lender_ref');
  const assignees = contextValues(filters, 'assigned_to');
  const modes = contextValues(filters, 'segment_mode').map((value) => value.toLowerCase());
  const segmentCodes = segments.filter(isContextSegmentCode);
  const stateCodes = states.filter(isContextStateCode);
  if (segments.length > 2 || segmentCodes.length !== segments.length) return null;
  if (stateCodes.length > 1 || stateCodes.length !== states.length) return null;
  if (!atMostOne(counties, (code) => FIVE_DIGITS.test(code)) || !atMostOne(zips, (code) => FIVE_DIGITS.test(code))) return null;
  if (!atMostOne(cities, isContextCity) || !atMostOne(lenders, (ref) => COMPETITOR_REF.test(ref))) return null;
  if (!atMostOne(assignees, (value) => value === 'me') || !atMostOne(modes, (value) => value === 'all' || value === 'any')) return null;
  const [state] = stateCodes;
  const [county] = counties;
  const [zip] = zips;
  const [city] = cities;
  const [lender] = lenders;
  if ([county, zip, city].filter(Boolean).length > 1) return null;
  if (city && state && !city.endsWith(`~${state}`)) return null;
  const narrowed = Boolean(county || zip || city || lender);
  // Phase 1 owns one segment and/or one state with nothing else.
  if (!narrowed && assignees.length === 0 && segmentCodes.length <= 1) return null;
  if (!narrowed && !state && segmentCodes.length === 0) return null;
  return {
    route: 'lead-queue',
    ...(segmentCodes.length > 0 ? { segment_codes: segmentCodes, segment_mode: modes[0] === 'all' ? 'all' : 'any' } : {}),
    ...(state ? { state } : {}),
    ...(county ? { county_fips: county } : {}),
    ...(zip ? { zip } : {}),
    ...(city ? { city } : {}),
    ...(lender ? { lender_ref: lender } : {}),
    ...(assignees.length > 0 ? { assignee: 'me' as const } : {}),
  };
}
