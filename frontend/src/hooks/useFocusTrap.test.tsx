/**
 * @vitest-environment happy-dom
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useRef } from 'react';
import { useFocusTrap } from './useFocusTrap';

function TrapHarness({ open, onClose }: { open: boolean; onClose: () => void }) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const initialRef = useRef<HTMLButtonElement | null>(null);
  useFocusTrap({ open, containerRef: panelRef, initialFocusRef: initialRef, onClose });

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
  });
});
