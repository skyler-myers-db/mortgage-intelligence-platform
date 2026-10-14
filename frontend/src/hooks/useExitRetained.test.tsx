// @vitest-environment happy-dom

import { act, useRef } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useExitPresence, useExitRetained } from './useExitRetained';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * useExitRetained: which transition events end a panel's exit.
 *
 * The Console (motion-01 follow-up) stays mounted through its exit only while
 * this hook retains it. A panel closed in the frame its ENTRY finished (or
 * mid-entry, which reverses the entry) receives that entry's transitionend /
 * transitioncancel AFTER the close, while its exit transitions are already
 * running; releasing on it swapped the Console out 0ms into its fade. The
 * rendered proof is shell-wayfinding.fixture.spec.ts; happy-dom has neither
 * Web Animations nor CSSTransition, so both are stubbed on the panel here.
 */

class FakeCssTransition {
  constructor(public playState: AnimationPlayState) {}
}

let running: FakeCssTransition[] = [];
let container: HTMLDivElement;
let root: Root;

function Panel({ value }: { value: string | null }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const shown = useExitRetained(value, ref);
  return (
    <div
      ref={(element) => {
        ref.current = element;
        if (element) {
          (element as unknown as { getAnimations: () => FakeCssTransition[] }).getAnimations = () => running;
        }
      }}
      id="panel"
      style={{ transitionProperty: 'opacity', transitionDuration: '120ms' }}
    >
      <span id="child">{shown ?? ''}</span>
    </div>
  );
}

async function render(value: string | null) {
  await act(async () => {
    root.render(<Panel value={value} />);
  });
}

const panel = () => container.querySelector<HTMLDivElement>('#panel')!;
const shown = () => container.querySelector('#child')?.textContent ?? '';
const fire = (target: Element, type: 'transitionend' | 'transitioncancel') =>
  act(() => {
    target.dispatchEvent(new Event(type, { bubbles: true }));
  });

describe('useExitRetained', () => {
  beforeEach(() => {
    (globalThis as unknown as { CSSTransition: unknown }).CSSTransition = FakeCssTransition;
    running = [];
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    delete (globalThis as unknown as { CSSTransition?: unknown }).CSSTransition;
  });

  it('keeps the closing value through a stale entry event while the exit still runs', async () => {
    await render('dossier');
    await render(null);
    expect(shown(), 'retained through the exit').toBe('dossier');

    // The entry's own end / cancel arrives after the close; the exit runs.
    running = [new FakeCssTransition('running')];
    fire(panel(), 'transitionend');
    fire(panel(), 'transitioncancel');
    expect(shown(), 'a stale entry event does not end the exit').toBe('dossier');

    // The exit's own end: nothing of the panel still runs.
    running = [new FakeCssTransition('finished')];
    fire(panel(), 'transitionend');
    expect(shown()).toBe('');
  });

  it("ignores a child's transition and releases on the panel's own end", async () => {
    await render('dossier');
    await render(null);
    fire(container.querySelector('#child')!, 'transitionend');
    expect(shown()).toBe('dossier');
    fire(panel(), 'transitionend');
    expect(shown()).toBe('');
  });
});

/**
 * The deadline (computed 120 ms + 120 ms grace on the Panel above) is wall
 * clock; the exit runs on the document timeline, which a starved renderer
 * advances late (Linux WebKit on CI). The rendered proof is the History menu
 * case in genie-context-deeplink.cross-engine.fixture.spec.ts.
 */
describe('useExitRetained deadline', () => {
  const DEADLINE_MS = 240;
  const advance = (ms: number) =>
    act(() => {
      vi.advanceTimersByTime(ms);
    });

  beforeEach(() => {
    vi.useFakeTimers();
    (globalThis as unknown as { CSSTransition: unknown }).CSSTransition = FakeCssTransition;
    running = [];
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    delete (globalThis as unknown as { CSSTransition?: unknown }).CSSTransition;
    vi.useRealTimers();
  });

  it('holds an exit still running at the deadline until its own transitionend', async () => {
    await render('dossier');
    running = [new FakeCssTransition('running')];
    await render(null);
    advance(DEADLINE_MS * 2);
    expect(shown(), 'a late exit is not cut at the deadline').toBe('dossier');
    running = [new FakeCssTransition('finished')];
    fire(panel(), 'transitionend');
    expect(shown()).toBe('');
  });

  it('holds an exit that finished on the timeline until its transitionend is dispatched', async () => {
    // WebKit reports a transition `finished` (and drops it from
    // getAnimations) before the rendering update that sends its transitionend.
    await render('dossier');
    const exit = new FakeCssTransition('running');
    running = [exit];
    await render(null);
    exit.playState = 'finished';
    running = [];
    advance(DEADLINE_MS);
    expect(shown(), 'its transitionend is still owed').toBe('dossier');
    fire(panel(), 'transitionend');
    expect(shown()).toBe('');
  });

  it('releases at the deadline when the exit was cancelled (display:none) or never ran', async () => {
    await render('dossier');
    const exit = new FakeCssTransition('running');
    running = [exit];
    await render(null);
    exit.playState = 'idle';
    running = [];
    advance(DEADLINE_MS - 1);
    expect(shown()).toBe('dossier');
    advance(1);
    expect(shown(), 'a cancelled exit owes no transitionend').toBe('');

    await render('brief');
    await render(null);
    advance(DEADLINE_MS);
    expect(shown(), 'no exit transition at all').toBe('');
  });

  it('gives a running exit at most four more deadlines', async () => {
    await render('dossier');
    running = [new FakeCssTransition('running')];
    await render(null);
    advance(DEADLINE_MS * 5 - 1);
    expect(shown(), 'four extensions').toBe('dossier');
    advance(1);
    expect(shown(), 'a timeline that never ticks cannot hold the panel').toBe('');
  });
});

