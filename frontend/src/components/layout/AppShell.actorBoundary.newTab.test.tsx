// @vitest-environment happy-dom

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ACTOR_SCOPE_REGISTRY, NOBODY, _resetActorScopeForTests, actorScopeStatus } from '../../lib/actorScope';
import { GENIE_CONVERSATION_STORAGE_KEY } from '../../lib/genieConversation';
import { GENIE_CONVERSATION_TURNS_KEY, getGenieTurns } from '../../lib/genieConversationStore';
import { SINGLE_KEY_SHORTCUTS_STORAGE_KEY, useSingleKeyShortcuts } from '../../lib/keymapPreference';
import { PINNED_INSIGHTS_KEY } from '../../lib/pinnedInsights';
import { _resetSessionStatusForTests } from '../../lib/sessionStatus';
import { ACTOR_A, ACTOR_B } from '../../test/actorKeys';
import { installLocalStorage } from '../../test/installLocalStorage';
import { PinnedInsights } from '../mortgage/PinnedInsights';
import { AppShell } from './AppShell';

/** The dock resumes through a lazy chunk; it has its own suite (see
 *  AppShell.actorBoundary.test.tsx). */
vi.mock('./GenieDock', () => ({ GenieDock: () => null }));

/**
 * The new-tab gap (D-identity-review-b; audit genie-02 item 1, bundle-04 item
 * 4), on the REAL AppShell. A new tab has no sessionStorage stamp, so the
 * shell used to find no previous key and keep whatever the previous actor
 * left in the SHARED localStorage: pins, the Genie conversation id, the
 * shortcut choice. The local owner stamp ('mip.actorOwner') now travels with
 * that data and the actor gate compares it with the first trusted
 * observation, which the zero-dependency /api/session read may supply before
 * the first health probe.
 */

const { STAMPS } = ACTOR_SCOPE_REGISTRY;
const PIN = { id: 'pin-a', question: "A's pinned question", summary: "A's answer", source: null, pinnedAt: '2026-09-30' };

function sessionBody(extra: Record<string, unknown>) {
  return { can_access_admin: false, can_approve: false, actor_email: null, role_labels: [], ...extra };
}

/** A's shared localStorage, seen from a new tab (no sessionStorage). */
function seedActorALocal(options: { stamped?: boolean; shortcuts?: string } = {}): void {
  const { stamped = true, shortcuts = JSON.stringify({ [ACTOR_A]: 'off' }) } = options;
  if (stamped) window.localStorage.setItem(STAMPS.local, ACTOR_A);
  window.localStorage.setItem(PINNED_INSIGHTS_KEY, JSON.stringify([PIN]));
  window.localStorage.setItem(GENIE_CONVERSATION_STORAGE_KEY, 'conv-of-a');
  window.localStorage.setItem(SINGLE_KEY_SHORTCUTS_STORAGE_KEY, shortcuts);
  window.localStorage.setItem('mip.theme', 'light');
  window.localStorage.setItem('mip-genie-chat-size-v1', '{"w":420,"h":560}');
}

function ShortcutsProbe() {
  const [enabled] = useSingleKeyShortcuts();
  return <output data-single-key="">{enabled ? 'on' : 'off'}</output>;
}

