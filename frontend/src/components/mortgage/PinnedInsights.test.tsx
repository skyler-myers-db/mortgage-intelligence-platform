/**
 * @vitest-environment happy-dom
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { PinnedInsights } from './PinnedInsights';
import { clearPinnedInsights, pinInsight } from '../../lib/pinnedInsights';

function installLocalStorage() {
  // Reuse a working localStorage if the environment already provides one
  // (happy-dom may define it non-configurably in the full-suite worker, where
  // redefining would throw); otherwise install a Map-backed stub.
  try {
    if (window.localStorage && typeof window.localStorage.clear === 'function') {
      window.localStorage.clear();
      return;
    }
  } catch {
    /* fall through to define a stub */
  }
  const m = new Map<string, string>();
  try {
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      writable: true,
      value: {
        getItem: (k: string) => m.get(k) ?? null,
        setItem: (k: string, v: string) => { m.set(k, String(v)); },
        removeItem: (k: string) => { m.delete(k); },
        clear: () => m.clear(),
        key: (i: number) => [...m.keys()][i] ?? null,
        get length() { return m.size; },
      },
    });
  } catch {
    /* environment owns localStorage; nothing to install */
  }
}

describe('PinnedInsights (Home card)', () => {
  let container: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    installLocalStorage();
    clearPinnedInsights();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });
  const render = () => act(() => root.render(<MemoryRouter><PinnedInsights /></MemoryRouter>));

  it('renders nothing when there are no pins (no empty chrome)', () => {
    render();
    expect(container.querySelector('.pinned-insights')).toBeNull();
  });

  it('renders pinned answers and unpins live (shared store sync)', () => {
    render();
    act(() => pinInsight({ id: 'p1', question: 'What is the average loan age?', summary: '5.25 years', source: 'mip.gold.lockin_cohort', pinnedAt: '2026-06-12T00:00:00Z' }));
    expect(container.querySelector('.pinned-insights')).not.toBeNull();
    expect(container.textContent).toContain('What is the average loan age?');
    expect(container.textContent).toContain('5.25 years');
    expect(container.textContent).toContain('mip.gold.lockin_cohort');
    // Unpin via the card button → card disappears (back to empty).
    act(() => container.querySelector<HTMLButtonElement>('.pinned-insights__unpin')!.click());
    expect(container.querySelector('.pinned-insights')).toBeNull();
  });
});
