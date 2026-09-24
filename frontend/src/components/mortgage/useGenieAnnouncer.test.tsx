/**
 * @vitest-environment happy-dom
 *
 * One announcer per Genie surface, never two at once, and each announcement
 * said once (audit 2026-09-21 `a11y-06`). Two probe surfaces render the REAL
 * region component (GenieAnnouncerRegion, what GenieChat and /ask-genie
 * render), and the real turn store drives them through a scripted live turn.
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
import { GenieAnnouncerRegion } from './GenieAnnouncerRegion';
import { __resetGenieAnnouncerForTests, isGenieRouteAskVisible } from './useGenieAnnouncer';

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
  return <GenieAnnouncerRegion surface={surface} visible={visible} />;
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

  function render(panelOpen: boolean, withRoute = true, withPanel = true) {
    act(() => {
      root.render(
        <>
          {withPanel && <Probe surface="panel" visible={panelOpen} />}
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

  /**
   * Every text INSERTED into a Genie region under the container (a new text
   * node, or a text node changed in place), as `surface:text` -- what a polite
   * live region speaks. A region that MOUNTS already holding text is logged as
   * `surface:mounted:text` (not reliably spoken). Watching the container
   * catches regions that mount after the watch starts. `stop()` drains pending
   * records with takeRecords() before disconnecting.
   */
  function watchInsertions() {
    const inserted: string[] = [];
    const surfaceOf = (node: Node | null) =>
      (node instanceof Element ? node : node?.parentElement)?.closest('[data-genie-announcer]')?.getAttribute('data-genie-announcer');
    const collect = (records: MutationRecord[]) => {
      for (const record of records) {
        if (record.type === 'characterData') {
          const surface = surfaceOf(record.target);
          if (surface && record.target.textContent) inserted.push(`${surface}:${record.target.textContent}`);
          continue;
        }
        record.addedNodes.forEach((node) => {
          if (!node.textContent) return;
          if (node.nodeType === Node.TEXT_NODE) {
            const surface = surfaceOf(record.target);
            if (surface) inserted.push(`${surface}:${node.textContent}`);
            return;
          }
          if (!(node instanceof Element)) return;
          const regions = [node, ...node.querySelectorAll('[data-genie-announcer]')].filter((el) =>
            el.hasAttribute('data-genie-announcer'),
          );
          for (const el of regions) {
            if (el.textContent) inserted.push(`${el.getAttribute('data-genie-announcer')}:mounted:${el.textContent}`);
          }
        });
      }
    };
    const observer = new MutationObserver(collect);
    observer.observe(container, { childList: true, characterData: true, subtree: true });
    return {
      inserted,
      stop: () => {
        collect(observer.takeRecords());
        observer.disconnect();
      },
    };
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

  it('says an announcement once: the floor moving (panel open, panel close, route unmount) never replays it', () => {
    render(false);
    const watch = watchInsertions();
    act(() => announceGenie('Answer ready'));
    expect(region('route').textContent).toBe('Answer ready');

    render(true); // the panel opens over the route
    expect(region('panel').textContent).toBe('');
    render(false); // and closes
    expect(region('route').textContent).toBe('');
    render(false, false); // the user leaves /ask-genie; the panel stays mounted
    expect(region('panel').textContent).toBe('');
    watch.stop();
    expect(watch.inserted).toEqual(['route:Answer ready']);
  });

  it('a landed answer is said once across a panel remount and a return to /ask-genie', async () => {
    mocks.genieSubmit.mockResolvedValue({ completed: true, response: ANSWER });
    render(false, false);
    const watch = watchInsertions();
    act(() => {
      startGenieTurn({ question: 'Which states lead?', conversationId: null, surface: 'panel', startedAt: Date.now() });
    });
    await advance();
    expect(region('panel').textContent).toBe('Answer ready');

    render(false, false, false); // an error boundary unmounts the panel ...
    render(false, false); // ... and recovers it
    render(false, true); // the user comes back to /ask-genie
    render(false, false); // and leaves again
    await advance(1_000);
    watch.stop();
    expect(watch.inserted).toEqual(['panel:Waiting for Genie response', 'panel:Answer ready']);
  });

  it('an announcement no surface was mounted to say is said once, by the next surface to take the floor', () => {
    const reason = 'Interrupted by a reload before the answer arrived. Ask again to get it.';
    const watch = watchInsertions();
    act(() => announceGenie(reason));
    render(false, true, false); // the route mounts: its region mounts EMPTY, then says it
    expect(region('route').textContent).toBe(reason);
    render(true); // the panel mounts open: the floor moves, the news does not
    render(false);
    watch.stop();
    expect(region('route').textContent).toBe('');
    expect(region('panel').textContent).toBe('');
    expect(watch.inserted).toEqual([`route:${reason}`]);
  });

  it('says the same confirmation again when it repeats', () => {
    render(false);
    const watch = watchInsertions();
    act(() => announceGenie('SQL copied'));
    act(() => announceGenie('SQL copied'));
    watch.stop();
    expect(watch.inserted).toEqual(['route:SQL copied', 'route:SQL copied']);
  });

  it('says a confirmation made mid-turn, holds it while the stage holds, then says the next stage', async () => {
    mocks.genieSubmit.mockResolvedValue({ completed: false, conversation_id: 'conv-1', message_id: 'msg-1', progress_token: 'tok' });
    const stages = [
      stage('executing', 'Running the governed query'),
      stage('executing', 'Running the governed query'),
      stage('executing', 'Running the governed query'),
      stage('complete', 'Verifying the answer against its rows', true),
    ];
    mocks.genieProgress.mockImplementation(() => Promise.resolve(stages.shift()));
    mocks.genieComplete.mockImplementation(() => new Promise<GenieAnswer>(() => undefined));
    render(false);
    act(() => {
      startGenieTurn({ question: 'Which states lead?', conversationId: null, surface: 'route', startedAt: Date.now() });
    });
    await advance();
    expect(region('route').textContent).toBe('Running the governed query');
    const watch = watchInsertions();
    act(() => announceGenie('SQL copied'));
    expect(region('route').textContent).toBe('SQL copied');
    await advance(1_600); // one more poll, same stage: the confirmation holds
    expect(region('route').textContent).toBe('SQL copied');
    await advance(10_000); // the stage moves on
    watch.stop();
    expect(region('route').textContent).toBe('Verifying the answer against its rows');
    expect(watch.inserted).toEqual(['route:SQL copied', 'route:Verifying the answer against its rows']);
  });
});
