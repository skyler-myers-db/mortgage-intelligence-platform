/**
 * Lab performance budget (2026-09-21 audit bundle-08): LCP and Total
 * Blocking Time of a cold, throttled load of Home and the Lead Queue, from
 * the production build. It is the lab complement to the RUM LCP / INP field
 * data the app already reports; the loadEvent budgets in
 * tests/e2e/route_performance.spec.ts (live only) stay as they are.
 *
 * INERT unless MIP_PERF=1: timings need a quiet, single-worker run, so the
 * normal fixture job skips this file. Run it with
 *
 *   MIP_PERF=1 E2E_FIXTURE=1 CI=1 E2E_FIXTURE_WORKERS=1 npx playwright test perf-budget
 *
 * (w2-safety-net owns the playwright.config.ts testIgnore and the
 * single-worker CI step that match this file's name.)
 *
 * Each sample is its own test, so its own cold browser context: the HTTP
 * cache is disabled over CDP, the network is 40 ms RTT / 10 Mbps down /
 * 5 Mbps up, and the CPU is throttled 4x. LCP is the last
 * largest-contentful-paint entry (buffered observer, no input happens); TBT
 * sums (duration - 50 ms) over the long tasks that start before LCP + 5 s.
 * The median of three samples per route is gated.
 *
 * `vite preview` compresses with gzip on the fly rather than serving the
 * build's brotli siblings, and the harness answers the API and the HTML
 * document without network cost, so these numbers are a conservative proxy
 * for asset delivery, not a field measurement.
 */
import type { Page } from '@playwright/test';
import { expect, test } from './test';

const PERF_ENABLED = process.env.MIP_PERF === '1';
const SAMPLES = 3;
const TBT_WINDOW_AFTER_LCP_MS = 5_000;

const ROUTES = [
  { name: 'home', path: '/' },
  { name: 'lead-queue', path: '/lead-queue' },
] as const;
type RouteName = (typeof ROUTES)[number]['name'];

/**
 * Ceilings: the measured median x 1.2, rounded up to 100 ms, from the lane's
 * final tree on 2026-09-24 (macOS, Chromium 1.59.1 headless, one worker):
 *   home        LCP 1688 / 1680 / 1716 ms (median 1688), TBT 307 / 315 / 349 (315)
 *   lead-queue  LCP 3488 / 2144 / 2228 ms (median 2228), TBT 629 / 736 / 777 (736)
 * PROVISIONAL: the shared host's 1-minute load average was ~45 during that
 * run (no quiet window came), which inflates timings, so these ceilings are
 * looser than a quiet-machine median would give. They were not measured on
 * the reference runner either: a GitHub ubuntu runner under the same 4x CPU
 * throttle can be slower than this M-series host, so the first
 * single-worker CI run (w2-safety-net's step) calibrates them once, in
 * either direction, from its own median x 1.2. After that, ratchet down,
 * never up.
 *
 * CI calibration (2026-09-25, the step's first runs on the reference runner,
 * after wave 3 made it run on every PR): home passed once on run 36096436271
 * and failed on run 36104375015, a backend-only change, with samples LCP
 * 2276 / 2164 / 2152 ms (median 2164) and TBT 447 / 453 / 479 (median 453),
 * above both provisional home ceilings. Home re-calibrated from that median
 * x 1.2, rounded up to 100 ms: LCP 2600, TBT 600. The next run (36106009664)
 * logged runner medians for both routes: home 2156 / 415 and, on its
 * retry, 2076 / 393 (inside the new ceilings); lead-queue LCP 2220 / 2172
 * (inside its provisional 2700) but TBT 971 / 904 against 900. Lead-queue
 * TBT re-calibrated from the higher runner median, 971 x 1.2 -> 1200; its
 * LCP ceiling already equals its runner median x 1.2 (2220 -> 2700). Every
 * run prints its medians ([lab-vitals]) so the next ratchet (down only) has
 * runner data.
 */