/**
 * useExitPresence (motion-01 item 2e): the boolean presence the unmounting
 * overlays render on. The menu below follows the caller contract: rendered
 * while present, `is-closing` + aria-hidden + inert while closing.
 */
type RenderLog = Array<{ present: boolean; closing: boolean }>;
let renders: RenderLog = [];

function Menu({ open, animated }: { open: boolean; animated: boolean }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const { present, closing } = useExitPresence(open, ref);
  renders.push({ present, closing });
  if (!present) return null;
  return (
    <div
      ref={(element) => {
        ref.current = element;
        if (element) {
          (element as unknown as { getAnimations: () => FakeCssTransition[] }).getAnimations = () => running;
        }
      }}
      id="menu"
      className={closing ? 'menu is-closing' : 'menu'}
      aria-hidden={closing || undefined}
      inert={closing}
      style={animated ? { transitionProperty: 'opacity', transitionDuration: '216ms' } : undefined}
    />
  );
}

async function renderMenu(open: boolean, animated = true) {
  await act(async () => {
    root.render(<Menu open={open} animated={animated} />);
  });
}

const menu = () => container.querySelector<HTMLDivElement>('#menu');

describe('useExitPresence', () => {
  let matchMedia: typeof window.matchMedia;

  beforeEach(() => {
    (globalThis as unknown as { CSSTransition: unknown }).CSSTransition = FakeCssTransition;
    running = [];
    renders = [];
    matchMedia = window.matchMedia;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    window.matchMedia = matchMedia;
    delete (globalThis as unknown as { CSSTransition?: unknown }).CSSTransition;
  });

  it("with no transition, drops the element in the closing commit's effect", async () => {
    await renderMenu(true, false);
    renders = [];
    await renderMenu(false, false);
    expect(menu()).toBeNull();
    // The closing commit rendered once as closing, then its effect dropped it.
    expect(renders).toEqual([
      { present: true, closing: true },
      { present: false, closing: false },
    ]);
  });

  it('drops at once under reduced motion, even with a transition declared', async () => {
    window.matchMedia = ((query: string) => ({
      matches: query.includes('reduce'),
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    })) as unknown as typeof window.matchMedia;
    await renderMenu(true);
    running = [new FakeCssTransition('running')];
    await renderMenu(false);
    expect(menu()).toBeNull();
  });

  it('keeps a running exit present, closing, aria-hidden and inert until its transitionend', async () => {
    await renderMenu(true);
    expect(menu()?.className).toBe('menu');
    running = [new FakeCssTransition('running')];
    await renderMenu(false);
    const closingMenu = menu();
    expect(closingMenu?.className).toBe('menu is-closing');
    expect(closingMenu?.getAttribute('aria-hidden')).toBe('true');
    expect(closingMenu?.hasAttribute('inert')).toBe(true);
    fire(closingMenu!, 'transitionend');
    expect(menu(), 'a transition still runs').not.toBeNull();
    running = [new FakeCssTransition('finished')];
    fire(closingMenu!, 'transitionend');
    expect(menu()).toBeNull();
  });

  it('reopening during the exit keeps the same element, so no id is duplicated', async () => {
    await renderMenu(true);
    const opened = menu();
    running = [new FakeCssTransition('running')];
    await renderMenu(false);
    expect(menu()).toBe(opened);
    await renderMenu(true);
    expect(menu()).toBe(opened);
    expect(menu()?.className).toBe('menu');
    expect(menu()?.hasAttribute('aria-hidden')).toBe(false);
    expect(container.querySelectorAll('#menu')).toHaveLength(1);
    // The interrupted exit's own end no longer drops the reopened menu.
    running = [new FakeCssTransition('finished')];
    fire(opened!, 'transitionend');
    expect(menu()).toBe(opened);
  });
});
