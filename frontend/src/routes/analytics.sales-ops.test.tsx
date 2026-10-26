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
import { MemoryRouter, useLocation, useNavigationType } from 'react-router';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

const apiMocks = vi.hoisted(() => ({
  salesTeam: vi.fn(),
  salesAging: vi.fn(),
  salesStandup: vi.fn(),
  salesConversion: vi.fn(),
  /** The cohort (team) reads of GET /sales/conversion, apart from the per-officer one. */
  salesCohort: vi.fn(),
  salesOutcomeSummary: vi.fn(),
  /** GET /sales/workload (the tab's read; the lazy SalesWorkloadTable renders it). */
  salesWorkload: vi.fn(),
}));

vi.mock('../lib/apiClients/salesWorkload', () => ({
  fetchSalesWorkload: apiMocks.salesWorkload,
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

// The team row's KPI cards carry evidence chips (critic-10 6b), which read
// the app context; the drawer itself is not under test here.
vi.mock('../components/AppContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../components/AppContext')>()),
  useApp: () => ({ lender: 'Summit Mortgage', setDrawer: () => undefined, showEvidence: true }),
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

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: {
    ...apiMocks,
    // groupBy=cohort goes to its own mock, so the per-officer pins stay on salesConversion.
    salesConversion: (from: string, to: string, groupBy: 'lo' | 'cohort' = 'lo', signal?: AbortSignal) =>
      groupBy === 'cohort' ? apiMocks.salesCohort(from, to, signal) : apiMocks.salesConversion(from, to, groupBy, signal),
  },
}));

import AnalyticsRoute from './analytics';
import './analytics.sales-ops';
import { useOfferSalesTeam } from './offer-orchestrator.sales-team';
import { ApiError } from '../lib/apiTransport';
import { signedCount, signedPct } from '../lib/formatters';
import { queryKeys } from '../lib/queryKeys';
import { preloadDescribedError } from '../components/ui/DescribedError';

