/**
 * @vitest-environment happy-dom
 *
 * /ask-genie on the in-flight turn store (audit 2026-09-21 wave 2
 * `runtime-01`, `genie-03`, `states-08`, `genie-v2`), at the rendered layer:
 * the route and the floating panel mount on the REAL turn and transcript
 * stores; only the api boundary is mocked.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { GenieLiveProgress } from '../lib/api';
import type { GenieAnswer } from '../types';
import {
  LIVE_SUBMIT,
  QUESTION,
  answer,
  appState,
  deferred,
  flush,
  genieAction,
  genieComplete,
  genieProgress,
  genieSubmit,
  installStorage,
  mount,
  progress,
  render,
  resetMocks,
  routeControls,
  setInputValue,
  setTextAreaValue,
  waitUntil,
} from './ask-genie.turn.test-support';
import { ApiError } from '../lib/api';
import { GENIE_IN_FLIGHT_TURN_KEY } from '../lib/genieConversation';
import { clearGenieTurns, getGenieTurns, setGenieTurns } from '../lib/genieConversationStore';
import { __resetGenieTurnStoreForTests, __setGenieTurnLockForTests } from '../lib/genieInFlightTurn';
import { __resetGenieAnnouncerForTests } from '../components/mortgage/useGenieAnnouncer';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const TIMEOUT_MS = 30_000;

describe('/ask-genie on the in-flight turn store', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
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
  });

  /** Ask on the route; the turn stays live until the handles resolve. */
  async function askLive(controls: ReturnType<typeof routeControls>, question = QUESTION) {
    const poll = deferred<GenieLiveProgress>();
    const complete = deferred<GenieAnswer>();
    genieSubmit.mockResolvedValueOnce(LIVE_SUBMIT);
    genieProgress.mockReturnValueOnce(poll.promise);
    genieComplete.mockReturnValueOnce(complete.promise);
    act(() => setTextAreaValue(controls.composer(), question));
    await act(async () => {
      controls.ask().click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await waitUntil(() => genieProgress.mock.calls.length > 0);
    return { poll, complete, signal: genieSubmit.mock.calls[genieSubmit.mock.calls.length - 1][2] as AbortSignal };
  }

  it('leaving and returning mid-turn keeps one submit, one complete and the answer', async () => {
    const queryClient = mount(root);
    const controls = routeControls(container);
    await waitUntil(() => container.querySelector('textarea') !== null);
    const turn = await askLive(controls);

    // Leave the route (the panel is not mounted): nothing is aborted.
    render(root, queryClient, { route: false });
    expect(turn.signal.aborted).toBe(false);
    await act(async () => {
      turn.poll.resolve(progress(true));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await waitUntil(() => genieComplete.mock.calls.length === 1);

    // Back while the answer is still being verified: the same pending turn.
    render(root, queryClient, { route: true });
    await waitUntil(() => container.querySelector('textarea') !== null);
    expect(controls.userBubbles()).toEqual([QUESTION]);
    expect(container.querySelector('.genie-progress')).not.toBeNull();
    expect(controls.stop()).not.toBeNull();

    await act(async () => {
      turn.complete.resolve(answer());
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await waitUntil(() => container.textContent?.includes('Illinois leads with 3,080 candidates.') ?? false);
    expect(genieSubmit).toHaveBeenCalledTimes(1);
    expect(genieComplete).toHaveBeenCalledTimes(1);
    expect(controls.userBubbles()).toEqual([QUESTION]);
    expect(container.querySelectorAll('.genie-thread .genie-answer')).toHaveLength(1);
    expect(container.querySelector('.genie-progress')).toBeNull();
    expect(getGenieTurns()).toHaveLength(1);
  }, TIMEOUT_MS);

  it('Stop restores the question, focuses the composer, leaves the note, and ignores a late reply', async () => {
    mount(root);
    const controls = routeControls(container);
    await waitUntil(() => container.querySelector('textarea') !== null);
    const turn = await askLive(controls);
    // The composer still holds the asked question while it runs.
    act(() => setTextAreaValue(controls.composer(), ''));

    const stop = controls.stop();
    expect(stop?.textContent?.trim()).toBe('Stop');
    expect(stop?.title).toBe(
      'Stop waiting for this answer. Genie may still finish it on the server; the reply is discarded.',
    );
    await act(async () => {
      stop?.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(turn.signal.aborted).toBe(true);
    expect(controls.composer().value).toBe(QUESTION);
    expect(document.activeElement).toBe(controls.composer());
    const note = container.querySelector('.genie-thread .genie__msg--stopped');
    expect(note?.textContent).toContain('Stopped');
    expect(note?.textContent).toContain('Genie may still finish this turn on the server');
    const actions = Array.from(container.querySelectorAll<HTMLButtonElement>('.genie-thread .genie__msg-actions--user button'));
    expect(actions.map((button) => button.textContent)).toEqual(['Edit', 'Ask again']);
    expect(container.querySelector('.genie-progress')).toBeNull();
    expect(controls.ask().disabled).toBe(false);
    expect(window.sessionStorage.getItem(GENIE_IN_FLIGHT_TURN_KEY)).toBeNull();

    await act(async () => {
      turn.poll.resolve(progress(true));
      turn.complete.resolve(answer({ conversation_id: 'conv-late' }));
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(getGenieTurns()).toEqual([]);
    expect(genieComplete).not.toHaveBeenCalled();
    expect(window.localStorage.getItem('mip.genie.conversationId')).toBeNull();
    expect(container.textContent).not.toContain('3,080 candidates');
  }, TIMEOUT_MS);

  it('Stop keeps a different draft and still focuses the composer', async () => {
    mount(root);
    const controls = routeControls(container);
    await waitUntil(() => container.querySelector('textarea') !== null);
    await askLive(controls);
    act(() => setTextAreaValue(controls.composer(), 'A follow-up draft'));
    await act(async () => {
      controls.stop()?.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(controls.composer().value).toBe('A follow-up draft');
    expect(document.activeElement).toBe(controls.composer());
    expect(container.querySelector('.genie-thread .genie__msg--stopped')).not.toBeNull();
  }, TIMEOUT_MS);

  it('holds the chips, follow-ups, Ask, Enter, Regenerate, History and New thread across both surfaces', async () => {
    setGenieTurns([{ question: 'Earlier question', response: answer({ message_id: 'msg-0' }) }]);
    appState.genieOpen = true;
    mount(root, { panel: true });
    const controls = routeControls(container);
    await waitUntil(() => controls.sampleChips().length > 0);
    expect(controls.sampleChips()[0].disabled).toBe(false);
    expect(controls.followUps()[0].disabled).toBe(false);

    // A turn asked from the FLOATING PANEL holds the route too.
    const poll = deferred<GenieLiveProgress>();
    genieSubmit.mockResolvedValueOnce(LIVE_SUBMIT);
    genieProgress.mockReturnValueOnce(poll.promise);
    const panelInput = container.querySelector<HTMLInputElement>('input[aria-label="Ask Genie"]')!;
    act(() => setInputValue(panelInput, 'Asked in the panel'));
    await act(async () => {
      container.querySelector<HTMLButtonElement>('button[aria-label="Ask"]')!.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await waitUntil(() => genieProgress.mock.calls.length > 0);

    const reason = 'Genie is still answering. Ask unlocks when this answer lands, or press Stop. Leaving this page does not stop it.';
    act(() => setTextAreaValue(controls.composer(), 'A route draft'));
    for (const chip of controls.sampleChips()) {
      expect(chip.disabled).toBe(true);
      expect(chip.title).toBe(reason);
    }
    for (const chip of controls.followUps()) expect(chip.disabled).toBe(true);
    expect(controls.ask().disabled).toBe(true);
    expect(controls.regenerate()?.disabled).toBe(true);
    expect(controls.history()?.disabled).toBe(true);
    expect(controls.newThread()?.disabled).toBe(true);
    expect(document.getElementById('ask-genie-busy')?.textContent).toBe(reason);
    expect(controls.composer().getAttribute('aria-describedby')).toBe('ask-genie-busy');
    // The panel shows the same reason.
    expect(document.getElementById('genie-composer-busy')?.textContent).toBe(reason);
    // The route shows the panel's pending turn.
    expect(controls.userBubbles()).toContain('Asked in the panel');

    // Enter and a stale chip click send nothing.
    await act(async () => {
      controls.composer().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
      controls.sampleChips()[0].click();
      controls.followUps()[0].click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(genieSubmit).toHaveBeenCalledTimes(1);
    expect(genieSubmit.mock.calls[0][0]).toBe('Asked in the panel');
    expect(controls.composer().value).toBe('A route draft');
  }, TIMEOUT_MS);

  it('a turn asked on the route holds the floating panel', async () => {
    appState.genieOpen = true;
    mount(root, { panel: true });
    const controls = routeControls(container);
    await waitUntil(() => container.querySelector('textarea') !== null);
    await askLive(controls);
    const panelAsk = container.querySelector<HTMLButtonElement>('button[aria-label="Ask"]')!;
    expect(panelAsk.disabled).toBe(true);
    const panel = container.querySelector<HTMLElement>('.genie[role="dialog"]')!;
    expect(panel.querySelector('.genie__msg--user')?.textContent).toBe(QUESTION);
    expect(panel.querySelector('button[aria-label="Stop this Genie turn"]')).not.toBeNull();
  }, TIMEOUT_MS);

  it('binds a governed action to its own turn, not the latest answer', async () => {
    const action = { id: 'save-1', label: 'Save reviewed cohort', action_type: 'save_borrowers', description: 'Save.' };
    setGenieTurns([
      { question: 'First question', response: answer({ message_id: 'msg-first', question_hash: 'hash-first', actions: [action] }) },
      { question: 'Second question', response: answer({ message_id: 'msg-second', question_hash: 'hash-second', actions: [] }) },
    ]);
    genieAction.mockResolvedValue({ ok: true, action_type: 'save_borrowers', message: 'Saved 12 borrowers.', audit_event_id: 'evt-1' });
    mount(root);
    await waitUntil(() => container.textContent?.includes('Save reviewed cohort') ?? false);
    const run = Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find((b) =>
      b.getAttribute('aria-label') === 'Run Save reviewed cohort',
    );
    await act(async () => {
      run?.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    const confirm = Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find((b) =>
      b.getAttribute('aria-label') === 'Confirm Save reviewed cohort',
    );
    await act(async () => {
      confirm?.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await waitUntil(() => genieAction.mock.calls.length === 1);
    expect(genieAction.mock.calls[0][0]).toMatchObject({ message_id: 'msg-first', question_hash: 'hash-first' });
    await waitUntil(() => container.textContent?.includes('Saved 12 borrowers. Audit event evt-1.') ?? false);
  }, TIMEOUT_MS);

  it('a resumed turn never renders its question before a progress poll returns 200 (403: silent reset)', async () => {
    window.sessionStorage.setItem(
      GENIE_IN_FLIGHT_TURN_KEY,
      JSON.stringify({
        v: 1,
        question: 'A question from before the reload',
        conversationId: 'conv-1',
        surface: 'route',
        startedAt: Date.now(),
        deep: false,
        phase: 'polling',
        ids: { conversationId: 'conv-1', messageId: 'msg-1', progressToken: 'tok-1' },
      }),
    );
    __setGenieTurnLockForTests(() => Promise.resolve({ kind: 'held', release: () => undefined }));
    const poll = deferred<GenieLiveProgress>();
    genieProgress.mockReturnValueOnce(poll.promise);

    // Every text the DOM ever held, including nodes added and removed again.
    const seen: string[] = [];
    const observer = new MutationObserver((records) => {
      for (const record of records) {
        if (record.oldValue) seen.push(record.oldValue);
        record.addedNodes.forEach((node) => seen.push(node.textContent ?? ''));
      }
      seen.push(container.textContent ?? '');
    });
    observer.observe(container, { childList: true, subtree: true, characterData: true, characterDataOldValue: true });
    mount(root);
    await waitUntil(() => genieProgress.mock.calls.length === 1);
    await flush();
    expect(container.textContent).toContain('Resuming your last question…');

    await act(async () => {
      poll.reject(new ApiError('forbidden', { path: '/api/genie/message/progress', status: 403 }));
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    await flush();
    observer.disconnect();
    seen.push(container.textContent ?? '');
    expect(seen.some((text) => text.includes('A question from before the reload'))).toBe(false);
    expect(genieComplete).not.toHaveBeenCalled();
    expect(genieSubmit).not.toHaveBeenCalled();
    expect(getGenieTurns()).toEqual([]);
    expect(window.sessionStorage.getItem(GENIE_IN_FLIGHT_TURN_KEY)).toBeNull();
    expect(container.querySelector('.genie-progress')).toBeNull();
  }, TIMEOUT_MS);
});
