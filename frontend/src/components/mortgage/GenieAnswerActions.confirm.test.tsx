/**
 * @vitest-environment happy-dom
 *
 * A confirmed governed action stays pessimistic (audit 2026-09-21
 * `runtime-03` moved the confirm handler's try/finally out of the component
 * so it compiles): "Recording…" holds, and every action stays disabled, until
 * the action's promise settles, then the controls come back.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieActionSuggestion } from '../../types';
import { GenieActions } from './GenieAnswerActions';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ACTION: GenieActionSuggestion = {
  id: 'save-1',
  label: 'Save reviewed cohort',
  action_type: 'save_borrowers',
  description: 'Create the reviewed Lead Queue handoff.',
};

describe('GenieActions confirm', () => {
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

  const button = (label: string) => container.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`);

  it('holds "Recording…" until the action settles, then offers Run again', async () => {
    let settle: () => void = () => undefined;
    const onAction = vi.fn(() => new Promise<void>((resolve) => {
      settle = resolve;
    }));
    act(() => root.render(<GenieActions actions={[ACTION]} onAction={onAction} />));

    act(() => button('Run Save reviewed cohort')!.click());
    act(() => button('Confirm Save reviewed cohort')!.click());
    expect(onAction).toHaveBeenCalledTimes(1);
    expect(onAction).toHaveBeenCalledWith(ACTION);
    const run = button('Run Save reviewed cohort')!;
    expect(run.textContent).toContain('Recording…');
    expect(run.disabled).toBe(true);

    await act(async () => {
      settle();
      await Promise.resolve();
    });
    expect(button('Run Save reviewed cohort')!.textContent).toContain('Run');
    expect(button('Run Save reviewed cohort')!.textContent).not.toContain('Recording');
    expect(button('Run Save reviewed cohort')!.disabled).toBe(false);
  });

  it('a handler that returns nothing releases the controls at once', async () => {
    const onAction = vi.fn(() => undefined);
    act(() => root.render(<GenieActions actions={[ACTION]} onAction={onAction} />));
    act(() => button('Run Save reviewed cohort')!.click());
    await act(async () => {
      button('Confirm Save reviewed cohort')!.click();
      await Promise.resolve();
    });
    expect(onAction).toHaveBeenCalledTimes(1);
    expect(button('Run Save reviewed cohort')!.disabled).toBe(false);
  });
});
