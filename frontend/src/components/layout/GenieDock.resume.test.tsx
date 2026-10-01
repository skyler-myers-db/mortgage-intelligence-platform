/**
 * @vitest-environment happy-dom
 *
 * The shell resumes a Genie turn before the panel's first open (audit
 * 2026-09-21 `genie-02` item 2, Genie residual #2), at the rendered layer:
 * GenieDock reads the in-flight record key, loads the lazy launcher signal
 * (through the chat's chunk, which it never mounts) only when there is one,
 * resumes, and its FAB and sr-only description follow the signal until the
 * chat mounts.
 *
 * Since the actor gate (lib/actorScope, D-identity-review-b) the record
 * belongs to no one until the first trusted observation: nothing is imported
 * or resumed while the gate is pending, and a record another actor left is
 * removed by the gate, never resumed.
 */
import { act, useEffect, type ComponentType } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { GENIE_IN_FLIGHT_TURN_KEY } from '../../lib/genieConversation';
import { ACTOR_A, ACTOR_B } from '../../test/actorKeys';

const signal = vi.hoisted(() => ({ imports: 0, ensure: vi.fn(), resume: vi.fn() }));

// The chat MODULE, as the shell loads it for a resume: only the launcher
// signal's re-exports; its GenieChat component is never rendered.
vi.mock('../mortgage/GenieChat', () => {
  signal.imports += 1;
  return { ensureGenieLauncherSignal: signal.ensure, resumeGenieTurnFromSession: signal.resume };
});

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const chat = { mounts: 0 };

/** Each test re-imports the dock's module graph (vi.resetModules): a budget
 *  for a loaded CI runner, not the 5 s / 10 s defaults. */
const MODULE_GRAPH_BUDGET_MS = 30_000;

/** Stands in for the lazy GenieChat: the chat is what reads /api/genie/start. */
function ChatProbe() {
  useEffect(() => {
    chat.mounts += 1;
  }, []);
  return <div data-testid="genie-chat-probe" />;
}

function installSessionStorage(): Map<string, string> {
  const values = new Map<string, string>();
  Object.defineProperty(window, 'sessionStorage', {
    configurable: true,
    value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
      clear: () => values.clear(),
    },
  });
  return values;
}

async function settle(): Promise<void> {
  await act(async () => new Promise((resolve) => setTimeout(resolve, 20)));
}

/** A dynamic import resolves in a later task: wait for it, bounded. */
async function waitFor(condition: () => boolean, timeoutMs = 10_000): Promise<void> {
  const startedAt = Date.now();
  while (!condition()) {
    if (Date.now() - startedAt > timeoutMs) throw new Error('waitFor timeout');
    await settle();
  }
}

