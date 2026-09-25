/**
 * @vitest-environment happy-dom
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { WarmingUpState } from '../../lib/useWarmingUpRetry';
import { WAREHOUSE_WARMING_BODY, WarmingUpBlock, preloadWarmingExtras } from './WarmingUpBlock';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const toastMocks = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock('../../lib/toast', () => ({ toast: toastMocks }));

/**
 * WarmingUpBlock's waits (audit states-08 part 4): the attempt counter and
 * the warming sentence stay, and the footer now says how long the wait has
 * run and when the next try is due; the correlation id moved behind a
 * Details disclosure with a Copy button.
 */

/** Any "30 s", "60 seconds" or "30–60 seconds" claim (warehouse-resume.fixture.spec.ts). */
const DURATION_CLAIM = /\b(30|60)\s*(?:[–-]\s*60\s*)?(?:s|sec|secs|seconds)\b/i;

function warming(overrides: Partial<WarmingUpState> = {}): WarmingUpState {
  return {
    dependency: 'warehouse',
    label: 'Warehouse warming up',
    attempt: 2,
    maxAttempts: 6,
    correlationId: null,
    intervalMs: 5_000,
    ...overrides,
  };
}

let root: Root;

// The clocks are their own chunk (loaded with the first warm-up).
beforeAll(async () => {
  await preloadWarmingExtras();
});

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-09-25T12:00:00Z'));
  document.body.innerHTML = '<div id="root"></div>';
  root = createRoot(document.getElementById('root') as HTMLElement);
});

afterEach(() => {
  act(() => root.unmount());
  document.body.innerHTML = '';
  vi.useRealTimers();
  vi.clearAllMocks();
});

const block = () => document.querySelector('[data-testid="warming-up-block"]');
/** The visible wait line: digits only (the sr-only sentences are read separately). */
function waitLine(): string {
  const line = document.querySelector('[data-testid="warming-up-wait"]');
  if (!line) return '';
  const clone = line.cloneNode(true) as HTMLElement;
  clone.querySelectorAll('.sr-only').forEach((node) => node.remove());
  return (clone.textContent ?? '').replace(/\s+/g, ' ').trim();
}

async function tick(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe('WarmingUpBlock', () => {
  it('keeps the attempt counter and the warming sentence', () => {
    act(() => root.render(<WarmingUpBlock state={warming()} title="Ranked borrowers loading" />));
    expect(block()?.querySelector('[data-testid="warming-up-attempt"]')?.textContent).toBe('(attempt 2 of 6)');
    expect(block()?.textContent).toContain(WAREHOUSE_WARMING_BODY);
    expect(block()?.textContent).toContain('Retrying automatically every 5 seconds.');
  });

  it('says how long the wait has run and when the next try is due', async () => {
    act(() => root.render(<WarmingUpBlock state={warming()} />));
    expect(waitLine()).toBe('· Waiting 0:00 · next try in 5 s');
    await tick(3_000);
    expect(waitLine()).toBe('· Waiting 0:03 · next try in 2 s');
  });

  it('restarts the next-try countdown when the attempt count changes; the wait keeps running', async () => {
    act(() => root.render(<WarmingUpBlock state={warming()} />));
    await tick(5_000);
    expect(waitLine()).toBe('· Waiting 0:05 · next try in 0 s');
    act(() => root.render(<WarmingUpBlock state={warming({ attempt: 3 })} />));
    expect(waitLine()).toBe('· Waiting 0:05 · next try in 5 s');
    await tick(2_000);
    expect(waitLine()).toBe('· Waiting 0:07 · next try in 3 s');
  });

  it('omits the next try without an interval (a hand-built state)', () => {
    act(() => root.render(<WarmingUpBlock state={warming({ intervalMs: undefined })} />));
    expect(waitLine()).toBe('· Waiting 0:00');
  });

  it('puts the correlation id behind a Details disclosure with a Copy button, not in the footer', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    act(() => root.render(<WarmingUpBlock state={warming({ correlationId: 'corr-warm-7' })} />));
    const details = block()?.querySelector('details');
    expect(details?.querySelector('summary')?.textContent).toBe('Details');
    expect(details?.querySelector('[data-testid="warming-up-reference"]')?.textContent).toBe('corr-warm-7');
    expect(block()?.querySelector('.warming-block__footer')?.textContent).not.toContain('corr-warm-7');
    expect(block()?.textContent).not.toContain('correlation_id:');
    await act(async () => {
      (details?.querySelector('button') as HTMLButtonElement).click();
      await Promise.resolve();
    });
    expect(writeText).toHaveBeenCalledWith('corr-warm-7');
    expect(toastMocks.success).toHaveBeenCalledWith('Reference copied', { detail: null });
  });

  it('shows no Details without a correlation id', () => {
    act(() => root.render(<WarmingUpBlock state={warming()} />));
    expect(block()?.querySelector('details')).toBeNull();
  });

  it('makes no 30 / 60 second claim while warming up, at any point of the wait', async () => {
    act(() => root.render(<WarmingUpBlock state={warming()} />));
    for (const step of [0, 25_000, 5_000, 30_000]) {
      await tick(step);
      expect(block()?.textContent ?? '').not.toMatch(DURATION_CLAIM);
    }
  });
});
