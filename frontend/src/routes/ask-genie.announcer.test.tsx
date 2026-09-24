/**
 * @vitest-environment happy-dom
 *
 * /ask-genie's ONE screen-reader announcer (audit 2026-09-21 `a11y-06`):
 * exactly one live region across the Ask tabpanel and the route root, mid-turn
 * and after landing; it sits outside every tabpanel with no aria-busy
 * ancestor; it does not move while the stage holds (the elapsed ticker and
 * repeated polls never reach it); it says "Answer ready" once, and never for
 * a withheld turn. Fake timers drive the real 1.5 s poll loop of the turn store.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieAnswer } from '../types';
import {
  LIVE_SUBMIT,
  QUESTION,
  answer,
  deferred,
  genieComplete,
  genieProgress,
  genieSubmit,
  installStorage,
  mount,
  progress,
  resetMocks,
  routeControls,
  setTextAreaValue,
} from './ask-genie.turn.test-support';
import { clearGenieTurns } from '../lib/genieConversationStore';
import { __resetGenieTurnStoreForTests, startGenieTurn } from '../lib/genieInFlightTurn';
import { __resetGenieAnnouncerForTests } from '../components/mortgage/useGenieAnnouncer';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const LIVE = '[role="status"], [role="alert"], [aria-live]';

describe('/ask-genie announcer', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.useFakeTimers();
    installStorage();
    resetMocks();
    clearGenieTurns();
    __resetGenieTurnStoreForTests();
    __resetGenieAnnouncerForTests();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    __resetGenieTurnStoreForTests();
    clearGenieTurns();
    vi.useRealTimers();
  });

  async function advance(ms: number) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  }

  async function until(condition: () => boolean, maxMs = 10_000) {
    for (let waited = 0; waited < maxMs && !condition(); waited += 50) await advance(50);
    if (!condition()) throw new Error('until: condition never held');
  }

  const region = () => {
    const el = container.querySelector<HTMLElement>('[data-genie-announcer="route"]');
    if (!el) throw new Error('route announcer not rendered');
    return el;
  };

  /** Live regions of the route root and the Ask tabpanel (the other tabs'
   *  panels own their workflow status lines). */
  const routeRegions = () =>
    Array.from(container.querySelectorAll<HTMLElement>(LIVE)).filter((el) => {
      const panel = el.closest('[role="tabpanel"]');
      return panel === null || panel.id === 'ask-genie-panel-ask';
    });

  function observe(el: HTMLElement): MutationObserver {
    const observer = new MutationObserver(() => undefined);
    observer.observe(el, { childList: true, subtree: true, characterData: true });
    return observer;
  }

  async function askOnRoute() {
    const controls = routeControls(container);
    act(() => setTextAreaValue(controls.composer(), QUESTION));
    await act(async () => {
      controls.ask().click();
      await vi.advanceTimersByTimeAsync(0);
    });
  }

  it('keeps one region, outside every tabpanel, still while the stage holds, and says "Answer ready" once', async () => {
    const complete = deferred<GenieAnswer>();
    genieSubmit.mockResolvedValue(LIVE_SUBMIT);
    genieProgress.mockResolvedValue(progress(false));
    genieComplete.mockReturnValue(complete.promise);
    mount(root);
    await until(() => container.querySelector('textarea') !== null);
    const spoken: string[] = [region().textContent ?? ''];
    const sample = () => {
      const text = region().textContent ?? '';
      if (text !== spoken[spoken.length - 1]) spoken.push(text);
    };

    await askOnRoute();
    await until(() => genieProgress.mock.calls.length > 0);
    sample();
    expect(routeRegions()).toEqual([region()]);
    expect(region().textContent).toBe('Running the governed query');
    // A direct child of the page, outside every tabpanel, with no aria-busy
    // ancestor: a hidden tabpanel is not spoken, and a busy one is held back.
    expect(region().closest('[role="tabpanel"]')).toBeNull();
    expect(region().closest('[aria-busy]')).toBeNull();
    expect(region().parentElement?.classList.contains('main__inner')).toBe(true);

    // Five seconds of the same stage: polls keep arriving, the ticker keeps
    // moving, and the region does not change once.
    const observer = observe(region());
    const polls = genieProgress.mock.calls.length;
    const ticker = container.querySelector('.genie-progress__elapsed')?.textContent;
    await advance(5_000);
    expect(genieProgress.mock.calls.length).toBeGreaterThanOrEqual(polls + 3);
    expect(container.querySelector('.genie-progress__elapsed')?.textContent).not.toBe(ticker);
    expect(observer.takeRecords()).toHaveLength(0);

    genieProgress.mockResolvedValue(progress(true));
    await until(() => genieComplete.mock.calls.length === 1);
    sample();
    expect(region().textContent).toBe('Verifying the answer against its rows');
    await act(async () => {
      complete.resolve(answer());
      await vi.advanceTimersByTimeAsync(0);
    });
    await until(() => container.querySelector('.genie-thread .genie-answer') !== null);
    sample();
    expect(routeRegions()).toEqual([region()]);
    observer.takeRecords();
    await advance(5_000);
    expect(observer.takeRecords()).toHaveLength(0);
    observer.disconnect();

    expect(spoken).toEqual([
      '',
      'Running the governed query',
      'Verifying the answer against its rows',
      'Answer ready',
    ]);
    expect(spoken.filter((text) => text === 'Answer ready')).toHaveLength(1);
    // Nothing else on the page says it: the answer mounts no region of its own.
    expect((container.textContent ?? '').split('Answer ready').length - 1).toBe(1);
  });

  it('never says "Answer ready" for a refused turn: it says the reason is in the thread', async () => {
    genieSubmit.mockResolvedValue({
      completed: true,
      response: answer({
        answer: 'I cannot rank borrowers on that criterion.',
        source: 'refused',
        trusted_assets: [],
        follow_up_questions: [],
      }),
    });
    mount(root);
    await until(() => container.querySelector('textarea') !== null);
    const observer = observe(region());
    await askOnRoute();
    await until(() => container.querySelector('.genie-thread .genie-answer') !== null);
    expect(region().textContent).toBe('Genie did not answer this question. The reason is shown in the thread.');
    expect(container.textContent).not.toContain('Answer ready');
    expect(routeRegions()).toEqual([region()]);
    observer.disconnect();
  });

  it('speaks a landed turn while the Workflows tab shows (the region sits outside the hidden Ask panel)', async () => {
    genieSubmit.mockResolvedValue({ completed: true, response: answer() });
    mount(root, { path: '/ask-genie?tab=workflows' });
    await until(() => container.querySelector('[data-genie-announcer="route"]') !== null);
    const askPanel = container.querySelector<HTMLElement>('#ask-genie-panel-ask');
    expect(askPanel?.hidden).toBe(true);
    await act(async () => {
      startGenieTurn({ question: QUESTION, conversationId: null, surface: 'panel', startedAt: Date.now() });
      await vi.advanceTimersByTimeAsync(0);
    });
    await until(() => region().textContent === 'Answer ready');
    expect(askPanel?.contains(region())).toBe(false);
  });
});
