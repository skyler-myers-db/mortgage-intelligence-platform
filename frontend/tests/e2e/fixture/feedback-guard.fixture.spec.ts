/**
 * Feedback guard (lane feedback-guard: audit states-05, states-07 slice 1),
 * proven in the production build at 1440x900.
 *
 * states-05, the unsaved-changes guard under main.tsx's data router:
 *  - A dirty Portfolio Builder (a typed campaign budget) stops a rail click,
 *    a route-nav click, a Cmd-K jump, a link in the floating Genie panel and
 *    the Back button at "Leave without saving?". Focus opens on Stay; Stay
 *    (or Escape) keeps the page, its URL and the typed value and hands focus
 *    back to the control that started the navigation; Leave completes it,
 *    including a redirect the destination issues on mount (Cmd-K's bare
 *    /offer-orchestrator). The dialog passes WCAG A/AA in both themes.
 *  - A query-string change on the same page (Run build) never asks.
 *  - Offer Orchestrator guards a typed rejection note.
 *  - A tab close raises the browser's own prompt only while dirty.
 *
 * states-07, the shell toast region:
 *  - One `popover="manual"` region in the top layer, a persistent polite
 *    status list, and no empty alert region on a healthy page.
 *  - A portfolio save shows ONE toast with its audit-event link, bottom-
 *    centre, painted above the page, AA text in both themes; the Save button
 *    keeps its own label.
 *  - A failed copy is a role=alert toast; repeats coalesce into one count.
 *    Dismissing a toast from the keyboard hands focus back to the control
 *    focus came from, not to <body>.
 *  - An approval routed to a loan officer is a toast linked to its audit
 *    row; the old in-page routing chip is gone.
 *  - Entry uses @starting-style; reduced motion drops the transition.
 */
import AxeBuilder from '@axe-core/playwright';
import type { Locator, Page } from '@playwright/test';
import type { DecisionReceipt, GenieSubmitResult } from '../../../src/lib/apiTypes';
import { PRIMARY_BORROWER } from './data/borrowers';
import { ledgerReceipt } from './data/decisionReceipt';
import { REFUSED_QUESTIONS, refusedSubmit } from './data/genieRefusal';
import {
  ROUTED_APPROVE_AUDIT_ID,
  ROUTED_LOAN_OFFICER,
  SAVE_AUDIT_ID,
  portfolioCreated,
  routedApproveResult,
} from './data/feedbackGuard';
import { json } from './mockApi';
import { contrastRatio, renderedColors } from './renderedColor';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

const DIALOG_NAME = 'Leave without saving?';
const BUDGET = '25000';
const SETUP_MESSAGE = 'Your campaign setup has not been saved with a build. Leaving discards it.';
const OFFER_PATH = `/offer-orchestrator/${PRIMARY_BORROWER.borrower_id}`;
const GENIE_SUBMIT_PATH = '/api/genie/message/submit';

function leaveDialog(page: Page): Locator {
  return page.getByRole('dialog', { name: DIALOG_NAME });
}

function budgetField(page: Page): Locator {
  return page.getByRole('spinbutton', { name: 'Budget', exact: true });
}

function routeNavLink(page: Page, name: string): Locator {
  return page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name, exact: true });
}

function railHome(page: Page): Locator {
  return page.getByRole('navigation', { name: 'Primary navigation' }).getByRole('link', { name: 'Entrada home' });
}

/** Portfolio Builder with a typed (unsaved) campaign budget. */
async function dirtyPortfolio(app: { gotoRoute: (path: string) => Promise<void> }, page: Page): Promise<void> {
  await app.gotoRoute('/portfolio-builder');
  // A real click first: Chromium only shows a beforeunload prompt for a
  // page the person has interacted with.
  await budgetField(page).click();
  await budgetField(page).fill(BUDGET);
  await budgetField(page).blur();
  await expect(budgetField(page)).toHaveValue(BUDGET);
}

async function expectBlockedOnPortfolio(page: Page): Promise<Locator> {
  const dialog = leaveDialog(page);
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText(SETUP_MESSAGE);
  await expect(dialog.getByRole('button', { name: 'Stay' })).toBeFocused();
  await expect(page).toHaveURL(/\/portfolio-builder$/);
  return dialog;
}

function toastRegion(page: Page): Locator {
  return page.locator('section.toast-region[aria-label="Notifications"]');
}

