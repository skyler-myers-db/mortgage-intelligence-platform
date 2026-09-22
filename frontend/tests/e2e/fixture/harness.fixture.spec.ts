/**
 * Self-tests for the fixture harness: the synthetic population, on-screen
 * reconciliation, each hygiene check, degrade(), and the shell helpers.
 *
 * The hygiene self-tests opt out by name so that the violation they cause on
 * purpose does not fail them; they then assert the violation was COLLECTED
 * with a readable message. That a test which does NOT opt out fails on the
 * same input is pinned in hygieneGate.fixture.spec.ts.
 */
import { SCORE_BUCKETS } from './data/analytics';
import { BORROWERS, LEADS, MASKED_BORROWER_ID, PRIMARY_BORROWER } from './data/borrowers';
import { PORTFOLIO_PREVIEW } from './data/portfolio';
import { STATES, TOTALS } from './data/reference';
import { SEGMENTS } from './data/segments';
import { formatViolations } from './hygiene';
import { WAREHOUSE_WARMING_UP, json } from './mockApi';
import { expect, test } from './test';

function sum(values: readonly number[]): number {
  return values.reduce((total, value) => total + value, 0);
}

test.describe('fixture population', () => {
  test('borrower ids are masked, unique and synthetic', () => {
    const ids = BORROWERS.map((borrower) => borrower.borrower_id);
    for (const id of ids) expect(id).toMatch(MASKED_BORROWER_ID);
    expect(new Set(ids).size).toBe(ids.length);
    expect(LEADS.map((lead) => lead.borrower_id)).toEqual(ids);
    for (const borrower of BORROWERS) {
      expect(borrower.display_name).toMatch(/^Owner [0-9A-Z]{6}$/);
      expect(borrower.subject_property).toMatch(/^Synthetic property · /);
      expect(borrower.eligibility_source).toBe('synthetic_seed');
    }
  });

  test('headline totals reconcile across the state, KPI and segment fixtures', () => {
    expect(sum(STATES.map((state) => state.addressable))).toBe(TOTALS.addressable);
    expect(sum(STATES.map((state) => state.contactable))).toBe(TOTALS.contactable);
    expect(sum(STATES.map((state) => state.inTheMoney))).toBe(TOTALS.inTheMoney);
    expect(sum(STATES.map((state) => state.topTier))).toBe(TOTALS.highOpportunity);
    expect(PORTFOLIO_PREVIEW.marketable_population).toBe(TOTALS.addressable);
    expect(PORTFOLIO_PREVIEW.high_intent_leads).toBe(TOTALS.inTheMoney);
    expect(SEGMENTS.find((segment) => segment.code === 'itm')?.count).toBe(TOTALS.inTheMoney);
    expect(sum(SCORE_BUCKETS.map(([, count]) => count))).toBe(TOTALS.addressable);
  });
});

test.describe('on-screen reconciliation', () => {
  test('home KPI equals the map total', async ({ app, page }) => {
    await app.gotoRoute('/');
    const addressable = TOTALS.addressable.toLocaleString('en-US');
    await expect(page.locator('.kpi .kpi__value').first()).toHaveText(addressable);
    await expect(page.locator('.map-legend__value')).toHaveText(addressable);
  });

  test('segment workbench queue total equals the contactable population', async ({ app, page }) => {
    await app.gotoRoute('/segment-intelligence');
    await expect(page.locator('.segment-mode-control__total .num')).toHaveText(TOTALS.contactable.toLocaleString('en-US'));
  });
});

