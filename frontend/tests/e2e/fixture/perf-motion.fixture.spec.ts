/**
 * Rendered-layer proofs for the wave-0 performance and motion quick wins
 * (audit bundle-07 hero geometry beside the route chunk, bundle-v1 hashed
 * lossless wordmark with an explicit box, motion-07 sparkline draw across
 * its whole window, motion-06 one-time reveals, print-safe reveals).
 *
 * The harness runs with `prefers-reduced-motion: reduce`; the describe
 * blocks that need real motion opt into `no-preference` explicitly.
 */
import type { Page } from '@playwright/test';
import { LEADS } from './data/borrowers';
import { expect, test } from './test';

const ALBERS_CHUNK = /\/assets\/states-albers-10m-[^/]+\.js$/;
const STATE_ROLLUPS = /\/api\/(v\d+\/)?geo\/state-rollups/;

function recordRequests(page: Page): string[] {
  const urls: string[] = [];
  page.on('request', (request) => {
    urls.push(request.url());
  });
  return urls;
}

test.describe('hero map geometry', () => {
  test('is warmed on Home hover before the map asks for its rollups, and never fetched twice', async ({ app, page }) => {
    const urls = recordRequests(page);
    await app.gotoRoute('/lead-queue');
    await page.waitForLoadState('networkidle');
    expect(urls.filter((url) => ALBERS_CHUNK.test(url))).toHaveLength(0);

    const hoverAt = urls.length;
    const geometry = page.waitForResponse((response) => ALBERS_CHUNK.test(response.url()));
    await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Home' }).hover();
    await geometry;
    expect(urls.slice(hoverAt).filter((url) => STATE_ROLLUPS.test(url)), 'a hover asks the API for nothing').toHaveLength(0);

    await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Home' }).click();
    await expect(page.locator('#main-content h1')).toBeVisible();
    await app.settle();
    const after = urls.slice(hoverAt);
    const albersIndex = after.findIndex((url) => ALBERS_CHUNK.test(url));
    const rollupIndex = after.findIndex((url) => STATE_ROLLUPS.test(url));
    expect(albersIndex).toBeGreaterThanOrEqual(0);
    expect(rollupIndex, 'Home asked for its state rollups').toBeGreaterThanOrEqual(0);
    expect(albersIndex, 'the geometry request precedes the rollup request').toBeLessThan(rollupIndex);
    expect(urls.filter((url) => ALBERS_CHUNK.test(url)), 'the geometry chunk was fetched once').toHaveLength(1);
    await expect(page.locator('.map-legend__value')).toBeVisible();
  });
});

test.describe('brand wordmark', () => {
  test('ships as the hashed WebP with an explicit box and shifts no layout', async ({ app, page }) => {
    // The not-found route is static: nothing above the footer loads later,
    // so any footer shift after first paint would be the image's own doing.
    await app.gotoRoute('/this-route-does-not-exist');
    await page.evaluate(() => {
      const win = window as Window & { __shifts?: Array<{ value: number; wordmark: boolean }> };
      win.__shifts = [];
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries() as Array<PerformanceEntry & { value: number; sources?: Array<{ node?: Node | null }> }>) {
          win.__shifts!.push({
            value: entry.value,
            wordmark: (entry.sources ?? []).some((source) =>
              source.node instanceof Element && source.node.closest('.page-footer') !== null,
            ),
          });
        }
      }).observe({ type: 'layout-shift', buffered: true });
    });
    const img = page.locator('img.entrada-wordmark-img').first();
    await expect(img).toBeVisible();
    await expect(img).toBeInViewport();
    await expect(img).toHaveAttribute('src', /\/assets\/entrada-wordmark-[^/]+\.webp$/);
    await expect(img).toHaveAttribute('width', /^\d+$/);
    await expect(img).toHaveAttribute('height', /^\d+$/);
    const box = await img.evaluate((node) => {
      const image = node as HTMLImageElement;
      const rect = image.getBoundingClientRect();
      return {
        complete: image.complete,
        naturalWidth: image.naturalWidth,
        naturalHeight: image.naturalHeight,
        attrWidth: Number(image.getAttribute('width')),
        attrHeight: Number(image.getAttribute('height')),
        renderedWidth: rect.width,
        renderedHeight: rect.height,
      };
    });
    expect(box.complete).toBe(true);
    expect(box.naturalWidth).toBeGreaterThan(0);
    expect(box.naturalHeight).toBeGreaterThan(0);
    // The reserved box has the asset's own proportions (width is rounded to
    // a whole pixel), so the image never stretches: derived from the decoded
    // asset rather than pinned, so a re-exported master still passes.
    const widthForAttrHeight = (box.naturalWidth / box.naturalHeight) * box.attrHeight;
    expect(Math.abs(widthForAttrHeight - box.attrWidth), 'the box matches the asset aspect ratio').toBeLessThanOrEqual(1);
    expect(Math.abs(box.renderedHeight - box.attrHeight)).toBeLessThanOrEqual(1);
    expect(Math.abs(box.renderedWidth - box.attrWidth)).toBeLessThanOrEqual(1);
    const shifts = await page.evaluate(
      () => (window as Window & { __shifts?: Array<{ value: number; wordmark: boolean }> }).__shifts ?? [],
    );
    expect(shifts.filter((shift) => shift.wordmark)).toEqual([]);
  });
});

