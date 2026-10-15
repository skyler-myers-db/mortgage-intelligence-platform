/**
 * The Lead Queue filter omnibox grammar (wow-power-6, merges wow-ai-6; W5e).
 * Pure and React-free: a hand-written tokenizer over a CLOSED vocabulary
 * that maps typed words onto the queue's existing URL filter params, shows
 * where each reading came from, and lists what it did not recognize.
 *
 *   TX IL           USPS state codes, UPPER CASE only (a lower-case 'in',
 *                   'or', 'me', 'oh' is a word, never a state); several ->
 *                   states=IL,TX (sorted), one -> state=TX
 *   itm, equity     segment codes, full segment names (case-insensitive
 *                   phrases) and 'in the money' / 'in-the-money' -> itm;
 *                   several -> segment_codes + segment_mode=any
 *   refi heloc      product words (refi, heloc, cash-out / cashout, purchase,
 *                   retention) -> product; a bare word that is both a product
 *                   and a segment word reads as the PRODUCT (the chip shows
 *                   the source phrase); 'segment:<code|name>' and
 *                   'product:<word>' force a reading
 *   pending ...     approval words (pending, approved, rejected, hold)
 *   sent ...        outreach words (queued, sent, replied, bounced, actioned)
 *   current ...     customer / current, former, competitor -> lender_relationship
 *   aged>7          aged>7 | aged>14 | aged>30 -> aged_days
 *   score>=70       score>=N, >N, <=N, <N, N-M -> the score bounds (0..100)
 *   spread>=50      spread / bps >=N and <=N -> the rate-spread bounds (-1000..5000)
 *
 * An inverted pair, a second value for a single-valued filter and every
 * other token are reported as unrecognized, never emitted. No ZIP, county,
 * city, borrower id, cohort, assignee email or free-text token is ever
 * emitted (ZIPs and masked ids stay with the palette's borrower search), and
 * nothing here is sent anywhere: the caller writes `params` into the URL on
 * an explicit Enter, and unrecognized text reaches Genie only through
 * openGenie's prefill after a click.
 */
import { SEGMENT_DEFINITIONS } from './segmentMetadata';
import { USPS_STATE_CODES } from './uspsStates';
import type { SegmentCode } from '../types';

export interface FilterGrammarChip {
  /** The URL param the reading writes. */
  key: string;
  /** The filter label, as the filter pills spell it ("STATE"). */
  label: string;
  /** The value as the pill shows it ("TX", "HELOC", "Aged >7d"). */
  value: string;
  /** The typed phrase it came from. */
  source: string;
}

export interface FilterGrammarResult {
  params: URLSearchParams;
  chips: FilterGrammarChip[];
  unrecognized: string[];
}

/** The product labels the PRODUCT filter accepts (lead-queue.filters PRODUCT_FILTER_OPTIONS). */
const PRODUCT_WORDS: Readonly<Record<string, string>> = {
  refi: 'Refi',
  heloc: 'HELOC',
  'cash-out': 'Cash-out',
  cashout: 'Cash-out',
  purchase: 'Purchase',
  retention: 'Retention',
};
const APPROVAL_WORDS = new Set(['pending', 'approved', 'rejected', 'hold']);
const OUTREACH_WORDS = new Set(['queued', 'sent', 'replied', 'bounced', 'actioned']);
const RELATIONSHIP_WORDS: Readonly<Record<string, string>> = {
  customer: 'Current customer',
  current: 'Current customer',
  former: 'Former customer',
  competitor: 'Competitor customer',
};
const AGED_DAYS = new Set(['7', '14', '30']);
const SCORE_LIMITS = { min: 0, max: 100 } as const;
const SPREAD_LIMITS = { min: -1000, max: 5000 } as const;

const ITM_PHRASES = ['in the money', 'in-the-money'];
const SEGMENT_CODES = new Set<string>(SEGMENT_DEFINITIONS.map((definition) => definition.code));
const SEGMENT_NAME_OF = new Map<SegmentCode, string>(SEGMENT_DEFINITIONS.map((definition) => [definition.code, definition.name]));
/** Every segment phrase (a full name, or an itm alias) as words, longest first. */
const SEGMENT_PHRASES: ReadonlyArray<{ words: readonly string[]; code: SegmentCode }> = [
  ...SEGMENT_DEFINITIONS.map((definition) => ({ words: words(definition.name), code: definition.code })),
  ...ITM_PHRASES.map((phrase) => ({ words: words(phrase), code: 'itm' as SegmentCode })),
].sort((a, b) => b.words.length - a.words.length);

function words(text: string): string[] {
  return text.toLowerCase().split(/\s+/).filter(Boolean);
}

