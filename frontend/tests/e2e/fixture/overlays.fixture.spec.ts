/**
 * Overlays on the top layer (2026-09-21 audit stack-05, a11y-07 step 2,
 * css-03 slice 2, bundle-04, motion-01, responsive-06): the rendered proofs
 * for lane w4-overlays, on the production build at 1440x900.
 *
 * Every modal surface is a native <dialog> opened with showModal() through
 * hooks/useModalDialog. For each one, opened from its real trigger:
 *   - it is `:modal`; Tab and Shift+Tab never leave it; the page behind is
 *     inert (a hit test and a focus attempt); focus returns to the opener,
 *     or to the region heading / page h1 when the opener is gone;
 *   - one Escape closes only it; a backdrop press closes it and a press on
 *     its own box does not;
 *   - the exit is sampled mid-transition and is instant under reduced motion;
 *   - `::backdrop` paints --surface-scrim per theme and drops its blur under
 *     reduced transparency;
 *   - opening, hovering and preloading read nothing audited (the borrower
 *     proof reads its proof on open, by design and as before).
 * Plus the toast region over a modal, a hover card over the approve review,
 * the lazy drawer body (held and retired chunk), the idle and saveData
 * preload rules, and the 960x600 topbar (responsive-06).
 */
import fs from 'node:fs';
import path from 'node:path';
import type { Locator, Page } from '@playwright/test';
import type { AppDriver } from './app';
import { expectAxeClean } from './axe';
import { PRIMARY_BORROWER } from './data/borrowers';
import { crashTheEvidenceDrawer, holdChunk, retireChunk, spendStaleChunkReload } from './data/errorTelemetry';
import { registerGenieTurn } from './data/genieTurn';
import { registerDraftEcho, registerHeldDecision } from './data/queueKeyboard';
import type { Hygiene } from './hygiene';
import { json, type MockApi } from './mockApi';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';
import { expectNoAuditedReadSince, markNaturalLoad } from './visual';

const WIDE = { width: 1440, height: 900 };
const ID = PRIMARY_BORROWER.borrower_id;

interface Ctx {
  app: AppDriver;
  page: Page;
  mockApi: MockApi;
  hygiene: Hygiene;
}

interface Surface {
  name: string;
  route: string;
  prepare?(ctx: Ctx): void;
  /** Open it from its real trigger; returns the dialog and the opener. */
  open(ctx: Ctx): Promise<{ dialog: Locator; opener: Locator }>;
  /** Its own close control, or null when Escape is the only one. */
  closeLabel: string | null;
  backdrop: 'outside' | 'self';
  /** Stays rendered to play an exit (always mounted, or retained). */
  exit: boolean;
  /** Opening it reads an audited payload by design (the borrower proof). */
  audited: boolean;
}

function allowNamedErrorLines(hygiene: Hygiene): void {
  hygiene.allow('console.error', /\[mip\] client error/);
  hygiene.allow('console.error', /Failed to load resource/);
  hygiene.allow('console.error', /above error occurred|recreate this component tree/);
}

async function openDrawerFromKpi({ app, page }: Ctx) {
  const opener = page.locator('.kpi .kpi__source .evidence-chip').first();
  const dialog = await app.openEvidenceDrawer(opener);
  return { dialog, opener };
}

