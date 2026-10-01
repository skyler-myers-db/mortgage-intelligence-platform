/**
 * @vitest-environment happy-dom
 *
 * Tooltip primitive contract (2026-09-21 audit critic-08): hover after the
 * shared delay with skip-delay between neighbours, keyboard-only focus
 * opening, Escape through the escapeStack (consumed, focus kept, layer
 * popped), touch ignored, aria-describedby MERGED with a persistent hidden
 * description (and pointed at the popup while open), the `<kbd>` shortcut
 * slot, and no timer or popup left behind by an unmounted trigger.
 *
 * ui/Tooltip.tsx is the render shell; ui/tooltipController.ts (imported here
 * so its delegated document listeners are installed) owns the behaviour, so
 * the tests dispatch the native events a browser would. happy-dom's
 * CSS.supports answers true for anything, so each test pins the placement
 * mode it exercises.
 */
import { act, createRef, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { escapeLayerCount, pushEscapeLayer } from '../../lib/escapeStack';
import { REOPEN_GRACE_MS, SHOW_DELAY_MS } from './anchorPlacement';
import { Tooltip } from './Tooltip';
import { TOOLTIP_POPUP_ID } from './tooltipController';

function stubAnchorSupport(supported: boolean): void {
  vi.stubGlobal('CSS', {
    supports: (condition: string) => (/anchor-name/.test(condition) ? supported : true),
    escape: (value: string) => value,
  });
}

let container: HTMLDivElement;
let root: Root;
/** A clock that only moves forward, so no test starts inside another's reopen grace. */
let epoch = Date.UTC(2026, 8, 30);

function render(node: ReactNode): void {
  act(() => root.render(node));
}

function button(name: string): HTMLButtonElement {
  const found = container.querySelector<HTMLButtonElement>(`button[aria-label="${name}"]`);
  if (!found) throw new Error(`no button ${name}`);
  return found;
}

/** The open popup, or null. */
function tooltip(): HTMLElement | null {
  return document.body.querySelector<HTMLElement>('[role="tooltip"]:not([hidden])');
}

function hover(element: Element, pointerType = 'mouse'): void {
  act(() => {
    element.dispatchEvent(new PointerEvent('pointerover', { bubbles: true, pointerType }));
  });
}

function leave(element: Element): void {
  act(() => {
    element.dispatchEvent(
      new PointerEvent('pointerout', { bubbles: true, pointerType: 'mouse', relatedTarget: document.body }),
    );
  });
}

function keyboardFocus(element: HTMLElement): void {
  act(() => {
    document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }));
    element.focus();
  });
}

function pointerFocus(element: HTMLElement): void {
  act(() => {
    element.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerType: 'mouse' }));
    element.focus();
  });
}

function escape(): void {
  act(() => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  });
}

function advance(ms: number): void {
  act(() => {
    vi.advanceTimersByTime(ms);
  });
}

function Pair() {
  return (
    <>
      <Tooltip content="Toggle theme">
        <button type="button" aria-label="Toggle theme">t</button>
      </Tooltip>
      <Tooltip content="Toggle console">
        <button type="button" aria-label="Toggle console">c</button>
      </Tooltip>
    </>
  );
}

