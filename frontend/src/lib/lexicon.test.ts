/**
 * The lexicon is the one source of the In the Money vocabulary (critic-11).
 */
import { describe, expect, it } from 'vitest';
import { LEXICON } from './lexicon';
import { segmentByCode } from './segmentMetadata';

describe('LEXICON.inTheMoney', () => {
  it('names the itm segment', () => {
    expect(segmentByCode('itm')?.name).toBe(LEXICON.inTheMoney.label);
    expect(LEXICON.inTheMoney.label).toBe('In the Money');
  });

  it('states the rule definition with grouped figures', () => {
    expect(LEXICON.inTheMoney.definition(75, 15)).toBe('Lien rate ≥ 75 bps above par and equity ≥ 15%');
    expect(LEXICON.inTheMoney.definition(1250, 15)).toBe('Lien rate ≥ 1,250 bps above par and equity ≥ 15%');
  });

  it('keeps the three grammatical forms and the stable ids', () => {
    expect(LEXICON.inTheMoney.prose).toBe('in the money');
    expect(LEXICON.inTheMoney.prenominal).toBe('in-the-money');
    expect(LEXICON.inTheMoney.ruleLabel).toBe('In the Money rule');
    expect(LEXICON.inTheMoney.glossaryId).toBe('in-the-money');
  });

  it('carries the retired names only as input aliases', () => {
    expect(LEXICON.inTheMoney.legacyNames).toEqual(['Prime Refi Candidates']);
    expect(LEXICON.inTheMoney.legacyAliases).toContain('refi economics screen');
    expect(LEXICON.inTheMoney.legacyAliases).not.toContain(LEXICON.inTheMoney.label);
  });
});
