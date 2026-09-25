/**
 * Route View Transitions, phase 1 (2026-09-21 audit stack-04, motion-03,
 * runtime-10, css-10, shell-10), proven in the production build at
 * 1440x900 through a probe on document.startViewTransition
 * (./viewTransitionProbe). app.tsx keys a <ViewTransition> by pathname;
 * app.transitions.css animates it.
 *
 *  a. a nav click makes exactly one call; its ::view-transition old / new
 *     animations end within 250 ms; no CSS animation runs on the wrapper;
 *  b. a same-path ?query navigation (an Analytics tab) makes none;
 *  c. under reduced motion (the harness default) nothing calls it;
 *  d. a navigation whose chunk is still loading holds the old page with no
 *     call; releasing the chunk makes exactly one and paints the new page;
 *  e. a page scrolled 1,500+ px resets to 0 before the new snapshot (at
 *     updateCallbackDone) and no group keyframe slides; Back (rendered
 *     synchronously by React, so with no transition) has the offset restored
 *     in its first frame;
 *  f. with the Genie panel open and the transition stretched to 3 s, the
 *     composer takes a click and typing mid-transition, and the shell (the
 *     panel included) switches at once instead of fading as a snapshot;
 *  g. the hygiene gate sees no page error, console error or client_error
 *     across all of it (a duplicate view-transition-name would be one);
 *  h. a cold load with the route chunk held: the fallback wrapper runs no
 *     CSS animation, and at most one entrance runs after release.
 *
 * No case reads a proof drawer, a draft or a borrower dossier: the routes
 * load naturally and nothing here writes an audit row.
 */
import type { Page, Route } from '@playwright/test';
import {
  installViewTransitionProbe,
  newRouteAnimations,
  readViewTransitions,
  waitForViewTransitionsToFinish,
  type ViewTransitionLog,
} from './viewTransitionProbe';
import { expect, test } from './test';

const MOTION_CEILING_MS = 250;
const GLOSSARY_CHUNK = /\/assets\/glossary-[^/]+\.js$/;

function nav(page: Page) {
  return page.getByRole('navigation', { name: 'Main navigation' });
}

/** Hold every request for one route chunk until `release()`. */
async function gateChunk(page: Page, chunk: RegExp): Promise<{ release: () => void }> {
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(chunk, async (route: Route) => {
    await gate;
    await route.continue();
  });
  return { release: () => release() };
}

function newCalls(after: ViewTransitionLog, before: ViewTransitionLog): number {
  return after.calls - before.calls;
}

function paintedRoutePath(page: Page): Promise<string | null> {
  return page.locator('#main-content .route-transition[data-route-path]').getAttribute('data-route-path');
}

test.use({ viewport: { width: 1440, height: 900 } });

