/**
 * Rendered-layer proofs for the wave-1c lane "shell-wayfinding" (audit
 * 2026-09-21 shell-04, shell-06, shell-08, motion-01): the topbar identity
 * menu, linked breadcrumbs with the queue context, the Borrower 360 pager and
 * J/K keys, and the Console exit motion. Every assertion reads the built app's
 * DOM, geometry or computed style at 1440x900.
 */
import type { Locator, Page } from '@playwright/test';
import { expect, test, type FixtureTheme } from './test';
import { SIGNED_IN_APPROVER, sessionReply } from './data/shellWayfinding';

interface Box {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

async function boxOf(locator: Locator): Promise<Box> {
  const box = await locator.boundingBox();
  if (!box) throw new Error('element has no bounding box');
  return { left: box.x, top: box.y, right: box.x + box.width, bottom: box.y + box.height };
}

function overlaps(a: Box, b: Box): boolean {
  return a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
}

const THEMES: readonly FixtureTheme[] = ['dark', 'light'];

test.describe('identity menu (shell-06)', () => {
  test.beforeEach(({ mockApi }) => {
    mockApi.register('GET', '/api/session', () => sessionReply());
  });

  for (const theme of THEMES) {
    test(`${theme}: names the actor, opens a styled menu inside the viewport and keeps the topbar clear`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue');
      const banner = page.getByRole('banner');
      const trigger = banner.getByRole('button', { name: 'Account menu, signed in as Jane Doe' });
      await expect(trigger).toBeVisible();
      await expect(trigger).toHaveAttribute('aria-haspopup', 'menu');

      // The fourth icon button fits beside the pills: nothing in the actions
      // cluster overlaps the centred search, and the tenant name is whole.
      const search = await boxOf(banner.getByRole('search'));
      for (const control of await banner.locator('.topbar__actions > *').all()) {
        expect(overlaps(await boxOf(control), search), 'an action overlaps the search').toBe(false);
      }
      const tenant = banner.locator('.topbar__pill-tenant');
      const clipped = await tenant.evaluate((el) => el.scrollWidth > el.clientWidth);
      expect(clipped, 'the tenant name is not ellipsized at 1440').toBe(false);

      await trigger.focus();
      await page.keyboard.press('ArrowDown');
      const menu = page.getByRole('menu', { name: 'Account' });
      await expect(menu).toBeVisible();
      await expect(page.getByRole('menuitemradio', { name: 'Dark' })).toBeFocused();
      const panel = banner.locator('.identity-menu__panel');
      await expect(panel).toContainText(SIGNED_IN_APPROVER.actor_email ?? '');
      await expect(panel.locator('.chip')).toHaveText(['Administrator', 'Approver']);
      // The lazy stylesheet applied: the popup anchors to the trigger's end
      // edge and stays inside the viewport instead of hanging off it.
      const viewport = page.viewportSize();
      if (!viewport) throw new Error('viewport size is unset');
      const panelBox = await boxOf(panel);
      const triggerBox = await boxOf(trigger);
      expect(Math.abs(panelBox.right - triggerBox.right)).toBeLessThanOrEqual(1);
      expect(panelBox.left).toBeGreaterThanOrEqual(0);
      expect(panelBox.right).toBeLessThanOrEqual(viewport.width);
      expect(panelBox.top).toBeGreaterThan(triggerBox.bottom);
      await expect(panel).toHaveCSS('display', 'grid');

      await page.keyboard.press('Escape');
      await expect(menu).toBeHidden();
      await expect(trigger).toBeFocused();
    });
  }

  test('a theme picked in the menu repaints the app and the menu marks it', async ({ app, page }) => {
    await app.setTheme('dark');
    await app.gotoRoute('/');
    const trigger = page.getByRole('banner').getByRole('button', { name: /^Account menu/ });
    await trigger.click();
    await page.getByRole('menuitemradio', { name: 'Light' }).click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    await expect(trigger).toBeFocused();
    await trigger.click();
    await expect(page.getByRole('menuitemradio', { name: 'Light' })).toHaveAttribute('aria-checked', 'true');
  });

  /**
   * The popup drops into the top-right corner, where the docked Console
   * (--z-console) and the floating Genie panel (--z-genie) sit above the
   * resting topbar layer. `toBeVisible` cannot see occlusion, so this hit
   * tests the rendered page: every item's centre, and the panel's inset
   * corners, must be the popup itself. Each layout first proves the popup
   * really overlaps that overlay, so the hit test is never vacuous.
   */
  const OVERLAY_LAYOUTS = [
    { name: 'Console and Genie open', withConsole: true, covered: 'Workspace console', click: 'Light' },
    { name: 'Genie open in its default dock', withConsole: false, covered: 'Genie chat', click: 'Glossary' },
  ] as const;
  for (const layout of OVERLAY_LAYOUTS) {
    test(`the open popup paints above the shell overlays (${layout.name})`, async ({ app, page }) => {
      await app.setTheme('dark');
      await app.gotoRoute('/lead-queue');
      if (layout.withConsole) await app.openConsole();
      await app.openGenie();
      const topbar = page.getByRole('banner');
      const trigger = topbar.getByRole('button', { name: /^Account menu/ });
      await trigger.click();
      const menu = page.getByRole('menu', { name: 'Account' });
      await expect(menu).toBeVisible();
      const panel = topbar.locator('.identity-menu__panel');
      await expect(panel).toHaveCSS('display', 'grid');

      const overlay = layout.withConsole
        ? page.getByRole('complementary', { name: layout.covered })
        : page.getByRole('dialog', { name: layout.covered });
      expect(overlaps(await boxOf(panel), await boxOf(overlay)), `the popup lies over the ${layout.covered}`).toBe(true);

      const items = menu.locator('[role^="menuitem"]');
      await expect(items).toHaveCount(5);
      for (const item of await items.all()) {
        const onTop = await item.evaluate((el) => {
          const r = el.getBoundingClientRect();
          const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
          return hit !== null && el.contains(hit);
        });
        expect(onTop, `"${await item.innerText()}" is the topmost element at its centre`).toBe(true);
      }
      const cornersOnTop = await panel.evaluate((el) => {
        const r = el.getBoundingClientRect();
        const inset = 8;
        return [
          [r.left + inset, r.top + inset],
          [r.right - inset, r.top + inset],
          [r.left + inset, r.bottom - inset],
          [r.right - inset, r.bottom - inset],
        ].map(([x, y]) => {
          const hit = document.elementFromPoint(x, y);
          return hit !== null && el.contains(hit);
        });
      });
      expect(cornersOnTop, 'the popup is the topmost element at its inset corners').toEqual([true, true, true, true]);

      // A real pointer click passes Playwright's own hit test.
      await menu.getByRole(layout.click === 'Light' ? 'menuitemradio' : 'menuitem', { name: layout.click }).click({ timeout: 10_000 });
      if (layout.click === 'Light') {
        await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
      } else {
        await expect(page).toHaveURL(/\/glossary$/);
      }
      // The lift lasts only while the popup is open: the resting topbar is
      // back on its prototype layer, under the Console and the Genie panel.
      await expect(menu).toBeHidden();
      const layers = await page.evaluate(() => {
        const bar = document.querySelector('.topbar');
        return {
          topbar: bar ? getComputedStyle(bar).zIndex : '',
          token: getComputedStyle(document.documentElement).getPropertyValue('--z-topbar').trim(),
        };
      });
      expect(layers.topbar).toBe(layers.token);
    });
  }

  test('Keyboard shortcuts opens the ? sheet through its event; Glossary navigates', async ({ app, page }) => {
    await app.gotoRoute('/');
    await page.evaluate(() => {
      (window as unknown as { __shortcutEvents: number }).__shortcutEvents = 0;
      window.addEventListener('mip:open-shortcuts', () => {
        (window as unknown as { __shortcutEvents: number }).__shortcutEvents += 1;
      });
    });
    const trigger = page.getByRole('banner').getByRole('button', { name: /^Account menu/ });
    await trigger.click();
    await page.getByRole('menuitem', { name: 'Keyboard shortcuts' }).click();
    await expect.poll(() => page.evaluate(() => (window as unknown as { __shortcutEvents: number }).__shortcutEvents)).toBe(1);
    // Integrated with the queue lane's ShortcutOverlay, the event opens the sheet.
    const sheet = page.getByRole('dialog', { name: 'Keyboard shortcuts' });
    await expect(sheet).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(sheet).toHaveCount(0);
    await trigger.click();
    await page.getByRole('menuitem', { name: 'Glossary' }).click();
    await expect(page).toHaveURL(/\/glossary$/);
  });
});

