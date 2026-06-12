/**
 * @vitest-environment happy-dom
 *
 * useFirstAppearance contract (re-audit #4 #2): true the FIRST time a key
 * renders this session, false on every later mount — this is what keeps the
 * KPI entrance from replaying on route re-entry (the original "demo-ticker"
 * complaint).
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { useFirstAppearance, __resetFirstAppearanceForTests } from './useFirstAppearance';

let seenValues: boolean[] = [];
function Probe({ k }: { k: string }) {
  const first = useFirstAppearance(k);
  seenValues.push(first);
  return <span>{first ? 'first' : 'repeat'}</span>;
}

describe('useFirstAppearance', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    __resetFirstAppearanceForTests();
    seenValues = [];
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('returns true on the first mount of a key', () => {
    act(() => root.render(<Probe k="kpi:Population:79,730" />));
    expect(container.textContent).toBe('first');
  });

  it('returns false on a later mount of the SAME key (route re-entry)', () => {
    act(() => root.render(<Probe k="kpi:Population:79,730" />));
    act(() => root.render(<div />)); // unmount Probe
    act(() => root.render(<Probe k="kpi:Population:79,730" />));
    expect(container.textContent).toBe('repeat');
  });

  it('treats a different key (a new value) as a fresh first appearance', () => {
    act(() => root.render(<Probe k="kpi:Population:79,730" />));
    act(() => root.render(<div />));
    act(() => root.render(<Probe k="kpi:Population:80,001" />));
    expect(container.textContent).toBe('first');
  });
});