test.describe('KPI sparkline draw', () => {
  test.describe('with motion allowed', () => {
    test.use({ contextOptions: { reducedMotion: 'no-preference' } });

    test('strokes in across its 700 ms window on first appearance and not on route re-entry', async ({ app, page }) => {
      await app.gotoRoute('/');
      const line = page.locator('.kpi .spark__line--draw').first();
      await expect(line).toBeVisible();
      const sample = await line.evaluate((node) => {
        const [animation] = node.getAnimations();
        if (!animation) return null;
        const timing = animation.effect?.getComputedTiming();
        animation.pause();
        // Sample the draw at ~400 ms after it started (80 ms delay + 320 ms in).
        animation.currentTime = 400;
        const at400 = Number.parseFloat(getComputedStyle(node).strokeDashoffset);
        animation.currentTime = 0;
        const atStart = Number.parseFloat(getComputedStyle(node).strokeDashoffset);
        animation.finish();
        const atEnd = Number.parseFloat(getComputedStyle(node).strokeDashoffset);
        return { duration: timing?.duration, delay: timing?.delay, at400, atStart, atEnd };
      });
      expect(sample, 'the stroke has a running draw animation').not.toBeNull();
      expect(sample!.duration).toBe(700);
      expect(sample!.delay).toBe(80);
      expect(sample!.atStart).toBe(1);
      expect(sample!.at400).toBeGreaterThan(0);
      expect(sample!.at400).toBeLessThan(1);
      expect(sample!.atEnd).toBe(0);

      await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Analytics' }).click();
      await expect(page.locator('#main-content h1')).toHaveText('Analytics');
      await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Home' }).click();
      await app.settle();
      await expect(page.locator('.kpi .spark__line').first()).toBeVisible();
      await expect(page.locator('.kpi .spark__line--draw')).toHaveCount(0);
    });
  });

  test('under reduced motion the line is fully drawn at once', async ({ app, page }) => {
    await app.gotoRoute('/');
    const line = page.locator('.kpi .spark__line--draw').first();
    await expect(line).toBeVisible();
    const state = await line.evaluate((node) => ({
      animations: node.getAnimations().length,
      animationName: getComputedStyle(node).animationName,
      dashoffset: Number.parseFloat(getComputedStyle(node).strokeDashoffset),
    }));
    expect(state.animations).toBe(0);
    expect(state.animationName).toBe('none');
    expect(state.dashoffset).toBe(0);
  });
});

test.describe('Borrower 360 trigger timeline reveal', () => {
  test.describe('with motion allowed', () => {
    test.use({ contextOptions: { reducedMotion: 'no-preference' } });

    test('fades once per session: the second dossier renders it visible at first paint', async ({ app, page }) => {
      await app.gotoRoute('/lead-queue');
      await app.expandFirstLeadRow();
      await page.locator('table.tbl tbody tr.tbl__expand').getByRole('link', { name: 'Open Borrower 360' }).click();
      await expect(page).toHaveURL(new RegExp(`/borrower-360/${LEADS[0].borrower_id}$`));
      await app.settle();
      const reveal = page.locator('.reveal-on-scroll', { hasText: 'Trigger timeline' });
      await expect(reveal).toHaveCount(1);
      // First appearance this session: hidden until it scrolls into view.
      await expect(reveal).not.toHaveClass(/is-visible/);
      await reveal.scrollIntoViewIfNeeded();
      await expect(reveal).toHaveClass(/is-visible/);

      // Record whether each later reveal node is inserted already visible.
      await page.evaluate(() => {
        const win = window as Window & { __reveals?: Array<{ visibleAtInsert: boolean }> };
        win.__reveals = [];
        new MutationObserver((records) => {
          for (const record of records) {
            for (const added of record.addedNodes) {
              if (!(added instanceof Element)) continue;
              const nodes = added.matches('.reveal-on-scroll') ? [added] : [...added.querySelectorAll('.reveal-on-scroll')];
              for (const node of nodes) win.__reveals!.push({ visibleAtInsert: node.classList.contains('is-visible') });
            }
          }
        }).observe(document.getElementById('main-content')!, { childList: true, subtree: true });
      });

      await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Leads' }).click();
      await app.settle();
      const secondRow = page.locator(`table.tbl tbody [aria-label="Toggle preview for lead ${LEADS[1].borrower_id}"]`);
      await secondRow.click();
      await page.locator('table.tbl tbody tr.tbl__expand').getByRole('link', { name: 'Open Borrower 360' }).click();
      await expect(page).toHaveURL(new RegExp(`/borrower-360/${LEADS[1].borrower_id}$`));
      await app.settle();
      const second = page.locator('.reveal-on-scroll', { hasText: 'Trigger timeline' });
      await expect(second).toHaveClass(/is-visible/);
      const inserted = await page.evaluate(
        () => (window as Window & { __reveals?: Array<{ visibleAtInsert: boolean }> }).__reveals ?? [],
      );
      expect(inserted.length).toBeGreaterThan(0);
      for (const entry of inserted) expect(entry.visibleAtInsert, 'the reveal was inserted already visible').toBe(true);
      expect(await second.evaluate((node) => getComputedStyle(node).opacity)).toBe('1');
    });

    test('is printed at full opacity even before it was scrolled into view', async ({ app, page }) => {
      await app.gotoRoute(`/borrower-360/${LEADS[0].borrower_id}`);
      const reveal = page.locator('.reveal-on-scroll', { hasText: 'Trigger timeline' });
      await expect(reveal).not.toHaveClass(/is-visible/);
      expect(await reveal.evaluate((node) => getComputedStyle(node).opacity)).toBe('0');
      await page.emulateMedia({ media: 'print' });
      expect(await reveal.evaluate((node) => getComputedStyle(node).opacity)).toBe('1');
      expect(await reveal.evaluate((node) => getComputedStyle(node).transform)).toBe('none');
      await page.emulateMedia({ media: 'screen' });
    });
  });
});
