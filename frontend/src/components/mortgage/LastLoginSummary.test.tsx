/**
 * @vitest-environment happy-dom
 *
 * S4 acceptance (component grain), as the answer band's WHY NOW column
 * (2026-09-21 audit flow-05): every server token renders verbatim as an
 * evidence chip that opens the EvidenceDrawer citing the kpi_snapshots
 * baseline row AND the headline metric view; each population deep-links to
 * the Lead Queue filter with the same predicate; first-visit / no-baseline
 * states render honest welcome copy with no fake deltas.
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DrawerSource } from '../AppContext';
import type { HomeSummary } from '../../types';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const setDrawer = vi.fn();
let showEvidence = true;
vi.mock('../AppContext', () => ({
  useApp: () => ({ lender: 'Summit Mortgage', setDrawer, showEvidence }),
}));

// The WHY NOW rate move's read (flow-05): recorded per call with its key and
// whether it is enabled; `rate.data` is what the rate window answers.
const rate = vi.hoisted(() => ({
  data: null as unknown,
  error: null as Error | null,
  calls: [] as Array<{ key: string; enabled: boolean }>,
}));
vi.mock('../../lib/useWarmingUpRetry', () => ({
  useWarmingUpRetry: (_fetcher: unknown, opts: { queryKey: readonly unknown[]; enabled?: boolean }) => {
    rate.calls.push({ key: opts.queryKey.join('.'), enabled: opts.enabled ?? true });
    return {
      data: opts.enabled === false ? null : rate.data,
      warmingUp: null,
      error: rate.error,
      manualRetry: () => undefined,
      isFetching: false,
      isPlaceholderData: false,
      dataUpdatedAt: null,
      errorUpdatedAt: null,
    };
  },
}));

import { LastLoginSummary } from './LastLoginSummary';

const DELTA_SUMMARY: HomeSummary = {
  status: 'delta',
  previous_visit_at: '2026-07-09T14:30:00+00:00',
  baseline_snapshot_at: '2026-07-09T06:00:00+00:00',
  headline:
    'Since your last login: +1.5% high-opportunity, +2,250 refi candidates, +4,120 offers available.',
  phrasing_source: 'deterministic',
  phrasing_fallback_reason: 'genie_not_configured',
  highlights: [
    {
      measure: 'high_opportunity',
      label: 'high-opportunity',
      display: '+1.5%',
      value_token: '+1.5%',
      current: 88210,
      baseline: 86900,
      delta: 1310,
      delta_pct: 1.5,
    },
    {
      measure: 'refi_economics_screen',
      label: 'refi candidates',
      display: '+2,250',
      value_token: '+2,250',
      current: 261400,
      baseline: 259150,
      delta: 2250,
      delta_pct: 0.9,
    },
    {
      measure: 'offers_available',
      label: 'offers available',
      display: '+4,120',
      value_token: '+4,120',
      current: 402330,
      baseline: 398210,
      delta: 4120,
      delta_pct: 1.0,
    },
  ],
  current: {},
  baseline: {},
  deltas: {},
  current_source: 'mip.semantics.portfolio_headline_metric_view',
  baseline_source: 'mip_app.kpi_snapshots',
};

const FIRST_VISIT_SUMMARY: HomeSummary = {
  ...DELTA_SUMMARY,
  status: 'first_visit',
  previous_visit_at: null,
  baseline_snapshot_at: null,
  headline:
    "Welcome — here's your book today: 5,240,100 marketable borrowers, 88,210 high-opportunity, 402,330 offers available.",
  phrasing_fallback_reason: null,
  highlights: [
    {
      measure: 'marketable_population',
      label: 'marketable borrowers',
      display: '5,240,100',
      value_token: '5,240,100',
      current: 5240100,
      baseline: null,
      delta: null,
      delta_pct: null,
    },
    {
      measure: 'high_opportunity',
      label: 'high-opportunity',
      display: '88,210',
      value_token: '88,210',
      current: 88210,
      baseline: null,
      delta: null,
      delta_pct: null,
    },
    {
      measure: 'offers_available',
      label: 'offers available',
      display: '402,330',
      value_token: '402,330',
      current: 402330,
      baseline: null,
      delta: null,
      delta_pct: null,
    },
  ],
  baseline: null,
  deltas: null,
};

describe('LastLoginSummary (the answer band WHY NOW column)', () => {
  let root: Root;
  let container: HTMLElement;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    setDrawer.mockClear();
    showEvidence = true;
    rate.data = null;
    rate.error = null;
    rate.calls = [];
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  const render = (summary: HomeSummary | null, loading = false) =>
    act(() =>
      root.render(
        <MemoryRouter>
          <LastLoginSummary summary={summary} loading={loading} />
        </MemoryRouter>,
      ),
    );
  const chips = () => Array.from(container.querySelectorAll<HTMLButtonElement>('.home-answer__trigger .evidence-chip'));
  const triggerLink = (index: number) =>
    container.querySelectorAll('.home-answer__trigger')[index]?.querySelector('a')?.getAttribute('href') ?? null;

  it('lists one trigger per highlight, each token verbatim as its evidence chip', () => {
    render(DELTA_SUMMARY);
    expect(container.querySelector('.login-summary')?.textContent).toContain('Since your last login');
    expect(chips().map((chip) => chip.textContent)).toEqual(['+1.5%', '+2,250', '+4,120']);
  });

  it('every token opens the drawer citing snapshot baseline + metric view', () => {
    render(DELTA_SUMMARY);
    const expectedFamilies = ['opportunity_score', 'in_the_money', 'next_best_offer'];
    for (const [index, chip] of chips().entries()) {
      setDrawer.mockClear();
      act(() => chip.click());
      expect(setDrawer).toHaveBeenCalledTimes(1);
      const source = setDrawer.mock.calls[0][0] as DrawerSource;
      expect(source.title).toMatch(/^Since your last login — /);
      const baseline = (source.signals ?? []).find((signal) => signal.label === 'Baseline');
      expect(baseline?.source).toMatch(/^kpi_snapshots\./);
      expect(source.assetPath).toBe('mip.semantics.portfolio_headline_metric_view');
      expect(source.assetKey).toBe('portfolio_headline_metric_view');
      expect(source.lineageFamily).toBe(expectedFamilies[index]);
      const signalLabels = (source.signals ?? []).map((s) => s.label);
      expect(signalLabels).toEqual(
        expect.arrayContaining(['Current', 'Baseline', 'Since last login']),
      );
    }
  });

  it('deep-links each population to the Lead Queue filter with the same predicate', () => {
    render(DELTA_SUMMARY);
    expect(triggerLink(0)).toBe('/lead-queue?funnel_stage=high_opportunity');
    expect(triggerLink(1)).toBe('/lead-queue?segment=itm');
    // offer_available includes "Monitor for later"; no queue filter is that
    // population, so the trigger links nowhere rather than somewhere wrong.
    expect(triggerLink(2)).toBeNull();
    expect(container.textContent).toContain('borrowers with an offer decision');
    // The counts are whole-book; the queue is the contactable subset (the
    // ~23x addressable-vs-contactable gap), and the column says so.
    expect(container.querySelector('.home-answer__note')?.textContent).toContain(
      'each link opens the contactable subset in the Lead Queue',
    );
  });

  it('reads a zero movement as "no change in", never as zero borrowers', () => {
    const flat: HomeSummary = {
      ...DELTA_SUMMARY,
      highlights: [{ ...DELTA_SUMMARY.highlights[1], display: 'no change', value_token: 'no change', delta: 0 }],
    };
    render(flat);
    expect(container.querySelector('.home-answer__trigger')?.textContent).toBe(
      'no change in borrowers who pass the refi screen',
    );
    expect(chips().map((chip) => chip.textContent)).toEqual(['no change']);
  });

  it('reads a percent token as a change in the population, never as a share of it', () => {
    render(DELTA_SUMMARY);
    const [pct, count] = Array.from(container.querySelectorAll('.home-answer__trigger')).map((el) => el.textContent);
    // "+1.5% borrowers with ..." read as "1.5% of borrowers".
    expect(pct).toBe('+1.5% in borrowers with opportunity score 75+');
    expect(count).toBe('+2,250 borrowers who pass the refi screen');
  });

  it('first visit renders welcome copy, no delta language, no snapshot citation', () => {
    render(FIRST_VISIT_SUMMARY);
    expect(container.textContent).toContain('Welcome to your book');
    expect(container.textContent).toContain('First visit on record');
    expect(container.textContent).not.toContain('Since your last login');
    expect(triggerLink(0)).toBe('/lead-queue');
    act(() => chips()[0].click());
    const source = setDrawer.mock.calls[0][0] as DrawerSource;
    // Titled by the band the chip sits in; the "Your book today" card is gone.
    expect(source.title).toBe("Today's briefing — marketable borrowers");
    const signalLabels = (source.signals ?? []).map((signal) => signal.label);
    expect(signalLabels).not.toContain('Baseline');
    expect(source.assetPath).toBe('mip.semantics.portfolio_headline_metric_view');
    expect(source.lineageFamily).toBe('marketable_population');
  });

  it('no-baseline state is honest about the pending snapshot', () => {
    render({ ...FIRST_VISIT_SUMMARY, status: 'no_baseline', previous_visit_at: '2026-07-09T14:30:00+00:00' });
    expect(container.textContent).toContain('Welcome back');
    expect(container.textContent).toContain('Deltas arrive after the next daily KPI snapshot');
    expect(chips()).toHaveLength(3);
  });

  it('never renders the model-phrased headline, so model output cannot reach the DOM', () => {
    const hostile: HomeSummary = {
      ...DELTA_SUMMARY,
      phrasing_source: 'genie',
      headline: '<img src=x onerror="window.__pwned=1"> strengthened overnight +1.5%, +2,250, +4,120.',
    };
    render(hostile);
    expect(container.querySelector('img[src="x"]')).toBeNull();
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
    expect(container.textContent).not.toContain('strengthened overnight');
    expect(chips()).toHaveLength(3);
  });

  it('renders static tokens when evidence chrome is toggled off', () => {
    showEvidence = false;
    render(DELTA_SUMMARY);
    expect(chips()).toHaveLength(0);
    expect(
      Array.from(container.querySelectorAll('.login-summary__num-static')).map((node) => node.textContent),
    ).toEqual(['+1.5%', '+2,250', '+4,120']);
  });

  it('says what it cannot show for null or malformed payloads, and is busy while loading', () => {
    render(null);
    expect(container.querySelector('.home-answer__empty[role="status"]')).toBeTruthy();
    expect(chips()).toHaveLength(0);
    render({ marketable_population: 12 } as unknown as HomeSummary);
    expect(container.querySelector('.home-answer__empty[role="status"]')).toBeTruthy();
    render(null, true);
    expect(container.querySelector('.login-summary[aria-busy="true"]')).toBeTruthy();
    expect(container.querySelector('.home-answer__empty')).toBeNull();
  });

  // flow-05: WHY NOW cites events. The par move since the last visit comes
  // first (two server prints, one client subtraction), then the server's
  // event measures with their exact queue destinations.
  const RATE_WINDOW = {
    series_id: 'MORTGAGE30US',
    weeks: [
      { week: '2026-07-06', market_rate_pct: 6.7, itm_count: 10 },
      { week: '2026-09-28', market_rate_pct: 6.3, itm_count: 14, is_latest: true },
    ],
    book_lien_count: 100,
    thresholds: {},
    provenance: { market_rate_source: 'm', book_source: 'b', gold_source: 'g', rule_source: 'r', note: 'n' },
  };

  it('lists the 30-year par move since the last visit FIRST, from the rate window prints', () => {
    rate.data = RATE_WINDOW;
    render(DELTA_SUMMARY);
    const rows = Array.from(container.querySelectorAll('.home-answer__trigger'));
    expect(rows).toHaveLength(4);
    expect(rows[0].getAttribute('data-testid')).toBe('why-now-rate-move');
    expect(rows[0].textContent).toBe('-40 bps 30-year par rate since your last visit (from 6.70% to 6.30%)');
    expect(rows[0].querySelector('a')?.getAttribute('href')).toBe('/analytics');
    act(() => chips()[0].click());
    const source = setDrawer.mock.calls[0][0] as DrawerSource;
    expect(source.assetPath).toBe('mip.gold.rate_window_weekly');
    expect(source.lineageFamily).toBe('rate_spread');
    expect((source.signals ?? []).slice(0, 3).map((signal) => signal.value)).toEqual(['6.70%', '6.30%', '-40 bps']);
    expect((source.signals ?? [])[0].source).toContain('MORTGAGE30US');
  });

  it('reads the rate window only for a delta with a previous visit, on the Analytics key', () => {
    render(FIRST_VISIT_SUMMARY);
    expect(rate.calls.every((call) => !call.enabled)).toBe(true);
    render({ ...DELTA_SUMMARY, previous_visit_at: null });
    expect(rate.calls[rate.calls.length - 1]).toEqual({ key: 'mip.analytics.rate-window', enabled: false });
    render(DELTA_SUMMARY);
    expect(rate.calls[rate.calls.length - 1]).toEqual({ key: 'mip.analytics.rate-window', enabled: true });
  });

  it('a failed, warming or unmoved rate read adds no row; the column stays additive', () => {
    rate.error = new Error('rate window down');
    render(DELTA_SUMMARY);
    expect(container.querySelector('[data-testid="why-now-rate-move"]')).toBeNull();
    expect(chips().map((chip) => chip.textContent)).toEqual(['+1.5%', '+2,250', '+4,120']);
    rate.error = null;
    rate.data = { ...RATE_WINDOW, weeks: RATE_WINDOW.weeks.map((week) => ({ ...week, market_rate_pct: 6.3 })) };
    render(DELTA_SUMMARY);
    expect(container.querySelector('[data-testid="why-now-rate-move"]')).toBeNull();
  });

  it('renders the event and offer-path triggers with their exact queue links', () => {
    const events: HomeSummary = {
      ...DELTA_SUMMARY,
      highlights: [
        { measure: 'listed_for_sale', label: 'listed for sale', display: '+44', value_token: '+44', current: 1412, baseline: 1368, delta: 44, delta_pct: 3.2 },
        { measure: 'competitor_lien', label: 'competitor liens', display: '-31', value_token: '-31', current: 9870, baseline: 9901, delta: -31, delta_pct: -0.3 },
        { measure: 'offers_recommended', label: 'primary offer paths', display: '+190', value_token: '+190', current: 6250, baseline: 6060, delta: 190, delta_pct: 3.1 },
      ],
    };
    render(events);
    const rows = Array.from(container.querySelectorAll('.home-answer__trigger')).map((el) => el.textContent);
    expect(rows).toEqual([
      '+44 borrowers with a listed home',
      '-31 borrowers with a competitor lien',
      '+190 borrowers with a primary offer path',
    ]);
    expect(triggerLink(0)).toBe('/lead-queue?purchase_intent=Listed+for+sale');
    expect(triggerLink(1)).toBe('/lead-queue?lender_relationship=Competitor+customer');
    expect(triggerLink(2)).toBe('/lead-queue?funnel_stage=offer_recommended');
  });
});
