/**
 * The preview criteria are the wire's closed PortfolioCriteria (audit
 * quality-04 P2, W5b M4a, W5e w5-wire-types A4). buildPreviewCriteria keeps
 * the filters' insertion order and only keys the wire declares, then appends
 * the states, so for every filter set the page can reach the JSON is
 * byte-identical to the open Record it replaced: JSON.stringify of it is the
 * campaign draft key (useCampaignDraft), and a reordered string would drop
 * every restored draft.
 */
import { describe, expect, it } from 'vitest';
import {
  BASE_DEFAULT_FILTERS,
  buildPreviewCriteria,
  CRITERIA_TEXT_KEYS,
  NON_GEO_FILTER_GROUPS,
  parseFiltersFromUrl,
  URL_FILTER_KEYS,
  type CriteriaTextKey,
  type PortfolioCriteria,
} from './portfolio-builder.logic';

/** The pre-A4 builder, verbatim: an open Record spread from the filters. */
function legacyCriteria(filters: Record<string, string>, stateCodes: readonly string[]): Record<string, unknown> {
  const criteria: Record<string, unknown> = { ...filters };
  if (stateCodes.length > 0) criteria.states = [...stateCodes];
  return criteria;
}

const STATE_SETS: ReadonlyArray<readonly string[]> = [[], ['TX'], ['CA', 'TX', 'FL']];

/** Every filter set the route can commit: the defaults, each option of each control, URL round-trips. */
function reachableFilterSets(): Record<string, string>[] {
  const sets: Record<string, string>[] = [{}, { ...BASE_DEFAULT_FILTERS }];
  for (const group of NON_GEO_FILTER_GROUPS) {
    for (const option of group.options) sets.push({ ...BASE_DEFAULT_FILTERS, [group.key]: option });
  }
  sets.push({ ...BASE_DEFAULT_FILTERS, target_lender_ref: 'competitor_lender_a' });
  const everyKey: Record<string, string> = {};
  for (const key of [...URL_FILTER_KEYS].reverse()) everyKey[key] = `${key}-value`;
  sets.push(everyKey);
  const query = new URLSearchParams({ occupancy: 'Non-owner-occupied', recency: 'Untouched 60d', product: 'HELOC' });
  sets.push(parseFiltersFromUrl(query, BASE_DEFAULT_FILTERS));
  return sets;
}

describe('buildPreviewCriteria (closed PortfolioCriteria)', () => {
  it('serializes every reachable filter set byte-identically to the open Record it replaced', () => {
    let compared = 0;
    for (const filters of reachableFilterSets()) {
      for (const states of STATE_SETS) {
        expect(JSON.stringify(buildPreviewCriteria(filters, states))).toBe(JSON.stringify(legacyCriteria(filters, states)));
        compared += 1;
      }
    }
    expect(compared).toBeGreaterThan(100);
  });

  it('keeps the filters in insertion order and appends the states last', () => {
    const criteria = buildPreviewCriteria({ recency: 'Any', occupancy: 'All', consent_status: 'Opt-in' }, ['TX']);

    expect(Object.keys(criteria)).toEqual(['recency', 'occupancy', 'consent_status', 'states']);
  });

  it('drops a key the wire does not declare (PortfolioCriteria is extra=forbid)', () => {
    const criteria = buildPreviewCriteria({ occupancy: 'All', not_a_criterion: 'x', min_equity_pct: '15' }, []);

    expect(criteria).toEqual({ occupancy: 'All' });
  });

  it('names every string-valued criterion exactly once', () => {
    expect(new Set(CRITERIA_TEXT_KEYS).size).toBe(CRITERIA_TEXT_KEYS.length);
    for (const key of URL_FILTER_KEYS) expect(CRITERIA_TEXT_KEYS).toContain(key);
  });

  it('pins the closed key set at compile time', () => {
    // Every URL filter key is a declared criterion.
    const urlKeysAreCriteria: URL_FILTER_KEYS_ARE_CRITERIA = true;
    // The text-key list covers every string-valued criterion the wire declares.
    const listIsComplete: [Exclude<CriteriaTextKey, (typeof CRITERIA_TEXT_KEYS)[number]>] extends [never] ? true : false = true;
    // @ts-expect-error a key the wire does not declare is a compile error on the closed criteria
    const planted: PortfolioCriteria = { occupancy: 'All', not_a_criterion: 'x' };

    expect([urlKeysAreCriteria, listIsComplete, Object.keys(planted).length]).toEqual([true, true, 2]);
  });
});

type URL_FILTER_KEYS_ARE_CRITERIA = (typeof URL_FILTER_KEYS)[number] extends keyof PortfolioCriteria ? true : false;
