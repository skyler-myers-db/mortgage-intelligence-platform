import { describe, expect, it } from 'vitest';
import { glossaryAnchor, glossaryEntries, mortgageGlossary } from './mortgageGlossary';

const REQUIRED_TERMS = [
  'avm',
  'bps',
  'clip',
  'ltv',
  'estimatedUpb',
  'heloc',
  'helocIntent',
  'mlsListings',
  'listedForSale',
  'buildingPermits',
  'loanProductType',
  'originationChannel',
  'inTheMoney',
  'nextBestOffer',
  'opportunityScore',
  'ownerLink',
  'signalStrength',
  'evidenceConfidence',
  'rateSpread',
  'supportingEvidence',
  'unityCatalog',
  // S1.3 overlay segments + published refi-propensity methodology.
  'refiPropensityHeuristic',
  'secondLienConsolidation',
  'helocDrawEnding',
  'homeEquityHistory',
  'itmOnRelatedProperty',
  'payoffLoss',
  'permitActivitySegment',
] as const;

describe('mortgage glossary', () => {
  it('covers the borrower-facing acronyms and proof terms', () => {
    const allKeys = Object.keys(mortgageGlossary).sort() as Array<keyof typeof mortgageGlossary>;
    expect([...REQUIRED_TERMS].sort()).toEqual(allKeys);
    for (const key of allKeys) {
      expect(mortgageGlossary[key].short.length).toBeGreaterThan(20);
      expect(mortgageGlossary[key].appContext.length).toBeGreaterThan(30);
      expect(mortgageGlossary[key].proof.length).toBeGreaterThan(20);
      expect(glossaryAnchor(key)).toBe(`/glossary#${mortgageGlossary[key].id}`);
    }
  });

  it('keeps signal strength distinct from evidence confidence', () => {
    expect(mortgageGlossary.signalStrength.short).toMatch(/deterministic average/i);
    expect(mortgageGlossary.signalStrength.appContext).toMatch(/not a statistical confidence interval/i);
    expect(mortgageGlossary.evidenceConfidence.short).toMatch(/row-level confidence/i);
    expect(mortgageGlossary.evidenceConfidence.appContext).toMatch(/separate from signal strength/i);
  });

  it('distinguishes refinance economics from opportunity score and primary offer', () => {
    expect(mortgageGlossary.inTheMoney.short).toMatch(/refinance-only economics screen/i);
    expect(mortgageGlossary.inTheMoney.appContext).toMatch(/not the same as a high-quality lead/i);
    expect(mortgageGlossary.opportunityScore.appContext).toMatch(/broader than refinance economics/i);
    expect(mortgageGlossary.nextBestOffer.term).toBe('Primary offer');
  });

  it('documents the estimated UPB methodology and confidence band', () => {
    const entry = mortgageGlossary.estimatedUpb;

    expect(entry.appContext).toMatch(/original_upb/i);
    expect(entry.appContext).toMatch(/monthly_rate/i);
    expect(entry.proof).toMatch(/fn_bounded_mortgage_rate/i);
    expect(entry.proof).toMatch(/months_elapsed clamps to 0\.\.360/i);
    expect(entry.proof).toMatch(/confidence band recomputes/i);
  });

  it('separates live listings, HELOC intent, and pending filed permits', () => {
    expect(mortgageGlossary.buildingPermits.term).toBe('Building Permits');
    expect(mortgageGlossary.listedForSale.appContext).toMatch(/purchase-intent trigger/i);
    expect(mortgageGlossary.mlsListings.appContext).toMatch(/separate from permits/i);
    expect(mortgageGlossary.helocIntent.short).toMatch(/HELOC propensity/i);
    expect(mortgageGlossary.buildingPermits.appContext).toMatch(/Do not infer filed permits/i);
  });

  it('has stable unique anchors for every entry', () => {
    const ids = glossaryEntries.map((entry) => entry.id);
    const terms = glossaryEntries.map((entry) => entry.term.trim().toLowerCase());
    expect(new Set(ids).size).toBe(ids.length);
    expect(new Set(terms).size).toBe(terms.length);
    expect(ids.every((id) => /^[a-z0-9-]+$/.test(id))).toBe(true);
  });
});
