/**
 * The hygiene rule for an asset the browser cancelled (hygiene.ts
 * isEngineAssetCancel). Chromium names a cancel net::ERR_ABORTED, which the
 * harness drops for every request; Firefox says NS_BINDING_ABORTED and WebKit
 * "cancelled" (macOS) or "Load request cancelled" (Linux), which it used to
 * report as failed requests. React 19 re-assigns every mounted <img>'s src at
 * commit (react-dom commitMount), and Firefox can answer that with a second
 * request for the image it already holds, then cancel it unsent: the footer
 * wordmark failed forced-colors.firefox twice in CI run 42864141680 with
 * `GET .../assets/entrada-wordmark-<hash>.webp failed: NS_BINDING_ABORTED`
 * while the same URL was answered 200 in the same load.
 * A same-origin /assets/ GET cancelled in one of those spellings is now
 * forgiven only when the same URL landed (2xx or 304, body received) in the
 * same test; anything else still fails.
 *
 *  1. The rule, through Hygiene's own event handlers and a stand-in page: a
 *     cancel whose URL landed passes, before or after the cancel, in every
 *     spelling; one that never landed, or landed only as a 404, fails with
 *     its message unchanged; an /api/ cancel, a POST, another failure text and
 *     Chromium's ERR_ABORTED behave as before; allow() and an opt-out apply.
 *  2. The real engine: the page aborts a held fetch of an asset, so each
 *     engine reports the cancel in its own words; that cancel is a violation
 *     until the same URL lands, then it is forgiven. fixture-chromium,
 *     fixture-webkit and fixture-firefox-forced (FIREFOX_FORCED_SPEC names
 *     this file) all run it.
 */
import type { Page, Request } from '@playwright/test';
import { Hygiene, isEngineAssetCancel, type HygieneCheck, type HygieneViolation } from './hygiene';
import type { MockApi } from './mockApi';
import { expect, test } from './test';

const ORIGIN = 'http://127.0.0.1:4273';
const ASSET = `${ORIGIN}/assets/entrada-wordmark-Ro0WjzQe.webp`;
const ENGINE_CANCELS = ['NS_BINDING_ABORTED', 'cancelled', 'Load request cancelled'];

type Listener = (request: Request) => void;

/** The page surface Hygiene.attach touches: event listeners, url(), and three no-op setups. */
class StandInPage {
  private readonly listeners = new Map<string, Listener[]>();

  on(event: string, listener: Listener): this {
    this.listeners.set(event, [...(this.listeners.get(event) ?? []), listener]);
    return this;
  }

  emit(event: 'requestfailed' | 'requestfinished', request: Request): void {
    for (const listener of this.listeners.get(event) ?? []) listener(request);
  }

  url(): string {
    return `${ORIGIN}/lead-queue`;
  }

  async exposeFunction(): Promise<void> {}

  async addInitScript(): Promise<void> {}

  async route(): Promise<void> {}
}

function standInRequest(method: string, url: string, outcome: { failure?: string; status?: number }): Request {
  const { failure, status } = outcome;
  return {
    method: () => method,
    url: () => url,
    failure: () => (failure === undefined ? null : { errorText: failure }),
    existingResponse: () => (status === undefined ? null : { status: () => status }),
  } as unknown as Request;
}

async function standIn(optOut: HygieneCheck[] = []) {
  const page = new StandInPage();
  const mockApi = { unregistered: [], degradedUrls: new Set<string>(), unregisteredUrls: new Set<string>() };
  const hygiene = new Hygiene(mockApi as unknown as MockApi, "default-src 'self'", optOut);
  await hygiene.attach(page as unknown as Page);
  return {
    hygiene,
    failed: (url: string, failure: string, method = 'GET') => page.emit('requestfailed', standInRequest(method, url, { failure })),
    landed: (url: string, status = 200) => page.emit('requestfinished', standInRequest('GET', url, { status })),
  };
}

function failedRequest(message: string): HygieneViolation {
  return { check: 'request-failed', message };
}

