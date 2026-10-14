/**
 * The Executive KPI cards' evidence (2026-09-21 audit flow-06,
 * D-audit-reads-c1; W5e w5-analytics item 8). Titles are derived from the
 * registry entries they spread, never pinned (lexicon renames them).
 */
import { describe, expect, it } from 'vitest';
import { DRAWER_SOURCES, drawerForAsset } from './drawerSources';
import {
  EXECUTIVE_KPI_PROOF_KEYS,
  EXECUTIVE_KPI_SOURCES,
  executiveKpiEvidence,
  type ExecutiveKpi,
} from './executiveKpiDrawerSource';

const KPIS: readonly ExecutiveKpi[] = ['addressable', 'inTheMoney', 'primaryOfferPaths', 'approved'];

describe('Executive KPI drawer sources', () => {
  it('spread the registry entry of the asset each number comes from', () => {
    const lifecycle = drawerForAsset('mip.gold.borrower_lifecycle_state');
    expect(lifecycle).not.toBeNull();
    const expected: Record<ExecutiveKpi, typeof DRAWER_SOURCES.population> = {
      addressable: DRAWER_SOURCES.population,
      inTheMoney: DRAWER_SOURCES.itm,
      primaryOfferPaths: DRAWER_SOURCES.nbo,
      approved: lifecycle ?? DRAWER_SOURCES.funnelSnapshot,
    };
    for (const kpi of KPIS) {
      const source = EXECUTIVE_KPI_SOURCES[kpi];
      const { definition, ...rest } = source;
      expect(rest, kpi).toEqual(expected[kpi]);
      expect(source.title).toBe(expected[kpi].title);
      expect(source.assetPath, kpi).toBeTruthy();
      // One sentence, under "How we got {value}".
      expect(definition, kpi).toMatch(/^[A-Z][^.]*(?:\([^)]*\))?\.$/);
    }
    expect(EXECUTIVE_KPI_SOURCES.approved.assetPath).toBe(DRAWER_SOURCES.funnelSnapshot.assetPath);
  });

  it('carry the unfiltered book proof keys, one per card', () => {
    expect(EXECUTIVE_KPI_PROOF_KEYS).toEqual({
      addressable: 'analytics.addressable',
      inTheMoney: 'analytics.in_the_money',
      primaryOfferPaths: 'analytics.primary_offer_paths',
      approved: 'analytics.approved',
    });
  });

  it('give a card no evidence at all while a tab filter is active', () => {
    for (const kpi of KPIS) {
      expect(executiveKpiEvidence(kpi, '2026-10-13', true), kpi).toBeUndefined();
      expect(executiveKpiEvidence(kpi, '2026-10-13', false)).toEqual({
        asOf: '2026-10-13',
        proofKey: EXECUTIVE_KPI_PROOF_KEYS[kpi],
      });
    }
    expect(executiveKpiEvidence('addressable', null, false)).toEqual({ asOf: null, proofKey: 'analytics.addressable' });
  });
});
