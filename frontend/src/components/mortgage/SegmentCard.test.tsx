import { describe, expect, it } from 'vitest';
import { SEGMENT_DEFINITIONS } from '../../lib/segmentMetadata';

describe('Segment definitions', () => {
  it('contains in-the-money segment', () => {
    expect(SEGMENT_DEFINITIONS.some((s) => s.code === 'itm')).toBe(true);
  });
});