// The error vocabulary is its own chunk (loaded with the first failure):
// load it up front. The Sales ops tab is its own chunk too (W5e item 2b); the
// static import below puts it in the module graph before any test renders.
beforeAll(async () => {
  await preloadDescribedError();
});

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
    apiMocks.salesCohort.mockResolvedValue({ rows: [] });
    apiMocks.salesWorkload.mockResolvedValue({
      from_date: '2026-07-08',
      to_date: '2026-07-14',
      previous_from_date: '2026-07-01',
      previous_to_date: '2026-07-07',
      stale_days: 7,
      rows: [],
    });
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

  it('shows LO display names in week-to-date conversion, and a fixed label for an officer off the roster', async () => {
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
    // critic-08: the LO email is never put in the DOM as a title= attribute.
    expect(card?.querySelectorAll('[title]').length).toBe(0);
    expect(card?.innerHTML).not.toContain('lo02@summit.example');
    // No roster entry -> the fixed label, never the raw email and never an
    // invented name (W5e C1, lexicon item 19b): not as text, a title or an
    // accessible name anywhere on the page.
    expect(card?.textContent).toContain('Loan officer not on the current roster');
    expect(document.body.innerHTML).not.toContain('manager@summit.example');
  });

  it('Sales ops and Offer\'s assignment share one roster read and narrow it each their own way (runtime-06)', async () => {
    apiMocks.salesTeam.mockResolvedValue([
      { email: 'lo02@summit.example', display_label: 'Summit LO 02', role: 'loan_officer', capacity_per_day: 25, active: true },
      { email: 'sm01@summit.example', display_label: 'Summit SM 01', role: 'sales_manager', capacity_per_day: 5, active: true },
    ]);
    apiMocks.salesConversion.mockResolvedValue({
      rows: [
        { group_key: 'lo02@summit.example', application_start_rate: 0.4 },
        { group_key: 'sm01@summit.example', application_start_rate: 0.1 },
      ],
    });
    let reviewers: readonly string[] = [];
    function OfferProbe() {
      reviewers = useOfferSalesTeam().map((member) => member.email);
      return null;
    }
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/analytics?view=sales-ops']}>
            <AnalyticsRoute />
            <OfferProbe />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await settle();

    expect(apiMocks.salesTeam, 'one GET /sales/team for both surfaces').toHaveBeenCalledTimes(1);
    // Sales ops narrows to loan officers: the manager's row reads as off the roster.
    const card = [...document.querySelectorAll('.sales-ops-card')].find((node) =>
      node.textContent?.includes('Week-to-date conversion'),
    );
    expect(card?.textContent).toContain('Summit LO 02');
    expect(card?.textContent).not.toContain('Summit SM 01');
    // Offer narrows the same entry to active loan officers and sales managers.
    expect(reviewers).toEqual(['lo02@summit.example', 'sm01@summit.example']);
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

  // Audit states-04: failures read in the shared vocabulary, never the
  // transport message or the server's detail.
  it('says why each read failed without the transport message', async () => {
    const SENTINEL = 'SENTINEL 500 Internal Server Error';
    apiMocks.salesTeam.mockRejectedValue(new ApiError(SENTINEL, { path: '/api/v1/sales/team', status: 403 }));
    apiMocks.salesAging.mockRejectedValue(new ApiError(SENTINEL, { path: '/api/v1/sales/aging', status: 500 }));
    await act(async () => {
      renderAt('/analytics?view=sales-ops');
    });
    await settle();

    const alerts = [...document.querySelectorAll('[role="alert"]')].map((node) => node.textContent ?? '');
    expect(alerts).toContain('Sales team unavailable: Ask an administrator for access.');
    expect(alerts).toContain('Sales operations metrics unavailable: The server hit an unexpected error.');
    expect(document.body.textContent).not.toContain('SENTINEL');
    expect(document.body.textContent).not.toContain('Internal Server Error');
  });

  describe('the sales manager view (critic-10)', () => {
    const TEAM = Array.from({ length: 5 }, (_, index) => ({
      email: `lo0${index + 1}@summit.example`,
      display_label: `Summit LO 0${index + 1}`,
      role: 'loan_officer',
      capacity_per_day: 20,
      active: true,
    }));
    let location = '';
    let navigation = '';

    function LocationProbe() {
      const current = useLocation();
      location = `${current.pathname}${current.search}`;
      navigation = useNavigationType();
      return null;
    }

    function conversionCard(): Element | undefined {
      return [...document.querySelectorAll('.sales-ops-card')].find((node) =>
        node.querySelector('.eyebrow')?.textContent?.toLowerCase().includes('conversion'),
      );
    }

    function conversionCells(): Array<Array<string | null>> {
      return [...(conversionCard()?.querySelectorAll('.split-row') ?? [])].map((row) =>
        [...row.children].map((cell) => cell.textContent),
      );
    }

    async function renderView(path: string): Promise<void> {
      await act(async () => {
        root.render(
          <QueryClientProvider client={queryClient}>
            <MemoryRouter initialEntries={[path]}>
              <AnalyticsRoute />
              <LocationProbe />
            </MemoryRouter>
          </QueryClientProvider>,
        );
      });
      await settle();
    }

    function periodButton(label: string): HTMLButtonElement | undefined {
      return [...document.querySelectorAll<HTMLButtonElement>('.sales-ops-period button')].find(
        (button) => button.textContent === label,
      );
    }

    function conversionWindows(): string[] {
      return apiMocks.salesConversion.mock.calls.map((call) => `${String(call[0])}..${String(call[1])}`);
    }

    it('reads week to date by default and puts a chosen preset in the URL', async () => {
      await renderView('/analytics?view=sales-ops');
      expect(periodButton('Week to date')?.getAttribute('aria-pressed')).toBe('true');
      expect(document.querySelector('.sales-ops-period [role="group"]')).toBeTruthy();

      await act(async () => {
        periodButton('7 days')?.click();
      });
      await settle();

      expect(location).toBe('/analytics?view=sales-ops&sales_period=7d');
      // A chosen period is a history entry: Back steps through periods.
      expect(navigation).toBe('PUSH');
      expect(periodButton('7 days')?.getAttribute('aria-pressed')).toBe('true');
      const [, last] = conversionWindows();
      expect(last).toMatch(/^\d{4}-\d{2}-\d{2}\.\.\d{4}-\d{2}-\d{2}$/);
      expect(document.body.textContent).toContain('Last 7 days conversion');
      // Aging and the standup are not period reads: one call each.
      expect(apiMocks.salesAging).toHaveBeenCalledTimes(1);
      expect(apiMocks.salesStandup).toHaveBeenCalledTimes(1);
    });

    // W5e C11: the press focuses the preset itself (WebKit never focuses a
    // pressed button, and preventDefault alone dropped focus to <body> once
    // Custom unmounted). The engine proof is sales-loop.fixture.spec.ts (f)
    // and sales-period-preset.webkit.fixture.spec.ts.
    it('a pressed preset takes focus itself and cancels the press default', async () => {
      await renderView('/analytics?view=sales-ops');
      const preset = periodButton('30 days') as HTMLButtonElement;
      const press = new MouseEvent('mousedown', { bubbles: true, cancelable: true });
      act(() => {
        preset.dispatchEvent(press);
      });
      expect(press.defaultPrevented).toBe(true);
      expect(document.activeElement).toBe(preset);
    });

    it('reads a custom range and normalises an invalid one to week to date with replace', async () => {
      await renderView('/analytics?view=sales-ops&sales_period=custom&sales_from=2026-01-05&sales_to=2026-01-20');
      expect(conversionWindows()).toEqual(['2026-01-05..2026-01-20']);
      // The range fields are their own lazy chunk (Field stays out of the route).
      await vi.waitFor(async () => {
        await settle();
        expect(document.querySelectorAll('.sales-ops-period input[type="date"]').length).toBe(2);
      }, { timeout: 10_000 });
      const from = document.querySelector<HTMLInputElement>('.sales-ops-period input[type="date"]');
      expect(from?.value).toBe('2026-01-05');
      expect(document.querySelector('.sales-ops-period label')?.textContent).toBe('From');

      act(() => root.unmount());
      root = createRoot(document.getElementById('root') as HTMLElement);
      apiMocks.salesConversion.mockClear();
      await renderView('/analytics?view=sales-ops&sales_period=custom&sales_from=2026-01-05&sales_to=2025-01-20');
      expect(location).toBe('/analytics?view=sales-ops');
      expect(navigation).toBe('REPLACE');
      expect(periodButton('Week to date')?.getAttribute('aria-pressed')).toBe('true');
    });

    it('keeps the last window busy and named for its own period while a new period loads', async () => {
      apiMocks.salesTeam.mockResolvedValue(TEAM);
      apiMocks.salesConversion.mockResolvedValue({ rows: [{ group_key: TEAM[0].email, application_start_rate: 0.5 }] });
      await renderView('/analytics?view=sales-ops');
      let answer: (value: unknown) => void = () => undefined;
      apiMocks.salesConversion.mockReturnValueOnce(new Promise((resolve) => {
        answer = resolve;
      }));

      await act(async () => {
        periodButton('30 days')?.click();
      });
      await settle();
      expect(periodButton('30 days')?.getAttribute('aria-pressed')).toBe('true');
      // The numbers on screen are still the week's: say so, and say busy.
      expect(conversionCard()?.querySelector('.eyebrow')?.textContent).toBe('Week-to-date conversion');
      expect(conversionCard()?.getAttribute('aria-busy')).toBe('true');
      expect(conversionCells()[0]).toEqual(['Summit LO 01', '50%']);

      await act(async () => {
        answer({ rows: [{ group_key: TEAM[0].email, application_start_rate: 0.2 }] });
      });
      await settle();
      expect(conversionCard()?.querySelector('.eyebrow')?.textContent).toBe('Last 30 days conversion');
      expect(conversionCard()?.getAttribute('aria-busy')).toBe('false');
      expect(conversionCells()[0]).toEqual(['Summit LO 01', '20%']);
    });

    it('lists every loan officer, with no title attribute', async () => {
      apiMocks.salesTeam.mockResolvedValue(TEAM);
      apiMocks.salesConversion.mockResolvedValue({
        rows: TEAM.map((member, index) => ({ group_key: member.email, application_start_rate: index / 10 })),
      });
      await renderView('/analytics?view=sales-ops');
      const card = [...document.querySelectorAll('.sales-ops-card')].find((node) =>
        node.textContent?.includes('Week-to-date conversion'),
      );
      expect(card?.querySelectorAll('.split-row').length).toBe(5);
      expect(card?.textContent).toContain('Summit LO 05');
      expect(card?.querySelectorAll('[title]').length).toBe(0);
    });

    it('lists the officers with no calls in the period too, as an explicit zero after the rows (critic-10 6e)', async () => {
      apiMocks.salesTeam.mockResolvedValue(TEAM);
      // conversion() groups call dispositions: only two officers have rows,
      // in the server's order, one keyed in another case.
      apiMocks.salesConversion.mockResolvedValue({
        rows: [
          { group_key: 'lo04@summit.example', application_start_rate: 0.25 },
          { group_key: 'LO02@summit.example', application_start_rate: 0.1 },
        ],
      });
      await renderView('/analytics?view=sales-ops');

      expect(conversionCells()).toEqual([
        ['Summit LO 04', '25%'],
        ['Summit LO 02', '10%'],
        ['Summit LO 01', '0 calls'],
        ['Summit LO 03', '0 calls'],
        ['Summit LO 05', '0 calls'],
      ]);
      // Never an invented rate, and still no LO email in the DOM.
      expect(conversionCells().filter(([, value]) => value === '0%')).toEqual([]);
      expect(conversionCard()?.innerHTML).not.toContain('@summit.example');
    });

    it('claims no zero calls when the conversion read failed', async () => {
      apiMocks.salesTeam.mockResolvedValue(TEAM);
      apiMocks.salesConversion.mockRejectedValue(new ApiError('down', { path: '/api/v1/sales/conversion', status: 500 }));
      await renderView('/analytics?view=sales-ops');

      expect(conversionCells()).toEqual([]);
      expect(conversionCard()?.textContent).not.toContain('No LO dispositions');
      expect(document.body.textContent).toContain('Sales operations metrics unavailable');
    });

    it('reads an officer missing past the 100-row cap as unknown, not as zero calls', async () => {
      apiMocks.salesTeam.mockResolvedValue(TEAM.slice(0, 1));
      apiMocks.salesConversion.mockResolvedValue({
        rows: Array.from({ length: 100 }, (_, index) => ({ group_key: `other${index}@summit.example`, application_start_rate: 0 })),
      });
      await renderView('/analytics?view=sales-ops');

      const cells = conversionCells();
      expect(cells).toHaveLength(101);
      expect(cells[100]).toEqual(['Summit LO 01', '—']);
    });

    it('offers the Distribution Board only after the manager read succeeded with an officer in scope', async () => {
      apiMocks.salesTeam.mockResolvedValue(TEAM);
      await renderView('/analytics?view=sales-ops');
      expect(document.body.textContent).toContain('Distribute approved leads…');

      act(() => root.unmount());
      queryClient.clear();
      root = createRoot(document.getElementById('root') as HTMLElement);
      apiMocks.salesAging.mockRejectedValue(new ApiError('forbidden', { path: '/api/v1/sales/aging', status: 403 }));
      await renderView('/analytics?view=sales-ops');
      expect(document.body.textContent).not.toContain('Distribute approved leads…');

      act(() => root.unmount());
      queryClient.clear();
      root = createRoot(document.getElementById('root') as HTMLElement);
      apiMocks.salesAging.mockResolvedValue([]);
      apiMocks.salesTeam.mockResolvedValue([]);
      await renderView('/analytics?view=sales-ops');
      expect(document.body.textContent).not.toContain('Distribute approved leads…');
    });
  });

  it('mounts the per-officer workload as its own chunk, reading the period window (critic-10 6c)', async () => {
    apiMocks.salesWorkload.mockResolvedValue({
      from_date: '2026-07-08',
      to_date: '2026-07-14',
      previous_from_date: '2026-07-01',
      previous_to_date: '2026-07-07',
      stale_days: 7,
      rows: [{ email: 'lo02@summit.example', display_label: 'Summit LO 02', region: 'CA', capacity_per_day: 25, open_count: 3, stale_count: 1, assigned_count: 2, worked_count: 1, previous_assigned_count: 1, previous_worked_count: 0 }],
    });
    await act(async () => {
      renderAt('/analytics?view=sales-ops&sales_period=7d');
    });
    await vi.waitFor(async () => {
      await settle();
      expect(document.querySelector('[data-testid="sales-workload"]')).not.toBeNull();
    }, { timeout: 10_000 });
    await settle();
    // One read for the shown period, the same window the conversion card reads,
    // owned by the tab on its own key (the lazy table imports no transport).
    expect(apiMocks.salesWorkload).toHaveBeenCalledTimes(1);
    const [from, to] = apiMocks.salesWorkload.mock.calls[0];
    expect(apiMocks.salesConversion.mock.calls.some((call) => call[0] === from && call[1] === to)).toBe(true);
    expect(queryClient.getQueryData(queryKeys.salesWorkload(from, to))).toMatchObject({ stale_days: 7 });
    const link = document.querySelector<HTMLAnchorElement>('[data-testid="sales-workload"] tbody a');
    expect(link?.textContent).toBe('Summit LO 02');
    expect(link?.getAttribute('href')).toBe('/lead-queue?assigned_to=lo02%40summit.example');
    expect(document.querySelector('[data-testid="sales-workload"]')?.textContent).not.toContain('@summit.example');
  });

  describe('the team comparison (critic-10 6b)', () => {
    const teamCards = () =>
      [...document.querySelectorAll('[data-testid="sales-team-comparison"] .kpi')].map((card) => ({
        label: card.querySelector('.kpi__label')?.textContent ?? null,
        value: card.querySelector('.kpi__value')?.textContent ?? null,
        delta: card.querySelector('.kpi__delta')?.textContent ?? null,
        loading: card.classList.contains('is-loading'),
      }));
    const cohort = (calls: number, contacts: number, starts: number) => ({
      rows: [{ group_key: 'all_cohorts', calls_attempted: calls, unique_contacts_reached: contacts, unique_application_starts: starts, contacts_reached: contacts, callbacks_scheduled: 0, applications_started: starts, application_start_rate: 0 }],
    });

    it('reads the cohort for the period and the same span before it, and states the change', async () => {
      // The period's read first, then the span before it (Promise.all order).
      apiMocks.salesCohort.mockResolvedValueOnce(cohort(220, 74, 12)).mockResolvedValueOnce(cohort(180, 74, 0));
      await act(async () => {
        renderAt('/analytics?view=sales-ops&sales_period=7d');
      });
      await settle();

      // Both reads are cohort reads: the team totals never sum the LO rows.
      const windows = apiMocks.salesCohort.mock.calls.map((call) => `${String(call[0])}..${String(call[1])}`);
      expect(windows).toHaveLength(2);
      const [current, previous] = windows.map((window) => window.split('..'));
      const dayMs = 24 * 60 * 60 * 1000;
      expect((Date.parse(current[0]) - Date.parse(previous[1])) / dayMs).toBe(1);
      expect((Date.parse(previous[1]) - Date.parse(previous[0])) / dayMs).toBe(6);
      expect(teamCards()).toEqual([
        { label: 'Calls attempted', value: '220', delta: `${signedCount(40)} vs previous 7 days`, loading: false },
        { label: 'Contacts reached', value: '74', delta: 'no change vs previous 7 days', loading: false },
        { label: 'Application starts', value: '12', delta: 'new vs previous 7 days', loading: false },
        { label: 'Start rate', value: '16.2%', delta: `${signedPct(16.2, 1).slice(0, -1)} pts vs previous 7 days`, loading: false },
      ]);
      // The delta direction follows the sign; "no change" is flat.
      const deltas = [...document.querySelectorAll('[data-testid="sales-team-comparison"] .kpi__delta')].map((node) => node.className);
      expect(deltas).toEqual(['kpi__delta up', 'kpi__delta flat', 'kpi__delta up', 'kpi__delta up']);
      // Each card opens the call-dispositions evidence.
      expect(document.querySelectorAll('[data-testid="sales-team-comparison"] .evidence-chip')).toHaveLength(4);
    });

    it('shows skeletons, never zeros, while loading and an em-dash when the team read failed', async () => {
      const never = new Promise<never>(() => {});
      apiMocks.salesConversion.mockReturnValueOnce(never);
      apiMocks.salesCohort.mockReturnValue(never);
      await act(async () => {
        renderAt('/analytics?view=sales-ops');
      });
      await settle();
      expect(teamCards().map((card) => card.loading)).toEqual([true, true, true, true]);
      expect(teamCards().map((card) => card.value)).toEqual([null, null, null, null]);

      act(() => root.unmount());
      queryClient.clear();
      root = createRoot(document.getElementById('root') as HTMLElement);
      apiMocks.salesConversion.mockResolvedValue({ rows: [] });
      apiMocks.salesCohort.mockRejectedValue(new ApiError('down', { path: '/api/v1/sales/conversion', status: 500 }));
      await act(async () => {
        renderAt('/analytics?view=sales-ops');
      });
      await settle();
      expect(teamCards().map((card) => card.value)).toEqual(['—', '—', '—', '—']);
      expect(teamCards().map((card) => card.delta)).toEqual([null, null, null, null]);
      // The per-officer card still reads: the team failure degrades the team row only.
      expect(document.body.textContent).toContain('No LO dispositions logged in this period.');
    });
  });

  it('says why the outcome summary failed, apart from the other cards', async () => {
    apiMocks.salesOutcomeSummary.mockRejectedValue(new ApiError('SENTINEL detail', {
      path: '/api/v1/sales/outcomes/summary', status: 503, retryable: true, dependency: 'lakebase', reason: 'retries_exhausted',
    }));
    await act(async () => {
      renderAt('/analytics?view=sales-ops');
    });
    await settle();

    expect(document.body.textContent).toContain('Outcome summary unavailable: The app already retried; try again shortly.');
    expect(document.body.textContent).not.toContain('SENTINEL');
  });
});