const SURFACES: readonly Surface[] = [
  {
    name: 'evidence drawer',
    route: '/',
    open: openDrawerFromKpi,
    closeLabel: 'Close drawer',
    backdrop: 'outside',
    exit: true,
    audited: false,
  },
  {
    name: 'drawer recovery frame',
    route: '/',
    prepare: ({ mockApi, hygiene }) => {
      allowNamedErrorLines(hygiene);
      crashTheEvidenceDrawer(mockApi);
    },
    open: async (ctx) => {
      const opened = await openDrawerFromKpi(ctx);
      await expect(opened.dialog.locator('[data-error-boundary="drawer"]')).toBeVisible();
      return opened;
    },
    closeLabel: 'Close drawer',
    backdrop: 'outside',
    // The frame closes by resetting its boundary, which unmounts it.
    exit: false,
    audited: false,
  },
  {
    name: 'command palette',
    route: '/',
    open: async ({ app, page }) => {
      const opener = page.getByRole('banner').getByRole('button', { name: /^Open command palette/ });
      await opener.focus();
      await app.openCommandPalette();
      return { dialog: page.locator('dialog.cmdk[aria-label="Command palette"]'), opener };
    },
    closeLabel: null,
    backdrop: 'self',
    exit: true,
    audited: false,
  },
  {
    name: 'shortcut sheet',
    route: '/lead-queue',
    open: async ({ page }) => {
      const opener = page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Leads', exact: true });
      await opener.focus();
      await page.keyboard.press('Shift+?');
      const dialog = page.locator('dialog.cmdk:has([data-testid="shortcut-sheet"])');
      await expect(dialog).toHaveAttribute('open', '');
      return { dialog, opener };
    },
    closeLabel: 'Close keyboard shortcuts',
    backdrop: 'self',
    exit: false,
    audited: false,
  },
  {
    name: 'borrower proof drawer',
    route: `/borrower-360/${ID}`,
    open: async ({ page }) => {
      const opener = page.locator('#main-content').getByRole('button', { name: `Show scoring math for borrower ${ID}` });
      await opener.click();
      const dialog = page.locator('dialog.proof-drawer');
      await expect(dialog).toHaveClass(/is-open/);
      return { dialog, opener };
    },
    closeLabel: 'Close proof drawer',
    backdrop: 'outside',
    exit: true,
    audited: true,
  },
  {
    name: 'Genie answer proof',
    route: '/ask-genie',
    prepare: ({ mockApi }) => {
      registerGenieTurn(mockApi, { holdProgress: false });
    },
    open: async ({ page }) => {
      const main = page.locator('#main-content');
      const opener = main.getByRole('button', { name: 'Show proof' }).first();
      // Ask once per page: a reopen uses the answer already on screen.
      if ((await opener.count()) === 0) {
        await main.getByRole('textbox', { name: 'Ask Genie — question' }).fill('Which states have the most prime refi candidates?');
        await main.getByRole('button', { name: 'Ask Genie', exact: true }).click();
      }
      await expect(opener).toBeVisible({ timeout: 20_000 });
      await opener.click();
      const dialog = page.locator('dialog.genie-proof-drawer');
      await expect(dialog).toHaveClass(/is-open/);
      return { dialog, opener };
    },
    closeLabel: 'Close Genie proof',
    backdrop: 'outside',
    exit: true,
    audited: false,
  },
  {
    name: 'borrower offer mock',
    route: `/offer-orchestrator/${ID}`,
    open: async ({ page }) => {
      const opener = page.getByTestId('preview-borrower-offer');
      await opener.click();
      const dialog = page.locator('dialog.offer-mock');
      await expect(dialog).toHaveAttribute('open', '');
      return { dialog, opener };
    },
    closeLabel: 'Close prototype',
    backdrop: 'outside',
    exit: false,
    audited: false,
  },
];

const isModal = (dialog: Locator) => dialog.evaluate((el) => el.matches(':modal'));
const holdsFocus = (dialog: Locator) => dialog.evaluate((el) => el.contains(document.activeElement));

/** Closed: no open copy left (a dialog its parent unmounts on close has no copy at all). */
async function expectClosed(dialog: Locator): Promise<void> {
  await expect.poll(() => dialog.evaluateAll((els) => els.some((el) => el.hasAttribute('open')))).toBe(false);
}

/** Tab stops in the dialog, counted the way the trap does. */
function tabStops(dialog: Locator): Promise<number> {
  return dialog.evaluate((el) =>
    [...el.querySelectorAll<HTMLElement>('button:not([disabled]), [href], input:not([disabled]), textarea:not([disabled]), select:not([disabled]), summary, [tabindex]:not([tabindex="-1"])')]
      .filter((node) => node.closest('[inert]') === null && node.checkVisibility()).length,
  );
}