/** WCAG 2.0/2.1/2.2 A + AA violations inside `selector` (the axe gate's tag set). */
async function axeViolations(page: Page, selector: string): Promise<string[]> {
  const results = await new AxeBuilder({ page })
    .include(selector)
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    .analyze();
  return results.violations.map((violation) => `${violation.id}: ${violation.nodes.map((node) => node.target.join(' ')).join(' ; ')}`);
}

test.describe('unsaved-changes guard (states-05)', () => {
  test('a dirty page stops a rail click; Stay keeps the page and the typed value, Leave goes', async ({ app, page }) => {
    await dirtyPortfolio(app, page);

    await railHome(page).click();
    let dialog = await expectBlockedOnPortfolio(page);
    // A native modal: the page behind it is inert and the dialog is on the top layer.
    expect(await dialog.evaluate((node) => node.matches(':modal'))).toBe(true);
    expect(await axeViolations(page, 'dialog.unsaved-dialog'), 'WCAG A/AA inside the open dialog').toEqual([]);
    await dialog.getByRole('button', { name: 'Stay' }).click();
    await expect(dialog).toHaveCount(0);
    await expect(page).toHaveURL(/\/portfolio-builder$/);
    await expect(budgetField(page)).toHaveValue(BUDGET);
    // WCAG 2.4.3: closing the dialog hands focus back to the control that
    // started the navigation, not to <body>.
    await expect(railHome(page)).toBeFocused();

    await routeNavLink(page, 'Leads').click();
    dialog = await expectBlockedOnPortfolio(page);
    await dialog.getByRole('button', { name: 'Leave' }).click();
    await expect(page).toHaveURL(/\/lead-queue$/);
    await app.settle();
    await expect(leaveDialog(page)).toHaveCount(0);
    await expect(page.locator('#main-content h1')).toBeVisible();
  });

  for (const theme of FIXTURE_THEMES) {
    test(`the dialog reads at AA in the ${theme} theme`, async ({ app, page }) => {
      await app.setTheme(theme);
      await dirtyPortfolio(app, page);
      await railHome(page).click();
      const dialog = await expectBlockedOnPortfolio(page);
      expect(await axeViolations(page, 'dialog.unsaved-dialog'), `WCAG A/AA inside the dialog (${theme})`).toEqual([]);
      for (const part of ['.approval__title', '.approval__sub']) {
        const { fg, bg } = await renderedColors(dialog.locator(part));
        expect(contrastRatio(fg, bg), `${part} contrast in ${theme}`).toBeGreaterThanOrEqual(4.5);
      }
      await page.keyboard.press('Escape');
      await expect(dialog).toHaveCount(0);
      await expect(railHome(page)).toBeFocused();
    });
  }

  test('Cmd-K and the Back button are guarded too; Escape means Stay', async ({ app, page }) => {
    await app.gotoRoute('/');
    await routeNavLink(page, 'Portfolio').click();
    await app.settle();
    await budgetField(page).fill(BUDGET);
    await budgetField(page).blur();

    const palette = await app.openCommandPalette();
    await palette.getByRole('combobox').fill('Lead Queue');
    await palette.getByRole('option', { name: /Lead Queue/ }).first().click();
    const dialog = await expectBlockedOnPortfolio(page);
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    await expect(budgetField(page)).toHaveValue(BUDGET);

    // Back is a POP the router reverts until the reviewer decides.
    await page.goBack();
    await expectBlockedOnPortfolio(page);
    await leaveDialog(page).getByRole('button', { name: 'Leave' }).click();
    await expect(page).toHaveURL(/\/$/);
  });

  test('Leave lets the destination redirect on mount (Cmd-K to the bare Offer route)', async ({ app, page }) => {
    // Visiting a borrower first remembers it, so the bare /offer-orchestrator
    // (the palette's target) redirects there with <Navigate replace/>, and
    // the Offer chunk is already loaded: the redirect runs in the same
    // effect flush as the dirty page's unregister.
    await app.gotoRoute(OFFER_PATH);
    await routeNavLink(page, 'Portfolio').click();
    await app.settle();
    await expect(page).toHaveURL(/\/portfolio-builder$/);
    await budgetField(page).fill(BUDGET);
    await budgetField(page).blur();

    const palette = await app.openCommandPalette();
    await palette.getByRole('combobox').fill('Offer Orchestrator');
    await palette.getByRole('option', { name: /Offer Orchestrator/ }).first().click();
    const dialog = await expectBlockedOnPortfolio(page);
    await dialog.getByRole('button', { name: 'Leave' }).click();

    await expect(page).toHaveURL(new RegExp(`${OFFER_PATH}$`));
    await app.settle();
    await expect(leaveDialog(page)).toHaveCount(0);
    await expect(page.locator('#main-content h1')).toHaveText('Review and approve outreach');
  });

  test('a query-string change on the same page never asks, and a clean page leaves freely', async ({ app, page }) => {
    await app.gotoRoute('/portfolio-builder');
    const menu = await app.openFilterMenu('OCCUPANCY');
    await menu.getByRole('option', { name: /Non-owner-occupied/ }).click();
    await page.getByRole('button', { name: 'Run build' }).click();
    await expect(page).toHaveURL(/\/portfolio-builder\?.+/);
    await app.settle();
    await expect(leaveDialog(page)).toHaveCount(0);

    await routeNavLink(page, 'Leads').click();
    await expect(page).toHaveURL(/\/lead-queue$/);
    await expect(leaveDialog(page)).toHaveCount(0);
  });

  test('Offer Orchestrator guards a typed rejection note', async ({ app, page }) => {
    await app.gotoRoute(OFFER_PATH);
    await page.locator('.approval').getByRole('button', { name: 'Reject', exact: true }).click();
    await page.getByRole('textbox', { name: 'Rationale note' }).fill('Borrower asked for no contact this quarter.');

    await routeNavLink(page, 'Leads').click();
    const dialog = leaveDialog(page);
    await expect(dialog).toContainText('Your rejection note has not been recorded.');
    await dialog.getByRole('button', { name: 'Stay' }).click();
    await expect(page.getByRole('textbox', { name: 'Rationale note' })).toHaveValue('Borrower asked for no contact this quarter.');
    await expect(page).toHaveURL(new RegExp(`${OFFER_PATH}$`));
  });

  test('a link inside the floating Genie panel is guarded, and Stay returns focus to it', async ({ app, mockApi, page }) => {
    mockApi.register('POST', GENIE_SUBMIT_PATH, () =>
      json<GenieSubmitResult>(refusedSubmit('out_of_scope', REFUSED_QUESTIONS.out_of_scope)),
    );
    await dirtyPortfolio(app, page);
    const genie = await app.askGenie(REFUSED_QUESTIONS.out_of_scope);
    const vocabulary = genie.getByTestId('genie-refusal-card').getByRole('link', { name: 'Reviewed vocabulary' });
    await vocabulary.click();

    let dialog = await expectBlockedOnPortfolio(page);
    await dialog.getByRole('button', { name: 'Stay' }).click();
    await expect(dialog).toHaveCount(0);
    await expect(budgetField(page)).toHaveValue(BUDGET);
    await expect(genie).toBeVisible();
    await expect(vocabulary).toBeFocused();

    await vocabulary.click();
    dialog = await expectBlockedOnPortfolio(page);
    await dialog.getByRole('button', { name: 'Leave' }).click();
    await expect(page).toHaveURL(/\/glossary#reviewed-vocabulary$/);
  });

  test('closing the tab raises the browser prompt only while the page is dirty', async ({ app, page }) => {
    await dirtyPortfolio(app, page);
    const prompts: string[] = [];
    page.on('dialog', (dialog) => {
      prompts.push(dialog.type());
      void dialog.dismiss();
    });
    await page.close({ runBeforeUnload: true });
    await expect.poll(() => prompts).toEqual(['beforeunload']);
    expect(page.isClosed(), 'dismissing the prompt keeps the tab').toBe(false);

    // Typing the budget back out makes the page clean again (and is a fresh
    // gesture, so a listener left attached would have prompted).
    await budgetField(page).click();
    await budgetField(page).fill('');
    await budgetField(page).blur();
    await page.close({ runBeforeUnload: true });
    await expect.poll(() => page.isClosed()).toBe(true);
    expect(prompts).toEqual(['beforeunload']);
  });
});

test.describe('toast region (states-07 slice 1)', () => {
  test('is one top-layer popover with a polite status list and no empty alert', async ({ app, page }) => {
    await app.gotoRoute('/');
    const region = toastRegion(page);
    await expect(region).toHaveCount(1);
    await expect(region).toHaveAttribute('popover', 'manual');
    await expect.poll(() => region.evaluate((node) => node.matches(':popover-open'))).toBe(true);
    const list = region.locator('.toast-region__list');
    await expect(list).toHaveAttribute('role', 'status');
    await expect(list).toHaveAttribute('aria-live', 'polite');
    await expect(region.locator('[role="alert"]')).toHaveCount(0);
    await expect(page.locator('[role="alert"]')).toHaveCount(0);
  });

  for (const theme of FIXTURE_THEMES) {
    test(`a portfolio save shows one toast with its audit link (${theme})`, async ({ app, mockApi, page }) => {
      const saved: string[] = [];
      mockApi.register('POST', '/api/portfolio/create', ({ body }) => {
        const name = (body as { name?: unknown } | null)?.name;
        saved.push(typeof name === 'string' ? name : '');
        return portfolioCreated(saved[saved.length - 1]);
      });
      await app.setTheme(theme);
      await app.gotoRoute('/portfolio-builder');
      await expect(toastRegion(page)).toHaveCount(1);

      const save = page.getByTestId('portfolio-save-build');
      await save.click();
      await page.getByTestId('portfolio-save-name').fill('Summit IL refi cohort');
      await page.getByTestId('portfolio-save-confirm').click();

      const toast = toastRegion(page).locator('.toast');
      await expect(toast).toHaveCount(1);
      // Hovering the region pauses the 8 s dismiss timer, so a loaded machine
      // cannot time the toast out under the assertions below.
      await toast.hover();
      await expect(toastRegion(page)).toHaveAttribute('data-paused', 'true');
      await expect(toast).toContainText('Build saved');
      await expect(toast).toContainText('Summit IL refi cohort');
      await expect(toastRegion(page).locator('[role="status"] .toast')).toHaveCount(1);
      const link = toast.getByRole('link', { name: 'View audit event' });
      await expect(link).toHaveAttribute('href', `/admin-config?audit_event_id=${SAVE_AUDIT_ID}#audit`);
      await expect(save).toHaveText('Save build');
      await app.settle();
      await expect(toast).toHaveCount(1);
      expect(saved).toEqual(['Summit IL refi cohort']);
      expect(await axeViolations(page, 'section.toast-region'), `WCAG A/AA in the toast region (${theme})`).toEqual([]);

      // Bottom-centre inside the 1440x900 viewport, painted above the page.
      const box = await toast.boundingBox();
      if (!box) throw new Error('toast has no box');
      expect(Math.abs(box.x + box.width / 2 - 720)).toBeLessThanOrEqual(2);
      expect(box.y + box.height).toBeLessThanOrEqual(900);
      expect(box.y + box.height).toBeGreaterThan(820);
      const onTop = await toast.evaluate((node) => {
        const rect = node.getBoundingClientRect();
        const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
        return hit !== null && node.contains(hit);
      });
      expect(onTop, 'the toast paints above the page content').toBe(true);

      for (const part of ['.toast__title', '.toast__detail', '.toast__link']) {
        const { fg, bg } = await renderedColors(toast.locator(part).first());
        expect(contrastRatio(fg, bg), `${part} contrast in ${theme}`).toBeGreaterThanOrEqual(4.5);
      }
      const icon = await renderedColors(toast.locator('.toast__ico'));
      expect(contrastRatio(icon.fg, icon.bg), `success icon in ${theme}`).toBeGreaterThanOrEqual(3);

      await toast.getByRole('button', { name: 'Dismiss notification' }).click();
      await expect(toast).toHaveCount(0);
    });
  }

  test('a failed copy is an alert toast, and repeats coalesce into one count', async ({ app, page }) => {
    await page.addInitScript(() => {
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: { writeText: () => Promise.reject(new DOMException('Denied (fixture)', 'NotAllowedError')) },
      });
    });
    await app.gotoRoute('/portfolio-builder');
    const share = page.getByTestId('portfolio-copy-link');
    for (let press = 0; press < 4; press += 1) await share.click();

    const failure = toastRegion(page).locator('.toast--error');
    await expect(failure).toHaveCount(1);
    await expect(failure).toHaveAttribute('role', 'alert');
    await expect(failure).toContainText('Copy failed');
    await expect(failure.locator('.toast__count')).toContainText('×4');
    await expect(toastRegion(page).locator('[role="status"] .toast--error')).toHaveCount(0);
    await expect(share).toHaveText('Share this build');
    const { fg, bg } = await renderedColors(failure.locator('.toast__ico'));
    expect(contrastRatio(fg, bg)).toBeGreaterThanOrEqual(3);
    expect(await axeViolations(page, 'section.toast-region'), 'WCAG A/AA with a failure toast').toEqual([]);
  });

  test('dismissing a toast from the keyboard hands focus back to where it came from', async ({ app, page }) => {
    await page.addInitScript(() => {
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: { writeText: () => Promise.reject(new DOMException('Denied (fixture)', 'NotAllowedError')) },
      });
    });
    await app.gotoRoute('/portfolio-builder');
    await expect(toastRegion(page)).toHaveCount(1);
    const share = page.getByTestId('portfolio-copy-link');
    await share.focus();
    await page.keyboard.press('Enter');

    const failure = toastRegion(page).locator('.toast--error');
    await expect(failure).toContainText('Copy failed');
    const dismiss = failure.getByRole('button', { name: 'Dismiss notification' });
    await dismiss.focus();
    await expect(dismiss).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(failure).toHaveCount(0);
    // WCAG 2.4.3: focus goes back to Share, not to <body>.
    await expect(share).toBeFocused();
  });

  test('an approval routed to a loan officer is announced with its audit link', async ({ app, mockApi, page }) => {
    mockApi.register('POST', '/api/outreach/approve', () => routedApproveResult());
    mockApi.register('GET', '/api/audit/receipt/:id', ({ params }) =>
      json<DecisionReceipt>(ledgerReceipt(params.id, PRIMARY_BORROWER, 'approved')),
    );
    await app.gotoRoute(OFFER_PATH);
    await page.getByLabel('Assign to loan officer').selectOption(ROUTED_LOAN_OFFICER.email);
    await page.getByTestId('hero-approve').click();

    const toast = toastRegion(page).locator('[role="status"] .toast');
    await expect(toast).toHaveCount(1);
    await expect(toast).toContainText('Approval routed');
    await expect(toast).toContainText(`Assigned to ${ROUTED_LOAN_OFFICER.email} · follow-up Jul 19`);
    await expect(toast.getByRole('link', { name: 'View audit event' })).toHaveAttribute(
      'href',
      `/admin-config?audit_event_id=${ROUTED_APPROVE_AUDIT_ID}#audit`,
    );
    await expect(page.getByTestId('decision-receipt')).toBeVisible();
    await expect(page.locator('[data-testid="routing-confirm"], .outreach-routing__confirm')).toHaveCount(0);
  });

  test.describe('motion', () => {
    test.use({ contextOptions: { reducedMotion: 'no-preference' }, traceScreenshots: false });

    test('a toast enters from @starting-style, and reduced motion drops the transition', async ({ app, page }) => {
      await page.addInitScript(() => {
        Object.defineProperty(navigator, 'clipboard', {
          configurable: true,
          value: { writeText: () => Promise.resolve() },
        });
      });
      await app.gotoRoute('/portfolio-builder');
      await expect(toastRegion(page)).toHaveCount(1);
      // Sample the toast's opacity in the same task that inserted it: the
      // @starting-style value, before the entry transition has advanced.
      await page.evaluate(() => {
        const win = window as Window & { __toastStart?: string };
        const region = document.querySelector('section.toast-region');
        if (!region) throw new Error('no toast region');
        new MutationObserver((_records, observer) => {
          const toast = region.querySelector('.toast');
          if (!toast) return;
          win.__toastStart = getComputedStyle(toast).opacity;
          observer.disconnect();
        }).observe(region, { childList: true, subtree: true });
      });
      await page.getByTestId('portfolio-copy-link').click();
      const toast = toastRegion(page).locator('.toast');
      await expect(toast).toContainText('Build link copied');
      expect(await page.evaluate(() => (window as Window & { __toastStart?: string }).__toastStart)).toBe('0');
      await expect.poll(() => toast.evaluate((node) => getComputedStyle(node).opacity)).toBe('1');
      expect(await toast.evaluate((node) => getComputedStyle(node).transitionDuration)).toBe('0.2s, 0.2s');

      await page.emulateMedia({ reducedMotion: 'reduce' });
      expect(await toast.evaluate((node) => getComputedStyle(node).transitionProperty)).toBe('none');
    });
  });
});
