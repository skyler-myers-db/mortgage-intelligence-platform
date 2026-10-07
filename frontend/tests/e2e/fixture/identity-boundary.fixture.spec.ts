/**
 * The identity boundary across tabs (D-identity-review-b; audit genie-02 item
 * 1, bundle-04 item 4) and the actor-scoped persisted aggregate cache (audit
 * delivery-05), proven in the built app at 1440x900 on one browser context.
 *
 * Tabs of one context share localStorage and each has its own
 * sessionStorage. A new tab therefore found the previous actor's pins and
 * Genie conversation id in localStorage with nothing to say whose they were.
 * The actor gate stamps each area with its owner ('mip.actorOwner') and
 * compares the stamp with the first trusted observation, here the
 * /api/session seed and /api/health, both served from ONE shared actor
 * variable (data/shell.ts serveActor).
 *
 * A Genie surface that mounts while both observations are still in flight
 * reads the stored conversation id as null (the gate is pending); it re-reads
 * the id when the gate opens, so the owner's next question continues it.
 *
 * Page 1's own reaction to the flip (D-identity-review-a3, W5b): a proven
 * mid-session change resets the document to '/' with a one-time notice, and
 * no audited read leaves under the new cookie; another tab restamping the
 * shared stamp suspends page 1 at once (its pin hides) until a trusted probe
 * proves the change.
 */
import type { Page } from '@playwright/test';
import type { HealthPayload } from '../../../src/lib/apiTypes';
import type { GenieStartResult, HomeSummary, PortfolioPreview, SessionResponse } from '../../../src/types';
import { AppDriver } from './app';
import { expectAxeClean, KNOWN_VIOLATIONS } from './axe';
import { GENIE_QUESTION, registerGenieTurn } from './data/genieTurn';
import { CONTACTABLE_PORTFOLIO_PREVIEW, HOME_SUMMARY, PORTFOLIO_PREVIEW } from './data/portfolio';
import { FIXTURE_ACTOR_A, FIXTURE_ACTOR_B, HEALTH_OK, SESSION, serveActor } from './data/shell';
import { json, type ApiCall, type FixtureRequest, type MockApi } from './mockApi';
import { seedOwnedStorage } from './ownedStorage';
import { expect, test } from './test';

const PINS_KEY = 'mip.pinnedInsights';
const CONVERSATION_KEY = 'mip.genie.conversationId';
const QUERY_CACHE_KEY = 'mip.queryCache.v1';
const A_CONVERSATION = 'fixture-conversation-of-a';
const A_PIN = {
  id: 'pin-of-a',
  question: 'Which states lead the refi screen?',
  summary: 'Illinois leads the refi screen.',
  source: 'mip.gold.geo_state_rollup',
  pinnedAt: '2026-07-14T14:00:00Z',
};
/** Reads that write VIEW_* / DRAFT_* audit rows. */
const AUDITED_READ = /^\/api\/(leads(\/|$)|borrowers\/|offers\/recommend|outreach\/draft)/;

const pinsCard = (page: Page) => page.locator('#main-content section.pinned-insights');
const kpiValues = (page: Page) => page.locator('#main-content .kpi-row .kpi__value');
const kpiSkeletons = (page: Page) => page.locator('#main-content .kpi-row .kpi.is-loading');

/** A's pins and Genie conversation id, owned by A, in page 1's storage. */
async function seedActorA(page: Page): Promise<void> {
  await seedOwnedStorage(page, FIXTURE_ACTOR_A, {
    local: { [PINS_KEY]: JSON.stringify([A_PIN]), [CONVERSATION_KEY]: A_CONVERSATION },
  });
}

/** Genie with no bootstrap conversation, recording each submit's conversation_id. */
function recordSubmits(mockApi: MockApi): Array<string | null> {
  mockApi.register('POST', '/api/genie/start', () => json<GenieStartResult>({ conversation_id: null, sample_questions: [] }));
  registerGenieTurn(mockApi);
  const seen: Array<string | null> = [];
  const answerSubmit = (request: FixtureRequest) => {
    seen.push((request.body as { conversation_id?: string | null } | null)?.conversation_id ?? null);
    return json({
      completed: false,
      conversation_id: 'fixture-conversation-0001',
      message_id: 'fixture-message-0001',
      progress_token: 'fixture-progress-token',
      question_hash: null,
      deep: false,
      response: null,
    });
  };
  mockApi.register('POST', '/api/genie/message/submit', answerSubmit);
  return seen;
}