async function expectTabStaysInside(page: Page, dialog: Locator): Promise<void> {
  const presses = (await tabStops(dialog)) + 2;
  for (const key of ['Tab', 'Shift+Tab']) {
    for (let i = 0; i < presses; i += 1) {
      await page.keyboard.press(key);
      expect(await holdsFocus(dialog), `${key} #${i + 1} stays in the dialog`).toBe(true);
    }
  }
}

/** A hit test and a focus attempt on the page behind both land on the dialog. */
async function expectPageInert(dialog: Locator): Promise<void> {
  const probe = await dialog.evaluate((el) => {
    const main = document.getElementById('main-content');
    if (!main) throw new Error('no #main-content');
    const box = main.getBoundingClientRect();
    const hit = document.elementFromPoint(box.left + 24, box.top + 24);
    main.querySelector<HTMLElement>('h1[tabindex]')?.focus();
    return { hitIsPage: hit !== null && main.contains(hit), focusInDialog: el.contains(document.activeElement) };
  });
  expect(probe.hitIsPage, 'a point on the page hits the backdrop, not the page').toBe(false);
  expect(probe.focusInDialog, 'the page behind refuses focus').toBe(true);
}

async function closeFromInside(page: Page, dialog: Locator, closeLabel: string | null): Promise<void> {
  if (closeLabel) await dialog.getByRole('button', { name: closeLabel }).click();
  else await page.keyboard.press('Escape');
}

/**
 * A point on the surface's own box: for a panel dialog a point whose hit
 * target is the dialog element itself (its padding or border), else its
 * title; for the full-viewport palette layer, its panel's footer.
 */
/**
 * Let the dialog finish any entry motion before a point is measured: a drawer
 * still sliding in moves its box between the measurement and the click, so a
 * point measured "outside" can land inside its settled box (1 in ~80 under
 * load: CI run 36185829440 and a local 20x stress).
 */
async function settled(dialog: Locator): Promise<void> {
  await dialog.evaluate((el) =>
    Promise.all(el.getAnimations({ subtree: true }).map((animation) => animation.finished.catch(() => undefined))),
  );
}

async function ownPoint(dialog: Locator, mode: 'outside' | 'self'): Promise<{ x: number; y: number }> {
  await settled(dialog);
  return dialog.evaluate((el, which) => {
    // The footer, not the panel's centre: that is a command row, which runs.
    const panel = which === 'self' ? el.querySelector('.cmdk__footer') ?? el.querySelector('.cmdk__panel') : null;
    if (panel) {
      const r = panel.getBoundingClientRect();
      return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
    }
    const box = el.getBoundingClientRect();
    for (let fy = 0.02; fy < 1; fy += 0.04) {
      for (let fx = 0.02; fx < 1; fx += 0.04) {
        const x = box.left + box.width * fx;
        const y = box.top + box.height * fy;
        if (document.elementFromPoint(x, y) === el) return { x, y };
      }
    }
    const inner = el.querySelector('.drawer__title, .offer-mock__banner') ?? el.firstElementChild;
    const r = (inner ?? el).getBoundingClientRect();
    return { x: r.left + Math.min(8, r.width / 2), y: r.top + r.height / 2 };
  }, mode);
}

/** A point on the backdrop: outside a panel dialog's box, or on the full-viewport layer beside its panel. */
async function backdropPoint(dialog: Locator, mode: 'outside' | 'self'): Promise<{ x: number; y: number }> {
  await settled(dialog);
  return dialog.evaluate((el, which) => {
    if (which === 'self') return { x: 24, y: 24 };
    const box = el.getBoundingClientRect();
    return box.left > 120 ? { x: box.left - 60, y: box.top + 60 } : { x: 24, y: 24 };
  }, mode);
}

