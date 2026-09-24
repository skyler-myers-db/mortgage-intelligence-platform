/**
 * @vitest-environment happy-dom
 *
 * GenieDock: the shell's Genie mount point (audit 2026-09-21 `runtime-01` /
 * `genie-02`). The defect lived HERE: `{genieOpen ? <LazyGenieChat/> : null}`
 * unmounted the chat on every close, and the chat aborted its in-flight ask on
 * unmount. These tests pin the mount lifecycle at the rendered layer, and the
 * Genie panel boundary (audit `states-01`): a throwing or unloadable chat
 * shows the recovery surface inside the Genie frame, never at the root.
 */

import { act, lazy, useEffect, useReducer, type ComponentType } from 'react';
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
  const onClose = vi.fn();
  const onWarm = vi.fn();

  beforeEach(() => {
    lifecycle.mounts = 0;
    lifecycle.unmounts = 0;
    onOpen.mockReset();
    onClose.mockReset();
    onWarm.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function render(open: boolean, Chat: ComponentType = ChatProbe) {
    act(() => {
      root.render(<GenieDock open={open} onOpen={onOpen} onClose={onClose} onWarm={onWarm} Chat={Chat} />);
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

  describe('panel boundary (states-01)', () => {
    const surface = () => container.querySelector<HTMLElement>('[data-testid="error-surface"]');
    const buttonLabels = () => Array.from(surface()?.querySelectorAll('button') ?? []).map((b) => b.textContent);

    beforeEach(() => {
      vi.spyOn(console, 'error').mockImplementation(() => undefined);
    });

    afterEach(() => {
      vi.restoreAllMocks();
    });

    function flakyChat() {
      const state = { throwing: true, mounts: 0 };
      function FlakyChat() {
        useEffect(() => {
          state.mounts += 1;
        }, []);
        if (state.throwing) throw new TypeError("Cannot read properties of null (reading 'turns') B-0TESTBORROWER");
        return <div data-testid="genie-chat-probe" />;
      }
      return { state, FlakyChat };
    }

    it('shows the recovery surface inside the open Genie frame, with no message and no second h1', () => {
      const { FlakyChat } = flakyChat();
      render(true, FlakyChat);

      const panel = container.querySelector('.genie.is-open[role="dialog"][aria-label="Genie chat"]');
      expect(panel?.contains(surface())).toBe(true);
      expect(panel?.hasAttribute('aria-modal')).toBe(false);
      expect(surface()?.getAttribute('data-error-boundary')).toBe('genie');
      expect(surface()?.getAttribute('data-error-kind')).toBe('render');
      expect(surface()?.className).toContain('error-surface--panel');
      expect(surface()?.querySelector('h2.h-4')?.textContent).toBe('Genie hit an unexpected error');
      expect(container.querySelector('h1')).toBeNull();
      expect(buttonLabels()).toEqual(['Try again', 'Reload']);
      expect(container.innerHTML).not.toContain('B-0TESTBORROWER');
      expect(container.innerHTML).not.toContain('Cannot read');
    });

    it("the frame's Close calls onClose and a closed frame hides the panel", () => {
      const { FlakyChat } = flakyChat();
      render(true, FlakyChat);
      act(() => container.querySelector<HTMLButtonElement>('button[aria-label="Close Genie"]')?.click());
      expect(onClose).toHaveBeenCalledTimes(1);

      render(false, FlakyChat);
      expect(container.querySelector('.genie[role="dialog"]')?.classList.contains('is-open')).toBe(false);
      expect(container.querySelector('.genie__fab')?.classList.contains('is-hidden')).toBe(false);
      // Reopening shows the same surface: closing never clears the error.
      render(true, FlakyChat);
      expect(surface()?.getAttribute('data-error-boundary')).toBe('genie');
    });

    it('puts focus on the frame\'s Close and lets Escape close the panel while focus is inside', async () => {
      const { FlakyChat } = flakyChat();
      render(true, FlakyChat);
      const close = container.querySelector<HTMLButtonElement>('button[aria-label="Close Genie"]');
      await vi.waitFor(() => expect(document.activeElement).toBe(close));

      act(() => {
        window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
      });
      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it('closing the frame of a chat that crashed with focus inside sends focus to the topbar Genie toggle, not <body>', async () => {
      // The crash removes the focused element, so the frame opens with <body>
      // focused and must not record <body> as the element to return focus to.
      const topbarToggle = document.createElement('button');
      topbarToggle.setAttribute('aria-label', 'Toggle Genie chat');
      document.body.appendChild(topbarToggle);
      const chat = { crashing: false, rerender: () => undefined as void };
      function FocusedChat() {
        const [, bump] = useReducer((n: number) => n + 1, 0);
        useEffect(() => {
          chat.rerender = bump;
        }, []);
        if (chat.crashing) throw new TypeError('Genie chat crashed');
        return <input data-testid="genie-chat-input" aria-label="Ask Genie" />;
      }
      try {
        render(true, FocusedChat);
        const input = container.querySelector<HTMLInputElement>('[data-testid="genie-chat-input"]');
        act(() => input?.focus());
        expect(document.activeElement).toBe(input);

        chat.crashing = true;
        act(() => chat.rerender());
        expect(surface()?.getAttribute('data-error-boundary')).toBe('genie');
        const close = container.querySelector<HTMLButtonElement>('button[aria-label="Close Genie"]');
        await vi.waitFor(() => expect(document.activeElement).toBe(close));

        act(() => close?.click());
        expect(onClose).toHaveBeenCalledTimes(1);
        render(false, FocusedChat);
        expect(document.activeElement).toBe(topbarToggle);
      } finally {
        topbarToggle.remove();
      }
    });

    it('Try again re-mounts the chat once', () => {
      const { state, FlakyChat } = flakyChat();
      render(true, FlakyChat);
      expect(state.mounts).toBe(0);

      state.throwing = false;
      act(() =>
        Array.from(container.querySelectorAll('button'))
          .find((b) => b.textContent === 'Try again')
          ?.click(),
      );
      expect(surface()).toBeNull();
      expect(probe()).not.toBeNull();
      expect(state.mounts).toBe(1);

      render(false, FlakyChat);
      render(true, FlakyChat);
      expect(state.mounts).toBe(1);
    });

    it('a failed chat chunk offers Reload only', async () => {
      const StaleChat = lazy<ComponentType>(() =>
        Promise.reject(new TypeError('Failed to fetch dynamically imported module: /assets/GenieChat-0ld.js')),
      );
      await act(async () => {
        root.render(<GenieDock open onOpen={onOpen} onClose={onClose} onWarm={onWarm} Chat={StaleChat} />);
      });
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
      });

      expect(surface()?.getAttribute('data-error-kind')).toBe('chunk');
      expect(surface()?.querySelector('h2')?.textContent).toBe('A new version is available');
      expect(surface()?.textContent).toContain('Genie could not finish loading');
      expect(buttonLabels()).toEqual(['Reload']);
      expect(container.innerHTML).not.toContain('GenieChat-0ld');
    });

    it('the dock stays mounted around a crashed chat: its open-request subscription still opens the panel', async () => {
      const { FlakyChat } = flakyChat();
      render(false, FlakyChat);
      render(true, FlakyChat);
      expect(surface()).not.toBeNull();
      render(false, FlakyChat);

      const { consumeGeniePrefill, openGenie } = await import('../../lib/genieOpen');
      act(() => openGenie({ prompt: 'Compare mean lead score by state.' }));
      expect(onOpen).toHaveBeenCalledTimes(1);
      consumeGeniePrefill();
      expect(container.querySelector('.genie[role="dialog"]')).not.toBeNull();
    });
  });
});
