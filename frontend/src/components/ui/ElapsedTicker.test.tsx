/**
 * @vitest-environment happy-dom
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
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

  it('paints the true elapsed time on its first frame, before any effect runs', () => {
    // A remount mid-wait (the Genie card reopened, the pill re-rendered on a
    // later poll) must not flash "0s" for a frame before the effect catches up.
    const startedAt = Date.now() - 4_000;
    const firstFrame = document.createElement('div');
    firstFrame.innerHTML = renderToStaticMarkup(<ElapsedTicker startedAt={startedAt} paused={false} />);
    expect(firstFrame.textContent, 'the pre-effect markup').toBe('4s');

    act(() => root.render(<ElapsedTicker startedAt={startedAt} paused />));
    expect(ticker().textContent, 'a paused mount shows the real elapsed time').toBe('4s');
  });

  it('counts from a new startedAt at once when a mounted ticker is handed one', () => {
    // A mounted ticker reused for a new wait (GenieProgress restarting a turn
    // without remounting) must not keep painting the previous wait's count;
    // while paused no effect ticks, so the stale count would never clear.
    const first = Date.now() - 9_000;
    const second = Date.now() - 2_000;
    act(() => root.render(<ElapsedTicker startedAt={first} paused />));
    expect(ticker().textContent).toBe('9s');

    act(() => root.render(<ElapsedTicker startedAt={second} paused />));
    expect(ticker().textContent, 'the new wait, not the previous one').toBe('2s');

    act(() => root.render(<ElapsedTicker startedAt={second} paused={false} />));
    act(() => {
      vi.advanceTimersByTime(1_000);
    });
    expect(ticker().textContent).toBe('3s');
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
