/**
 * @vitest-environment happy-dom
 *
 * The conversation link's unavailable state: Retry is aria-disabled (never
 * native disabled, so it keeps keyboard focus) and inert while a retry runs.
 * The route-level focus and request proofs live in
 * routes/ask-genie.deep-link.test.tsx.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import GenieConversationLinkState from './GenieConversationLinkState';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('GenieConversationLinkState Retry', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function renderRetry(retrying: boolean, onRetry: () => void): HTMLButtonElement {
    act(() => {
      root.render(
        <GenieConversationLinkState state="unavailable" onOpenAskGenie={() => undefined} onRetry={onRetry} retrying={retrying} />,
      );
    });
    const retry = Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.startsWith('Retry'));
    if (!retry) throw new Error('Retry not rendered');
    return retry;
  }

  it('a press while a retry runs does nothing; otherwise it retries once', () => {
    const onRetry = vi.fn();
    const running = renderRetry(true, onRetry);
    expect(running.textContent).toBe('Retrying…');
    expect(running.disabled).toBe(false);
    expect(running.getAttribute('aria-disabled')).toBe('true');
    act(() => running.click());
    expect(onRetry).not.toHaveBeenCalled();

    const idle = renderRetry(false, onRetry);
    expect(idle.hasAttribute('aria-disabled')).toBe(false);
    act(() => idle.click());
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('a retry in flight wears the shared pending style (Button loading) and keeps its label and focus', () => {
    const running = renderRetry(true, () => undefined);
    act(() => running.focus());
    expect(running.classList.contains('btn--loading')).toBe(true);
    expect(running.classList.contains('btn--primary')).toBe(true);
    expect(running.getAttribute('aria-busy')).toBe('true');
    expect(running.querySelector('.btn__spinner')?.getAttribute('aria-hidden')).toBe('true');
    expect(running.querySelector('.btn__label')?.textContent).toBe('Retrying…');

    // Settling back to idle swaps the markup in place: same button, focus kept.
    const idle = renderRetry(false, () => undefined);
    expect(idle).toBe(running);
    expect(document.activeElement).toBe(idle);
    expect(idle.classList.contains('btn--loading')).toBe(false);
    expect(idle.hasAttribute('aria-busy')).toBe(false);
    expect(idle.textContent).toBe('Retry');
  });
});