test.describe('with motion allowed', () => {
  // Real motion; skip the trace screencast (see test.ts traceScreenshots).
  test.use({ contextOptions: { reducedMotion: 'no-preference' }, traceScreenshots: false });

  test.beforeEach(async ({ page }) => {
    await installViewTransitionProbe(page);
  });

  test('(a) a nav click makes one View Transition whose animations end within 250 ms', async ({ app, page }) => {
    await app.gotoRoute('/');
    await waitForViewTransitionsToFinish(page);
    const before = await readViewTransitions(page);
    expect(before.supported, 'Chromium ships same-document View Transitions').toBe(true);

    await nav(page).getByRole('link', { name: 'Glossary' }).click();
    await app.settle();
    const log = await waitForViewTransitionsToFinish(page);
    expect(newCalls(log, before)).toBe(1);
    const animations = log.transitions.at(-1)?.ready ?? [];
    const pseudos = animations.map((animation) => animation.pseudo);
    expect(pseudos.some((pseudo) => pseudo.startsWith('::view-transition-old(')), 'the old page fades out').toBe(true);
    expect(pseudos.some((pseudo) => pseudo.startsWith('::view-transition-new(')), 'the new page fades in').toBe(true);
    for (const animation of animations) {
      expect(animation.endMs, `${animation.pseudo} ends within ${MOTION_CEILING_MS} ms`).toBeLessThanOrEqual(MOTION_CEILING_MS);
    }
    expect(newRouteAnimations(log, before), 'route-in never doubles the View Transition').toEqual([]);
    await expect(page.locator('#main-content h1')).toHaveText('Mortgage intelligence glossary');
  });

  test('(b) a same-path ?query navigation makes none', async ({ app, page }) => {
    await app.gotoRoute('/analytics');
    await waitForViewTransitionsToFinish(page);
    const before = await readViewTransitions(page);
    await page.getByRole('tablist', { name: 'Analytics views' }).getByRole('tab', { name: 'Geography' }).click();
    await expect(page).toHaveURL(/[?&]view=geography(&|$)/);
    await app.settle();
    expect(newCalls(await readViewTransitions(page), before)).toBe(0);
  });

  test('(d) a held chunk keeps the old page with no call; its release makes one and paints the new page', async ({ app, page }) => {
    const chunk = await gateChunk(page, GLOSSARY_CHUNK);
    await app.gotoRoute('/');
    await waitForViewTransitionsToFinish(page);
    const before = await readViewTransitions(page);
    const homeHeading = await page.locator('#main-content h1').textContent();

    await nav(page).getByRole('link', { name: 'Glossary' }).click();
    await expect(page).toHaveURL(/\/glossary$/);
    // Held for a while: the old route is still the painted one.
    await page.waitForTimeout(400);
    expect(await paintedRoutePath(page)).toBe('/');
    await expect(page.locator('#main-content h1')).toHaveText(homeHeading ?? '');
    expect(newCalls(await readViewTransitions(page), before), 'nothing starts while the chunk is held').toBe(0);

    chunk.release();
    await expect(page.locator('#main-content h1')).toHaveText('Mortgage intelligence glossary');
    expect(await paintedRoutePath(page)).toBe('/glossary');
    const log = await waitForViewTransitionsToFinish(page);
    expect(newCalls(log, before)).toBe(1);
  });

  // The brief named the Lead Queue; with the fixture rows it scrolls only
  // 545 px at 1440x900, so the tallest static route stands in for it.
  test('(e) a page scrolled 1,500+ px resets before the new snapshot, never slides, and Back restores before its own', async ({ app, page }) => {
    await app.gotoRoute('/glossary');
    await waitForViewTransitionsToFinish(page);
    const main = page.locator('#main-content');
    const scrolled = await main.evaluate((node) => {
      node.scrollTop = 1600;
      return node.scrollTop;
    });
    expect(scrolled, 'the page scrolls at least 1,500 px').toBeGreaterThanOrEqual(1500);
    const before = await readViewTransitions(page);

    await nav(page).getByRole('link', { name: 'Analytics' }).click();
    await app.settle();
    let log = await waitForViewTransitionsToFinish(page);
    expect(newCalls(log, before)).toBe(1);
    const forward = log.transitions.at(-1);
    expect(forward?.updateDone, 'the new snapshot is taken at the reset offset').toEqual({ scrollTop: 0, paintedPath: '/analytics' });
    for (const animation of forward?.ready ?? []) {
      expect(animation.maxShiftPx, `${animation.pseudo} slides`).toBeLessThanOrEqual(8);
    }

    // Back: React 19 renders a transition started inside `popstate`
    // synchronously (entangled with the sync lane, so the browser can restore
    // scroll), and a sync render starts no View Transition. The restored
    // offset must then already be in place in the first frame after it.
    await page.evaluate(() => {
      const win = window as Window & { __firstFrameAfterPop?: { scrollTop: number; paintedPath: string | null } };
      window.addEventListener('popstate', () => {
        requestAnimationFrame(() => {
          const main = document.querySelector('#main-content');
          win.__firstFrameAfterPop = {
            scrollTop: main instanceof HTMLElement ? main.scrollTop : -1,
            paintedPath: main?.querySelector('.route-transition[data-route-path]')?.getAttribute('data-route-path') ?? null,
          };
        });
      }, { once: true });
    });
    await page.goBack();
    await app.settle();
    log = await waitForViewTransitionsToFinish(page);
    expect(newCalls(log, before), 'Back renders synchronously, with no View Transition').toBe(1);
    const firstFrame = await page.evaluate(
      () => (window as Window & { __firstFrameAfterPop?: { scrollTop: number; paintedPath: string | null } }).__firstFrameAfterPop,
    );
    expect(firstFrame, 'Back restores the offset before its first frame').toEqual({ scrollTop: scrolled, paintedPath: '/glossary' });
  });

  test('(f) mid-transition, the open Genie composer takes a click and typing, and the shell is never faded', async ({ app, page }) => {
    await app.gotoRoute('/');
    await waitForViewTransitionsToFinish(page);
    const dialog = await app.openGenie();
    const composer = dialog.getByRole('textbox', { name: 'Ask Genie' });
    // Stretch every transition to 3 s (a constructed sheet: the production
    // CSP refuses an injected <style>).
    await page.evaluate(() => {
      const sheet = new CSSStyleSheet();
      sheet.replaceSync(
        '::view-transition-group(*), ::view-transition-old(*), ::view-transition-new(*) { animation-duration: 3s !important; }',
      );
      document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
    });
    const before = await readViewTransitions(page);

    await nav(page).getByRole('link', { name: 'Analytics' }).click();
    await page.waitForFunction(
      (count) => {
        const log = (window as Window & { __mipViewTransitions?: ViewTransitionLog }).__mipViewTransitions;
        const last = log?.transitions[count];
        return Boolean(last && last.ready !== null && !last.finished);
      },
      before.transitions.length,
    );
    // A real click (Playwright waits until the composer receives it): it
    // must land while the 3 s transition still runs.
    await composer.click();
    await page.keyboard.type('refi pull');
    const during = await readViewTransitions(page);
    expect(during.transitions.at(-1)?.finished, 'still mid-transition').toBe(false);
    await expect(composer).toBeFocused();
    await expect(composer).toHaveValue('refi pull');
    const pseudos = (during.transitions.at(-1)?.ready ?? []).map((animation) => animation.pseudo);
    // The route content cross-fades; the root (the shell and the open panel)
    // switches at once, so a streaming answer is never a fading snapshot.
    expect(pseudos.some((pseudo) => pseudo.startsWith('::view-transition-new(') && !pseudo.endsWith('(root)'))).toBe(true);
    expect(pseudos.filter((pseudo) => /^::view-transition-(?:old|new)\(root\)$/.test(pseudo)), 'the shell never fades').toEqual([]);
    await waitForViewTransitionsToFinish(page);
    expect(newCalls(await readViewTransitions(page), before)).toBe(1);
  });

  test('(h) a cold load with the chunk held: the fallback never animates, and one entrance at most', async ({ page }) => {
    const chunk = await gateChunk(page, GLOSSARY_CHUNK);
    await page.goto('/glossary', { waitUntil: 'domcontentloaded' });
    const fallback = page.locator('#main-content .route-transition--fallback');
    await expect(fallback).toHaveCount(1);
    await expect(fallback.locator('[data-route-fallback]')).toBeVisible();
    const held = await readViewTransitions(page);
    expect(newRouteAnimations(held), 'the fallback wrapper runs no CSS animation').toEqual([]);

    chunk.release();
    await expect(page.locator('#main-content h1')).toHaveText('Mortgage intelligence glossary');
    const log = await waitForViewTransitionsToFinish(page);
    expect(newCalls(log, held), 'the reveal makes one View Transition at most').toBeLessThanOrEqual(1);
    const entrances = log.transitions
      .slice(held.transitions.length)
      .flatMap((record) => record.ready ?? [])
      .filter((animation) => animation.pseudo.startsWith('::view-transition-new(') && !animation.pseudo.endsWith('(root)'));
    expect(entrances.length, 'one route entrance').toBeLessThanOrEqual(1);
    expect(newRouteAnimations(log), 'route-in does not add a second').toEqual([]);
  });
});

