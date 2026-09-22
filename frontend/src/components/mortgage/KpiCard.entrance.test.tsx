/**
 * @vitest-environment happy-dom
 *
 * KpiCard one-time entrance (re-audit #5, D5). The entrance must fire once per
 * KPI per session and NOT replay when a live data refresh changes the value.
 * The fix keys `useFirstAppearance` on the label alone (not label+value); this
 * test pins that behavior so a regression back to value-in-key (which replays
 * the animation on every refresh) is caught.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { KpiCard } from './KpiCard';
import { __resetFirstAppearanceForTests } from '../../lib/useFirstAppearance';

describe('KpiCard one-time entrance does not replay on data refresh', () => {
  let container: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    __resetFirstAppearanceForTests();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  const enterEl = () => container.querySelector('.kpi__value--enter');

  it('animates on first appearance, then not again when the value refreshes (remount, same label)', () => {
    act(() => root.render(<KpiCard label="Marketable population" valueAnimated={79_730} />));
    expect(enterEl(), 'first appearance animates').not.toBeNull();

    // Simulate a live data refresh that remounts the card with a new value.
    act(() => root.unmount());
    container.remove();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => root.render(<KpiCard label="Marketable population" valueAnimated={80_120} />));
    expect(enterEl(), 'a refreshed value (same label) must NOT replay the entrance').toBeNull();
  });

  it('still animates the loading→loaded transition within a single mount', () => {
    act(() => root.render(<KpiCard label="High-intent leads" valueAnimated={undefined} loading />));
    expect(enterEl(), 'loading skeleton does not animate').toBeNull();
    // The real number lands in the same mount.
    act(() => root.render(<KpiCard label="High-intent leads" valueAnimated={111_726} />));
    expect(enterEl(), 'entrance fires when the real number lands, not on the skeleton').not.toBeNull();
  });

  /**
   * Audit motion-07: the sparkline draw finished in ~50-100ms of its 700ms
   * because a fixed dash of 220 was applied to a ~64-124px path. The stroke
   * path now normalises its length with pathLength="1" so the CSS dash of 1
   * (02-chips-kpi-segments.css) maps 1:1 to the visible line, and the area
   * fill fades over the same window instead of popping in.
   */
  it('draws the sparkline against a normalised path length and fades the area with it', () => {
    act(() => root.render(
      <KpiCard label="Marketable population" valueAnimated={79_730} trend={[3, 5, 4, 6, 8, 7, 9]} />,
    ));
    const line = container.querySelector('path.spark__line--draw');
    const area = container.querySelector('path.spark__area--fade');
    expect(line, 'entrance draws the stroke').not.toBeNull();
    expect(line?.getAttribute('pathLength')).toBe('1');
    expect(area, 'entrance fades the area fill').not.toBeNull();
  });

  it('leaves a non-entrance sparkline static: no draw or fade classes', () => {
    act(() => root.render(
      <KpiCard label="Marketable population" valueAnimated={79_730} trend={[3, 5, 4, 6, 8, 7, 9]} />,
    ));
    act(() => root.unmount());
    root = createRoot(container);
    act(() => root.render(
      <KpiCard label="Marketable population" valueAnimated={80_120} trend={[3, 5, 4, 6, 8, 7, 9]} />,
    ));
    expect(container.querySelector('path.spark__line--draw')).toBeNull();
    expect(container.querySelector('path.spark__area--fade')).toBeNull();
    expect(container.querySelector('path.spark__line')).not.toBeNull();
    expect(container.querySelector('path.spark__area')).not.toBeNull();
  });
});