/** Masked ids of the dossiers the app read (GET /api/borrowers/:id only). */
function dossierReads(calls: ReadonlyArray<{ method: string; path: string }>): string[] {
  return calls
    .filter((call) => call.method === 'GET')
    .map((call) => /^\/api\/borrowers\/(B-[0-9A-Z]{13})$/.exec(call.path)?.[1])
    .filter((id): id is string => Boolean(id));
}

/** The masked ids of the ranked rows, in the order the queue shows them. */
async function queueIds(page: Page): Promise<string[]> {
  const rows = page.locator('table.tbl tbody tr:not(.tbl__expand)');
  await expect(rows.first()).toBeVisible();
  const texts = await rows.allInnerTexts();
  return texts.map((text) => /B-[0-9A-Z]{13}/.exec(text)?.[0]).filter((id): id is string => Boolean(id));
}

async function openDossierFromQueue(page: Page, rowIndex: number): Promise<void> {
  const toggle = page.locator('table.tbl tbody [aria-expanded]').nth(rowIndex);
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await page.locator('tr.tbl__expand').getByRole('link', { name: 'Open Borrower 360' }).click();
}

test.describe('queue-to-dossier wayfinding (shell-04)', () => {
  for (const theme of THEMES) {
    test(`${theme}: linked crumbs name the queue and the borrower, and fit the topbar`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue?state=IL');
      const ids = await queueIds(page);
      expect(ids.length).toBe(3);
      await openDossierFromQueue(page, 1);
      await expect(page).toHaveURL(new RegExp(`/borrower-360/${ids[1]}$`));
      await app.settle();

      const crumbs = page.getByRole('banner').getByRole('navigation', { name: 'Breadcrumb' });
      const queueLink = crumbs.getByRole('link', { name: 'Lead Queue · IL' });
      // The published queue search carries the reader's place (wave 3,
      // shell-03): the row they opened the dossier from.
      await expect(queueLink).toHaveAttribute('href', `/lead-queue?state=IL&row=${ids[1]}`);
      const current = crumbs.locator('[aria-current="page"]');
      await expect(current).toHaveText(ids[1]);
      // The trail fits the left track: whole labels, clear of the search.
      const search = await boxOf(page.getByRole('banner').getByRole('search'));
      expect((await boxOf(crumbs)).right).toBeLessThanOrEqual(search.left);
      for (const crumb of [queueLink, current]) {
        const clipped = await crumb.evaluate((el) => el.scrollWidth > el.clientWidth);
        expect(clipped, `${await crumb.textContent()} is not ellipsized`).toBe(false);
      }
      // The crumb returns to the exact filtered queue, that row still open.
      await queueLink.click();
      await expect(page).toHaveURL(new RegExp(`/lead-queue\\?state=IL&row=${ids[1]}$`));
      await expect(page.locator(`tr.is-expanded[data-borrower-row="${ids[1]}"]`)).toBeVisible();
      await expect(page.locator('[aria-label^="STATE:"]').first()).toHaveAttribute('aria-label', /^STATE: IL\b/);
      expect(await queueIds(page)).toEqual(ids);
    });
  }

  test('the pager steps through the queue with J / K and never reads a neighbour ahead of time', async ({ app, page, mockApi }) => {
    await app.gotoRoute('/lead-queue?state=IL');
    const ids = await queueIds(page);
    await openDossierFromQueue(page, 1);
    await app.settle();

    const pager = page.getByRole('navigation', { name: 'Lead queue position' });
    await expect(pager).toContainText('2 of 3 ranked in IL');
    // Opening a dossier writes a VIEW_BORROWER audit row: only the one opened
    // was read, not the previous or next borrower.
    expect(dossierReads(mockApi.calls)).toEqual([ids[1]]);

    await page.keyboard.press('j');
    await expect(page).toHaveURL(new RegExp(`/borrower-360/${ids[2]}$`));
    await app.settle();
    await expect(pager).toContainText('3 of 3 ranked in IL');
    await expect(pager.getByRole('button', { name: 'Next' })).toBeDisabled();
    expect(dossierReads(mockApi.calls)).toEqual([ids[1], ids[2]]);
    await page.keyboard.press('k');
    await expect(page).toHaveURL(new RegExp(`/borrower-360/${ids[1]}$`));
    await app.settle();
    // Back on a dossier read moments ago: the cached read serves it, and
    // still nothing ahead of the reviewer (ids[0]) was read.
    expect(dossierReads(mockApi.calls)).not.toContain(ids[0]);
    await pager.getByRole('button', { name: 'Previous' }).click();
    await expect(page).toHaveURL(new RegExp(`/borrower-360/${ids[0]}$`));
    await app.settle();
    await expect(pager).toContainText('1 of 3 ranked in IL');
    expect([...new Set(dossierReads(mockApi.calls))]).toEqual([ids[1], ids[2], ids[0]]);

    // Scoped like the queue's row shortcuts: the keys are live only while
    // focus is inside the dossier's <main> (WCAG 2.1.4). J on a topbar
    // control, which is no text field, does not page. The pager navigates
    // inside the keydown (history.pushState is synchronous), so the URL read
    // straight after the key is final, not a race; the same key from the page
    // heading then moves exactly one borrower.
    await page.getByRole('banner').getByRole('button', { name: 'Toggle console' }).focus();
    await page.keyboard.press('j');
    expect(page.url(), 'J on a topbar control stays put').toMatch(new RegExp(`/borrower-360/${ids[0]}$`));
    await page.locator('main#main-content h1').focus();
    await page.keyboard.press('j');
    await expect(page).toHaveURL(new RegExp(`/borrower-360/${ids[1]}$`));
    await app.settle();
    await expect(pager).toContainText('2 of 3 ranked in IL');

    // J typed into the topbar search is text.
    const searchBox = page.getByRole('combobox', { name: 'Search borrowers' });
    await searchBox.focus();
    await page.keyboard.press('j');
    await expect(searchBox).toHaveValue('j');
    await expect(page).toHaveURL(new RegExp(`/borrower-360/${ids[1]}$`));
    // The crumb still returns to the exact filtered queue after paging.
    await expect(page.getByRole('navigation', { name: 'Breadcrumb' }).getByRole('link', { name: 'Lead Queue · IL' }))
      .toHaveAttribute('href', `/lead-queue?state=IL&row=${ids[1]}`);
  });

  test('a dossier opened by URL keeps its queue through the session fallback; Offer and asset crumbs link to real indexes', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue?state=IL');
    const ids = await queueIds(page);
    // A fresh document with no history state: the stored queue lists it.
    await app.gotoRoute(`/borrower-360/${ids[2]}`);
    const crumbs = page.getByRole('navigation', { name: 'Breadcrumb' });
    await expect(crumbs.getByRole('link', { name: 'Lead Queue · IL' })).toHaveAttribute('href', '/lead-queue?state=IL');
    await expect(page.getByRole('navigation', { name: 'Lead queue position' })).toContainText('3 of 3 ranked in IL');

    await page.getByRole('link', { name: 'Build outreach draft' }).click();
    await expect(page).toHaveURL(new RegExp(`/offer-orchestrator/${ids[2]}$`));
    await app.settle();
    await expect(crumbs.getByRole('listitem')).toHaveText([/Lead Queue · IL/, new RegExp(ids[2]), /Offer Orchestrator/]);
    await expect(crumbs.locator('[aria-current="page"]')).toHaveText('Offer Orchestrator');
    await crumbs.getByRole('link', { name: ids[2] }).click();
    await expect(page).toHaveURL(new RegExp(`/borrower-360/${ids[2]}$`));
    await expect(crumbs.locator('[aria-current="page"]')).toHaveText(ids[2]);
    await expect(page.getByRole('navigation', { name: 'Lead queue position' })).toContainText('3 of 3');

    await app.gotoRoute('/data-estate/assets/borrower_360');
    const assetCrumbs = page.getByRole('navigation', { name: 'Breadcrumb' });
    await expect(assetCrumbs.getByRole('link', { name: 'Data estate' })).toHaveAttribute('href', '/admin-config');
    await expect(assetCrumbs.locator('[aria-current="page"]')).toHaveText('Governed asset');
  });
});

