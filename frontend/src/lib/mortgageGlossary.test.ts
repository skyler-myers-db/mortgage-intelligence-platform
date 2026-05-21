import { describe, expect, it } from 'vitest';
import { glossaryAnchor, glossaryEntries, mortgageGlossary } from './mortgageGlossary';

const REQUIRED_TERMS = [
  'avm',
  'bps',
  'clip',
  'ltv',
  'heloc',
  'inTheMoney',
  'nextBestOffer',
  'ownerLink',
  'signalStrength',
  'evidenceConfidence',
  'supportingEvidence',
] as const;

describe('mortgage glossary', () => {
  it('covers the borrower-facing acronyms and proof terms', () => {
    for (const key of REQUIRED_TERMS) {
      expect(mortgageGlossary[key].short.length).toBeGreaterThan(20);
      expect(mortgageGlossary[key].appContext.length).toBeGreaterThan(30);
      expect(glossaryAnchor(key)).toBe(`/glossary#${mortgageGlossary[key].id}`);
    }
  });

  it('keeps signal strength distinct from evidence confidence', () => {
    expect(mortgageGlossary.signalStrength.short).toMatch(/deterministic average/i);
    expect(mortgageGlossary.signalStrength.appContext).toMatch(/not a statistical confidence interval/i);
    expect(mortgageGlossary.evidenceConfidence.short).toMatch(/row-level confidence/i);
    expect(mortgageGlossary.evidenceConfidence.appContext).toMatch(/separate from signal strength/i);
  });

  it('has stable unique anchors for every entry', () => {
    const ids = glossaryEntries.map((entry) => entry.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids.every((id) => /^[a-z0-9-]+$/.test(id))).toBe(true);
  });
});
