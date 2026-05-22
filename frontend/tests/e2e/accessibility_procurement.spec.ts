import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

const LIVE = process.env.E2E_LIVE === '1';
test.skip(!LIVE, 'Set E2E_LIVE=1 to run procurement accessibility canaries.');

const APP_URL = process.env.MIP_APP_URL || 'http://127.0.0.1:5173';
const API_URL = process.env.MIP_API_URL || APP_URL.replace(':5173', ':8000');
const BEARER = process.env.MIP_BEARER_TOKEN || process.env.DATABRICKS_TOKEN || '';
const AUTH_HEADERS: Record<string, string> = BEARER ? { Authorization: `Bearer ${BEARER}` } : {};

test.use({ baseURL: APP_URL, extraHTTPHeaders: AUTH_HEADERS });

const CORE_ROUTES = ['/', '/lead-queue', '/segment-intelligence', '/ask-genie', '/admin-config'] as const;
const BORROWER_DETAIL_ROUTE_PREFIXES = ['/borrower-360', '/offer-orchestrator'] as const;

async function fetchFirstLeadId(request: APIRequestContext): Promise<string> {
  const resp = await request.get(`${API_URL}/api/leads?limit=1`, { headers: AUTH_HEADERS });
  expect(resp.status(), 'GET /api/leads returned non-200').toBe(200);
  const rows = (await resp.json()) as Array<{ borrower_id?: string }>;
  const id = rows[0]?.borrower_id;
  expect(id, 'need a live borrower id for Borrower 360 touch-target coverage').toBeTruthy();
  return id!;
}

function parseAriaRowCount(raw: string | null): number {
  expect(raw, 'Lead Queue table must expose aria-rowcount').not.toBeNull();
  expect(raw, 'aria-rowcount must be an integer string').toMatch(/^\d+$/);
  const parsed = Number(raw);
  expect(Number.isSafeInteger(parsed), 'aria-rowcount must parse to a safe integer').toBe(true);
  expect(parsed, 'aria-rowcount must include at least the header row').toBeGreaterThanOrEqual(1);
  return parsed;
}

async function skipOnlyWhenLeadQueueIsExplicitlyEmpty(page: Page): Promise<void> {
  const bodyText = await page.locator('body').innerText();
  const explicitEmpty =
    /No leads match this filter/i.test(bodyText) ||
    /Showing 0 (ranked borrowers|of 0 total matching filters)/i.test(bodyText) ||
    /Top 0 ranked borrowers/i.test(bodyText);
  if (explicitEmpty) {
    test.skip(true, 'Lead Queue explicitly returned zero live rows; row-level a11y checks require lead data.');
  }
}