describe('AppShell actor boundary in a new tab', () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;
  let healthKey: string | null;
  let session: unknown;
  let releaseHealth: () => void;
  let healthHeld: Promise<void>;
  let sessionFetches: number;

  beforeEach(() => {
    _resetActorScopeForTests({ status: 'pending', owner: NOBODY });
    installLocalStorage();
    window.sessionStorage.clear();
    _resetSessionStatusForTests();
    healthKey = ACTOR_B;
    session = null;
    sessionFetches = 0;
    healthHeld = Promise.resolve();
    releaseHealth = () => undefined;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      const path = new URL(url, 'http://localhost').pathname;
      if (path === '/api/v1/health') {
        await healthHeld;
        return new Response(
          JSON.stringify({ status: 'ok', mode: 'live', dependencies: {}, circuit_breakers: {}, actor_cache_key: healthKey }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        );
      }
      if (path === '/api/v1/session' && session !== null) {
        sessionFetches += 1;
        return new Response(JSON.stringify(session), { status: 200, headers: { 'content-type': 'application/json' } });
      }
      return new Response('{"detail":"not found"}', { status: 404 });
    }));
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    releaseHealth();
    await act(async () => root.unmount());
    container.remove();
    queryClient.clear();
    window.sessionStorage.clear();
    _resetSessionStatusForTests();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    _resetActorScopeForTests({ status: 'open', owner: NOBODY });
  });

  function holdHealth(): void {
    healthHeld = new Promise((resolve) => {
      releaseHealth = resolve;
    });
  }

  async function settle(rounds = 10): Promise<void> {
    for (let round = 0; round < rounds; round += 1) {
      await act(async () => {
        await new Promise((resolve) => window.setTimeout(resolve, 5));
      });
    }
  }

  async function openTab(): Promise<void> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/']}>
            <AppShell>
              <PinnedInsights />
              <ShortcutsProbe />
            </AppShell>
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  /** Open the tab and wait for the gate to resolve for the probed key. */
  async function openTabAndResolve(): Promise<void> {
    await openTab();
    for (let attempt = 0; attempt < 50 && actorScopeStatus() === 'pending'; attempt += 1) await settle(1);
    await settle(2);
  }

  const pinsCard = () => container.querySelector('section.pinned-insights');
  const singleKey = () => container.querySelector('[data-single-key]')?.textContent;
  const shortcutMap = () => JSON.parse(window.localStorage.getItem(SINGLE_KEY_SHORTCUTS_STORAGE_KEY) ?? '{}');

  it("probed as B: A's pins and conversation id are removed; B reads the default shortcut while A keeps its entry; device keys stay", async () => {
    seedActorALocal();
    await openTabAndResolve();
    expect(actorScopeStatus()).toBe('open');
    expect(window.localStorage.getItem(PINNED_INSIGHTS_KEY)).toBeNull();
    expect(window.localStorage.getItem(GENIE_CONVERSATION_STORAGE_KEY)).toBeNull();
    expect(pinsCard()).toBeNull();
    expect(singleKey()).toBe('on');
    expect(shortcutMap()).toEqual({ [ACTOR_A]: 'off' });
    expect(window.localStorage.getItem('mip.theme')).toBe('light');
    expect(window.localStorage.getItem('mip-genie-chat-size-v1')).toBe('{"w":420,"h":560}');
    expect(window.localStorage.getItem(STAMPS.local)).toBe(ACTOR_B);
    expect(window.sessionStorage.getItem(STAMPS.session)).toBe(ACTOR_B);
  });

  it('probed as A: keeps everything, shows the pin, and the shortcut reads off', async () => {
    healthKey = ACTOR_A;
    seedActorALocal();
    await openTabAndResolve();
    expect(window.localStorage.getItem(PINNED_INSIGHTS_KEY)).not.toBeNull();
    expect(window.localStorage.getItem(GENIE_CONVERSATION_STORAGE_KEY)).toBe('conv-of-a');
    expect(pinsCard()?.textContent).toContain("A's pinned question");
    expect(singleKey()).toBe('off');
  });

  it('upgrade (no owner stamp yet): the pins are removed and only the legacy shortcut value is adopted', async () => {
    healthKey = ACTOR_A;
    seedActorALocal({ stamped: false, shortcuts: 'off' });
    await openTabAndResolve();
    expect(window.localStorage.getItem(PINNED_INSIGHTS_KEY)).toBeNull();
    expect(pinsCard()).toBeNull();
    expect(shortcutMap()).toEqual({ [ACTOR_A]: 'off' });
    expect(singleKey()).toBe('off');
  });

  it('before any trusted observation, the pins card and the transcript render nothing from storage', async () => {
    healthKey = ACTOR_A;
    holdHealth();
    seedActorALocal();
    window.sessionStorage.setItem(STAMPS.session, ACTOR_A);
    window.sessionStorage.setItem(
      GENIE_CONVERSATION_TURNS_KEY,
      JSON.stringify([{ question: "A's question", response: { answer: "A's answer", source: 'genie' } }]),
    );
    await openTab();
    await settle();
    expect(actorScopeStatus()).toBe('pending');
    expect(pinsCard()).toBeNull();
    expect(getGenieTurns()).toEqual([]);
    releaseHealth();
    for (let attempt = 0; attempt < 50 && actorScopeStatus() === 'pending'; attempt += 1) await settle(1);
    await settle(2);
    expect(pinsCard()?.textContent).toContain("A's pinned question");
    expect(getGenieTurns().map((turn) => turn.question)).toEqual(["A's question"]);
  });

  it('a session seed of A opens the gate before the first health probe answers', async () => {
    healthKey = ACTOR_A;
    holdHealth();
    session = sessionBody({ actor_cache_key: ACTOR_A });
    seedActorALocal();
    await openTab();
    for (let attempt = 0; attempt < 50 && actorScopeStatus() === 'pending'; attempt += 1) await settle(1);
    await settle(2);
    expect(sessionFetches).toBeGreaterThan(0);
    expect(actorScopeStatus(), 'opened by the session seed; health is still held').toBe('open');
    expect(pinsCard()?.textContent).toContain("A's pinned question");
  });

  it.each([
    ['{}', {}],
    ['a malformed key', sessionBody({ actor_cache_key: 'bob@x' })],
    ['an older backend without the field', sessionBody({})],
  ])('a session body of %s seeds nothing', async (_label, body) => {
    holdHealth();
    session = body;
    seedActorALocal();
    await openTab();
    await settle();
    expect(sessionFetches).toBeGreaterThan(0);
    expect(actorScopeStatus()).toBe('pending');
    expect(pinsCard()).toBeNull();
    expect(window.localStorage.getItem(PINNED_INSIGHTS_KEY), 'nothing resolved, nothing removed').not.toBeNull();
  });
});