interface ConsoleSample {
  inDom: boolean;
  open: boolean;
  display: string;
  opacity: number;
  transitions: Array<{ property: string; playState: string; duration: number; easing: string }>;
}

/**
 * Open the Console from the topbar, wait for its ENTRY to finish (a close
 * that lands before the entry moved the panel has nothing to transition,
 * which says nothing about the exit), close it from inside the page and read
 * the panel one macrotask later, then once every transition on it is done.
 * Mirrors closeAndSampleExit in console-layout.fixture.spec.ts; sampling
 * in-page keeps the first read inside the exit window on a loaded runner.
 */
async function closeConsoleAndSample(page: Page) {
  return page.evaluate(async () => {
    const toggle = document.querySelector<HTMLButtonElement>('header [aria-label="Toggle console"]');
    if (!toggle) throw new Error('no Console toggle in the topbar');
    toggle.click();
    let panel: HTMLElement | null = null;
    for (let attempt = 0; attempt < 200 && !panel?.querySelector('.tweaks__body'); attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 10));
      panel = document.querySelector<HTMLElement>('aside.tweaks.is-open');
    }
    if (!panel) throw new Error('the Console did not open');
    const entry = panel.getAnimations().filter((a): a is CSSTransition => a instanceof CSSTransition);
    const entryDuration = Math.max(0, ...entry.map((t) => Number(t.effect?.getTiming().duration ?? 0)));
    await Promise.all(panel.getAnimations().map((a) => a.finished.catch(() => undefined)));
    const read = (element: HTMLElement): ConsoleSample => {
      const style = getComputedStyle(element);
      return {
        inDom: element.isConnected,
        open: element.classList.contains('is-open'),
        display: style.display,
        opacity: Number(style.opacity),
        transitions: element
          .getAnimations()
          .filter((a): a is CSSTransition => a instanceof CSSTransition)
          .map((t) => ({
            property: t.transitionProperty,
            playState: t.playState,
            duration: Number(t.effect?.getTiming().duration ?? 0),
            easing: String(t.effect?.getTiming().easing ?? ''),
          })),
      };
    };
    const close = panel.querySelector<HTMLButtonElement>('[aria-label="Close console"]');
    if (!close) throw new Error('no Close console control');
    close.click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    const justClosed = read(panel);
    const inert = panel.inert;
    // Whatever element holds the landmark at that moment: the closing panel
    // with motion, the empty placeholder once there is nothing to wait for.
    const landmarkNow = document.getElementById('workspace-console');
    const landmarkJustClosed = landmarkNow ? read(landmarkNow) : null;
    await Promise.all(panel.getAnimations().map((a) => a.finished.catch(() => undefined)));
    await new Promise((resolve) => setTimeout(resolve, 50));
    const landmark = document.getElementById('workspace-console');
    return {
      entryDuration,
      justClosed,
      inert,
      landmarkJustClosed,
      afterExit: landmark ? read(landmark) : null,
      samePanelAfterExit: landmark === panel,
    };
  });
}

