/**
 * The Console workspace writes on the TanStack mutation cache (audit
 * stack-09 item 3, states-04, runtime-03): the keys, per-target scope
 * serialisation, isMutating, the failure copy, and the wire posture through
 * the REAL transport and api client with `fetch` stubbed (no request_id; a
 * 503 on the PUT is never re-sent).
 */
// @ts-expect-error Frontend app types intentionally exclude Node globals; the
// source and build-modules gates below read files under Vitest only.
import { existsSync, readFileSync } from 'node:fs';
import { QueryClient } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '../api';
import { ApiError } from '../apiTransport';
import {
  WORKSPACE_FAILURE,
  WORKSPACE_WRITE_KEY,
  reportWorkspaceFailure,
  runWorkspaceWrite,
  withEntry,
  withoutKey,
} from './workspace';

const FRONTEND = decodeURIComponent(new URL('../../../', import.meta.url).pathname);
const LEAD = 'B-0000000000001';

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  let reject: (error: unknown) => void = () => undefined;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('runWorkspaceWrite', () => {
  it('keys the mutation by kind, op and target, and scopes it per target', async () => {
    const client = new QueryClient();
    await runWorkspaceWrite(client, { kind: 'lead', op: 'save', target: LEAD, write: async () => 'ok' });

    const [mutation] = client.getMutationCache().getAll();
    expect(mutation?.options.mutationKey).toEqual([...WORKSPACE_WRITE_KEY, 'lead', 'save', LEAD]);
    expect(mutation?.options.scope).toEqual({ id: `workspace:lead:${LEAD}` });
    expect(mutation?.options.networkMode).toBe('always');
    expect(mutation?.options.retry).toBeUndefined();
    expect(mutation?.state.status).toBe('success');
  });

  it('runs writes to ONE target in order: a remove never starts before its save settles', async () => {
    const client = new QueryClient();
    const save = deferred<string>();
    const started: string[] = [];
    const first = runWorkspaceWrite(client, { kind: 'lead', op: 'save', target: LEAD, write: () => (started.push('save'), save.promise) });
    const second = runWorkspaceWrite(client, { kind: 'lead', op: 'remove', target: LEAD, write: async () => (started.push('remove'), 'removed') });
    const other = runWorkspaceWrite(client, { kind: 'lead', op: 'save', target: 'B-0000000000002', write: async () => (started.push('other'), 'other') });
    await flush();

    expect(started, 'another target is not held').toEqual(['save', 'other']);
    expect(client.isMutating({ mutationKey: [...WORKSPACE_WRITE_KEY] }), 'the save and the queued remove').toBe(2);
    save.resolve('saved');
    await expect(first).resolves.toBe('saved');
    await expect(second).resolves.toBe('removed');
    await other;
    expect(started).toEqual(['save', 'other', 'remove']);
    expect(client.isMutating({ mutationKey: [...WORKSPACE_WRITE_KEY] })).toBe(0);
  });

  it('a failed write rejects with its error and is not retried', async () => {
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    const write = vi.fn(async () => {
      throw new Error('boom');
    });

    await expect(runWorkspaceWrite(client, { kind: 'draft', op: 'remove', target: LEAD, write })).rejects.toThrow('boom');
    expect(write).toHaveBeenCalledTimes(1);
  });
});

describe('the wire posture through the real transport', () => {
  function stubFetch(responses: Response[]): Array<{ path: string; init: RequestInit | undefined }> {
    const sent: Array<{ path: string; init: RequestInit | undefined }> = [];
    vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
      sent.push({ path, init });
      const next = responses.shift();
      if (!next) throw new Error('unexpected extra request');
      return next;
    });
    return sent;
  }

  const warming503 = () =>
    new Response(JSON.stringify({ detail: 'warming', retryable: true, dependency: 'lakebase', reason: 'warming_up' }), {
      status: 503,
      headers: { 'Content-Type': 'application/json', 'Retry-After': '0' },
    });

  it('a save sends no request_id, and a 503 on PUT /api/workspace/leads/{id} is not re-sent', async () => {
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    const sent = stubFetch([warming503(), new Response('{}', { status: 200 })]);
    const lead = { borrower_id: LEAD, city: 'Chicago', state: 'IL', zip: '60611', recommended_offer: 'Refi', opportunity_score: 80, confidence: 0.8 };

    const error = await runWorkspaceWrite(client, { kind: 'lead', op: 'save', target: LEAD, write: () => api.saveWorkspaceLead(lead) }).catch(
      (err: unknown) => err,
    );

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(503);
    expect(sent).toHaveLength(1);
    expect(sent[0]?.path).toBe(`/api/v1/workspace/leads/${LEAD}`);
    expect(sent[0]?.init?.method).toBe('PUT');
    expect(JSON.parse(String(sent[0]?.init?.body))).not.toHaveProperty('request_id');
  });
});