test.describe('the asset-cancel rule (stand-in page)', () => {
  test('a Firefox or WebKit asset cancel whose URL then lands is forgiven, in every spelling', async () => {
    for (const failure of ENGINE_CANCELS) {
      const probe = await standIn();
      probe.failed(ASSET, failure);
      expect(probe.hygiene.violations(), `${failure}: held while the asset has not landed`).toEqual([failedRequest(`GET ${ASSET} failed: ${failure}`)]);
      probe.landed(ASSET);
      expect(probe.hygiene.violations(), `${failure}: forgiven once it lands`).toEqual([]);
      expect(probe.hygiene.forgivenAssetCancels(), `${failure}: reported for the annotation`).toEqual([`GET ${ASSET} failed: ${failure}`]);
    }
  });

  test('order does not matter, and a 304 revalidation lands the asset too', async () => {
    // CI run 42864141680: the 200 started before the cancelled request, and
    // Playwright delivered the cancel first; either order is one load.
    const earlier = await standIn();
    earlier.landed(ASSET);
    earlier.failed(ASSET, 'NS_BINDING_ABORTED');
    expect(earlier.hygiene.violations()).toEqual([]);

    const revalidated = await standIn();
    revalidated.failed(ASSET, 'Load request cancelled');
    revalidated.landed(ASSET, 304);
    expect(revalidated.hygiene.violations()).toEqual([]);
  });

  test('an asset cancel that never lands still fails, with its message unchanged', async () => {
    const never = await standIn();
    never.failed(ASSET, 'NS_BINDING_ABORTED');
    never.landed(`${ORIGIN}/assets/other-Ab12Cd34.webp`);
    never.landed(`${ASSET}?v=2`);
    expect(never.hygiene.violations(), 'another asset, or the same path with a query, does not land this one').toEqual([
      failedRequest(`GET ${ASSET} failed: NS_BINDING_ABORTED`),
    ]);
    expect(never.hygiene.forgivenAssetCancels()).toEqual([]);

    const notFound = await standIn();
    notFound.failed(ASSET, 'cancelled');
    notFound.landed(ASSET, 404);
    expect(notFound.hygiene.violations(), 'a 404 is not the asset landing').toEqual([failedRequest(`GET ${ASSET} failed: cancelled`)]);
  });

  test('an /api/ cancel, a POST and any other failure stay violations even when the URL lands', async () => {
    const api = `${ORIGIN}/api/v1/leads?limit=5`;
    const chunk = `${ORIGIN}/assets/lead-queue-CXGnHCye.js`;
    const probe = await standIn();
    probe.failed(api, 'NS_BINDING_ABORTED');
    probe.failed(chunk, 'Load request cancelled', 'POST');
    probe.failed(chunk, 'NS_ERROR_CONNECTION_REFUSED');
    probe.failed(chunk, 'cancelled by the test');
    probe.landed(api);
    probe.landed(chunk);
    expect(probe.hygiene.violations()).toEqual([
      failedRequest(`GET ${api} failed: NS_BINDING_ABORTED`),
      failedRequest(`POST ${chunk} failed: Load request cancelled`),
      failedRequest(`GET ${chunk} failed: NS_ERROR_CONNECTION_REFUSED`),
      failedRequest(`GET ${chunk} failed: cancelled by the test`),
    ]);
    expect(isEngineAssetCancel('GET', `${ORIGIN}/assetsx/a.js`, 'cancelled'), 'only the /assets/ directory').toBe(false);
  });

  test('Chromium ERR_ABORTED and cross-origin failures are still dropped, never held', async () => {
    const probe = await standIn();
    probe.failed(`${ORIGIN}/api/v1/leads`, 'net::ERR_ABORTED');
    probe.failed(ASSET, 'net::ERR_ABORTED');
    probe.failed('https://cdn.example.test/assets/x.webp', 'NS_BINDING_ABORTED');
    expect(probe.hygiene.collected()).toEqual([]);
    expect(probe.hygiene.forgivenAssetCancels()).toEqual([]);
  });

  test('allow() and an opt-out still apply to an asset cancel that never landed', async () => {
    const allowed = await standIn();
    allowed.failed(ASSET, 'NS_BINDING_ABORTED');
    allowed.hygiene.allow('request-failed', /entrada-wordmark-\w+\.webp failed: NS_BINDING_ABORTED$/);
    expect(allowed.hygiene.violations()).toEqual([]);
    expect(allowed.hygiene.collected(), 'collected keeps it for self-tests').toHaveLength(1);

    const optedOut = await standIn(['request-failed']);
    optedOut.failed(ASSET, 'cancelled');
    expect(optedOut.hygiene.violations()).toEqual([]);
  });
});

test.describe('the asset-cancel rule in this engine', () => {
  test('an aborted asset fetch is a violation until the same URL lands, then it is forgiven', async ({ app, browserName, hygiene, page }) => {
    await app.gotoRoute('/glossary');
    const src = await page.locator('#main-content footer img.entrada-wordmark-img').getAttribute('src');
    expect(src, 'precondition: the footer wordmark names a hashed asset').toMatch(/^\/assets\/entrada-wordmark-[\w-]+\.webp$/);
    // A query keeps the probe apart from the footer's own load of the image.
    const probe = new URL(`${src}?hygiene-probe=cancel`, page.url()).href;
    const requestFailed = () => hygiene.collected().filter((violation) => violation.check === 'request-failed');

    // Hold the first request at the route, so the abort below always lands
    // while it is in flight; later requests pass straight through.
    let release = () => {};
    const held = new Promise<void>((resolve) => { release = resolve; });
    let reachedRoute = () => {};
    const reached = new Promise<void>((resolve) => { reachedRoute = resolve; });
    let requests = 0;
    await page.route((url) => url.href === probe, async (route) => {
      requests += 1;
      if (requests === 1) {
        reachedRoute();
        await held;
      }
      // The held request was cancelled by then; continuing it may throw.
      await route.continue().catch(() => undefined);
    });

    const failed = page.waitForEvent('requestfailed', (request) => request.url() === probe);
    await page.evaluate((url) => {
      const controller = new AbortController();
      (window as unknown as Record<string, AbortController>).__hygieneProbe = controller;
      void fetch(url, { signal: controller.signal }).catch(() => undefined);
    }, probe);
    await reached;
    await page.evaluate(() => (window as unknown as Record<string, AbortController>).__hygieneProbe.abort());
    const failure = (await failed).failure()?.errorText ?? '';
    release();
    test.info().annotations.push({ type: 'engine cancel', description: `${browserName}: ${failure}` });

    const message = `GET ${probe} failed: ${failure}`;
    if (browserName === 'chromium') {
      expect(failure, 'Chromium names the cancel ERR_ABORTED').toBe('net::ERR_ABORTED');
      expect(requestFailed(), 'and the harness drops it, as before').toEqual([]);
    } else {
      expect(isEngineAssetCancel('GET', probe, failure), `the engine's own spelling is one the rule knows: ${failure}`).toBe(true);
      expect(requestFailed(), 'not landed yet: still a violation').toEqual([failedRequest(message)]);
    }

    const status = await page.evaluate(async (url) => {
      const response = await fetch(url);
      await response.arrayBuffer();
      return response.status;
    }, probe);
    expect(status, 'the same URL lands').toBe(200);
    await expect.poll(requestFailed, { message: 'landed: forgiven' }).toEqual([]);
    expect(hygiene.forgivenAssetCancels()).toEqual(browserName === 'chromium' ? [] : [message]);
  });
});
