/**
 * Shared e2e hygiene: a fixture test FAILS on an uncaught page error, a
 * `console.error`, a Content-Security-Policy violation, a failed same-origin
 * request, or an API call that has no registered fixture.
 *
 * A test opts out by naming the check, never silently:
 *
 *   test.use({ hygieneOptOut: ['console.error'] });   // whole check
 *   hygiene.allow('console.error', /ResizeObserver/);  // one known message
 *
 * The production CSP is applied to every document response (see
 * productionCsp.ts) so the `csp` check exercises the real policy.
 *
 * A request the browser cancelled is not a failed request by itself.
 * Chromium's net::ERR_ABORTED is dropped for every request. A Firefox or
 * WebKit cancel of a same-origin /assets/ GET is forgiven only when the same
 * URL landed (2xx or 304, body received) in the same test, and each one
 * forgiven is annotated on the test; an asset that never landed, and every
 * other cancel (an /api/ read, a POST), still fails unless the test allows it.
 */
import type { ConsoleMessage, Page, Request } from '@playwright/test';
import type { MockApi } from './mockApi';

export const HYGIENE_CHECKS = [
  'pageerror',
  'console.error',
  'csp',
  'request-failed',
  'unregistered-api',
] as const;

export type HygieneCheck = (typeof HYGIENE_CHECKS)[number];

export interface HygieneViolation {
  check: HygieneCheck;
  message: string;
}

interface CspReport {
  directive: string;
  blocked: string;
  source: string;
  line: number;
}

const CSP_BINDING = '__mipFixtureCspViolation';

/**
 * Firefox's and WebKit's names for a request the page or the engine cancelled
 * itself. WebKit spells it per port: "cancelled" on macOS, "Load request
 * cancelled" on Linux (the e2e-cross-engine runner). Chromium's name,
 * net::ERR_ABORTED, is dropped for every request (onRequestFailed).
 */
const ENGINE_CANCEL = /^(?:NS_BINDING_ABORTED|cancelled|Load request cancelled)$/;

/**
 * A same-origin GET under /assets/ that Firefox or WebKit cancelled. The
 * harness holds it until the verdict and forgives it only when the same URL
 * landed in the same test (Hygiene.collected); an /api/ cancel, any other
 * method, and any other failure text are reported as they happen.
 */
export function isEngineAssetCancel(method: string, url: string, failure: string): boolean {
  if (method !== 'GET' || !ENGINE_CANCEL.test(failure)) return false;
  try {
    return new URL(url).pathname.startsWith('/assets/');
  } catch {
    return false;
  }
}

/** A response that delivered the asset: 2xx, or a 304 revalidation. */
function delivered(status: number): boolean {
  return (status >= 200 && status < 300) || status === 304;
}

function oneLine(text: string, max = 400): string {
  const flat = text.replace(/\s+/g, ' ').trim();
  return flat.length > max ? `${flat.slice(0, max)}…` : flat;
}

export class Hygiene {
  /** Number of document responses the production CSP was attached to. */
  documentsWithCsp = 0;

  private readonly found: HygieneViolation[] = [];
  private readonly allowed: Array<{ check: HygieneCheck; pattern: RegExp }> = [];
  /** Firefox / WebKit /assets/ cancels, resolved against `landed` at the verdict. */
  private readonly assetCancels: Array<{ url: string; message: string }> = [];
  /** Every URL this page received in full with a delivering status, in this test. */
  private readonly landed = new Set<string>();
  private attached = false;

  constructor(
    private readonly mockApi: MockApi,
    private readonly csp: string,
    private readonly optOut: readonly HygieneCheck[],
  ) {
    for (const check of optOut) {
      if (!HYGIENE_CHECKS.includes(check)) {
        throw new Error(`Unknown hygiene check "${check}". Known: ${HYGIENE_CHECKS.join(', ')}`);
      }
    }
  }

  get active(): boolean {
    return this.attached;
  }

  get policy(): string {
    return this.csp;
  }

  /** Allow one known message for one check. Prefer this over a whole-check opt-out. */
  allow(check: HygieneCheck, pattern: RegExp): void {
    this.allowed.push({ check, pattern });
  }

  /**
   * Everything collected so far, including opted-out checks (harness self-tests read this).
   *
   * A Firefox / WebKit asset cancel (isEngineAssetCancel) is in it only while
   * its URL has not landed. Order does not matter. React 19 re-assigns every
   * mounted <img>'s src at commit (react-dom commitMount, to replay onLoad);
   * Firefox can answer that with a second request for the image it already
   * holds and cancel it unsent. The footer wordmark did (CI run 42864141680,
   * both attempts): the 200 started 33-41 ms BEFORE the cancelled request,
   * yet Playwright delivered the cancel first. So "landed" means anywhere in
   * the test, before or after the cancel.
   */
  collected(): HygieneViolation[] {
    const unregistered: HygieneViolation[] = this.mockApi.unregistered.map((call) => ({
      check: 'unregistered-api',
      message:
        `${call.method} ${call.path}${call.search ? `?${call.search}` : ''} has no registered fixture. ` +
        'Add one under tests/e2e/fixture/data/ or call mockApi.register() in the test; ' +
        'use mockApi.degrade() if the test wants this endpoint to fail.',
    }));
    const unlanded: HygieneViolation[] = this.assetCancels
      .filter((cancel) => !this.landed.has(cancel.url))
      .map((cancel) => ({ check: 'request-failed', message: cancel.message }));
    return [...unregistered, ...this.found, ...unlanded];
  }