for (const surface of SURFACES) {
  test.describe(`${surface.name} (native modal dialog)`, () => {
    test('is modal: Tab stays inside, the page is inert, focus returns to the opener', async ({ app, page, mockApi, hygiene }) => {
      const ctx = { app, page, mockApi, hygiene };
      await page.setViewportSize(WIDE);
      surface.prepare?.(ctx);
      await app.gotoRoute(surface.route);
      const natural = markNaturalLoad(mockApi);
      const { dialog, opener } = await surface.open(ctx);
      expect(await isModal(dialog), 'showModal() put it in the top layer').toBe(true);
      expect(await dialog.evaluate((el) => el.hasAttribute('role') || el.hasAttribute('aria-modal'))).toBe(false);
      await expectTabStaysInside(page, dialog);
      await expectPageInert(dialog);
      await closeFromInside(page, dialog, surface.closeLabel);
      await expect(opener).toBeFocused();
      if (!surface.audited) expectNoAuditedReadSince(mockApi, natural, surface.name);
    });

    test('one Escape closes only it; a backdrop press closes it, a press on its own box does not', async ({ app, page, mockApi, hygiene }) => {
      const ctx = { app, page, mockApi, hygiene };
      await page.setViewportSize(WIDE);
      surface.prepare?.(ctx);
      await app.gotoRoute(surface.route);
      const genie = surface.name === 'evidence drawer' ? await app.openGenie() : null;
      let { dialog } = await surface.open(ctx);
      await page.keyboard.press('Escape');
      await expectClosed(dialog);
      if (genie) await expect(genie, 'Escape closed the drawer alone').toHaveClass(/is-open/);

      ({ dialog } = await surface.open(ctx));
      const own = await ownPoint(dialog, surface.backdrop);
      await page.mouse.click(own.x, own.y);
      expect(await isModal(dialog), 'a press on its own box keeps it open').toBe(true);
      const outside = await backdropPoint(dialog, surface.backdrop);
      await page.mouse.click(outside.x, outside.y);
      await expectClosed(dialog);
    });
  });
}

test.describe('focus falls back when the opener is gone', () => {
  test('the evidence drawer hands focus to the page h1 when its chip was removed', async ({ app, page }) => {
    await page.setViewportSize(WIDE);
    await app.gotoRoute('/');
    const { dialog, opener } = await openDrawerFromKpi({ app, page } as Ctx);
    await opener.evaluate((el) => el.closest('.kpi')?.remove());
    await dialog.getByRole('button', { name: 'Close drawer' }).click();
    await expect(page.locator('#main-content h1')).toBeFocused();
  });

  test('the approve review hands focus to the table region when its row control was re-rendered', async ({ app, mockApi, page }) => {
    registerDraftEcho(mockApi);
    registerHeldDecision(mockApi);
    await page.setViewportSize(WIDE);
    await app.gotoRoute('/lead-queue');
    const region = page.getByRole('region', { name: 'Ranked borrowers table scroll region' });
    await region.focus();
    await page.keyboard.press('j');
    await page.keyboard.press('a');
    const dialog = page.locator('dialog.lead-approve-dialog');
    await expect(dialog.getByTestId('lead-approve-review-confirm')).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    await expect(region).toBeFocused();
  });
});

async function sampleExit(page: Page, selector: string, closeLabel: string | null) {
  return page.evaluate(async ([sel, label]) => {
    const el = document.querySelector<HTMLElement>(sel);
    if (!el) throw new Error(`no ${sel}`);
    await Promise.all(el.getAnimations({ subtree: true }).map((animation) => animation.finished.catch(() => undefined)));
    if (label) el.querySelector<HTMLButtonElement>(`[aria-label="${label}"]`)?.click();
    else window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    await new Promise((resolve) => setTimeout(resolve, 0));
    const style = getComputedStyle(el);
    const running = el.getAnimations()
      .filter((animation): animation is CSSTransition => animation instanceof CSSTransition && animation.playState === 'running')
      .map((transition) => transition.transitionProperty);
    const justClosed = { open: el.hasAttribute('open'), connected: el.isConnected, display: style.display, running };
    await Promise.all(el.getAnimations().map((animation) => animation.finished.catch(() => undefined)));
    // A retained dialog unmounts once its exit ends; either way it is gone.
    await new Promise((resolve) => setTimeout(resolve, 200));
    return { justClosed, afterExit: { hidden: !el.isConnected || getComputedStyle(el).display === 'none' } };
  }, [selector, closeLabel] as const);
}

