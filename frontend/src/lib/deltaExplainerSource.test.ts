/**
 * The Delta Explainer's drawer source (audit wow-ai-3): which "since your
 * last login" numbers carry an explainer, and the guard the drawer body uses.
 * W5c folded it into DrawerSource: `deltaExplainer = { measure, baselineDate }`
 * and the live figure is the source's own `value`. Whether the funnel snapshot
 * attributes a measure per state is the route's answer (`snapshotted`), so
 * competitor liens carry the explainer and no hard-coded signal says otherwise.
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
  it('knows the five "since your last login" measures the explainer opens for', () => {
    for (const measure of ['refi_economics_screen', 'high_opportunity', 'offers_recommended', 'listed_for_sale', 'competitor_lien']) {
      expect(isDeltaExplainerMeasure(measure)).toBe(true);
    }
    for (const measure of ['marketable_population', 'offers_available', 'nope']) {
      expect(isDeltaExplainerMeasure(measure)).toBe(false);
    }
  });

  it('attaches the explainer to a delta of a supported measure, on the baseline snapshot date (UTC)', () => {
    const source = loginSummaryDrawerSource(highlight('refi_economics_screen'), DELTA);
    expect(source.deltaExplainer).toEqual({ measure: 'refi_economics_screen', baselineDate: '2026-09-09' });
    expect(source.value).toBe('+2,250');
    expect(deltaExplainerOf(source)).toEqual({
      measure: 'refi_economics_screen',
      baselineDate: '2026-09-09',
      liveDisplay: '+2,250',
    });
    // The drawer's own facts are unchanged.
    expect(source.assetPath).toBe('mip.semantics.portfolio_headline_metric_view');
  });

  it('reads the live figure from the source value, never a copy inside the explainer', () => {
    const explainer = deltaExplainerOf({
      title: 't',
      value: '-31',
      deltaExplainer: { measure: 'high_opportunity', baselineDate: '2026-09-09' },
    });
    expect(explainer?.liveDisplay).toBe('-31');
    expect(deltaExplainerOf({ title: 't', deltaExplainer: { measure: 'high_opportunity', baselineDate: '2026-09-09' } })?.liveDisplay).toBe('');
  });

  it('attaches none to a first visit, a missing baseline snapshot, or an unsupported measure', () => {
    expect(deltaExplainerOf(loginSummaryDrawerSource(highlight('refi_economics_screen'), { ...DELTA, status: 'first_visit' }))).toBeNull();
    expect(deltaExplainerOf(loginSummaryDrawerSource(highlight('refi_economics_screen'), { ...DELTA, baselineSnapshotAt: null }))).toBeNull();
    expect(deltaExplainerOf(loginSummaryDrawerSource(highlight('marketable_population'), DELTA))).toBeNull();
    expect(
      deltaExplainerOf(loginSummaryDrawerSource(highlight('high_opportunity', { baseline: null, delta: null }), DELTA)),
    ).toBeNull();
  });

  it('gives competitor liens the explainer and no hard-coded "not snapshotted" signal', () => {
    const source = loginSummaryDrawerSource(highlight('competitor_lien', { display: '-31' }), DELTA);
    expect(deltaExplainerOf(source)).toEqual({ measure: 'competitor_lien', baselineDate: '2026-09-09', liveDisplay: '-31' });
    expect(JSON.stringify(source.signals)).not.toContain('not snapshotted');
  });

  it('splits the first sentence into the definition, so no sentence renders twice', () => {
    const source = loginSummaryDrawerSource(highlight('refi_economics_screen'), DELTA);
    expect(source.definition).toMatch(/^Signed movement between/);
    expect(source.description).toBe('Both sides aggregate the same headline set, so the comparison is apples-to-apples.');
  });

  it('rejects a malformed explainer rather than reading an unknown measure or date', () => {
    expect(deltaExplainerOf(null)).toBeNull();
    expect(deltaExplainerOf({ title: 't' })).toBeNull();
    const bad = (deltaExplainer: unknown) => deltaExplainerOf({ title: 't', deltaExplainer } as never);
    expect(bad({ measure: 'marketable_population', baselineDate: '2026-09-09' })).toBeNull();
    expect(bad({ measure: 'high_opportunity', baselineDate: '09/09/2026' })).toBeNull();
    expect(bad('high_opportunity')).toBeNull();
  });
});
