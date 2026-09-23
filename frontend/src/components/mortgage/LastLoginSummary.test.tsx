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
      'no change in borrowers whose rate and equity pass the refinance screen',
    );
    expect(chips().map((chip) => chip.textContent)).toEqual(['no change']);
  });

  it('reads a percent token as a change in the population, never as a share of it', () => {
    render(DELTA_SUMMARY);
    const [pct, count] = Array.from(container.querySelectorAll('.home-answer__trigger')).map((el) => el.textContent);
    // "+1.5% borrowers with ..." read as "1.5% of borrowers".
    expect(pct).toBe('+1.5% in borrowers with an opportunity score of 75+');
    expect(count).toBe('+2,250 borrowers whose rate and equity pass the refinance screen');
  });

  it('first visit renders welcome copy, no delta language, no snapshot citation', () => {
    render(FIRST_VISIT_SUMMARY);
    expect(container.textContent).toContain('Welcome to your book');
    expect(container.textContent).toContain('First visit on record');
    expect(container.textContent).not.toContain('Since your last login');
    expect(triggerLink(0)).toBe('/lead-queue');
    act(() => chips()[0].click());
    const source = setDrawer.mock.calls[0][0] as DrawerSource;
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
});
