import { expect, test, type Page, type Route } from '@playwright/test';

const APP_URL = process.env.MIP_APP_URL || 'http://127.0.0.1:5173';

function normalizedApiPath(rawUrl: string): string {
  return new URL(rawUrl).pathname.replace(/^\/api\/v\d+(?=\/)/, '/api');
}

async function fulfillJson(route: Route, body: unknown): Promise<void> {
  await route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

async function installConsoleApi(page: Page): Promise<void> {
  await page.route('**/api/**', async (route) => {
    const path = normalizedApiPath(route.request().url());

    if (path === '/api/health') {
      await fulfillJson(route, {
        status: 'ok',
        mode: 'live',
        dependencies: { warehouse: 'up', lakebase: 'up', genie: 'up' },
        circuit_breakers: { warehouse: 'closed', lakebase: 'closed', genie: 'closed' },
      });
      return;
    }
    if (path === '/api/session') {
      await fulfillJson(route, { can_access_admin: false });
      return;
    }
    if (path === '/api/workspace') {
      await fulfillJson(route, { saved_leads: [], saved_drafts: [] });
      return;
    }
    if (path === '/api/config/options') {
      await fulfillJson(route, {
        lender_name: 'Summit Mortgage',
        rum_enabled: false,
        geographies: ['All states'],
        geographies_status: 'live',
        occupancy: ['All'],
        lien_status: ['Any'],
        lender_relationships: ['All'],
        products: ['All products'],
        equity_thresholds: ['Any'],
        target_lender_refs: ['All'],
        target_lender_refs_status: 'live',
      });
      return;
    }
    if (path === '/api/config/footprint') {
      await fulfillJson(route, {
        states: [],
        using_fallback: false,
        geography_scope: {
          state_count: 0,
          county_count: 0,
          zip_count: 0,
          scope_label: 'No geography required for Console layout coverage',
          counties: [],
        },
      });
      return;
    }
    if (path === '/api/audit/my-events') {
      await fulfillJson(route, {
        items: Array.from({ length: 8 }, (_, index) => ({
          event_type: index % 2 === 0 ? 'VIEW_LEADS' : 'RUN_GENIE',
          entity_type: 'borrower',
          subject_id: `B-CONSOLE${String(index).padStart(5, '0')}`,
          created_at: `2026-07-14T12:${String(index).padStart(2, '0')}:00Z`,
        })),
        next_cursor: null,
      });
      return;
    }
    if (path === '/api/genie/start') {
      await fulfillJson(route, {
        conversation_id: 'console-layout-conversation',
        sample_questions: [],
      });
      return;
    }

    await route.fulfill({ status: 404, body: `Unhandled Console test API route: ${path}` });
  });
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => window.localStorage.clear());
  await installConsoleApi(page);
});

test.use({ baseURL: APP_URL });

test('desktop Console stays in the viewport and scrolls to its last control @desktop', async ({ page }) => {
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/glossary');
  await page.getByRole('banner').getByRole('button', { name: 'Toggle console' }).click();

  const panel = page.getByRole('complementary', { name: 'Workspace console' });
  const body = panel.locator('.tweaks__body');
  const lastControl = body.getByRole('button', { name: 'Open Genie' });
  await expect(body, 'the real Console must replace its empty Suspense fallback').toBeVisible({
    timeout: 30_000,
  });

  const geometry = await panel.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return {
      top: rect.top,
      right: rect.right,
      bottom: rect.bottom,
      width: rect.width,
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
    };
  });
  expect(geometry.top).toBeGreaterThanOrEqual(0);
  expect(geometry.right).toBeLessThanOrEqual(geometry.viewportWidth);
  expect(geometry.bottom).toBeLessThanOrEqual(geometry.viewportHeight);
  expect(geometry.width).toBe(300);

  const initialScroll = await body.evaluate((element) => ({
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
    scrollTop: element.scrollTop,
    overflowY: getComputedStyle(element).overflowY,
  }));
  expect(initialScroll.overflowY).toBe('auto');
  expect(initialScroll.scrollHeight).toBeGreaterThan(initialScroll.clientHeight);
  expect(initialScroll.scrollTop).toBe(0);

  await lastControl.focus();
  await expect(lastControl).toBeFocused();
  await expect(lastControl).toBeInViewport();

  const reached = await lastControl.evaluate((control) => {
    const bodyElement = control.closest<HTMLElement>('.tweaks__body');
    const bodyRect = bodyElement?.getBoundingClientRect();
    const controlRect = control.getBoundingClientRect();
    return {
      scrollTop: bodyElement?.scrollTop ?? 0,
      controlTop: controlRect.top,
      controlBottom: controlRect.bottom,
      bodyTop: bodyRect?.top ?? Number.NaN,
      bodyBottom: bodyRect?.bottom ?? Number.NaN,
    };
  });
  expect(reached.scrollTop).toBeGreaterThan(0);
  expect(reached.controlTop).toBeGreaterThanOrEqual(reached.bodyTop);
  expect(reached.controlBottom).toBeLessThanOrEqual(reached.bodyBottom);
});