describe('Tooltip', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    // The reopen grace is module-wide: start past the previous test's close.
    epoch += 60_000;
    vi.setSystemTime(epoch);
    stubAnchorSupport(true);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    (document.activeElement as HTMLElement | null)?.blur?.();
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('opens after the hover delay, names the popup in aria-describedby, and closes on leave', () => {
    render(<Pair />);
    const theme = button('Toggle theme');
    const descId = theme.getAttribute('aria-describedby');

    hover(theme);
    expect(tooltip()).toBeNull();
    advance(SHOW_DELAY_MS - 1);
    expect(tooltip()).toBeNull();
    advance(1);
    expect(tooltip()?.textContent).toBe('Toggle theme');
    expect(tooltip()?.id).toBe(TOOLTIP_POPUP_ID);
    expect(theme.getAttribute('aria-describedby')).toBe(TOOLTIP_POPUP_ID);

    leave(theme);
    expect(tooltip()).toBeNull();
    expect(theme.getAttribute('aria-describedby')).toBe(descId);
  });

  it('skips the delay for a neighbour inside the reopen grace, and only there', () => {
    render(<Pair />);
    hover(button('Toggle theme'));
    advance(SHOW_DELAY_MS);
    leave(button('Toggle theme'));

    hover(button('Toggle console'));
    expect(tooltip()?.textContent).toBe('Toggle console');
    leave(button('Toggle console'));

    advance(REOPEN_GRACE_MS + 1);
    hover(button('Toggle theme'));
    expect(tooltip()).toBeNull();
    advance(SHOW_DELAY_MS);
    expect(tooltip()?.textContent).toBe('Toggle theme');
  });

  it('opens at once on keyboard focus but never on pointer focus, and closes on blur', () => {
    render(<Pair />);
    const theme = button('Toggle theme');

    pointerFocus(theme);
    advance(SHOW_DELAY_MS);
    expect(tooltip()).toBeNull();
    act(() => theme.blur());

    keyboardFocus(theme);
    expect(tooltip()?.textContent).toBe('Toggle theme');
    act(() => theme.blur());
    expect(tooltip()).toBeNull();
  });

  it('closes on pointer down', () => {
    render(<Pair />);
    const theme = button('Toggle theme');
    keyboardFocus(theme);
    expect(tooltip()).not.toBeNull();

    act(() => {
      theme.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerType: 'mouse' }));
    });
    expect(tooltip()).toBeNull();
  });

  it('takes Escape before the layer below, keeps focus on the trigger and pops its layer', () => {
    const lower = vi.fn(() => true);
    const popLower = pushEscapeLayer(lower);
    try {
      render(<Pair />);
      const theme = button('Toggle theme');
      const base = escapeLayerCount();
      keyboardFocus(theme);
      expect(escapeLayerCount()).toBe(base + 1);

      escape();

      expect(tooltip()).toBeNull();
      expect(lower).not.toHaveBeenCalled();
      expect(document.activeElement).toBe(theme);
      expect(escapeLayerCount()).toBe(base);

      escape();
      expect(lower).toHaveBeenCalledTimes(1);
    } finally {
      popLower();
    }
  });

  it('closes and pops its escape layer when the trigger unmounts', async () => {
    render(<Pair />);
    const base = escapeLayerCount();
    keyboardFocus(button('Toggle theme'));
    expect(escapeLayerCount()).toBe(base + 1);

    // The unmount watcher is a MutationObserver: flush its microtask.
    await act(async () => {
      root.render(null);
    });

    expect(tooltip()).toBeNull();
    expect(escapeLayerCount()).toBe(base);
  });

  it('never opens for a touch pointer', () => {
    render(<Pair />);
    hover(button('Toggle theme'), 'touch');
    advance(SHOW_DELAY_MS * 3);

    expect(tooltip()).toBeNull();
  });

  it('merges aria-describedby with the existing ids and keeps a hidden description', () => {
    render(
      <>
        <span id="launcher-status">Answer ready</span>
        <Tooltip content="Ask Genie">
          <button type="button" aria-label="Toggle Genie chat" aria-describedby="launcher-status">g</button>
        </Tooltip>
      </>,
    );
    const genie = button('Toggle Genie chat');

    const [existing, descId] = (genie.getAttribute('aria-describedby') ?? '').split(' ');
    expect(existing).toBe('launcher-status');
    const description = document.getElementById(descId ?? '');
    expect(description?.hidden).toBe(true);
    expect(description?.textContent).toBe('Ask Genie');
    expect(genie.getAttribute('aria-label')).toBe('Toggle Genie chat');

    keyboardFocus(genie);
    expect(genie.getAttribute('aria-describedby')).toBe(`launcher-status ${TOOLTIP_POPUP_ID}`);
    act(() => genie.blur());
    expect(genie.getAttribute('aria-describedby')).toBe(`launcher-status ${descId}`);
  });

  it('renders the shortcut in a kbd slot and in the description', () => {
    render(
      <Tooltip content="Command palette" shortcut="⌘K">
        <button type="button" aria-label="Open command palette (⌘K)">⌘K</button>
      </Tooltip>,
    );
    const trigger = button('Open command palette (⌘K)');
    const descId = trigger.getAttribute('aria-describedby') ?? '';
    expect(document.getElementById(descId)?.textContent).toBe('Command palette (⌘K)');

    keyboardFocus(trigger);
    expect(tooltip()?.querySelector('kbd.tooltip__kbd')?.textContent).toBe('⌘K');
    expect(tooltip()?.textContent).toBe('Command palette⌘K');
  });

  it('keeps the child ref and its own handlers', () => {
    const onFocus = vi.fn();
    const onPointerDown = vi.fn();
    const captured = createRef<HTMLButtonElement>();
    render(
      <Tooltip content="Refresh">
        <button ref={captured} type="button" aria-label="Refresh" onFocus={onFocus} onPointerDown={onPointerDown}>
          r
        </button>
      </Tooltip>,
    );
    const refresh = button('Refresh');

    keyboardFocus(refresh);
    act(() => {
      refresh.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerType: 'mouse' }));
    });

    expect(captured.current).toBe(refresh);
    expect(onFocus).toHaveBeenCalledTimes(1);
    expect(onPointerDown).toHaveBeenCalledTimes(1);
  });

  it('places by measured rect without anchor positioning and hides on scroll', () => {
    stubAnchorSupport(false);
    render(<Pair />);
    keyboardFocus(button('Toggle theme'));

    const tip = tooltip();
    expect(tip?.hasAttribute('data-anchored')).toBe(false);
    expect(tip?.className).toMatch(/tooltip--(above|below)/);
    expect(tip?.style.left).toMatch(/px$/);

    act(() => {
      window.dispatchEvent(new Event('scroll'));
    });
    expect(tooltip()).toBeNull();
  });

  it('anchors to the trigger with CSS anchor positioning where supported', () => {
    render(<Pair />);
    const theme = button('Toggle theme');
    keyboardFocus(theme);

    expect(tooltip()?.hasAttribute('data-anchored')).toBe(true);
    expect(theme.style.getPropertyValue('anchor-name')).toBe(tooltip()?.style.getPropertyValue('position-anchor'));
    act(() => theme.blur());
    expect(theme.style.getPropertyValue('anchor-name')).toBe('');
  });

  it('clears a pending hover timer when the trigger unmounts', async () => {
    render(<Pair />);
    const before = vi.getTimerCount();
    hover(button('Toggle theme'));
    expect(vi.getTimerCount()).toBe(before + 1);

    await act(async () => {
      root.render(null);
    });

    expect(vi.getTimerCount()).toBe(before);
    advance(SHOW_DELAY_MS);
    expect(tooltip()).toBeNull();
  });
});