test.describe('Console motion (motion-01)', () => {
  test.use({ contextOptions: { reducedMotion: 'no-preference' }, traceScreenshots: false });
  test.slow();

  test('the Console fades out on the ease-in exit curve, faster than it came in, before it leaves', async ({ app, page }) => {
    await app.gotoRoute('/');
    const exit = await closeConsoleAndSample(page);
    const sample: ConsoleSample = exit.justClosed;
    expect(sample.inDom, 'the closing Console is still the mounted panel').toBe(true);
    expect(sample.open).toBe(false);
    expect(sample.display, 'allow-discrete holds display through the exit').toBe('flex');
    expect(exit.inert, 'a closing Console takes no focus or clicks').toBe(true);
    const fade = sample.transitions.find((t) => t.property === 'opacity');
    expect(fade?.playState, 'the fade-out is running').toBe('running');
    expect(fade?.easing).toBe('cubic-bezier(0.4, 0, 1, 1)');
    expect(exit.entryDuration).toBeGreaterThan(0);
    // Exit ~0.6x the entry (--dur-fast 120ms vs --dur-base 200ms).
    expect((fade?.duration ?? 0) / exit.entryDuration).toBeCloseTo(0.6, 1);
    expect(exit.afterExit?.display).toBe('none');
    await expect(page.getByRole('complementary', { name: 'Workspace console', includeHidden: true })).toBeHidden();
  });
});

