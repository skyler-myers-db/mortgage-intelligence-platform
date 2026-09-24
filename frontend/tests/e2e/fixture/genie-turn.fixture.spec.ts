/**
 * Rendered-layer proofs for the wave-2 Genie turn lane (audit 2026-09-21
 * `runtime-01`, `genie-03`, `a11y-06`, `states-08`, `genie-v2`): ONE
 * in-flight turn per tab that survives leaving /ask-genie and a reload
 * without a second submit or a second complete, route Stop, and one
 * announcer per surface.
 *
 * The turn is scripted through data/genieTurn.ts (registerGenieTurn) plus
 * per-test register() overrides; the counters read the mock's own call log,
 * so they count what the browser really sent. 1440x900, production build.
 */
import AxeBuilder from '@axe-core/playwright';
import type { Page } from '@playwright/test';
import type { HealthPayload } from '../../../src/lib/apiTypes';
import { GENIE_QUESTION, registerGenieTurn } from './data/genieTurn';
import { HEALTH_OK } from './data/shell';
import { json } from './mockApi';
import { contrastRatio, renderedColors } from './renderedColor';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
/** The client's progress poll cadence (lib/genieAsk.ts PROGRESS_POLL_MS). */
const PROGRESS_POLL_MS = 1_500;
const IN_FLIGHT_KEY = 'mip.genie.inFlightTurn';
const ANSWER_TEXT = /leads the footprint with/;

function main(page: Page) {
  return page.locator('#main-content');
}

function composer(page: Page) {
  return main(page).getByRole('textbox', { name: 'Ask Genie — question' });
}

function thread(page: Page) {
  return main(page).locator('.genie-thread');
}

function routeRegion(page: Page) {
  return page.locator('[data-genie-announcer="route"]');
}

function panelRegion(page: Page) {
  return page.locator('[data-genie-announcer="panel"]');
}

async function askOnRoute(page: Page, question = GENIE_QUESTION): Promise<void> {
  await composer(page).fill(question);
  await main(page).getByRole('button', { name: 'Ask Genie', exact: true }).click();
  await expect(thread(page).locator('.genie-progress')).toBeVisible();
}

async function inFlightRecord(page: Page): Promise<string | null> {
  return page.evaluate((key) => window.sessionStorage.getItem(key), IN_FLIGHT_KEY);
}

function nav(page: Page, name: RegExp) {
  return page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name });
}