const EXITS = SURFACES.filter((surface) => surface.exit);

test.describe('overlay exits', () => {
  test.use({ contextOptions: { reducedMotion: 'no-preference' } });
  test.slow();
  for (const surface of EXITS) {
    test(`${surface.name}: closes at exit start and stays rendered while it leaves`, async ({ app, page, mockApi, hygiene }) => {
      const ctx = { app, page, mockApi, hygiene };
      await page.setViewportSize(WIDE);
      surface.prepare?.(ctx);
      await app.gotoRoute(surface.route);
      const { dialog } = await surface.open(ctx);
      const selector = await dialog.evaluate((el) => `dialog.${[...el.classList].filter((name) => name !== 'is-open').join('.')}`);
      const exit = await sampleExit(page, selector, surface.closeLabel);
      expect(exit.justClosed.open, 'close() ran at exit start').toBe(false);
      expect(exit.justClosed.display, 'still rendered mid-exit').not.toBe('none');
      expect(exit.justClosed.running, 'a slide or fade is running').toEqual(expect.arrayContaining([expect.stringMatching(/^(transform|opacity)$/)]));
      expect(exit.afterExit.hidden, 'hidden once the exit ends').toBe(true);
    });
  }
});

test.describe('overlay exits under reduced motion', () => {
  for (const surface of EXITS) {
    test(`${surface.name}: hides at once`, async ({ app, page, mockApi, hygiene }) => {
      const ctx = { app, page, mockApi, hygiene };
      await page.setViewportSize(WIDE);
      surface.prepare?.(ctx);
      await app.gotoRoute(surface.route);
      const { dialog } = await surface.open(ctx);
      const selector = await dialog.evaluate((el) => `dialog.${[...el.classList].filter((name) => name !== 'is-open').join('.')}`);
      const exit = await sampleExit(page, selector, surface.closeLabel);
      expect(exit.justClosed.open).toBe(false);
      // Hidden in the closing frame: display none, or (a retained dialog
      // with no exit to wait for) already unmounted.
      expect(!exit.justClosed.connected || exit.justClosed.display === 'none', 'hidden in the closing frame').toBe(true);
    });
  }
});

async function scrimToken(page: Page): Promise<string> {
  return page.evaluate(() => {
    const probe = document.createElement('div');
    probe.style.background = 'var(--surface-scrim)';
    document.body.appendChild(probe);
    const value = getComputedStyle(probe).backgroundColor;
    probe.remove();
    return value;
  });
}

test.describe('the backdrop is the prototype scrim', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`${theme}: ::backdrop paints --surface-scrim with blur, and drops the blur under reduced transparency`, async ({ app, browserName, page }) => {
      await page.setViewportSize(WIDE);
      await app.setTheme(theme);
      await app.gotoRoute('/');
      const { dialog } = await openDrawerFromKpi({ app, page } as Ctx);
      const read = () => dialog.evaluate((el) => {
        const style = getComputedStyle(el, '::backdrop');
        return { background: style.backgroundColor, blur: style.backdropFilter };
      });
      const painted = await read();
      expect(painted.background).toBe(await scrimToken(page));
      expect(painted.blur).toBe('blur(2px)');

      test.skip(browserName !== 'chromium', 'reduced transparency is emulated through a Chromium DevTools Protocol media feature');
      const cdp = await page.context().newCDPSession(page);
      await cdp.send('Emulation.setEmulatedMedia', {
        features: [
          { name: 'prefers-reduced-transparency', value: 'reduce' },
          { name: 'prefers-color-scheme', value: theme },
          { name: 'prefers-reduced-motion', value: 'reduce' },
        ],
      });
      expect((await read()).blur).toBe('none');
    });
  }
});