test('mobile Console remains a viewport-width bottom sheet @device', async ({ page }) => {
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/glossary');
  await page.getByRole('button', { name: 'Toggle console' }).click();

  const panel = page.getByRole('complementary', { name: 'Workspace console' });
  const body = panel.locator('.tweaks__body');
  const lastControl = body.getByRole('button', { name: 'Open Genie' });
  await expect(body, 'the real Console must replace its empty Suspense fallback').toBeVisible({
    timeout: 30_000,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(body).toBeVisible();
  await expect(panel).toHaveCSS('width', '390px');

  const geometry = await panel.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return {
      left: rect.left,
      right: rect.right,
      bottom: rect.bottom,
      height: rect.height,
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
    };
  });
  expect(geometry.left).toBe(0);
  expect(geometry.right).toBe(geometry.viewportWidth);
  expect(geometry.bottom).toBe(geometry.viewportHeight);
  expect(geometry.height).toBeLessThanOrEqual(geometry.viewportHeight / 2);

  const bodyScroll = await body.evaluate((element) => ({
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
    overflowY: getComputedStyle(element).overflowY,
  }));
  expect(bodyScroll.overflowY).toBe('auto');
  expect(bodyScroll.scrollHeight).toBeGreaterThan(bodyScroll.clientHeight);

  await lastControl.focus();
  await expect(lastControl).toBeFocused();
  await expect(lastControl).toBeInViewport();
});

test('persisted oversized Genie stays inside 16px gutters at 390px and 320px @device', async ({ page }) => {
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => {
    window.localStorage.setItem('mip-genie-chat-size-v1', JSON.stringify({ w: 900, h: 900 }));
    window.localStorage.setItem(
      'mip-genie-chat-pos-v1',
      JSON.stringify({ pos: { x: 760, y: 520 } }),
    );
  });
  await page.goto('/glossary');
  await page.locator('.genie__fab:visible').click();

  const panel = page.getByRole('dialog', { name: 'Genie chat' });
  await expect(panel).toBeVisible({ timeout: 30_000 });

  const expectGutters = async (width: number, height: number) => {
    await expect.poll(async () => panel.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      return rect.left >= 15.5
        && rect.top >= 15.5
        && rect.right <= window.innerWidth - 15.5
        && rect.bottom <= window.innerHeight - 15.5;
    })).toBe(true);
    const geometry = await panel.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      return {
        width: rect.width,
        height: rect.height,
        viewportWidth: window.innerWidth,
        viewportHeight: window.innerHeight,
      };
    });
    expect(geometry.viewportWidth).toBe(width);
    expect(geometry.viewportHeight).toBe(height);
    expect(geometry.width).toBe(width - 32);
    expect(geometry.height).toBe(height - 32);
  };

  await expectGutters(390, 844);
  await page.setViewportSize({ width: 320, height: 568 });
  await expectGutters(320, 568);
});
