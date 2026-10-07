/**
 * Figure tokens and the fail-closed highlight rule (audit 2026-09-21
 * `genie-10` phase 2). The client token grammar is asserted against the same
 * parity cases pytest asserts against the backend `_NUMBER_RE`
 * (tests/unit/test_genie_numeric_provenance.py), and every disqualifier of
 * highlightableFigures has its own case.
 */
import { describe, expect, it } from 'vitest';
import type { GenieVerifiedClaim } from '../types';
import { figureTokens, highlightableFigures, renderedSegments } from './genieFigureTokens';
import parity from './genieFigureTokens.parity.json';

const HOST = 'https://dbc-test.cloud.databricks.com';

function cell(token: string, occurrences: number | null, column: string | null = 'borrowers'): GenieVerifiedClaim {
  return { token, kind: 'number', derivation: 'returned_value', support: 'cell', column, occurrences };
}

describe('figureTokens mirrors the backend _NUMBER_RE', () => {
  it('has at least 30 parity cases', () => {
    expect(parity.length).toBeGreaterThanOrEqual(30);
  });

  it.each(parity.map((entry) => [JSON.stringify(entry.text), entry.text, entry.tokens] as const))(
    'tokenizes %s exactly like the server',
    (_label, text, tokens) => {
      expect(figureTokens(text)).toEqual(tokens);
    },
  );
});

describe('highlightableFigures', () => {
  it('marks a cell figure whose full-text and rendered counts both equal occurrences', () => {
    const marks = highlightableFigures('Texas has 250 borrowers.', [cell('250', 1)]);
    expect([...marks]).toEqual([['250', { column: 'borrowers' }]]);
  });

  it('marks a repeated cell figure when every occurrence is counted', () => {
    const marks = highlightableFigures('Texas has 250 borrowers; 250 is the larger count.', [cell('250', 2)]);
    expect(marks.has('250')).toBe(true);
  });

  it('refuses a token an appended disclosure repeats (full-text count)', () => {
    const text =
      'Illinois has 10 borrowers in the top band.\n\nGoverned cross-check: this answer overlaps 3 of 10 borrowers with the canonical ranking.';
    expect(highlightableFigures(text, [cell('10', 1)]).size).toBe(0);
  });

  it('refuses a token the renderer drops: an ordered-list marker (rendered-segment count)', () => {
    // Full text: the marker "1." and the figure both match; the list item
    // renders only the figure.
    const text = '1. Illinois holds 1 borrower in scope.';
    expect(figureTokens(text)).toEqual(['1', '1']);
    expect(renderedSegments(text).flatMap(figureTokens)).toEqual(['1']);
    expect(highlightableFigures(text, [cell('1', 2)]).size).toBe(0);
  });

  it('refuses a token inside a link target the renderer drops (rendered-segment count)', () => {
    const text = 'See [the 7 rates](https://example.test/7) for 7 borrowers.';
    expect(highlightableFigures(text, [cell('7', 3)]).size).toBe(0);
  });

  it('never reads _5_ as a figure: only the standalone 5 is counted', () => {
    const text = 'Codes like _5_ are labels; 5 borrowers qualify.';
    expect(figureTokens(text)).toEqual(['5']);
    expect(highlightableFigures(text, [cell('5', 1)]).has('5')).toBe(true);
    // A count that claims the underscored code too never matches.
    expect(highlightableFigures(text, [cell('5', 2)]).size).toBe(0);
  });

  it('counts the strings the Source linker splits out: a figure the split would create is refused', () => {
    // Unsplit, "5Source" holds no figure; with a workspace host the linker
    // splits before "Source", which would leave a bare "5" to mark.
    const text = 'There are 5 borrowers, per 5Source: mip.gold.borrower_360.';
    expect(highlightableFigures(text, [cell('5', 1)], null).has('5')).toBe(true);
    expect(highlightableFigures(text, [cell('5', 1)], HOST).size).toBe(0);
  });

  it('counts code and verbatim segments (they are never marked)', () => {
    const text = 'Use `250 rows` here; 250 borrowers.\n\n```\n250\n```';
    expect(renderedSegments(text)).toContain('250 rows');
    expect(highlightableFigures(text, [cell('250', 3)]).has('250')).toBe(true);
    expect(highlightableFigures(text, [cell('250', 1)]).size).toBe(0);
  });

  it.each<[string, GenieVerifiedClaim]>([
    ['a derived figure', { token: '370', kind: 'number', derivation: 'derived_from_rows', support: 'derived', occurrences: 1 }],
    ['a sum', { token: '370', kind: 'number', derivation: 'derived_from_rows', support: 'sum', column: 'borrowers', occurrences: 1 }],
    ['a bound', { token: '370', kind: 'number', derivation: 'bound', support: 'bound', occurrences: 1 }],
    ['an answer recorded before provenance', { token: '370', kind: 'number', derivation: 'returned_value' }],
    ['a cell without occurrences', { token: '370', kind: 'number', derivation: 'returned_value', support: 'cell', occurrences: null }],
  ])('never marks %s', (_label, item) => {
    expect(highlightableFigures('Together they hold 370 borrowers.', [item]).size).toBe(0);
  });

  it('refuses a token whose items disagree on occurrences or support', () => {
    const text = 'Texas has 250 borrowers.';
    expect(highlightableFigures(text, [cell('250', 1), cell('250', 2)]).size).toBe(0);
    expect(
      highlightableFigures(text, [
        cell('250', 1),
        { token: '250', kind: 'bps', derivation: 'derived_from_rows', support: 'derived' },
      ]).size,
    ).toBe(0);
  });

  it('names no column when the items listing a token disagree on it', () => {
    const marks = highlightableFigures('Texas has 50 loans and 50 bps.', [cell('50', 2, 'loans'), cell('50', 2, 'spread_bps')]);
    expect(marks.get('50')).toEqual({ column: null });
  });
});