test.describe('hygiene collects what it must fail on', () => {
  test.use({ hygieneOptOut: ['unregistered-api', 'console.error', 'pageerror', 'csp'] });

  test('an API call with no fixture is reported with its method and path', async ({ app, hygiene, mockApi, page }) => {
    await app.gotoRoute('/glossary');
    const status = await page.evaluate(async () => (await fetch('/api/v1/not-a-fixture?probe=1')).status);
    expect(status).toBe(501);
    expect(mockApi.unregistered).toEqual([
      { method: 'GET', path: '/api/v1/not-a-fixture', search: 'probe=1', status: 501, outcome: 'unregistered' },
    ]);
    const collected = hygiene.collected();
    expect(collected.map((violation) => violation.check)).toEqual(['unregistered-api']);
    expect(formatViolations('probe', collected)).toContain('[unregistered-api] GET /api/v1/not-a-fixture?probe=1 has no registered fixture');
    expect(hygiene.violations(), 'opted out by name for this self-test only').toEqual([]);
  });

  test('console.error and uncaught page errors are collected', async ({ app, hygiene, page }) => {
    await app.gotoRoute('/glossary');
    await page.evaluate(() => {
      console.error('fixture self-test console error');
      window.addEventListener('mip-fixture-probe', () => {
        throw new Error('fixture self-test uncaught error');
      }, { once: true });
      window.dispatchEvent(new Event('mip-fixture-probe'));
    });
    await expect.poll(() => hygiene.collected().map((violation) => violation.check).sort()).toEqual(['console.error', 'pageerror']);
    const report = formatViolations('probe', hygiene.collected());
    expect(report).toContain('[console.error] fixture self-test console error');
    expect(report).toMatch(/\[pageerror\] .*fixture self-test uncaught error/);
  });

  test('an error thrown inside a timer is still collected under the frozen clock', async ({ app, hygiene, page }) => {
    // Playwright's fake clock runs timer callbacks itself and reports what
    // they throw through console.error instead of `pageerror`. Either way the
    // test fails; this pins that the error is not swallowed.
    await app.gotoRoute('/glossary');
    await page.evaluate(() => {
      window.setTimeout(() => {
        throw new Error('fixture self-test timer error');
      }, 0);
    });
    await expect.poll(() => formatViolations('probe', hygiene.collected())).toContain('fixture self-test timer error');
  });

  test('the production CSP is enforced and a violation is collected', async ({ app, hygiene, page }) => {
    await app.gotoRoute('/glossary');
    expect(hygiene.policy).toContain("script-src 'self'");
    await page.evaluate(() => {
      const inline = document.createElement('script');
      inline.textContent = 'window.__mipInlineScriptRan = true;';
      document.head.appendChild(inline);
    });
    await expect.poll(() => hygiene.collected().filter((violation) => violation.check === 'csp').length).toBeGreaterThan(0);
    expect(hygiene.collected()[0].message).toContain('script-src');
    const ran = await page.evaluate(() => '__mipInlineScriptRan' in window);
    expect(ran, 'the inline script must be blocked, not merely reported').toBe(false);
  });
});

test.describe('degrade()', () => {
  test('renders the explicit degraded state without tripping hygiene', async ({ app, hygiene, mockApi, page }) => {
    app.degrade('/api/leads', WAREHOUSE_WARMING_UP);
    await page.goto('/lead-queue');
    await expect(page.locator('#main-content .warming-block').first()).toBeVisible();
    expect(mockApi.calls.some((call) => call.path === '/api/leads' && call.outcome === 'degraded' && call.status === 503)).toBe(true);
    await expect(page.locator('table.tbl tbody tr')).toHaveCount(0);
    expect(hygiene.violations(), 'a 503 the test asked for is not a hygiene failure').toEqual([]);
  });

  test('register() overrides one fixture for one test', async ({ app, mockApi, page }) => {
    mockApi.register('GET', '/api/leads', () => json([LEADS[3]], { headers: { 'X-Total-Matching': '1', 'X-Returned-Rows': '1' } }));
    await app.gotoRoute('/lead-queue');
    await expect(page.locator('#main-content')).toContainText(LEADS[3].borrower_id);
    await expect(page.locator('#main-content')).not.toContainText(PRIMARY_BORROWER.borrower_id);
  });
});

test.describe('shell helpers', () => {
  test('openConsole shows recent activity from the audit fixture', async ({ app }) => {
    await app.gotoRoute('/glossary');
    const panel = await app.openConsole();
    await expect(panel).toContainText(LEADS[0].borrower_id);
  });

  test('openGenie opens the floating panel with the fixture sample questions', async ({ app }) => {
    await app.gotoRoute('/glossary');
    const dialog = await app.openGenie();
    await expect(dialog).toContainText('Which states have the most prime refi candidates?');
  });

  test('openCommandPalette searches the fixture borrowers', async ({ app, page }) => {
    await app.gotoRoute('/glossary');
    const palette = await app.openCommandPalette();
    await page.keyboard.type(PRIMARY_BORROWER.borrower_id);
    await expect(palette.getByRole('group', { name: 'Borrowers' })).toContainText(PRIMARY_BORROWER.borrower_id);
  });

  test('expandFirstLeadRow opens the first ranked borrower preview', async ({ app }) => {
    await app.gotoRoute('/lead-queue');
    const expanded = await app.expandFirstLeadRow();
    await expect(expanded).toContainText(PRIMARY_BORROWER.clip);
  });
});
