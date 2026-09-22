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

function oneLine(text: string, max = 400): string {
  const flat = text.replace(/\s+/g, ' ').trim();
  return flat.length > max ? `${flat.slice(0, max)}…` : flat;
}

export class Hygiene {
  /** Number of document responses the production CSP was attached to. */
  documentsWithCsp = 0;

  private readonly found: HygieneViolation[] = [];
  private readonly allowed: Array<{ check: HygieneCheck; pattern: RegExp }> = [];
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

  /** Everything collected so far, including opted-out checks (harness self-tests read this). */
  collected(): HygieneViolation[] {
    const unregistered: HygieneViolation[] = this.mockApi.unregistered.map((call) => ({
      check: 'unregistered-api',
      message:
        `${call.method} ${call.path}${call.search ? `?${call.search}` : ''} has no registered fixture. ` +
        'Add one under tests/e2e/fixture/data/ or call mockApi.register() in the test; ' +
        'use mockApi.degrade() if the test wants this endpoint to fail.',
    }));
    return [...unregistered, ...this.found];
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
    this.found.push({
      check: 'request-failed',
      message: oneLine(`${request.method()} ${request.url()} failed: ${failure}`),
    });
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
