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
 * Page 1's own reaction to the flip belongs to D-identity-review-a3 (W5b).
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
