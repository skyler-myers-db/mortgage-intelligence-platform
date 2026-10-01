/**
 * The Delta Explainer's drawer source (audit wow-ai-3): which "since your
 * last login" numbers carry an explainer, and the guard the drawer body uses.
 */
import { describe, expect, it } from 'vitest';
import type { HomeSummaryHighlight } from '../types';
import { deltaExplainerOf, isDeltaExplainerMeasure } from './deltaExplainerSource';
import { loginSummaryDrawerSource } from './loginSummaryDrawerSource';

const highlight = (measure: string, overrides: Partial<HomeSummaryHighlight> = {}): HomeSummaryHighlight => ({
  measure,
  label: measure,
  display: '+2,250',
  value_token: '+2,250',
  current: 12_840,
  baseline: 10_590,
  delta: 2_250,
  delta_pct: 21.2,
  ...overrides,
});

const DELTA = { previousVisitAt: '2026-09-09T14:30:00Z', baselineSnapshotAt: '2026-09-09T06:00:00Z', status: 'delta' };

describe('deltaExplainerSource', () => {
  it('knows the four measures the funnel snapshots attribute per state', () => {
    for (const measure of ['refi_economics_screen', 'high_opportunity', 'offers_recommended', 'listed_for_sale']) {
      expect(isDeltaExplainerMeasure(measure)).toBe(true);
    }
    for (const measure of ['competitor_lien', 'marketable_population', 'offers_available', 'nope']) {
      expect(isDeltaExplainerMeasure(measure)).toBe(false);
    }
  });

  it('attaches the explainer to a delta of a supported measure, on the baseline snapshot date (UTC)', () => {
    const source = loginSummaryDrawerSource(highlight('refi_economics_screen'), DELTA);
    expect(deltaExplainerOf(source)).toEqual({
      measure: 'refi_economics_screen',
      baselineDate: '2026-09-09',
      liveDisplay: '+2,250',
    });
    // The drawer's own facts are unchanged.
    expect(source.assetPath).toBe('mip.semantics.portfolio_headline_metric_view');
  });

  it('attaches none to a first visit, a missing baseline snapshot, or an unsupported measure', () => {
    expect(deltaExplainerOf(loginSummaryDrawerSource(highlight('refi_economics_screen'), { ...DELTA, status: 'first_visit' }))).toBeNull();
    expect(deltaExplainerOf(loginSummaryDrawerSource(highlight('refi_economics_screen'), { ...DELTA, baselineSnapshotAt: null }))).toBeNull();
    expect(deltaExplainerOf(loginSummaryDrawerSource(highlight('marketable_population'), DELTA))).toBeNull();
    expect(
      deltaExplainerOf(loginSummaryDrawerSource(highlight('high_opportunity', { baseline: null, delta: null }), DELTA)),
    ).toBeNull();
  });

  it('says why competitor liens have no per-state breakdown', () => {
    const source = loginSummaryDrawerSource(highlight('competitor_lien'), DELTA);
    expect(deltaExplainerOf(source)).toBeNull();
    expect(source.signals).toContainEqual({
      label: 'Per-state attribution',
      source: 'mip.gold.funnel_snapshot_daily',
      value: 'not snapshotted for this measure',
    });
  });

  it('rejects a malformed explainer rather than reading an unknown measure or date', () => {
    expect(deltaExplainerOf(null)).toBeNull();
    expect(deltaExplainerOf({ title: 't' })).toBeNull();
    const bad = (deltaExplainer: unknown) => deltaExplainerOf({ title: 't', deltaExplainer } as never);
    expect(bad({ measure: 'competitor_lien', baselineDate: '2026-09-09', liveDisplay: '+1' })).toBeNull();
    expect(bad({ measure: 'high_opportunity', baselineDate: '09/09/2026', liveDisplay: '+1' })).toBeNull();
    expect(bad('high_opportunity')).toBeNull();
  });
});
