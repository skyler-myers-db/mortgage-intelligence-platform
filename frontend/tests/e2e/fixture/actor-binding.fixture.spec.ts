/**
 * The per-request actor binding in the built app at 1440x900
 * (D-identity-review-a4; audit genie-02, bundle-04 item 4).
 *
 * Every app request from an opened document carries the document owner's key
 * in X-MIP-Actor-Key; the server refuses one that no longer names the
 * signed-in user with a 409 actor_changed. actorBinding.ts emulates that
 * server half (the backend's own proof is tests/unit/test_actor_binding.py).
 *
 *   (a) the header rides every app request after the first open, and never
 *       health, session, the boot primes or the RUM beacon;
 *   (b) a read sent after the cookie flipped is refused: the shared failure
 *       says the signed-in user changed, offers no action, asks for exactly
 *       one health recheck, and the tab resets once that probe answers;
 *   (c) a rotation alias re-stamps in place: no reset, the pin stays, later
 *       requests carry the new key and nothing is refused.
 */
import type { Page } from '@playwright/test';
import type { HealthPayload } from '../../../src/lib/apiTypes';
import type { SessionResponse } from '../../../src/types';
import { installActorBinding, type BindingLogEntry } from './actorBinding';
import { expectAxeClean, KNOWN_VIOLATIONS } from './axe';
import { RequestGate } from './data/decisionReceipt';
import { enableRum } from './data/errorTelemetry';
import { FIXTURE_ACTOR_A, FIXTURE_ACTOR_B, HEALTH_OK, SESSION, serveActor } from './data/shell';
import { json } from './mockApi';
import { seedOwnedStorage } from './ownedStorage';
import { expect, test } from './test';

/** ACTOR_A's key under a rotated secret: its alias list names ACTOR_A. */
const ACTOR_A2 = 'actor_a2a2a2a2a2a2a2a2';
const PINS_KEY = 'mip.pinnedInsights';
const A_PIN = {
  id: 'pin-of-a',
  question: 'Which states have the most in-the-money borrowers?',
  summary: 'Illinois has the most in-the-money borrowers.',
  source: 'mip.gold.geo_state_rollup',
  pinnedAt: '2026-07-14T14:00:00Z',
};
const RESET_NOTICE = 'The signed-in user changed, so this tab was reset. Nothing from the previous session carries over.';
const BOOT_PRIMES = /^\/api\/v1\/(session|config\/options|config\/footprint|health)$/;
/** The raw, header-less call sites that may run after the open (rawFetchGate allowlist). */
const RAW_AFTER_OPEN = /^\/api\/v1\/(config\/footprint|health|telemetry\/rum)$/;
const isHealth = (entry: BindingLogEntry) => /^\/api(\/v1)?\/health$/.test(entry.path);

const pinsCard = (page: Page) => page.locator('#main-content section.pinned-insights');
const toastRegion = (page: Page) => page.locator('section.toast-region[aria-label="Notifications"]');
const mainNav = (page: Page) => page.getByRole('navigation', { name: 'Main navigation' });

test('(a) dark: every app request after the first open carries the owner key; health, session, the boot primes and the RUM beacon carry none', async ({ app, page, mockApi }) => {
  const rum = enableRum(mockApi);
  mockApi.register('GET', '/api/session', () => json<SessionResponse>({ ...SESSION, rum_enabled: true, actor_cache_key: FIXTURE_ACTOR_A }));
  mockApi.register('GET', '/api/health', () => json<HealthPayload>({ ...HEALTH_OK, actor_cache_key: FIXTURE_ACTOR_A }));
  const binding = await installActorBinding(page, () => FIXTURE_ACTOR_A);
  await app.setTheme('dark');
  await app.gotoRoute('/');
  await mainNav(page).getByRole('link', { name: 'Segments' }).click();
  await app.settle();
  await expect.poll(() => rum.bodies.length, { timeout: 15_000 }).toBeGreaterThan(0);

  const firstKeyed = binding.log.findIndex((entry) => entry.key !== null);
  expect(firstKeyed, 'the document opened and bound its requests').toBeGreaterThan(0);
  const keyed = binding.log.filter((entry) => entry.key !== null);
  expect(keyed.length).toBeGreaterThan(3);
  expect([...new Set(keyed.map((entry) => entry.key))]).toEqual([FIXTURE_ACTOR_A]);
  const unboundAfterOpen = binding
    .bound(firstKeyed)
    .filter((entry) => entry.key === null && !RAW_AFTER_OPEN.test(entry.path))
    .map((entry) => `${entry.method} ${entry.path}`);
  expect(unboundAfterOpen, 'every app request after the open carries the key').toEqual([]);
  const primes = binding.log.slice(0, firstKeyed).filter((entry) => BOOT_PRIMES.test(entry.path));
  expect(primes.map((entry) => entry.path).sort()).toEqual([
    '/api/v1/config/footprint',
    '/api/v1/config/options',
    '/api/v1/health',
    '/api/v1/session',
  ]);
  expect(primes.every((entry) => entry.key === null), 'the boot primes are raw and header-less').toBe(true);
  const exempt = binding.log.filter((entry) => /\/(health|session|telemetry\/rum)$/.test(entry.path));
  expect(exempt.some((entry) => entry.path.endsWith('/telemetry/rum')), 'the RUM beacon was seen').toBe(true);
  expect(exempt.filter((entry) => entry.key !== null), 'health, session and the beacon carry none').toEqual([]);
  expect(binding.log.filter((entry) => entry.outcome === 'refused')).toEqual([]);
});

