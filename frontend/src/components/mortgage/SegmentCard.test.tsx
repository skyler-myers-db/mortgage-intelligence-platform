import { describe, expect, it } from 'vitest';
import { mockSegments } from '../../mocks/demoData';

describe('Segment data', () => {
  it('contains in-the-money segment', () => {
    expect(mockSegments.some((s) => s.code === 'itm')).toBe(true);
  });
});
