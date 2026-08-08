/**
 * @vitest-environment happy-dom
 *
 * Pins the Sales ops snapshot's relocation into a dedicated Analytics tab
 * (2026-07-10): the tab is URL-addressable via `?view=sales-ops`, the moved
 * cards render, and the analytics filter row is hidden on it (the snapshot is
 * not filterable).
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const apiMocks = vi.hoisted(() => ({
  salesTeam: vi.fn(),
  salesAging: vi.fn(),
  salesStandup: vi.fn(),
  salesConversion: vi.fn(),
  salesOutcomeSummary: vi.fn(),
}));

// The five analytics tabs use useWarmingUpRetry; stub it idle so they render
// loading placeholders without network. The Sales ops tab uses plain useQuery
// against the mocked api below, not this hook.
vi.mock('../lib/useWarmingUpRetry', () => ({
  useWarmingUpRetry: () => ({
    data: null,
    warmingUp: null,
    error: null,
    isFetching: false,
    manualRetry: vi.fn(),
  }),
}));

vi.mock('../lib/configOptionsQuery', () => ({
  useConfigOptionsQuery: () => ({ data: { target_lender_refs: ['All'] }, isError: false }),
}));

vi.mock('../components/FootprintProvider', () => ({
  useFootprint: () => ({
    ready: true,
    usingFallback: false,
    states: [
      { state_code: 'IL', state_name: 'Illinois', display_order: 1, is_default_state: true },
    ],
  }),
}));

vi.mock('../lib/api', () => ({
  api: apiMocks,
}));

import AnalyticsRoute from './analytics';

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

let root: Root;
let queryClient: QueryClient;

function renderAt(path: string): void {
  root.render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <AnalyticsRoute />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function salesOpsTabButton(): HTMLButtonElement | undefined {
  return [...document.querySelectorAll<HTMLButtonElement>('button[role="tab"]')].find(
    (btn) => btn.textContent?.includes('Sales ops'),
  );
}

describe('Analytics Sales ops tab', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity } },
    });
    apiMocks.salesTeam.mockResolvedValue([]);
    apiMocks.salesAging.mockResolvedValue([]);
    apiMocks.salesStandup.mockResolvedValue({
      calls_logged: 0,
      contacts_reached: 0,
      callbacks_scheduled: 0,
      applications_started: 0,
    });
    apiMocks.salesConversion.mockResolvedValue({ rows: [] });
    apiMocks.salesOutcomeSummary.mockResolvedValue({
      total_outcomes: 0,
      applications_submitted: 0,
      closed_funded: 0,
      lost_to_competitor: 0,
      withdrawn: 0,
      not_qualified: 0,
      by_source_system: [],
      source_statuses: [
        {
          source_system: 'salesforce',
          display_name: 'Salesforce CRM',
          status: 'not_configured',
          configured: false,
          outcome_count: 0,
        },
      ],
      by_lo: [],
      top_competitors: [],
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  it('selects the Sales ops tab from the ?view=sales-ops deep link and hides the filter row', async () => {
    await act(async () => {
      renderAt('/analytics?view=sales-ops');
    });
    await settle();

    expect(salesOpsTabButton()?.getAttribute('aria-selected')).toBe('true');
    // The moved snapshot renders.
    expect(document.body.textContent).toContain('Sales ops snapshot');
    expect(document.body.textContent).toContain('Stale approved');
    expect(document.body.textContent).toContain('Yesterday standup');
    expect(document.body.textContent).toContain('Week-to-date conversion');
    expect(document.body.textContent).toContain('Closed-loop outcomes');
    // Compressed-but-honest outcome ledger caption.
    expect(document.body.textContent).toContain('Imported, read-only outcome ledger');
    expect(document.body.textContent).toContain('Customer CRM/LOS/POS outcome feeds are not configured yet');
    // The analytics filter row is not filterable context here — it must be hidden.
    expect(document.querySelector('[aria-label="Analytics filters"]')).toBeNull();
  });

  it('shows LO display names in week-to-date conversion, falling back to the raw key', async () => {
    apiMocks.salesTeam.mockResolvedValue([
      {
        email: 'lo02@summit.example',
        display_label: 'Summit LO 02',
        role: 'loan_officer',
        capacity_per_day: 25,
        active: true,
      },
    ]);
    apiMocks.salesConversion.mockResolvedValue({
      rows: [
        { group_key: 'lo02@summit.example', application_start_rate: 0.4 },
        { group_key: 'manager@summit.example', application_start_rate: 0.1 },
      ],
    });

    await act(async () => {
      renderAt('/analytics?view=sales-ops');
    });
    await settle();

    const card = [...document.querySelectorAll('.sales-ops-card')].find((node) =>
      node.textContent?.includes('Week-to-date conversion'),
    );
    expect(card?.textContent).toContain('Summit LO 02');
    expect(card?.textContent).not.toContain('lo02@summit.example');
    // The raw key stays reachable as the title so the row is still traceable.
    expect(
      [...(card?.querySelectorAll('[title]') ?? [])].map((node) => node.getAttribute('title')),
    ).toContain('lo02@summit.example');
    // No roster entry -> render the key, never an invented name.
    expect(card?.textContent).toContain('manager@summit.example');
  });

  it('headlines the outcome window total so the card cannot contradict its subline', async () => {
    // Live shape observed 2026-08-08: 8 submitted, nothing funded yet. The
    // card used to headline `closed_funded` — a bold 0 above "8 submitted".
    apiMocks.salesOutcomeSummary.mockResolvedValue({
      total_outcomes: 8,
      applications_submitted: 8,
      closed_funded: 0,
      lost_to_competitor: 0,
      withdrawn: 0,
      not_qualified: 0,
      by_source_system: [],
      source_statuses: [],
      by_lo: [],
      top_competitors: [],
    });

    await act(async () => {
      renderAt('/analytics?view=sales-ops');
    });
    await settle();

    const card = [...document.querySelectorAll('.sales-ops-card')].find((node) =>
      node.textContent?.includes('Closed-loop outcomes'),
    );
    expect(card?.querySelector('.kpi__value')?.textContent).toBe('8');
    // Funded is stated explicitly in the breakdown, never implied by the
    // headline.
    expect(card?.textContent).toContain('0 funded');
    expect(card?.textContent).toContain('8 submitted');
  });

  it('renders skeletons, never zeros, while the snapshot queries are in flight', async () => {
    // Hold every sales-ops request open: this is the window in which the
    // page used to claim "STALE APPROVED 0" from its `?? []` fallback.
    const never = new Promise<never>(() => {});
    apiMocks.salesAging.mockReturnValue(never);
    apiMocks.salesStandup.mockReturnValue(never);
    apiMocks.salesConversion.mockReturnValue(never);
    apiMocks.salesOutcomeSummary.mockReturnValue(never);
    apiMocks.salesTeam.mockReturnValue(never);

    await act(async () => {
      renderAt('/analytics?view=sales-ops');
    });
    await settle();

    const cards = [...document.querySelectorAll('.sales-ops-card')];
    expect(cards.length).toBe(4);
    for (const card of cards) {
      expect(card.getAttribute('aria-busy')).toBe('true');
      expect(card.querySelector('.skeleton')).toBeTruthy();
      // No headline number at all while loading — not even a zero.
      expect(card.querySelector('.kpi__value')).toBeNull();
    }
    // Claims about the window stay unrendered until the window is read.
    expect(document.body.textContent).not.toContain('No LO dispositions logged this week');
    expect(document.body.textContent).not.toContain('outcome feeds are not configured yet');
    expect(document.body.textContent).toContain('Loading roster…');
  });

  it('renders the filter row on analytical tabs and hides it after clicking Sales ops', async () => {
    await act(async () => {
      renderAt('/analytics');
    });
    await settle();

    // Executive (default) shows the analytics filter row and no snapshot.
    expect(document.querySelector('[aria-label="Analytics filters"]')).toBeTruthy();
    expect(document.body.textContent).not.toContain('Sales ops snapshot');

    await act(async () => {
      salesOpsTabButton()?.click();
    });
    await settle();

    expect(salesOpsTabButton()?.getAttribute('aria-selected')).toBe('true');
    expect(document.body.textContent).toContain('Sales ops snapshot');
    expect(document.querySelector('[aria-label="Analytics filters"]')).toBeNull();
  });
});
