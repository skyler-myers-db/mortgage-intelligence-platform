import { describe, expect, it } from 'vitest';
import { glossaryQueryTokens, matchesGlossaryQuery, normalizeGlossaryText } from './glossarySearch';

const ENTRY = {
  term: 'In-the-money',
  aliases: ['ITM', 'refi economics'],
  short: 'A borrower whose rate spread and equity make a refinance worth it.',
};

describe('glossarySearch', () => {
  it('normalizes with NFKD, strips diacritics and lower-cases', () => {
    expect(normalizeGlossaryText('CRÉDITO Ñandú')).toBe('credito nandu');
    expect(glossaryQueryTokens('  Refi   ÉCONOMICS ')).toEqual(['refi', 'economics']);
    expect(glossaryQueryTokens('   ')).toEqual([]);
  });

  it('matches the term, an alias or the short text, case- and accent-blind', () => {
    expect(matchesGlossaryQuery(ENTRY, glossaryQueryTokens('in-the'))).toBe(true);
    expect(matchesGlossaryQuery(ENTRY, glossaryQueryTokens('itm'))).toBe(true);
    expect(matchesGlossaryQuery(ENTRY, glossaryQueryTokens('Réfinance'))).toBe(true);
  });

  it('requires every token to match', () => {
    expect(matchesGlossaryQuery(ENTRY, glossaryQueryTokens('refi equity'))).toBe(true);
    expect(matchesGlossaryQuery(ENTRY, glossaryQueryTokens('refi permit'))).toBe(false);
  });

  it('matches every entry for a blank query', () => {
    expect(matchesGlossaryQuery(ENTRY, [])).toBe(true);
  });
});