test.describe('axe inside the new dialogs', () => {
  for (const theme of FIXTURE_THEMES) {
    for (const name of ['borrower offer mock', 'Genie answer proof', 'drawer recovery frame']) {
      test(`${name} (${theme}) has no WCAG A/AA violation`, async ({ app, page, mockApi, hygiene }) => {
        const surface = SURFACES.find((candidate) => candidate.name === name);
        if (!surface) throw new Error(`unknown surface ${name}`);
        const ctx = { app, page, mockApi, hygiene };
        await page.setViewportSize(WIDE);
        await app.setTheme(theme);
        surface.prepare?.(ctx);
        await app.gotoRoute(surface.route);
        const { dialog } = await surface.open(ctx);
        await dialog.evaluate((el) => Promise.all(el.getAnimations({ subtree: true }).map((animation) => animation.finished.catch(() => undefined))));
        const selector = await dialog.evaluate((el) => `dialog.${[...el.classList].filter((cls) => cls !== 'is-open').join('.')}`);
        await expectAxeClean(page, { key: { route: 'overlays', state: name.replace(/\s+/g, '-') }, theme, known: {}, include: selector });
      });
    }
  }
});

test.describe('toasts over a modal', () => {
  test('a toast raised while the drawer is open lands in its live list, on top, and dismisses', async ({ app, mockApi, page }) => {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    mockApi.register('POST', '/api/sales/distribute', async ({ body }) => {
      await gate;
      const ids = (body as { borrower_ids: string[] }).borrower_ids;
      return json({
        assigned_count: ids.length,
        strategy: 'manual',
        assignments: ids.map((borrowerId, index) => ({
          assignment_id: `asg-${index}`,
          borrower_id: borrowerId,
          assigned_to_email: 'lo1@summit-mortgage.example',
          assigned_by: 'approver@summit-mortgage.example',
          assigned_at: '2026-07-14T15:00:00Z',
          strategy: 'manual',
        })),
        per_lo_counts: {},
        audit_event_id: 'audit-overlays-assign',
      });
    });
    await page.setViewportSize(WIDE);
    await app.gotoRoute('/lead-queue');
    const boxes = page.locator('table.tbl tbody [data-testid^="lead-select-B-"]');
    await boxes.nth(0).check();
    await boxes.nth(1).check();
    await page.getByRole('toolbar', { name: 'Bulk actions' }).getByRole('button', { name: /^Assign 2 selected leads/ }).click();

    const drawer = await app.openEvidenceDrawer(page.locator('.evidence-chip:visible').first());
    const liveList = drawer.locator('section.toast-region [role="status"][aria-live="polite"]');
    await expect(liveList, 'the region moved into the open dialog before the toast').toBeAttached();
    const listBefore = await liveList.elementHandle();
    release();

    const toast = liveList.locator('.toast', { hasText: '2 leads assigned' });
    await expect(toast).toBeVisible();
    expect(await liveList.evaluate((el, before) => el === before, listBefore), 'the same, pre-existing live list').toBe(true);
    const box = await toast.boundingBox();
    if (!box) throw new Error('the toast has no box');
    const hit = await page.evaluate(([x, y]) => document.elementFromPoint(x, y)?.closest('.toast') !== null, [box.x + box.width / 2, box.y + box.height / 2] as const);
    expect(hit, 'the toast is hit-testable at its centre, above the drawer').toBe(true);
    await toast.getByRole('button', { name: 'Dismiss notification' }).click();
    await expect(toast).toHaveCount(0);
    expect(await isModal(drawer), 'dismissing the toast keeps the drawer open').toBe(true);
  });

  test('a failure toast on screen when a modal opens stays visible, the same element, and is not re-announced', async ({ app, page }) => {
    await page.addInitScript(() => {
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: { writeText: () => Promise.reject(new DOMException('Denied (fixture)', 'NotAllowedError')) },
      });
    });
    await page.setViewportSize(WIDE);
    await app.gotoRoute('/portfolio-builder');
    await page.getByTestId('portfolio-copy-link').click();
    const failure = page.locator('section.toast-region .toast--error');
    await expect(failure).toHaveAttribute('role', 'alert');
    const before = await failure.elementHandle();

    await app.openCommandPalette();
    const palette = page.locator('dialog.cmdk[aria-label="Command palette"]');
    const carried = palette.locator('section.toast-region .toast--error');
    await expect(carried, 'the toast moved with its region into the palette, still shown').toBeVisible();
    expect(await carried.evaluate((el, previous) => el === previous, before), 'the same element, never re-created').toBe(true);
    // A moved role=alert would be an inserted alert, announced again.
    await expect(carried).not.toHaveAttribute('role', 'alert');
    await expect(palette.locator('[role="alert"]')).toHaveCount(0);
  });
});