for (const theme of ['dark', 'light'] as const) {
  test(`(b) ${theme}: a read sent after the flip is refused with the shared no-action failure, one recheck, then the reset`, async ({ app, page, mockApi, hygiene }) => {
    hygiene.allow('console.error', /status of 409/);
    let actor = FIXTURE_ACTOR_A;
    serveActor(mockApi, () => actor);
    const gate = new RequestGate();
    let holdHealth = false;
    mockApi.register('GET', '/api/health', async () => {
      if (holdHealth) await gate.hold();
      return json<HealthPayload>({ ...HEALTH_OK, actor_cache_key: actor, actor_cache_key_aliases: [] });
    });
    const binding = await installActorBinding(page, () => actor);
    await app.setTheme(theme);
    await app.gotoRoute('/');

    const from = binding.log.length;
    holdHealth = true;
    actor = FIXTURE_ACTOR_B;
    const healthBefore = binding.log.filter(isHealth).length;
    await mainNav(page).getByRole('link', { name: 'Segments' }).click();
    const failure = page.locator('#main-content [data-async-status="actor_changed"]').first();
    await expect(failure).toContainText('The signed-in user changed.');
    await expect(failure).toContainText('Nothing was recorded. This tab resets for the new user.');
    await expect(failure.getByRole('button'), 'no Retry and no Reload').toHaveCount(0);
    await expectAxeClean(page, { key: { route: 'segment-intelligence', state: 'actor-changed' }, theme, known: KNOWN_VIOLATIONS });
    await expect.poll(() => binding.log.filter(isHealth).length).toBe(healthBefore + 1);
    await expect.poll(() => gate.received).toBe(true);
    expect(binding.log.filter(isHealth).length, 'exactly one extra health request while it is held').toBe(healthBefore + 1);

    gate.release();
    await page.waitForURL((url) => url.pathname === '/', { timeout: 15_000 });
    await app.settle();
    await expect(toastRegion(page)).toContainText(RESET_NOTICE);
    const after = binding.log.slice(from);
    expect(after.some((entry) => entry.key === FIXTURE_ACTOR_A && entry.outcome === 'refused'), 'non-vacuity').toBe(true);
    const leaked = after.filter((entry) => entry.key === FIXTURE_ACTOR_A && entry.served === FIXTURE_ACTOR_B && entry.outcome !== 'refused');
    expect(leaked, "no request bound to A was answered while B was served").toEqual([]);
  });
}

test('(c) dark: a rotation alias re-stamps in place: no reset, the pin stays, later requests carry the new key, none refused', async ({ app, page, mockApi }) => {
  let actor = FIXTURE_ACTOR_A;
  let aliases: string[] = [];
  serveActor(mockApi, () => actor, () => aliases);
  await seedOwnedStorage(page, FIXTURE_ACTOR_A, { local: { [PINS_KEY]: JSON.stringify([A_PIN]) } });
  const binding = await installActorBinding(page, () => actor, () => aliases);
  await app.setTheme('dark');
  await app.gotoRoute('/');
  await expect(pinsCard(page)).toContainText(A_PIN.question);

  actor = ACTOR_A2;
  aliases = [FIXTURE_ACTOR_A];
  const probes = () => mockApi.calls.filter((call) => /^\/api(\/v1)?\/health$/.test(call.path)).length;
  const before = probes();
  await page.clock.runFor(8_000);
  await expect.poll(probes, { timeout: 15_000 }).toBeGreaterThan(before);
  await expect
    .poll(() => page.evaluate(() => [window.localStorage.getItem('mip.actorOwner'), window.sessionStorage.getItem('mip.actorCacheKey')]))
    .toEqual([ACTOR_A2, ACTOR_A2]);
  await expect(pinsCard(page), 'the alias kept the pin').toContainText(A_PIN.question);

  const from = binding.log.length;
  await mainNav(page).getByRole('link', { name: 'Segments' }).click();
  await app.settle();
  expect(new URL(page.url()).pathname, 'no reset').toBe('/segment-intelligence');
  await expect(toastRegion(page)).not.toContainText(RESET_NOTICE);
  const later = binding.bound(from).filter((entry) => entry.key !== null);
  expect(later.length, 'the next route sent bound reads').toBeGreaterThan(0);
  expect([...new Set(later.map((entry) => entry.key))]).toEqual([ACTOR_A2]);
  expect(binding.log.filter((entry) => entry.outcome === 'refused')).toEqual([]);
});