describe('GenieDock resumes a turn before the first open', () => {
  let container: HTMLDivElement;
  let root: Root;
  let session: Map<string, string>;
  let GenieDock: typeof import('./GenieDock').GenieDock;
  let turnStatus: typeof import('../../lib/genieTurnStatus');
  let scope: typeof import('../../lib/actorScope');

  beforeEach(async () => {
    // A fresh module graph per test, so each test counts its own import of
    // the lazy signal.
    vi.resetModules();
    signal.imports = 0;
    signal.ensure.mockReset();
    signal.resume.mockReset();
    chat.mounts = 0;
    session = installSessionStorage();
    GenieDock = (await import('./GenieDock')).GenieDock;
    turnStatus = await import('../../lib/genieTurnStatus');
    // The fresh graph's gate: a new document, pending, this tab stamped A.
    scope = await import('../../lib/actorScope');
    scope._resetActorScopeForTests({ status: 'pending', owner: scope.NOBODY });
    session.clear();
    session.set('mip.actorCacheKey', ACTOR_A);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  }, MODULE_GRAPH_BUDGET_MS);

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    turnStatus.setGenieTurnStatus('idle');
  });

  function render(open: boolean, Chat: ComponentType = ChatProbe) {
    act(() => {
      root.render(<GenieDock open={open} onOpen={() => undefined} onClose={() => undefined} onWarm={() => undefined} Chat={Chat} />);
    });
  }

  const fab = () => container.querySelector<HTMLButtonElement>('button.genie__fab');
  const description = () => document.getElementById(turnStatus.GENIE_LAUNCHER_STATUS_ID);

  /** A trusted observation reaches the gate (what AppShell does). */
  async function observe(key: string | null): Promise<void> {
    act(() => scope.observeActor({ key }));
    await settle();
  }

  it('with no record: no import, no resume, an idle launcher', async () => {
    render(false);
    await observe(ACTOR_A);
    expect(signal.imports).toBe(0);
    expect(signal.ensure).not.toHaveBeenCalled();
    expect(signal.resume).not.toHaveBeenCalled();
    expect([...fab()!.classList]).toEqual(['genie__fab']);
    expect(fab()!.hasAttribute('aria-describedby')).toBe(false);
    expect(description()?.textContent).toBe('');
  }, MODULE_GRAPH_BUDGET_MS);

  it("a stamp mismatch removes the record: no import, no resume, no note", async () => {
    session.set(GENIE_IN_FLIGHT_TURN_KEY, JSON.stringify({ v: 2, phase: 'completing' }));
    render(false);
    await observe(ACTOR_B);
    await settle();
    expect(session.has(GENIE_IN_FLIGHT_TURN_KEY)).toBe(false);
    expect(signal.imports).toBe(0);
    expect(signal.resume).not.toHaveBeenCalled();
    expect(description()?.textContent).toBe('');
  }, MODULE_GRAPH_BUDGET_MS);

  it('with a record: nothing until the gate opens, then one import, one resume, no chat; the FAB and its description follow the signal until the open', async () => {
    session.set(GENIE_IN_FLIGHT_TURN_KEY, JSON.stringify({ v: 2, phase: 'completing' }));
    render(false);
    await settle();
    await settle();
    expect(scope.actorScopeStatus()).toBe('pending');
    expect(signal.imports, 'no chunk import while pending').toBe(0);
    expect(signal.resume).not.toHaveBeenCalled();
    await observe(ACTOR_A);
    await waitFor(() => signal.resume.mock.calls.length > 0);
    expect(session.has(GENIE_IN_FLIGHT_TURN_KEY), 'the gate kept the owner record').toBe(true);
    expect(signal.imports).toBe(1);
    expect(signal.ensure).toHaveBeenCalledTimes(1);
    expect(signal.resume).toHaveBeenCalledTimes(1);
    expect(signal.ensure.mock.invocationCallOrder[0]).toBeLessThan(signal.resume.mock.invocationCallOrder[0]);
    // Resuming never mounts the chat (the only reader of /api/genie/start).
    expect(container.querySelector('[data-testid="genie-chat-probe"]')).toBeNull();
    expect(chat.mounts).toBe(0);

    act(() => turnStatus.setGenieTurnStatus('running'));
    expect(fab()!.classList.contains('is-genie-running')).toBe(true);
    expect(fab()!.getAttribute('aria-describedby')).toBe(turnStatus.GENIE_LAUNCHER_STATUS_ID);
    expect(description()?.textContent).toBe('Genie is still working on your question.');

    act(() => turnStatus.setGenieTurnStatus('ready', 'withheld'));
    expect(fab()!.classList.contains('is-genie-ready')).toBe(true);
    expect(fab()!.classList.contains('is-genie-running')).toBe(false);
    expect(description()?.textContent).toBe('Genie finished your question. Open Genie to see the result.');

    // Opening mounts the chat, which renders its own FAB and description:
    // the dock's are gone, so the two never coexist.
    render(true);
    await settle();
    expect(chat.mounts).toBe(1);
    expect(fab()).toBeNull();
    expect(description()).toBeNull();
    expect(signal.resume).toHaveBeenCalledTimes(1);
  }, MODULE_GRAPH_BUDGET_MS);
});

/**
 * The panel (GenieChat) and /ask-genie resume through the same store call on
 * mount, resumeGenieTurnFromSession (lib/genieInFlightTurn). Mounted while
 * the gate is pending, that call is recorded once and runs on the first
 * 'opened': the record is read only after the gate kept it for its owner.
 */
describe('a Genie surface mounted while the gate is pending resumes once it opens', () => {
  let turns: typeof import('../../lib/genieInFlightTurn');
  let scope: typeof import('../../lib/actorScope');
  let session: Map<string, string>;

  beforeEach(async () => {
    vi.resetModules();
    session = installSessionStorage();
    scope = await import('../../lib/actorScope');
    turns = await import('../../lib/genieInFlightTurn');
    turns.__resetGenieTurnStoreForTests();
    // After the turn store's reset: the gate reset also drops the removal
    // that reset queued while the fresh gate was pending.
    scope._resetActorScopeForTests({ status: 'pending', owner: scope.NOBODY });
    session.clear();
    session.set('mip.actorCacheKey', ACTOR_A);
    // A record written before completion jobs: resuming it removes it and
    // leaves an "interrupted" note, both observable without a network.
    session.set(GENIE_IN_FLIGHT_TURN_KEY, JSON.stringify({ v: 1, phase: 'polling' }));
  }, MODULE_GRAPH_BUDGET_MS);

  it('the same actor: nothing while pending, the resume on the first opened', () => {
    turns.resumeGenieTurnFromSession();
    turns.resumeGenieTurnFromSession();
    expect(session.has(GENIE_IN_FLIGHT_TURN_KEY), 'untouched while pending').toBe(true);
    expect(turns.getGenieTurnSnapshot().notes).toHaveLength(0);
    scope.observeActor({ key: ACTOR_A });
    expect(session.has(GENIE_IN_FLIGHT_TURN_KEY)).toBe(false);
    expect(turns.getGenieTurnSnapshot().notes).toHaveLength(1);
  });

  it('another actor: the gate removes the record first, so the deferred resume finds nothing and adds no note', () => {
    turns.resumeGenieTurnFromSession();
    scope.observeActor({ key: ACTOR_B });
    expect(session.has(GENIE_IN_FLIGHT_TURN_KEY)).toBe(false);
    expect(turns.getGenieTurnSnapshot().notes).toHaveLength(0);
  });
});
