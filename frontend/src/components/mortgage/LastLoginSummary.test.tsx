/**
 * @vitest-environment happy-dom
 *
 * S4 acceptance (component grain): every number in the "since your last
 * login" summary opens the EvidenceDrawer citing the kpi_snapshots baseline
 * row AND the headline metric view; first-visit / no-baseline states render
 * honest welcome copy with no fake deltas; a Genie phrasing is labelled and
 * still renders every deterministic token as an evidence affordance.
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DrawerSource } from '../AppContext';
import type { HomeSummary } from '../../types';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const setDrawer = vi.fn();
let showEvidence = true;
vi.mock('../AppContext', () => ({
  useApp: () => ({ lender: 'Summit Mortgage', setDrawer, showEvidence }),
}));

import { LastLoginSummary, segmentHeadline } from './LastLoginSummary';

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

describe('LastLoginSummary', () => {
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
    act(() => root.render(<LastLoginSummary summary={summary} loading={loading} />));

  it('renders the delta sentence with one evidence button per number', () => {
    render(DELTA_SUMMARY);
    const narrative = container.querySelector('.login-summary__narrative');
    expect(narrative?.textContent).toContain('Since your last login:');
    const buttons = container.querySelectorAll<HTMLButtonElement>('.login-summary__num');
    expect(buttons.length).toBe(3);
    expect(Array.from(buttons).map((b) => b.textContent)).toEqual([
      '+1.5%',
      '+2,250',
      '+4,120',
    ]);
  });

  it('every number opens the drawer citing snapshot baseline + metric view', () => {
    render(DELTA_SUMMARY);
    const buttons = Array.from(
      container.querySelectorAll<HTMLButtonElement>('.login-summary__num'),
    );
    const expectedFamilies = ['opportunity_score', 'in_the_money', 'next_best_offer'];
    for (const [index, button] of buttons.entries()) {
      setDrawer.mockClear();
      act(() => button.click());
      expect(setDrawer).toHaveBeenCalledTimes(1);
      const source = setDrawer.mock.calls[0][0] as DrawerSource;
      const lineageNames = (source.lineage ?? []).map((step) => step.name);
      expect(lineageNames).toContain('mip_app.kpi_snapshots');
      expect(lineageNames).toContain('mip.semantics.portfolio_headline_metric_view');
      expect(source.assetKey).toBe('portfolio_headline_metric_view');
      expect(source.lineageFamily).toBe(expectedFamilies[index]);
      const signalLabels = (source.signals ?? []).map((s) => s.label);
      expect(signalLabels).toEqual(
        expect.arrayContaining(['Current', 'Baseline', 'Since last login']),
      );
    }
  });

  it('first visit renders welcome copy, no delta language, no snapshot citation', () => {
    render(FIRST_VISIT_SUMMARY);
    expect(container.textContent).toContain('Welcome to your book');
    expect(container.textContent).toContain('First visit on record');
    expect(container.textContent).not.toContain('Since your last login:');
    const button = container.querySelector<HTMLButtonElement>('.login-summary__num');
    expect(button).toBeTruthy();
    act(() => button!.click());
    const source = setDrawer.mock.calls[0][0] as DrawerSource;
    const lineageNames = (source.lineage ?? []).map((step) => step.name);
    expect(lineageNames).not.toContain('mip_app.kpi_snapshots');
    expect(lineageNames).toContain('mip.semantics.portfolio_headline_metric_view');
    expect(source.lineageFamily).toBe('marketable_population');
  });

  it('no-baseline state is honest about the pending snapshot', () => {
    const summary: HomeSummary = {
      ...FIRST_VISIT_SUMMARY,
      status: 'no_baseline',
      previous_visit_at: '2026-07-09T14:30:00+00:00',
      headline:
        'Welcome back — your last-login baseline is still being captured, so deltas arrive after the next daily KPI snapshot. Today: 5,240,100 marketable borrowers, 88,210 high-opportunity, 402,330 offers available.',
    };
    render(summary);
    expect(container.textContent).toContain('Welcome back');
    expect(container.textContent).toContain('Deltas arrive after the next daily KPI snapshot');
    expect(container.querySelectorAll('.login-summary__num').length).toBe(3);
  });

  it('labels a Genie phrasing and keeps every token interactive', () => {
    const genie: HomeSummary = {
      ...DELTA_SUMMARY,
      phrasing_source: 'genie',
      phrasing_fallback_reason: null,
      headline:
        'Your book strengthened overnight: high-opportunity up +1.5%, with +2,250 refi candidates and +4,120 offers available to work.',
    };
    render(genie);
    expect(container.textContent).toContain('Genie-phrased · deterministic numbers');
    const buttons = container.querySelectorAll<HTMLButtonElement>('.login-summary__num');
    expect(buttons.length).toBe(3);
    expect(container.querySelector('.login-summary__narrative')?.textContent).toContain(
      'strengthened overnight',
    );
  });

  it('does not label the deterministic phrasing as Genie', () => {
    render(DELTA_SUMMARY);
    expect(container.textContent).not.toContain('Genie-phrased');
  });

  it('never renders model output as HTML (defense-in-depth; backend also rejects markup)', () => {
    const hostile: HomeSummary = {
      ...DELTA_SUMMARY,
      phrasing_source: 'genie',
      headline:
        '<img src=x onerror="window.__pwned=1"> up +1.5%, +2,250 refi candidates, +4,120 offers available.',
    };
    render(hostile);
    expect(container.querySelector('img[src="x"]')).toBeNull();
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
    // The markup shows up as inert text, tokens stay interactive.
    expect(container.querySelector('.login-summary__narrative')?.textContent).toContain('<img');
    expect(container.querySelectorAll('.login-summary__num').length).toBe(3);
  });

  it('falls back to structured highlights when a token cannot be located', () => {
    const mangled: HomeSummary = {
      ...DELTA_SUMMARY,
      headline: 'A sentence that lost its numbers somehow.',
    };
    render(mangled);
    const buttons = container.querySelectorAll<HTMLButtonElement>('.login-summary__num');
    expect(buttons.length).toBe(3);
    expect(container.textContent).toContain('refi candidates');
  });

  it('renders static numbers when evidence chrome is toggled off', () => {
    showEvidence = false;
    render(DELTA_SUMMARY);
    expect(container.querySelectorAll('.login-summary__num').length).toBe(0);
    expect(container.querySelectorAll('.login-summary__num-static').length).toBe(3);
  });

  it('renders nothing for null or malformed payloads and a skeleton while loading', () => {
    render(null);
    expect(container.querySelector('.login-summary')).toBeNull();
    render({ marketable_population: 12 } as unknown as HomeSummary);
    expect(container.querySelector('.login-summary')).toBeNull();
    render(null, true);
    expect(container.querySelector('.login-summary[aria-busy="true"]')).toBeTruthy();
  });
});

describe('segmentHeadline', () => {
  it('splits around every token exactly once, in sentence order', () => {
    const segments = segmentHeadline(DELTA_SUMMARY.headline, DELTA_SUMMARY.highlights);
    expect(segments).not.toBeNull();
    const tokens = segments!
      .filter((s): s is { highlight: (typeof DELTA_SUMMARY.highlights)[number] } => 'highlight' in s)
      .map((s) => s.highlight.display);
    expect(tokens).toEqual(['+1.5%', '+2,250', '+4,120']);
  });

  it('handles duplicate tokens by claiming distinct occurrences', () => {
    const highlights = [
      { ...DELTA_SUMMARY.highlights[1], display: '0', value_token: '0' },
      { ...DELTA_SUMMARY.highlights[2], display: '0', value_token: '0' },
    ];
    const segments = segmentHeadline('No change: 0 refi candidates, 0 offers available.', highlights);
    expect(segments).not.toBeNull();
    expect(segments!.filter((s) => 'highlight' in s).length).toBe(2);
  });

  it('returns null when a token is missing', () => {
    expect(segmentHeadline('no numbers here', DELTA_SUMMARY.highlights)).toBeNull();
  });
});