describe('reportWorkspaceFailure', () => {
  function errorState(initial: string | null = null) {
    const state = { current: initial };
    const setError = (update: (current: string | null) => string | null) => {
      state.current = update(state.current);
    };
    return { state, setError };
  }

  it('a 409 actor_changed reads as the user change; never the message', () => {
    const { state, setError } = errorState();
    const refused = new ApiError('SENTINEL raw server detail', { path: '/api/v1/workspace/leads/x', status: 409, reason: 'actor_changed' });

    reportWorkspaceFailure('saveLead', refused, setError);

    expect(state.current).toBe("Couldn't save the lead. Nothing was recorded. This tab resets for the new user.");
  });

  it.each([
    ['a server error', new ApiError('SENTINEL 500 Internal Server Error', { path: '/api/v1/workspace', status: 500 })],
    ['another 409', new ApiError('SENTINEL stale', { path: '/api/v1/workspace', status: 409, reason: 'something_else' })],
    ['a plain Error', new Error('SENTINEL Failed to fetch')],
    ['a thrown string', 'SENTINEL'],
  ])('%s shows the fixed sentence alone', (_label, error) => {
    const { state, setError } = errorState('previous');
    reportWorkspaceFailure('removeDraft', error, setError);
    expect(state.current).toBe(WORKSPACE_FAILURE.removeDraft);
  });

  it('every action has its own fixed sentence', () => {
    expect(Object.values(WORKSPACE_FAILURE)).toEqual([
      "Couldn't load the saved workspace.",
      "Couldn't save the lead.",
      "Couldn't remove the saved lead.",
      "Couldn't save the draft.",
      "Couldn't remove the draft.",
    ]);
  });
});

describe('record helpers', () => {
  it('withEntry sets one key and withoutKey drops one, leaving the input untouched', () => {
    const base = { a: 1, b: 2 };
    expect(withEntry(base, 'c', 3)).toEqual({ a: 1, b: 2, c: 3 });
    expect(withEntry(base, 'a', 9)).toEqual({ a: 9, b: 2 });
    expect(withoutKey(base, 'a')).toEqual({ b: 2 });
    expect(base).toEqual({ a: 1, b: 2 });
  });
});

describe('the initial chunk never takes the observer-based mutation hooks', () => {
  const LAZY_ONLY = ['@tanstack/react-query/build/modern/useMutation.js', '@tanstack/query-core/build/modern/mutationObserver.js'];

  it('AppContext and this module import neither useMutation nor MutationObserver', () => {
    for (const path of ['src/components/AppContext.tsx', 'src/lib/mutations/workspace.ts']) {
      const text = readFileSync(`${FRONTEND}${path}`, 'utf8') as string;
      expect(text, path).not.toMatch(/import\s*(type\s*)?\{[^}]*\b(useMutation|MutationObserver)\b[^}]*\}\s*from/);
      expect(text, path).not.toMatch(/\b(useMutation|new MutationObserver)\(/);
    }
  });

  it.skipIf(!existsSync(`${FRONTEND}build-meta/build-modules.json`))(
    "the built chunk holding AppContext, and every vendor chunk, renders neither module",
    () => {
      const meta = JSON.parse(readFileSync(`${FRONTEND}build-meta/build-modules.json`, 'utf8') as string) as {
        chunks: Record<string, string[]>;
      };
      const chunks = Object.entries(meta.chunks);
      const appContext = chunks.filter(([, ids]) => ids.includes('src/components/AppContext.tsx'));
      expect(appContext, 'AppContext is in exactly one chunk').toHaveLength(1);
      const watched = [...appContext, ...chunks.filter(([file]) => file.startsWith('assets/vendor-'))];
      const offenders = watched.flatMap(([file, ids]) => ids.filter((id) => LAZY_ONLY.some((m) => id.endsWith(m))).map((id) => `${file}: ${id}`));
      expect(offenders).toEqual([]);
    },
  );
});