test.describe('the evidence hover card over the approve review', () => {
  test('a chip inside the review dialog shows its card painted above the dialog', async ({ app, mockApi, page }) => {
    registerDraftEcho(mockApi);
    registerHeldDecision(mockApi);
    await page.setViewportSize(WIDE);
    await app.gotoRoute('/lead-queue');
    await page.getByRole('region', { name: 'Ranked borrowers table scroll region' }).focus();
    await page.keyboard.press('j');
    await page.keyboard.press('a');
    const dialog = page.locator('dialog.lead-approve-dialog');
    await expect(dialog.getByTestId('lead-approve-review-confirm')).toBeFocused();
    const chip = dialog.locator('.evidence-chip').first();
    await chip.focus();
    const card = page.locator('.evidence-hovercard:popover-open');
    await expect(card).toBeVisible();
    await card.evaluate((el) => Promise.all(el.getAnimations().map((animation) => animation.finished.catch(() => undefined))));
    // The card is aria-hidden, pointer-events: none and (outside the dialog)
    // inert, so no hit test can find it: compare what is PAINTED at its box
    // with and without it. Beneath the dialog, hiding it would change nothing.
    const box = await card.boundingBox();
    if (!box) throw new Error('the hover card has no box');
    const painted = await page.screenshot({ clip: box, animations: 'disabled' });
    await card.evaluate((el) => {
      (el as HTMLElement).style.visibility = 'hidden';
    });
    const without = await page.screenshot({ clip: box, animations: 'disabled' });
    expect(painted.equals(without), 'the card paints above the modal review').toBe(false);
  });
});

test.describe('the lazy drawer body', () => {
  test('with its chunk held, a chip opens the drawer with its title and a loading status first', async ({ app, page }) => {
    const release = await holdChunk(page, 'EvidenceDrawerBody');
    await page.setViewportSize(WIDE);
    await app.gotoRoute('/');
    const chip = page.locator('.kpi .kpi__source .evidence-chip').first();
    await chip.click();
    const drawer = app.evidenceDrawer();
    await expect(drawer).toHaveClass(/is-open/);
    await expect(drawer.locator('#evidence-drawer-title')).not.toHaveText('Data source');
    await expect(drawer.locator('.drawer__body [role="status"]')).toHaveText('Loading evidence…');
    release();
    await expect(drawer.locator('.drawer__body [role="status"]', { hasText: 'Loading evidence…' })).toHaveCount(0);
    await expect(drawer.locator('[role="tabpanel"]')).toBeVisible();
  });

  test('with its chunk retired, the drawer shows its recovery frame (Reload)', async ({ app, hygiene, page }) => {
    allowNamedErrorLines(hygiene);
    await retireChunk(page, 'EvidenceDrawerBody');
    await page.setViewportSize(WIDE);
    await app.gotoRoute('/');
    await spendStaleChunkReload(page);
    await page.locator('.kpi .kpi__source .evidence-chip').first().click();
    const surface = app.evidenceDrawer().locator('[data-error-boundary="drawer"][data-error-kind="chunk"]');
    await expect(surface).toBeVisible();
    await expect(surface.getByRole('button')).toHaveText(['Reload']);
    expect(await isModal(app.evidenceDrawer())).toBe(true);
  });
});