test.describe('one turn that survives the route', () => {
  test('(a) leaving /ask-genie mid-turn keeps polling, completes once, and the answer is there on return', async ({ app, page, mockApi }) => {
    const turn = registerGenieTurn(mockApi);
    await app.gotoRoute('/ask-genie');
    await askOnRoute(page);
    await expect.poll(() => turn.progressPolls).toBeGreaterThan(0);

    await nav(page, /^Leads/).click();
    await expect(page).toHaveURL(/\/lead-queue$/);
    await app.settle();
    const pollsAtLeave = turn.progressPolls;
    await expect.poll(() => turn.progressPolls, { timeout: 15_000 }).toBeGreaterThanOrEqual(pollsAtLeave + 2);
    expect(turn.submits).toBe(1);

    turn.finishGenieTurn();
    await expect.poll(() => turn.completes, { timeout: 15_000 }).toBe(1);

    await nav(page, /^Ask Genie/).click();
    await expect(page).toHaveURL(/\/ask-genie$/);
    await expect(thread(page).locator('.genie-answer')).toHaveCount(1);
    await expect(thread(page).locator('.genie__msg--user')).toHaveText([GENIE_QUESTION]);
    await expect(thread(page)).toContainText(ANSWER_TEXT);
    await expect(thread(page).locator('.genie-progress')).toHaveCount(0);
    expect(turn.submits).toBe(1);
    expect(turn.completes).toBe(1);
  });

  test('(b) a reload while polling resumes the same turn: no second submit, one complete, one answer', async ({ app, page, mockApi }) => {
    const turn = registerGenieTurn(mockApi);
    await app.gotoRoute('/ask-genie');
    await askOnRoute(page);
    await expect.poll(() => turn.progressPolls).toBeGreaterThan(0);
    expect(JSON.parse((await inFlightRecord(page)) ?? '{}')).toMatchObject({ v: 1, phase: 'polling' });

    await page.reload({ waitUntil: 'domcontentloaded' });
    await app.settle();
    const pollsAfterReload = turn.progressPolls;
    await expect(thread(page).locator('.genie__msg--user')).toHaveText([GENIE_QUESTION]);
    await expect(thread(page).locator('.genie-progress')).toBeVisible();
    await expect.poll(() => turn.progressPolls, { timeout: 15_000 }).toBeGreaterThan(pollsAfterReload);
    expect(turn.submits).toBe(1);

    turn.finishGenieTurn();
    await expect(thread(page).locator('.genie-answer')).toHaveCount(1);
    await expect(thread(page)).toContainText(ANSWER_TEXT);
    expect(turn.completes).toBe(1);
    expect(turn.submits).toBe(1);
    expect(await inFlightRecord(page)).toBeNull();
  });

  test('(c) a reload during the complete call never completes again: the interrupted note says check History', async ({ app, page, mockApi }) => {
    const turn = registerGenieTurn(mockApi, { holdComplete: true });
    await app.gotoRoute('/ask-genie');
    await askOnRoute(page);
    turn.finishGenieTurn();
    await expect.poll(() => turn.completes, { timeout: 15_000 }).toBe(1);
    expect(JSON.parse((await inFlightRecord(page)) ?? '{}')).toMatchObject({ phase: 'completing' });

    await page.reload({ waitUntil: 'domcontentloaded' });
    // The held complete belonged to the unloaded page; let its handler end.
    turn.releaseComplete();
    await app.settle();
    const note = thread(page).locator('.genie__msg--stopped');
    await expect(note).toContainText(
      'Interrupted by a reload while the answer was being verified. It may still be recorded: check History, or Ask again.',
    );
    await expect(thread(page).getByRole('button', { name: 'Ask again' })).toBeVisible();
    await expect(thread(page).getByRole('button', { name: 'Edit question' })).toBeVisible();

    const polls = turn.progressPolls;
    await page.clock.runFor(PROGRESS_POLL_MS * 4);
    await app.settle();
    expect(turn.progressPolls, 'no poll after the reload').toBe(polls);
    expect(turn.completes, 'the complete is never sent twice').toBe(1);
    expect(turn.submits).toBe(1);
    expect(await inFlightRecord(page)).toBeNull();
  });

  test('(d) an actor change mid-turn clears the record, stops polling, and a reload resumes nothing', async ({ app, page, mockApi }) => {
    let actor = 'fixture-actor-a';
    mockApi.register('GET', '/api/health', () => json<HealthPayload>({ ...HEALTH_OK, actor_cache_key: actor }));
    const turn = registerGenieTurn(mockApi);
    await app.gotoRoute('/ask-genie');
    await askOnRoute(page);
    await expect.poll(() => turn.progressPolls).toBeGreaterThan(0);
    expect(await inFlightRecord(page)).not.toBeNull();

    // The next health poll (8 s) reports a different actor: the shell clears
    // actor-scoped state, and the turn store fails closed on the reset event.
    actor = 'fixture-actor-b';
    const healthCalls = () => mockApi.calls.filter((call) => call.path === '/api/v1/health' || call.path === '/api/health').length;
    const healthBefore = healthCalls();
    await page.clock.runFor(8_000);
    await expect.poll(healthCalls, { timeout: 15_000 }).toBeGreaterThan(healthBefore);
    await expect.poll(() => inFlightRecord(page)).toBeNull();
    await expect(thread(page).locator('.genie-progress')).toHaveCount(0);
    await expect(main(page).locator('.genie__msg--user')).toHaveCount(0);

    const polls = turn.progressPolls;
    await page.clock.runFor(PROGRESS_POLL_MS * 4);
    await app.settle();
    expect(turn.progressPolls, 'no poll after the actor change').toBe(polls);
    expect(turn.completes).toBe(0);

    await page.reload({ waitUntil: 'domcontentloaded' });
    await app.settle();
    await page.clock.runFor(PROGRESS_POLL_MS * 4);
    await app.settle();
    expect(turn.progressPolls, 'a reload resumes nothing').toBe(polls);
    expect(turn.submits).toBe(1);
    expect(turn.completes).toBe(0);
    await expect(main(page).locator('.genie-progress')).toHaveCount(0);
  });
});