/** The longest segment phrase starting at `tokens[at]`, or null. */
function segmentPhraseAt(tokens: readonly string[], at: number): { code: SegmentCode; length: number } | null {
  for (const phrase of SEGMENT_PHRASES) {
    if (phrase.words.length < 2 && !ITM_PHRASES.includes(phrase.words.join(' '))) continue;
    const slice = tokens.slice(at, at + phrase.words.length).map((token) => token.toLowerCase());
    if (slice.length === phrase.words.length && slice.every((token, index) => token === phrase.words[index])) {
      return { code: phrase.code, length: phrase.words.length };
    }
  }
  return null;
}

/** A segment named by its code or a one-word full name. */
function segmentWord(token: string): SegmentCode | null {
  const lower = token.toLowerCase();
  if (SEGMENT_CODES.has(lower)) return lower as SegmentCode;
  const named = SEGMENT_PHRASES.find((phrase) => phrase.words.length === 1 && phrase.words[0] === lower);
  return named?.code ?? null;
}

interface Bound {
  min?: number;
  max?: number;
  sources: string[];
}

/** score>=70 / score>70 / score<=90 / score<90 / score 70-90 (the range form is one token: score70-90 is not). */
function scoreBound(token: string, next: string | undefined): { bound: Bound; consumed: number } | null {
  const comparison = /^score(>=|>|<=|<)(\d{1,3})$/i.exec(token);
  if (comparison) {
    const value = Number(comparison[2]);
    const op = comparison[1];
    const bound = op === '>=' ? { min: value } : op === '>' ? { min: value + 1 } : op === '<=' ? { max: value } : { max: value - 1 };
    return { bound: { ...bound, sources: [token] }, consumed: 1 };
  }
  if (token.toLowerCase() === 'score' && next) {
    const range = /^(\d{1,3})-(\d{1,3})$/.exec(next);
    if (range) return { bound: { min: Number(range[1]), max: Number(range[2]), sources: [`${token} ${next}`] }, consumed: 2 };
  }
  return null;
}

function spreadBound(token: string): Bound | null {
  const match = /^(spread|bps)(>=|<=)(-?\d{1,4})$/i.exec(token);
  if (!match) return null;
  const value = Number(match[3]);
  return match[2] === '>=' ? { min: value, sources: [token] } : { max: value, sources: [token] };
}

function inLimits(value: number | undefined, limits: { min: number; max: number }): boolean {
  return value === undefined || (Number.isInteger(value) && value >= limits.min && value <= limits.max);
}