/** The idle-preloader rule of lib/prefetch.ts honours Save-Data (w4-delivery-boot item 7). */
const PREFETCH_SOURCE = fs.readFileSync(path.join(process.cwd(), 'src', 'lib', 'prefetch.ts'), 'utf8');
const IDLE_HONOURS_SAVE_DATA = /saveData/.test(PREFETCH_SOURCE);

function chunkRequests(page: Page, prefix: string): { count: () => number } {
  let count = 0;
  page.on('request', (request) => {
    if (new RegExp(`/assets/${prefix}-[^/]+\\.js$`).test(request.url())) count += 1;
  });
  return { count: () => count };
}

test.describe('preloads', () => {
  test('the palette and drawer-body chunks load on idle, before any ⌘K or chip', async ({ app, page }) => {
    const palette = chunkRequests(page, 'CommandPaletteDialog');
    const body = chunkRequests(page, 'EvidenceDrawerBody');
    await page.setViewportSize(WIDE);
    await app.gotoRoute('/');
    await expect.poll(() => palette.count(), { timeout: 15_000 }).toBeGreaterThan(0);
    await expect.poll(() => body.count(), { timeout: 15_000 }).toBeGreaterThan(0);
    await expect(page.locator('dialog.cmdk[open], dialog.drawer[open]')).toHaveCount(0);
  });

  test('Save-Data suppresses the idle preloads; a Control keydown and a chip hover still preload', async ({ app, page }) => {
    test.fixme(!IDLE_HONOURS_SAVE_DATA, 'needs the Save-Data rule in createIdlePreloader (w4-delivery-boot item 7, merged before this lane)');
    await page.addInitScript(() => {
      Object.defineProperty(navigator, 'connection', { configurable: true, value: { saveData: true } });
    });
    const palette = chunkRequests(page, 'CommandPaletteDialog');
    const body = chunkRequests(page, 'EvidenceDrawerBody');
    await page.setViewportSize(WIDE);
    await app.gotoRoute('/');
    await page.waitForTimeout(6_000);
    expect(palette.count(), 'no idle palette preload under Save-Data').toBe(0);
    expect(body.count(), 'no idle drawer-body preload under Save-Data').toBe(0);
    await page.keyboard.down('Control');
    await page.keyboard.up('Control');
    await expect.poll(() => palette.count()).toBeGreaterThan(0);
    await page.locator('.kpi .kpi__source .evidence-chip').first().hover();
    await expect.poll(() => body.count()).toBeGreaterThan(0);
  });
});

test.describe('the topbar below 1024px (responsive-06)', () => {
  test('at 960x600 the Genie launcher, Console and theme toggles and the account menu are visible, topmost and clickable', async ({ app, page }) => {
    await page.setViewportSize({ width: 960, height: 600 });
    await app.gotoRoute('/');
    const banner = page.getByRole('banner');
    const controls: Array<[string, Locator]> = [
      ['Genie launcher', page.locator('.genie__fab')],
      ['Console toggle', banner.getByRole('button', { name: 'Toggle console' })],
      ['theme toggle', banner.getByRole('button', { name: 'Toggle theme' })],
      ['account menu', banner.getByRole('button', { name: /^Account menu/ })],
    ];
    for (const [name, control] of controls) {
      await expect(control, `${name} is visible`).toBeVisible();
      const topmost = await control.evaluate((el) => {
        const box = el.getBoundingClientRect();
        const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
        return hit !== null && el.contains(hit);
      });
      expect(topmost, `${name} is topmost at its centre`).toBe(true);
      await control.click({ trial: true });
    }
    await expect(banner.locator('.topbar__pill-tenant')).toBeHidden();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, 'no horizontal page scroll').toBeLessThanOrEqual(0);
  });
});