test.describe('Console width (motion-01 follow-up)', () => {
  test('the .main gutter and the panel both follow --console-w, at 1440 and at the 2560 width step', async ({ app, page }) => {
    for (const viewport of [{ width: 1440, height: 900 }, { width: 2560, height: 1440 }]) {
      await page.setViewportSize(viewport);
      await app.gotoRoute('/');
      const panel = await app.openConsole();
      const measured = await page.evaluate(() => {
        const root = getComputedStyle(document.documentElement);
        const main = document.querySelector<HTMLElement>('main#main-content');
        const aside = document.getElementById('workspace-console');
        if (!main || !aside) throw new Error('shell landmarks missing');
        return {
          consoleW: parseFloat(root.getPropertyValue('--console-w')),
          sp6: parseFloat(root.getPropertyValue('--sp-6')),
          gutter: parseFloat(getComputedStyle(main).paddingRight),
          panelWidth: aside.getBoundingClientRect().width,
        };
      });
      expect(measured.consoleW).toBe(viewport.width >= 2560 ? 340 : 300);
      expect(measured.panelWidth).toBeCloseTo(measured.consoleW, 0);
      expect(measured.gutter, `${viewport.width}px gutter`).toBeCloseTo(measured.consoleW + measured.sp6, 0);
      // Content in <main> ends left of the docked panel.
      const mainBox = await boxOf(page.locator('main#main-content'));
      expect(mainBox.right - measured.gutter).toBeLessThanOrEqual((await boxOf(panel)).left);
    }
  });
});

test.describe('Console motion under reduced motion (motion-01)', () => {
  test('closing hides the Console at once', async ({ app, page }) => {
    await app.gotoRoute('/');
    const exit = await closeConsoleAndSample(page);
    // One macrotask after the click the landmark is already gone from view:
    // nothing held the panel for an exit the user asked not to see.
    expect(exit.landmarkJustClosed?.open).toBe(false);
    expect(exit.landmarkJustClosed?.display, 'hidden in the closing task').toBe('none');
    for (const transition of exit.landmarkJustClosed?.transitions ?? []) {
      expect(transition.duration, `${transition.property} is instant`).toBeLessThanOrEqual(1);
    }
    expect(exit.afterExit?.display).toBe('none');
  });
});
