/**
 * @vitest-environment happy-dom
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { SEGMENT_DEFINITIONS } from '../../lib/segmentMetadata';
import type { SegmentSummary } from '../../types';
import { SegmentCard } from './SegmentCard';

describe('Segment definitions', () => {
  it('contains in-the-money segment', () => {
    expect(SEGMENT_DEFINITIONS.some((s) => s.code === 'itm')).toBe(true);
  });

  it('uses equity-credit presentation for the HELOC Intent legacy segment code', () => {
    const helocIntent = SEGMENT_DEFINITIONS.find((s) => s.code === 'permit');
    expect(helocIntent?.name).toBe('HELOC Intent');
    expect(helocIntent?.description).toContain('HELOC propensity');
    expect(helocIntent?.icon).toBe('equity');
  });
});

describe('SegmentCard', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function render(segment: Partial<SegmentSummary>) {
    const base: SegmentSummary = {
      code: 'retention',
      name: 'Retention Risk',
      count: 0,
      delta: '+42%',
      avg_score: 0,
      description: 'Current customer retention segment.',
      color: 'var(--seg-retention)',
    };
    act(() => root.render(<SegmentCard segment={{ ...base, ...segment }} />));
  }

  it('does not show stale delta or average when the filtered count is zero', () => {
    render({ count: 0, delta: '+42%', avg_score: 0 });
    expect(container.textContent).toContain('no borrowers in current view');
    expect(container.textContent).not.toContain('+42%');
    expect(container.textContent).not.toContain('avg 0');
  });
});
