/**
 * Lab interaction budget for the Lead Queue (2026-09-21 audit runtime-09
 * step 1, runtime-04 step 0): how long the 500-row queue takes to respond
 * to the three interactions the table's render cost decides.
 *
 *   (a) 20 J presses on the focused table: the cursor walk, which is also
 *       the virtual-window interaction (a wheel scroll carries no
 *       interactionId, so it cannot be measured this way).
 *   (b) a row expand by a click on the borrower button of the cursor row
 *       (row 20, rendered wherever the virtual window is).
 *   (c) 20 characters typed into the reject panel's Rationale textarea.
 *       Never submitted: the spec asserts zero reject POSTs.
 *
 * INERT unless MIP_PERF=1, like perf-budget.fixture.spec.ts (the
 * playwright.config.ts PERF_SPEC contract): run it on a quiet, single worker:
 *
 *   MIP_PERF=1 E2E_FIXTURE=1 CI=1 E2E_FIXTURE_WORKERS=1 npx playwright test interaction-budget
 *
 * Method: the CPU is throttled 4x over CDP (Emulation.setCPUThrottlingRate)
 * once the queue has rendered, so the load itself is not what is timed. A
 * buffered PerformanceObserver({ type: 'event', durationThreshold: 16 })
 * keeps every Event Timing entry with an interactionId; one interaction's
 * duration is the longest entry sharing its interactionId (keydown, keyup,
 * pointer and click entries of one gesture), and an interaction set's
 * number is its slowest interaction (INP's rule under 50 interactions). An
 * interaction with no entry at or above 16 ms reports "<16". Three samples,
 * each its own page; the log line carries the medians, numbers only (no
 * borrower id, no text):
 *
 *   [interaction-budget] j=… expand=… keystroke=… ms
 *
 * GATING MECHANISM (runtime-09): INTERACTION_CEILINGS_MS holds one ceiling
 * per interaction set, each null (report-only) until the integrator sets it.
 * The report test gates every non-null median (`<= ceiling`) and logs
 * `gating: ...` or `report-only`; the non-vacuity assertions always run (the
 * Event Timing observer is supported, and each interaction changed the DOM:
 * the cursor moved, the row expanded, the textarea holds the typed text).
 * Every run writes calibration/interaction-budget.json (medians, samples and
 * the CI run's sha / run id / attempt / runner, numbers only), which the
 * e2e-fixture job uploads as perf-calibration-<run id>-<attempt>.
 */
import type { Page } from '@playwright/test';
import { writeCalibration } from './calibration';
import { LEAD_QUEUE_500, registerLeadQueue, registerRejectRecorder } from './data/leadQueue';
import { expect, test } from './test';

const PERF_ENABLED = process.env.MIP_PERF === '1';
const SAMPLES = 3;
const PRESSES = 20;
const TYPED = 'governance hold note';
const EVENT_THRESHOLD_MS = 16;

type InteractionName = 'j' | 'expand' | 'keystroke';

/**
 * Median ceilings in ms, null = report-only. Set ONLY by the integrator, from
 * three reference-runner runs of one sha (tools/perf_ceilings.mjs: median of
 * the per-run medians x 1.2, rounded up to 10 ms), and ratcheted down, never
 * up. Record the runs here when a ceiling is set:
 *   runs: <run id / attempt>, <run id / attempt>, <run id / attempt>
 *   sha:  <sha>
 *   medians (j / expand / keystroke): <ms> / <ms> / <ms>
 */
const INTERACTION_CEILINGS_MS: Readonly<Record<InteractionName, number | null>> = {
  j: null,
  expand: null,
  keystroke: null,
};

interface EventProbe {
  supported: boolean;
  entries: Array<{ interactionId: number; duration: number; startTime: number }>;
}

declare global {
  interface Window {
    __mipEventProbe?: EventProbe;
  }
}

/** Per sample: the slowest interaction of each set, or null when none reached 16 ms. */
const samples: Array<Record<InteractionName, number | null>> = [];

async function observeEvents(page: Page): Promise<void> {
  await page.addInitScript((threshold) => {
    const supported = typeof PerformanceObserver !== 'undefined'
      && PerformanceObserver.supportedEntryTypes.includes('event');
    const probe: EventProbe = { supported, entries: [] };
    window.__mipEventProbe = probe;
    if (!supported) return;
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        const interactionId = (entry as PerformanceEntry & { interactionId?: number }).interactionId ?? 0;
        if (interactionId > 0) {
          probe.entries.push({ interactionId, duration: entry.duration, startTime: entry.startTime });
        }
      }
    }).observe({ type: 'event', durationThreshold: threshold, buffered: true } as PerformanceObserverInit);
  }, EVENT_THRESHOLD_MS);
}

async function throttleCpu(page: Page): Promise<void> {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
}

/** Let Event Timing deliver the entries of what just happened (after the next paints). */
async function flushEntries(page: Page): Promise<void> {
  await page.evaluate(() => new Promise<void>((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(resolve, 250)));
  }));
}

async function now(page: Page): Promise<number> {
  return page.evaluate(() => performance.now());
}

