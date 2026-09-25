import { describe, expect, it } from 'vitest';
import { pruneTo, rangeIds } from './LeadTable.selection';

const ORDER = ['B1', 'B2', 'B3', 'B4', 'B5', 'B6'];
const ALL = new Set(ORDER);

describe('rangeIds (audit tables-07 shift-range selection)', () => {
  it('selects anchor to target inclusive, in on-screen order, either direction', () => {
    expect(rangeIds(ORDER, 'B2', 'B5', ALL)).toEqual(['B2', 'B3', 'B4', 'B5']);
    expect(rangeIds(ORDER, 'B5', 'B2', ALL)).toEqual(['B2', 'B3', 'B4', 'B5']);
    expect(rangeIds(ORDER, 'B3', 'B3', ALL)).toEqual(['B3']);
  });

  it('skips rows that may not be selected (rejected, held, suppressed)', () => {
    expect(rangeIds(ORDER, 'B1', 'B6', new Set(['B1', 'B3', 'B6']))).toEqual(['B1', 'B3', 'B6']);
  });

  it('is just the target without a usable anchor, and nothing for an off-screen target', () => {
    expect(rangeIds(ORDER, null, 'B4', ALL)).toEqual(['B4']);
    expect(rangeIds(ORDER, 'B-GONE', 'B4', ALL)).toEqual(['B4']);
    expect(rangeIds(ORDER, null, 'B4', new Set())).toEqual([]);
    expect(rangeIds(ORDER, 'B1', 'B-GONE', ALL)).toEqual([]);
  });
});

describe('pruneTo (audit tables-07 phantom selection)', () => {
  it('keeps only the ids still on screen and leaves the input alone', () => {
    const selected = new Set(['B1', 'B9', 'B3']);
    const pruned = pruneTo(selected, new Set(['B1', 'B2', 'B3']));
    expect([...pruned]).toEqual(['B1', 'B3']);
    expect([...selected]).toEqual(['B1', 'B9', 'B3']);
  });

  it('is empty when nothing selected is on screen', () => {
    expect(pruneTo(new Set(['B9']), new Set(['B1'])).size).toBe(0);
  });
});
