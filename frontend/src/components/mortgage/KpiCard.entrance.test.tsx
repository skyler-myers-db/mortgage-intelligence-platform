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
});
