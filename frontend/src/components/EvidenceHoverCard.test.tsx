/**
 * @vitest-environment happy-dom
 *
 * Evidence hover micro-preview contract (re-audit #4 #8; timing, placement
 * and exit per 2026-09-21 audit motion-10 / css-10): a PORTAL card (in
 * document.body, never clipped) on hover after the hover-intent delay, at
 * once on keyboard focus or within the re-open grace window; hidden on
 * leave / blur; never without a source; decorative (aria-hidden); it never
 * steals the chip click that opens the full drawer, and it requests nothing.
 * Placement is either CSS anchor positioning (followed on scroll) or fixed
 * coordinates chosen by the card's MEASURED height (hidden on scroll).
 *
 * React synthesizes mouseenter/focus (they don't bubble), so raw
 * dispatchEvent on the chip can't reliably fire them. We drive the hook's
 * plain-function handlers through a harness (buttons whose click — which
 * DOES bubble through React — invokes each handler), and separately assert
 * the real EvidenceChip click path. happy-dom's CSS.supports answers true
 * for anything, so every test pins the placement mode it exercises.
 */
import { useRef } from 'react';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DrawerSource } from './AppContext';
import { useEvidenceHoverCard } from './EvidenceHoverCard';
import { EvidenceChip } from './Primitives';

const setDrawer = vi.fn();
vi.mock('./AppContext', () => ({
  useApp: () => ({ setDrawer, showEvidence: true }),
}));

const SOURCE: DrawerSource = {
  title: 'Lock-in cohort',
  updatedAt: '2026-06-11 17:00 UTC',
  signals: [{ label: 'avg loan age', source: 'mip.gold.lockin_cohort', value: '5.25 yrs' }],
};

function Chip({ name, source }: { name: string; source?: DrawerSource }) {
  const { anchorRef, anchorHandlers, hoverCard } = useEvidenceHoverCard(source);
  const ref = useRef<HTMLButtonElement>(null);
  return (
    <div data-chip={name}>
      <button data-anchor={name} ref={(el) => { ref.current = el; anchorRef(el); }}>anchor</button>
      <button data-act={`enter-${name}`} onClick={anchorHandlers.onMouseEnter}>enter</button>
      <button data-act={`leave-${name}`} onClick={anchorHandlers.onMouseLeave}>leave</button>
      <button data-act={`focus-${name}`} onClick={anchorHandlers.onFocus}>focus</button>
      <button data-act={`blur-${name}`} onClick={anchorHandlers.onBlur}>blur</button>
      {hoverCard}
    </div>
  );
}

function Harness({ source }: { source?: DrawerSource }) {
  return (
    <>
      <Chip name="a" source={source} />
      <Chip name="b" source={source} />
    </>
  );
}

/** happy-dom hands out a fresh `CSS` per access, so the global is replaced. */
function stubAnchorSupport(supported: boolean): void {
  vi.stubGlobal('CSS', {
    supports: (condition: string) => (/anchor-name/.test(condition) ? supported : true),
    escape: (value: string) => value,
  });
}

