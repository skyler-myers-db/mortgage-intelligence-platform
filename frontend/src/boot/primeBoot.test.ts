// @vitest-environment happy-dom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { healthPath } from '../lib/apiClients/analytics';
import { apiPath } from '../lib/apiPaths';
import source from './primeBoot.ts?raw';
import type { BootPrime } from './primeBoot';

/**
 * The boot module (audit bundle-02) run as the browser runs it: imported for
 * its side effect, with a stubbed fetch. Its paths are literals (it imports
 * nothing), so they are pinned here to what lib/api would request.
 */

interface Call {
  url: string;
  init: RequestInit | undefined;
}

const EXPECTED = {
  session: { url: apiPath('/session'), redirect: 'manual' },
  options: { url: apiPath('/config/options'), redirect: 'manual' },
  footprint: { url: apiPath('/config/footprint'), redirect: undefined },
  health: { url: apiPath(healthPath({ idleS: 0 })), redirect: 'manual' },
} as const;

type ProcessLike = {
  on: (event: string, listener: (reason: unknown) => void) => void;
  off: (event: string, listener: (reason: unknown) => void) => void;
};

function bootGlobal(): BootPrime | undefined {
  return (window as Window & { __MIP_BOOT__?: BootPrime }).__MIP_BOOT__;
}

function setVisibility(state: DocumentVisibilityState): void {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state });
}

function setOnline(online: boolean): void {
  Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => online });
}

describe('src/boot/primeBoot', () => {
  let calls: Call[];

  beforeEach(() => {
    calls = [];
    delete (window as Window & { __MIP_BOOT__?: BootPrime }).__MIP_BOOT__;
    setVisibility('visible');
    setOnline(true);
    vi.stubGlobal('fetch', (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return Promise.resolve(new Response('{}', { status: 200 }));
    });
    vi.resetModules();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    delete (window as Window & { __MIP_BOOT__?: BootPrime }).__MIP_BOOT__;
    setVisibility('visible');
    setOnline(true);
  });

  async function runBootModule(): Promise<void> {
    await import('./primeBoot');
  }

  it('starts the four non-audited boot reads with the paths lib/api would use', async () => {
    await runBootModule();

    expect(calls.map((call) => ({ url: call.url, redirect: call.init?.redirect }))).toEqual([
      EXPECTED.session,
      EXPECTED.options,
      EXPECTED.footprint,
      EXPECTED.health,
    ]);
    expect(Object.keys(bootGlobal()?.reads ?? {}).sort()).toEqual(['footprint', 'health', 'options', 'session']);
    expect(calls.map((call) => call.url).join(' '), 'never an audit-writing read').not.toMatch(
      /\/leads|\/borrowers|\/outreach|\/offers|\/proof/,
    );
  });

  it('skips the health prime while the tab is hidden, as the first probe would', async () => {
    setVisibility('hidden');
    await runBootModule();

    expect(calls.map((call) => call.url)).toEqual([EXPECTED.session.url, EXPECTED.options.url, EXPECTED.footprint.url]);
    expect(bootGlobal()?.reads.health).toBeUndefined();
  });

  it('skips the health prime while the browser is offline', async () => {
    setOnline(false);
    await runBootModule();

    expect(calls.map((call) => call.url)).not.toContain(EXPECTED.health.url);
    expect(bootGlobal()?.reads.health).toBeUndefined();
  });

  it('records start and settle times', async () => {
    const answers: Array<(res: Response) => void> = [];
    vi.stubGlobal('fetch', () => new Promise<Response>((resolve) => {
      answers.push(resolve);
    }));
    await runBootModule();
    const read = bootGlobal()?.reads.session;
    expect(read?.startedAt).toEqual(expect.any(Number));
    expect(read?.settledAt, 'in flight').toBeNull();
    answers[0](new Response('{}', { status: 200 }));
    await read?.response;
    await Promise.resolve();
    expect(read?.settledAt).toBeGreaterThanOrEqual(read?.startedAt ?? Number.POSITIVE_INFINITY);
  });

  it('never lets a failed prime reach the error log as an unhandled rejection', async () => {
    const unhandled: unknown[] = [];
    const onUnhandled = (reason: unknown) => {
      unhandled.push(reason);
    };
    const proc = (globalThis as { process?: ProcessLike }).process;
    proc?.on('unhandledRejection', onUnhandled);
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('Failed to fetch')));
    try {
      await runBootModule();
      await new Promise((resolve) => setTimeout(resolve, 0));
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(unhandled).toEqual([]);
      const read = bootGlobal()?.reads.options;
      expect(read?.settledAt, 'a rejection settles too').not.toBeNull();
    } finally {
      proc?.off('unhandledRejection', onUnhandled);
    }
  });

  it('exposes one frozen global', async () => {
    await runBootModule();
    const prime = bootGlobal();
    expect(prime).toBeDefined();
    expect(Object.isFrozen(prime)).toBe(true);
    expect(Object.isFrozen(prime?.reads)).toBe(true);
    for (const read of Object.values(prime?.reads ?? {})) expect(Object.isFrozen(read)).toBe(true);
    expect(() => {
      (prime as { reads: unknown }).reads = {};
    }).toThrow(TypeError);
  });

  it('a boot failure leaves no global and throws nothing', async () => {
    vi.stubGlobal('fetch', () => {
      throw new Error('fetch is broken');
    });
    await expect(runBootModule()).resolves.toBeUndefined();
    expect(bootGlobal()).toBeUndefined();
  });

  it('imports nothing and exports only types, so no chunk can depend on it', () => {
    const code = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
    expect(code).not.toMatch(/^\s*import\b/m);
    expect(code).not.toMatch(/\bimport\s*\(/);
    expect(code).not.toMatch(/\brequire\s*\(/);
    expect(code).not.toMatch(/^\s*export\s+(?!type\b|interface\b)/m);
  });
});
