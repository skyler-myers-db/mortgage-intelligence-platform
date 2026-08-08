import { describe, expect, it } from 'vitest';
import { loanTypeLabel, metroLabel, metroLoanTypeLabel } from './dossierCodes';

describe('loanTypeLabel', () => {
  it('expands the Cotality loan type codes gold actually carries', () => {
    // Live 2026-08-08 dossier value.
    expect(loanTypeLabel('CNV')).toBe('Conventional');
    // The spelling the UC functions test for.
    expect(loanTypeLabel('CONV')).toBe('Conventional');
    expect(loanTypeLabel('fha')).toBe('FHA');
    expect(loanTypeLabel(' va ')).toBe('VA');
  });

  it('passes an unknown code through instead of guessing a bucket', () => {
    expect(loanTypeLabel('ZZZ')).toBe('ZZZ');
    expect(loanTypeLabel('')).toBeNull();
    expect(loanTypeLabel(null)).toBeNull();
  });
});

describe('metroLabel', () => {
  it('labels the bare CBSA code', () => {
    expect(metroLabel('42660')).toBe('CBSA 42660');
    // Never double-prefixes if a labelled value ever arrives.
    expect(metroLabel('CBSA 42660')).toBe('CBSA 42660');
    expect(metroLabel(null)).toBeNull();
  });
});

describe('metroLoanTypeLabel', () => {
  it('renders the dossier line without raw codes', () => {
    expect(metroLoanTypeLabel('42660', 'CNV')).toBe('CBSA 42660 · Conventional');
  });

  it('marks each half honestly when it is missing', () => {
    expect(metroLoanTypeLabel(null, 'FHA')).toBe('CBSA unavailable · FHA');
    expect(metroLoanTypeLabel('42660', null)).toBe('CBSA 42660 · Loan type unavailable');
  });
});