/** A new tab in the same context, served by the same mock API. */
async function newTab(page: Page, mockApi: MockApi): Promise<{ page: Page; app: AppDriver }> {
  const tab = await page.context().newPage();
  await mockApi.install(tab);
  return { page: tab, app: new AppDriver(tab, mockApi) };
}

test.describe('a new tab after an actor change (D-identity-review-b)', () => {
  test("B's new tab shows none of A's pins, and its first Genie submit carries no conversation id", async ({ app, page, mockApi }) => {
    let actor = FIXTURE_ACTOR_A;
    serveActor(mockApi, () => actor);
    const submits = recordSubmits(mockApi);
    await seedActorA(page);
    await app.setTheme('dark');
    await app.gotoRoute('/');
    await expect(pinsCard(page)).toContainText(A_PIN.question);

    actor = FIXTURE_ACTOR_B;
    const tab = await newTab(page, mockApi);
    await tab.app.setTheme('dark');
    await tab.app.gotoRoute('/');
    await expect(pinsCard(tab.page)).toHaveCount(0);
    expect(await tab.page.evaluate((key) => window.localStorage.getItem(key), PINS_KEY), "A's pins were removed").toBeNull();
    expect(await tab.page.evaluate((key) => window.localStorage.getItem(key), CONVERSATION_KEY)).toBeNull();
    await expectAxeClean(tab.page, { key: { route: 'home', state: 'default' }, theme: 'dark', known: KNOWN_VIOLATIONS });

    await tab.app.askGenie(GENIE_QUESTION);
    await expect.poll(() => submits.length).toBe(1);
    expect(submits[0], "B's first turn starts a new conversation").toBeNull();
    await tab.page.close();
  });

  test("the same actor's new tab keeps the pin and continues A's conversation", async ({ app, page, mockApi }) => {
    serveActor(mockApi, () => FIXTURE_ACTOR_A);
    const submits = recordSubmits(mockApi);
    await seedActorA(page);
    await app.gotoRoute('/');
    await expect(pinsCard(page)).toContainText(A_PIN.question);

    const tab = await newTab(page, mockApi);
    await tab.app.gotoRoute('/');
    await expect(pinsCard(tab.page)).toContainText(A_PIN.question);
    await tab.app.askGenie(GENIE_QUESTION);
    await expect.poll(() => submits.length).toBe(1);
    expect(submits[0]).toBe(A_CONVERSATION);
    await tab.page.close();
  });
});

const RESET_NOTICE = 'The signed-in user changed, so this tab was reset. Nothing from the previous session carries over.';
const DRAFT_KEY = 'mip.portfolio.campaignDraft.v1';
/** The audit-free reads every new document primes at boot. */
const BOOT_PRIMES = [/^\/api(\/v1)?\/session$/, /^\/api(\/v1)?\/config\/options$/, /^\/api(\/v1)?\/config\/footprint$/, /^\/api(\/v1)?\/health$/];
const HEALTH_CALL = /^\/api(\/v1)?\/health$/;

const toastRegion = (page: Page) => page.locator('section.toast-region[aria-label="Notifications"]');
const pathname = (page: Page) => new URL(page.url()).pathname;

/** Flip the served actor to B and run page 1's clock past its 8 s poll until the reset lands on '/'. */
async function flipAndReset(page: Page, app: AppDriver, mockApi: MockApi, flip: () => void): Promise<number> {
  const from = mockApi.calls.length;
  flip();
  const healthBefore = mockApi.calls.filter((call) => HEALTH_CALL.test(call.path)).length;
  await page.clock.runFor(8_000);
  await expect.poll(() => mockApi.calls.filter((call) => HEALTH_CALL.test(call.path)).length, { timeout: 15_000 }).toBeGreaterThan(healthBefore);
  await page.waitForURL((url) => url.pathname === '/', { timeout: 15_000 });
  await app.settle();
  return from;
}

