/**
 * @vitest-environment happy-dom
 *
 * The shell resumes a Genie turn before the panel's first open (audit
 * 2026-09-21 `genie-02` item 2, Genie residual #2), at the rendered layer:
 * GenieDock reads the in-flight record key, loads the lazy launcher signal
 * only when there is one, resumes without mounting the chat, and its FAB and
 * sr-only description follow the signal until the chat mounts.
 */
import { act, useEffect, type ComponentType } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { GENIE_IN_FLIGHT_TURN_KEY } from '../../lib/genieConversation';

const signal = vi.hoisted(() => ({ imports: 0, ensure: vi.fn(), resume: vi.fn() }));

vi.mock('../../lib/genieLauncherSignal', () => {
  signal.imports += 1;
  return { ensureGenieLauncherSignal: signal.ensure, resumeGenieTurnFromSession: signal.resume };
});

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const chat = { mounts: 0 };

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

describe('GenieDock resumes a turn before the first open', () => {
  let container: HTMLDivElement;
  let root: Root;
  let session: Map<string, string>;
  let GenieDock: typeof import('./GenieDock').GenieDock;
  let turnStatus: typeof import('../../lib/genieTurnStatus');

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
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

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

  it('with no record: no import, no resume, an idle launcher', async () => {
    render(false);
    await settle();
    expect(signal.imports).toBe(0);
    expect(signal.ensure).not.toHaveBeenCalled();
    expect(signal.resume).not.toHaveBeenCalled();
    expect(fab()!.className).toBe('genie__fab');
    expect(fab()!.hasAttribute('aria-describedby')).toBe(false);
    expect(description()?.textContent).toBe('');
  });

  it('with a record: one import, one resume, no chat; the FAB and its description follow the signal until the open', async () => {
    session.set(GENIE_IN_FLIGHT_TURN_KEY, JSON.stringify({ v: 2, phase: 'completing' }));
    render(false);
    await settle();
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
  });
});
