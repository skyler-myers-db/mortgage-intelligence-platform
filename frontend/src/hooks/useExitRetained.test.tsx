// @vitest-environment happy-dom

import { act, useRef } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { useExitRetained } from './useExitRetained';

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
