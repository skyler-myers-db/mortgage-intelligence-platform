import { describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router';

const apiMocks = vi.hoisted(() => ({
  portfolioPreview: vi.fn(),
}));

// What Home's reads return, by retry key (the preview is ['home']). Only the
// rendered-state tests below set it; the request-contract tests never render.
const reads = vi.hoisted(() => ({
  preview: { data: null as unknown, warmingUp: null as unknown, error: null as unknown },
  warehouse: 'up' as string,
}));

vi.mock('../lib/api', () => ({
  api: {
    portfolioPreview: apiMocks.portfolioPreview,
  },
  dependencyLabel: vi.fn(),
  isAbortError: vi.fn(),
  isWarmingUpError: vi.fn(),
}));
vi.mock('../lib/useWarmingUpRetry', () => ({
  useWarmingUpRetry: (_fetcher: unknown, key: string[]) =>
    key.join('/') === 'home'
      ? { ...reads.preview, manualRetry: vi.fn() }
      : { data: null, warmingUp: null, error: null, manualRetry: vi.fn() },
}));
vi.mock('../components/AppContext', () => ({
  useApp: () => ({ lender: 'Summit Mortgage', canAccessAdmin: false, setDrawer: vi.fn(), showEvidence: true }),
}));
vi.mock('../components/HealthProvider', () => ({
  computeDegraded: () => reads.warehouse === 'down',
  useOptionalHealth: () => ({
    health: { status: 'ok', mode: 'live', dependencies: { warehouse: reads.warehouse, lakebase: 'up', genie: 'up' } },
  }),
}));
vi.mock('../components/mortgage/USChoroplethMap', () => ({ USChoroplethMap: () => null }));
vi.mock('../components/mortgage/PinnedInsights', () => ({ PinnedInsights: () => null }));
vi.mock('../components/mortgage/HomeAnswerWho', () => ({ HomeAnswerWho: () => null }));

import Home, {
  APPROVAL_QUEUE_STATE_LABEL,
  HOME_PORTFOLIO_PREVIEW_CRITERIA,
  HomeDayZeroStatus,
  requestHomePortfolioPreview,
} from './home';
import { WAREHOUSE_WARMING_BODY } from '../components/ui/WarmingUpBlock';

const WARMING = {
  dependency: 'warehouse',
  label: 'Warehouse warming up',
  attempt: 1,
  maxAttempts: 6,
  correlationId: null,
};

function renderHome(): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <Home />
    </MemoryRouter>,
  );
}

/** Any "N seconds" / "N s" / "N–M seconds" duration claim. */
const DURATION_CLAIM = /\b(30|60)\s*(–|-)?\s*(60)?\s*(s\b|sec|seconds)/i;

describe('Home through a warehouse warm-up and an outage (audit delivery-01)', () => {
  it('keeps the KPI row and the answer band mounted, loading, beside the warming block', () => {
    reads.preview = { data: null, warmingUp: WARMING, error: null };
    reads.warehouse = 'up';
    const html = renderHome();

    expect(html).toContain('warming-block');
    expect(html).toContain('class="kpi-row"');
    expect(html, 'the answer band stays mounted through a warm-up').toContain('home-answer');
    expect(html).toContain('home-answer__briefing-skeleton');
    expect(html).toContain(WAREHOUSE_WARMING_BODY);
    expect(html).not.toMatch(DURATION_CLAIM);
  });

  it('promises no duration for a real warehouse outage', () => {
    reads.preview = { data: null, warmingUp: null, error: new Error('warehouse down') };
    reads.warehouse = 'down';
    const html = renderHome();

    expect(html).toContain('Portfolio KPIs are waiting on the analytics warehouse');
    expect(html).not.toMatch(DURATION_CLAIM);
    expect(html).not.toContain('typically');
  });

  it('says the serverless truth in the shared warming sentence', () => {
    expect(WAREHOUSE_WARMING_BODY).toContain('2–6 seconds');
    expect(WAREHOUSE_WARMING_BODY).not.toMatch(DURATION_CLAIM);
  });
});

describe('Home portfolio preview request contract', () => {
  it('pins Home KPIs to the global market rather than the eligible-only campaign default', async () => {
    const signal = new AbortController().signal;
    apiMocks.portfolioPreview.mockResolvedValueOnce({});

    await requestHomePortfolioPreview(signal);

    expect(HOME_PORTFOLIO_PREVIEW_CRITERIA).toEqual({ marketing_eligibility: 'Any' });
    expect(apiMocks.portfolioPreview).toHaveBeenCalledWith(
      { marketing_eligibility: 'Any' },
      signal,
    );
  });

  it('describes approval metrics as current lifecycle state, not snapshot state', () => {
    expect(APPROVAL_QUEUE_STATE_LABEL).toBe('current lifecycle state');
  });

  it('replaces the day-zero Admin link with guidance for non-admins', () => {
    const regularHtml = renderToStaticMarkup(
      <MemoryRouter><HomeDayZeroStatus canAccessAdmin={false} /></MemoryRouter>,
    );
    expect(regularHtml).toContain('Contact an administrator');
    expect(regularHtml).not.toContain('/admin-config');

    const adminHtml = renderToStaticMarkup(
      <MemoryRouter><HomeDayZeroStatus canAccessAdmin /></MemoryRouter>,
    );
    expect(adminHtml).toContain('/admin-config#data-operations');
  });
});
