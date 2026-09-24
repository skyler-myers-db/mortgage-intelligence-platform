/**
 * @vitest-environment happy-dom
 *
 * One announcer per Genie surface, never two at once (audit 2026-09-21
 * `a11y-06`). Two probe surfaces render the hook's text into a persistent
 * region each, the way GenieChat and /ask-genie do, and the real turn store
 * drives them through a scripted live turn.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieLiveProgress } from '../../lib/api';
import type { GenieAnswer } from '../../types';

const mocks = vi.hoisted(() => ({
  genieSubmit: vi.fn(),
  genieProgress: vi.fn(),
  genieComplete: vi.fn(),
}));

vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api');
  return {
    ...actual,
    api: {
      genieSubmit: mocks.genieSubmit,
      genieProgress: mocks.genieProgress,
      genieComplete: mocks.genieComplete,
    },
  };
});

import { clearGenieTurns } from '../../lib/genieConversationStore';
import {
  __resetGenieTurnStoreForTests,
  __setGenieTurnLockForTests,
  announceGenie,
  startGenieTurn,
} from '../../lib/genieInFlightTurn';
import { __resetGenieAnnouncerForTests, isGenieRouteAskVisible, useGenieAnnouncer } from './useGenieAnnouncer';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function stage(stageKey: string, label: string, terminal = false): GenieLiveProgress {
  return {
    status: terminal ? 'COMPLETED' : 'EXECUTING_QUERY',
    stage: stageKey,
    stage_label: label,
    terminal,
    failed: false,
    reasoning_trace: [],
    sql_preview: null,
    error_hint: null,
  };
}

const ANSWER: GenieAnswer = {
  answer: 'Illinois leads.',
  source: 'genie',
  trusted_assets: ['mip.gold.borrower_360'],
  conversation_id: 'conv-1',
  message_id: 'msg-1',
};

function Probe({ surface, visible }: { surface: 'panel' | 'route'; visible: boolean }) {
  const text = useGenieAnnouncer(surface, visible);
  return (
    <div role="status" aria-live="polite" data-genie-announcer={surface}>
      {text}
    </div>
  );
}

function installStorage(): void {
  for (const key of ['localStorage', 'sessionStorage'] as const) {
    const values = new Map<string, string>();
    Object.defineProperty(window, key, {
      configurable: true,
      value: {
        getItem: (k: string) => values.get(k) ?? null,
        setItem: (k: string, v: string) => values.set(k, v),
        removeItem: (k: string) => values.delete(k),
        clear: () => values.clear(),
      },
    });
  }
}

describe('useGenieAnnouncer', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.useFakeTimers();
    Object.values(mocks).forEach((mock) => mock.mockReset());
    installStorage();
    clearGenieTurns();
    __resetGenieTurnStoreForTests();
    __resetGenieAnnouncerForTests();
    __setGenieTurnLockForTests(() => Promise.resolve({ kind: 'unsupported' }));
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

  function render(panelOpen: boolean, withRoute = true) {
    act(() => {
      root.render(
        <>
          <Probe surface="panel" visible={panelOpen} />
          {withRoute && <Probe surface="route" visible />}
        </>,
      );
    });
  }

  const region = (surface: 'panel' | 'route') =>
    container.querySelector<HTMLElement>(`[data-genie-announcer="${surface}"]`)!;

  async function advance(ms = 0) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  }

  function recordChanges(el: HTMLElement): { texts: string[]; observer: MutationObserver } {
    const texts: string[] = [];
    const observer = new MutationObserver(() => texts.push(el.textContent ?? ''));
    observer.observe(el, { childList: true, characterData: true, subtree: true });
    return { texts, observer };
  }

  it('only one surface speaks: the route while the panel is closed', async () => {
    render(false);
    act(() => announceGenie('Stopped. The question is back in the composer.'));
    expect(region('route').textContent).toBe('Stopped. The question is back in the composer.');
    expect(region('panel').textContent).toBe('');
    expect(isGenieRouteAskVisible()).toBe(true);
  });

  it('the panel speaks when it is the only surface, even while closed', () => {
    render(false, false);
    act(() => announceGenie('Answer ready'));
    expect(region('panel').textContent).toBe('Answer ready');
    expect(isGenieRouteAskVisible()).toBe(false);
  });

  it('switches the speaker when the panel opens and closes, and the silent one stays constant', async () => {
    mocks.genieSubmit.mockResolvedValue({ completed: false, conversation_id: 'conv-1', message_id: 'msg-1', progress_token: 'tok' });
    mocks.genieProgress.mockResolvedValue(stage('executing', 'Running the governed query'));
    render(false);
    act(() => {
      startGenieTurn({ question: 'Which states lead?', conversationId: null, surface: 'route', startedAt: Date.now() });
    });
    await advance();
    expect(region('route').textContent).toBe('Running the governed query');
    expect(region('panel').textContent).toBe('');

    render(true);
    const route = recordChanges(region('route'));
    expect(region('panel').textContent).toBe('Running the governed query');
    expect(region('route').textContent).toBe('');
    await advance(4_500);
    expect(route.texts).toEqual([]);
    route.observer.disconnect();

    render(false);
    expect(region('route').textContent).toBe('Running the governed query');
    expect(region('panel').textContent).toBe('');
  });

  it('speaks each stage change once, and nothing while the stage holds', async () => {
    mocks.genieSubmit.mockResolvedValue({ completed: false, conversation_id: 'conv-1', message_id: 'msg-1', progress_token: 'tok' });
    const stages = [
      stage('drafting', 'Drafting a governed SQL plan'),
      stage('drafting', 'Drafting a governed SQL plan'),
      stage('executing', 'Running the governed query'),
      stage('executing', 'Running the governed query'),
      stage('executing', 'Running the governed query'),
      stage('complete', 'Verifying the answer against its rows', true),
    ];
    mocks.genieProgress.mockImplementation(() => Promise.resolve(stages.shift()));
    let finish: (value: GenieAnswer) => void = () => undefined;
    mocks.genieComplete.mockImplementation(() => new Promise<GenieAnswer>((resolve) => {
      finish = resolve;
    }));
    render(true);
    const panel = recordChanges(region('panel'));
    act(() => {
      startGenieTurn({ question: 'Which states lead?', conversationId: null, surface: 'panel', startedAt: Date.now() });
    });
    await advance(10_000);
    await act(async () => {
      finish(ANSWER);
      await vi.advanceTimersByTimeAsync(0);
    });
    panel.observer.disconnect();
    expect(panel.texts).toEqual([
      'Waiting for Genie response',
      'Drafting a governed SQL plan',
      'Running the governed query',
      'Verifying the answer against its rows',
      'Answer ready',
    ]);
  });
});
