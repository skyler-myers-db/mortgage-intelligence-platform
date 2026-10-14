/**
 * Let a test's pending async work land INSIDE act() (audit quality-06, W5e).
 *
 * A suite that waits with a plain vi.waitFor, or ends right after its last
 * wait (a mocked request that resolved but whose answer has not rendered),
 * leaves React state updates to run outside any act scope, and React warns
 * "An update to X inside a test was not wrapped in act(...)". These helpers
 * advance real time in short act-wrapped ticks instead, so resolved
 * queries, lazy chunks and timers commit where React expects them.
 *
 * Polling must happen BETWEEN act scopes, never inside one: React flushes
 * the work it queued during an act scope when that scope ends, so a DOM
 * poll inside a single long act would never see the update it waits for.
 *
 * They never weaken an assertion: `eventuallyInAct` re-runs the caller's
 * assertion and throws its own error at the deadline, and `settleInAct`
 * only drains work already in flight (typically in an afterEach, before
 * the unmount). Test-only: production code never imports src/test.
 */
import { act } from 'react';

const TICK_MS = 10;

async function tick(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => window.setTimeout(resolve, TICK_MS));
  });
}

/** Advance a few act-wrapped ticks so in-flight updates commit inside act. */
export async function settleInAct(turns = 3): Promise<void> {
  for (let turn = 0; turn < turns; turn += 1) await tick();
}

/** Re-run `assertion` between act-wrapped ticks until it passes, or throw its error at the deadline. */
export async function eventuallyInAct(assertion: () => void, timeoutMs = 10_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    try {
      assertion();
      return;
    } catch (error) {
      if (Date.now() > deadline) throw error;
    }
    await tick();
  }
}
