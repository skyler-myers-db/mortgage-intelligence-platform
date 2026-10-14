/**
 * Rendered-layer proofs for the W5d Growth Agent watchlists lane (audit
 * 2026-09-21 `wow-ai-2`, `genie-09` part 4, `flow-04`), at 1440x900:
 *
 * 1. "Watch this level" on the Rate Lever (Home and Segment Intelligence):
 *    the control sits inside the legend's reserved lever slot in its button
 *    and its watching states; one POST whose body is the step, the cadence
 *    and an id only (no rate, no name); the server's level and the
 *    scheduler state shown; Home reads nothing more than the post-save
 *    summary refetch; a 409 is inline copy and a 429 is a shell toast with a
 *    countdown.
 * 2. A composed plan's per-step progress: no progress read before Run, about
 *    one a second carrying the posted request id while the held execute
 *    runs, Running / Done / Waiting then Recording, and none after settle.
 * 3. Trace audit ids open /audit-ledger for administrators and auditors and
 *    stay mono text otherwise.
 * 4. No state reads an audited endpoint; the new surfaces are axe-clean in
 *    both themes.
 */
import type { Locator, Page } from '@playwright/test';
import type { SessionResponse } from '../../../src/types';
import { expectAxeClean } from './axe';
import {
  EXECUTE_PATTERN,
  composeReply,
  executedReply,
  registerGrowthAgentTrust,
} from './data/growthAgentTrust';
import {
  PLAN_PROGRESS_PATTERN,
  PROGRESS_SEQUENCE,
  RATE_WATCH_PATTERN,
  registerRateWatch,
} from './data/growthWatchlists';
import { RATE_LEVER, rateLeverAt } from './data/rateLever';
import { SESSION } from './data/shell';
import { json, type MockApi } from './mockApi';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';
import { expectNoAuditedReadSince, markNaturalLoad } from './visual';

const SEGMENTS_CLEARED = '/segment-intelligence?marketing_eligibility=Any';
const AT_MINUS_50 = '?map_mode=rate&rate_step=-50';
/** The longest headline the fixture grid writes (geo-foundation's slider state). */
const AT_MINUS_100 = '?map_mode=rate&rate_step=-100';
/** geo-foundation's off-Linux widening of the lever's mono text (Linux Chromium draws it wider). */
const LINUX_TEXT_EMULATION = '.map-legend__lever .rate-lever__output, .map-legend__lever .evidence-chip { letter-spacing: 0.5px; }';
const LEVEL = RATE_LEVER.scenario_market_rate_pct[rateLeverAt(-50)];
const EXPECTED_STATUS = /status of (409 \(Conflict\)|429 \(Too Many Requests\)).*\/growth-agent\/rate-watch/;

