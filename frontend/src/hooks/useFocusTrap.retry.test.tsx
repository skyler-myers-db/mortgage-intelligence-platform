/**
 * @vitest-environment happy-dom
 *
 * useFocusTrap's initial focus when the target refuses it for a frame (wave
 * 1c review round 2). The always-mounted evidence drawer turns visible in
 * the same commit that opens its trap; under prefers-reduced-motion every
 * element transitions `all` for 0.01ms, so its Close button stays
 * `visibility: hidden` until frames commit and the microtask focus() is a
 * no-op, leaving focus on the control BEHIND the modal drawer. The trap
 * retries on the next frames, but never takes focus back from an element
 * the reader (or other code) moved it to. The browser-level proof is the
 * evidence-drawer handoff in queue-keyboard.fixture.spec.ts.
 */
import { act, useRef } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useFocusTrap } from './useFocusTrap';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/** The first `refusals` focus() calls on the initial target do nothing, like a hidden element. */
function Panel({ open, refusals }: { open: boolean; refusals: number }) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const left = useRef(refusals);
  useFocusTrap({ open, containerRef: panelRef, initialFocusRef: closeRef, onClose: () => undefined });
  return (
    <div ref={panelRef} role="dialog" aria-modal="true" tabIndex={-1}>
      <button
        type="button"
        ref={(button) => {
          closeRef.current = button;
          if (!button) return;
          const focus = HTMLElement.prototype.focus;
          button.focus = function refusingFocus(options?: FocusOptions) {
            if (left.current > 0) {
              left.current -= 1;
              return;
            }
            focus.call(this, options);
          };
        }}
      >
        Close
      </button>
    </div>
  );
}

function frames(count: number): Promise<void> {
  return new Promise((resolve) => {
    const step = (remaining: number) => {
      if (remaining <= 0) resolve();
      else requestAnimationFrame(() => step(remaining - 1));
    };
    step(count);
  });
}

describe('useFocusTrap initial focus retry', () => {
  let root: Root;
  let opener: HTMLButtonElement;

  beforeEach(() => {
    document.body.innerHTML = '<button id="opener">Evidence</button><div id="root"></div><button id="elsewhere">Elsewhere</button>';
    opener = document.getElementById('opener') as HTMLButtonElement;
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
  });

  async function open(refusals: number) {
    opener.focus();
    await act(async () => {
      root.render(<Panel open refusals={refusals} />);
      await Promise.resolve();
    });
  }

  const close = () => document.querySelector('[role="dialog"] button');

  it('focus that did not land on open lands on a following frame', async () => {
    await open(2);
    expect(document.activeElement, 'the first attempt was refused').toBe(opener);
    await act(async () => {
      await frames(4);
    });
    expect(document.activeElement).toBe(close());
  });

  it('never takes focus back from an element focus moved to meanwhile', async () => {
    await open(1);
    expect(document.activeElement).toBe(opener);
    const elsewhere = document.getElementById('elsewhere') as HTMLButtonElement;
    elsewhere.focus();
    await act(async () => {
      await frames(4);
    });
    expect(document.activeElement).toBe(elsewhere);
  });

  it('stops retrying once the trap closes', async () => {
    const raf = vi.spyOn(window, 'requestAnimationFrame');
    await open(100);
    await act(async () => {
      root.render(<Panel open={false} refusals={0} />);
      await frames(2);
    });
    const scheduled = raf.mock.calls.length;
    await act(async () => {
      await frames(4);
    });
    // Only the test's own frame steps were scheduled after the close.
    expect(raf.mock.calls.length - scheduled).toBe(4);
    raf.mockRestore();
  });
});
