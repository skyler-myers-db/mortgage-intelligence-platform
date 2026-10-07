/**
 * @vitest-environment happy-dom
 *
 * The client half of the per-request actor binding (D-identity-review-a4;
 * audit genie-02 / bundle-04 item 4), driven through the real transport with
 * `fetch` stubbed. happy-dom, because the gate's cross-tab listener is a real
 * `window` 'storage' listener.
 *
 * Pins: the owner key on every app request once the document opened for a
 * real actor; none on health or session, while pending, or for nobody; still
 * sent after the gate closes (the key is sticky); ONE captured value per
 * logical request across a retry; a 409 actor_changed surfaces as its reason,
 * is never re-sent and asks for one health recheck. And the two residual
 * windows of docs/security/identity-boundary.md §6, each ending in a 409
 * instead of a request accepted under the new cookie.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ACTOR_A, ACTOR_B } from '../test/actorKeys';
import { installLocalStorage } from '../test/installLocalStorage';
import {
  ACTOR_SCOPE_REGISTRY,
  NOBODY,
  _resetActorScopeForTests,
  _setResetDocumentForTests,
  actorResetHeld,
  actorScopeStatus,
  documentOwnerKey,
  observeActor,
} from './actorScope';
import { ACTOR_KEY_HEADER } from './apiActorBinding';
import { ApiError, getJson, postJson } from './apiTransport';
import { subscribeHealthRecheck } from './healthRecheck';

const { STAMPS } = ACTOR_SCOPE_REGISTRY;
const ACTOR_A2 = `actor_${'a2'.repeat(8)}`;
const REFUSAL = {
  detail: 'The signed-in user changed in this browser. Nothing was recorded.',
  reason: 'actor_changed',
};

interface Sent {
  path: string;
  method: string;
  headers: Record<string, string>;
}

let sent: Sent[];
let rechecks: number;
const cleanups: Array<() => void> = [];

function reply(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', 'Retry-After': '0', ...headers },
  });
}

/** Answer in order; `during` runs inside the call it is keyed to (0-based). */
function stubFetch(responses: Response[], during: Record<number, () => void> = {}): void {
  vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
    const index = sent.length;
    sent.push({
      path,
      method: (init?.method ?? 'GET').toUpperCase(),
      headers: { ...((init?.headers ?? {}) as Record<string, string>) },
    });
    during[index]?.();
    const next = responses.shift();
    if (!next) throw new Error('unexpected extra request');
    return next;
  });
}

const keyOf = (request: Sent | undefined) => request?.headers[ACTOR_KEY_HEADER];

function openFor(owner: string): void {
  _resetActorScopeForTests({ status: 'open', owner });
  rechecks = 0;
}

beforeEach(() => {
  installLocalStorage();
  window.sessionStorage.clear();
  sent = [];
  rechecks = 0;
  cleanups.push(subscribeHealthRecheck(() => {
    rechecks += 1;
  }));
});

afterEach(() => {
  while (cleanups.length > 0) cleanups.pop()?.();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  _resetActorScopeForTests({ status: 'open', owner: NOBODY });
  window.sessionStorage.clear();
});

describe('the owner key on app requests', () => {
  it('rides GET and POST once the document opened for a real actor, keeping the other headers', async () => {
    openFor(ACTOR_A);
    stubFetch([reply(200, []), reply(200, { ok: true })]);

    await getJson('/api/leads?limit=5');
    await postJson('/api/portfolio/preview', {}, undefined, { 'Idempotency-Key': 'idem-1' });

    expect(sent.map(keyOf)).toEqual([ACTOR_A, ACTOR_A]);
    expect(sent[1]?.headers).toEqual({
      'Content-Type': 'application/json',
      'Idempotency-Key': 'idem-1',
      [ACTOR_KEY_HEADER]: ACTOR_A,
    });
  });

  it.each(['/api/health', '/api/health?idle_s=0', '/api/v1/health?idle_s=12', '/api/session', '/api/v1/session'])(
    'is never sent on %s: health and session say who the tab is now',
    async (path) => {
      openFor(ACTOR_A);
      stubFetch([reply(200, { status: 'ok', mode: 'live' })]);

      await getJson(path);

      expect(sent).toHaveLength(1);
      expect(keyOf(sent[0])).toBeUndefined();
    },
  );

  it('is not sent while the gate is pending, before any open', async () => {
    _resetActorScopeForTests({ status: 'pending', owner: NOBODY });
    stubFetch([reply(200, [])]);

    await getJson('/api/leads');

    expect(documentOwnerKey()).toBeNull();
    expect(keyOf(sent[0])).toBeUndefined();
  });

  it("is not sent for '~nobody', nor after nobody is observed", async () => {
    openFor(NOBODY);
    stubFetch([reply(200, []), reply(200, [])]);

    await getJson('/api/leads');
    observeActor({ key: null });
    await getJson('/api/leads');

    expect(sent.map(keyOf)).toEqual([undefined, undefined]);
  });
});

