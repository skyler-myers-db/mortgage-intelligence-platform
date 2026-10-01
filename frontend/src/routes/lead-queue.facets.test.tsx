/**
 * @vitest-environment happy-dom
 *
 * Lead Queue facet counts (audit tables-06): read only on an explicit open,
 * never on mount, hover or a filter change while closed; a reopen within
 * 60 s is served from cache. The count is aria-hidden and describes its
 * option, so the option's name stays its label.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { MemoryRouter, useSearchParams } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FilterSelect } from '../components/ui/FilterSelect';
import { mount } from '../test/render';
import type { LeadFacetsResponse } from '../types/leadFilters';
import { LeadQueueFilterBar } from './lead-queue.filterBar';
import { facetCountForOption } from './lead-queue.facets';

vi.mock('../components/AppContext', () => ({
  useApp: () => ({ actorEmail: 'lo.one@summit.example', canAccessAdmin: false }),
}));

vi.mock('../lib/configOptionsQuery', () => {
  const STABLE = { data: { target_lender_refs: ['All', 'Competitor B'] }, isError: false };
  return { useConfigOptionsQuery: () => STABLE };
});

const STATE_FACETS: LeadFacetsResponse = {
  dimension: 'state',
  total_matching: 1234,
  buckets: [{ value: 'IL', count: 1000 }, { value: 'TX', count: 234 }],
};

let facetCalls: string[] = [];
let failFacets = false;

function Harness() {
  const [, setSearchParams] = useSearchParams();
  return (
    <>
      <LeadQueueFilterBar
        filtersActive={false}
        onClearAll={() => undefined}
        moreActiveCount={0}
        core={(
          <>
            <FilterSelect label="STATE" value="All states" options={['All states', 'IL', 'TX', 'WA']} onChange={() => undefined} />
            <FilterSelect label="RELATIONSHIP" value="All" options={['All', 'Current customer']} onChange={() => undefined} />
          </>
        )}
        more={null}
      />
      <button type="button" data-testid="set-product" onClick={() => setSearchParams(new URLSearchParams('state=IL&product=HELOC'))}>
        product
      </button>
    </>
  );
}

async function mountAt(query: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await mount(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/lead-queue${query}`]}>
        <Harness />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const trigger = (label: string) =>
  document.querySelector<HTMLButtonElement>(`button[aria-haspopup="listbox"][aria-label^="${label}:"]`)!;
const listbox = () => document.querySelector<HTMLUListElement>('[role="listbox"]');

async function settle() {
  await act(async () => {
    for (let i = 0; i < 5; i += 1) await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

async function toggle(label: string) {
  await act(async () => trigger(label).click());
  await settle();
}

beforeEach(() => {
  facetCalls = [];
  failFacets = false;
  vi.stubGlobal('fetch', async (path: string) => {
    if (path.includes('/leads/facets')) facetCalls.push(path);
    if (failFacets) return new Response(JSON.stringify({ detail: 'down' }), { status: 500 });
    return new Response(JSON.stringify(STATE_FACETS), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Lead Queue facet counts', () => {
  it('reads nothing on mount or hover, once on open, and not again on a reopen', async () => {
    await mountAt('?state=IL&segment=itm');
    expect(facetCalls).toEqual([]);
    await act(async () => {
      trigger('STATE').dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
      trigger('STATE').dispatchEvent(new PointerEvent('pointerenter'));
    });
    await settle();
    expect(facetCalls).toEqual([]);

    await toggle('STATE');
    expect(facetCalls).toHaveLength(1);
    const query = new URL(facetCalls[0], 'http://queue.test').searchParams;
    expect(query.get('dimension')).toBe('state');
    // The dimension's own filter is dropped; every other filter stays.
    expect(query.has('state')).toBe(false);
    expect(query.get('segment')).toBe('itm');
    expect(query.has('limit')).toBe(false);

    await toggle('STATE');
    await toggle('STATE');
    expect(facetCalls).toHaveLength(1);
  });

  it('never counts a borrower list: the menu opens with labels only and reads nothing', async () => {
    await mountAt('?borrower_ids=B-0123456789ABC,B-0123456789ABD&segment=itm');
    await toggle('STATE');
    expect(listbox()).not.toBeNull();
    expect(facetCalls).toEqual([]);
    expect(listbox()!.getAttribute('aria-busy')).not.toBe('true');
    expect(listbox()!.querySelector('[aria-hidden="true"].filter-menu__count, .filter-menu__count')).toBeNull();
  });

  it('never reads for an uncounted menu, and a change while closed reads nothing', async () => {
    await mountAt('');
    await toggle('RELATIONSHIP');
    expect(listbox()).not.toBeNull();
    expect(facetCalls).toEqual([]);
    await toggle('RELATIONSHIP');
    await act(async () => document.querySelector<HTMLButtonElement>('[data-testid="set-product"]')!.click());
    await settle();
    expect(facetCalls).toEqual([]);
    await toggle('STATE');
    expect(facetCalls).toHaveLength(1);
    expect(new URL(facetCalls[0], 'http://queue.test').searchParams.get('product')).toBe('HELOC');
  });

  it('describes each option with an aria-hidden count and keeps the label as its name', async () => {
    await mountAt('');
    await toggle('STATE');
    const options = [...listbox()!.querySelectorAll<HTMLLIElement>('[role="option"]')];
    expect(options.map((option) => option.firstChild?.textContent)).toEqual(['All states', 'IL', 'TX', 'WA']);
    expect(options.map((option) => option.querySelector('.filter-menu__count')?.textContent)).toEqual([
      '1,234', '1,000', '234', '0',
    ]);
    for (const option of options) {
      const count = option.querySelector('.filter-menu__count')!;
      expect(count.getAttribute('aria-hidden')).toBe('true');
      expect(option.getAttribute('aria-describedby')).toBe(count.id);
    }
    expect(listbox()!.getAttribute('aria-busy')).toBeNull();
  });

  it('marks the listbox busy while the counts load', async () => {
    vi.stubGlobal('fetch', (path: string) => {
      facetCalls.push(path);
      return new Promise<Response>(() => undefined);
    });
    await mountAt('');
    await toggle('STATE');
    expect(facetCalls).toHaveLength(1);
    expect(listbox()!.getAttribute('aria-busy')).toBe('true');
    expect(listbox()!.querySelector('.filter-menu__count')).toBeNull();
  });

  it('shows a dash and a polite Counts unavailable when the read fails', async () => {
    failFacets = true;
    await mountAt('');
    await toggle('STATE');
    const options = [...listbox()!.querySelectorAll<HTMLLIElement>('[role="option"]')];
    expect(options.map((option) => option.querySelector('.filter-menu__count')?.textContent)).toEqual(['—', '—', '—', '—']);
    expect(options.every((option) => option.getAttribute('aria-describedby') === null)).toBe(true);
    const status = document.querySelector('[role="status"].sr-only');
    expect(status?.textContent).toBe('Counts unavailable');
  });

  it('maps options to buckets per dimension', () => {
    const segment: LeadFacetsResponse = { dimension: 'segment', total_matching: 9, buckets: [{ value: 'itm', count: 4 }] };
    expect(facetCountForOption('segment', segment, 'All segments')).toBe(9);
    expect(facetCountForOption('segment', segment, 'Prime Refi Candidates')).toBe(4);
    expect(facetCountForOption('segment', segment, '2 segments selected (any selected)')).toBeNull();
    const approval: LeadFacetsResponse = { dimension: 'approval', total_matching: 5, buckets: [{ value: 'pending', count: 5 }] };
    expect(facetCountForOption('approval', approval, 'Any approval')).toBe(5);
    expect(facetCountForOption('approval', approval, 'Pending')).toBe(5);
    expect(facetCountForOption('approval', approval, 'Hold')).toBe(0);
    const product: LeadFacetsResponse = { dimension: 'product', total_matching: 3, buckets: [{ value: 'HELOC', count: 3 }] };
    expect(facetCountForOption('product', product, 'HELOC')).toBe(3);
    expect(facetCountForOption('state', STATE_FACETS, '2 states selected')).toBeNull();
  });
});

describe('FilterSelect without a counts provider', () => {
  it('renders no count, no busy state and no status region', async () => {
    await mount(<FilterSelect label="STATE" value="All states" options={['All states', 'IL']} onChange={() => undefined} />);
    await act(async () => trigger('STATE').click());
    const menu = listbox()!;
    expect(menu.querySelector('.filter-menu__count')).toBeNull();
    expect(menu.getAttribute('aria-busy')).toBeNull();
    expect([...menu.querySelectorAll('[role="option"]')].every((option) => !option.hasAttribute('aria-describedby'))).toBe(true);
    expect(document.querySelector('[role="status"]')).toBeNull();
    expect(facetCalls).toEqual([]);
  });
});
