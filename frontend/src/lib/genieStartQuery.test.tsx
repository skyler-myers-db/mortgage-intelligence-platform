/**
 * @vitest-environment happy-dom
 *
 * One `/api/genie/start` read for both Genie surfaces (audit 2026-09-21
 * `runtime-06`, Genie slice), at the rendered layer: the route and the
 * floating panel on the real query client and the real stores; only the api
 * boundary is mocked (the /ask-genie turn harness).
 */
import { act, lazy, type ComponentType } from 'react';
import { QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import {
  SAMPLE,
  appState,
  genieStart,
  installStorage,
  mount,
  render,
  resetMocks,
  waitUntil,
} from '../routes/ask-genie.turn.test-support';
import { GenieDock } from '../components/layout/GenieDock';
import { GenieChat } from '../components/mortgage/GenieChat';
import { clearGenieTurns } from './genieConversationStore';
import { __resetGenieTurnStoreForTests } from './genieInFlightTurn';
import { __resetGenieAnnouncerForTests } from '../components/mortgage/useGenieAnnouncer';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

async function settle(): Promise<void> {
  await act(async () => new Promise((resolve) => setTimeout(resolve, 20)));
}

describe('the shared /api/genie/start read', () => {
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

  it('the route reads it once, as before, and shows its starters', async () => {
    mount(root, { route: true });
    await waitUntil(() => container.textContent?.includes(SAMPLE) ?? false);
    expect(genieStart).toHaveBeenCalledTimes(1);
    expect(genieStart.mock.calls[0][0]).toBeInstanceOf(AbortSignal);
  });

  it('a panel opened after /ask-genie read it (within five minutes) sends no second read', async () => {
    const queryClient = mount(root, { route: true });
    await waitUntil(() => genieStart.mock.calls.length === 1);
    await settle();

    appState.genieOpen = true;
    render(root, queryClient, { route: true, panel: true });
    await waitUntil(() => container.querySelector('[role="dialog"][aria-label="Genie chat"]') !== null);
    await settle();
    expect(genieStart).toHaveBeenCalledTimes(1);
    // The panel took the route's starters from the shared cache.
    const panel = container.querySelector('[role="dialog"][aria-label="Genie chat"]')!;
    expect(panel.textContent).toContain(SAMPLE);
  });

  it('a closed, never-opened panel sends none; its first open sends one', async () => {
    const queryClient = mount(root, { route: false });
    const Chat: ComponentType = lazy(async () => ({ default: GenieChat }));
    const dock = (open: boolean) =>
      act(() =>
        root.render(
          <QueryClientProvider client={queryClient}>
            <MemoryRouter initialEntries={['/segment-intelligence']}>
              <GenieDock open={open} onOpen={() => undefined} onClose={() => undefined} onWarm={() => undefined} Chat={Chat} />
            </MemoryRouter>
          </QueryClientProvider>,
        ),
      );
    dock(false);
    await settle();
    expect(genieStart).not.toHaveBeenCalled();

    appState.genieOpen = true;
    dock(true);
    await waitUntil(() => genieStart.mock.calls.length === 1);
    await settle();
    expect(genieStart).toHaveBeenCalledTimes(1);
  });
});