test.describe('theme cross-fade (lib/themeTransition)', () => {
  test.describe('with motion allowed', () => {
    test.use({ contextOptions: { reducedMotion: 'no-preference' }, traceScreenshots: false });

    for (const [from, to] of [['dark', 'light'], ['light', 'dark']] as const) {
      test(`the Topbar toggle cross-fades ${from} to ${to} in one transition within 250 ms`, async ({ app, page }) => {
        await installViewTransitionProbe(page);
        await app.setTheme(from);
        await app.gotoRoute('/');
        await waitForViewTransitionsToFinish(page);
        const before = await readViewTransitions(page);

        await page.getByRole('banner').getByRole('button', { name: 'Toggle theme' }).click();
        await expect(page.locator('html')).toHaveAttribute('data-theme', to);
        const log = await waitForViewTransitionsToFinish(page);
        expect(newCalls(log, before)).toBe(1);
        const root = (log.transitions.at(-1)?.ready ?? []).filter((animation) => /\(root\)$/.test(animation.pseudo) && !animation.pseudo.startsWith('::view-transition-group'));
        // Each image runs the UA fade plus its blend-mode animation.
        expect([...new Set(root.map((animation) => animation.pseudo))].sort(), 'the whole page cross-fades').toEqual([
          '::view-transition-new(root)',
          '::view-transition-old(root)',
        ]);
        for (const animation of root) expect(animation.endMs, animation.pseudo).toBeLessThanOrEqual(MOTION_CEILING_MS);
        await expect(page.locator('html'), 'the switching flag clears when it ends').not.toHaveAttribute('data-theme-switching', /.*/);
      });
    }
  });

  test('under reduced motion the toggle switches with no transition', async ({ app, page }) => {
    await installViewTransitionProbe(page);
    await app.setTheme('dark');
    await app.gotoRoute('/');
    await page.getByRole('banner').getByRole('button', { name: 'Toggle theme' }).click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    expect((await readViewTransitions(page)).calls).toBe(0);
  });
});

test('(c) under reduced motion nothing calls startViewTransition, on a cold load or a navigation', async ({ app, page }) => {
  await installViewTransitionProbe(page);
  await app.gotoRoute('/');
  await nav(page).getByRole('link', { name: 'Glossary' }).click();
  await app.settle();
  await page.goBack();
  await app.settle();
  const log = await readViewTransitions(page);
  expect(log.supported).toBe(true);
  expect(log.calls).toBe(0);
  expect(newRouteAnimations(log)).toEqual([]);
});