test.describe('a proven mid-session actor change resets the document (D-identity-review-a3)', () => {
  for (const theme of ['dark', 'light'] as const) {
    test(`(1a) ${theme}: /lead-queue as A, then B: the tab resets to '/' with the notice, and no audited read leaves after the flip`, async ({ app, page, mockApi }) => {
      let actor = FIXTURE_ACTOR_A;
      serveActor(mockApi, () => actor);
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue');
      await expect(page.locator('tr[data-borrower-row]').first()).toBeVisible();

      const from = await flipAndReset(page, app, mockApi, () => {
        actor = FIXTURE_ACTOR_B;
      });
      expect(pathname(page)).toBe('/');
      const after = mockApi.calls.slice(from).map((call) => call.path);
      expect(after.filter((path) => AUDITED_READ.test(path)), 'no audited read after the flip').toEqual([]);
      for (const prime of BOOT_PRIMES) {
        expect(after.some((path) => prime.test(path)), `the new document primed ${prime}`).toBe(true);
      }
      await expect(toastRegion(page)).toContainText(RESET_NOTICE);
      await expectAxeClean(page, { key: { route: 'home', state: 'default' }, theme, known: KNOWN_VIOLATIONS });
    });
  }

  test('(1b) /portfolio-builder with unsaved work as A, then B: no "Leave site?" dialog, the tab resets to "/" with the notice and the draft is gone', async ({ app, page, mockApi }) => {
    let actor = FIXTURE_ACTOR_A;
    serveActor(mockApi, () => actor);
    const dialogs: string[] = [];
    page.on('dialog', (dialog) => {
      dialogs.push(dialog.type());
      void dialog.accept();
    });
    await app.gotoRoute('/portfolio-builder');
    // A typed budget (feedback-guard's dirtyPortfolio pattern): a real click
    // first, so Chromium would show a beforeunload prompt for this page.
    const budget = page.getByRole('spinbutton', { name: 'Budget', exact: true });
    await budget.click();
    await budget.fill('25000');
    await budget.blur();
    await expect(budget).toHaveValue('25000');
    const beforeFlip = await page.evaluate((key) => window.sessionStorage.getItem(key), DRAFT_KEY);
    expect(beforeFlip, "A's per-tab campaign draft exists before the flip (non-vacuity)").not.toBeNull();

    await flipAndReset(page, app, mockApi, () => {
      actor = FIXTURE_ACTOR_B;
    });
    expect(dialogs, 'no page dialog (the unsaved work was dropped first)').toEqual([]);
    expect(pathname(page)).toBe('/');
    await expect(toastRegion(page)).toContainText(RESET_NOTICE);
    expect(await page.evaluate((key) => window.sessionStorage.getItem(key), DRAFT_KEY), "A's draft is gone").toBeNull();
  });

  test("(2) another tab signs in as B: page 1's pin hides at once, then page 1 resets to '/' with the notice", async ({ app, page, mockApi }) => {
    let actor = FIXTURE_ACTOR_A;
    serveActor(mockApi, () => actor);
    await seedActorA(page);
    await app.gotoRoute('/');
    await expect(pinsCard(page)).toContainText(A_PIN.question);
    const resets: string[] = [];
    page.on('framenavigated', (frame) => {
      if (frame === page.mainFrame()) resets.push(new URL(frame.url()).pathname);
    });

    actor = FIXTURE_ACTOR_B;
    const tab = await newTab(page, mockApi);
    await tab.app.gotoRoute('/');
    await expect(pinsCard(tab.page)).toHaveCount(0);

    // The storage event: page 1 hides the pin with no probe needed...
    await expect(pinsCard(page)).toHaveCount(0);
    // ...then its recheck probe proves B: the document is replaced at '/'.
    await expect.poll(() => resets, { timeout: 15_000 }).toContain('/');
    await app.settle();
    await expect(toastRegion(page)).toContainText(RESET_NOTICE);
    await expect(pinsCard(page)).toHaveCount(0);
    await tab.page.close();
  });
});