/** The slowest interaction that started at or after `since`, or null. */
async function slowestSince(page: Page, since: number): Promise<number | null> {
  return page.evaluate((from) => {
    const byInteraction = new Map<number, number>();
    for (const entry of window.__mipEventProbe?.entries ?? []) {
      if (entry.startTime < from) continue;
      byInteraction.set(entry.interactionId, Math.max(byInteraction.get(entry.interactionId) ?? 0, entry.duration));
    }
    return byInteraction.size === 0 ? null : Math.max(...byInteraction.values());
  }, since);
}

/** The middle sample; a sample under the 16 ms threshold counts as 0 ("<16"). */
function median(values: Array<number | null>): number {
  const sorted = values.map((value) => value ?? 0).sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)];
}

function format(value: number): string {
  return value < EVENT_THRESHOLD_MS ? `<${EVENT_THRESHOLD_MS}` : String(Math.round(value));
}

test.describe('lab interaction budget: the 500-row Lead Queue (runtime-09, report-only)', () => {
  test.skip(!PERF_ENABLED, 'Lab timings run only with MIP_PERF=1 on a quiet, single-worker run.');
  test.describe.configure({ mode: 'serial' });

  for (let run = 1; run <= SAMPLES; run += 1) {
    test(`sample ${run}: J walk, row expand, rationale keystrokes`, async ({ app, mockApi, page }, testInfo) => {
      test.slow();
      registerLeadQueue(mockApi);
      const rejects = registerRejectRecorder(mockApi);
      await observeEvents(page);
      await app.gotoRoute('/lead-queue', { timeoutMs: 45_000 });
      const supported = await page.evaluate(() => window.__mipEventProbe?.supported === true);
      expect(supported, 'the Event Timing observer is supported').toBe(true);
      const wrap = page.getByRole('region', { name: 'Ranked borrowers table scroll region' });
      await expect(page.locator('tr[data-borrower-row]').first()).toBeVisible();
      await throttleCpu(page);

      // (a) J x 20 from the focused region: the cursor lands on row 20.
      await wrap.focus();
      let since = await now(page);
      for (let press = 0; press < PRESSES; press += 1) await page.keyboard.press('j');
      const cursorRow = LEAD_QUEUE_500[PRESSES - 1].borrower_id;
      await expect(page.locator(`tr.is-cursor[data-borrower-row="${cursorRow}"]`), 'the J walk moved the cursor').toBeVisible();
      await flushEntries(page);
      const j = await slowestSince(page, since);

      // (b) Expand the cursor row with a click on its borrower button.
      const toggle = page.getByRole('button', { name: `Toggle preview for lead ${cursorRow}` });
      await toggle.scrollIntoViewIfNeeded();
      since = await now(page);
      await toggle.click();
      await expect(toggle, 'the row expanded').toHaveAttribute('aria-expanded', 'true');
      await flushEntries(page);
      const expand = await slowestSince(page, since);

      // (c) 20 characters into the reject panel's Rationale (never submitted),
      // opened from the pending row just above the cursor row.
      const target = LEAD_QUEUE_500[PRESSES - 2];
      expect(target.approval_status, 'precondition: a pending row to reject').toBe('pending');
      const reject = page.getByTestId(`lead-reject-${target.borrower_id}`);
      await reject.scrollIntoViewIfNeeded();
      await reject.click();
      const rationale = page.locator('form.decision-panel textarea');
      await rationale.focus();
      since = await now(page);
      for (const character of TYPED) await page.keyboard.type(character);
      await expect(rationale, 'the textarea holds the typed text').toHaveValue(TYPED);
      await flushEntries(page);
      const keystroke = await slowestSince(page, since);
      expect(rejects.bodies, 'typing never submits a reject').toHaveLength(0);

      const sample = { j, expand, keystroke };
      samples.push(sample);
      testInfo.annotations.push({ type: 'interaction-budget', description: JSON.stringify(sample) });
    });
  }

  test('report the medians and gate every metric with a ceiling', async ({}, testInfo) => {
    expect(samples, 'every sample ran').toHaveLength(SAMPLES);
    const result: Record<InteractionName, number> = {
      j: median(samples.map((sample) => sample.j)),
      expand: median(samples.map((sample) => sample.expand)),
      keystroke: median(samples.map((sample) => sample.keystroke)),
    };
    console.log(`[interaction-budget] j=${format(result.j)} expand=${format(result.expand)} keystroke=${format(result.keystroke)} ms`);
    writeCalibration(testInfo, 'interaction-budget', { spec: 'interaction-budget', medians: result, samples });
    const gated = (Object.keys(INTERACTION_CEILINGS_MS) as InteractionName[]).filter((name) => INTERACTION_CEILINGS_MS[name] !== null);
    console.log(`[interaction-budget] ${gated.length ? `gating: ${gated.map((name) => `${name}<=${INTERACTION_CEILINGS_MS[name]}`).join(' ')}` : 'report-only'}`);
    for (const name of gated) {
      expect.soft(result[name], `${name} median within its ${INTERACTION_CEILINGS_MS[name]} ms ceiling`).toBeLessThanOrEqual(INTERACTION_CEILINGS_MS[name] ?? 0);
    }
    await testInfo.attach('interaction-budget.json', {
      body: JSON.stringify({ medians: result, samples }, null, 2),
      contentType: 'application/json',
    });
  });
});
