/**
 * @vitest-environment happy-dom
 *
 * GenieDock: the shell's Genie mount point (audit 2026-09-21 `runtime-01` /
 * `genie-02`). The defect lived HERE: `{genieOpen ? <LazyGenieChat/> : null}`
 * unmounted the chat on every close, and the chat aborted its in-flight ask on
 * unmount. These tests pin the mount lifecycle at the rendered layer.
 */

import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { GenieDock } from './GenieDock';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const lifecycle = { mounts: 0, unmounts: 0 };

function ChatProbe() {
  useEffect(() => {
    lifecycle.mounts += 1;
    return () => {
      lifecycle.unmounts += 1;
    };
  }, []);
  return <div data-testid="genie-chat-probe" />;
}

describe('GenieDock', () => {
  let container: HTMLDivElement;
  let root: Root;
  const onOpen = vi.fn();
  const onWarm = vi.fn();

  beforeEach(() => {
    lifecycle.mounts = 0;
    lifecycle.unmounts = 0;
    onOpen.mockReset();
    onWarm.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function render(open: boolean) {
    act(() => {
      root.render(<GenieDock open={open} onOpen={onOpen} onWarm={onWarm} Chat={ChatProbe} />);
    });
  }

  const probe = () => container.querySelector('[data-testid="genie-chat-probe"]');
  const shellFab = () => container.querySelector<HTMLButtonElement>('button.genie__fab');

  it('does not mount the chat before the first open, and offers the shell launcher', () => {
    render(false);

    expect(probe()).toBeNull();
    expect(lifecycle.mounts).toBe(0);
    expect(shellFab()).not.toBeNull();

    act(() => shellFab()?.dispatchEvent(new MouseEvent('mouseover', { bubbles: true })));
    act(() => shellFab()?.click());
    expect(onWarm).toHaveBeenCalled();
    expect(onOpen).toHaveBeenCalledOnce();
  });

  it('mounts the chat on first open and never unmounts it on close', () => {
    render(false);
    render(true);
    expect(probe()).not.toBeNull();
    expect(lifecycle.mounts).toBe(1);

    render(false);
    // Closed is hidden, not gone: the chat (and any turn it is running) stays.
    expect(probe()).not.toBeNull();
    expect(lifecycle.unmounts).toBe(0);

    render(true);
    render(false);
    expect(lifecycle.mounts).toBe(1);
    expect(lifecycle.unmounts).toBe(0);
  });

  it('retires the shell launcher once the chat is mounted, so only one FAB exists', () => {
    render(true);
    expect(shellFab()).toBeNull();

    render(false);
    // The chat renders its own `.genie__fab` (with the running ring / ready
    // badge); a second one from the shell would stack on the same spot.
    expect(shellFab()).toBeNull();
  });
});
