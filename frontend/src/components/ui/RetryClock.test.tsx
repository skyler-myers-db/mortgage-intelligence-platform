/**
 * @vitest-environment happy-dom
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Countdown, WaitClock, formatWait, secondsUntil } from './RetryClock';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/** The wait clocks (audit states-08 part 4), under fake timers. */

let root: Root;
const T0 = new Date('2026-09-25T12:00:00Z').getTime();

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(T0);
  document.body.innerHTML = '<div id="root"></div>';
  root = createRoot(document.getElementById('root') as HTMLElement);
});

afterEach(() => {
  act(() => root.unmount());
  document.body.innerHTML = '';
  vi.useRealTimers();
});

const digits = () => document.querySelector('[aria-hidden="true"]')?.textContent;

describe('formatWait / secondsUntil', () => {
  it('formats m:ss, never a bare "30 s" claim', () => {
    expect(formatWait(0)).toBe('0:00');
    expect(formatWait(42)).toBe('0:42');
    expect(formatWait(90.9)).toBe('1:30');
    expect(formatWait(-5)).toBe('0:00');
  });

  it('rounds the seconds left up and never goes negative', () => {
    expect(secondsUntil(T0 + 12_000, T0)).toBe(12);
    expect(secondsUntil(T0 + 11_001, T0)).toBe(12);
    expect(secondsUntil(T0, T0)).toBe(0);
    expect(secondsUntil(T0 - 5_000, T0)).toBe(0);
  });
});

describe('WaitClock', () => {
  it('counts up once a second as m:ss with one fixed screen-reader sentence', async () => {
    act(() => root.render(<WaitClock since={T0} />));
    expect(digits()).toBe('0:00');
    const spoken = document.querySelector('.sr-only')?.textContent;
    expect(spoken).toMatch(/^Waiting since /);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(42_000);
    });
    expect(digits()).toBe('0:42');
    expect(document.querySelector('.sr-only')?.textContent).toBe(spoken);
  });

  it('does not re-render while the document is hidden, and catches up when shown', async () => {
    act(() => root.render(<WaitClock since={T0} />));
    const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(digits()).toBe('0:00');
    visibility.mockReturnValue('visible');
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(digits()).toBe('0:05');
    visibility.mockRestore();
  });
});

describe('Countdown', () => {
  it('counts down to zero and stays there', async () => {
    act(() => root.render(<Countdown until={T0 + 3_000} />));
    expect(digits()).toBe('3 s');
    expect(document.querySelector('.sr-only')?.textContent).toBe('About 3 seconds.');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(digits()).toBe('2 s');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(digits()).toBe('0 s');
  });
});
