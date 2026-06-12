/**
 * @vitest-environment happy-dom
 *
 * Evidence hover micro-preview contract (re-audit #4 #8): shows a PORTAL
 * card (in document.body, never clipped) on hover/focus and hides it on
 * leave/blur; never appears without a source; is decorative (aria-hidden);
 * and never steals the chip click that opens the full drawer.
 *
 * React synthesizes mouseenter/focus (they don't bubble), so raw
 * dispatchEvent on the chip can't reliably fire them. We drive the hook's
 * plain-function handlers through a harness (buttons whose click — which
 * DOES bubble through React — invokes each handler), and separately assert
 * the real EvidenceChip click path.
 */
import { useRef } from 'react';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DrawerSource } from './AppContext';
import { useEvidenceHoverCard } from './EvidenceHoverCard';

const setDrawer = vi.fn();
vi.mock('./AppContext', () => ({
  useApp: () => ({ setDrawer, showEvidence: true }),
}));

const SOURCE: DrawerSource = {
  title: 'Lock-in cohort',
  updatedAt: '2026-06-11 17:00 UTC',
  signals: [{ label: 'avg loan age', source: 'mip.gold.lockin_cohort', value: '5.25 yrs' }],
};

function Harness({ source }: { source?: DrawerSource }) {
  const { anchorRef, anchorHandlers, hoverCard } = useEvidenceHoverCard(source);
  const ref = useRef<HTMLButtonElement>(null);
  return (
    <div>
      <button ref={(el) => { ref.current = el; anchorRef(el); }}>anchor</button>
      <button data-act="enter" onClick={anchorHandlers.onMouseEnter}>enter</button>
      <button data-act="leave" onClick={anchorHandlers.onMouseLeave}>leave</button>
      <button data-act="focus" onClick={anchorHandlers.onFocus}>focus</button>
      <button data-act="blur" onClick={anchorHandlers.onBlur}>blur</button>
      {hoverCard}
    </div>
  );
}

describe('useEvidenceHoverCard', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.useRealTimers();
  });

  const card = () => document.body.querySelector('.evidence-hovercard');
  function fire(actName: string) {
    act(() => {
      container.querySelector<HTMLButtonElement>(`[data-act="${actName}"]`)!.click();
    });
  }
  function render(source?: DrawerSource) {
    act(() => root.render(<Harness source={source} />));
  }

  it('shows the portal card after the hover-intent delay and hides on leave', () => {
    render(SOURCE);
    fire('enter');
    expect(card()).toBeNull(); // not until the intent delay elapses
    act(() => vi.advanceTimersByTime(150));
    const shown = card();
    expect(shown).not.toBeNull();
    expect(container.contains(shown)).toBe(false); // portaled to <body>
    expect(shown!.textContent).toContain('Lock-in cohort');
    expect(shown!.textContent).toContain('5.25 yrs');
    expect(shown!.getAttribute('aria-hidden')).toBe('true');
    fire('leave');
    expect(card()).toBeNull();
  });

  it('shows immediately on keyboard focus (no hover delay) and hides on blur', () => {
    render(SOURCE);
    fire('focus');
    expect(card()).not.toBeNull();
    fire('blur');
    expect(card()).toBeNull();
  });

  it('never shows a card when there is no source', () => {
    render(undefined);
    fire('enter');
    act(() => vi.advanceTimersByTime(300));
    expect(card()).toBeNull();
    fire('focus');
    expect(card()).toBeNull();
  });

  it('cancels the pending show when the pointer leaves before the intent delay', () => {
    render(SOURCE);
    fire('enter');
    act(() => vi.advanceTimersByTime(80)); // before the 110ms delay
    fire('leave');
    act(() => vi.advanceTimersByTime(200)); // the original timer must NOT fire
    expect(card()).toBeNull();
  });

  it('hides the card on scroll (it is position:fixed and would otherwise drift)', () => {
    render(SOURCE);
    fire('focus');
    expect(card()).not.toBeNull();
    // The hook listens in the capture phase; dispatch a window scroll.
    act(() => window.dispatchEvent(new Event('scroll')));
    expect(card()).toBeNull();
  });

  it('hides the card on resize', () => {
    render(SOURCE);
    fire('focus');
    expect(card()).not.toBeNull();
    act(() => window.dispatchEvent(new Event('resize')));
    expect(card()).toBeNull();
  });
});

describe('EvidenceChip click path is unaffected by the hover card', () => {
  let container: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    vi.clearAllMocks();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('still opens the full drawer on click', async () => {
    const { EvidenceChip } = await import('./Primitives');
    act(() => root.render(<EvidenceChip source={SOURCE}>Source</EvidenceChip>));
    act(() => container.querySelector<HTMLButtonElement>('.evidence-chip')!.click());
    expect(setDrawer).toHaveBeenCalledWith(SOURCE);
  });
});