describe('the key is sticky: still sent after the gate closes', () => {
  it('after nobody is observed (row 5)', async () => {
    openFor(ACTOR_A);
    observeActor({ key: null });
    stubFetch([reply(200, [])]);

    await getJson('/api/leads');

    expect(actorScopeStatus()).toBe('closed');
    expect(keyOf(sent[0])).toBe(ACTOR_A);
  });

  it('after a cross-tab storage event suspends the gate', async () => {
    openFor(ACTOR_A);
    window.dispatchEvent(new StorageEvent('storage', { key: STAMPS.local, newValue: ACTOR_B, storageArea: window.localStorage }));
    stubFetch([reply(200, [])]);

    await getJson('/api/leads');

    expect(actorScopeStatus()).toBe('closed');
    expect(keyOf(sent[0])).toBe(ACTOR_A);
  });

  it('after a proven change resets the document, and during its 30 s hold (an imperative prefetch)', async () => {
    openFor(ACTOR_A);
    window.sessionStorage.setItem('mip.actorResetAt', String(Date.now() - 5_000));
    _setResetDocumentForTests(vi.fn());
    observeActor({ key: ACTOR_B });
    stubFetch([reply(200, [])]);

    await getJson('/api/leads');

    expect(actorResetHeld()).toBe(true);
    expect(keyOf(sent[0])).toBe(ACTOR_A);
  });
});

describe('one captured value per logical request', () => {
  it('a 429 retry re-sends the same key even when the owner is re-stamped to an alias between attempts', async () => {
    openFor(ACTOR_A);
    stubFetch(
      [reply(429, { detail: 'busy', retryable: true, reason: 'rate_limited' }), reply(200, []), reply(200, [])],
      { 0: () => observeActor({ key: ACTOR_A2, aliases: [ACTOR_A] }) },
    );

    await getJson('/api/leads');
    await getJson('/api/leads');

    expect(documentOwnerKey()).toBe(ACTOR_A2);
    expect(sent.map(keyOf)).toEqual([ACTOR_A, ACTOR_A, ACTOR_A2]);
  });
});

describe('a 409 actor_changed', () => {
  it('surfaces as ApiError.reason, is never re-sent, and asks for one health recheck', async () => {
    openFor(ACTOR_A);
    stubFetch([reply(409, REFUSAL), reply(200, [])]);

    const error = await getJson('/api/leads').catch((err: unknown) => err);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(409);
    expect((error as ApiError).reason).toBe('actor_changed');
    expect(sent).toHaveLength(1);
    expect(rechecks).toBe(1);
  });

  it('any other 409 reason passes through without a recheck; a reason-less 409 has none', async () => {
    openFor(ACTOR_A);
    stubFetch([reply(409, { detail: 'stale', reason: 'something_else' }), reply(409, { detail: 'stale' })]);

    const other = await getJson('/api/leads').catch((err: unknown) => err);
    const plain = await getJson('/api/leads').catch((err: unknown) => err);

    expect((other as ApiError).reason).toBe('something_else');
    expect((plain as ApiError).reason).toBeNull();
    expect(rechecks).toBe(0);
  });
});

describe('the residual windows end in a 409 (identity-boundary.md §6)', () => {
  it('window (1): a read issued after the reset, before the page unloads, carries the old owner and is refused', async () => {
    openFor(ACTOR_A);
    const resetDocument = vi.fn();
    _setResetDocumentForTests(resetDocument);
    observeActor({ key: ACTOR_B });
    expect(resetDocument).toHaveBeenCalledTimes(1);
    stubFetch([reply(409, REFUSAL), reply(200, [])]);

    const error = await getJson('/api/leads').catch((err: unknown) => err);

    expect(keyOf(sent[0])).toBe(ACTOR_A);
    expect((error as ApiError).reason).toBe('actor_changed');
    expect(sent).toHaveLength(1);
    expect(rechecks).toBe(1);
  });

  it('window (3): the live Genie poll during a cross-tab suspicion carries the old owner and is refused', async () => {
    openFor(ACTOR_A);
    window.dispatchEvent(new StorageEvent('storage', { key: STAMPS.local, newValue: ACTOR_B, storageArea: window.localStorage }));
    expect(actorScopeStatus()).toBe('closed');
    expect(documentOwnerKey()).toBe(ACTOR_A);
    const afterSuspend = rechecks;
    stubFetch([reply(409, REFUSAL), reply(200, {})]);

    const error = await postJson('/api/genie/message/status', { message_id: 'msg-1' }).catch((err: unknown) => err);

    expect(sent[0]?.path).toBe('/api/v1/genie/message/status');
    expect(keyOf(sent[0])).toBe(ACTOR_A);
    expect((error as ApiError).reason).toBe('actor_changed');
    expect(sent).toHaveLength(1);
    expect(rechecks - afterSuspend).toBe(1);
  });
});