test.describe('a Genie surface mounted while the actor gate is pending (D-identity-review-b)', () => {
  test("/ask-genie mounted while the actor reads are held continues A's conversation once the gate opens", async ({ app, page, mockApi }) => {
    const submits = recordSubmits(mockApi);
    await seedOwnedStorage(page, FIXTURE_ACTOR_A, { local: { [CONVERSATION_KEY]: A_CONVERSATION } });
    // Hold both trusted observations, so the route mounts with the gate
    // pending and its first read of the stored id returns null.
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    mockApi.register('GET', '/api/session', async () => {
      await held;
      return json<SessionResponse>({ ...SESSION, actor_cache_key: FIXTURE_ACTOR_A });
    });
    mockApi.register('GET', '/api/health', async () => {
      await held;
      return json<HealthPayload>({ ...HEALTH_OK, actor_cache_key: FIXTURE_ACTOR_A });
    });
    await page.goto('/ask-genie', { waitUntil: 'domcontentloaded' });
    const composer = page.locator('#main-content').getByRole('textbox', { name: 'Ask Genie — question' });
    await expect(composer, 'the route mounted while the gate was pending').toBeVisible();

    release();
    await app.settle();
    await composer.fill(GENIE_QUESTION);
    await composer.press('Enter');
    await expect.poll(() => submits.length).toBe(1);
    expect(submits[0], "A's first question after the gate opened continues A's conversation").toBe(A_CONVERSATION);
  });
});

test.describe('the persisted aggregate cache (delivery-05)', () => {
  /** Hold Home's preview and summary reads until `release()`. */
  function holdHomeReads(mockApi: MockApi): () => void {
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    mockApi.register('POST', '/api/portfolio/preview', async (request) => {
      await held;
      const criteria = (request.body as { criteria?: { marketing_eligibility?: unknown } } | null)?.criteria;
      return json<PortfolioPreview>(criteria?.marketing_eligibility === 'Eligible only' ? CONTACTABLE_PORTFOLIO_PREVIEW : PORTFOLIO_PREVIEW);
    });
    mockApi.register('GET', '/api/home/summary', async () => {
      await held;
      return json<HomeSummary>(HOME_SUMMARY);
    });
    return release;
  }

  /** Load Home as the served actor and wait for the throttled save. */
  async function loadAndPersistHome(app: AppDriver, page: Page): Promise<string[]> {
    await app.gotoRoute('/');
    await expect(kpiSkeletons(page)).toHaveCount(0);
    const values = await kpiValues(page).allTextContents();
    expect(values).toHaveLength(4);
    await expect
      .poll(() => page.evaluate((key) => window.sessionStorage.getItem(key)?.includes('"preview","home"') ?? false, QUERY_CACHE_KEY))
      .toBe(true);
    return values;
  }

  const since = (calls: readonly ApiCall[], from: number) => calls.slice(from);

  test('a reload repaints Home from the snapshot while its reads are held, with no audited read', async ({ app, page, mockApi }) => {
    serveActor(mockApi, () => FIXTURE_ACTOR_A);
    const before = await loadAndPersistHome(app, page);
    const release = holdHomeReads(mockApi);
    const from = mockApi.calls.length;
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(kpiValues(page)).toHaveText(before);
    await expect(kpiSkeletons(page)).toHaveCount(0);
    expect(since(mockApi.calls, from).filter((call) => AUDITED_READ.test(call.path)).map((call) => call.path)).toEqual([]);
    release();
    await app.settle();
    await expect(kpiValues(page)).toHaveText(before);
  });

  test('after an actor change, a reload restores nothing: skeletons while the reads are held', async ({ app, page, mockApi }) => {
    let actor = FIXTURE_ACTOR_A;
    serveActor(mockApi, () => actor);
    await loadAndPersistHome(app, page);
    actor = FIXTURE_ACTOR_B;
    const release = holdHomeReads(mockApi);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(kpiSkeletons(page)).toHaveCount(4);
    await expect
      .poll(() => page.evaluate((key) => window.sessionStorage.getItem(key)?.includes('"preview","home"') ?? false, QUERY_CACHE_KEY))
      .toBe(false);
    await expect(kpiSkeletons(page), 'still held: nothing restored').toHaveCount(4);
    release();
    await app.settle();
    await expect(kpiSkeletons(page)).toHaveCount(0);
  });
});
