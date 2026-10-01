/**
 * Portfolio Builder forms (W5b lane w5-portfolio-forms: audit critic-v3,
 * critic-04, states-04 / states-03), proven in the production build at
 * 1440x900:
 *
 *   P1 critic-v3: a typed budget and applied variants survive leaving through
 *      the rail (Leave) and coming back: 'Draft restored', the values and the
 *      variants; Reset brings the defaults back, drops the chip and puts focus
 *      on Holdout.
 *   P2 critic-04: '$' and '%' sit beside the controls; a Holdout of 75 commits
 *      as 50 with 'Capped at 50%'; a Field-wrapped control the commit leaves
 *      invalid paints its border with the danger token (both themes).
 *   P3 states-04: a failed /api/portfolio/preview is the buyer-safe callout
 *      with Retry and no transport text; a 429 counts down, then Retry is live.
 *   P4 states-04: a save refused with 409 and a save hitting an outage read
 *      differently, and the typed name is kept.
 *   P6 critic-v3: a successful save, then a reload, shows no chip.
 *   P7 critic-v3: a draft A owns, with B served on the reload, is removed at
 *      B's first observation and never shown.
 *
 * Every state is axe-clean. Key names are literals: this directory never
 * imports runtime src (docs/testing.md, "Owned storage in fixture specs").
 */
import type { Locator, Page } from '@playwright/test';
import { expectAxeClean } from './axe';
import { FIXTURE_ACTOR_A, FIXTURE_ACTOR_B, serveActor } from './data/shell';
import { portfolioCreated } from './data/feedbackGuard';
import { RATE_LIMITED_429, TRANSPORT_JARGON } from './data/errorSurfaces';
import type { DegradeOptions } from './mockApi';
import { seedOwnedStorage } from './ownedStorage';
import { asComputedRgb } from './renderedColor';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';
import type { FixtureTheme } from './app';

const DRAFT_KEY = 'mip.portfolio.campaignDraft.v1';
const MAIN = '#main-content';
const HOLDOUT = 'Holdout % (0-50)';
/** The fixture recommendation's benefit-led subject (data/portfolio.ts). */
const BENEFIT_SUBJECT = 'See whether your mortgage options have improved';
const SERVER_DETAIL = 'fixture portfolio-forms detail';

const PREVIEW_500: DegradeOptions = { status: 500, body: { detail: `Preview query failed (${SERVER_DETAIL}).` } };
const SAVE_409: DegradeOptions = { method: 'POST', status: 409, body: { detail: `Duplicate campaign name (${SERVER_DETAIL}).` } };
const SAVE_503: DegradeOptions = {
  method: 'POST',
  status: 503,
  body: {
    detail: `Lakebase unavailable (${SERVER_DETAIL}).`,
    retryable: true,
    dependency: 'lakebase',
    reason: 'retries_exhausted',
    correlation_id: 'fixture-correlation-pf01',
  },
};

const spin = (page: Page, name: string) => page.getByRole('spinbutton', { name, exact: true });
const campaignSetup = (page: Page) => page.locator('.surface', { has: page.getByRole('heading', { name: 'Campaign setup' }) });
const draftChip = (page: Page) => page.getByTestId('campaign-draft-restored');
const readoutValue = (page: Page, label: string) => page.getByRole('group', { name: label, exact: true }).locator('.field__value');

async function storedDraft(page: Page): Promise<string | null> {
  return page.evaluate((key) => window.sessionStorage.getItem(key), DRAFT_KEY);
}

async function commit(field: Locator, value: string): Promise<void> {
  await field.click();
  await field.fill(value);
  await field.blur();
}

async function expectBuyerSafe(page: Page): Promise<void> {
  const text = (await page.locator(MAIN).textContent()) ?? '';
  expect(text, 'transport jargon reached the page').not.toMatch(TRANSPORT_JARGON);
  expect(text, 'server detail reached the page').not.toContain(SERVER_DETAIL);
}

async function axe(page: Page, state: string, theme: FixtureTheme): Promise<void> {
  await expectAxeClean(page, { key: { route: 'portfolio-builder', state }, theme, known: {} });
}

async function openSaveAndSubmit(page: Page, name: string): Promise<void> {
  await page.getByTestId('portfolio-save-build').click();
  await page.getByTestId('portfolio-save-name').fill(name);
  await page.getByTestId('portfolio-save-confirm').click();
}