describe('useEvidenceHoverCard', () => {
  let container: HTMLDivElement;
  let root: Root;
  let fetchSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    // The re-open grace window is module-wide: start every test well past
    // the last close of the previous one.
    vi.setSystemTime(new Date(Date.now() + 60_000));
    fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  const cards = () => Array.from(document.body.querySelectorAll<HTMLElement>('.evidence-hovercard'));
  const card = () => cards()[0] ?? null;
  const anchor = (name: string) => container.querySelector<HTMLElement>(`[data-anchor="${name}"]`)!;
  function fire(actName: string) {
    act(() => {
      container.querySelector<HTMLButtonElement>(`[data-act="${actName}"]`)!.click();
    });
  }
  function render(source?: DrawerSource) {
    act(() => root.render(<Harness source={source} />));
  }

  it('waits out the hover-intent delay: no card at 300 ms, a card at 400 ms; hides on leave', () => {
    stubAnchorSupport(false);
    render(SOURCE);
    fire('enter-a');
    act(() => vi.advanceTimersByTime(300));
    expect(card()).toBeNull();
    act(() => vi.advanceTimersByTime(100));
    const shown = card();
    expect(shown).not.toBeNull();
    expect(container.contains(shown)).toBe(false); // portaled to <body>
    expect(shown!.textContent).toContain('Lock-in cohort');
    expect(shown!.textContent).toContain('5.25 yrs');
    expect(shown!.getAttribute('aria-hidden')).toBe('true');
    expect(shown!.getAttribute('popover')).toBe('manual');
    fire('leave-a');
    expect(card()).toBeNull();
    expect(fetchSpy, 'a passive preview requests nothing').not.toHaveBeenCalled();
  });

  it('opens the next card at once within 300 ms of a close, and waits again after', () => {
    stubAnchorSupport(false);
    render(SOURCE);
    fire('focus-a');
    expect(card()).not.toBeNull();
    fire('blur-a');
    expect(card()).toBeNull();

    act(() => vi.advanceTimersByTime(200));
    fire('enter-b');
    expect(cards(), 'within the grace window: no delay').toHaveLength(1);
    fire('leave-b');

    act(() => vi.advanceTimersByTime(400));
    fire('enter-a');
    expect(card(), 'past the grace window: the delay applies again').toBeNull();
    act(() => vi.advanceTimersByTime(350));
    expect(card()).not.toBeNull();
  });

  it('shows immediately on keyboard focus (no hover delay) and hides on blur', () => {
    stubAnchorSupport(false);
    render(SOURCE);
    fire('focus-a');
    expect(card()).not.toBeNull();
    fire('blur-a');
    expect(card()).toBeNull();
  });

  it('never shows a card when there is no source', () => {
    render(undefined);
    fire('enter-a');
    act(() => vi.advanceTimersByTime(1000));
    expect(card()).toBeNull();
    fire('focus-a');
    expect(card()).toBeNull();
  });

  it('cancels the pending show when the pointer leaves before the intent delay', () => {
    stubAnchorSupport(false);
    render(SOURCE);
    fire('enter-a');
    act(() => vi.advanceTimersByTime(200));
    fire('leave-a');
    act(() => vi.advanceTimersByTime(500)); // the original timer must NOT fire
    expect(card()).toBeNull();
  });

  describe('fixed-coordinate fallback (no CSS anchor positioning)', () => {
    /** The chip at `top` px; the card renders `height` px tall. */
    function stubGeometry(top: number, height: number) {
      const original = HTMLElement.prototype.getBoundingClientRect;
      vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
        if (this.classList.contains('evidence-hovercard')) {
          return DOMRect.fromRect({ x: 0, y: 0, width: 240, height });
        }
        if (this.dataset.anchor) return DOMRect.fromRect({ x: 100, y: top, width: 40, height: 20 });
        return original.call(this);
      });
    }

    it('places the card by its measured height, not an estimate: a 200 px card under a chip at 150 px goes below', () => {
      stubAnchorSupport(false);
      stubGeometry(150, 200);
      render(SOURCE);
      fire('focus-a');
      const shown = card()!;
      expect(shown.className).toContain('evidence-hovercard--below');
      expect(shown.style.top).toBe('178px'); // chip bottom 170 + 8 px gap
      expect(shown.style.left).toBe('120px'); // chip centre
      expect(shown.style.visibility, 'measured before it is shown').toBe('');
    });

    it('and a 100 px card under the same chip goes above', () => {
      stubAnchorSupport(false);
      stubGeometry(150, 100);
      render(SOURCE);
      fire('focus-a');
      const shown = card()!;
      expect(shown.className).toContain('evidence-hovercard--above');
      expect(shown.style.top).toBe('142px'); // chip top 150 - 8 px gap
      expect(shown.hasAttribute('data-anchored')).toBe(false);
    });

    it('hides the card on scroll (it is position:fixed and would otherwise drift)', () => {
      stubAnchorSupport(false);
      render(SOURCE);
      fire('focus-a');
      expect(card()).not.toBeNull();
      // The hook listens in the capture phase; dispatch a window scroll.
      act(() => window.dispatchEvent(new Event('scroll')));
      expect(card()).toBeNull();
    });

    it('hides the card on resize', () => {
      stubAnchorSupport(false);
      render(SOURCE);
      fire('focus-a');
      expect(card()).not.toBeNull();
      act(() => window.dispatchEvent(new Event('resize')));
      expect(card()).toBeNull();
    });
  });

  describe('CSS anchor positioning', () => {
    it('names the chip only while its card is open, anchors the card to it, and follows it on scroll', () => {
      stubAnchorSupport(true);
      const setProperty = vi.spyOn(CSSStyleDeclaration.prototype, 'setProperty');
      const removeProperty = vi.spyOn(CSSStyleDeclaration.prototype, 'removeProperty');
      render(SOURCE);
      expect(setProperty.mock.calls.filter(([name]) => name === 'anchor-name')).toEqual([]);

      fire('focus-a');
      const shown = card()!;
      expect(shown.hasAttribute('data-anchored')).toBe(true);
      const named = setProperty.mock.calls.filter(([name]) => name === 'anchor-name');
      expect(named).toHaveLength(1);
      const [, anchorName] = named[0];
      expect(anchorName).toMatch(/^--evidence-anchor[\w-]+$/);
      expect(setProperty.mock.contexts[setProperty.mock.calls.findIndex(([name]) => name === 'anchor-name')]).toBe(anchor('a').style);
      expect((shown.style as CSSStyleDeclaration & { positionAnchor?: string }).positionAnchor).toBe(anchorName);
      expect(shown.style.top, 'no fixed coordinates in anchor mode').toBe('');

      act(() => window.dispatchEvent(new Event('scroll')));
      expect(card(), 'an anchored card follows its chip instead of hiding').not.toBeNull();

      fire('blur-a');
      expect(card()).toBeNull();
      expect(removeProperty.mock.calls.filter(([name]) => name === 'anchor-name')).toHaveLength(1);
    });

    it('gives each chip its own anchor name', () => {
      stubAnchorSupport(true);
      const setProperty = vi.spyOn(CSSStyleDeclaration.prototype, 'setProperty');
      render(SOURCE);
      fire('focus-a');
      fire('focus-b');
      const names = setProperty.mock.calls.filter(([name]) => name === 'anchor-name').map(([, value]) => value);
      expect(new Set(names).size).toBe(2);
    });
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

  it('still opens the full drawer on click', () => {
    act(() => root.render(<EvidenceChip source={SOURCE}>Source</EvidenceChip>));
    act(() => container.querySelector<HTMLButtonElement>('.evidence-chip')!.click());
    expect(setDrawer).toHaveBeenCalledWith(SOURCE);
  });
});
