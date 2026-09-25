/**
 * @vitest-environment happy-dom
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useRef } from 'react';
import { escapeLayerCount } from '../lib/escapeStack';
import { focusableElements, useFocusTrap } from './useFocusTrap';

function TrapHarness({ open, onClose, restoreFocus }: { open: boolean; onClose: () => void; restoreFocus?: boolean }) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const initialRef = useRef<HTMLButtonElement | null>(null);
  useFocusTrap({ open, containerRef: panelRef, initialFocusRef: initialRef, onClose, restoreFocus });

  if (!open) return null;
  return (
    <div ref={panelRef} role="dialog" tabIndex={-1}>
      <button ref={initialRef} type="button">Close</button>
      <a href="/asset">Asset</a>
      <button type="button">Done</button>
    </div>
  );
}

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
  });
}

describe('useFocusTrap', () => {
  let root: Root;
  const onClose = vi.fn();

  beforeEach(() => {
    document.body.innerHTML = '<button id="launcher">Open drawer</button><div id="root"></div><button id="outside">Outside</button>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    onClose.mockClear();
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  async function render(open: boolean): Promise<void> {
    await act(async () => {
      root.render(<TrapHarness open={open} onClose={onClose} />);
    });
    await settle();
  }

  it('focuses inside, cycles Tab and Shift+Tab, closes on Escape, and restores focus', async () => {
    const launcher = document.getElementById('launcher') as HTMLButtonElement;
    launcher.focus();

    await render(true);
    // The open trap is one layer on the shared Escape stack (runtime-v2).
    expect(escapeLayerCount()).toBe(1);

    const buttons = Array.from(document.querySelectorAll('button'));
    const close = buttons.find((button) => button.textContent === 'Close');
    const done = buttons.find((button) => button.textContent === 'Done');
    expect(document.activeElement).toBe(close);

    done?.focus();
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab' }));
    });
    expect(document.activeElement).toBe(close);

    close?.focus();
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true }));
    });
    expect(document.activeElement).toBe(done);

    const outside = document.getElementById('outside') as HTMLButtonElement;
    outside.focus();
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab' }));
    });
    expect(document.activeElement).toBe(close);

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    });
    expect(onClose).toHaveBeenCalledTimes(1);

    await render(false);
    expect(document.activeElement).toBe(launcher);
    expect(escapeLayerCount()).toBe(0);
  });

  // stack-04 tail: onClose is an Effect Event, not a trap dependency. A
  // caller that passes a fresh inline handler on every render must neither
  // re-run the trap (which re-focused the initial target, and restored and
  // re-took focus in between) nor leave Escape calling a stale handler.
  it('calls the latest onClose on Escape without re-running the trap when the handler changes', async () => {
    (document.getElementById('launcher') as HTMLButtonElement).focus();
    let initialFocuses = 0;
    const countInitialFocus = (event: FocusEvent) => {
      if ((event.target as HTMLElement | null)?.textContent === 'Close') initialFocuses += 1;
    };
    document.addEventListener('focusin', countInitialFocus);
    try {
      const first = vi.fn();
      const second = vi.fn();
      await act(async () => {
        root.render(<TrapHarness open onClose={first} />);
      });
      await settle();
      expect(initialFocuses).toBe(1);

      await act(async () => {
        root.render(<TrapHarness open onClose={second} />);
      });
      await settle();
      expect(initialFocuses, 'the initial target is focused once, not again on a new handler').toBe(1);
      expect(escapeLayerCount()).toBe(1);

      await act(async () => {
        window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
      });
      expect(first).not.toHaveBeenCalled();
      expect(second).toHaveBeenCalledTimes(1);
    } finally {
      document.removeEventListener('focusin', countInitialFocus);
    }
  });

  it('leaves focus where it is on close when restoreFocus is false (useModalDialog restores it)', async () => {
    const launcher = document.getElementById('launcher') as HTMLButtonElement;
    launcher.focus();
    await act(async () => {
      root.render(<TrapHarness open onClose={onClose} restoreFocus={false} />);
    });
    await settle();
    const outside = document.getElementById('outside') as HTMLButtonElement;
    outside.focus();
    await act(async () => {
      root.render(<TrapHarness open={false} onClose={onClose} restoreFocus={false} />);
    });
    await settle();
    expect(document.activeElement).toBe(outside);
  });
});

/**
 * The Tab wrap cycles only through real tab stops (audit a11y-07). The
 * selector used to match elements Tab skips (inside an `inert` subtree, not
 * rendered) and to miss ones it visits (`summary`, `contenteditable`), so
 * the wrap either never fired (the last listed element was one Tab skips,
 * and focus walked out of the dialog) or skipped a real stop.
 */