test.describe('the campaign-setup draft (critic-v3)', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`P1 ${theme}: a draft survives leaving through the rail; Reset brings the defaults back`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/portfolio-builder');
      await expect(draftChip(page)).toHaveCount(0);
      await commit(spin(page, 'Budget'), '25000');
      const setup = campaignSetup(page);
      await setup.getByRole('button', { name: 'Apply variants' }).click();
      await expect(readoutValue(page, 'Benefit-led subject')).toHaveText(BENEFIT_SUBJECT);
      await expect.poll(() => storedDraft(page)).toContain('"budget":"25000"');

      // Leave through the rail: the guard still asks (a closed tab loses the draft).
      await page.getByRole('navigation', { name: 'Primary navigation' }).getByRole('link', { name: 'Entrada home' }).click();
      const dialog = page.getByRole('dialog', { name: 'Leave without saving?' });
      await expect(dialog).toContainText('It is kept as a draft in this tab until you save or reset it.');
      await dialog.getByRole('button', { name: 'Leave' }).click();
      await expect(page).toHaveURL(/\/$/);
      await app.settle();

      await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Portfolio', exact: true }).click();
      await app.settle();
      await expect(draftChip(page)).toHaveText('Draft restored');
      await expect(spin(page, 'Budget')).toHaveValue('25000');
      await expect(readoutValue(page, 'Benefit-led subject')).toHaveText(BENEFIT_SUBJECT);
      await expect(page.getByTestId('campaign-draft-variants-note')).toHaveCount(0);
      await axe(page, 'draft-restored', theme);

      await setup.getByRole('button', { name: 'Reset', exact: true }).click();
      await expect(draftChip(page)).toHaveCount(0);
      await expect(setup.getByRole('button', { name: 'Reset', exact: true })).toHaveCount(0);
      await expect(spin(page, 'Budget')).toHaveValue('');
      await expect(spin(page, HOLDOUT)).toHaveValue('10');
      await expect(readoutValue(page, 'Benefit-led subject')).toHaveText('Not set. Apply a recommendation to fill it.');
      await expect(spin(page, HOLDOUT)).toBeFocused();
      expect(await storedDraft(page)).toBeNull();
    });
  }

  test('P6: a successful save, then a reload, shows no chip', async ({ app, mockApi, page }) => {
    mockApi.register('POST', '/api/portfolio/create', ({ body }) => portfolioCreated((body as { name?: string } | null)?.name ?? ''));
    await app.gotoRoute('/portfolio-builder');
    await commit(spin(page, 'Budget'), '30000');
    await expect.poll(() => storedDraft(page)).toContain('"budget":"30000"');
    await openSaveAndSubmit(page, 'Summit IL refi cohort');
    await expect(page.getByTestId('portfolio-save-name')).toHaveCount(0);
    await expect.poll(() => storedDraft(page)).toBeNull();

    await page.reload({ waitUntil: 'domcontentloaded' });
    await app.settle();
    await expect(draftChip(page)).toHaveCount(0);
    await expect(spin(page, 'Budget')).toHaveValue('');
  });

  test("P7: a draft A owns is removed at B's first observation and never shown", async ({ app, mockApi, page }) => {
    let actor = FIXTURE_ACTOR_A;
    serveActor(mockApi, () => actor);
    // Another build's draft: its delivery settings restore, so A sees the chip.
    const setup = {
      subjectA: '', subjectB: '', bodyA: '', bodyB: '',
      holdoutPct: '12', startLocal: '09:00', endLocal: '16:00',
      budget: '41000', emailCost: '', smsCost: '', mailCost: '',
      marketHouseholdTogether: false, generationMode: 'operator', generatorLabel: 'Operator edited',
      provenanceTokenA: null, provenanceTokenB: null,
    };
    await seedOwnedStorage(page, FIXTURE_ACTOR_A, {
      session: { [DRAFT_KEY]: JSON.stringify({ v: 1, buildKey: '{"fixture":"another build"}', savedAt: 1_784_000_000_000, setup }) },
    });
    await app.gotoRoute('/portfolio-builder');
    await expect(draftChip(page)).toHaveText('Draft restored');
    await expect(spin(page, 'Budget')).toHaveValue('41000');

    // Flip, then reload at once: B is this new document's FIRST observation.
    actor = FIXTURE_ACTOR_B;
    await page.reload({ waitUntil: 'domcontentloaded' });
    await app.settle();
    await expect(draftChip(page)).toHaveCount(0);
    await expect(spin(page, 'Budget')).toHaveValue('');
    expect(await storedDraft(page)).toBeNull();
  });
});