  /** The asset cancels forgiven because their URL landed; test.ts annotates each one. */
  forgivenAssetCancels(): string[] {
    return this.assetCancels.filter((cancel) => this.landed.has(cancel.url)).map((cancel) => cancel.message);
  }

  /** Violations that fail the test: collected minus opt-outs and allowed messages. */
  violations(): HygieneViolation[] {
    return this.collected().filter(
      (violation) =>
        !this.optOut.includes(violation.check) &&
        !this.allowed.some((rule) => rule.check === violation.check && rule.pattern.test(violation.message)),
    );
  }

  async attach(page: Page): Promise<void> {
    page.on('pageerror', (error) => {
      this.found.push({ check: 'pageerror', message: oneLine(error.stack ?? error.message) });
    });
    page.on('console', (message) => this.onConsole(message));
    page.on('requestfailed', (request) => this.onRequestFailed(page, request));
    page.on('requestfinished', (request) => {
      const status = request.existingResponse()?.status();
      if (status !== undefined && delivered(status)) this.landed.add(request.url());
    });

    await page.exposeFunction(CSP_BINDING, (report: CspReport) => {
      this.found.push({
        check: 'csp',
        message: oneLine(
          `${report.directive} blocked ${report.blocked || '(inline)'} at ${report.source || 'document'}:${report.line}`,
        ),
      });
    });
    await page.addInitScript((binding) => {
      document.addEventListener('securitypolicyviolation', (event) => {
        const report = (window as unknown as Record<string, ((payload: unknown) => void) | undefined>)[binding];
        report?.({
          directive: event.effectiveDirective,
          blocked: event.blockedURI,
          source: event.sourceFile,
          line: event.lineNumber,
        });
      });
    }, CSP_BINDING);

    // Attach the production CSP to app documents. Assets and API calls are
    // left alone; the API is answered by the MockApi route.
    await page.route(
      (url) => !url.pathname.startsWith('/api/') && !url.pathname.startsWith('/assets/'),
      async (route) => {
        const request = route.request();
        if (request.resourceType() !== 'document') {
          await route.fallback();
          return;
        }
        try {
          const response = await route.fetch();
          await route.fulfill({
            response,
            headers: { ...response.headers(), 'content-security-policy': this.csp },
          });
          this.documentsWithCsp += 1;
        } catch {
          // The page closed or navigated away mid-request; nothing to serve.
        }
      },
    );
    this.attached = true;
  }

  private onConsole(message: ConsoleMessage): void {
    if (message.type() !== 'error') return;
    const text = message.text();
    // Chromium mirrors CSP blocks to the console; the DOM event already
    // reports them under the `csp` check.
    if (/Content Security Policy/i.test(text)) return;
    const url = message.location().url;
    if (/Failed to load resource/i.test(text) && url) {
      // The browser logs every non-2xx fetch. An explicit degrade() is the
      // test's own choice, and an unregistered call is reported (with its
      // method and path) by the `unregistered-api` check instead.
      if (this.mockApi.degradedUrls.has(url) || this.mockApi.unregisteredUrls.has(url)) return;
    }
    this.found.push({ check: 'console.error', message: oneLine(url ? `${text} (${url})` : text) });
  }

  private onRequestFailed(page: Page, request: Request): void {
    const failure = request.failure()?.errorText ?? 'unknown failure';
    // Navigation and unmount cancel in-flight requests; CSP blocks are
    // reported by the `csp` check.
    if (/ERR_ABORTED|BLOCKED_BY_CSP|csp/i.test(failure)) return;
    try {
      if (new URL(request.url()).origin !== new URL(page.url()).origin) return;
    } catch {
      return;
    }
    const message = oneLine(`${request.method()} ${request.url()} failed: ${failure}`);
    // Firefox and WebKit report their cancels by other names, so they are
    // not dropped like Chromium's: an asset one is held for the verdict
    // (collected), every other one is a violation now, as before.
    if (isEngineAssetCancel(request.method(), request.url(), failure)) {
      this.assetCancels.push({ url: request.url(), message });
      return;
    }
    this.found.push({ check: 'request-failed', message });
  }
}

export function formatViolations(testTitle: string, violations: HygieneViolation[]): string {
  const lines = violations.map((violation) => `  [${violation.check}] ${violation.message}`);
  return [
    `Fixture hygiene failed for "${testTitle}" (${violations.length} violation${violations.length === 1 ? '' : 's'}):`,
    ...lines,
    'Opt out by name only when the noise is understood: test.use({ hygieneOptOut: [...] }) or hygiene.allow(check, /pattern/).',
  ].join('\n');
}
