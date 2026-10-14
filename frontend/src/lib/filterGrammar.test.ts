/**
 * The filter omnibox grammar (wow-power-6, W5e item 3a). Pinned against the
 * Lead Queue's own filter vocabulary (the route module is imported HERE, by
 * the test, never by lib/filterGrammar.ts): every emitted key is a known
 * queue param, every emitted value is in the matching option array or bound
 * range, and the queue's own share grammar keeps every emitted param
 * unchanged (so a reading is exactly a filter the queue applies).
 */
import { describe, expect, it } from 'vitest';
import { SEGMENT_DEFINITIONS } from './segmentMetadata';
import { LENDER_RELATIONSHIP_OPTIONS } from './lenderFilters';
import { parseFilterGrammar } from './filterGrammar';
import {
  AGING_FILTER_OPTIONS,
  APPROVAL_FILTER_OPTIONS,
  KNOWN_QUEUE_PARAMS,
  LEAD_BOUND_LIMITS,
  OUTREACH_FILTER_OPTIONS,
  PRODUCT_FILTER_OPTIONS,
  SEGMENT_CODES,
  leadQueueShareParams,
} from '../routes/lead-queue.filters';
import { leadQueueFilterInputFromSearchParams } from '../routes/lead-queue.request';

const SWEEP = [
  'TX IL heloc aged>7 zyrplax',
  'CA itm equity pending',
  'in the money sent current',
  'in-the-money former score>=70',
  'segment:retention product:refi competitor score 60-80',
  'cash-out cashout purchase',
  'listed investor permit approved hold',
  'retention score<90 spread>=50 bps<=400',
  'NY NJ PA queued replied bounced actioned rejected',
  'customer aged>14 aged>30 score>10',
  `${SEGMENT_DEFINITIONS[0].name} ${SEGMENT_DEFINITIONS[2].name.toUpperCase()} WA`,
];

function valuesAllowed(key: string, value: string): boolean {
  switch (key) {
    case 'state': return /^[A-Z]{2}$/.test(value);
    case 'states': return value.split(',').every((code) => /^[A-Z]{2}$/.test(code));
    case 'segment': return SEGMENT_CODES.has(value as never);
    case 'segment_codes': return value.split(',').every((code) => SEGMENT_CODES.has(code as never));
    case 'segment_mode': return value === 'any';
    case 'product': return (PRODUCT_FILTER_OPTIONS as readonly string[]).includes(value);
    case 'lender_relationship': return (LENDER_RELATIONSHIP_OPTIONS as readonly string[]).includes(value);
    case 'approval_status': return APPROVAL_FILTER_OPTIONS.map((option) => option.toLowerCase()).includes(value);
    case 'outreach_status': return OUTREACH_FILTER_OPTIONS.map((option) => option.toLowerCase()).includes(value);
    case 'aged_days': return AGING_FILTER_OPTIONS.includes(`Aged >${value}d` as never);
    case 'min_opportunity_score':
    case 'max_opportunity_score':
      return Number(value) >= LEAD_BOUND_LIMITS.opportunity_score.min && Number(value) <= LEAD_BOUND_LIMITS.opportunity_score.max;
    case 'min_rate_spread_bps':
    case 'max_rate_spread_bps':
      return Number(value) >= LEAD_BOUND_LIMITS.rate_spread_bps.min && Number(value) <= LEAD_BOUND_LIMITS.rate_spread_bps.max;
    default: return false;
  }
}

describe('the filter grammar against the queue vocabulary', () => {
  it.each(SWEEP)('emits only known keys and reviewed values the queue applies unchanged: %s', (text) => {
    const { params, chips } = parseFilterGrammar(text);
    expect(chips.length).toBeGreaterThan(0);
    for (const [key, value] of params) {
      expect(KNOWN_QUEUE_PARAMS, key).toContain(key);
      expect(valuesAllowed(key, value), `${key}=${value}`).toBe(true);
    }
    const shared = new URLSearchParams(leadQueueShareParams(params, leadQueueFilterInputFromSearchParams(params, [])).search);
    for (const [key, value] of params) expect(shared.get(key), `${key} survives the queue's own grammar`).toBe(value);
  });

  it('reads the acceptance example', () => {
    const result = parseFilterGrammar('TX IL heloc aged>7 zyrplax');
    expect(result.params.toString()).toBe('states=IL%2CTX&product=HELOC&aged_days=7');
    expect(result.chips.map((chip) => [chip.label, chip.value, chip.source])).toEqual([
      ['STATE', 'TX', 'TX'], ['STATE', 'IL', 'IL'], ['PRODUCT', 'HELOC', 'heloc'], ['AGING', 'Aged >7d', 'aged>7'],
    ]);
    expect(result.unrecognized).toEqual(['zyrplax']);
  });

  it('never reads a lower-case or mixed-case two-letter word as a state', () => {
    const result = parseFilterGrammar('in or me oh Tx tX IN');
    expect(result.params.toString()).toBe('state=IN');
    expect(result.unrecognized).toEqual(['in', 'or', 'me', 'oh', 'Tx', 'tX']);
  });

  it('reads a product word as the product, a full segment name as the segment, and forced readings as forced', () => {
    expect(parseFilterGrammar('retention').params.toString()).toBe('product=Retention');
    expect(parseFilterGrammar('segment:retention').params.toString()).toBe('segment=retention');
    expect(parseFilterGrammar(SEGMENT_DEFINITIONS.find((d) => d.code === 'permit')!.name).params.toString()).toBe('segment=permit');
    expect(parseFilterGrammar('heloc').params.toString()).toBe('product=HELOC');
    expect(parseFilterGrammar('product:cashout').params.toString()).toBe('product=Cash-out');
    expect(parseFilterGrammar('itm equity').params.toString()).toBe('segment_codes=itm%2Cequity&segment_mode=any');
    expect(parseFilterGrammar('IN THE MONEY').params.toString()).toBe('segment=itm');
  });

  it('reports an inverted or out-of-range bound and a second single value, never emits them', () => {
    expect(parseFilterGrammar('score>=90 score<=80')).toMatchObject({ unrecognized: ['score>=90', 'score<=80'] });
    expect(parseFilterGrammar('score>=90 score<=80').params.toString()).toBe('');
    expect(parseFilterGrammar('score>100').unrecognized).toEqual(['score>100']);
    expect(parseFilterGrammar('spread>=6000').unrecognized).toEqual(['spread>=6000']);
    expect(parseFilterGrammar('refi heloc').params.toString()).toBe('product=Refi');
    expect(parseFilterGrammar('refi heloc').unrecognized).toEqual(['heloc']);
    expect(parseFilterGrammar('score 70-90').params.toString()).toBe('min_opportunity_score=70&max_opportunity_score=90');
    expect(parseFilterGrammar('score>70 score<90').params.toString()).toBe('min_opportunity_score=71&max_opportunity_score=89');
  });

  it('never emits a ZIP, county, city, borrower id, cohort or email', () => {
    const result = parseFilterGrammar('60611 17031 Chicago B-0123456789ABC 0f8fad5b-d9cb-469f-a165-70867728950e lo@summit.example');
    expect(result.params.toString()).toBe('');
    expect(result.chips).toEqual([]);
    expect(result.unrecognized).toHaveLength(6);
  });
});