test.describe('route Stop', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`(e) ${theme}: Stop sits above the docked composer, restores and focuses it, stops polling, and the note clears AA`, async ({ app, page, mockApi }) => {
      const turn = registerGenieTurn(mockApi);
      await app.setTheme(theme);
      await app.gotoRoute('/ask-genie');
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      await askOnRoute(page);
      await expect.poll(() => turn.progressPolls).toBeGreaterThan(0);

      const stop = main(page).getByRole('button', { name: 'Stop this Genie turn' });
      await expect(stop).toBeVisible();
      await expect(stop).toHaveText('Stop');
      const stopBox = await stop.boundingBox();
      const dockBox = await main(page).locator('form.genie-composer').boundingBox();
      expect(stopBox && dockBox, 'Stop and the docked composer are laid out').toBeTruthy();
      expect(stopBox!.y + stopBox!.height, 'the Stop button sits fully above the docked composer').toBeLessThanOrEqual(dockBox!.y);

      await composer(page).fill('');
      await stop.click();
      await expect(composer(page)).toHaveValue(GENIE_QUESTION);
      await expect(composer(page)).toBeFocused();
      const note = thread(page).locator('.genie__msg--stopped');
      await expect(note).toContainText('Stopped');
      await expect(thread(page).getByRole('button', { name: 'Ask again' })).toBeVisible();
      await expect(thread(page).getByRole('button', { name: 'Edit question' })).toBeVisible();
      await expect(stop).toHaveCount(0);

      const polls = turn.progressPolls;
      await page.clock.runFor(PROGRESS_POLL_MS * 4);
      await app.settle();
      expect(turn.progressPolls, 'no poll after Stop').toBe(polls);
      expect(turn.completes).toBe(0);
      expect(await inFlightRecord(page)).toBeNull();

      const text = note.locator('.bubble > span').last();
      const colors = await renderedColors(text);
      const ratio = contrastRatio(colors.fg, colors.bg);
      test.info().annotations.push({ type: 'contrast', description: `stopped note in ${theme}: ${ratio.toFixed(2)}:1` });
      expect(ratio, `the stopped note text clears AA in ${theme}`).toBeGreaterThanOrEqual(4.5);
    });
  }
});

test.describe('one announcer per surface', () => {
  test('(f) with the panel open over /ask-genie only the panel speaks; closed, the route says "Answer ready" once', async ({ app, page, mockApi }) => {
    const turn = registerGenieTurn(mockApi, { holdComplete: true });
    await app.gotoRoute('/ask-genie');
    await askOnRoute(page);
    await expect(routeRegion(page)).toHaveText('Running the governed query');

    const dialog = await app.openGenie();
    await expect(panelRegion(page)).toHaveText('Running the governed query');
    await expect(routeRegion(page)).toHaveText('');
    await expect(dialog.locator('.genie__msg--user')).toHaveText(GENIE_QUESTION);
    await expect(dialog.getByRole('button', { name: 'Ask', exact: true })).toBeDisabled();

    await page.evaluate(() => {
      const win = window as Window & { __genieMutations?: { panel: number; route: string[] } };
      const record = { panel: 0, route: [] as string[] };
      win.__genieMutations = record;
      const panel = document.querySelector('[data-genie-announcer="panel"]')!;
      const route = document.querySelector('[data-genie-announcer="route"]')!;
      const options = { childList: true, subtree: true, characterData: true };
      new MutationObserver(() => {
        record.panel += 1;
      }).observe(panel, options);
      new MutationObserver(() => {
        record.route.push(route.textContent ?? '');
      }).observe(route, options);
    });
    const mutations = () =>
      page.evaluate(
        () => (window as Window & { __genieMutations?: { panel: number; route: string[] } }).__genieMutations!,
      );

    turn.finishGenieTurn();
    await expect(panelRegion(page)).toHaveText('Verifying the answer against its rows');
    await expect.poll(() => turn.completes, { timeout: 15_000 }).toBe(1);
    const whileOpen = await mutations();
    expect(whileOpen.panel, 'the open panel spoke the stage change').toBeGreaterThan(0);
    expect(whileOpen.route, 'the route stayed silent under the open panel').toEqual([]);

    await dialog.getByRole('button', { name: 'Close Genie' }).click();
    await expect(routeRegion(page)).toHaveText('Verifying the answer against its rows');
    turn.releaseComplete();
    await expect(thread(page).locator('.genie-answer')).toHaveCount(1);
    await expect(routeRegion(page)).toHaveText('Answer ready');
    await page.clock.runFor(PROGRESS_POLL_MS * 2);
    const spoken = (await mutations()).route.filter((text) => text === 'Answer ready');
    expect(spoken, '"Answer ready" was said once').toHaveLength(1);
    // Seen on the route's Ask tab: the launcher is not badged for it.
    await expect(app.genieToggle()).not.toHaveClass(/is-genie-ready/);
  });
});

test.describe('axe', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`(g) ${theme}: /ask-genie mid-turn and after Stop has no WCAG violations`, async ({ app, page, mockApi }) => {
      const turn = registerGenieTurn(mockApi);
      await app.setTheme(theme);
      await app.gotoRoute('/ask-genie');
      await askOnRoute(page);
      await expect.poll(() => turn.progressPolls).toBeGreaterThan(0);

      const midTurn = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
      expect(midTurn.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([]);

      await main(page).getByRole('button', { name: 'Stop this Genie turn' }).click();
      await expect(thread(page).locator('.genie__msg--stopped')).toBeVisible();
      const afterStop = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
      expect(afterStop.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([]);
    });
  }
});
