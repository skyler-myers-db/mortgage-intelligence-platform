/**
 * @vitest-environment happy-dom
 *
 * Lead Queue filter bar and column preset (audit tables-06, tables-05): the
 * core pills stay visible, the rest sit behind an inline "More filters"
 * disclosure, active non-core filters are removable hero chips, Clear all
 * drops every filter but keeps `?view=`, and the preset reaches LeadTable.
 * The rendered heights and the URL round trip in a real browser are proven in
 * tests/e2e/fixture/queue-layout.fixture.spec.ts.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const retryState = vi.hoisted(() => ({
  data: { leads: [], totalMatching: 0, returnedRows: 0, truncatedAt: null },
  warmingUp: null,
  error: null,
  manualRetry: () => undefined,
}));

const apiMocks = vi.hoisted(() => ({
  salesTeam: () => Promise.resolve([]),
  portfolioPreview: () => Promise.resolve({ data_refreshed_at: null }),
  adminRules: () => Promise.resolve({ offer_rules_version: null }),
}));

vi.mock('../components/AppContext', () => ({
  useApp: () => ({ canAccessAdmin: false }),
}));

vi.mock('../lib/useWarmingUpRetry', () => ({
  useWarmingUpRetry: () => retryState,
}));

vi.mock('../lib/configOptionsQuery', () => {
  const STABLE = { data: { target_lender_refs: ['All', 'Competitor B'] }, isError: false };
  return { useConfigOptionsQuery: () => STABLE };
});

vi.mock('../components/FootprintProvider', () => {
  const STABLE = {
    ready: true,
    usingFallback: false,
    states: [{ state_code: 'IL', state_name: 'Illinois', display_order: 1, is_default_state: true }],
  };
  return { useFootprint: () => STABLE };
});

vi.mock('../components/mortgage/LeadTable', () => ({
  LeadTable: ({ view, onViewChange, fillHeight }: {
    view?: string;
    onViewChange?: (next: 'default' | 'sales-ops') => void;
    fillHeight?: boolean;
  }) => (
    <div data-testid="lead-table" data-view={view} data-fill={String(Boolean(fillHeight))}>
      <button type="button" data-testid="to-sales-ops" onClick={() => onViewChange?.('sales-ops')}>sales ops</button>
      <button type="button" data-testid="to-default" onClick={() => onViewChange?.('default')}>default</button>
    </div>
  ),
}));

vi.mock('../lib/api', () => ({
  ApiError: class ApiError extends Error {},
  api: apiMocks,
}));

import LeadQueue from './lead-queue';

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.search}</output>;
}

const search = () => document.querySelector('[data-testid="location"]')?.textContent ?? '';
const byTestId = <T extends HTMLElement = HTMLElement>(id: string) =>
  document.querySelector(`[data-testid="${id}"]`) as T | null;

describe('Lead Queue filter bar', () => {
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    document.body.innerHTML = '';
  });

  async function mountAt(url: string) {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={[url]}>
            <LeadQueue />
            <LocationProbe />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
  }

  it('keeps the core pills in the row and the rest behind a collapsed inline panel', async () => {
    await mountAt('/lead-queue');
    const row = document.querySelector('[role="group"][aria-label="Queue filters"]') as HTMLElement;
    const labels = [...row.querySelectorAll('button[aria-haspopup="listbox"]')].map(
      (button) => button.getAttribute('aria-label')?.split(':')[0],
    );
    expect(labels).toEqual(['STATE', 'SEGMENT', 'RELATIONSHIP', 'PRODUCT', 'APPROVAL']);

    const toggle = byTestId<HTMLButtonElement>('lead-queue-more-filters')!;
    const panel = document.getElementById(toggle.getAttribute('aria-controls') ?? '') as HTMLElement;
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(panel.hidden).toBe(true);
    expect([...panel.querySelectorAll('button[aria-haspopup="listbox"]')].map(
      (button) => button.getAttribute('aria-label')?.split(':')[0],
    )).toEqual([
      'TARGET LIEN HOLDER', 'OWNER LINK', 'PURCHASE INTENT', 'PRODUCT TYPE', 'CHANNEL',
      'CONTACTABILITY', 'CONSENT', 'RECENCY', 'OUTREACH', 'ASSIGNED', 'AGING',
    ]);

    await act(async () => toggle.click());
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(panel.hidden).toBe(false);
    // Nothing active: no hero chips, Clear all disabled.
    expect(byTestId('lead-queue-active-filters')?.classList.contains('is-empty')).toBe(true);
    expect(byTestId<HTMLButtonElement>('lead-queue-clear-all')?.disabled).toBe(true);
  });

  it('lists active non-core filters as removable hero chips and removes one param at a time', async () => {
    await mountAt(
      '/lead-queue?state=IL&owner_link=Portfolio+investor+%285%2B%29&purchase_intent=HELOC+intent'
      + '&cohort_id=11111111-1111-1111-1111-111111111111&outreach_status=sent',
    );
    const hero = byTestId('lead-queue-active-filters')!;
    const removeLabels = [...hero.querySelectorAll('button')].map((button) => button.getAttribute('aria-label'));
    expect(removeLabels).toEqual([
      'Remove OWNER LINK: Portfolio investor (5+) filter',
      'Remove PURCHASE INTENT: HELOC intent filter',
      'Remove OUTREACH: Sent filter',
      'Remove COHORT: Genie cohort filter',
    ]);
    // Core filters are shown by their highlighted pill, not repeated here.
    expect(hero.textContent).not.toContain('STATE');
    expect(byTestId('lead-queue-more-filters')?.getAttribute('aria-label')).toBe('More filters (3 active)');

    await act(async () => (hero.querySelector('button') as HTMLButtonElement).click());
    const params = new URLSearchParams(search());
    expect(params.has('owner_link')).toBe(false);
    expect(params.get('purchase_intent')).toBe('HELOC intent');
    expect(params.get('state')).toBe('IL');
  });

  it('hands focus to the next Remove button when a hero chip goes, and to More filters after the last one', async () => {
    await mountAt('/lead-queue?owner_link=Portfolio+investor+%285%2B%29&purchase_intent=HELOC+intent&outreach_status=sent');
    const removes = () => [...byTestId('lead-queue-active-filters')!.querySelectorAll<HTMLButtonElement>('.filter__remove')];
    const label = (button: Element | null) => button?.getAttribute('aria-label');

    // Remove the middle chip: the one that slides into its place takes focus.
    removes()[1].focus();
    await act(async () => removes()[1].click());
    expect(removes().map(label)).toEqual([
      'Remove OWNER LINK: Portfolio investor (5+) filter',
      'Remove OUTREACH: Sent filter',
    ]);
    expect(label(document.activeElement)).toBe('Remove OUTREACH: Sent filter');

    // Remove the last chip in the row: the one before it takes focus.
    await act(async () => removes()[1].click());
    expect(label(document.activeElement)).toBe('Remove OWNER LINK: Portfolio investor (5+) filter');

    // No chip left: focus lands on the More filters toggle, never on <body>.
    await act(async () => removes()[0].click());
    expect(removes()).toHaveLength(0);
    expect(document.activeElement).toBe(byTestId('lead-queue-more-filters'));
  });

  it('Clear all drops every filter but keeps the column preset', async () => {
    await mountAt('/lead-queue?view=sales-ops&state=IL&owner_link=Portfolio+investor+%285%2B%29');
    const clearAll = byTestId<HTMLButtonElement>('lead-queue-clear-all')!;
    expect(clearAll.disabled).toBe(false);
    await act(async () => clearAll.click());
    expect(search()).toBe('?view=sales-ops');
    expect(byTestId<HTMLButtonElement>('lead-queue-clear-all')?.disabled).toBe(true);
  });

  it('parses ?view= for LeadTable, writes it back, and sizes the scroller to the viewport', async () => {
    await mountAt('/lead-queue?view=sales-ops&state=IL');
    const table = byTestId('lead-table')!;
    expect(table.dataset.view).toBe('sales-ops');
    expect(table.dataset.fill).toBe('true');

    await act(async () => byTestId<HTMLButtonElement>('to-default')!.click());
    expect(search()).toBe('?state=IL');
    expect(byTestId('lead-table')?.dataset.view).toBe('default');

    await act(async () => byTestId<HTMLButtonElement>('to-sales-ops')!.click());
    expect(new URLSearchParams(search()).get('view')).toBe('sales-ops');
  });

  it('reads an unknown ?view= as the Default preset', async () => {
    await mountAt('/lead-queue?view=everything');
    expect(byTestId('lead-table')?.dataset.view).toBe('default');
  });
});
