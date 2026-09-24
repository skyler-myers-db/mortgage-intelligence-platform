/**
 * @vitest-environment happy-dom
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ElapsedTicker } from './ElapsedTicker';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('ElapsedTicker', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-09-24T12:00:00Z'));
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.useRealTimers();
  });

  const ticker = () => container.querySelector('span')!;

  it('counts whole seconds from startedAt, aria-hidden, as .mono by default', () => {
    act(() => root.render(<ElapsedTicker startedAt={Date.now() - 2_000} paused={false} />));

    expect(ticker().textContent).toBe('2s');
    expect(ticker().className).toBe('mono');
    expect(ticker().getAttribute('aria-hidden')).toBe('true');

    act(() => {
      vi.advanceTimersByTime(3_000);
    });
    expect(ticker().textContent).toBe('5s');
  });

  it('runs no interval while paused and reads the real clock on resume', () => {
    const startedAt = Date.now();
    act(() => root.render(<ElapsedTicker startedAt={startedAt} paused />));
    expect(vi.getTimerCount()).toBe(0);

    act(() => {
      vi.advanceTimersByTime(7_000);
    });
    expect(ticker().textContent, 'nothing re-rendered while paused').toBe('0s');

    act(() => root.render(<ElapsedTicker startedAt={startedAt} paused={false} />));
    expect(ticker().textContent).toBe('7s');
    expect(vi.getTimerCount()).toBe(1);
  });

  it('takes a class name and an injected clock', () => {
    let clock = 10_000;
    const now = () => clock;
    act(() => root.render(<ElapsedTicker startedAt={4_000} paused={false} className="genie-progress__elapsed mono" now={now} />));

    expect(ticker().className).toBe('genie-progress__elapsed mono');
    expect(ticker().textContent).toBe('6s');
    clock = 12_000;
    act(() => {
      vi.advanceTimersByTime(1_000);
    });
    expect(ticker().textContent).toBe('8s');
  });

  it('never shows a negative time', () => {
    act(() => root.render(<ElapsedTicker startedAt={Date.now() + 5_000} paused={false} />));
    expect(ticker().textContent).toBe('0s');
  });
});
