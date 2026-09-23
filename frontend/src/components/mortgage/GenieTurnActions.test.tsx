/**
 * @vitest-environment happy-dom
 *
 * Per-turn Genie controls (audit 2026-09-21 `genie-03`): every button's
 * accessible name contains its visible text (WCAG 2.5.3 Label in Name), so a
 * speech-input user who says what they see ("click Ask again") reaches it.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { GenieTurnActions } from './GenieTurnActions';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

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

/** The accessible name as a browser computes it for these plain buttons. */
function accessibleName(button: HTMLButtonElement): string {
  return (button.getAttribute('aria-label') ?? button.textContent ?? '').trim();
}

describe('GenieTurnActions', () => {
  it('names every control with its visible text (Label in Name)', () => {
    act(() =>
      root.render(
        <GenieTurnActions
          question="Which states lead?"
          onEdit={vi.fn()}
          onRetry={vi.fn()}
          onRegenerate={vi.fn()}
          onAskAgain={vi.fn()}
          placement="question"
        />,
      ),
    );
    const buttons = Array.from(container.querySelectorAll<HTMLButtonElement>('button'));
    expect(buttons.map((button) => button.textContent)).toEqual(['Edit', 'Retry', 'Regenerate', 'Ask again']);
    for (const button of buttons) {
      const visible = (button.textContent ?? '').trim().toLowerCase();
      expect(accessibleName(button).toLowerCase(), `"${visible}"`).toContain(visible);
    }
  });

  it('Ask again re-asks the question it sits under', () => {
    const onAskAgain = vi.fn();
    act(() => root.render(<GenieTurnActions question="Which states lead?" onAskAgain={onAskAgain} placement="question" />));
    const askAgain = container.querySelector<HTMLButtonElement>('button');
    expect(accessibleName(askAgain!)).toBe('Ask again');
    act(() => askAgain!.click());
    expect(onAskAgain).toHaveBeenCalledWith('Which states lead?');
  });
});