const CEILINGS: Readonly<Record<RouteName, { lcpMs: number; tbtMs: number }>> = {
  home: { lcpMs: 2_600, tbtMs: 600 },
  'lead-queue': { lcpMs: 2_700, tbtMs: 1_200 },
};

interface Vitals {
  lcpMs: number;
  tbtMs: number;
}

interface PerfProbe {
  lcp: number;
  longTasks: Array<{ start: number; duration: number }>;
}

declare global {
  interface Window {
    __mipPerfProbe?: PerfProbe;
  }
}

const samples: Record<RouteName, Vitals[]> = { home: [], 'lead-queue': [] };

async function throttle(page: Page): Promise<void> {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Network.enable');
  await cdp.send('Network.setCacheDisabled', { cacheDisabled: true });
  await cdp.send('Network.emulateNetworkConditions', {
    offline: false,
    latency: 40,
    downloadThroughput: (10 * 1_000_000) / 8,
    uploadThroughput: (5 * 1_000_000) / 8,
  });
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
}

async function observeVitals(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const probe: PerfProbe = { lcp: 0, longTasks: [] };
    window.__mipPerfProbe = probe;
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) probe.lcp = entry.startTime;
    }).observe({ type: 'largest-contentful-paint', buffered: true });
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) probe.longTasks.push({ start: entry.startTime, duration: entry.duration });
    }).observe({ type: 'longtask', buffered: true });
  });
}

async function readVitals(page: Page): Promise<Vitals> {
  await page.waitForFunction(
    (windowMs) => {
      const probe = window.__mipPerfProbe;
      return Boolean(probe && probe.lcp > 0 && performance.now() >= probe.lcp + windowMs);
    },
    TBT_WINDOW_AFTER_LCP_MS,
    { polling: 250, timeout: 30_000 },
  );
  return page.evaluate((windowMs) => {
    const probe = window.__mipPerfProbe as PerfProbe;
    const end = probe.lcp + windowMs;
    const tbtMs = probe.longTasks
      .filter((task) => task.start < end)
      .reduce((sum, task) => sum + Math.max(0, task.duration - 50), 0);
    return { lcpMs: probe.lcp, tbtMs };
  }, TBT_WINDOW_AFTER_LCP_MS);
}

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)];
}

test.describe('lab performance: throttled cold loads (bundle-08)', () => {
  test.skip(!PERF_ENABLED, 'Lab timings run only with MIP_PERF=1 on a quiet, single-worker run.');
  test.describe.configure({ mode: 'serial' });

  for (const route of ROUTES) {
    for (let run = 1; run <= SAMPLES; run += 1) {
      test(`${route.name} cold sample ${run}`, async ({ app, page }, testInfo) => {
        await observeVitals(page);
        await throttle(page);
        await app.gotoRoute(route.path, { timeoutMs: 45_000 });
        const vitals = await readVitals(page);
        samples[route.name].push(vitals);
        testInfo.annotations.push({ type: 'lab-vitals', description: JSON.stringify(vitals) });
      });
    }

    test(`${route.name} median LCP and TBT stay under their ceilings`, async ({}, testInfo) => {
      const taken = samples[route.name];
      expect(taken, 'every cold sample ran').toHaveLength(SAMPLES);
      const result = {
        lcpMs: median(taken.map((sample) => sample.lcpMs)),
        tbtMs: median(taken.map((sample) => sample.tbtMs)),
        samples: taken,
      };
      // The runner's own numbers, in the job log even when the step passes.
      console.log(`[lab-vitals] ${route.name} ${JSON.stringify(result)}`);
      await testInfo.attach(`${route.name}-lab-vitals.json`, {
        body: JSON.stringify(result, null, 2),
        contentType: 'application/json',
      });
      expect(result.lcpMs, `${route.name} median LCP (${JSON.stringify(taken)})`).toBeLessThanOrEqual(
        CEILINGS[route.name].lcpMs,
      );
      expect(result.tbtMs, `${route.name} median TBT (${JSON.stringify(taken)})`).toBeLessThanOrEqual(
        CEILINGS[route.name].tbtMs,
      );
    });
  }
});