test.describe('campaign setup fields (critic-04)', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`P2 ${theme}: affixes, a clamped holdout, and the danger border on a value the commit leaves invalid`, async ({ app, page }, testInfo) => {
      await app.setTheme(theme);
      await app.gotoRoute('/portfolio-builder');
      for (const [name, affix] of [[HOLDOUT, '%'], ['Budget', '$'], ['Email cost', '$'], ['SMS cost', '$'], ['Mail cost', '$']] as const) {
        const affixNode = page.locator('.field__control', { has: spin(page, name) }).locator('.field__affix');
        await expect(affixNode, name).toHaveText(affix);
        await expect(affixNode, name).toBeVisible();
        await expect(affixNode, name).toHaveAttribute('aria-hidden', 'true');
      }

      // Typed, not yet left: record whether this engine already flags it.
      const holdout = spin(page, HOLDOUT);
      await holdout.click();
      await holdout.fill('');
      await page.keyboard.type('75');
      const beforeBlur = await holdout.evaluate((input: HTMLInputElement) => ({
        userInvalid: input.matches(':user-invalid'),
        rangeOverflow: input.validity.rangeOverflow,
      }));
      expect(beforeBlur.rangeOverflow).toBe(true);
      testInfo.annotations.push({ type: 'user-invalid-before-blur', description: `${testInfo.project.name}: ${beforeBlur.userInvalid}` });
      const danger = await asComputedRgb(page, 'var(--status-danger-line-strong)');
      if (beforeBlur.userInvalid) {
        await expect(holdout).toHaveCSS('border-top-color', danger);
      }
      await holdout.blur();
      await expect(holdout).toHaveValue('50');
      await expect(page.locator('.field', { has: holdout }).getByRole('status')).toHaveText('Capped at 50%');
      expect(await holdout.evaluate((input: HTMLInputElement) => input.matches(':user-invalid'))).toBe(false);

      // The blur clamp leaves Holdout valid, so the danger border is proven on
      // a state the commit leaves invalid: text the number control cannot
      // parse ('1e') commits as no budget, and the field stays flagged.
      const budget = spin(page, 'Budget');
      await budget.click();
      await page.keyboard.type('1e');
      await budget.blur();
      const flagged = await budget.evaluate((input: HTMLInputElement) => ({
        userInvalid: input.matches(':user-invalid'),
        badInput: input.validity.badInput,
      }));
      expect(flagged).toEqual({ userInvalid: true, badInput: true });
      await expect(budget).toHaveCSS('border-top-color', danger);
      await axe(page, 'user-invalid', theme);
    });
  }
});

test.describe('portfolio failures in the buyer-safe vocabulary (states-04)', () => {
  test('P3: a failed preview is the shared callout with Retry, never transport text', async ({ app, page }) => {
    const restore = app.degrade('/api/portfolio/preview', PREVIEW_500);
    await app.gotoRoute('/portfolio-builder');
    const alert = page.locator(`${MAIN} [role="alert"]`, { hasText: "Couldn't load portfolio preview" });
    await expect(alert).toContainText("Couldn't load portfolio preview. The server hit an unexpected error.");
    await expectBuyerSafe(page);
    await axe(page, 'preview-failed', 'dark');

    restore();
    await alert.getByRole('button', { name: 'Retry loading portfolio preview' }).click();
    await expect(alert).toHaveCount(0);
    await expect(page.locator(`${MAIN} .kpi-row .kpi__value`).first()).not.toHaveText('—');
  });

  test.describe('timed waits', () => {
    test.use({ fixtureNow: null });

    test('P3: a 429 counts its Retry-After down, then Retry is live', async ({ app, mockApi, page }) => {
      await page.clock.install();
      const restore = app.degrade('/api/portfolio/preview', RATE_LIMITED_429);
      await app.gotoRoute('/portfolio-builder');
      const alert = page.locator(`${MAIN} [role="alert"]`, { hasText: 'Too many requests right now.' });
      await expect(alert.locator('.async-status__wait')).toContainText(/Try again in (1[0-2]|[1-9]) s/);
      const retry = alert.getByRole('button', { name: 'Retry loading portfolio preview' });
      await expect(retry).toHaveAttribute('aria-disabled', 'true');
      await expectBuyerSafe(page);
      await axe(page, 'preview-rate-limited', 'light');

      const previews = () => mockApi.calls.filter((call) => call.path === '/api/portfolio/preview').length;
      const before = previews();
      await retry.click({ force: true });
      expect(previews(), 'an inert Retry sends nothing').toBe(before);
      await page.clock.runFor(12_000);
      await expect(retry).not.toHaveAttribute('aria-disabled', 'true');
      restore();
      await retry.click();
      await expect(alert).toHaveCount(0);
      expect(previews()).toBe(before + 1);
    });
  });

  test('P4: a refused save and an outage read differently, and the typed name is kept', async ({ app, page }) => {
    await app.gotoRoute('/portfolio-builder');
    const failure = page.locator('.save-build-form__error[role="alert"]');

    const conflict = app.degrade('/api/portfolio/create', SAVE_409);
    await openSaveAndSubmit(page, 'Summit IL refi cohort');
    await expect(failure).toHaveText('Save failed: Reload to read the current version. Your name is kept; try again.');
    await expect(page.getByTestId('portfolio-save-name')).toHaveValue('Summit IL refi cohort');
    await expectBuyerSafe(page);
    await axe(page, 'save-conflict', 'dark');
    conflict();

    app.degrade('/api/portfolio/create', SAVE_503);
    await page.getByTestId('portfolio-save-confirm').click();
    await expect(failure).toHaveText('Save failed: The app already retried; try again shortly. Your name is kept; try again.');
    await expect(page.getByTestId('portfolio-save-name')).toHaveValue('Summit IL refi cohort');
    await expectBuyerSafe(page);
    await axe(page, 'save-outage', 'light');
  });
});