describe('useFocusTrap tab stops', () => {
  let root: Root;
  const realCheckVisibility = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'checkVisibility');

  function StopsHarness() {
    const panelRef = useRef<HTMLDivElement | null>(null);
    const firstRef = useRef<HTMLButtonElement | null>(null);
    useFocusTrap({ open: true, containerRef: panelRef, initialFocusRef: firstRef, onClose: () => undefined });
    return (
      <div ref={panelRef} role="dialog" tabIndex={-1}>
        <button type="button" data-hidden="" id="hidden-first">Hidden first</button>
        <button ref={firstRef} type="button" id="first">First</button>
        <details>
          <summary id="summary">More detail</summary>
        </details>
        <div contentEditable suppressContentEditableWarning id="editable">Notes</div>
        <div contentEditable="false" suppressContentEditableWarning id="not-editable">Read only</div>
        <button type="button" id="last">Last</button>
        <div inert>
          <button type="button" id="inert-after">Inert</button>
        </div>
        <button type="button" data-hidden="" id="hidden-after">Hidden</button>
      </div>
    );
  }

  beforeEach(() => {
    // happy-dom has no checkVisibility: model "not rendered" with a marker.
    Object.defineProperty(HTMLElement.prototype, 'checkVisibility', {
      configurable: true,
      value(this: HTMLElement) {
        return !this.hasAttribute('data-hidden');
      },
    });
    document.body.innerHTML = '<div id="root"></div><button id="page-control">Page</button>';
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    if (realCheckVisibility) Object.defineProperty(HTMLElement.prototype, 'checkVisibility', realCheckVisibility);
    else delete (HTMLElement.prototype as { checkVisibility?: unknown }).checkVisibility;
  });

  function tab(shiftKey = false): KeyboardEvent {
    const event = new KeyboardEvent('keydown', { key: 'Tab', shiftKey, cancelable: true });
    window.dispatchEvent(event);
    return event;
  }

  it('wraps from the last real stop, skipping inert and unrendered elements after it', async () => {
    await act(async () => {
      root.render(<StopsHarness />);
    });
    await settle();
    const last = document.getElementById('last') as HTMLButtonElement;
    last.focus();
    const event = tab();
    expect(event.defaultPrevented).toBe(true);
    expect(document.activeElement?.id).toBe('first');
  });

  it('wraps backwards from the first real stop, skipping an unrendered element before it', async () => {
    await act(async () => {
      root.render(<StopsHarness />);
    });
    await settle();
    (document.getElementById('first') as HTMLButtonElement).focus();
    const event = tab(true);
    expect(event.defaultPrevented).toBe(true);
    expect(document.activeElement?.id).toBe('last');
  });

  it('counts summary and contenteditable as stops, never contenteditable="false"', async () => {
    await act(async () => {
      root.render(<StopsHarness />);
    });
    await settle();
    const container = document.querySelector<HTMLElement>('[role="dialog"]')!;
    expect(focusableElements(container).map((element) => element.id)).toEqual(['first', 'summary', 'editable', 'last']);
  });
});