test.describe('procurement accessibility canaries', () => {
  test('skip link moves keyboard focus into the main landmark @a11y', async ({ page }) => {
    await page.goto('/');
    await page.keyboard.press('Tab');
    await expect(page.locator('.sr-skip-link').first()).toBeFocused();
    const skipLinkBox = await page.locator('.sr-skip-link').first().boundingBox();
    expect(skipLinkBox?.width ?? 0, 'focused skip link should meet the WCAG 2.2 AA target-size floor').toBeGreaterThanOrEqual(24);
    expect(skipLinkBox?.height ?? 0, 'focused skip link should meet the WCAG 2.2 AA target-size floor').toBeGreaterThanOrEqual(24);
    await page.keyboard.press('Enter');
    await expect(page.locator('#main-content')).toBeFocused();
  });

  test('visible links and buttons have programmatic names on core routes @a11y', async ({ page }) => {
    for (const path of CORE_ROUTES) {
      await page.goto(path);
      await expect(page.locator('#main-content')).toBeVisible({ timeout: 20_000 });
      const nameless = await page.evaluate(() => {
        const controls = Array.from(document.querySelectorAll<HTMLElement>('button, a[href]'));
        return controls
          .filter((el) => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') return false;
            if (rect.width < 1 || rect.height < 1) return false;
            const name = [
              el.getAttribute('aria-label'),
              el.getAttribute('title'),
              el.textContent,
            ].join(' ').trim();
            return name.length === 0;
          })
          .map((el) => el.outerHTML.slice(0, 180));
      });
      expect(nameless, `${path}: every visible button/link needs an accessible name`).toEqual([]);
    }
  });

  test('keyboard focus order exposes visible named controls without traps @a11y', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('#main-content')).toBeVisible({ timeout: 20_000 });

    const focused: string[] = [];
    for (let i = 0; i < 24; i += 1) {
      await page.keyboard.press('Tab');
      const state = await page.evaluate(() => {
        const el = document.activeElement as HTMLElement | null;
        if (!el || el === document.body) return null;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        const label = [
          el.getAttribute('aria-label'),
          el.getAttribute('title'),
          el.textContent,
          el.getAttribute('placeholder'),
        ].join(' ').replace(/\s+/g, ' ').trim();
        const outlineWidth = Number.parseFloat(style.outlineWidth || '0');
        const outlineVisible = style.outlineStyle !== 'none' && outlineWidth >= 1;
        const shadowVisible =
          style.boxShadow !== 'none' &&
          !/rgba?\(\s*0\s*,\s*0\s*,\s*0\s*(?:,\s*0\s*)?\)/i.test(style.boxShadow);
        const hasFocusRing =
          outlineVisible ||
          shadowVisible;
        return {
          tag: el.tagName.toLowerCase(),
          label,
          visible: rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden',
          hasFocusRing,
        };
      });
      if (state === null && focused.length >= 10) break;
      expect(state, `tab stop ${i + 1} should land on an element`).not.toBeNull();
      expect(state?.visible, `tab stop ${i + 1} should be visible`).toBe(true);
      expect(state?.label, `tab stop ${i + 1} should have a name`).not.toBe('');
      expect(state?.hasFocusRing, `tab stop ${i + 1} should have visible focus treatment`).toBe(true);
      focused.push(`${state?.tag}:${state?.label}`);
    }
    expect(new Set(focused).size, 'tab order should advance instead of trapping on one control').toBeGreaterThan(8);
  });

  test('interactive controls meet the WCAG 2.2 AA target-size floor @a11y', async ({ page, request }) => {
    const borrowerId = await fetchFirstLeadId(request);
    const routes = [
      ...CORE_ROUTES,
      ...BORROWER_DETAIL_ROUTE_PREFIXES.map((prefix) => `${prefix}/${borrowerId}`),
    ] as const;

    for (const path of routes) {
      await page.goto(path);
      await expect(page.locator('#main-content')).toBeVisible({ timeout: 20_000 });
      const undersized = await page.evaluate(() => {
        const selectors = [
          'button',
          'input:not([type="hidden"])',
          'select',
          'textarea',
          'a[href]',
          '[role="button"]',
          '[tabindex="0"]',
          '.filter',
        ];
        const controls = Array.from(document.querySelectorAll<HTMLElement>(selectors.join(',')));
        return controls
          .filter((el) => {
            const style = window.getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            if (style.display === 'none' || style.visibility === 'hidden') return false;
            if (rect.width < 1 || rect.height < 1) return false;
            if (el.closest('[aria-hidden="true"]')) return false;
            if (el.classList.contains('sr-skip-link')) return false;
            // SVG geography regions preserve exact state/county shapes. They
            // are keyboard reachable and visibly focusable, but target-size
            // remediation would distort the map geometry; track the exception
            // explicitly instead of hiding it behind a class selector.
            if (el.dataset.targetSizeExempt === 'geographic-shape') return false;
            return rect.width < 24 || rect.height < 24;
          })
          .map((el) => {
            const rect = el.getBoundingClientRect();
            const name = [
              el.getAttribute('aria-label'),
              el.getAttribute('title'),
              el.textContent,
              el.getAttribute('placeholder'),
            ].join(' ').replace(/\s+/g, ' ').trim();
            return `${el.tagName.toLowerCase()} ${name || el.className || el.id} ${Math.round(rect.width)}x${Math.round(rect.height)}`;
          })
          .slice(0, 20);
      });
      expect(undersized, `${path}: controls should be at least 24x24 CSS px`).toEqual([]);
    }
  });

  test('Lead Queue virtualization keeps DOM bounded while exposing row metadata @a11y', async ({ page }) => {
    await page.goto('/lead-queue');
    const table = page.locator('.lead-table__table').first();
    await expect(table).toBeVisible({ timeout: 30_000 });
    const totalRows = parseAriaRowCount(await table.getAttribute('aria-rowcount'));
    if (totalRows <= 1) {
      await skipOnlyWhenLeadQueueIsExplicitlyEmpty(page);
      expect(totalRows, 'Lead Queue row metadata gate requires live rows unless the UI is explicitly empty').toBeGreaterThan(1);
    }

    const renderedRows = await page.locator('.lead-table__table tbody > tr:not(.lead-table__virtual-spacer)').count();
    if (totalRows <= 121) {
      test.info().annotations.push({
        type: 'note',
        description: `Live dataset returned only ${totalRows - 1} rows; virtualization threshold not crossed.`,
      });
      return;
    }

    expect(renderedRows, 'virtualized table should not mount every row').toBeLessThan(totalRows / 2);
    const firstRowIndex = Number(
      await page.locator('.lead-table__table tbody > tr[aria-rowindex]').first().getAttribute('aria-rowindex'),
    );
    expect(firstRowIndex, 'first virtualized data row should expose aria-rowindex').toBeGreaterThanOrEqual(2);
  });

  test('Lead Queue rows are keyboard-expandable with stable virtual row metadata @a11y', async ({ page }) => {
    await page.goto('/lead-queue');
    const table = page.locator('.lead-table__table').first();
    await expect(table).toBeVisible({ timeout: 30_000 });
    const totalRows = parseAriaRowCount(await table.getAttribute('aria-rowcount'));
    if (totalRows <= 1) {
      await skipOnlyWhenLeadQueueIsExplicitlyEmpty(page);
      expect(totalRows, 'Lead Queue keyboard gate requires live rows unless the UI is explicitly empty').toBeGreaterThan(1);
    }
    const firstRow = page.locator('.lead-table__table tbody > tr[role="button"][aria-rowindex]').first();
    await expect(firstRow).toBeVisible({ timeout: 30_000 });
    await firstRow.focus();
    await expect(firstRow).toBeFocused();
    const initiallyExpanded = (await firstRow.getAttribute('aria-expanded')) === 'true';
    await firstRow.press('Enter');
    await expect(firstRow).toHaveAttribute('aria-expanded', initiallyExpanded ? 'false' : 'true');
    if (initiallyExpanded) {
      await expect(page.locator('.tbl__expand')).toHaveCount(0);
    } else {
      await expect(page.locator('.tbl__expand').first()).toBeVisible();
    }
    await firstRow.press('Space');
    await expect(firstRow).toHaveAttribute('aria-expanded', initiallyExpanded ? 'true' : 'false');
    if (initiallyExpanded) {
      await expect(page.locator('.tbl__expand').first()).toBeVisible();
    } else {
      await expect(page.locator('.tbl__expand')).toHaveCount(0);
    }
  });

  test('prefers-reduced-motion collapses app transitions and animations @a11y', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto('/');
    await expect(page.locator('#main-content')).toBeVisible({ timeout: 20_000 });
    const maxMotionMs = await page.evaluate(() => {
      const parseDuration = (raw: string): number =>
        raw
          .split(',')
          .map((part) => part.trim())
          .filter(Boolean)
          .reduce((max, part) => {
            if (part.endsWith('ms')) return Math.max(max, Number.parseFloat(part));
            if (part.endsWith('s')) return Math.max(max, Number.parseFloat(part) * 1000);
            return max;
          }, 0);
      const selectors = [
        '.topbar',
        '.rail',
        '.route-nav',
        '.surface',
        '.drawer',
        '.genie__panel',
        '.topbar__icon-btn',
      ];
      return Math.max(
        ...selectors.flatMap((selector) =>
          Array.from(document.querySelectorAll<HTMLElement>(selector)).map((el) => {
            const style = window.getComputedStyle(el);
            return Math.max(
              parseDuration(style.transitionDuration),
              parseDuration(style.animationDuration),
            );
          }),
        ),
        0,
      );
    });
    expect(maxMotionMs, 'reduced-motion media query should clamp motion').toBeLessThanOrEqual(1);
  });
});
