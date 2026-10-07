/**
 * @vitest-environment happy-dom
 *
 * The explorer's facet-fed menus (W5d, tables-10). GET /audit/facets is a
 * RECORDED ledger read, so the provider reads only on an explicit menu open
 * for the applied window; a window change disarms the read until the next
 * open, and a closed menu leaves the query disabled.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FilterSelect } from '../ui/FilterSelect';

const calls = vi.hoisted(() => [] as Array<{ since: string | null; until: string | null }>);

vi.mock('../../lib/apiClients/auditExplorer', () => ({
  auditFacets: (window: { since: string | null; until: string | null }) => {
    calls.push(window);
    return Promise.resolve({
      event_types: [{ value: 'APPROVE', count: 4 }],
      actions: [{ value: 'outreach.approve', count: 4 }],
      actors: [],
      truncated: { event_types: false, actions: false, actors: false },
      since: window.since ?? '2026-07-01T00:00:00Z',
      until: window.until,
    });
  },
}));

import { ACTION_FILTER_LABEL, ALL_ACTIONS_OPTION, AuditFacetsProvider, actionFilterOptions } from './AdminAuditExplorer.facets';
import { auditDayBoundary } from './AdminAuditExplorer.params';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;

function render(since: string) {
  act(() => root.render(
    <QueryClientProvider client={client}>
      <AuditFacetsProvider since={since} until="" eventType="">
        <FilterSelect label={ACTION_FILTER_LABEL} value={ALL_ACTIONS_OPTION} options={[ALL_ACTIONS_OPTION]} onChange={() => undefined} />
        <FilterSelect label="State" value="All" options={['All', 'TX']} onChange={() => undefined} />
      </AuditFacetsProvider>
    </QueryClientProvider>,
  ));
}

const trigger = (label: string) => [...container.querySelectorAll<HTMLButtonElement>('button.filter')]
  .find((button) => button.getAttribute('aria-label')?.startsWith(`${label}:`))!;
const settle = () => act(async () => {
  await new Promise((resolve) => setTimeout(resolve, 0));
});

beforeEach(() => {
  calls.length = 0;
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  client.clear();
});

describe('AuditFacetsProvider', () => {
  it('reads nothing on mount, and nothing for a menu it does not count', async () => {
    render('2026-07-01');
    act(() => trigger('State').click());
    await settle();
    expect(calls).toEqual([]);
  });

  it('reads on an explicit open for the applied window; a window change disarms it until the next open', async () => {
    render('2026-07-01');
    act(() => trigger(ACTION_FILTER_LABEL).click());
    await settle();
    expect(calls).toEqual([{ since: auditDayBoundary('2026-07-01', false), until: null }]);
    act(() => trigger(ACTION_FILTER_LABEL).click());

    render('2026-07-02');
    await settle();
    expect(calls).toHaveLength(1);

    act(() => trigger(ACTION_FILTER_LABEL).click());
    await settle();
    expect(calls).toHaveLength(2);
    expect(calls[1].since).toBe(auditDayBoundary('2026-07-02', false));
  });
});

describe('actionFilterOptions', () => {
  it('lists facet actions most frequent first, drops what the URL refuses and keeps the applied one', () => {
    expect(actionFilterOptions(
      [
        { value: 'leads.view', count: 2 },
        { value: 'outreach.approve', count: 8 },
        { value: 'DROP TABLE', count: 99 },
      ],
      'approval_request.create',
    )).toEqual([ALL_ACTIONS_OPTION, 'outreach.approve', 'leads.view', 'approval_request.create']);
    expect(actionFilterOptions(null, '')).toEqual([ALL_ACTIONS_OPTION]);
  });
});
