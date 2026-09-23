/**
 * @vitest-environment happy-dom
 *
 * `openGenie({ prompt })` (audit 2026-09-21 `genie-04`, phase 1): queue a
 * composer prefill and ask the dock to open the panel. It never submits and
 * never touches the network.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  GENIE_OPEN_REQUEST_EVENT,
  consumeGeniePrefill,
  openGenie,
  subscribeGenieOpenRequests,
  subscribeGeniePrefill,
} from './genieOpen';

afterEach(() => {
  consumeGeniePrefill();
  vi.restoreAllMocks();
});

describe('openGenie', () => {
  it('queues the prompt and asks the dock to open, and never submits anything', () => {
    const opens: number[] = [];
    let prefillNotices = 0;
    const unsubscribeOpen = subscribeGenieOpenRequests(() => opens.push(1));
    const unsubscribePrefill = subscribeGeniePrefill(() => {
      prefillNotices += 1;
    });
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    try {
      openGenie({ prompt: 'Compare mean lead score by current coverage state.' });
      expect(opens).toEqual([1]);
      expect(prefillNotices).toBe(1);
      expect(consumeGeniePrefill()).toBe('Compare mean lead score by current coverage state.');
      // Consumed once: a second read finds nothing.
      expect(consumeGeniePrefill()).toBeNull();
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      unsubscribeOpen();
      unsubscribePrefill();
    }
    // Unsubscribed listeners no longer hear the event.
    window.dispatchEvent(new Event(GENIE_OPEN_REQUEST_EVENT));
    expect(opens).toEqual([1]);
  });

  it('a later request replaces an earlier unconsumed one', () => {
    openGenie({ prompt: 'first' });
    openGenie({ prompt: 'second' });
    expect(consumeGeniePrefill()).toBe('second');
  });

  it('a blank prompt opens nothing and queues nothing', () => {
    const opens: number[] = [];
    const unsubscribe = subscribeGenieOpenRequests(() => opens.push(1));
    try {
      openGenie({ prompt: '   ' });
    } finally {
      unsubscribe();
    }
    expect(opens).toEqual([]);
    expect(consumeGeniePrefill()).toBeNull();
  });
});