const normalize = (path: string) => path.replace(/^\/api\/v\d+/, '/api');
const growthCalls = (mockApi: MockApi) =>
  mockApi.calls.filter((call) => /\/growth-agent\//.test(call.path)).map((call) => `${call.method} ${normalize(call.path)}`);

function watchButton(page: Page): Locator {
  return page.locator('.map-legend__lever').getByRole('button', { name: /^Watch \d/ });
}

async function slotFits(page: Page): Promise<{ slot: number; control: number }> {
  return page.locator('.map-legend__lever-slot').evaluate((slot) => ({
    slot: parseFloat(getComputedStyle(slot).minBlockSize),
    control: slot.firstElementChild?.getBoundingClientRect().height ?? 0,
  }));
}

async function setConsole(page: Page, open: boolean): Promise<void> {
  const body = page.getByRole('complementary', { name: 'Workspace console' }).locator('.tweaks__body');
  if ((await body.isVisible().catch(() => false)) !== open) {
    await page.getByRole('banner').getByRole('button', { name: 'Toggle console' }).click();
  }
  if (open) await expect(body).toBeVisible();
  else await expect(body).toBeHidden();
}

test.describe('Watch this level', () => {
  test('Home: one step-only POST, the server level and scheduler state, no extra read', async ({ app, page, mockApi }) => {
    const recorder = registerRateWatch(mockApi, ['saved']);
    await app.gotoRoute(`/${AT_MINUS_50}`);
    const button = watchButton(page);
    await expect(button).toHaveText(`Watch ${LEVEL.toFixed(2)}%`);
    await expect(button).toHaveAccessibleDescription(/drafts a review note when it is reached\. Nothing is sent\./);
    await app.settle();
    const naturalLoad = markNaturalLoad(mockApi);
    expect(growthCalls(mockApi)).toEqual(['GET /api/growth-agent/monitors/summary']);
    await button.hover();
    await button.focus();
    await app.settle();
    expect(growthCalls(mockApi), 'hover and focus read nothing').toEqual(['GET /api/growth-agent/monitors/summary']);

    await button.click();
    const watching = page.locator('.map-legend__lever .rate-lever__watching');
    await expect(watching).toHaveText(`Watching ${LEVEL.toFixed(2)}% · scheduled runs off · Latest print 6.12%`);
    await expect(watching).toBeFocused();
    await expect(page.locator('.map-legend__lever').getByRole('link', { name: 'Saved monitors' })).toHaveAttribute(
      'href',
      '/ask-genie?tab=monitors',
    );
    expect(recorder.bodies).toHaveLength(1);
    expect(Object.keys(recorder.bodies[0]).sort()).toEqual(['cadence', 'request_id', 'step_bps']);
    expect(recorder.bodies[0]).toMatchObject({ step_bps: -50, cadence: 'daily' });
    await app.settle();
    expect(growthCalls(mockApi)).toEqual([
      'GET /api/growth-agent/monitors/summary',
      `POST ${RATE_WATCH_PATTERN}`,
      'GET /api/growth-agent/monitors/summary',
    ]);
    expectNoAuditedReadSince(mockApi, naturalLoad, 'Watch this level');
  });

  for (const route of ['/', SEGMENTS_CLEARED]) {
    test(`${route}: the lever slot holds the control in its button and watching states`, async ({ app, page, mockApi }) => {
      registerRateWatch(mockApi, ['saved']);
      for (const consoleOpen of [false, true]) {
        await app.gotoRoute(`${route}${route.includes('?') ? `&${AT_MINUS_100.slice(1)}` : AT_MINUS_100}`);
        await setConsole(page, consoleOpen);
        if (process.platform !== 'linux') await page.addStyleTag({ content: LINUX_TEXT_EMULATION });
        await expect(watchButton(page)).toBeVisible();
        const button = await slotFits(page);
        await watchButton(page).click();
        await expect(page.locator('.map-legend__lever .rate-lever__watching')).toBeVisible();
        const watching = await slotFits(page);
        for (const [state, box] of [['button', button], ['watching', watching]] as const) {
          console.log(`[lever-slot] ${JSON.stringify({ route, console: consoleOpen, state: `watch-${state}`, ...box })}`);
          expect(box.slot, `${route} console ${consoleOpen} ${state}`).toBeGreaterThanOrEqual(box.control);
        }
      }
    });
  }

  test('a 409 is inline copy and a 429 is a toast with a countdown', async ({ app, page, mockApi, hygiene }) => {
    hygiene.allow('console.error', EXPECTED_STATUS);
    registerRateWatch(mockApi, ['grid-changed', 'throttled']);
    await app.gotoRoute(`${SEGMENTS_CLEARED}&${AT_MINUS_50.slice(1)}`);
    await watchButton(page).click();
    await expect(page.locator('.map-legend__lever [role="alert"]')).toHaveText(
      "This level can't be watched as shown. Move the slider and try again.",
    );
    await expect(page.locator('.map-legend__lever')).not.toContainText('grid changed');

    await watchButton(page).click();
    const toast = page.locator('.toast').filter({ hasText: "Couldn't save the watch yet" });
    await expect(toast).toBeVisible();
    await expect(toast.locator('.toast__wait')).toContainText('Try again in');
    await expect(toast.getByRole('button', { name: 'Retry' })).toHaveAttribute('aria-disabled', 'true');
    await expect(watchButton(page)).toHaveText(`Watch ${LEVEL.toFixed(2)}%`);
  });

  for (const theme of FIXTURE_THEMES) {
    test(`${theme}: the watch control is axe-clean in both of its states`, async ({ app, page, mockApi }) => {
      registerRateWatch(mockApi, ['saved']);
      await app.setTheme(theme);
      await app.gotoRoute(`/${AT_MINUS_50}`);
      await expect(watchButton(page)).toBeVisible();
      await expectAxeClean(page, { key: { route: 'home', state: 'rate-watch' }, theme, known: {}, include: '.map-legend__lever' });
      await watchButton(page).click();
      await expect(page.locator('.map-legend__lever .rate-lever__watching')).toBeVisible();
      await expectAxeClean(page, { key: { route: 'home', state: 'rate-watching' }, theme, known: {}, include: '.map-legend__lever' });
    });
  }
});

/**
 * Linux Chromium also draws the lever's sans lines wider (CI run
 * 38563619204's actuals: the cohort link ~397px against 390px here, the
 * meta line ~389px against 378px), enough to wrap the meta line in the
 * 384px lever; off Linux those lines are widened past that as well.
 */
const LEVER_LINE_EMULATION = `${LINUX_TEXT_EMULATION}
.map-legend__lever .rate-lever__meta, .map-legend__lever .rate-lever__actions > a { letter-spacing: 0.1px; }`;

/** The cohort link's box against the lever's, and the lines the meta line's book date paints on. */
async function leverLines(page: Page) {
  return page.locator('#main-content .map-legend__lever').evaluate((lever) => {
    const link = lever.querySelector<HTMLElement>('.rate-lever__actions > a');
    const metas = lever.querySelectorAll<HTMLElement>('.rate-lever__meta');
    const meta = metas[metas.length - 1];
    if (!link || !meta) throw new Error('lever lines not found');
    // A Range over the date's characters, whatever elements carry them.
    const text = meta.textContent ?? '';
    const date = /book as of (.+)$/.exec(text.trim())?.[1] ?? '';
    const start = text.lastIndexOf(date);
    const range = document.createRange();
    const walker = document.createTreeWalker(meta, NodeFilter.SHOW_TEXT);
    let offset = 0;
    for (let node = walker.nextNode() as Text | null; node; node = walker.nextNode() as Text | null) {
      const end = offset + node.data.length;
      if (start >= offset && start < end) range.setStart(node, start - offset);
      if (start + date.length <= end) {
        range.setEnd(node, start + date.length - offset);
        break;
      }
      offset = end;
    }
    const tops = new Set([...range.getClientRects()].filter((rect) => rect.width > 0).map((rect) => Math.round(rect.top)));
    const linkStyle = getComputedStyle(link);
    const linkText = link.clientHeight - parseFloat(linkStyle.paddingBlockStart) - parseFloat(linkStyle.paddingBlockEnd);
    return {
      date,
      leverRight: lever.getBoundingClientRect().right,
      linkRight: link.getBoundingClientRect().right,
      linkScroll: link.scrollWidth,
      linkClient: link.clientWidth,
      linkLines: Math.round(linkText / parseFloat(linkStyle.lineHeight)),
      metaLines: Math.round(meta.getBoundingClientRect().height / parseFloat(getComputedStyle(meta).lineHeight)),
      dateLines: tops.size,
    };
  });
}

// W5d VRT review (segment-intelligence--*--rate-watch-row / rate-watching):
// in the 384px lever (Segments, Console closed) the one-line cohort link ran
// past the lever's edge, its last glyph clipped, and the meta line split its
// date ('Jul / 14'). Both cohort labels (gains at -100, drops at +100), in
// every layout the lever has.
test.describe('lever lines', () => {
  for (const route of ['/', SEGMENTS_CLEARED]) {
    test(`${route}: the cohort link wraps inside the lever and the book date stays on one line`, async ({ app, page, mockApi }) => {
      registerRateWatch(mockApi, ['saved']);
      for (const consoleOpen of [false, true]) {
        for (const step of [-100, 100]) {
          await app.gotoRoute(`${route}${route.includes('?') ? '&' : '?'}map_mode=rate&rate_step=${step}`);
          await setConsole(page, consoleOpen);
          if (process.platform !== 'linux') await page.addStyleTag({ content: LEVER_LINE_EMULATION });
          await expect(page.locator('.map-legend__lever').getByRole('link', { name: /^Open / })).toBeVisible();
          const lines = await leverLines(page);
          const where = `${route} console ${consoleOpen} step ${step}`;
          console.log(`[lever-lines] ${JSON.stringify({ route, console: consoleOpen, step, ...lines })}`);
          expect(lines.date, `${where}: the meta line names the book date`).not.toBe('');
          expect(lines.linkScroll, `${where}: the cohort label fits its link`).toBeLessThanOrEqual(lines.linkClient);
          expect(lines.linkRight, `${where}: the cohort link ends inside the lever`).toBeLessThanOrEqual(lines.leverRight + 0.5);
          expect(lines.dateLines, `${where}: the book date is on one line`).toBe(1);
          if (route === SEGMENTS_CLEARED && !consoleOpen) {
            // The narrow lever is where both lines are at stake; elsewhere the checks above hold trivially.
            expect(lines.linkLines, `${where}: the cohort label wraps`).toBeGreaterThan(1);
            expect(lines.metaLines, `${where}: the meta line wraps`).toBeGreaterThan(1);
          }
        }
      }
    });
  }
});

const CARD = '[aria-label="Composed Growth Agent plan"]';

function gate() {
  let open: () => void = () => undefined;
  const opened = new Promise<void>((resolve) => {
    open = resolve;
  });
  return { opened, open: () => open() };
}

async function compose(page: Page) {
  await page
    .getByRole('region', { name: 'Mortgage Growth Agent command center' })
    .getByRole('button', { name: 'Compose plan', exact: true })
    .click();
  await expect(page.locator(CARD)).toBeVisible();
}

test.describe('composed-plan progress', () => {
  test('polls its own request about once a second only while the execute runs', async ({ app, page, mockApi }) => {
    registerGrowthAgentTrust(mockApi, { replies: [composeReply()], execute: 'ran' });
    const held = gate();
    const executeIds: string[] = [];
    mockApi.register('POST', EXECUTE_PATTERN, async (request) => {
      const body = request.body as { plan_digest: string; request_id: string };
      executeIds.push(body.request_id);
      await held.opened;
      return { body: executedReply(body.plan_digest) };
    });
    const polledIds: string[] = [];
    mockApi.register('GET', PLAN_PROGRESS_PATTERN, (request) => {
      polledIds.push(request.query.get('request_id') ?? '');
      return json(PROGRESS_SEQUENCE[Math.min(polledIds.length, PROGRESS_SEQUENCE.length) - 1]);
    });
    await app.gotoRoute('/ask-genie?tab=workflows');
    await compose(page);
    await page.waitForTimeout(2_500);
    expect(polledIds, 'no progress read before Run').toEqual([]);

    const started = Date.now();
    await page.locator(CARD).getByRole('button', { name: 'Run this plan' }).click();
    const steps = page.locator(`${CARD} .growth-agent-timeline`).first().locator('.growth-agent-step');
    await expect(steps.nth(0).locator('.growth-agent-step__progress')).toHaveText('Running');
    await expect(steps.nth(1).locator('.growth-agent-step__progress')).toHaveText('Running', { timeout: 15_000 });
    await expect(steps.nth(0).locator('.growth-agent-step__progress')).toHaveText('Done');
    await expect(steps.nth(2).locator('.growth-agent-step__progress')).toHaveText('Waiting');
    await expect(steps.nth(1)).toHaveAttribute('aria-current', 'step');
    await expect(steps.nth(2).locator('.growth-agent-step__progress')).toHaveText('Approval gate', { timeout: 15_000 });
    await expect(page.locator(`${CARD} .growth-agent-step__recording`)).toHaveText('Recording the audit trail…', {
      timeout: 15_000,
    });
    const elapsedS = (Date.now() - started) / 1000;
    expect(executeIds).toHaveLength(1);
    expect(new Set(polledIds)).toEqual(new Set(executeIds));
    expect(polledIds.length, 'about one read a second').toBeLessThanOrEqual(Math.ceil(elapsedS) + 2);

    held.open();
    await expect(page.locator(CARD).getByRole('region', { name: 'Execution trace' })).toBeFocused();
    const settled = polledIds.length;
    await page.waitForTimeout(3_000);
    expect(polledIds.length, 'no progress read after settle').toBe(settled);
    await expect(page.locator(`${CARD} .growth-agent-step__progress`)).toHaveCount(0);
  });

  for (const theme of FIXTURE_THEMES) {
    test(`${theme}: the running card is axe-clean`, async ({ app, page, mockApi }) => {
      registerGrowthAgentTrust(mockApi, { replies: [composeReply()], execute: 'ran' });
      const held = gate();
      mockApi.register('POST', EXECUTE_PATTERN, async (request) => {
        await held.opened;
        return { body: executedReply((request.body as { plan_digest: string }).plan_digest) };
      });
      mockApi.register('GET', PLAN_PROGRESS_PATTERN, () => json(PROGRESS_SEQUENCE[1]));
      await app.setTheme(theme);
      await app.gotoRoute('/ask-genie?tab=workflows');
      await compose(page);
      await page.locator(CARD).getByRole('button', { name: 'Run this plan' }).click();
      await expect(page.locator(`${CARD} .growth-agent-step--running`)).toBeVisible();
      await expectAxeClean(page, { key: { route: 'ask-genie', state: 'plan-progress' }, theme, known: {}, include: CARD });
      held.open();
      await expect(page.locator(CARD).getByRole('region', { name: 'Execution trace' })).toBeVisible();
    });
  }
});

test.describe('trace audit ids', () => {
  const AUDITOR: SessionResponse = { ...SESSION, can_access_admin: false, can_approve: false, can_read_audit: true };
  const PLAIN: SessionResponse = { ...SESSION, can_access_admin: false, can_approve: true, can_read_audit: false };
  for (const { who, session, linked } of [
    { who: 'admin', session: SESSION, linked: true },
    { who: 'auditor', session: AUDITOR, linked: true },
    { who: 'plain', session: PLAIN, linked: false },
  ]) {
    test(`${who}: ${linked ? 'links into the ledger' : 'mono text'}`, async ({ app, page, mockApi }) => {
      mockApi.register<SessionResponse>('GET', '/api/session', () => json(session));
      registerGrowthAgentTrust(mockApi, { replies: [composeReply()], execute: 'ran' });
      await app.gotoRoute('/ask-genie?tab=workflows');
      const naturalLoad = markNaturalLoad(mockApi);
      await compose(page);
      await page.locator(CARD).getByRole('button', { name: 'Run this plan' }).click();
      const trace = page.locator(CARD).getByRole('region', { name: 'Execution trace' });
      await expect(trace).toBeVisible();
      const meta = trace.locator('.growth-agent-step__meta').first();
      if (linked) {
        const link = meta.getByRole('link', { name: 'Open audit event fixture-audit-plan-step-1 in the audit ledger' });
        await expect(link).toHaveText('audit fixture-audi');
        await expect(link).toHaveAttribute('href', /^\/audit-ledger/);
        expect(await link.getAttribute('title')).toBeNull();
      } else {
        await expect(meta.locator('a')).toHaveCount(0);
        await expect(meta.locator('span.mono')).toHaveText('audit fixture-audi');
      }
      expectNoAuditedReadSince(mockApi, naturalLoad, `trace audit ids (${who})`);
    });
  }
});