/** Parse the omnibox text. Pure: the same text always gives the same result. */
export function parseFilterGrammar(text: string): FilterGrammarResult {
  const tokens = text.split(/[\s,]+/).map((token) => token.trim()).filter(Boolean);
  const states: Array<{ code: string; source: string }> = [];
  const segments: Array<{ code: SegmentCode; source: string }> = [];
  const single = new Map<string, { value: string; label: string; display: string; source: string }>();
  const unrecognized: string[] = [];
  const score: Bound = { sources: [] };
  const spread: Bound = { sources: [] };

  const setSingle = (key: string, label: string, value: string, display: string, source: string) => {
    const current = single.get(key);
    if (current && current.value !== value) unrecognized.push(source);
    else if (!current) single.set(key, { value, label, display, source });
  };
  const addSegment = (code: SegmentCode, source: string) => {
    if (!segments.some((segment) => segment.code === code)) segments.push({ code, source });
  };
  // A second, different at-least (or at-most) for one dimension is not read.
  const mergeBound = (target: Bound, bound: Bound) => {
    const clashes = (bound.min !== undefined && target.min !== undefined && bound.min !== target.min)
      || (bound.max !== undefined && target.max !== undefined && bound.max !== target.max);
    if (clashes) {
      unrecognized.push(...bound.sources);
      return;
    }
    target.min ??= bound.min;
    target.max ??= bound.max;
    target.sources.push(...bound.sources);
  };

  for (let at = 0; at < tokens.length; at += 1) {
    const token = tokens[at];
    const lower = token.toLowerCase();
    // A segment phrase first: 'IN THE MONEY' is the segment, never Indiana.
    const phrase = segmentPhraseAt(tokens, at);
    if (phrase) {
      addSegment(phrase.code, tokens.slice(at, at + phrase.length).join(' '));
      at += phrase.length - 1;
      continue;
    }
    if (/^[A-Z]{2}$/.test(token) && USPS_STATE_CODES.has(token)) {
      if (!states.some((state) => state.code === token)) states.push({ code: token, source: token });
      continue;
    }
    if (lower.startsWith('segment:')) {
      const rest = token.slice('segment:'.length);
      const forced = rest ? segmentPhraseAt([rest, ...tokens.slice(at + 1)], 0) : null;
      const direct = rest ? segmentWord(rest) : null;
      if (forced) {
        addSegment(forced.code, tokens.slice(at, at + forced.length).join(' '));
        at += forced.length - 1;
      } else if (direct) {
        addSegment(direct, token);
      } else {
        unrecognized.push(token);
      }
      continue;
    }
    if (lower.startsWith('product:')) {
      const product = PRODUCT_WORDS[lower.slice('product:'.length)];
      if (product) setSingle('product', 'PRODUCT', product, product, token);
      else unrecognized.push(token);
      continue;
    }
    if (PRODUCT_WORDS[lower]) {
      setSingle('product', 'PRODUCT', PRODUCT_WORDS[lower], PRODUCT_WORDS[lower], token);
      continue;
    }
    const segment = segmentWord(token);
    if (segment) {
      addSegment(segment, token);
      continue;
    }
    if (APPROVAL_WORDS.has(lower)) {
      setSingle('approval_status', 'APPROVAL', lower, lower.charAt(0).toUpperCase() + lower.slice(1), token);
      continue;
    }
    if (OUTREACH_WORDS.has(lower)) {
      setSingle('outreach_status', 'OUTREACH', lower, lower.charAt(0).toUpperCase() + lower.slice(1), token);
      continue;
    }
    if (RELATIONSHIP_WORDS[lower]) {
      setSingle('lender_relationship', 'RELATIONSHIP', RELATIONSHIP_WORDS[lower], RELATIONSHIP_WORDS[lower], token);
      continue;
    }
    const aged = /^aged>(\d+)$/i.exec(token);
    if (aged && AGED_DAYS.has(aged[1])) {
      setSingle('aged_days', 'AGING', aged[1], `Aged >${aged[1]}d`, token);
      continue;
    }
    const scored = scoreBound(token, tokens[at + 1]);
    if (scored) {
      mergeBound(score, scored.bound);
      at += scored.consumed - 1;
      continue;
    }
    const spreadRead = spreadBound(token);
    if (spreadRead) {
      mergeBound(spread, spreadRead);
      continue;
    }
    unrecognized.push(token);
  }

  const params = new URLSearchParams();
  const chips: FilterGrammarChip[] = [];
  const sortedStates = states.map((state) => state.code).sort();
  if (sortedStates.length === 1) params.set('state', sortedStates[0]);
  else if (sortedStates.length > 1) params.set('states', sortedStates.join(','));
  for (const state of states) chips.push({ key: sortedStates.length === 1 ? 'state' : 'states', label: 'STATE', value: state.code, source: state.source });
  if (segments.length === 1) params.set('segment', segments[0].code);
  else if (segments.length > 1) {
    params.set('segment_codes', segments.map((segment) => segment.code).join(','));
    params.set('segment_mode', 'any');
  }
  for (const segment of segments) {
    chips.push({ key: segments.length === 1 ? 'segment' : 'segment_codes', label: 'SEGMENT', value: SEGMENT_NAME_OF.get(segment.code) ?? segment.code, source: segment.source });
  }
  for (const key of ['product', 'lender_relationship', 'approval_status', 'outreach_status', 'aged_days']) {
    const reading = single.get(key);
    if (!reading) continue;
    params.set(key, reading.value);
    chips.push({ key, label: reading.label, value: reading.display, source: reading.source });
  }
  const emitBound = (bound: Bound, dimension: 'opportunity_score' | 'rate_spread_bps', limits: { min: number; max: number }, label: string) => {
    if (bound.sources.length === 0) return;
    const valid = inLimits(bound.min, limits) && inLimits(bound.max, limits)
      && !(bound.min !== undefined && bound.max !== undefined && bound.min > bound.max);
    if (!valid) {
      unrecognized.push(...bound.sources);
      return;
    }
    if (bound.min !== undefined) params.set(`min_${dimension}`, String(bound.min));
    if (bound.max !== undefined) params.set(`max_${dimension}`, String(bound.max));
    const value = bound.min !== undefined && bound.max !== undefined
      ? `${bound.min}–${bound.max}`
      : bound.min !== undefined ? `≥ ${bound.min}` : `≤ ${bound.max}`;
    chips.push({ key: `${dimension}`, label, value, source: bound.sources.join(' ') });
  };
  emitBound(score, 'opportunity_score', SCORE_LIMITS, 'SCORE');
  emitBound(spread, 'rate_spread_bps', SPREAD_LIMITS, 'RATE SPREAD');
  return { params, chips, unrecognized };
}
