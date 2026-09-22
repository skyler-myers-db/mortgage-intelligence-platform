/**
 * @vitest-environment happy-dom
 *
 * Audit motion-06: one-time motion must be one-time. The Reveal fade played
 * on every mount (every borrower's trigger timeline, every route re-entry)
 * because nothing remembered that a key had already revealed. These tests
 * render the real component and read the DOM class list at first paint.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Reveal } from './Reveal';
import { __resetFirstAppearanceForTests } from '../../lib/useFirstAppearance';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type IOCallback = (entries: Array<{ isIntersecting: boolean; target: Element }>) => void;

const observers: Array<{ callback: IOCallback; observed: Element[] }> = [];

class FakeIntersectionObserver {
  private readonly record: { callback: IOCallback; observed: Element[] };

  constructor(callback: IOCallback) {
    this.record = { callback, observed: [] };
    observers.push(this.record);
  }

  observe(el: Element) {
    this.record.observed.push(el);
  }

  unobserve() {}

  disconnect() {}
}

function intersectAll() {
  act(() => {
    for (const obs of observers) {
      obs.callback(obs.observed.map((target) => ({ isIntersecting: true, target })));
    }
  });
}

let reducedMotion = false;

describe('Reveal', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    __resetFirstAppearanceForTests();
    observers.length = 0;
    reducedMotion = false;
    vi.stubGlobal('IntersectionObserver', FakeIntersectionObserver);
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query.includes('prefers-reduced-motion') && reducedMotion,
      media: query,
      addEventListener() {},
      removeEventListener() {},
    }));
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  const block = () => container.querySelector<HTMLElement>('.reveal-on-scroll');

  it('fades in on first viewport entry the first time a key mounts', () => {
    act(() => root.render(<Reveal revealKey="borrower-360:trigger-timeline">timeline</Reveal>));

    expect(block()?.classList.contains('is-visible')).toBe(false);
    expect(observers).toHaveLength(1);

    intersectAll();
    expect(block()?.classList.contains('is-visible')).toBe(true);
  });

  it('renders already visible on a later mount of the same key, without observing', () => {
    act(() => root.render(<Reveal revealKey="borrower-360:trigger-timeline">first borrower</Reveal>));
    intersectAll();
    act(() => root.unmount());
    root = createRoot(container);
    observers.length = 0;

    act(() => root.render(<Reveal revealKey="borrower-360:trigger-timeline">next borrower</Reveal>));

    // Visible from the first paint (the class is in the rendered markup, not
    // added later by an observer), so no transition replays.
    expect(block()?.className).toContain('is-visible');
    expect(observers).toHaveLength(0);
  });

  it('treats a mount that was never scrolled into view as seen for the session', () => {
    act(() => root.render(<Reveal revealKey="offer-orchestrator:details-rows">rows</Reveal>));
    act(() => root.unmount());
    root = createRoot(container);

    act(() => root.render(<Reveal revealKey="offer-orchestrator:details-rows">rows again</Reveal>));

    expect(block()?.className).toContain('is-visible');
  });

  it('keeps the reveal per key: a different key still fades in', () => {
    act(() => root.render(<Reveal revealKey="home:brand-signature">brand</Reveal>));
    intersectAll();
    act(() => root.unmount());
    root = createRoot(container);
    observers.length = 0;

    act(() => root.render(<Reveal revealKey="borrower-360:trigger-timeline">timeline</Reveal>));

    expect(block()?.classList.contains('is-visible')).toBe(false);
    expect(observers).toHaveLength(1);
  });

  it('renders the final state immediately under prefers-reduced-motion', () => {
    reducedMotion = true;

    act(() => root.render(<Reveal revealKey="borrower-360:trigger-timeline">timeline</Reveal>));

    expect(block()?.classList.contains('is-visible')).toBe(true);
    expect(observers).toHaveLength(0);
  });

  it('supports the section element and passes className through', () => {
    act(() => root.render(
      <Reveal revealKey="x" as="section" className="layoutA-grid">s</Reveal>,
    ));
    const el = container.querySelector('section.reveal-on-scroll.layoutA-grid');
    expect(el).not.toBeNull();
  });
});
